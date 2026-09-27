//! knowl-searcher：结构化索引（parse-then-index）与纯 RAG（chunk-then-index）
//! 的对照实验。
//!
//! 可复用件：
//! - [`corpus`]：用工具箱的仓库模型定语料边界与类型，带行号切检索单元
//! - [`bm25`] / [`embed`]：两种打分器，同一套臂共用
//!
//! 实验设计与判定标准见 `docs/experiment.md`。

pub mod bm25;
pub mod chunk;
pub mod corpus;
pub mod embed;
pub mod emotion;
pub mod harness;
pub mod metrics;
