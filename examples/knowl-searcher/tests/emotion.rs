//! 第二轮纯函数回归：日志段切分与坐标、查询分组、分块、首句、出口阈值、聚线、标注合并。
//!
//! 全部用内存夹具，不依赖真实资产目录，也不打网络。

use knowl_searcher::corpus::{Doc, Kind};
use knowl_searcher::embed::EMBED_CHARS;
use knowl_searcher::emotion::{
    GROUP_DIARY, LINE_TAU, TAU1, TAU2, TOP3, Verdict, build_queries, cluster_lines, date_of,
    embed_chunks, first_sentence, merge_related, segments_from_docs, verdict,
};

fn doc(path: &str, kind: Kind, text: &str) -> Doc {
    Doc {
        path: path.to_string(),
        kind,
        text: text.to_string(),
    }
}

#[test]
fn segments_split_by_separator_with_coordinates() {
    let text = "第一段。\n\n---\n\n第二段。\n---\n第三段。\n";
    let docs = vec![doc(
        "memory/default/journal/2026-09-23.md",
        Kind::Journal,
        text,
    )];
    let segs = segments_from_docs(&docs);

    assert_eq!(
        segs.len(),
        3,
        "三段：{:?}",
        segs.iter().map(|s| &s.id).collect::<Vec<_>>()
    );
    assert_eq!(segs[0].id, "memory/default/journal/2026-09-23.md#1");
    assert_eq!(segs[1].index, 2);
    assert_eq!(segs[2].index, 3);
    assert_eq!(segs[0].line_start, 1);
    assert_eq!(segs[1].line_start, 4, "分隔线之后开新段");
    assert_eq!(segs[1].line_end, 5);
    assert_eq!(segs[0].date, "2026-09-23");
    assert!(segs.iter().all(|s| s.text.trim() != "---"));
}

#[test]
fn segment_ids_are_unique_across_sets() {
    let docs = vec![
        doc(
            "memory/default/journal/2026-09-23.md",
            Kind::Journal,
            "甲。\n---\n乙。\n",
        ),
        doc(
            "memory/fiction/journal/2026-09-22.md",
            Kind::Journal,
            "丙。\n",
        ),
        doc("memory/game/2026-09-27.md", Kind::Journal, "丁。\n"),
    ];
    let segs = segments_from_docs(&docs);
    let mut ids: Vec<&str> = segs.iter().map(|s| s.id.as_str()).collect();
    let before = ids.len();
    ids.sort();
    ids.dedup();
    assert_eq!(ids.len(), before, "id 必须全局唯一");
}

#[test]
fn date_only_from_real_date_filename() {
    assert_eq!(
        date_of("memory/default/journal/2026-09-23.md"),
        "2026-09-23"
    );
    assert_eq!(date_of("memory/game/2026-09-27.md"), "2026-09-27");
    assert_eq!(date_of("memory/default/README.md"), "");
    assert_eq!(date_of("memory/default/2026-9-23.md"), "");
}

#[test]
fn queries_take_only_emotion_diaries() {
    let docs = vec![
        doc(
            "fiction/观察站/1_情绪日记/失败感.md",
            Kind::Emotion,
            "今天听了一个店主。",
        ),
        doc(
            "fiction/重生言情/1_灵感/命运.md",
            Kind::Chapter,
            "命运是什么。",
        ),
        doc(
            "fiction/职场言情/2_场景/3_展会再遇.md",
            Kind::Chapter,
            "展会又见面了。",
        ),
        doc(
            "memory/default/journal/2026-09-23.md",
            Kind::Journal,
            "随手写的。",
        ),
    ];
    let qs = build_queries(&docs);

    assert_eq!(qs.len(), 1, "本轮只取情绪日记: {qs:?}");
    assert_eq!(qs[0].path, "fiction/观察站/1_情绪日记/失败感.md");
    assert_eq!(qs[0].group, GROUP_DIARY);
}

#[test]
fn embed_chunks_cover_long_text_without_loss() {
    let long: String = "很".repeat(1200);
    let chunks = embed_chunks(&long);
    assert!(chunks.len() > 1, "超长文本必须分块");
    let total: usize = chunks.iter().map(|c| c.chars().count()).sum();
    assert!(total >= 1200, "分块有重叠，字符总量不应少于原文: {total}");
    assert!(chunks.iter().all(|c| c.chars().count() <= EMBED_CHARS));
    assert_eq!(embed_chunks("短文本").len(), 1);
    assert!(embed_chunks("   ").is_empty());
}

#[test]
fn first_sentence_stops_at_punctuation_or_newline() {
    assert_eq!(
        first_sentence("今天想写的是拿下订单的过程。想写别的"),
        "今天想写的是拿下订单的过程"
    );
    assert_eq!(first_sentence("第一行\n第二行"), "第一行");
    assert_eq!(first_sentence("？没有句号"), "？没有句号");
    let empty_start = first_sentence("\n\n开头没有标点的一句话，很长很长");
    assert!(!empty_start.is_empty(), "空首句要回退到前 40 字");
}

#[test]
fn verdict_honours_preregistered_thresholds() {
    assert_eq!(verdict(TAU1 - 1e-9), Verdict::BelowFloor);
    assert_eq!(verdict(TAU1), Verdict::Remind, "等于 τ₁ 不丢弃");
    assert_eq!(verdict(TAU2), Verdict::Remind, "等于 τ₂ 不算已写过");
    assert_eq!(verdict(TAU2 + 1e-9), Verdict::AlreadyWritten);
    assert!(TAU1 < LINE_TAU && LINE_TAU < TAU2);
}

#[test]
fn lines_chain_by_single_linkage() {
    // 0-1 相似、1-2 相似、0-2 不相似 → 单链接应连成一条线
    let group = cluster_lines(4, |a, b| {
        let pair = (a.min(b), a.max(b));
        matches!(pair, (0, 1) | (1, 2))
    });
    assert_eq!(group, vec![vec![0, 1, 2]], "孤立成员 3 不进线");
    assert_eq!(
        cluster_lines(1, |_, _| true),
        Vec::<Vec<usize>>::new(),
        "单成员不构成线"
    );
}

#[test]
fn related_parts_merge_and_reject_conflicts() {
    let a = r#"{
        "rule": "能提供提醒价值判相关",
        "related": {"fiction/a.md": {"m/x.md#1": true}},
        "notes": {"fiction/a.md": "同事件"}
    }"#;
    let b = r#"{
        "related": {"fiction/a.md": {"m/y.md#2": false}, "fiction/b.md": {"m/z.md#1": true}},
        "notes": {"fiction/b.md": "同场景"}
    }"#;
    let set = merge_related(&[a, b]).expect("分片可合并");
    assert_eq!(set.labels["fiction/a.md"]["m/x.md#1"], true);
    assert_eq!(set.labels["fiction/a.md"]["m/y.md#2"], false);
    assert_eq!(set.labels["fiction/b.md"]["m/z.md#1"], true);
    assert!(set.notes["fiction/a.md"].contains("同事件"));

    let clash = r#"{"related": {"fiction/a.md": {"m/x.md#1": false}}}"#;
    assert!(merge_related(&[a, clash]).is_err(), "同键不同值必须报冲突");
    assert!(merge_related(&["{不是 json}"]).is_err());
}

#[test]
fn top3_is_a_prefix_of_retrieve_k() {
    assert!(TOP3 <= 8, "评判截断必须不超过检索条数");
}
