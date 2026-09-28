# 量潮创始人实验室

将方法论翻译为可执行程序的实验场。

## 核心模型

仓库按「规格—数据—代码」三层组织：

| 层 | 位置 | 性质 |
|----|------|------|
| 规格 | `docs/` | 方法论、流程与规则，是持久的沉淀物 |
| 数据 | `data/` | 案例与语料，结构化是演进方向；程序读写的数据也在这里（如 `data/write/*.json`） |
| 代码 | `examples/` | 轻量脚本，快更迭的验证载体，按需重建与废弃 |

## 目录结构

```
docs/
  write/     写作规则与创作日志的收集整理流程
data/
  write/     任务扫描数据（*.json，看板读写）
  agent/     情绪结构化推演实例
  work/      工作方式语料
examples/
  task-board/  任务评审看板（export/merge 与 Label Studio 往返）
tests/           固定测试（unittest，不依赖图形界面）
```

## 当前状态

- 任务评审 `examples/task-board/task_board.py`：读取 `data/write/*.json`，与 Label Studio 双向往返——`export` 导出任务清单去标注，`merge` 把标注写回 json；工作流见 `docs/dev-guide/label-studio.md`
- 固定测试 `tests/`：`python3 -m unittest discover -s tests`，锁定看板读写与状态行为，改代码先跑它
- 任务发现规则：`docs/write/task-discovery.md`；写作规则：`docs/write/writing-rules.md`；流程：`docs/write/creation-log-workflow.md`

## 工作方式

1. 修改方法论：先改 `docs/`
2. 补充素材：直接进 `data/` 对应主题目录
3. 需要程序验证时：快速搭建、跑完即弃，产出沉淀回 `docs/` 与 `data/`
4. 实验过程记录到实验日志，保持文档与实际进展一致
