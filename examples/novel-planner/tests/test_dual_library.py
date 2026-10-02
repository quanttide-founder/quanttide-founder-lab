#!/usr/bin/env python3
"""双库实验骨架护栏与核心函数的回归测试（不依赖任何模型/网络）。"""
import importlib.util
import os
import sys
import unittest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

spec = importlib.util.spec_from_file_location(
    "dual_library", os.path.join(SRC, "dual_library.py"))
dl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dl)

loader_spec = importlib.util.spec_from_file_location(
    "loader", os.path.join(SRC, "loader.py"))
ld = importlib.util.module_from_spec(loader_spec)
loader_spec.loader.exec_module(ld)


def _rules(n_enabled):
    rules = {"rules": []}
    for i in range(n_enabled):
        rules["rules"].append({
            "rule_id": f"R{i:03d}", "instruction": f"rule {i}",
            "type": "禁用", "status": "启用", "source": "x", "hit_count": 0,
        })
    return rules


def _ledger():
    return {"entries": [
        {"entry_id": "E001", "type": "fact", "chapter_id": "c1",
         "content": "拖鞋在玄关", "resolved": False, "tags": []},
        {"entry_id": "E002", "type": "fact", "chapter_id": "c2",
         "content": "他不知道拖鞋的事", "resolved": False, "tags": []},
        {"entry_id": "E003", "type": "debt", "chapter_id": "c1",
         "content": "欠账：谁买了拖鞋", "resolved": False, "tags": []},
        {"entry_id": "E004", "type": "debt", "chapter_id": "c1",
         "content": "已解欠账", "resolved": True, "tags": []},
    ], "pending": []}


class TestGuardrails(unittest.TestCase):
    def test_enabled_cap_raises(self):
        with self.assertRaises(ValueError):
            dl.enabled_rules(_rules(13))

    def test_enabled_cap_ok(self):
        self.assertEqual(len(dl.enabled_rules(_rules(12))), 12)

    def test_backflow_fact_as_rule_blocked(self):
        rules, ledger = _rules(3), _ledger()
        devs = {"records": [{"type": "fact", "snippet": "x",
                             "rule_or_fact_involved": "R001"}]}
        with self.assertRaises(RuntimeError):
            dl.backflow(devs, rules, ledger, allow_fact_as_rule=True)

    def test_candidate_rule_not_enabled(self):
        rules = _rules(3)
        dl.add_candidate_rule(rules, "新候选")
        new = rules["rules"][-1]
        self.assertEqual(new["status"], "观察")
        self.assertEqual(len(dl.enabled_rules(rules)), 3)


class TestAssembly(unittest.TestCase):
    def test_blocks_present_and_order(self):
        ctx = dl.assemble_context(_rules(3), _ledger(), {"goal": "g", "constraints": "c"})
        self.assertIn("【STYLE】", ctx["prompt"])
        self.assertIn("【STATE】", ctx["prompt"])
        self.assertIn("【DEBTS】", ctx["prompt"])
        self.assertIn("【SCENE】", ctx["prompt"])
        # 顺序固定：STYLE 在 STATE 前
        self.assertLess(ctx["prompt"].index("【STYLE】"),
                        ctx["prompt"].index("【STATE】"))

    def test_state_filtered_by_chapter(self):
        ctx = dl.assemble_context(_rules(3), _ledger(), {"goal": "", "constraints": ""},
                                  chapter_id="c2")
        self.assertIn("他不知道拖鞋的事", ctx["prompt"])
        self.assertNotIn("拖鞋在玄关", ctx["prompt"])

    def test_debts_only_unresolved(self):
        ctx = dl.assemble_context(_rules(3), _ledger(), {"goal": "", "constraints": ""})
        self.assertIn("谁买了拖鞋", ctx["prompt"])
        self.assertNotIn("已解欠账", ctx["prompt"])


class TestSnapshotAndMetrics(unittest.TestCase):
    def test_snapshot_rejects_bad_group(self):
        ctx = dl.assemble_context(_rules(3), _ledger(), {"goal": "", "constraints": ""})
        with self.assertRaises(ValueError):
            dl.make_snapshot("Z", ctx)

    def test_metrics_math(self):
        devs = {"records": [
            {"type": "style", "count": 2},
            {"type": "fact", "count": 1},
        ]}
        snaps = [{"length": 500}, {"length": 500}]
        m = dl.compute_metrics(devs, snaps)
        self.assertEqual(m["total_deviations"], 3)
        self.assertEqual(m["per_type"]["style"], 2)
        self.assertEqual(m["per_1k_words"]["style"], 2.0)


class TestBackflow(unittest.TestCase):
    def test_fact_backfill(self):
        rules, ledger = _rules(3), _ledger()
        devs = {"records": [{"type": "fact", "snippet": "补录事实"}]}
        dl.backflow(devs, rules, ledger)
        self.assertEqual(len(ledger["entries"]), 5)
        self.assertTrue(any(e["content"] == "补录事实" for e in ledger["entries"]))

    def test_fabrication_to_pending(self):
        rules, ledger = _rules(3), _ledger()
        devs = {"records": [{"type": "fabrication", "snippet": "编造设定"}]}
        dl.backflow(devs, rules, ledger)
        self.assertEqual(len(ledger["pending"]), 1)
        self.assertEqual(ledger["pending"][0]["status"], "待确认")

    def test_style_hit_count(self):
        rules, ledger = _rules(3), _ledger()
        devs = {"records": [{"type": "style", "count": 2,
                             "rule_or_fact_involved": "R001"}]}
        dl.backflow(devs, rules, ledger)
        # R001 是 _rules(3) 中下标 1 的那条
        self.assertEqual(rules["rules"][1]["hit_count"], 2)


class TestLoader(unittest.TestCase):
    SAMPLE = (
        "# 第一章 玄关的拖鞋\n正文一\n"
        "# 第二章 没说出口的疑问\n正文二\n"
        "- [fact][c1] 拖鞋在玄关鞋柜\n"
        "- [debt][c1] 谁买了拖鞋\n"
        "- [inventory][c3] 钥匙在抽屉\n"
    )

    def test_chapters_and_entries(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".md",
                                         encoding="utf-8", delete=False) as f:
            f.write(self.SAMPLE)
            path = f.name
        try:
            parsed = ld.load_test_text(path)
            self.assertEqual(len(parsed["chapters"]), 2)
            self.assertEqual(parsed["chapters"][0]["id"], "c1")
            self.assertIn("正文一", parsed["chapters"][0]["text"])
            self.assertEqual(len(parsed["entries"]), 3)
            types = {e["type"] for e in parsed["entries"]}
            self.assertEqual(types, {"fact", "debt", "inventory"})
        finally:
            os.unlink(path)

    def test_entry_regex_rejects_bad_type(self):
        self.assertIsNone(ld.ENTRY_RE.match("- [bogus][c1] x"))


if __name__ == "__main__":
    unittest.main()
