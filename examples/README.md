# 案例库

`examples/` 存放轻量脚本，用来快速验证方法论，按需重建与废弃。每个案例自成一个目录，自带说明、数据与测试。

## 案例

- [knowl-searcher](knowl-searcher/README.md)：结构化索引与纯 RAG 对照实验，验证「结构存知识」与「向量管联想」；
- [shop-launch](shop-launch/README.md)：开店计划，以及一次「AI 能干什么、不能干什么」的实地验证；
- [task-board](task-board/README.md)：任务评审看板，导出任务到 Label Studio 标注，再把标注写回 JSON。

## 约定

- 一个案例一个目录，入口是目录下的 `README.md`；
- 中间数据放案例自己的 `data/`，一律 JSON；
- 回归测试随案例放置，不依赖图形界面。
