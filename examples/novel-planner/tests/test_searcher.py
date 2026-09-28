"""检索引擎纯函数回归：切段与坐标、查询分组、分块、首句、出口阈值、聚线、
标注合并、单元切分、BM25 确定性；外加对 data/segments.json 的语料一致性锁定。

除语料一致性一项（只读主仓库 assets/）外全部用内存夹具，不打网络。
运行：python3 -m unittest discover -s examples/novel-planner/tests
"""

import json
import sys
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import searcher  # noqa: E402

ASSETS = PROJECT.parents[3] / "assets"
SEGMENTS_JSON = PROJECT / "data" / "segments.json"


def doc(path, kind, text):
    return searcher.Doc(path, kind, text)


class SegmentSplitTest(unittest.TestCase):
    """日志段切分与坐标。"""

    def test_segments_split_by_separator_with_coordinates(self):
        text = "第一段。\n\n---\n\n第二段。\n---\n第三段。\n"
        segs = searcher.segments_from_docs([
            doc("memory/default/journal/2026-09-23.md", "journal", text)])

        self.assertEqual(len(segs), 3, [s.id for s in segs])
        self.assertEqual(segs[0].id, "memory/default/journal/2026-09-23.md#1")
        self.assertEqual(segs[1].index, 2)
        self.assertEqual(segs[2].index, 3)
        self.assertEqual(segs[0].line_start, 1)
        self.assertEqual(segs[1].line_start, 4, "分隔线之后开新段")
        self.assertEqual(segs[1].line_end, 5)
        self.assertEqual(segs[0].date, "2026-09-23")
        self.assertTrue(all(s.text.strip() != "---" for s in segs))

    def test_segment_ids_are_unique_across_sets(self):
        segs = searcher.segments_from_docs([
            doc("memory/default/journal/2026-09-23.md", "journal", "甲。\n---\n乙。\n"),
            doc("memory/fiction/journal/2026-09-22.md", "journal", "丙。\n"),
            doc("memory/game/2026-09-27.md", "journal", "丁。\n"),
        ])
        ids = [s.id for s in segs]
        self.assertEqual(len(ids), len(set(ids)), "id 必须全局唯一")

    def test_date_only_from_real_date_filename(self):
        self.assertEqual(searcher.date_of("memory/default/journal/2026-09-23.md"),
                         "2026-09-23")
        self.assertEqual(searcher.date_of("memory/game/2026-09-27.md"), "2026-09-27")
        self.assertEqual(searcher.date_of("memory/default/README.md"), "")
        self.assertEqual(searcher.date_of("memory/default/2026-9-23.md"), "")


class QueryTest(unittest.TestCase):
    def test_queries_take_only_emotion_diaries(self):
        docs = [
            doc("fiction/观察站/1_情绪日记/失败感.md", "emotion", "今天听了一个店主。"),
            doc("fiction/重生言情/1_灵感/命运.md", "chapter", "命运是什么。"),
            doc("fiction/职场言情/2_场景/3_展会再遇.md", "chapter", "展会又见面了。"),
            doc("memory/default/journal/2026-09-23.md", "journal", "随手写的。"),
        ]
        qs = searcher.build_queries(docs)

        self.assertEqual(len(qs), 1, qs)
        self.assertEqual(qs[0].path, "fiction/观察站/1_情绪日记/失败感.md")
        self.assertEqual(qs[0].group, searcher.GROUP_DIARY)


class TextTest(unittest.TestCase):
    def test_embed_chunks_cover_long_text_without_loss(self):
        long_text = "很" * 1200
        chunks = searcher.embed_chunks(long_text)
        self.assertGreater(len(chunks), 1, "超长文本必须分块")
        self.assertGreaterEqual(sum(len(c) for c in chunks), 1200,
                                "分块有重叠，字符总量不应少于原文")
        self.assertTrue(all(len(c) <= searcher.EMBED_CHARS for c in chunks))
        self.assertEqual(len(searcher.embed_chunks("短文本")), 1)
        self.assertEqual(searcher.embed_chunks("   "), [])

    def test_first_sentence_stops_at_punctuation_or_newline(self):
        self.assertEqual(
            searcher.first_sentence("今天想写的是拿下订单的过程。想写别的"),
            "今天想写的是拿下订单的过程")
        self.assertEqual(searcher.first_sentence("第一行\n第二行"), "第一行")
        self.assertEqual(searcher.first_sentence("？没有句号"), "？没有句号")
        fallback = searcher.first_sentence("\n\n开头没有标点的一句话，很长很长")
        self.assertTrue(fallback, "空首句要回退到前 40 字")


class RuleTest(unittest.TestCase):
    def test_verdict_honours_preregistered_thresholds(self):
        self.assertEqual(searcher.verdict(searcher.TAU1 - 1e-9), "below_floor")
        self.assertEqual(searcher.verdict(searcher.TAU1), "remind", "等于 τ₁ 不丢弃")
        self.assertEqual(searcher.verdict(searcher.TAU2), "remind", "等于 τ₂ 不算已写过")
        self.assertEqual(searcher.verdict(searcher.TAU2 + 1e-9), "already_written")
        self.assertTrue(searcher.TAU1 < searcher.LINE_TAU < searcher.TAU2)

    def test_lines_chain_by_single_linkage(self):
        # 0-1 相似、1-2 相似、0-2 不相似 → 单链接应连成一条线
        pairs = {(0, 1), (1, 2)}
        group = searcher.cluster_lines(
            4, lambda a, b: (min(a, b), max(a, b)) in pairs)
        self.assertEqual(group, [[0, 1, 2]], "孤立成员 3 不进线")
        self.assertEqual(searcher.cluster_lines(1, lambda a, b: True), [],
                         "单成员不构成线")

    def test_top3_is_a_prefix_of_retrieve_k(self):
        self.assertLessEqual(searcher.TOP3, searcher.RETRIEVE_K,
                             "评判截断必须不超过检索条数")


class RelatedTest(unittest.TestCase):
    A = json.dumps({
        "rule": "能提供提醒价值判相关",
        "related": {"fiction/a.md": {"m/x.md#1": True}},
        "notes": {"fiction/a.md": "同事件"},
    }, ensure_ascii=False)
    B = json.dumps({
        "related": {"fiction/a.md": {"m/y.md#2": False},
                    "fiction/b.md": {"m/z.md#1": True}},
        "notes": {"fiction/b.md": "同场景"},
    }, ensure_ascii=False)

    def test_related_parts_merge_and_reject_conflicts(self):
        merged = searcher.merge_related([self.A, self.B])
        self.assertTrue(merged.labels["fiction/a.md"]["m/x.md#1"])
        self.assertFalse(merged.labels["fiction/a.md"]["m/y.md#2"])
        self.assertTrue(merged.labels["fiction/b.md"]["m/z.md#1"])
        self.assertIn("同事件", merged.notes["fiction/a.md"])

        clash = json.dumps({"related": {"fiction/a.md": {"m/x.md#1": False}}})
        with self.assertRaisesRegex(ValueError, "标注冲突"):
            searcher.merge_related([self.A, clash])
        with self.assertRaises(Exception):
            searcher.merge_related(["{不是 json}"])


class UnitTest(unittest.TestCase):
    def test_units_split_by_heading_and_separator(self):
        text = "# 标题\n开头一段。\n## 已确认\n- 条目一\n---\n## 假说\n- 条目二\n"
        units = searcher.build_units([doc("memory/default/insight/x.md", "insight", text)])

        self.assertGreaterEqual(len(units), 3, "应切出 标题/已确认/假说 至少三段")
        confirmed = next(u for u in units if u.title == "已确认")
        self.assertEqual(confirmed.line_start, 3)
        self.assertIn("条目一", confirmed.text)
        self.assertEqual(confirmed.coord(), "insight:3-4")

    def test_oversized_unit_is_split_with_line_numbers(self):
        text = "# 长文\n" + "".join(
            f"第{i}段，这是一行有相当长度的中文内容用于撑大单元体积。\n\n"
            for i in range(60))
        units = searcher.build_units([doc("a/b.md", "journal", text)])

        self.assertGreater(len(units), 1, "超长单元应按段落续分")
        for u in units:
            self.assertGreaterEqual(u.line_start, 1)
            self.assertGreaterEqual(u.line_end, u.line_start)


class Bm25Test(unittest.TestCase):
    DOCS = [
        "AI 协作的盲区要靠测试才能发现",
        "小说的场景与人物弧线",
        "时间管理与注意力收缩的方法",
    ]

    def test_bm25_is_deterministic_and_ranks_relevant_first(self):
        idx = searcher.Bm25(self.DOCS)
        a = idx.search("注意力 收缩 方法", 3)
        b = idx.search("注意力 收缩 方法", 3)
        self.assertEqual(a, b, "同输入必须同输出")
        self.assertEqual(a[0][0], 2, "相关文档应排第一")
        self.assertEqual(searcher.Bm25(self.DOCS).search("注意力 收缩 方法", 3), b)

    def test_tokenizer_handles_cjk_and_ascii(self):
        tokens = searcher.tokenize("GitHub 第二大脑 v2")
        self.assertIn("github", tokens)
        self.assertIn("第二", tokens)
        self.assertIn("脑", tokens)


@unittest.skipUnless(ASSETS.is_dir() and SEGMENTS_JSON.is_file(),
                     "主仓库 assets/ 或 data/segments.json 缺失")
class CorpusParityTest(unittest.TestCase):
    """移植一致性：Python 切段必须与既有的 data/segments.json 完全一致。"""

    def test_segments_match_committed_json(self):
        got = [s.to_dict() for s in searcher.load_segments(ASSETS)]
        want = json.loads(SEGMENTS_JSON.read_text(encoding="utf-8"))
        self.assertEqual(got, want)


if __name__ == "__main__":
    unittest.main()
