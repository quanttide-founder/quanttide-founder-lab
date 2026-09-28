"""review 往返的回归：导出同构、写回推进、L2 拒绝、幂等。无图形环境可跑。"""
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
            {"type": "L1缺口", "folder": "待回填", "title": "翻台率：L1 回填",
             "hint": "…", "meta": "…", "date": "2026-09-28"},
        ],
        "state": {"feedback": {}},
    }


def card(title: str, tag: str = "", text: str = "", created: str = "2026-09-28T10:00:00") -> dict:
    result = []
    if tag:
        result.append({"type": "choices", "value": {"choices": [tag]}})
    if text:
        result.append({"type": "textarea", "value": {"text": [text]}})
    return {"data": {"title": title},
            "annotations": [{"result": result, "created_at": created}]}


def rows_fixture(level: str = "L0") -> list[list[str]]:
    return [["市场调研", "80", "仅通用知识，未喂本地数据", "A", level, "待验证"]]


class TestExport(unittest.TestCase):
    def test_card_shape_matches_task_board(self):
        """与 task-board export 同构：data.title/hint/meta 齐全，title 作主键。"""
        cards = RX.build_cards(queue_fixture())
        d = cards[0]["data"]
        for key in ("title", "type", "folder", "rec", "hint", "meta", "tag", "text",
                    "project", "sub"):
            self.assertIn(key, d)
        self.assertEqual(cards[0]["id"], 1)

    def test_build_covers_three_sources(self):
        """队列条目与三处素材一一对应：对照表 L0 行 + GAPS，无重复。"""
        tasks = RX.build_tasks()
        titles = [t["title"] for t in tasks]
        self.assertEqual(len(titles), len(set(titles)))
        table_rows = assess.read_csv(RX.TABLE)
        l0 = [r for r in table_rows if r[4] == "L0"]
        gaps = len(__import__("ledger").GAPS)
        self.assertEqual(len(tasks), len(l0) + gaps)
        kinds = {t["type"] for t in tasks}
        self.assertEqual(kinds, {"L0假设", "C类判断", "L1缺口"})


class TestMerge(unittest.TestCase):
    def test_l0_advances_to_l1(self):
        """带理由的标注 → 对照表证据等级 L0→L1。"""
        q, rows = queue_fixture(), rows_fixture()
        done, adv, unm, ref = RM.merge([card("市场调研：L0 分 80 采不采纳", "先做",
                                             "滁州本地已有公开竞品数据可喂")], q, rows)
        self.assertEqual((done, adv, unm, ref), (1, 1, 0, []))
        self.assertEqual(rows[0][4], "L1")
        self.assertEqual(q["state"]["feedback"]["市场调研：L0 分 80 采不采纳"]["tag"], "先做")

    def test_l2_refused(self):
        """实地校准（L2）的结论不被标注覆盖。"""
        q, rows = queue_fixture(), rows_fixture(level="L2")
        done, adv, unm, ref = RM.merge([card("市场调研：L0 分 80 采不采纳", "不做", "改判")],
                                       q, rows)
        self.assertEqual(rows[0][4], "L2")
        self.assertEqual(len(ref), 1)
        self.assertEqual(adv, 0)
        self.assertEqual(done, 1)  # feedback 照记，只有对照表被拒

    def test_dissent_marks_falsified(self):
        q, rows = queue_fixture(), rows_fixture()
        RM.merge([card("市场调研：L0 分 80 采不采纳", "有异议", "分数虚高")], q, rows)
        self.assertEqual(rows[0][5], "已证伪")

    def test_merge_idempotent(self):
        """同标注重复导入不重复记账。"""
        q, rows = queue_fixture(), rows_fixture()
        c = [card("市场调研：L0 分 80 采不采纳", "先做", "理由")]
        RM.merge(c, q, rows)
        done2, _, _, _ = RM.merge(c, q, rows)
        self.assertEqual(done2, 0)
        fb = q["state"]["feedback"]["市场调研：L0 分 80 采不采纳"]
        self.assertEqual(len(fb["history"]), 1)

    def test_unmatched_title_skipped(self):
        q, rows = queue_fixture(), rows_fixture()
        done, _, unm, _ = RM.merge([card("不存在的环节", "先做")], q, rows)
        self.assertEqual((done, unm), (0, 1))

    def test_gap_item_only_feedback(self):
        """L1 缺口条目不在对照表，只写 feedback。"""
        q, rows = queue_fixture(), rows_fixture()
        done, adv, _, _ = RM.merge([card("翻台率：L1 回填", "缓做", "下周探店带回")], q, rows)
        self.assertEqual((done, adv), (1, 0))
        self.assertIn("翻台率：L1 回填", q["state"]["feedback"])


if __name__ == "__main__":
    unittest.main()
