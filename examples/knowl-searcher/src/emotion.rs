//! 第二轮：情绪日记提醒——默认日志段索引、出口规则（τ₁/τ₂/聚线）与倒查评测。
//!
//! - 索引：`assets/memory/default` 的日志按 `---` 切会话段（复用 [`crate::corpus::build_units`]，
//!   超 900 字按段续分），一段 = 一个检索单元，坐标 `path#index` + `line_start-line_end` + `date`
//! - 查询：情绪日记（评测用首句，业务侧用草稿全文，见 `--query`）
//! - 裁决：τ₁ 共振下限、τ₂ 重复上限、聚线（段间 cosine 单链接）——规则只在出口生效
//! - 评判：源段 top-3 命中率，金标 `data/emotion-gold.json` 预标注，不回调参

use std::cell::RefCell;
use std::collections::HashMap;
use std::error::Error;
use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::bm25::Bm25;
use crate::corpus::{Doc, Kind, build_units, load_corpus};
use crate::embed::{Embedder, cosine, embed_text};

/// 预注册参数：跑完标定，不回调参。
pub const TAU1: f64 = 0.55;
pub const TAU2: f64 = 0.92;
/// 段间情绪线阈值（段-段分布与查询-段分布不同，分开标定）。
pub const LINE_TAU: f64 = 0.7;
/// 检索条数；评判截断到 [`TOP3`]。
pub const RETRIEVE_K: usize = 8;
pub const TOP3: usize = 3;

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

/// 默认集日志段（语料边界 = 工具箱模型的 `journal` 层，仅 `memory/default/`）。
pub fn load_segments(assets: &Path) -> Result<Vec<Segment>, Box<dyn Error>> {
    let (docs, _) = load_corpus(assets)?;
    let docs: Vec<Doc> = docs
        .into_iter()
        .filter(|d| d.kind == Kind::Journal && d.path.starts_with("memory/default/"))
        .collect();
    Ok(segments_from_docs(&docs))
}

/// 情绪日记（查询侧语料）。
pub fn load_diaries(assets: &Path) -> Result<Vec<Doc>, Box<dyn Error>> {
    let (docs, _) = load_corpus(assets)?;
    Ok(docs
        .into_iter()
        .filter(|d| d.kind == Kind::Emotion)
        .collect())
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
/// 返回成员下标（升序），组间按成员向量排序，确定性。
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

/// 金标文件：`gold` 的键是日记相对 `assets/` 的路径，值是日志段 id 列表；空数组表示无金标。
/// `notes` 存逐篇标注理由，只用于报告可读性，不参与判定。
#[derive(Deserialize)]
pub struct GoldFile {
    pub rule: String,
    #[serde(default)]
    pub annotator: Option<String>,
    pub gold: HashMap<String, Vec<String>>,
    #[serde(default)]
    pub notes: HashMap<String, String>,
}

pub fn parse_gold(raw: &str) -> Result<GoldFile, Box<dyn Error>> {
    Ok(serde_json::from_str(raw)?)
}

/// 事后相关性标注（诊断口径，非预注册）：`related` 的键是日记路径，值是 段 id → 是否相关。
#[derive(Deserialize)]
pub struct RelatedFile {
    pub rule: String,
    #[serde(default)]
    pub annotator: Option<String>,
    pub related: HashMap<String, HashMap<String, bool>>,
    #[serde(default)]
    pub notes: HashMap<String, String>,
}

pub fn parse_related(raw: &str) -> Result<RelatedFile, Box<dyn Error>> {
    Ok(serde_json::from_str(raw)?)
}

/// 一条命中。`verdict` 只对余弦分（向量臂）有定义，词法诊断臂为 `null`。
#[derive(Serialize)]
pub struct SegmentHit {
    pub id: String,
    pub line: String,
    pub date: String,
    pub score: f64,
    pub verdict: Option<Verdict>,
}

/// 单篇日记的结局。
#[derive(Serialize)]
pub struct DiaryOutcome {
    pub file: String,
    pub query: String,
    /// 是否有金标（无金标不进分母）。
    pub covered: bool,
    /// 金标最好名次（1-based），无金标或未命中为 `null`。
    pub rank: Option<usize>,
    pub hit_at_3: bool,
    pub top1_score: f64,
    /// 金标标注理由（可空），只作可读性展示。
    pub note: Option<String>,
    pub hits: Vec<SegmentHit>,
    /// 提醒集合内的情绪线（成员 id），仅向量臂产出。
    pub lines: Vec<Vec<String>>,
    /// top-3 中判为相关的段数（诊断口径）；无标注时为 `null`。
    pub related_top3: Option<usize>,
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
pub struct Report {
    pub scorer: String,
    pub query_mode: String,
    pub segments: usize,
    pub diaries: usize,
    pub covered: usize,
    /// 金标覆盖子集上的 top-3 命中率；覆盖为 0 时为 `null`。
    pub top3_hit_rate: Option<f64>,
    /// 随机基线：TOP3 / 段数。
    pub random_baseline: f64,
    /// 管线自检：每段首句查自身，top-3 命中率（不参与业务判定）。
    pub self_retrieval_top3: f64,
    /// 每篇日记 top-1 分数（τ₁ 标定依据）。
    pub top1_scores: Vec<f64>,
    /// top-8 中得分 > τ₂ 的（日记, 段）对数；词法臂不适用。
    pub tau2_hits: usize,
    /// top-8 中得分 < τ₁ 被丢弃的对数；词法臂不适用。
    pub tau1_drops: usize,
    /// 出口规则是否生效（τ 是余弦分阈值，仅向量臂适用）。
    pub rules_applied: bool,
    /// 事后诊断：top-3 相关率（相关段数 / (3 × 有标注日记数)），非预注册指标。
    pub related_p_at_3: Option<f64>,
    /// 诊断口径下 top-3 命中的相关段总数。
    pub related_hits: usize,
    pub rule: RuleParams,
    pub outcomes: Vec<DiaryOutcome>,
}

pub struct Config {
    pub assets: PathBuf,
    pub gold: PathBuf,
    /// 事后相关性标注，可不存在（缺省路径下无文件时跳过诊断指标）。
    pub related: PathBuf,
    pub out: PathBuf,
    /// 查询档：`first` 首句（预注册主档），`full` 草稿全文（辅助档）。
    pub query_mode: String,
    pub embed: bool,
    pub verbose: bool,
}

/// 两种打分器，与第一轮同一套实现（打分器是工具变量）。
enum Scorer {
    Lexical(Bm25),
    Vector {
        vecs: Vec<Vec<f32>>,
        embedder: Embedder,
        cache: RefCell<HashMap<String, Vec<f32>>>,
    },
}

impl Scorer {
    fn is_vector(&self) -> bool {
        matches!(self, Scorer::Vector { .. })
    }

    fn topk(&self, query: &str, k: usize) -> Vec<(usize, f64)> {
        match self {
            Scorer::Lexical(idx) => idx.search(query, k),
            Scorer::Vector {
                vecs,
                embedder,
                cache,
            } => {
                let qv = query_vector(query, embedder, cache);
                let mut scored: Vec<(usize, f64)> = vecs
                    .iter()
                    .enumerate()
                    .map(|(i, v)| (i, cosine(&qv, v)))
                    .filter(|(_, s)| *s > 0.0)
                    .collect();
                scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap().then(a.0.cmp(&b.0)));
                scored.truncate(k);
                scored
            }
        }
    }

    /// 段-段相似度（聚线专用）：向量臂取 cosine，词法臂无定义。
    fn segment_pair(&self, a: usize, b: usize) -> Option<f64> {
        match self {
            Scorer::Vector { vecs, .. } => Some(cosine(&vecs[a], &vecs[b])),
            Scorer::Lexical(_) => None,
        }
    }
}

fn query_vector(
    query: &str,
    embedder: &Embedder,
    cache: &RefCell<HashMap<String, Vec<f32>>>,
) -> Vec<f32> {
    let key = embed_text(query);
    if let Some(v) = cache.borrow().get(&key) {
        return v.clone();
    }
    let v = embedder
        .embed_all(&[key.clone()])
        .expect("查询嵌入失败")
        .into_iter()
        .next()
        .expect("查询嵌入为空");
    cache.borrow_mut().insert(key, v.clone());
    v
}

/// 汇总打印。
fn print_report(r: &Report) {
    println!(
        "\n打分器 {}，查询档 {}，段 {} / 日记 {}（有金标 {}）",
        r.scorer, r.query_mode, r.segments, r.diaries, r.covered
    );
    let rate = r
        .top3_hit_rate
        .map(|v| format!("{v:.2}"))
        .unwrap_or_else(|| "n/a".into());
    println!(
        "源段 top-3 命中率 {rate}（随机基线 {:.2}，自检 {:.2}）",
        r.random_baseline, r.self_retrieval_top3
    );
    if let Some(p) = r.related_p_at_3 {
        println!(
            "事后诊断（非预注册）：top-3 相关率 {p:.2}，命中相关段 {} 个",
            r.related_hits
        );
    }
    let (min, max) = top1_range(r);
    if r.rules_applied {
        println!(
            "出口规则：τ₁ 丢弃 {} 对 / τ₂ 已写过 {} 对；top-1 分数 {min:.3}–{max:.3}",
            r.tau1_drops, r.tau2_hits
        );
    } else {
        println!("出口规则不适用（τ₁/τ₂ 是余弦分阈值，词法分无上界）");
    }
    for o in &r.outcomes {
        let rank = o.rank.map(|x| x.to_string()).unwrap_or_else(|| "—".into());
        let cov = if o.covered { "" } else { "（无金标）" };
        println!("\n## {} 查询「{}」{cov}", o.file, o.query);
        println!("   金标最好名次 {rank}，top-1 分 {:.3}", o.top1_score);
        if let Some(n) = &o.note {
            println!("   标注: {n}");
        }
        for (i, h) in o.hits.iter().enumerate() {
            let mark = if o.covered && o.rank == Some(i + 1) {
                "*"
            } else {
                " "
            };
            println!(
                "  {}. {mark} {} | 行 {} | {} | {:.3} | {}",
                i + 1,
                h.id,
                h.line,
                h.date,
                h.score,
                h.verdict
                    .map(|v| format!("{v:?}"))
                    .unwrap_or_else(|| "—".into())
            );
        }
        for g in &o.lines {
            let dates: Vec<&str> = g
                .iter()
                .filter_map(|id| o.hits.iter().find(|h| &h.id == id).map(|h| h.date.as_str()))
                .collect();
            println!(
                "   情绪线（{} 段）: {} → {:?}",
                g.len(),
                g.join(" / "),
                dates
            );
        }
    }
}

fn top1_range(r: &Report) -> (f64, f64) {
    let mut min = f64::INFINITY;
    let mut max = 0.0f64;
    for v in &r.top1_scores {
        min = min.min(*v);
        max = max.max(*v);
    }
    if r.top1_scores.is_empty() {
        (0.0, 0.0)
    } else {
        (min, max)
    }
}

/// 跑第二轮：建索引 → 检索 → 出口规则 → 评测，返回报告并写 JSON。
pub fn run(cfg: &Config) -> Result<(), Box<dyn Error>> {
    let segments = load_segments(&cfg.assets)?;
    let diaries = load_diaries(&cfg.assets)?;
    if segments.is_empty() {
        return Err("默认日志段为空".into());
    }

    let gold = parse_gold(&fs::read_to_string(&cfg.gold)?);
    let gold = gold?;
    let related = match fs::read_to_string(&cfg.related) {
        Ok(raw) => Some(parse_related(&raw)?),
        Err(_) => None,
    };

    let scorer_name = if cfg.embed { "embed" } else { "bm25" };
    let rules_applied = cfg.embed;
    let scorer = if cfg.embed {
        let embedder = Embedder::from_env().map_err(|m| m.to_string())?;
        // 一次性嵌入并按文本去重：段、段首句、日记首句、日记全文
        let mut texts: Vec<String> = Vec::new();
        let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
        let mut push = |t: String| {
            if seen.insert(t.clone()) {
                texts.push(t);
            }
        };
        for s in &segments {
            push(embed_text(&s.text));
            push(embed_text(&first_sentence(&s.text)));
        }
        for d in &diaries {
            push(embed_text(&first_sentence(&d.text)));
            push(embed_text(&d.text));
        }
        drop(seen);
        eprintln!(
            "嵌入 {} 条唯一文本（{}，段 {}）…",
            texts.len(),
            embedder.model(),
            segments.len()
        );
        let vecs = embedder.embed_all(&texts)?;
        let mut map: HashMap<String, Vec<f32>> = HashMap::new();
        for (t, v) in texts.into_iter().zip(vecs) {
            map.insert(t, v);
        }
        let seg_vecs: Vec<Vec<f32>> = segments
            .iter()
            .map(|s| map.get(&embed_text(&s.text)).expect("段向量缺失").clone())
            .collect();
        Scorer::Vector {
            vecs: seg_vecs,
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
    let mut covered = 0usize;
    let mut hits_in_covered = 0usize;
    let mut related_hits = 0usize;
    let mut related_diaries = 0usize;

    for d in &diaries {
        let query = match cfg.query_mode.as_str() {
            "full" => d.text.clone(),
            _ => first_sentence(&d.text),
        };
        let found: Vec<(usize, f64)> = scorer.topk(&query, RETRIEVE_K);
        let gold_ids: &[String] = gold.gold.get(&d.path).map(Vec::as_slice).unwrap_or(&[]);
        let is_covered = !gold_ids.is_empty();
        if is_covered {
            covered += 1;
        }

        let mut rank = None;
        let mut hit_at_3 = false;
        for (i, (idx, _)) in found.iter().enumerate() {
            let id = &segments[*idx].id;
            if gold_ids.iter().any(|g| g == id) {
                if rank.is_none() {
                    rank = Some(i + 1);
                }
                if i + 1 <= TOP3 {
                    hit_at_3 = true;
                }
            }
        }
        if is_covered && hit_at_3 {
            hits_in_covered += 1;
        }

        let hits: Vec<SegmentHit> = found
            .iter()
            .map(|(i, s)| SegmentHit {
                id: segments[*i].id.clone(),
                line: format!("{}-{}", segments[*i].line_start, segments[*i].line_end),
                date: segments[*i].date.clone(),
                score: *s,
                verdict: if rules_applied {
                    Some(verdict(*s))
                } else {
                    None
                },
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

        let related_top3 = related.as_ref().and_then(|r| {
            let labels = r.related.get(&d.path)?;
            related_diaries += 1;
            let n = hits
                .iter()
                .take(TOP3)
                .filter(|h| labels.get(&h.id).copied().unwrap_or(false))
                .count();
            related_hits += n;
            Some(n)
        });

        // 聚线：只在通过 τ₁ 的提醒集合内做，且只在向量臂（段间 cosine 才有定义）
        let remind_pos: Vec<usize> = found
            .iter()
            .enumerate()
            .filter(|(_, (_, s))| rules_applied && verdict(*s) == Verdict::Remind)
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

        outcomes.push(DiaryOutcome {
            file: d.path.clone(),
            query,
            covered: is_covered,
            rank,
            hit_at_3,
            top1_score: top1,
            note: gold.notes.get(&d.path).cloned(),
            hits,
            lines,
            related_top3,
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

    let report = Report {
        scorer: scorer_name.to_string(),
        query_mode: cfg.query_mode.clone(),
        segments: segments.len(),
        diaries: diaries.len(),
        covered,
        top3_hit_rate: if covered > 0 {
            Some(hits_in_covered as f64 / covered as f64)
        } else {
            None
        },
        random_baseline: TOP3 as f64 / segments.len() as f64,
        self_retrieval_top3: self_ok as f64 / segments.len() as f64,
        top1_scores,
        tau2_hits,
        tau1_drops,
        rules_applied,
        related_p_at_3: if related_diaries > 0 {
            Some(related_hits as f64 / (TOP3 * related_diaries) as f64)
        } else {
            None
        },
        related_hits,
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

/// 段清单 JSON（供金标标注与复现用）：每段含 id、行区间、日期与正文。
pub fn dump_segments(segments: &[Segment]) -> Result<String, Box<dyn Error>> {
    Ok(serde_json::to_string_pretty(segments)?)
}
