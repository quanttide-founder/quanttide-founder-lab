# 案例库

`examples/` 存放轻量脚本，用来快速验证方法论，按需重建与废弃。每个案例自成一个目录，自带说明、数据与测试。

## 案例

- [novel-planner](novel-planner/README.md)：小说策划助手，从 memory 原始日志为加工中的创作素材捞相关片段（联想检索），并把任务扫描导出到 Label Studio 标注、再把标注写回 JSON。

开店助手（原 `shop-launch`）已移交 `roadriver-tech` 仓库 `apps/shop-launcher`。

## 约定

- 一个案例一个目录，入口是目录下的 `README.md`；
- 中间数据放案例自己的 `data/`，一律 JSON；
- 回归测试随案例放置，不依赖图形界面。
