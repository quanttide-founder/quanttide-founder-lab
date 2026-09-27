//! 实验主流程：装载语料 → 建两套索引 → 跑三臂 × 查询集 → 指标汇总与 JSON 落盘。

use std::cell::RefCell;
use std::collections::HashMap;
use std::error::Error;
use std::fs;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};

use crate::bm25::Bm25;
use crate::chunk::{Chunk, chunk_docs};
use crate::corpus::{Unit, build_units, load_corpus};
use crate::embed::{Embedder, cosine, embed_text};
use crate::metrics::{hit, is_gold, jaccard, recall_at_k, reciprocal_rank};

/// 融合前的候选数。
const CANDIDATES: usize = 30;
/// 切块参数：窗口 / 步长。
const CHUNK_WINDOW: usize = 400;
const CHUNK_STEP: usize = 320;

#[derive(Deserialize)]
struct QueryFile {
    queries: Vec<Query>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Query {
    pub id: String,
    #[serde(default)]
    pub category: String,
    pub text: String,
    #[serde(default)]
    pub rewrites: Vec<String>,
    pub relevant: Vec<String>,
}

pub struct Config {
    pub assets: PathBuf,
    pub queries: PathBuf,
    pub out: PathBuf,
    pub topk: usize,
    pub embed: bool,
    pub verbose: bool,
}

#[derive(Serialize)]
pub struct Hit {
    pub path: String,
    pub label: String,
    pub score: f64,
    pub coord: String,
}

#[derive(Serialize)]
struct Outcome {
    id: String,
    arm: String,
    hits: Vec<Hit>,
    gold: Vec<String>,
    hit: bool,
    recall: f64,
    rr: f64,
    stability: Option<f64>,
}

#[derive(Serialize)]
struct Summary {
    arm: String,
    hit_at_k: f64,
    recall_at_k: f64,
    mrr: f64,
    model_coord: f64,
    stability: Option<f64>,
}

#[derive(Serialize)]
struct Report {
    scorer: String,
    topk: usize,
    query_count: usize,
    corpus: CorpusStat,
    summaries: Vec<Summary>,
    outcomes: Vec<Outcome>,
}

#[derive(Serialize)]
struct CorpusStat {
    docs: usize,
    units: usize,
    chunks: usize,
    kinds: Vec<(String, usize)>,
}

/// 两种打分器，同一套臂共用。
enum Scorer {
    Lexical {
        units: Bm25,
        chunks: Bm25,
    },
    Vector {
        unit_vec: Vec<Vec<f32>>,
        chunk_vec: Vec<Vec<f32>>,
        embedder: Embedder,
        query_cache: RefCell<HashMap<String, Vec<f32>>>,
    },
}

impl Scorer {
    fn unit_candidates(&self, query: &str, _units: &[Unit]) -> Vec<(usize, f64)> {
        match self {
            Scorer::Lexical { units: idx, .. } => idx.search(query, CANDIDATES),
            Scorer::Vector {
                unit_vec,
                embedder,
                query_cache,
                ..
            } => {
                let qv = query_vector(query, embedder, query_cache);
                let mut scored: Vec<(usize, f64)> = unit_vec
                    .iter()
                    .enumerate()
                    .map(|(i, v)| (i, cosine(&qv, v)))
                    .filter(|(_, s)| *s > 0.0)
                    .collect();
                scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap().then(a.0.cmp(&b.0)));
                scored.truncate(CANDIDATES);
                scored
            }
        }
    }

    fn chunk_candidates(&self, query: &str, _chunks: &[Chunk]) -> Vec<(usize, f64)> {
        match self {
            Scorer::Lexical { chunks: idx, .. } => idx.search(query, CANDIDATES),
            Scorer::Vector {
                chunk_vec,
                embedder,
                query_cache,
                ..
            } => {
                let qv = query_vector(query, embedder, query_cache);
                let mut scored: Vec<(usize, f64)> = chunk_vec
                    .iter()
                    .enumerate()
                    .map(|(i, v)| (i, cosine(&qv, v)))
                    .filter(|(_, s)| *s > 0.0)
                    .collect();
                scored.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap().then(a.0.cmp(&b.0)));
                scored.truncate(CANDIDATES);
                scored
            }
        }
    }
}

fn query_vector(
    query: &str,
    embedder: &Embedder,
    cache: &RefCell<HashMap<String, Vec<f32>>>,
) -> Vec<f32> {
    if let Some(v) = cache.borrow().get(query) {
        return v.clone();
    }
    let text = embed_text(query);
    let v = embedder
        .embed_all(&[text])
        .expect("查询嵌入失败")
        .into_iter()
        .next()
        .expect("查询嵌入为空");
    cache.borrow_mut().insert(query.to_string(), v.clone());
    v
}

/// 单臂检索：rag=块索引，idx=单元索引。
fn retrieve(
    arm: &str,
    scorer: &Scorer,
    query: &str,
    units: &[Unit],
    chunks: &[Chunk],
    topk: usize,
) -> Vec<Hit> {
    match arm {
        "rag" => scorer
            .chunk_candidates(query, chunks)
            .into_iter()
            .take(topk)
            .map(|(i, s)| {
                let c = &chunks[i];
                Hit {
                    path: c.path.clone(),
                    label: format!("chunk#{}", c.index),
                    score: s,
                    coord: String::new(),
                }
            })
            .collect(),
        "idx" => scorer
            .unit_candidates(query, units)
            .into_iter()
            .take(topk)
            .map(|(i, s)| unit_hit(&units[i], s))
            .collect(),
        _ => unreachable!("未知臂: {arm}"),
    }
}

fn unit_hit(u: &Unit, score: f64) -> Hit {
    Hit {
        path: u.path.clone(),
        label: u.title.clone(),
        score,
        coord: u.coord(),
    }
}

/// 语料统计（`stats` 子命令）。
pub fn stats(cfg: &Config) -> Result<(), Box<dyn Error>> {
    let (docs, tier_map) = load_corpus(&cfg.assets)?;
    let units = build_units(&docs, &tier_map);
    let chunks = chunk_docs(&docs, CHUNK_WINDOW, CHUNK_STEP);
    let stat = corpus_stat(&docs, &units, &chunks);
    println!(
        "语料: {} 文件 / {} 单元 / {} 块",
        stat.docs, stat.units, stat.chunks
    );
    for (kind, n) in &stat.kinds {
        println!("  {kind:<12} {n}");
    }
    Ok(())
}

fn corpus_stat(docs: &[crate::corpus::Doc], units: &[Unit], chunks: &[Chunk]) -> CorpusStat {
    let mut kinds: HashMap<&'static str, usize> = HashMap::new();
    for d in docs {
        *kinds.entry(d.kind.as_str()).or_insert(0) += 1;
    }
    let mut kind_list: Vec<(String, usize)> =
        kinds.into_iter().map(|(k, n)| (k.to_string(), n)).collect();
    kind_list.sort();
    CorpusStat {
        docs: docs.len(),
        units: units.len(),
        chunks: chunks.len(),
        kinds: kind_list,
    }
}

/// 跑实验：返回报告并写 JSON。
pub fn run(cfg: &Config) -> Result<(), Box<dyn Error>> {
    let (docs, tier_map) = load_corpus(&cfg.assets)?;
    let units = build_units(&docs, &tier_map);
    let chunks = chunk_docs(&docs, CHUNK_WINDOW, CHUNK_STEP);

    let raw = fs::read_to_string(&cfg.queries)?;
    let qf: QueryFile = serde_json::from_str(&raw)?;
    for q in &qf.queries {
        for g in &q.relevant {
            if !docs.iter().any(|d| d.path == *g) {
                return Err(format!("金标不在语料: {g}（查询 {}）", q.id).into());
            }
        }
    }

    let scorer_name = if cfg.embed { "embed" } else { "bm25" };
    let scorer = if cfg.embed {
        let embedder = Embedder::from_env().map_err(|m| m.to_string())?;
        let unit_texts: Vec<String> = units.iter().map(|u| embed_text(&u.text)).collect();
        let chunk_texts: Vec<String> = chunks.iter().map(|c| embed_text(&c.text)).collect();
        eprintln!(
            "嵌入语料（{}，{} 单元 / {} 块）…",
            embedder.model(),
            unit_texts.len(),
            chunk_texts.len()
        );
        let unit_vec = embedder.embed_all(&unit_texts)?;
        let chunk_vec = embedder.embed_all(&chunk_texts)?;
        Scorer::Vector {
            unit_vec,
            chunk_vec,
            embedder,
            query_cache: RefCell::new(HashMap::new()),
        }
    } else {
        Scorer::Lexical {
            units: Bm25::new(&units.iter().map(|u| u.text.clone()).collect::<Vec<_>>()),
            chunks: Bm25::new(&chunks.iter().map(|c| c.text.clone()).collect::<Vec<_>>()),
        }
    };

    let arms = ["rag", "idx"];
    let mut summaries = Vec::new();
    let mut outcomes = Vec::new();

    for arm in arms {
        let arm_name = format!("{arm}-{scorer_name}");
        let (mut n_hit, mut recall, mut rr, mut coord, mut stab, mut stab_n) =
            (0.0, 0.0, 0.0, 0.0, 0.0, 0.0);

        for q in &qf.queries {
            let hits = retrieve(arm, &scorer, &q.text, &units, &chunks, cfg.topk);
            let paths: Vec<String> = hits.iter().map(|h| h.path.clone()).collect();

            let h = hit(&paths, &q.relevant);
            let r = recall_at_k(&paths, &q.relevant);
            let rr_v = reciprocal_rank(&paths, &q.relevant);
            let coord_v = if hits.is_empty() {
                0.0
            } else {
                hits.iter().filter(|x| !x.coord.is_empty()).count() as f64 / hits.len() as f64
            };

            let mut stability = None;
            if !q.rewrites.is_empty() {
                let mut s = 0.0;
                for rw in &q.rewrites {
                    let rwhits = retrieve(arm, &scorer, rw, &units, &chunks, cfg.topk);
                    let rwpaths: Vec<String> = rwhits.iter().map(|x| x.path.clone()).collect();
                    s += jaccard(&paths, &rwpaths);
                }
                stability = Some(s / q.rewrites.len() as f64);
            }

            n_hit += f64::from(h as u8);
            recall += r;
            rr += rr_v;
            coord += coord_v;
            if let Some(s) = stability {
                stab += s;
                stab_n += 1.0;
            }

            if cfg.verbose {
                println!("## {arm_name} / {} {}", q.id, q.text);
                for (i, x) in hits.iter().enumerate() {
                    let mark = if q.relevant.iter().any(|g| is_gold(&x.path, g)) {
                        "*"
                    } else {
                        " "
                    };
                    println!("  {}. {mark} {} | {} | {}", i + 1, x.path, x.label, x.coord);
                }
            }

            outcomes.push(Outcome {
                id: q.id.clone(),
                arm: arm_name.clone(),
                hits,
                gold: q.relevant.clone(),
                hit: h,
                recall: r,
                rr: rr_v,
                stability,
            });
        }

        let n = qf.queries.len() as f64;
        summaries.push(Summary {
            arm: arm_name,
            hit_at_k: n_hit / n,
            recall_at_k: recall / n,
            mrr: rr / n,
            model_coord: coord / n,
            stability: if stab_n > 0.0 {
                Some(stab / stab_n)
            } else {
                None
            },
        });
    }

    let report = Report {
        scorer: scorer_name.to_string(),
        topk: cfg.topk,
        query_count: qf.queries.len(),
        corpus: corpus_stat(&docs, &units, &chunks),
        summaries,
        outcomes,
    };

    // 汇总表
    println!(
        "\n打分器 {}，top-k {}，查询 {} 条，语料 {} 文件 / {} 单元 / {} 块",
        report.scorer,
        report.topk,
        report.query_count,
        report.corpus.docs,
        report.corpus.units,
        report.corpus.chunks
    );
    println!(
        "\n| 臂 | hit@{} | recall@{} | MRR | 模型坐标 | 改写稳定性 |",
        cfg.topk, cfg.topk
    );
    println!("|---|---|---|---|---|---|");
    for s in &report.summaries {
        let stab = s.stability.map(|v| format!("{v:.2}")).unwrap_or("—".into());
        println!(
            "| {} | {:.2} | {:.2} | {:.2} | {:.2} | {} |",
            s.arm, s.hit_at_k, s.recall_at_k, s.mrr, s.model_coord, stab
        );
    }

    if let Some(parent) = cfg.out.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(&cfg.out, serde_json::to_string_pretty(&report)?)?;
    println!("\n结果已写入 {}", cfg.out.display());
    Ok(())
}
