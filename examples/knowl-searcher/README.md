# knowl-searcher：结构化索引与纯 RAG 对照实验

验证「结构存知识」这一条：同一批文件、同一个打分器，只比「先解析成模型单元再索引」（parse-then-index）与「先切块再索引」（chunk-then-index）的差别。第二轮验「向量管联想」：写作者把体验加工成情绪日记时，能否从 memory 原始日志里捞出有用片段。

## 内容

| 文件 | 说明 |
|------|------|
| `docs/experiment.md` | 实验设计、设计评审与两轮结果 |
| `src/` | Rust 实现：语料装载、切块、BM25、嵌入、指标、情绪日记联想 |
| `tests/` | 纯函数回归（第一轮对照、第二轮联想） |
| `data/` | 中间数据，全部 JSON：段清单、候选池、相关性标注、两臂结果 |

语料是主仓库 `assets/`，语料边界与类型由工具箱 Rust 包决定（path 依赖 `packages/quanttide-founder-toolkit/packages/rust`）。

## 运行

```sh
cargo run -- stats                # 第一轮：语料统计
cargo run -- run --scorer bm25    # 第一轮：词法臂，离线可跑
cargo run -- run --scorer embed   # 第一轮：向量臂，需 GLM_API_KEY 或 KNOWL_EMBED_* 环境变量
cargo run -- segments             # 第二轮：重建日志段清单 data/segments.json
cargo run -- emotion --scorer embed   # 第二轮：联想评测（向量臂，主臂）
cargo run -- emotion --scorer bm25    # 第二轮：词法对照臂
cargo run -- emotion --query first    # 第二轮：首句对照档
cargo test                        # 纯函数回归
```
