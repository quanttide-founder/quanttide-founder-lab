# 案例库

`examples/` 存放轻量脚本，用来快速验证方法论，按需重建与废弃。每个案例自成一个目录，自带说明、数据与测试。

## 案例

- [novel-planner](novel-planner/README.md)：小说策划助手。三条能力轨：① 联想检索（从 memory 原始日志为创作素材捞相关片段）；② 任务评审看板（任务扫描导出 Label Studio 标注、写回 JSON）；③ 双库生成控制（风格规则库 + 情节账本，实验性，见 `novel-planner/docs/dual-library.md`）。

开店助手（原 `shop-launch`）已移交 `roadriver-tech` 仓库 `apps/shop-launcher`。

## 约定

- 一个案例一个目录，入口是目录下的 `README.md`；
- 中间数据放案例自己的 `data/`，一律 JSON；
- 回归测试随案例放置，不依赖图形界面。
