# 量潮创始人实验室

将方法论翻译为可执行程序的实验场。

## 核心模型

仓库按「规格—数据—代码」三层组织：

| 层 | 位置 | 性质 |
|----|------|------|
| 规格 | `docs/` | 方法论、流程与规则，是持久的沉淀物 |
| 数据 | `data/` | 案例与语料，结构化是演进方向；程序的中间 JSON 落各案例自己的 `data/` |
| 代码 | `examples/` | 轻量脚本，快更迭的验证载体，按需重建与废弃 |

## 目录结构

```
docs/
  dev-guide/ 标注工作流等工具说明
data/
  write/     写作规则与任务发现（task-discovery / writing-rules / creation-log-workflow）
  agent/     情绪结构化推演实例
  work/      工作方式语料
examples/
  novel-planner/  小说策划助手（联想检索 + 任务评审看板，export/merge 与 Label Studio 往返）
    src/          代码（planner / searcher / task_board）
    data/         中间数据（JSON：段清单、金标、两臂结果、任务扫描）
    tests/        固定测试（unittest，不依赖图形界面）
```

## 当前状态

- 联想检索 `examples/novel-planner/src/planner.py emotion`：从 memory 原始日志段为情绪日记草稿捞相关片段（bm25 / embed 两臂，预注册 τ 不回调），口径与结果见 `examples/novel-planner/docs/experiment.md`
- 任务评审 `examples/novel-planner/src/planner.py export`：读 `examples/novel-planner/data/*任务扫描*.json`，与 Label Studio 双向往返——`export` 导出任务清单去标注，`merge` 把标注写回 json；工作流见 `docs/dev-guide/label-studio.md`
- 固定测试 `python3 -m unittest discover -s examples/novel-planner/tests`：锁定切段、规则与看板读写行为，改代码前先跑
- 任务发现规则：`data/write/task-discovery.md`；写作规则：`data/write/writing-rules.md`；流程：`data/write/creation-log-workflow.md`

## 工作方式

1. 修改方法论：先改 `docs/`
2. 补充素材：直接进 `data/` 对应主题目录
3. 需要程序验证时：快速搭建、跑完即弃，产出沉淀回 `docs/` 与 `data/`
4. 实验过程记录到实验日志，保持文档与实际进展一致
