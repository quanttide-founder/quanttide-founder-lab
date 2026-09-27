//! knowl-searcher CLI：
//!
//! ```text
//! cargo run -- stats                          # 语料与索引统计
//! cargo run -- run --scorer bm25              # 离线词法臂（默认）
//! cargo run -- run --scorer embed             # 向量臂（需 embedding key）
//! cargo run -- run --scorer bm25 --verbose    # 附逐查询命中明细
//! ```

use std::env;
use std::path::PathBuf;
use std::process::ExitCode;

use knowl_searcher::harness::{Config, run, stats};

fn manifest(rel: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(rel)
}

fn main() -> ExitCode {
    let args: Vec<String> = env::args().collect();
    let cmd = args.get(1).map(String::as_str).unwrap_or("");

    let mut cfg = Config {
        assets: manifest("../../../../assets"),
        queries: manifest("data/queries.json"),
        out: manifest("data/results.json"),
        topk: 5,
        embed: false,
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
                Some(v) => cfg.assets = PathBuf::from(v),
                None => return usage("--assets 缺值"),
            },
            "--queries" => match need(&mut i) {
                Some(v) => cfg.queries = PathBuf::from(v),
                None => return usage("--queries 缺值"),
            },
            "--out" => match need(&mut i) {
                Some(v) => cfg.out = PathBuf::from(v),
                None => return usage("--out 缺值"),
            },
            "--topk" => match need(&mut i).and_then(|v| v.parse::<usize>().ok()) {
                Some(v) if v > 0 => cfg.topk = v,
                _ => return usage("--topk 需要正整数"),
            },
            "--scorer" => match need(&mut i).map(String::as_str) {
                Some("bm25") => cfg.embed = false,
                Some("embed") => cfg.embed = true,
                Some(other) => return usage(&format!("未知打分器 {other}，可选 bm25 | embed")),
                None => return usage("--scorer 缺值"),
            },
            "--verbose" => cfg.verbose = true,
            other => return usage(&format!("未知参数 {other}")),
        }
        i += 1;
    }

    let result = match cmd {
        "stats" => stats(&cfg),
        "run" => run(&cfg),
        _ => {
            return usage(
                "用法: knowl-searcher <stats|run> [--scorer bm25|embed] [--topk N] [--verbose] [--assets DIR] [--queries FILE] [--out FILE]",
            );
        }
    };

    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("错误: {e}");
            ExitCode::FAILURE
        }
    }
}

fn usage(msg: &str) -> ExitCode {
    eprintln!("{msg}");
    ExitCode::from(2)
}
