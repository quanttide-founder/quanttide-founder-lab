# 任务评审看板

把任务扫描 JSON 导出为 Label Studio 任务清单，人工标注后再把结果写回 JSON 的意见字段。标注在本地 Label Studio 界面完成，程序只做导出与写回，中间数据一律 JSON。

## 内容

- `task_board.py`：CLI，`export` 导出任务清单，`merge` 写回标注；
- `label-config.xml`：Label Studio 标注配置，四枚标签固定，建项目时导入；
- `data/`：任务扫描 JSON 与同名说明文档；
- `test_task_board.py`：回归测试，锁定读写、状态、清单生成与写回幂等，不依赖图形界面。

## 用法

导出任务清单，默认取最新一份任务扫描 JSON：

```sh
python3 examples/task-board/task_board.py export
```

在 Label Studio 中逐条标注后，把导出的文件写回：

```sh
python3 examples/task-board/task_board.py merge <Label Studio 导出.json>
```

## 写回规则

- 按 `title` 对齐任务，取每条任务最后一个标注；
- 每次写回追加 `history` 快照，时间取标注时间；
- 内容与当前值一致时不重复记账，重复 `merge` 幂等；
- `title` 对不上的标注跳过并告警。

工作流全貌见 [docs/dev-guide/label-studio.md](../../docs/dev-guide/label-studio.md)。
