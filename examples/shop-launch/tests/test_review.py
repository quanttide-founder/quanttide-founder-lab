"""裁决队列与状态式写回的回归：素材对应、推进规则、L2 拒绝、无理由不生效。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import assess  # noqa: E402
import review_export as RX  # noqa: E402
import review_merge as RM  # noqa: E402


def queue_fixture() -> dict:
    return {
        "title": "开店裁决队列", "sub": "测试",
        "tasks": [
            {"type": "L0假设", "folder": "市场调研", "title": "市场调研：L0 分 80 采不采纳",
             "hint": "…", "meta": "…", "date": "2026-09-28"},
            {"type": "C类判断", "folder": "底料/供应链", "title": "底料/供应链：C 类人工兜底裁决",
             "hint": "…", "meta": "…", "date": "2026-09-28"},
        ],
        "state": {"feedback": {}},
    }


def answer(queue: dict, title: str, tag: str, text: str) -> None:
    queue["state"]["feedback"][title] = {"tag": tag, "text": text}


def rows_fixture(level: str = "L0") -> list[list[str]]:
    return [["市场调研", "80", "仅通用知识，未喂本地数据", "A", level, "待验证"],
            ["底料/供应链", "60", "闻不到、尝不出，人工兜底", "A + C", level, "待验证"]]


class TestBuild(unittest.TestCase):
    def test_queue_covers_only_l0_rows(self):
        """队列 = 对照表 L0 行，一一对应无重复；L1 缺口不进队列。"""
        tasks = RX.build_tasks()
        titles = [t["title"] for t in tasks]
        self.assertEqual(len(titles), len(set(titles)))
        l0 = [r for r in assess.read_csv(RX.TABLE) if r[4] == "L0"]
        self.assertEqual(len(tasks), len(l0))

    def test_no_gap_type(self):
        """要数据的条目不进裁决队列（2026-09-28 纠偏）。"""
        kinds = {t["type"] for t in RX.build_tasks()}
        self.assertNotIn("L1缺口", kinds)
        self.assertTrue(kinds <= {"L0假设", "C类判断"})

    def test_c_row_asked_about_fallback(self):
        c = next(t for t in RX.build_tasks() if t["type"] == "C类判断")
        self.assertIn("兜底", c["hint"])


class TestApply(unittest.TestCase):
    def test_accept_advances_l0_to_l1(self):
        q, rows = queue_fixture(), rows_fixture()
        answer(q, "市场调研：L0 分 80 采不采纳", "采纳", "本地已有公开竞品数据可喂")
        adv, ref, pending = RM.apply(q, rows)
        self.assertEqual(rows[0][4], "L1")
        self.assertEqual((len(adv), len(ref), pending), (1, 0, 1))

    def test_reject_marks_falsified(self):
        q, rows = queue_fixture(), rows_fixture()
        answer(q, "市场调研：L0 分 80 采不采纳", "否决", "分数虚高，本地口味不成立")
        RM.apply(q, rows)
        self.assertEqual(rows[0][5], "已证伪")
        self.assertEqual(rows[0][4], "L0")  # 否决不推进等级

    def test_doubt_leaves_row_untouched(self):
        q, rows = queue_fixture(), rows_fixture()
        answer(q, "市场调研：L0 分 80 采不采纳", "存疑", "等探店数据")
        adv, _, _ = RM.apply(q, rows)
        self.assertEqual(adv, [])
        self.assertEqual(rows[0][4], "L0")

    def test_l2_refused(self):
        """实地校准（L2）的结论不被作答改写。"""
        q, rows = queue_fixture(), rows_fixture(level="L2")
        answer(q, "市场调研：L0 分 80 采不采纳", "否决", "改判")
        adv, ref, _ = RM.apply(q, rows)
        self.assertEqual(rows[0][4], "L2")
        self.assertEqual((len(adv), len(ref)), (0, 1))

    def test_no_reason_no_effect(self):
        """无理由的采纳/否决不生效。"""
        q, rows = queue_fixture(), rows_fixture()
        answer(q, "市场调研：L0 分 80 采不采纳", "采纳", "")
        RM.apply(q, rows)
        self.assertEqual(rows[0][4], "L0")

    def test_apply_idempotent(self):
        """状态式写回：重复跑不重复推进。"""
        q, rows = queue_fixture(), rows_fixture()
        answer(q, "市场调研：L0 分 80 采不采纳", "采纳", "理由")
        RM.apply(q, rows)
        adv2, _, _ = RM.apply(q, rows)
        self.assertEqual(adv2, [])
        self.assertEqual(rows[0][4], "L1")

    def test_pending_counts_unanswered(self):
        q, rows = queue_fixture(), rows_fixture()
        _, _, pending = RM.apply(q, rows)
        self.assertEqual(pending, 2)


if __name__ == "__main__":
    unittest.main()
