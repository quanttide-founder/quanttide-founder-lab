# TODO

[ROADMAP.md](ROADMAP.md) 的可执行拆解与验收判据。状态：☐ 待办 ◐ 进行中 ☑ 完成 ⏳ 待外部数据

**目录约定**（AGENTS.md）：**读不受限，写只落 `shop-launch/`**。外部实验与上层文档均可读作复用参考，外部目录一律不改；所有脚本、中间 JSON、对照表只写本目录。实地动作（探店、摆摊）是数据来源，其产物同样落回本目录 `data/`。

**依赖链**：Phase 1（产出判例库）→ Phase 2（人机对齐：判例复核与法条修订）→ Phase 3（汇总偏差地图，受实测数据到位时间约束）。

---

## Phase 0 — 收敛：移除 GUI，只留数据处理代码（独立项，建议先行）

最终人机交互收敛：`ledger.py` CLI 出数据（缺 L1 拒绝计算），人的判断收在 Phase 2 对齐链路（Label Studio 往返与直答队列两版均废弃，终态为判例/成文法双标注，见 `data/review/2026-09-28-裁决往返为何没用.md`）。GUI 三层报表与「估算代填」策略随交互迁移一并废弃——估算代填唯一消费者是 GUI，CLI 恒为 `allow_estimates=False`。不依赖 Phase 1–3，先行可缩小更迭面。

- [x] **0.1 删除 `src/ledger_gui.py`，清理仅 GUI 使用的估算代填路径**
  - `ledger.py` 一并清理：`ESTIMATES`、`ESTIMATE_NOTE`、`present(allow_estimates=...)` 与 `estimated` 分支；`COMPLETENESS` 完整度保留，供 `report` 显示可信度
  - 验收：`grep -rn "ledger_gui\|allow_estimates\|ESTIMATES" src/` 无残留；`python3 src/ledger.py selftest`、`report` 正常

- [x] **0.2 测试收敛**
  - `tests/test_ledger_present.py`：删除 GUI 估算策略用例（`test_gui_estimates` 等）与「CLI 与 GUI 共用同一份数据」的分叉断言，保留 CLI 拒绝计算、缺口、完整度与共享数据层；`allow_estimates=True` 的用例改写或删除
  - 验收：`python3 -m unittest discover -s tests` 全绿

- [x] **0.3 文档同步**（按变更规则先改 `AGENTS.md`）
  - `AGENTS.md`：`src/` 定位行删 GUI、「### GUI」章节与策略分歧段删除，交互面写成 CLI + 对齐链路；`README.md` 内容表删 GUI 行并记一笔；`ROADMAP.md` 现状行、`docs/ledger.md` 图形界面与自检章节同步
  - 验收：`grep -rni "ledger_gui\|GUI" --include="*.md" --include="*.py" .` 无非历史性残留

- [x] **0.4 交互定型**
  - 验收：README 内容表体现分工——数据处理在 `src/`，人的判断在判例复核与法条修订（Phase 2），不再有第三个人机界面

---

## Phase 1 — 分：能力边界框架升级（ROADMAP「分」）

- [x] **1.1 定义能力对照表的机器可读格式**
  - 产出：`data/能力对照表.csv`，字段对齐 `AGENTS.md` 六字段：`环节, AI 能力分, 局限, 输入依赖, 证据等级, 验证状态`
  - 输入种子：`docs/AI 辅助开店.md` 中 8 行手填对照表（市场调研 80 … 现场运营 20）
  - 验收：8 行全部六字段齐全，无空值；含 C 的行 `局限` 必有点名人工兜底的句子

- [x] **1.2 评估流水脚本**（按快更迭原则，验证后可弃）
  - 产出：`src/assess.py`，输入决策环节 + 材料文本 → 输出能力分、输入依赖、证据等级三字段
  - 验收：`python3 src/assess.py --seed docs/AI\ 辅助开店.md` 产出与 1.1 同构的表；对新增环节（如「证照合规」）能出完整行

- [x] **1.3 一致性检查自动化**
  - 并入 `src/assess.py check` 子命令（或独立小脚本），规则照抄 `AGENTS.md`：纯 A ≥ 60、含 C ≤ 80、纯 C ≤ 30、B 类必须标证据等级
  - 验收：构造 4 类违规样本报错并以非 0 退出；`tests/` 补回归用例，`python3 -m unittest discover -s tests` 全绿

- [x] **1.4 产出落 `data/` 并接进文档**
  - 验收：对照表在 `data/`；`README.md` 内容表与 `AGENTS.md` 产出节链接到它

## Phase 2 — 做：人机对齐（判例与成文法，ROADMAP「做」）

**定位**：具体决策（判例）与决策准则（成文法）都显式存，各配与之匹配的人工标注。AI 按成文法自主判决，人只做两层标注——**判例层复核**（维持/改判，抽查制）、**法条层修订**（改 `AGENTS.md` 条文后同步 `assess.py` 重放，归纳制）。逐条裁决的两版实现（Label Studio 往返、直答队列）均因把决策负担推给人而废弃，反思归档 `data/review/2026-09-28-裁决往返为何没用.md`。机制定义见 `AGENTS.md`「人机对齐」，用法见 `docs/alignment.md`。

- [x] **2.1 成文法条文显式编号**
  - `AGENTS.md` 一致性检查 → `检查§1–4`，硬约束 → `硬约束§n`，配合既有的 `标尺`/`依A·B·C`/`证据`，判例可引用
  - 验收：判例 `依据法条` 列能写出形如 `依A·依C·标尺·检查§2` 的引用

- [x] **2.2 判例库显式化（八字段）**
  - 产出：`data/能力对照表.csv` 扩为八字段（+ `依据法条` + `复核`）；`src/assess.py` 推导自动填法条引用，`check` 校验 `复核 ∈ 未复核/维持/改判`
  - 验收：`--seed` 重放不覆盖 `维持/改判` 行（改判终审），未复核行重算；`tests/` 覆盖三态重放

- [x] **2.3 两种标注通道落文档与数据**
  - 产出：`docs/alignment.md`（判例复核 / 法条修订怎么走、重放规则、放大率算法）、`data/纠偏记录.md`（层/对象/标注/分诊/影响行数）
  - 验收：README 分工行、`AGENTS.md` 产出节、`docs/assess.md` 均链到 alignment；纠偏记录有首条（本轮设计纠偏即第一条）

- [x] **2.4 拆除逐条裁决实现**
  - 删 `src/review_export.py`、`src/review_merge.py`、`data/裁决队列.json`、`tests/test_review.py`
  - 验收：全库 grep 无 `review_export|review_merge|裁决队列` 残留引用（反思文档的历史记述除外）

- [ ] ⏳ **2.5 实测一轮对齐循环**（人工，非代码）
  - 抽查 → 判例改判（记纠偏记录）→ 分诊偶发/通则 → 通则修 `AGENTS.md` 条文 → 同步 `assess.py` → `--seed` 重放 → `git diff` 取影响行数记账
  - 验收：纠偏记录含至少 1 条成文法层条目且填了影响行数；重放后 `check` 全过、改判行仍在；得出首个**放大率**（影响行数 / 干预次数）

## Phase 3 — 偏差地图：串联产出（ROADMAP「偏差地图」）

- [x] **3.1 定义偏差地图 schema**
  - 产出：`data/偏差地图.csv`，字段：`环节, 品类, 城市, 预测值, 实测值, 偏差率, 证据等级, 来源日期`
  - 验收：字段能覆盖 `docs/AI 辅助开店.md` 第二层验证的三件事（鲜切牛肉签售价、损耗率 vs 10%、客单价落点）

- [x] **3.2 汇总脚本**
  - 产出：`src/deviation.py`，从对照表 + 实测记录生成地图，并按品类/城市标注每环节可信度；输入全部取自本目录 `data/`
  - 验收：喂入 2–3 行样例实测数据能出表；无实测数据时明确报「缺数据」而非输出空结论

- [ ] ⏳ **3.3 实测数据回填**
  - 外部来源：探店 4 数据（翻台率、荤签品类、锅底好坏、服务短板）+ 丰全巷 2 天摆摊记录；到位后写入 `data/`，对照表推进到 L2，再跑 3.2

## Phase 4 — 文档：`docs/` 用法说明（面向使用者）

新工具随 Phase 0–3 陆续落地，`docs/` 只有 `ledger.md`（且 0.3 要删其 GUI 章节）。给使用者一份「装完怎么跑」的说明，按工具增章，不重复 `AGENTS.md` 的框架论述。

- [x] **4.1 新工具用法各成一篇**（随对应任务交付，不攒到最后）
  - `docs/assess.md`：评估流水与一致性检查怎么跑、输出表字段怎么读（Phase 1）
  - `docs/alignment.md`：判例复核与法条修订两种标注、重放规则、放大率算法（Phase 2）
  - `docs/deviation.md`：偏差地图 schema 与汇总脚本用法（Phase 3）
  - 验收：每篇含「一条最短可跑命令 + 输出示例 + 边界说明」；GUI 相关内容随 0.3 从 `ledger.md` 移除

- [x] **4.2 README 接口**
  - `README.md` 内容表与 `AGENTS.md` 快速索引挂上新篇目；`docs/` 每篇首行链回对应 TODO 任务
  - 验收：从 README 三跳内到达任一工具的用法页；`docs/` 下无孤儿篇目、无未挂链接的工具

---

## 变更规则

- 只改案例数据/新增任务 → 改本文件；改字段、标尺、判定规则 → 先改 `AGENTS.md`，本文件与 `README.md` 记一笔
- 任务完成即勾选并在提交信息中注明；⏳ 项保留等待说明，不删除
