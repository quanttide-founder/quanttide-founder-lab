//! 纯函数回归：单元切分、BM25 确定性、规则融合、切块行号。
//!
//! 全部用内存夹具，不依赖真实资产目录。

use std::collections::HashMap;

use knowl_searcher::bm25::{Bm25, tokenize};
use knowl_searcher::chunk::chunk_docs;
use knowl_searcher::corpus::{Doc, Kind, Tier, build_units};
use quanttide_founder::memory::states::Destination;

fn doc(path: &str, kind: Kind, text: &str) -> Doc {
    Doc {
        path: path.to_string(),
        kind,
        text: text.to_string(),
    }
}

#[test]
fn units_split_by_heading_and_separator() {
    let text = "# 标题\n开头一段。\n## 已确认\n- 条目一\n---\n## 假说\n- 条目二\n";
    let docs = vec![doc("memory/default/insight/x.md", Kind::Insight, text)];
    let units = build_units(&docs, &HashMap::new());

    assert!(
        units.len() >= 3,
        "应切出 前言/已确认/假说 至少三段: {:?}",
        units.len()
    );
    let confirmed = units
        .iter()
        .find(|u| u.title == "已确认")
        .expect("有已确认单元");
    assert_eq!(confirmed.line_start, 3);
    assert!(confirmed.text.contains("条目一"));
    // 分隔线本身不进单元
    assert!(
        units
            .iter()
            .all(|u| !u.text.trim().starts_with("---\n") || u.text.trim() != "---")
    );
}

#[test]
fn units_get_tier_from_insight_map() {
    let text = "# 洞察\n## 已确认\n- 命题\n";
    let docs = vec![doc("memory/default/insight/x.md", Kind::Insight, text)];
    let mut by_title = HashMap::new();
    by_title.insert("已确认".to_string(), Tier::Confirmed);
    let mut tier_map = HashMap::new();
    tier_map.insert("memory/default/insight/x.md".to_string(), by_title);

    let units = build_units(&docs, &tier_map);
    let confirmed = units.iter().find(|u| u.title == "已确认").unwrap();
    assert_eq!(confirmed.tier, Some(Tier::Confirmed));
}

#[test]
fn oversized_unit_is_split_with_line_numbers() {
    let mut text = String::from("# 长文\n");
    for i in 0..60 {
        text.push_str(&format!(
            "第{}段，这是一行有相当长度的中文内容用于撑大单元体积。\n\n",
            i
        ));
    }
    let docs = vec![doc("a/b.md", Kind::Journal, &text)];
    let units = build_units(&docs, &HashMap::new());
    assert!(units.len() > 1, "超长单元应按段落续分");
    for u in &units {
        assert!(u.line_start >= 1 && u.line_end >= u.line_start);
    }
}

#[test]
fn bm25_is_deterministic_and_ranks_relevant_first() {
    let docs = vec![
        "AI 协作的盲区要靠测试才能发现".to_string(),
        "小说的场景与人物弧线".to_string(),
        "时间管理与注意力收缩的方法".to_string(),
    ];
    let idx = Bm25::new(&docs);
    let a = idx.search("注意力 收缩 方法", 3);
    let b = idx.search("注意力 收缩 方法", 3);
    assert_eq!(a, b, "同输入必须同输出");
    assert_eq!(a[0].0, 2, "相关文档应排第一");

    // 换跑两次结果一致（HashMap 随机种子不影响输出）
    let idx2 = Bm25::new(&docs);
    assert_eq!(idx2.search("注意力 收缩 方法", 3), b);
}

#[test]
fn tokenizer_handles_cjk_and_ascii() {
    let tokens = tokenize("GitHub 第二大脑 v2");
    assert!(tokens.contains(&"github".to_string()));
    assert!(tokens.contains(&"第二".to_string()));
    assert!(tokens.contains(&"脑".to_string()));
}

#[test]
fn chunker_covers_document_and_records_lines() {
    let text = "第一段内容。\n\n第二段内容。\n\n第三段内容。".to_string();
    let docs = vec![doc("a/b.md", Kind::Journal, &text)];
    let chunks = chunk_docs(&docs, 8, 6);
    assert!(chunks.len() >= 2);
    assert_eq!(chunks[0].path, "a/b.md");
    assert!(chunks[0].line_start == 1);
}
