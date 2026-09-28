"""assess.py 一致性检查的回归：四类违规必须报错，合法行必须通过。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import assess  # noqa: E402

VALID = ["定价", "40", "给得出通用模型，但不知道本地已锚定 5 毛/签", "B", "L0", "待验证"]


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
    def test_valid_passes(self):
        self.assertEqual(run_check(VALID), 0)

    def test_pure_a_below_60_rejected(self):
        row = ["市场调研", "50", "仅通用知识，未喂本地数据", "A", "L0", "待验证"]
        self.assertEqual(run_check(row), 1)

    def test_c_above_80_rejected(self):
        row = ["底料/供应链", "90", "闻不到、尝不出，需人工试吃", "A + C", "L0", "待验证"]
        self.assertEqual(run_check(row), 1)

    def test_pure_c_above_30_rejected(self):
        row = ["现场运营", "40", "现场盯摊由人工负责", "C", "L0", "待验证"]
        self.assertEqual(run_check(row), 1)

    def test_bad_level_rejected(self):
        row = ["定价", "40", "不知道本地锚点，需人工验证", "B", "L3", "待验证"]
        self.assertEqual(run_check(row), 1)

    def test_c_without_human_note_rejected(self):
        """含 C 的局限必须点名人工兜底，笼统评价不算。"""
        row = ["产品差异化", "75", "落地有点难", "A + C", "L0", "待验证"]
        self.assertEqual(run_check(row), 1)

    def test_empty_field_rejected(self):
        row = ["定价", "40", "", "B", "L0", "待验证"]
        self.assertEqual(run_check(row), 1)


class TestDerive(unittest.TestCase):
    def test_eval_row_is_complete(self):
        row = assess.derive("证照合规", "证照清单能列全，本地执法口径未核实")
        self.assertEqual(len(row), 6)
        self.assertTrue(all(row))
        self.assertEqual(row[4], "L0")
        self.assertEqual(row[5], "待验证")

    def test_pure_reasoning_is_a(self):
        row = assess.derive("风险预案", "常见翻车点能列全", score=80)
        self.assertEqual(row[3], "A")

    def test_local_data_is_b(self):
        row = assess.derive("定价", "不知道本地已锚定 5 毛/签", score=40)
        self.assertEqual(row[3], "B")

    def test_sensing_is_c(self):
        row = assess.derive("底料", "闻不到、尝不出，人工兜底", score=60)
        self.assertEqual(row[3], "A + C")

    def test_seed_matches_table_shape(self):
        seed = Path(__file__).resolve().parent.parent / "docs" / "AI 辅助开店.md"
        rows = assess.parse_seed(seed)
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(len(r) == 6 and all(r) for r in rows))


if __name__ == "__main__":
    unittest.main()
