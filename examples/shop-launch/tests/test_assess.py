"""assess.py 的回归：一致性检查、判例推导与法条引用、判例优先重放。"""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import assess  # noqa: E402


def row(name="定价", score="40", limit="给得出通用模型，但不知道本地已锚定 5 毛/签",
        dep="B", level="L0", status="待验证", basis="依B·标尺·检查§4", review="未复核"):
    return [name, score, limit, dep, level, status, basis, review]


def run_check(*rows: list[str]) -> int:
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                     encoding="utf-8") as f:
        f.write(",".join(assess.HEADER) + "\n")
        for r in rows:
            f.write(",".join(r) + "\n")
        path = Path(f.name)
    try:
        return assess.check(path)
    finally:
        path.unlink()


class TestConsistencyRules(unittest.TestCase):
    """AGENTS.md 检查§1–4 与八字段、复核标注的入库校验。"""

    def test_valid_passes(self):
        self.assertEqual(run_check(row()), 0)

    def test_pure_a_below_60_rejected(self):
        self.assertEqual(run_check(row("市场调研", "50", "仅通用知识，未喂本地数据",
                                       "A", "L0", "待验证", "依A·标尺·检查§1")), 1)

    def test_c_above_80_rejected(self):
        self.assertEqual(run_check(row("底料/供应链", "90", "闻不到、尝不出，需人工试吃",
                                       "A + C", "L0", "待验证", "依A·依C·标尺·检查§2")), 1)

    def test_pure_c_above_30_rejected(self):
        self.assertEqual(run_check(row("现场运营", "40", "现场盯摊由人工负责",
                                       "C", "L0", "待验证", "依C·标尺·检查§3")), 1)

    def test_bad_level_rejected(self):
        self.assertEqual(run_check(row(level="L3")), 1)

    def test_bad_review_state_rejected(self):
        self.assertEqual(run_check(row(review="同意")), 1)

    def test_c_without_human_note_rejected(self):
        self.assertEqual(run_check(row("产品差异化", "75", "落地有点难",
                                       "A + C", "L0", "待验证", "依A·依C·标尺·检查§2")), 1)

    def test_empty_field_rejected(self):
        self.assertEqual(run_check(row(limit="")), 1)


class TestDerive(unittest.TestCase):
    """成文法投影：词表 → 依赖，分数带 → 分，法条引用自动生成。"""

    def test_eval_row_is_complete(self):
        row_ = assess.derive("证照合规", "证照清单能列全，本地执法口径未核实")
        self.assertEqual(len(row_), len(assess.HEADER))
        self.assertTrue(all(row_))
        self.assertEqual(row_[4], "L0")
        self.assertEqual(row_[7], "未复核")

    def test_basis_cites_rules(self):
        self.assertEqual(assess.derive("风险预案", "常见翻车点能列全", score=80)[6],
                         "依A·标尺·检查§1")
        self.assertEqual(assess.derive("定价", "不知道本地已锚定 5 毛/签", score=40)[6],
                         "依B·标尺·检查§4")
        self.assertEqual(assess.derive("底料", "闻不到、尝不出，人工兜底", score=60)[6],
                         "依A·依C·标尺·检查§2")

    def test_seed_matches_table_shape(self):
        seed = Path(__file__).resolve().parent.parent / "docs" / "AI 辅助开店.md"
        rows = assess.parse_seed(seed)
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(len(r) == len(assess.HEADER) and all(r) for r in rows))


class TestReplay(unittest.TestCase):
    """判例优先：重放不覆盖 维持/改判 的行，未复核行重算。"""

    def _merge(self, existing_rows):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                         encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(assess.HEADER)
            w.writerows(existing_rows)
            table = Path(f.name)
        try:
            fresh = [row(name="市场调研", score="80", dep="A",
                         basis="依A·标尺·检查§1")]
            return assess.preserve_reviewed(fresh, table)
        finally:
            table.unlink()

    def test_revised_verdict_survives_replay(self):
        kept = self._merge([row(name="市场调研", score="65", dep="A",
                                basis="改判·探店", review="改判")])
        self.assertEqual(kept[0][1], "65")
        self.assertEqual(kept[0][7], "改判")

    def test_unreviewed_row_is_recomputed(self):
        merged = self._merge([row(name="市场调研", score="65", dep="A",
                                  basis="改判·探店", review="未复核")])
        self.assertEqual(merged[0][1], "80")  # 未复核 → 按成文法重算

    def test_old_schema_table_is_ignored(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False,
                                         encoding="utf-8") as f:
            f.write("环节,AI 能力分,局限,输入依赖,证据等级,验证状态\n")
            f.write("市场调研,65,x,A,L0,待验证\n")
            table = Path(f.name)
        try:
            fresh = [row(name="市场调研", score="80", dep="A")]
            merged = assess.preserve_reviewed(fresh, table)
            self.assertEqual(merged[0][1], "80")  # 旧表头无复核列，不保留
        finally:
            table.unlink()


if __name__ == "__main__":
    unittest.main()
