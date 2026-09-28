# TODO

[ROADMAP.md](ROADMAP.md) 的可执行拆解与验收判据。状态：☐ 待办 ◐ 进行中 ☑ 完成 ⏳ 待外部数据

**目录约定**（AGENTS.md）：**读不受限，写只落 `shop-launch/`**。`examples/task-board/` 的 export/merge 流程、`label-config.xml`、上层文档均可读作复用参考，外部目录一律不改；所有脚本、中间 JSON、对照表只写本目录。实地动作（探店、摆摊、Label Studio 标注）是数据来源，其产物同样落回本目录 `data/`。

**依赖链**：Phase 1（产出对照表）→ Phase 2（裁决回写推进证据等级）→ Phase 3（汇总偏差地图，受实测数据到位时间约束）。

---

## Phase 0 — 收敛：移除 GUI，只留数据处理代码（独立项，建议先行）

最终人机交互收敛为两个界面：`ledger.py` CLI 出数据（缺 L1 拒绝计算），Label Studio 收人裁决（Phase 2 链路）。GUI 三层报表与「估算代填」策略随交互迁移一并废弃——估算代填唯一消费者是 GUI，CLI 恒为 `allow_estimates=False`。不依赖 Phase 1–3，先行可缩小更迭面。

- [x] **0.1 删除 `src/ledger_gui.py`，清理仅 GUI 使用的估算代填路径**
  - `ledger.py` 一并清理：`ESTIMATES`、`ESTIMATE_NOTE`、`present(allow_estimates=...)` 与 `estimated` 分支；`COMPLETENESS` 完整度保留，供 `report` 显示可信度
  - 验收：`grep -rn "ledger_gui\|allow_estimates\|ESTIMATES" src/` 无残留；`python3 src/ledger.py selftest`、`report` 正常

- [x] **0.2 测试收敛**
  - `tests/test_ledger_present.py`：删除 GUI 估算策略用例（`test_gui_estimates` 等）与「CLI 与 GUI 共用同一份数据」的分叉断言，保留 CLI 拒绝计算、缺口、完整度与共享数据层；`allow_estimates=True` 的用例改写或删除
  - 验收：`python3 -m unittest discover -s tests` 全绿

- [x] **0.3 文档同步**（按变更规则先改 `AGENTS.md`）
  - `AGENTS.md`：`src/` 定位行删 GUI、「### GUI」章节与策略分歧段删除，交互面写成 CLI + Label Studio；`README.md` 内容表删 GUI 行并记一笔；`ROADMAP.md` 现状行、`docs/ledger.md` 图形界面与自检章节同步
  - 验收：`grep -rni "ledger_gui\|GUI" --include="*.md" --include="*.py" .` 无非历史性残留

- [x] **0.4 交互定型**
  - 验收：README 内容表体现分工——数据处理在 `src/`，人的判断在 Label Studio（Phase 2），不再有第三个人机界面

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

## Phase 2 — 做：人工裁决回写（ROADMAP「做」）

复用 `examples/task-board/`（只读）：清单格式取其 `build_tasks` 的输出结构，标注配置用其 `label-config.xml`（四枚标签：先做/缓做/不做/有异议），写回语义照其 merge 的「按 title 对齐、追加 history、重复幂等」。本目录产出清单与配置副本，不回写 task-board 的 `data/write/`。

- [x] **2.1 定义待裁决队列**
  - 素材三处：`docs/AI 辅助开店.md` 的 L0 假设行、对照表中含 C 环节、`src/ledger.py` 的 `GAPS` 待回填清单
  - 产出：`data/裁决队列.json`，每条含 `title, 环节, 问题, 类型(L0假设/C类判断/L1缺口), 提出日期, feedback(tag/text/history)`
  - 验收：队列条目与三处素材一一对应，无遗漏无重复；`feedback` 结构兼容 task-board 的写回语义

- [x] **2.2 导出评审卡**
  - 产出：`src/review_export.py`，读 `data/裁决队列.json` → 生成 Label Studio 可导入的任务清单 `data/review/任务清单.json` + 配置副本 `data/review/label-config.xml`（复制 task-board 的，写在本目录）
  - 验收：清单与 task-board `export` 输出同构（`data.title/hint/meta` 字段齐全），用其 `label-config.xml` 建的项目可直接导入本清单

- [x] **2.3 标注写回**
  - 产出：`src/review_merge.py`，读 Label Studio 导出 JSON → 写回 `data/裁决队列.json` 的 `feedback`，并推进 `data/能力对照表.csv` 的 `证据等级`（L0→L1→L2）与 `验证状态`（待验证→已验证/已证伪）；已由实地数据校准的 `[锁]` 项拒绝覆盖（`AGENTS.md` 硬约束）
  - 验收：重复 merge 幂等（同标注不重复记账）；`tests/` 覆盖「L0→L1 推进」「锁项被拒」两个用例

- [ ] **2.4 实操一轮标注**（人工，非代码）
  - Label Studio 建项目 → 导入 2.2 清单 → 逐条标注 → 导出落 `data/review/` → 跑 2.3 写回
  - 验收：裁决队列中至少 L0 假设类条目全部获得 `tag`，对照表证据等级出现 L1

## Phase 3 — 偏差地图：串联产出（ROADMAP「偏差地图」）

- [ ] **3.1 定义偏差地图 schema**
  - 产出：`data/偏差地图.csv`，字段：`环节, 品类, 城市, 预测值, 实测值, 偏差率, 证据等级, 来源日期`
  - 验收：字段能覆盖 `docs/AI 辅助开店.md` 第二层验证的三件事（鲜切牛肉签售价、损耗率 vs 10%、客单价落点）

- [ ] **3.2 汇总脚本**
  - 产出：`src/deviation.py`，从对照表 + 实测记录生成地图，并按品类/城市标注每环节可信度；输入全部取自本目录 `data/`
  - 验收：喂入 2–3 行样例实测数据能出表；无实测数据时明确报「缺数据」而非输出空结论

- [ ] ⏳ **3.3 实测数据回填**
  - 外部来源：探店 4 数据（翻台率、荤签品类、锅底好坏、服务短板）+ 丰全巷 2 天摆摊记录；到位后写入 `data/`，对照表推进到 L2，再跑 3.2

## Phase 4 — 文档：`docs/` 用法说明（面向使用者）

新工具随 Phase 0–3 陆续落地，`docs/` 只有 `ledger.md`（且 0.3 要删其 GUI 章节）。给使用者一份「装完怎么跑」的说明，按工具增章，不重复 `AGENTS.md` 的框架论述。

- [ ] **4.1 新工具用法各成一篇**（随对应任务交付，不攒到最后）
  - `docs/assess.md`：评估流水与一致性检查怎么跑、输出表字段怎么读（Phase 1）
  - `docs/review.md`：裁决队列 → 导出清单 → Label Studio 标注 → 写回，整条往返命令与目录约定（Phase 2；可参考 task-board 的 README 体例，只读不改）
  - `docs/deviation.md`：偏差地图 schema 与汇总脚本用法（Phase 3）
  - 验收：每篇含「一条最短可跑命令 + 输出示例 + 边界说明」；GUI 相关内容随 0.3 从 `ledger.md` 移除

- [ ] **4.2 README 接口**
  - `README.md` 内容表与 `AGENTS.md` 快速索引挂上新篇目；`docs/` 每篇首行链回对应 TODO 任务
  - 验收：从 README 三跳内到达任一工具的用法页；`docs/` 下无孤儿篇目、无未挂链接的工具

---

## 变更规则

- 只改案例数据/新增任务 → 改本文件；改字段、标尺、判定规则 → 先改 `AGENTS.md`，本文件与 `README.md` 记一笔
- 任务完成即勾选并在提交信息中注明；⏳ 项保留等待说明，不删除
