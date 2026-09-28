# Label Studio 标注工作流

本地 Label Studio 实例承接需要人工确认的标注：程序导出任务清单，人在界面里标注，程序把结果写回 JSON。中间数据一律 JSON，程序不碰文档。

## 本地实例

| 项 | 值 |
|----|-----|
| 启动 | `label-studio start --port 8090 --enable-legacy-api-token` |
| 地址 | http://localhost:8090 |
| 版本 | 1.23.0（`label-studio version` 查看） |

## 任务评审标注

工具在 `examples/novel-planner/`，读写 `examples/novel-planner/data/*任务扫描*.json` 的评审意见（`state.feedback`）。

### 步骤

1. 导出任务清单（默认取 `data/` 下文件名含「任务扫描」的最新一份）：

   ```bash
   python3 examples/novel-planner/planner.py export
   ```

   产出 `examples/novel-planner/data/label-studio/tasks.json`，已有意见随任务带出。

2. Label Studio 新建项目，导入 `examples/novel-planner/label-config.xml`，再导入任务清单。

3. 逐条标注：四枚标签 `先做 / 缓做 / 不做 / 有异议` 选一，理由写进文本框。已有意见显示在「当前意见」栏，照旧可改。

4. 项目页 Export → JSON，下载导出文件。

5. 写回数据文件（不带路径参数则写最新一份）：

   ```bash
   python3 examples/novel-planner/planner.py merge <导出文件.json>
   ```

### 写回规则

- 按 `title` 对齐任务，取每条任务最后一个标注
- 每次写回追加 `history` 快照（时间取标注时间）
- 内容与当前值一致时不重复记账，重复 merge 幂等
- `title` 对不上的标注跳过并告警

## 相关性金标抽查（novel-planner 检索）

`examples/novel-planner/data/related/diary.json` 的 AI 判断要经作者抽查。抽查走同一模式：程序按 `examples/novel-planner/data/pool.json` 导出候选对，人在界面里逐对判有用/没用，导出后按（查询, 段 id）写回。该导出命令尚未实现，属下轮工作。
