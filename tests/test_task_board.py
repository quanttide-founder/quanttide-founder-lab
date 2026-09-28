"""任务看板固定测试：锁定读写、状态与清单生成行为。

运行：python3 -m unittest discover -s tests
不依赖图形界面；修改 examples/task-board/task_board.py 前后都应保持全绿。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "examples" / "task-board"))

import task_board  # noqa: E402

FIXTURE = REPO / "data" / "write" / "2026-09-06-任务扫描.json"


FOLDERS = {"职场言情", "重生言情", "校园言情", "草稿箱"}


def sample_data():
    return {
        "title": "测试",
        "sub": "",
        "tasks": [
            {"type": "补写", "folder": "职场言情", "rec": True,
             "title": "任务甲", "hint": "甲的提示", "meta": "m"},
            {"type": "定稿", "folder": "草稿箱", "rec": False,
             "title": "任务乙", "hint": "乙的提示", "meta": "m"},
        ],
        "state": {"feedback": {}},
    }


class FixtureTest(unittest.TestCase):
    """固定数据契约：看板依赖的字段结构不被无意破坏。"""

    def setUp(self):
        self.data = task_board.load(FIXTURE)

    def test_task_count(self):
        self.assertEqual(len(self.data["tasks"]), 10)

    def test_required_keys(self):
        for t in self.data["tasks"]:
            for key in ("type", "folder", "title", "hint", "meta"):
                self.assertIn(key, t, f"任务缺少字段 {key}：{t.get('title')}")

    def test_rec_optional_bool(self):
        for t in self.data["tasks"]:
            self.assertIsInstance(t.get("rec", False), bool)

    def test_folder_is_real(self):
        """分类严格对应仓库文件夹，不得出现自造分组。"""
        for t in self.data["tasks"]:
            self.assertIn(t["folder"], FOLDERS,
                          f"分类不是真实文件夹：{t['folder']}（{t['title']}）")

    def test_titles_unique(self):
        titles = [t["title"] for t in self.data["tasks"]]
        self.assertEqual(len(titles), len(set(titles)))

    def test_state_defaults(self):
        self.assertIn("feedback", self.data["state"])


class RoundtripTest(unittest.TestCase):
    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            data = sample_data()
            data["state"] = {"feedback": {"任务甲": {"tag": "先做", "text": "意见"}}}
            task_board.save(p, data)
            loaded = task_board.load(p)
            self.assertEqual(loaded["state"]["feedback"]["任务甲"]["tag"], "先做")

    def test_save_leaves_no_tmp(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            task_board.save(p, sample_data())
            self.assertEqual(list(Path(d).glob("*.tmp")), [])
            self.assertEqual(list(Path(d).glob("*.json")), [p])


class PruneFeedbackTest(unittest.TestCase):
    def test_empty_entries_dropped(self):
        state = {"feedback": {
            "甲": {"tag": "", "text": ""},
            "乙": {"tag": "先做", "text": ""},
            "丙": {"tag": "", "text": "理由"},
        }}
        pruned = task_board.prune_feedback(state)
        self.assertIn("乙", pruned["feedback"])
        self.assertIn("丙", pruned["feedback"])
        self.assertNotIn("甲", pruned["feedback"])

    def test_history_only_kept(self):
        state = {"feedback": {
            "甲": {"tag": "", "text": "", "history": [{"time": "09-06 10:00", "tag": "不做", "text": ""}]},
        }}
        self.assertIn("甲", task_board.prune_feedback(state)["feedback"])

    def test_all_empty_gives_empty(self):
        state = {"feedback": {"甲": {"tag": "", "text": ""}}}
        self.assertEqual(task_board.prune_feedback(state)["feedback"], {})


class RecordTest(unittest.TestCase):
    def test_record_updates_and_appends(self):
        entry = {}
        task_board.record(entry, "先做", "趁热打铁", "09-06 10:00")
        self.assertEqual(entry["tag"], "先做")
        self.assertEqual(entry["text"], "趁热打铁")
        self.assertEqual(entry["history"], [{"time": "09-06 10:00", "tag": "先做", "text": "趁热打铁"}])

    def test_record_keeps_previous_tag(self):
        entry = {"tag": "先做"}
        task_board.record(entry, "", "补充理由", "09-06 10:01")
        self.assertEqual(entry["tag"], "先做")
        self.assertEqual(entry["history"][0]["tag"], "先做")

    def test_record_twice_gives_two_history(self):
        entry = {}
        task_board.record(entry, "先做", "一", "09-06 10:00")
        task_board.record(entry, "缓做", "改主意了", "09-06 10:05")
        self.assertEqual(entry["tag"], "缓做")
        self.assertEqual(len(entry["history"]), 2)


class DataFileTest(unittest.TestCase):
    """数据目录与默认路径行为。"""

    def test_default_dir_has_json(self):
        self.assertTrue(list(task_board.DEFAULT_DIR.glob("*.json")),
                        f"{task_board.DEFAULT_DIR} 下应有数据文件")

    def test_fixture_is_valid_json(self):
        with open(FIXTURE, encoding="utf-8") as f:
            json.load(f)


class ExportMergeTest(unittest.TestCase):
    """Label Studio 往返：导出携带意见，标注写回并幂等。"""

    def exported(self, tag="先做", text="理由"):
        return [{
            "data": {"title": "任务甲"},
            "annotations": [{
                "created_at": "2026-09-28T10:00:00.000000Z",
                "result": [
                    {"type": "choices", "value": {"choices": [tag]}},
                    {"type": "textarea", "value": {"text": [text]}},
                ],
            }],
        }]

    def test_build_tasks_carries_feedback(self):
        data = sample_data()
        data["state"]["feedback"] = {"任务甲": {"tag": "缓做", "text": "先放放"}}
        tasks = task_board.build_tasks(data)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["data"]["tag"], "缓做")
        self.assertEqual(tasks[1]["data"]["tag"], "")
        self.assertEqual(tasks[0]["id"], 1)

    def test_merge_writes_back(self):
        data = sample_data()
        done, unmatched = task_board.apply_annotations(data, self.exported(), now="09-28 18:00")
        fb = data["state"]["feedback"]["任务甲"]
        self.assertEqual((done, unmatched), (1, 0))
        self.assertEqual(fb["tag"], "先做")
        self.assertEqual(fb["text"], "理由")
        self.assertEqual(fb["history"][0]["time"], "09-28 18:00")

    def test_merge_is_idempotent(self):
        data = sample_data()
        task_board.apply_annotations(data, self.exported(), now="09-28 18:00")
        done, _ = task_board.apply_annotations(data, self.exported(), now="09-28 18:01")
        self.assertEqual(done, 0)
        self.assertEqual(len(data["state"]["feedback"]["任务甲"]["history"]), 1)

    def test_merge_skips_unknown_title(self):
        data = sample_data()
        exp = self.exported()
        exp[0]["data"]["title"] = "不存在的任务"
        done, unmatched = task_board.apply_annotations(data, exp)
        self.assertEqual((done, unmatched), (0, 1))

    def test_merge_takes_last_annotation(self):
        data = sample_data()
        exp = self.exported()
        exp[0]["annotations"].append({
            "created_at": "2026-09-28T11:00:00.000000Z",
            "result": [
                {"type": "choices", "value": {"choices": ["不做"]}},
                {"type": "textarea", "value": {"text": ["改主意了"]}},
            ],
        })
        task_board.apply_annotations(data, exp)
        fb = data["state"]["feedback"]["任务甲"]
        self.assertEqual(fb["tag"], "不做")
        self.assertEqual(fb["text"], "改主意了")


if __name__ == "__main__":
    unittest.main()
