//! 规则融合：用工具箱的三问路由与证据分级给语义候选重排。
//!
//! 权重在 `docs/experiment.md` 先注册后运行，不看结果回调参。

use quanttide_founder::memory::states::{Destination, route};

use crate::corpus::{Kind, Tier, Unit};

/// 路由亲和权重。
pub const W_ROUTE: f64 = 0.30;
/// 分级加权。
pub const W_TIER: f64 = 0.10;
/// fiction 单元的中性亲和（保证跨域联想可达，不被 memory 类型亲和打压）。
const FICTION_NEUTRAL: f64 = 0.20;

/// 查询路由到的去向与单元类型之间的亲和。
pub fn route_affinity(dest: Destination, kind: Kind) -> f64 {
    if kind.is_fiction() {
        return FICTION_NEUTRAL;
    }
    match dest {
        Destination::Insight => i64::from(kind == Kind::Insight) as f64,
        Destination::Profile => i64::from(kind == Kind::Profile) as f64,
        Destination::Roadmap => i64::from(kind == Kind::Roadmap) as f64,
        Destination::Journal => i64::from(kind == Kind::Journal) as f64,
        // intention 层尚未建模，语料里没有对应类型
        Destination::Intention => 0.0,
    }
}

/// 已确认级的加权。
pub fn tier_bonus(tier: Option<Tier>) -> f64 {
    if matches!(tier, Some(Tier::Confirmed)) {
        1.0
    } else {
        0.0
    }
}

/// 规则融合：基础分归一后加路由亲和与分级加权（`units` 与 `base` 对齐）。
pub fn fuse(base: &[f64], query: &str, units: &[&Unit]) -> Vec<f64> {
    let max = base.iter().fold(0.0f64, |a, &b| a.max(b));
    let max = if max > 0.0 { max } else { 1.0 };
    let dest = route(query);
    base.iter()
        .enumerate()
        .map(|(i, s)| {
            s / max
                + W_ROUTE * route_affinity(dest, units[i].kind)
                + W_TIER * tier_bonus(units[i].tier)
        })
        .collect()
}
