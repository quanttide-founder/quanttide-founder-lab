"""deviation.py 的回归：schema 校验、偏差算术、缺数据不给空结论。"""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import deviation as D  # noqa: E402


def rows(*raw: list[str]) -> list[dict]:
    return [dict(zip(D.HEADER, r)) for r in raw]


FULL = ["定价", "热锅串串", "滁州", "2.5", "2.0", "-20.0%", "L2", "2026-09-28"]


class TestSchema(unittest.TestCase):
    def test_data_file_has_header_only(self):
        """schema 先行：实测数据到位前只有表头（3.3 待外部回填）。"""
        loaded = D.load(D.MAP)
        if D.MAP.exists():
            self.assertEqual(loaded, [])

    def test_valid_row_passes(self):
        self.assertEqual(D.validate(rows(FULL)), [])

    def test_non_l2_rejected(self):
        bad = ["定价", "热锅串串", "滁州", "2.5", "2.0", "-20.0%", "L0", "2026-09-28"]
        self.assertTrue(D.validate(rows(bad)))

    def test_wrong_pct_rejected(self):
        bad = ["定价", "热锅串串", "滁州", "2.5", "2.0", "-10.0%", "L2", "2026-09-28"]
        self.assertTrue(D.validate(rows(bad)))

    def test_empty_required_field_rejected(self):
        bad = ["定价", "热锅串串", "滁州", "", "2.0", "", "L2", "2026-09-28"]
        self.assertTrue(D.validate(rows(bad)))


class TestDeviation(unittest.TestCase):
    def test_dev_pct(self):
        self.assertEqual(D.dev_pct("2.5", "2.0"), "-20.0%")
        self.assertEqual(D.dev_pct("0", "1"), "")  # 预测 0 不算，留空
        self.assertEqual(D.dev_pct("abc", "1"), "")

    def test_credibility_groups_by_cat_city(self):
        got = D.credibility(rows(FULL,
                                 ["财务测算", "热锅串串", "滁州", "0.10", "0.13",
                                  "+30.0%", "L2", "2026-09-28"]))
        self.assertEqual(len(got), 1)
        cat, city, n, avg = got[0]
        self.assertEqual((cat, city, n), ("热锅串串", "滁州", 2))
        self.assertAlmostEqual(avg, 25.0)

    def test_missing_data_reports_gap(self):
        """无实测数据：报缺口、退出码 2，不输出空结论。"""
        saved = D.MAP
        D.MAP = Path("/tmp/不存在的偏差地图.csv")
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                code = D.main([])
        finally:
            D.MAP = saved
        self.assertEqual(code, 2)
        self.assertIn("缺数据", buf.getvalue())


class TestCoversThreeTargets(unittest.TestCase):
    def test_fields_cover_docs_layer2_targets(self):
        """docs/AI 辅助开店.md 第二层三件事都能落到本 schema。"""
        targets = [
            ["定价", "热锅串串", "滁州", "2.5", "2.0", "-20.0%", "L2", "2026-09-28"],
            ["财务测算", "热锅串串", "滁州", "0.10", "0.13", "+30.0%", "L2", "2026-09-28"],
            ["定价", "热锅串串", "滁州", "55", "48", "-12.7%", "L2", "2026-09-28"],
        ]
        self.assertEqual(D.validate(rows(*targets)), [])


if __name__ == "__main__":
    unittest.main()
