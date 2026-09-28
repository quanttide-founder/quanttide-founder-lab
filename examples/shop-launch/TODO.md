# TODO

[ROADMAP.md](ROADMAP.md) 的可执行拆解与验收判据。状态：☐ 待办 ☑ 完成 ⏳ 待实测/外部数据

**目录约定**（`AGENTS.md`）：读不受限，写只落 `shop-launch/`；外部文档只读作参考，实地动作的产物落回本目录 `data/`。

**依赖链**：Phase 1（判例库）→ Phase 2（人机对齐）→ Phase 3（偏差地图，受实测约束）。

## 已完成

- **Phase 0 收敛**：删 GUI 与估算代填路径，交互面只剩 CLI（`report` 三层报表）+ 对齐链路
- **Phase 1 分**：`src/assess.py`（推导 / `check` 一致性检查 / `--seed` 重放）+ 判例库 `data/能力对照表.csv`（八字段，8 行，种子 `docs/AI 辅助开店.md`）
- **Phase 2 机制**：判例复核 GUI `src/review_gui.py`（本地标注，保存即校验，改判自动记纠偏记录；Label Studio 项目已删）、成文法条文编号（`检查§1–4`、`硬约束§n`）、判例优先重放（改判终审）、`docs/alignment.md` 用法、`data/纠偏记录.md` 流水；逐条裁决的两版实现（Label Studio 往返、直答队列）已删，反思见 `data/review/2026-09-28-裁决往返为何没用.md`
- **Phase 3 schema**：`data/偏差地图.csv` + `src/deviation.py`（缺数据报缺口退出码 2，不输出空结论）
- **Phase 2 标注对象**：GUI 重做——要标注的两类信息显式落 `data/决策判例.csv`（16 条实质决策）与 `data/准则候选.csv`（10 条隐含准则），互链校验；采纳自动渲染 `docs/决策准则.md`（原元评估 8 行视图被判「用处为 0」，降为背景）
- **Phase 4 文档**：`docs/assess.md`、`docs/alignment.md`、`docs/deviation.md` 各一篇，README / AGENTS 挂链

## 待办

- [ ] ⏳ **2.5 实测一轮对齐循环**（人工，非代码）
  - 抽查 → 判例改判（记纠偏记录）→ 分诊偶发/通则 → 通则修 `AGENTS.md` 条文 → 同步 `assess.py` → `--seed` 重放 → `git diff data/能力对照表.csv` 取影响行数记账
  - 验收：纠偏记录含 ≥1 条成文法层条目且填影响行数；重放后 `check` 全过、改判行仍在；得出首个**放大率**（影响行数 / 干预次数）

- [ ] ⏳ **3.3 实测数据回填**
  - 外部来源：探店 4 数据（翻台率、荤签品类、锅底好坏、服务短板）+ 丰全巷 2 天摆摊记录
  - 验收：写入 `data/` 后对照表推进 L2，`src/deviation.py` 出表覆盖三件实测（鲜切牛肉签售价、损耗率 vs 10%、客单价落点）

## 变更规则

- 只改案例数据/新增任务 → 改本文件；改字段、标尺、判定规则 → 先改 `AGENTS.md`，本文件与 `README.md` 记一笔
