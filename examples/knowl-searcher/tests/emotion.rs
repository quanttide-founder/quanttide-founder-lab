//! 第二轮纯函数回归：日志段切分与坐标、首句、出口阈值、聚线、金标解析。
//!
//! 全部用内存夹具，不依赖真实资产目录，也不打网络。

use knowl_searcher::corpus::{Doc, Kind};
use knowl_searcher::emotion::{
    LINE_TAU, TAU1, TAU2, TOP3, Verdict, cluster_lines, date_of, first_sentence, parse_gold,
    parse_related, segments_from_docs, verdict,
};

fn journal(path: &str, text: &str) -> Doc {
    Doc {
        path: path.to_string(),
        kind: Kind::Journal,
        text: text.to_string(),
    }
}

#[test]
fn segments_split_by_separator_with_coordinates() {
    let text = "第一段。\n\n---\n\n第二段。\n---\n第三段。\n";
    let docs = vec![journal("memory/default/journal/2026-09-23.md", text)];
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
fn segment_ids_are_unique_across_files() {
    let docs = vec![
        journal("memory/default/journal/2026-09-23.md", "甲。\n---\n乙。\n"),
        journal("memory/default/2026-09-27.md", "丙。\n"),
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
    assert_eq!(date_of("memory/default/2026-09-27.md"), "2026-09-27");
    assert_eq!(date_of("memory/default/README.md"), "");
    assert_eq!(date_of("memory/default/2026-9-23.md"), "");
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
fn gold_file_parses_with_empty_meaning_uncovered() {
    let raw = r#"{
        "rule": "同事件或同情绪对象的默认日志段 id；空数组表示无金标",
        "annotator": "tester",
        "gold": {
            "fiction/观察站/1_情绪日记/失败感.md": ["memory/default/journal/2026-09-26.md#5"],
            "fiction/观察站/1_情绪日记/隐形劳动.md": []
        }
    }"#;
    let g = parse_gold(raw).expect("金标可解析");
    assert!(g.rule.contains("日志段"));
    assert_eq!(
        g.gold["fiction/观察站/1_情绪日记/失败感.md"],
        vec!["memory/default/journal/2026-09-26.md#5"]
    );
    assert!(g.gold["fiction/观察站/1_情绪日记/隐形劳动.md"].is_empty());
    assert!(parse_gold("{不是 json}").is_err());
}

#[test]
fn related_labels_are_diagnostic_input() {
    let raw = r#"{
        "rule": "诊断口径：能提供提醒价值判相关",
        "annotator": "tester",
        "related": {
            "fiction/观察站/1_情绪日记/失败感.md": {
                "memory/default/journal/2026-09-26.md#3": true,
                "memory/default/journal/2026-09-20.md#1": false
            }
        },
        "notes": { "fiction/观察站/1_情绪日记/失败感.md": "同一情绪处境" }
    }"#;
    let r = parse_related(raw).expect("相关性标注可解析");
    let labels = &r.related["fiction/观察站/1_情绪日记/失败感.md"];
    assert_eq!(labels["memory/default/journal/2026-09-26.md#3"], true);
    assert_eq!(labels["memory/default/journal/2026-09-20.md#1"], false);
    assert!(r.notes["fiction/观察站/1_情绪日记/失败感.md"].contains("情绪"));
}

#[test]
fn top3_is_a_prefix_of_retrieve_k() {
    assert!(TOP3 <= 8, "评判截断必须不超过检索条数");
}
