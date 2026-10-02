# 小说策划助手

`knowl-searcher`（结构化索引与纯 RAG 对照实验）与 `task-board`（任务评审看板）合并而成的助手：一边从 memory 原始日志段里为加工中的创作素材捞相关片段，一边把任务扫描送进 Label Studio 评审、把意见写回 JSON。Python 实现，只用标准库，中间数据一律 JSON。

两轮合并前的实验分工：检索测机器怎么存、怎么找知识，看板测人的判断怎么回写进系统；合并后同一套数据约定与同一批固定回归。

## 内容

| 文件 | 说明 |
|------|------|
| `src/planner.py` | CLI：`segments` / `emotion` / `export` / `merge` 四个子命令 |
| `src/searcher.py` | 检索引擎：语料装载、切段、BM25、嵌入、出口裁决、评测 |
| `src/task_board.py` | 任务评审看板：Label Studio 导出与写回 |
| `label-config.xml` | Label Studio 标注配置，四枚标签固定，建项目时导入 |
| `docs/experiment.md` | 检索实验设计、设计评审与两轮结果 |
| `docs/cases.md` | 三则联想案例与使用边界 |
| `data/` | 中间数据，全部 JSON：段清单、金标标注、两臂结果、任务扫描与标注回写 |
| `tests/` | unittest 固定回归：切段与坐标、出口阈值、聚线、标注合并、BM25 确定性、看板读写 |

语料是主仓库 `assets/`（只读）：`memory/` 各集原始日志作索引，`fiction/观察站` 的情绪日记作查询。

## 用法

以下命令的工作目录是 `examples/quanttide-founder-lab`：

```sh
# 检索：重建日志段清单 data/segments.json
python3 examples/novel-planner/src/planner.py segments

# 检索：情绪日记联想评测（词法臂离线可跑）
python3 examples/novel-planner/src/planner.py emotion --scorer bm25
python3 examples/novel-planner/src/planner.py emotion --scorer embed   # 需 GLM_API_KEY 或 KNOWL_EMBED_*
python3 examples/novel-planner/src/planner.py emotion --query first    # 首句对照档

# 看板：导出任务清单 → Label Studio 标注 → 写回
# export 默认取 data/ 下文件名含「任务扫描」的最新一份（data/ 同时放检索 JSON）
python3 examples/novel-planner/src/planner.py export
python3 examples/novel-planner/src/planner.py merge <Label Studio 导出.json>

# 固定回归（改代码前先跑）
python3 -m unittest discover -s examples/novel-planner/tests

# 双库生成控制（实验，见 docs/dual-library.md）
python3 examples/novel-planner/src/planner.py rules          # 查看规则库（启用数/上限 12）
python3 examples/novel-planner/src/planner.py ledger         # 查看账本
python3 examples/novel-planner/src/planner.py ledger --backfill   # 从 source/测试文本.md 解析回填账本
python3 examples/novel-planner/src/planner.py generate D --scene scene.json   # 组装并写快照
python3 examples/novel-planner/src/planner.py annotate A style --count 2      # 记偏差
python3 examples/novel-planner/src/planner.py backflow       # 回流（带跨库护栏）
python3 examples/novel-planner/src/planner.py report         # 指标
```

## 检索口径

- 预注册参数：τ₁ = 0.55（共振下限）、τ₂ = 0.92（重复上限）、聚线 0.70，检索 top-8、评判 top-3；跑完标定，不回调参
- τ 是余弦分阈值，只对向量臂生效，词法臂 verdict 记 null
- 主指标 top-3 相关率，金标 `data/related/*.json`，设计与结果见 [docs/experiment.md](docs/experiment.md)
- 分数只排序不卡线：有用段与无用段的分数两堆分不开，读结果的姿势见 [docs/cases.md](docs/cases.md)

## 看板写回规则

- 按 `title` 对齐任务，取每条任务最后一个标注
- 每次写回追加 `history` 快照，时间取标注时间
- 内容与当前值一致时不重复记账，重复 `merge` 幂等
- `title` 对不上的标注跳过并告警

## 版本说明

合并前 `knowl-searcher` 是 Rust 实现（`cargo run` / `cargo test`），`task-board` 是 Python 脚本；合并后统一为 Python，检索口径与评测结果不变——`data/segments.json`、`data/results-bm25.json` 与合并前的产物逐字节一致，`data/results-embed.json` 的指标一致（分数有 API 噪声）。第一轮「索引粒度对照」的实现随 Rust 版下线，其设计与结果仍记录在 `docs/experiment.md`。
