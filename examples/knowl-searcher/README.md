# knowl-searcher：结构化索引与纯 RAG 对照实验

验证「结构存知识」这一条：同一批文件、同一个打分器，只比「先解析成模型单元再索引」（parse-then-index）与「先切块再索引」（chunk-then-index）的差别。

## 内容

| 文件 | 说明 |
|------|------|
| `docs/experiment.md` | 实验设计 |
| `src/` | Rust 实现：语料装载、切块、BM25、嵌入、指标 |
| `tests/experiment.rs` | 纯函数回归 |

语料是主仓库 `assets/`，语料边界与类型由工具箱 Rust 包决定（path 依赖 `packages/quanttide-founder-toolkit/packages/rust`）。

## 运行

```sh
cargo run -- stats                # 语料统计
cargo run -- run --scorer bm25    # 词法臂，离线可跑
cargo run -- run --scorer embed   # 向量臂，需 GLM_API_KEY 或 KNOWL_EMBED_* 环境变量
cargo test                        # 纯函数回归
```
