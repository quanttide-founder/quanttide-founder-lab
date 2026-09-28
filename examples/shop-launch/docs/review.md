# 裁决往返使用说明

`src/review_export.py` 与 `src/review_merge.py` 的用户文档，对应 [TODO.md](../TODO.md) Phase 2。回答一件事：AI 定不了的环节，怎么交给人在 Label Studio 里裁决、再写回系统。

程序只做导出与写回，标注本身在本地 Label Studio 界面完成；中间数据一律 JSON/CSV，全部落在本目录。清单格式与标注配置只读复用 `examples/task-board/`（不改该目录）。

## 最短可跑命令（整条往返）

```sh
python3 src/review_export.py build     # 对照表 L0 行 + GAPS → data/裁决队列.json
python3 src/review_export.py export    # 队列 → data/review/任务清单.json + label-config.xml
```

```
裁决队列.json：15 条待裁决（已标注 0），feedback 保留
15 个评审卡已写入 data/review/任务清单.json
标注配置副本 data/review/label-config.xml
下一步：Label Studio 新建项目（导入配置副本），再导入任务清单
```

Label Studio 里逐条标注（四枚标签：先做/缓做/不做/有异议 + 理由），导出 JSON 后：

```sh
python3 src/review_merge.py <Label Studio 导出.json>
```

```
写回 1 条，对照表推进 1 行
```

再跑一次同文件应输出 `写回 0 条`——幂等是硬要求。

## 待裁决队列的三处素材

| 素材 | 归类 | 条数 |
|------|------|------|
| `data/能力对照表.csv` 的 L0 行 | L0假设 | 5 |
| 其中含 C 的环节 | C类判断 | 3 |
| `src/ledger.py` 的 `GAPS` 待回填清单 | L1缺口 | 7 |

`build` 重建时按 `title` 对齐去重、保留已有 `feedback` 与提出日期，重复跑不丢标注。对照表升到 L1/L2 的行不再进队列。

## 写回规则

按 `title` 对齐，取每条任务最后一个标注，`feedback` 追加 `history` 快照，内容一致不重复记账（照 task-board 的 merge 语义）。多一条推进规则：

| 标注 | 对照表动作 |
|------|-----------|
| 带理由的标签（先做/缓做/不做 + text） | 证据等级 L0 → L1（人的本地判断已喂入） |
| 有异议 | 该环节验证状态 → 已证伪 |
| 证据等级 L2 的行 | **拒绝覆盖**，feedback 照记、对照表不动（`AGENTS.md` 硬约束） |
| L1缺口条目（folder=待回填） | 不在对照表，只写 feedback |

`title` 对不上的标注跳过并告警；未匹配清单的评审卡不落盘。

## 目录约定

```
data/裁决队列.json        队列（任务 + feedback），写回目标
data/review/任务清单.json  Label Studio 可导入的评审卡
data/review/label-config.xml  标注配置副本（复制自 task-board）
data/能力对照表.csv        写回推进的对象
```

## 边界

- **标注是判断，不是实测**。L0→L1 到此为止；升 L2（已实测）只能由探店/摆摊数据回填触发，标注推不动
- **不回写 task-board**。其 `data/write/` 属于另一实验，本目录只产自己的清单与配置副本
- **2.4 实操一轮是人工步骤**：建项目、导入、标注、导出、写回，验收看 L0 假设类条目是否全部获得 tag
