"""review_gui 数据层的回归：两类标注、两表互链校验、成文法渲染。无图形环境可跑。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import review_gui as G  # noqa: E402


def d_rows() -> list[list[str]]:
    return [
        ["D01", "定价结构", "素 0.5–0.8 / 荤 1–1.5 / 招牌 2–3", "火锅串串.md", "G01",
         "L1", "未复核", ""],
        ["D02", "纪律：不降价", "不降价参战", "火锅串串.md", "", "L0", "未复核", ""],
    ]


def g_rows() -> list[list[str]]:
    return [
        ["G01", "本地锚点优先", "本地数据优先于通用模型", "D01", "候选", ""],
        ["G02", "错位定价", "不跟随低价", "D02", "候选", ""],
    ]


class TestApplyDecision(unittest.TestCase):
    def test_keep_preserves_content(self):
        rows, msg = G.apply_decision(d_rows(), "D01", "维持")
        self.assertEqual(rows[0][6], "维持")
        self.assertEqual(rows[0][2], "素 0.5–0.8 / 荤 1–1.5 / 招牌 2–3")
        self.assertIn("维持", msg)

    def test_revise_updates_content_and_reason(self):
        rows, _ = G.apply_decision(d_rows(), "D01", "改判",
                                   content="人均 50–60", reason="锚点下移")
        self.assertEqual(rows[0][2], "人均 50–60")
        self.assertEqual(rows[0][6], "改判")
        self.assertEqual(rows[0][7], "锚点下移")

    def test_revise_requires_content_and_reason(self):
        with self.assertRaises(ValueError):
            G.apply_decision(d_rows(), "D01", "改判", reason="理由")
        with self.assertRaises(ValueError):
            G.apply_decision(d_rows(), "D01", "改判", content="新内容")

    def test_unknown_or_bad_input(self):
        with self.assertRaises(ValueError):
            G.apply_decision(d_rows(), "D99", "维持")
        with self.assertRaises(ValueError):
            G.apply_decision(d_rows(), "D01", "存疑")


class TestApplyCanon(unittest.TestCase):
    def test_adopt_requires_reason(self):
        with self.assertRaises(ValueError):
            G.apply_canon(g_rows(), "G01", "采纳")

    def test_reject_requires_reason(self):
        with self.assertRaises(ValueError):
            G.apply_canon(g_rows(), "G01", "否决")

    def test_candidate_needs_no_reason(self):
        rows, _ = G.apply_canon(g_rows(), "G01", "候选")
        self.assertEqual(rows[0][4], "候选")

    def test_adopt_sets_fields(self):
        rows, msg = G.apply_canon(g_rows(), "G01", "采纳", reason="已被 D01/D16 验证")
        self.assertEqual(rows[0][4], "采纳")
        self.assertEqual(rows[0][5], "已被 D01/D16 验证")
        self.assertIn("采纳", msg)

    def test_unknown_inputs(self):
        with self.assertRaises(ValueError):
            G.apply_canon(g_rows(), "G99", "采纳", reason="理由")
        with self.assertRaises(ValueError):
            G.apply_canon(g_rows(), "G01", "同意", reason="理由")


class TestValidateAll(unittest.TestCase):
    def test_clean_tables_pass(self):
        self.assertEqual(G.validate_all(d_rows(), g_rows()), [])

    def test_dangling_canon_ref_rejected(self):
        rows = d_rows()
        rows[0][4] = "G01;G99"
        self.assertTrue(any("G99" in b for b in G.validate_all(rows, g_rows())))

    def test_dangling_decision_ref_rejected(self):
        gs = g_rows()
        gs[0][3] = "D01;D99"
        self.assertTrue(any("D99" in b for b in G.validate_all(d_rows(), gs)))

    def test_adopted_without_reason_rejected(self):
        gs = g_rows()
        gs[0][4] = "采纳"
        self.assertTrue(any("采纳必须填理由" in b for b in G.validate_all(d_rows(), gs)))

    def test_bad_review_state_rejected(self):
        rows = d_rows()
        rows[0][6] = "同意"
        self.assertTrue(any("复核非法" in b for b in G.validate_all(rows, g_rows())))

    def test_duplicate_id_rejected(self):
        rows = d_rows()
        rows[1][0] = "D01"
        self.assertTrue(any("重复" in b for b in G.validate_all(rows, g_rows())))


class TestCanonRender(unittest.TestCase):
    def test_adopted_in_rejected_out(self):
        gs = g_rows()
        gs[0][4], gs[0][5] = "采纳", "理由成立"
        out = G.render_canon(gs)
        self.assertIn("G01 本地锚点优先", out)
        self.assertIn("采纳理由：理由成立", out)
        self.assertNotIn("G02 错位定价", out)

    def test_empty_adopted_shows_hint(self):
        self.assertIn("尚无采纳条目", G.render_canon(g_rows()))

    def test_render_is_idempotent(self):
        gs = g_rows()
        self.assertEqual(G.render_canon(gs), G.render_canon(gs))


class TestCommit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._saved = (G.DECISIONS, G.CANON_CAND, G.CANON, G.LOG)
        G.DECISIONS = self.tmp / "决策判例.csv"
        G.CANON_CAND = self.tmp / "准则候选.csv"
        G.CANON = self.tmp / "决策准则.md"
        G.LOG = self.tmp / "纠偏记录.md"
        G.save_table(G.DECISIONS, G.DHEADER, d_rows())
        G.save_table(G.CANON_CAND, G.GHEADER, g_rows())
        G.LOG.write_text(
            "# 纠偏记录\n\n| 日期 | 层 | 对象 | 标注 | 分诊 | 影响行数 | 备注 |\n"
            "|------|----|------|------|------|----------|------|\n"
            "| 2026-09-28 | 成文法 | 示例 | 示例 | 通则 | 0 | 首条 |\n",
            encoding="utf-8")

    def tearDown(self):
        (G.DECISIONS, G.CANON_CAND, G.CANON, G.LOG) = self._saved

    def test_decision_revise_writes_and_logs(self):
        msg = G.commit("decision", "D01", "改判", "人均 50–60", "锚点下移")
        self.assertIn("改判", msg)
        self.assertEqual(G.load_table(G.DECISIONS, G.DHEADER)[0][6], "改判")
        self.assertIn("| 判例 | D01 | 改判决策 | 个例 | 1 | 锚点下移 |",
                      G.LOG.read_text(encoding="utf-8"))

    def test_canon_adopt_writes_logs_and_renders(self):
        msg = G.commit("canon", "G01", "采纳", "", "多条决策验证")
        self.assertIn("采纳 1 条", msg)
        canon = G.CANON.read_text(encoding="utf-8")
        self.assertIn("G01 本地锚点优先", canon)
        self.assertIn("| 成文法 | G01 | 采纳 |", G.LOG.read_text(encoding="utf-8"))

    def test_invalid_commit_rejected_without_write(self):
        before = G.DECISIONS.read_text(encoding="utf-8")
        # D01 改判后暴露准则仍指 G01（合法），改用悬空引用制造违规
        rows = G.load_table(G.DECISIONS, G.DHEADER)
        rows[0][4] = "G99"
        G.save_table(G.DECISIONS, G.DHEADER, rows)
        poisoned = G.DECISIONS.read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            G.commit("decision", "D02", "维持")
        self.assertEqual(G.DECISIONS.read_text(encoding="utf-8"), poisoned)
        self.assertNotEqual(before, poisoned)  # 前置确已改写


if __name__ == "__main__":
    unittest.main()
