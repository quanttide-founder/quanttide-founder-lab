//! 判定指标：金标在 top-k 内的命中、召回、倒数排名、改写稳定性。

/// 命中判定：金标路径与命中路径后缀互含（两侧都是相对 `assets/` 的路径）。
pub fn is_gold(hit: &str, gold: &str) -> bool {
    hit == gold || hit.ends_with(gold) || gold.ends_with(hit)
}

/// Recall@k：命中的金标数 / 金标总数。
pub fn recall_at_k(hits: &[String], gold: &[String]) -> f64 {
    if gold.is_empty() {
        return 0.0;
    }
    let found = gold
        .iter()
        .filter(|g| hits.iter().any(|h| is_gold(h, g)))
        .count();
    found as f64 / gold.len() as f64
}

/// 命中：任一金标进入 top-k。
pub fn hit(hits: &[String], gold: &[String]) -> bool {
    hits.iter().any(|h| gold.iter().any(|g| is_gold(h, g)))
}

/// 倒数排名：首个金标所在名次的倒数，未命中为 0。
pub fn reciprocal_rank(hits: &[String], gold: &[String]) -> f64 {
    for (i, h) in hits.iter().enumerate() {
        if gold.iter().any(|g| is_gold(h, g)) {
            return 1.0 / (i + 1) as f64;
        }
    }
    0.0
}

/// 改写稳定性：两个结果路径集合的 Jaccard。
pub fn jaccard(a: &[String], b: &[String]) -> f64 {
    let sa: std::collections::HashSet<&str> = a.iter().map(String::as_str).collect();
    let sb: std::collections::HashSet<&str> = b.iter().map(String::as_str).collect();
    if sa.is_empty() && sb.is_empty() {
        return 1.0;
    }
    let inter = sa.intersection(&sb).count();
    let union = sa.union(&sb).count();
    inter as f64 / union as f64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn suffix_match_is_tolerant() {
        assert!(is_gold(
            "memory/default/insight/ai.md",
            "memory/default/insight/ai.md"
        ));
        assert!(is_gold("a/b/insight/ai.md", "insight/ai.md"));
        assert!(!is_gold(
            "memory/default/insight/ai.md",
            "roadmap/business.md"
        ));
    }

    #[test]
    fn recall_counts_multiple_golds() {
        let gold = vec!["m/a.md".to_string(), "m/b.md".to_string()];
        let hits = vec!["m/a.md".to_string(), "x/y.md".to_string()];
        assert!((recall_at_k(&hits, &gold) - 0.5).abs() < 1e-9);
        assert!(hit(&hits, &gold));
        assert!((reciprocal_rank(&hits, &gold) - 1.0).abs() < 1e-9);
    }

    #[test]
    fn jaccard_measures_overlap() {
        let a = vec!["1".to_string(), "2".to_string(), "3".to_string()];
        let b = vec!["2".to_string(), "3".to_string(), "4".to_string()];
        assert!((jaccard(&a, &b) - 0.5).abs() < 1e-9);
        assert!((jaccard(&a, &a) - 1.0).abs() < 1e-9);
    }
}
