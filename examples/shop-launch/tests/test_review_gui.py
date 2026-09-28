"""review_gui 数据层的回归：复核标注、写盘校验、纠偏记录追加。无图形环境可跑。"""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import assess  # noqa: E402
import review_gui as G  # noqa: E402


def rows_fixture() -> list[list[str]]:
    return [
        ["市场调研", "80", "能梳理赛道逻辑、竞品打法、行业趋势；仅通用知识，未喂本地数据",
         "A", "L0", "待验证", "依A·标尺·检查§1", "未复核"],
        ["底料/供应链", "60", "能给采购渠道与选品标准，但闻不到、尝不出",
         "A + C", "L0", "待验证", "依A·依C·标尺·检查§2", "未复核"],
    ]


class TestApplyReview(unittest.TestCase):
    def test_keep_preserves_values(self):
        rows, msg = G.apply_review(rows_fixture(), "市场调研", "维持")
        self.assertEqual(rows[0][7], "维持")
        self.assertEqual(rows[0][1], "80")  # 判决值不变
        self.assertIn("维持", msg)

    def test_revise_updates_row(self):
        rows, msg = G.apply_review(rows_fixture(), "市场调研", "改判",
                                   score="65", basis="改判·探店", reason="竞品信息充分")
        self.assertEqual(rows[0][1], "65")
        self.assertEqual(rows[0][6], "改判·探店")
        self.assertEqual(rows[0][7], "改判")
        self.assertIn("80 → 65", msg)

    def test_revise_without_reason_rejected(self):
        with self.assertRaises(ValueError):
            G.apply_review(rows_fixture(), "市场调研", "改判", score="65")

    def test_revise_without_score_rejected(self):
        with self.assertRaises(ValueError):
            G.apply_review(rows_fixture(), "市场调研", "改判", reason="理由")

    def test_bad_score_rejected(self):
        for bad in ("abc", "120", "-5"):
            with self.assertRaises(ValueError):
                G.apply_review(rows_fixture(), "市场调研", "改判", score=bad, reason="理由")

    def test_unknown_row_rejected(self):
        with self.assertRaises(ValueError):
            G.apply_review(rows_fixture(), "不存在", "维持")

    def test_bad_verdict_rejected(self):
        with self.assertRaises(ValueError):
            G.apply_review(rows_fixture(), "市场调研", "存疑")


class TestCommit(unittest.TestCase):
    def _paths(self):
        tmp = Path(tempfile.mkdtemp())
        table, log = tmp / "能力对照表.csv", tmp / "纠偏记录.md"
        with open(table, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(assess.HEADER)
            w.writerows(rows_fixture())
        log.write_text(
            "# 纠偏记录\n\n说明。\n\n| 日期 | 层 | 对象 | 标注 | 分诊 | 影响行数 | 备注 |\n"
            "|------|----|------|------|------|----------|------|\n"
            "| 2026-09-28 | 成文法 | 示例 | 示例 | 通则 | 0 | 首条 |\n",
            encoding="utf-8")
        return table, log

    def test_commit_writes_table_and_log(self):
        table, log = self._paths()
        msg = G.commit(rows_fixture(), "市场调研", "改判", score="65",
                       reason="竞品信息充分", table=table, log=log)
        self.assertIn("80 → 65", msg)
        saved = assess.read_csv(table)
        self.assertEqual(saved[0][7], "改判")
        text = log.read_text(encoding="utf-8")
        self.assertIn("| 判例 | 市场调研 | 改判 80 → 65 | 个例 | 1 | 竞品信息充分 |", text)
        # 首条原样保留，新行插在表头之后
        self.assertLess(text.index("首条"), text.index("市场调研"))

    def test_commit_keep_does_not_touch_log(self):
        table, log = self._paths()
        before = log.read_text(encoding="utf-8")
        G.commit(rows_fixture(), "市场调研", "维持", table=table, log=log)
        self.assertEqual(log.read_text(encoding="utf-8"), before)
        self.assertEqual(assess.read_csv(table)[0][7], "维持")

    def test_commit_refuses_invalid_rows_without_writing(self):
        """改判后若违反一致性检查（纯 A < 60），拒绝写盘且不留半截文件。"""
        table, log = self._paths()
        before = table.read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            G.commit(rows_fixture(), "市场调研", "改判", score="50", reason="理由",
                     table=table, log=log)
        self.assertEqual(table.read_text(encoding="utf-8"), before)


class TestValidateReuse(unittest.TestCase):
    def test_gui_rule_is_assess_rule(self):
        """GUI 保存前的校验与 assess check 同源，不复制规则。"""
        rows = rows_fixture()
        self.assertEqual(assess.validate_rows(rows), [])
        rows[0][1] = "50"  # 违反检查§1：纯 A < 60
        self.assertTrue(assess.validate_rows(rows))


if __name__ == "__main__":
    unittest.main()
