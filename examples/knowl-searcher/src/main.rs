//! knowl-searcher CLI：
//!
//! ```text
//! cargo run -- stats                          # 第一轮：语料与索引统计
//! cargo run -- run --scorer bm25              # 第一轮：离线词法臂
//! cargo run -- run --scorer embed             # 第一轮：向量臂（需 embedding key）
//! cargo run -- segments --out data/segments.json # 第二轮：日志段清单 JSON（金标标注用）
//! cargo run -- emotion --scorer embed         # 第二轮：情绪日记提醒倒查
//! cargo run -- emotion --scorer bm25 --verbose # 第二轮：词法诊断臂
//! ```

use std::env;
use std::path::PathBuf;
use std::process::ExitCode;

use knowl_searcher::emotion;
use knowl_searcher::harness::{Config, run, stats};

fn manifest(rel: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(rel)
}

const USAGE: &str = "用法: knowl-searcher <stats|run|segments|emotion> [--scorer bm25|embed] [--query first|full] [--topk N] [--assets DIR] [--queries FILE] [--gold FILE] [--related FILE] [--out FILE] [--verbose]";

struct Opts {
    assets: PathBuf,
    queries: PathBuf,
    gold: PathBuf,
    related: PathBuf,
    out: Option<PathBuf>,
    topk: usize,
    embed: bool,
    query_mode: String,
    verbose: bool,
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().collect();
    let cmd = args.get(1).map(String::as_str).unwrap_or("");

    let mut opts = Opts {
        assets: manifest("../../../../assets"),
        queries: manifest("data/queries.json"),
        gold: manifest("data/emotion-gold.json"),
        related: manifest("data/emotion-related.json"),
        out: None,
        topk: 5,
        embed: false,
        query_mode: "first".to_string(),
        verbose: false,
    };

    let mut i = 2;
    while i < args.len() {
        let flag = args[i].as_str();
        let need = |i: &mut usize| -> Option<&String> {
            *i += 1;
            args.get(*i)
        };
        match flag {
            "--assets" => match need(&mut i) {
                Some(v) => opts.assets = PathBuf::from(v),
                None => return usage("--assets 缺值"),
            },
            "--queries" => match need(&mut i) {
                Some(v) => opts.queries = PathBuf::from(v),
                None => return usage("--queries 缺值"),
            },
            "--gold" => match need(&mut i) {
                Some(v) => opts.gold = PathBuf::from(v),
                None => return usage("--gold 缺值"),
            },
            "--related" => match need(&mut i) {
                Some(v) => opts.related = PathBuf::from(v),
                None => return usage("--related 缺值"),
            },
            "--out" => match need(&mut i) {
                Some(v) => opts.out = Some(PathBuf::from(v)),
                None => return usage("--out 缺值"),
            },
            "--topk" => match need(&mut i).and_then(|v| v.parse::<usize>().ok()) {
                Some(v) if v > 0 => opts.topk = v,
                _ => return usage("--topk 需要正整数"),
            },
            "--scorer" => match need(&mut i).map(String::as_str) {
                Some("bm25") => opts.embed = false,
                Some("embed") => opts.embed = true,
                Some(other) => return usage(&format!("未知打分器 {other}，可选 bm25 | embed")),
                None => return usage("--scorer 缺值"),
            },
            "--query" => {
                let v = match need(&mut i) {
                    Some(v) => v.clone(),
                    None => return usage("--query 缺值"),
                };
                match v.as_str() {
                    "first" | "full" => opts.query_mode = v,
                    other => return usage(&format!("未知查询档 {other}，可选 first | full")),
                }
            }
            "--verbose" => opts.verbose = true,
            other => return usage(&format!("未知参数 {other}")),
        }
        i += 1;
    }

    let result = match cmd {
        "stats" => stats(&round1_config(&opts)),
        "run" => run(&round1_config(&opts)),
        "segments" => segments(&opts),
        "emotion" => emotion_cmd(&opts),
        _ => return usage(USAGE),
    };

    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("错误: {e}");
            ExitCode::FAILURE
        }
    }
}

fn round1_config(opts: &Opts) -> Config {
    Config {
        assets: opts.assets.clone(),
        queries: opts.queries.clone(),
        out: opts
            .out
            .clone()
            .unwrap_or_else(|| manifest("data/results.json")),
        topk: opts.topk,
        embed: opts.embed,
        verbose: opts.verbose,
    }
}

/// 第二轮：打印日志段清单 JSON，供金标标注与复现。
fn segments(opts: &Opts) -> Result<(), Box<dyn std::error::Error>> {
    let segs = emotion::load_segments(&opts.assets)?;
    let text = emotion::dump_segments(&segs)?;
    let path = opts
        .out
        .clone()
        .unwrap_or_else(|| manifest("data/segments.json"));
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(&path, &text)?;
    println!("{} 段已写入 {}", segs.len(), path.display());
    Ok(())
}

fn emotion_cmd(opts: &Opts) -> Result<(), Box<dyn std::error::Error>> {
    emotion::run(&emotion::Config {
        assets: opts.assets.clone(),
        gold: opts.gold.clone(),
        related: opts.related.clone(),
        out: opts
            .out
            .clone()
            .unwrap_or_else(|| manifest("data/emotion-results.json")),
        query_mode: opts.query_mode.clone(),
        embed: opts.embed,
        verbose: opts.verbose,
    })
}

fn usage(msg: &str) -> ExitCode {
    eprintln!("{msg}");
    ExitCode::from(2)
}
