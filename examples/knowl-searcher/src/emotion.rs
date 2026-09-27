//! 第二轮：联想评测——从 memory 原始日志段里为加工中的创作素材捞出相关片段。
//!
//! - 索引：`assets/memory` 各集的日志按 `---` 切会话段（复用 [`crate::corpus::build_units`]，
//!   超 900 字按段续分），一段 = 一个检索单元，坐标 `path#index` + `line_start-line_end` + `date`
//! - 查询：加工侧的情绪日记草稿（`--query full` 全文为业务主档）
//! - 打分：文本按 480 字分块嵌入，查询与段的相似度取分块对的最大值（长文不吃截断亏）
//! - 裁决：τ₁ 共振下限、τ₂ 重复上限、聚线（段间相似度单链接）——规则只在出口生效
//! - 评判：top-3 相关率（主指标），金标 `data/related/*.json` 相关性标注，不回调参

use std::cell::RefCell;
use std::collections::HashMap;
use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::bm25::Bm25;
use crate::corpus::{Doc, Kind, build_units, load_corpus};
use crate::embed::{EMBED_CHARS, Embedder, cosine};

/// 预注册参数：跑完标定，不回调参。
pub const TAU1: f64 = 0.55;
pub const TAU2: f64 = 0.92;
/// 段间情绪线阈值（段-段分布与查询-段分布不同，分开标定）。
pub const LINE_TAU: f64 = 0.7;
/// 检索条数；评判截断到 [`TOP3`]。
pub const RETRIEVE_K: usize = 8;
pub const TOP3: usize = 3;
/// 分块步长（窗口 [`EMBED_CHARS`]，重叠 80 字）。
const CHUNK_STEP: usize = EMBED_CHARS - 80;

/// 查询分组（当前仅情绪日记，灵感/场景待后续轮次接入）。
pub const GROUP_DIARY: &str = "情绪日记";

/// 一个日志检索单元。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Segment {
    /// `path#index`，index 从 1 起，同文件内按行序。
    pub id: String,
    pub path: String,
    pub index: usize,
    pub line_start: usize,
    pub line_end: usize,
    /// 文件名日期（`YYYY-MM-DD`），非日期文件为空串。
    pub date: String,
    pub text: String,
}

/// 语料文件 → 检索单元（保序：同文件按行序编号）。
pub fn segments_from_docs(docs: &[Doc]) -> Vec<Segment> {
    let units = build_units(docs, &HashMap::new());
    let mut counter: HashMap<String, usize> = HashMap::new();
    units
        .into_iter()
        .map(|u| {
            let n = counter.entry(u.path.clone()).or_insert(0);
            *n += 1;
            Segment {
                id: format!("{}#{}", u.path, n),
                path: u.path.clone(),
                index: *n,
                line_start: u.line_start,
                line_end: u.line_end,
                date: date_of(&u.path),
                text: u.text,
            }
        })
        .collect()
}

/// 取路径文件名（去扩展名）作为日期，形如 `2026-09-23` 才算数。
pub fn date_of(path: &str) -> String {
    let stem = path.rsplit('/').next().unwrap_or(path);
    let stem = stem.strip_suffix(".md").unwrap_or(stem);
    let is_date = stem.len() == 10
        && stem
            .chars()
            .enumerate()
            .all(|(i, c)| c.is_ascii_digit() || ((i == 4 || i == 7) && c == '-'));
    if is_date {
        stem.to_string()
    } else {
        String::new()
    }
}

/// 全部集的原始日志段（语料 = 工具箱模型的 `journal` 层，跨 default / fiction / game 各集）。
pub fn load_segments(assets: &Path) -> Result<Vec<Segment>, Box<dyn Error>> {
    let (docs, _) = load_corpus(assets)?;
    let docs: Vec<Doc> = docs
        .into_iter()
        .filter(|d| d.kind == Kind::Journal && d.path.starts_with("memory/"))
        .collect();
    Ok(segments_from_docs(&docs))
}

/// 一条查询：加工侧的情绪日记。
#[derive(Debug, Clone, Serialize)]
pub struct QueryDoc {
    /// 相对 `assets/` 的路径。
    pub path: String,
    /// 分组，当前恒为 [`GROUP_DIARY`]。
    pub group: String,
    pub text: String,
}

/// 语料文件 → 查询集（纯函数，只取情绪日记）。
pub fn build_queries(docs: &[Doc]) -> Vec<QueryDoc> {
    let mut out: Vec<QueryDoc> = docs
        .iter()
        .filter(|d| d.kind == Kind::Emotion)
        .filter(|d| !d.path.ends_with("/README.md") && !d.text.trim().is_empty())
        .map(|d| QueryDoc {
            path: d.path.clone(),
            group: GROUP_DIARY.to_string(),
            text: d.text.clone(),
        })
        .collect();
    out.sort_by(|a, b| a.path.cmp(&b.path));
    out
}

/// 查询集（情绪日记）。
pub fn load_queries(assets: &Path) -> Result<Vec<QueryDoc>, Box<dyn Error>> {
    let (docs, _) = load_corpus(assets)?;
    Ok(build_queries(&docs))
}

/// 首句：到第一个句末标点或换行为止；为空则取前 40 字。
pub fn first_sentence(text: &str) -> String {
    let mut out = String::new();
    for ch in text.chars() {
        if matches!(ch, '。' | '！' | '？' | '!' | '?') || ch == '\n' {
            break;
        }
        out.push(ch);
    }
    let t = out.trim().trim_end_matches(['，', '、', ',']);
    if t.is_empty() {
        text.trim().chars().take(40).collect()
    } else {
        t.to_string()
    }
}

/// 长文本分块：窗口 [`EMBED_CHARS`]、步长 [`CHUNK_STEP`]，不足一窗则单块。
pub fn embed_chunks(text: &str) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    if chars.is_empty() {
        return Vec::new();
    }
    let mut out = Vec::new();
    let mut start = 0usize;
    while start < chars.len() {
        let end = (start + EMBED_CHARS).min(chars.len());
        let chunk: String = chars[start..end].iter().collect();
        if !chunk.trim().is_empty() {
            out.push(chunk);
        }
        if end == chars.len() {
            break;
        }
        start += CHUNK_STEP;
    }
    out
}

/// 出口裁决：低于 τ₁ 丢弃，高于 τ₂ 标「已写过」，其余提醒。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Verdict {
    BelowFloor,
    Remind,
    AlreadyWritten,
}

pub fn verdict(score: f64) -> Verdict {
    if score < TAU1 {
        Verdict::BelowFloor
    } else if score > TAU2 {
        Verdict::AlreadyWritten
    } else {
        Verdict::Remind
    }
}

/// 聚线：候选两两相似度过 [`LINE_TAU`] 单链接成情绪线；少于 2 个成员不构成线。
pub fn cluster_lines(n: usize, similar: impl Fn(usize, usize) -> bool) -> Vec<Vec<usize>> {
    let mut parent: Vec<usize> = (0..n).collect();
    fn find(parent: &mut [usize], x: usize) -> usize {
        if parent[x] != x {
            let root = find(parent, parent[x]);
            parent[x] = root;
        }
        parent[x]
    }
    for i in 0..n {
        for j in (i + 1)..n {
            if similar(i, j) {
                let (a, b) = (find(&mut parent, i), find(&mut parent, j));
                if a != b {
                    parent[a] = b;
                }
            }
        }
    }
    let mut groups: HashMap<usize, Vec<usize>> = HashMap::new();
    for i in 0..n {
        let root = find(&mut parent, i);
        groups.entry(root).or_default().push(i);
    }
    let mut out: Vec<Vec<usize>> = groups.into_values().filter(|g| g.len() >= 2).collect();
    out.sort();
    out
}

/// 相关性标注：查询路径 →（段 id → 是否相关），外加逐查询的标注理由。
#[derive(Debug, Default)]
pub struct RelatedSet {
    pub labels: HashMap<String, HashMap<String, bool>>,
    pub notes: HashMap<String, String>,
}

#[derive(Deserialize)]
struct RelatedPart {
    related: HashMap<String, HashMap<String, bool>>,
    #[serde(default)]
    notes: HashMap<String, String>,
}

/// 合并若干标注分片（分片间同键同值才允许重复，冲突即报错）。
pub fn merge_related(raws: &[&str]) -> Result<RelatedSet, Box<dyn Error>> {
    let mut set = RelatedSet::default();
    for raw in raws {
        let part: RelatedPart = serde_json::from_str(raw)?;
        for (q, labels) in part.related {
            match set.labels.get_mut(&q) {
                None => {
                    set.labels.insert(q, labels);
                }
                Some(existing) => {
                    for (id, v) in labels {
                        match existing.get(&id) {
                            Some(prev) if *prev == v => {}
                            Some(_) => return Err(format!("标注冲突: {q} {id}").into()),
                            None => {
                                existing.insert(id, v);
                            }
                        }
                    }
                }
            }
        }
        for (q, note) in part.notes {
            set.notes.entry(q).or_insert(note);
        }
    }
    Ok(set)
}

/// 载入标注：路径是目录则读其中全部 `*.json` 并合并，是文件则读单个。
pub fn load_related(path: &Path) -> Result<RelatedSet, Box<dyn Error>> {
    if path.is_dir() {
        let mut parts: Vec<PathBuf> = fs::read_dir(path)?
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| p.extension().is_some_and(|x| x == "json"))
            .collect();
        parts.sort();
        let raws: Vec<String> = parts
            .iter()
            .map(|p| fs::read_to_string(p).map_err(|e| format!("{}: {e}", p.display())))
            .collect::<Result<_, _>>()?;
        let refs: Vec<&str> = raws.iter().map(String::as_str).collect();
        return merge_related(&refs);
    }
    merge_related(&[&fs::read_to_string(path)?])
}

/// 一条命中。
#[derive(Serialize)]
pub struct SegmentHit {
    pub id: String,
    pub line: String,
    pub date: String,
    pub score: f64,
    /// 只对余弦分（向量臂）有定义，词法诊断臂为 `null`。
    pub verdict: Option<Verdict>,
}

/// 单条查询的结局。
#[derive(Serialize)]
pub struct QueryOutcome {
    pub file: String,
    pub group: String,
    /// 实际使用的查询文本（截到 80 字展示）。
    pub query: String,
    /// 是否有相关性标注（无标注不进分母）。
    pub labeled: bool,
    /// 首个相关段的名次（1-based），无标注或未命中为 `null`。
    pub rank: Option<usize>,
    pub hit_at_3: bool,
    pub top1_score: f64,
    /// 标注理由，只作展示。
    pub note: Option<String>,
    /// 命中中判为相关的段 id（有序）。
    pub related_ids: Vec<String>,
    pub hits: Vec<SegmentHit>,
    /// 提醒集合内的情绪线（成员 id），仅向量臂产出。
    pub lines: Vec<Vec<String>>,
}

#[derive(Serialize)]
pub struct RuleParams {
    pub tau1: f64,
    pub tau2: f64,
    pub line_tau: f64,
    pub retrieve_k: usize,
    pub top3: usize,
}

#[derive(Serialize)]
pub struct GroupSummary {
    pub group: String,
    pub queries: usize,
    pub labeled: usize,
    /// 该组 top-3 相关率。
    pub p_at_3: Option<f64>,
    /// 该组至少一条相关进 top-3 的比例。
    pub hit_at_3_rate: Option<f64>,
}

#[derive(Serialize)]
pub struct Report {
    pub scorer: String,
    pub query_mode: String,
    pub segments: usize,
    pub queries: usize,
    pub labeled: usize,
    /// 主指标：相关段总数 / (3 × 有标注查询数)。
    pub p_at_3: Option<f64>,
    /// 至少一条相关段进 top-3 的查询占比。
    pub hit_at_3_rate: Option<f64>,
    pub groups: Vec<GroupSummary>,
    /// 随机基线：TOP3 / 段数。
    pub random_baseline: f64,
    /// 管线自检：每段首句查自身，top-3 命中率（不参与业务判定）。
    pub self_retrieval_top3: f64,
    /// 每条查询 top-1 分数（τ₁ 标定依据）。
    pub top1_scores: Vec<f64>,
    /// top-8 中得分 > τ₂ 的（查询, 段）对数；词法臂不适用。
    pub tau2_hits: usize,
    /// top-8 中得分 < τ₁ 被丢弃的对数；词法臂不适用。
    pub tau1_drops: usize,
    /// 出口规则是否生效（τ 是余弦分阈值，仅向量臂适用）。
    pub rules_applied: bool,
    pub rule: RuleParams,
    pub outcomes: Vec<QueryOutcome>,
}

pub struct Config {
    pub assets: PathBuf,
    /// 相关性标注：文件或目录（目录下全部 `*.json` 合并）。
    pub related: PathBuf,
    pub out: PathBuf,
    /// 查询档：`full` 全文（业务主档），`first` 首句（辅助档）。
    pub query_mode: String,
    pub embed: bool,
    pub verbose: bool,
}

/// 两种打分器，与第一轮同一套实现（打分器是工具变量）。
enum Scorer {
    Lexical(Bm25),
    Vector {
        /// 每个单元的分块向量，得分取分块对最大值。
        unit_chunks: Vec<Vec<Vec<f32>>>,
        embedder: Embedder,
        cache: RefCell<HashMap<String, Vec<f32>>>,
    },
}

impl Scorer {
    fn is_vector(&self) -> bool {
        matches!(self, Scorer::Vector { .. })
    }

    fn vecs(&self, text: &str) -> Vec<Vec<f32>> {
        match self {
            Scorer::Lexical(_) => Vec::new(),
            Scorer::Vector {
                embedder, cache, ..
            } => embed_chunks(text)
                .into_iter()
                .map(|chunk| vector_of(&chunk, embedder, cache))
                .collect(),
        }
    }

    /// 查询与全部单元的相似度，按分数降序取前 k。
    fn topk(&self, query: &str, k: usize) -> Vec<(usize, f64)> {
        match self {
            Scorer::Lexical(idx) => idx.search(query, k),
            Scorer::Vector { unit_chunks, .. } => {
                let qs = self.vecs(query);
                let mut scored: Vec<(usize, f64)> = unit_chunks
                    .iter()
                    .enumerate()
                    .map(|(i, us)| {
                        let best = qs
                            .iter()
                            .flat_map(|q| us.iter().map(move |u| cosine(q, u)))
                            .fold(0.0f64, f64::max);
                        (i, best)
                    })
                    .filter(|(_, s)| *s > 0.0)
                    .collect();
                scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap().then(a.0.cmp(&b.0)));
                scored.truncate(k);
                scored
            }
        }
    }

    /// 段-段相似度（聚线专用）：向量臂取分块最大值，词法臂无定义。
    fn segment_pair(&self, a: usize, b: usize) -> Option<f64> {
        match self {
            Scorer::Vector { unit_chunks, .. } => {
                let best = unit_chunks[a]
                    .iter()
                    .flat_map(|x| unit_chunks[b].iter().map(move |y| cosine(x, y)))
                    .fold(0.0f64, f64::max);
                Some(best)
            }
            Scorer::Lexical(_) => None,
        }
    }
}

fn vector_of(
    chunk: &str,
    embedder: &Embedder,
    cache: &RefCell<HashMap<String, Vec<f32>>>,
) -> Vec<f32> {
    if let Some(v) = cache.borrow().get(chunk) {
        return v.clone();
    }
    let v = embedder
        .embed_all(&[chunk.to_string()])
        .expect("查询嵌入失败")
        .into_iter()
        .next()
        .expect("查询嵌入为空");
    cache.borrow_mut().insert(chunk.to_string(), v.clone());
    v
}

/// 一次性嵌入并按文本去重，返回 文本 → 向量。
fn embed_texts(embedder: &Embedder, texts: &[String]) -> Result<HashMap<String, Vec<f32>>, String> {
    let vecs = embedder.embed_all(texts)?;
    let mut map = HashMap::with_capacity(texts.len());
    for (t, v) in texts.iter().zip(vecs) {
        map.insert(t.clone(), v);
    }
    Ok(map)
}

fn top1_range(scores: &[f64]) -> (f64, f64) {
    let mut min = f64::INFINITY;
    let mut max = 0.0f64;
    for v in scores {
        min = min.min(*v);
        max = max.max(*v);
    }
    if scores.is_empty() {
        (0.0, 0.0)
    } else {
        (min, max)
    }
}

/// 汇总打印。
fn print_report(r: &Report) {
    println!(
        "\n打分器 {}，查询档 {}，段 {} / 查询 {}（有标注 {}）",
        r.scorer, r.query_mode, r.segments, r.queries, r.labeled
    );
    let f = |v: Option<f64>| v.map(|x| format!("{x:.2}")).unwrap_or_else(|| "n/a".into());
    println!(
        "主指标 top-3 相关率 {}（命中率 {}，随机基线 {:.2}，自检 {:.2}）",
        f(r.p_at_3),
        f(r.hit_at_3_rate),
        r.random_baseline,
        r.self_retrieval_top3
    );
    for g in &r.groups {
        println!(
            "  {:<6} 查询 {:>2}（标注 {:>2}） 相关率 {} 命中率 {}",
            g.group,
            g.queries,
            g.labeled,
            f(g.p_at_3),
            f(g.hit_at_3_rate)
        );
    }
    if r.rules_applied {
        let (min, max) = top1_range(&r.top1_scores);
        println!(
            "出口规则：τ₁ 丢弃 {} 对 / τ₂ 已写过 {} 对；top-1 分 {min:.3}–{max:.3}",
            r.tau1_drops, r.tau2_hits
        );
    } else {
        println!("出口规则不适用（τ₁/τ₂ 是余弦分阈值，词法分无上界）");
    }
    for o in &r.outcomes {
        let rank = o.rank.map(|x| x.to_string()).unwrap_or_else(|| "—".into());
        let flag = if o.labeled { "" } else { "（无标注）" };
        println!("\n## [{}] {} {} {flag}", o.group, o.file, o.query);
        println!("   首个相关段名次 {rank}，top-1 分 {:.3}", o.top1_score);
        for (i, h) in o.hits.iter().enumerate() {
            let mark = if o.hit_at_3 && o.rank == Some(i + 1) {
                "*"
            } else {
                " "
            };
            let label = h
                .verdict
                .map(|v| format!("{v:?}"))
                .unwrap_or_else(|| "—".into());
            let rel = if o.related_ids.contains(&h.id) {
                " [相关]"
            } else {
                ""
            };
            println!(
                "  {}. {mark} {} | 行 {} | {} | {:.3} | {}{rel}",
                i + 1,
                h.id,
                h.line,
                h.date,
                h.score,
                label
            );
        }
        for g in &o.lines {
            println!("   情绪线（{} 段）: {}", g.len(), g.join(" / "));
        }
    }
}

/// 跑联想评测：建索引 → 检索 → 出口规则 → 指标，返回报告并写 JSON。
pub fn run(cfg: &Config) -> Result<(), Box<dyn Error>> {
    let segments = load_segments(&cfg.assets)?;
    let queries = load_queries(&cfg.assets)?;
    if segments.is_empty() {
        return Err("日志段为空".into());
    }
    if queries.is_empty() {
        return Err("查询集为空".into());
    }
    let related = load_related(&cfg.related)?;

    let scorer_name = if cfg.embed { "embed" } else { "bm25" };
    let scorer = if cfg.embed {
        let embedder = Embedder::from_env().map_err(|m| m.to_string())?;
        let mut texts: Vec<String> = Vec::new();
        let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
        let mut push = |t: String| {
            if seen.insert(t.clone()) {
                texts.push(t);
            }
        };
        for s in &segments {
            for c in embed_chunks(&s.text) {
                push(c);
            }
            for c in embed_chunks(&first_sentence(&s.text)) {
                push(c);
            }
        }
        for q in &queries {
            let source = match cfg.query_mode.as_str() {
                "first" => first_sentence(&q.text),
                _ => q.text.clone(),
            };
            for c in embed_chunks(&source) {
                push(c);
            }
        }
        drop(seen);
        eprintln!(
            "嵌入 {} 条唯一文本（{}，段 {} / 查询 {}）…",
            texts.len(),
            embedder.model(),
            segments.len(),
            queries.len()
        );
        let map = embed_texts(&embedder, &texts)?;
        let unit_chunks: Vec<Vec<Vec<f32>>> = segments
            .iter()
            .map(|s| {
                embed_chunks(&s.text)
                    .into_iter()
                    .map(|c| map.get(&c).expect("段向量缺失").clone())
                    .collect()
            })
            .collect();
        Scorer::Vector {
            unit_chunks,
            embedder,
            cache: RefCell::new(map),
        }
    } else {
        Scorer::Lexical(Bm25::new(
            &segments.iter().map(|s| s.text.clone()).collect::<Vec<_>>(),
        ))
    };

    let mut outcomes = Vec::new();
    let mut top1_scores = Vec::new();
    let mut tau2_hits = 0usize;
    let mut tau1_drops = 0usize;
    let mut labeled = 0usize;
    let mut hits_in_top3 = 0usize;
    let mut hit_queries = 0usize;
    let mut group_stat: HashMap<String, (usize, usize, usize, usize)> = HashMap::new();

    for q in &queries {
        let source = match cfg.query_mode.as_str() {
            "first" => first_sentence(&q.text),
            _ => q.text.clone(),
        };
        let found: Vec<(usize, f64)> = scorer.topk(&source, RETRIEVE_K);
        let labels = related.labels.get(&q.path);
        let is_labeled = labels.is_some();
        if is_labeled {
            labeled += 1;
        }

        let mut rank = None;
        let mut hit_at_3 = false;
        let mut related_in_top3 = 0usize;
        let mut related_ids: Vec<String> = Vec::new();
        for (i, (idx, _)) in found.iter().enumerate() {
            let id = &segments[*idx].id;
            let is_rel = labels.is_some_and(|m| m.get(id).copied().unwrap_or(false));
            if is_rel {
                related_ids.push(id.clone());
                if rank.is_none() {
                    rank = Some(i + 1);
                }
                if i + 1 <= TOP3 {
                    hit_at_3 = true;
                    related_in_top3 += 1;
                }
            }
        }
        if is_labeled {
            hits_in_top3 += related_in_top3;
            if hit_at_3 {
                hit_queries += 1;
            }
        }

        let hits: Vec<SegmentHit> = found
            .iter()
            .map(|(i, s)| SegmentHit {
                id: segments[*i].id.clone(),
                line: format!("{}-{}", segments[*i].line_start, segments[*i].line_end),
                date: segments[*i].date.clone(),
                score: *s,
                verdict: if cfg.embed { Some(verdict(*s)) } else { None },
            })
            .collect();
        for h in &hits {
            match h.verdict {
                Some(Verdict::AlreadyWritten) => tau2_hits += 1,
                Some(Verdict::BelowFloor) => tau1_drops += 1,
                Some(Verdict::Remind) | None => {}
            }
        }

        let top1 = hits.first().map(|h| h.score).unwrap_or(0.0);
        top1_scores.push(top1);

        // 聚线：只在通过 τ₁ 的提醒集合内做，且只在向量臂（分块相似度才有定义）
        let remind_pos: Vec<usize> = found
            .iter()
            .enumerate()
            .filter(|(_, (_, s))| cfg.embed && verdict(*s) == Verdict::Remind)
            .map(|(pos, _)| pos)
            .collect();
        let mut lines: Vec<Vec<String>> = Vec::new();
        if scorer.is_vector() && remind_pos.len() >= 2 {
            let group = cluster_lines(remind_pos.len(), |a, b| {
                let (ia, ib) = (remind_pos[a], remind_pos[b]);
                scorer
                    .segment_pair(found[ia].0, found[ib].0)
                    .map(|c| c > LINE_TAU)
                    .unwrap_or(false)
            });
            lines = group
                .into_iter()
                .map(|g| g.iter().map(|&p| hits[remind_pos[p]].id.clone()).collect())
                .collect();
        }

        let entry = group_stat.entry(q.group.clone()).or_insert((0, 0, 0, 0));
        entry.0 += 1;
        if is_labeled {
            entry.1 += 1;
            entry.2 += related_in_top3;
            entry.3 += usize::from(hit_at_3);
        }

        outcomes.push(QueryOutcome {
            file: q.path.clone(),
            group: q.group.clone(),
            query: preview(&source),
            labeled: is_labeled,
            rank,
            hit_at_3,
            top1_score: top1,
            note: related.notes.get(&q.path).cloned(),
            related_ids,
            hits,
            lines,
        });
    }

    // 管线自检：每段首句查自身，应落在 top-3
    let mut self_ok = 0usize;
    for (i, s) in segments.iter().enumerate() {
        let found = scorer.topk(&first_sentence(&s.text), TOP3);
        if found.iter().any(|(idx, _)| *idx == i) {
            self_ok += 1;
        }
    }

    let mut groups: Vec<GroupSummary> = group_stat
        .into_iter()
        .map(|(group, (n, lb, rel, hit))| GroupSummary {
            group,
            queries: n,
            labeled: lb,
            p_at_3: if lb > 0 {
                Some(rel as f64 / (TOP3 * lb) as f64)
            } else {
                None
            },
            hit_at_3_rate: if lb > 0 {
                Some(hit as f64 / lb as f64)
            } else {
                None
            },
        })
        .collect();
    groups.sort_by(|a, b| a.group.cmp(&b.group));

    let report = Report {
        scorer: scorer_name.to_string(),
        query_mode: cfg.query_mode.clone(),
        segments: segments.len(),
        queries: queries.len(),
        labeled,
        p_at_3: if labeled > 0 {
            Some(hits_in_top3 as f64 / (TOP3 * labeled) as f64)
        } else {
            None
        },
        hit_at_3_rate: if labeled > 0 {
            Some(hit_queries as f64 / labeled as f64)
        } else {
            None
        },
        groups,
        random_baseline: TOP3 as f64 / segments.len() as f64,
        self_retrieval_top3: self_ok as f64 / segments.len() as f64,
        top1_scores,
        tau2_hits,
        tau1_drops,
        rules_applied: cfg.embed,
        rule: RuleParams {
            tau1: TAU1,
            tau2: TAU2,
            line_tau: LINE_TAU,
            retrieve_k: RETRIEVE_K,
            top3: TOP3,
        },
        outcomes,
    };

    print_report(&report);
    if let Some(parent) = cfg.out.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(&cfg.out, serde_json::to_string_pretty(&report)?)?;
    println!("\n结果已写入 {}", cfg.out.display());
    Ok(())
}

/// 展示用截断：80 字加省略号。
fn preview(text: &str) -> String {
    let t: String = text.chars().take(80).collect();
    if text.chars().count() > 80 {
        format!("{t}…")
    } else {
        t
    }
}

/// 段清单 JSON（供金标标注与复现用）：每段含 id、行区间、日期与正文。
pub fn dump_segments(segments: &[Segment]) -> Result<String, Box<dyn Error>> {
    Ok(serde_json::to_string_pretty(segments)?)
}
