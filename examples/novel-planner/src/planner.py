#!/usr/bin/env python3
"""小说策划助手 CLI：联想检索 + 任务评审看板。

用法：
    python3 examples/novel-planner/src/planner.py segments [--assets DIR] [--out FILE]
    python3 examples/novel-planner/src/planner.py emotion [--scorer bm25|embed] [--query full|first]
    python3 examples/novel-planner/src/planner.py export [任务扫描.json] [--out 任务清单.json]
    python3 examples/novel-planner/src/planner.py merge <label-studio导出.json> [任务扫描.json]

segments：重建日志段清单 data/segments.json（金标标注与复现用）
emotion  ：从 memory 原始日志段为情绪日记草稿捞相关片段，写 data/emotion-results.json；
           向量臂需 KNOWL_EMBED_API_KEY 或 GLM_API_KEY 环境变量
export   ：把 data/ 下最新一份任务扫描导出为 Label Studio 可导入的任务清单
merge    ：把 Label Studio 标注按 title 写回 state.feedback（重复导入幂等）

双库生成控制（docs/dual-library.md）：
rules    ：查看风格规则库（启用数/上限 12）
ledger   ：查看情节账本（按类型计数/未解欠账）
generate ：组装上下文并写生成快照（不调模型，文本槽留空）
annotate ：记录一组偏差计数（group type --count）
backflow ：把偏差回流进规则库/账本（带跨库护栏）
report   ：计算四组偏差指标

标注配置 label-config.xml 建项目时导入；检索金标在 data/related/*.json。
中间数据一律 JSON，程序不读写文档。检索口径见 docs/experiment.md，
看板写回规则见 task_board.py。
"""

import argparse
import sys
from pathlib import Path

import searcher
import task_board
import dual_library

# 代码在 src/，PROJECT 取项目根（数据与语料都在它外面或下面）
PROJECT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT / "data"
# 语料在主仓库 assets/（只读），与旧版 knowl-searcher 同一相对深度
DEFAULT_ASSETS = PROJECT.parents[3] / "assets"


def cmd_segments(argv: list) -> None:
    p = argparse.ArgumentParser(prog="planner segments")
    p.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    p.add_argument("--out", type=Path, default=DATA_DIR / "segments.json")
    a = p.parse_args(argv)
    segments = searcher.load_segments(a.assets)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(searcher.dump_segments(segments), encoding="utf-8")
    print(f"{len(segments)} 段已写入 {a.out}")


def cmd_emotion(argv: list) -> None:
    p = argparse.ArgumentParser(prog="planner emotion")
    p.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    p.add_argument("--related", type=Path, default=DATA_DIR / "related")
    p.add_argument("--out", type=Path, default=DATA_DIR / "emotion-results.json")
    p.add_argument("--scorer", choices=("bm25", "embed"), default="bm25")
    p.add_argument("--query", choices=("full", "first"), default="full")
    a = p.parse_args(argv)
    try:
        searcher.run_emotion(a.assets, a.related, a.out,
                             query_mode=a.query, embed=(a.scorer == "embed"))
    except (RuntimeError, ValueError) as e:
        sys.exit(f"错误: {e}")


def main(argv: list = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    usage = __doc__
    if not argv or argv[0] not in (
        "segments", "emotion", "export", "merge",
        "rules", "ledger", "generate", "annotate", "backflow", "report",
    ):
        print(usage, file=sys.stderr)
        raise SystemExit(2)
    cmd, rest = argv[0], argv[1:]
    if cmd == "segments":
        cmd_segments(rest)
    elif cmd == "emotion":
        cmd_emotion(rest)
    elif cmd == "export":
        task_board.cmd_export(rest)
    elif cmd == "merge":
        task_board.cmd_merge(rest)
    elif cmd in ("rules", "ledger", "generate", "annotate", "backflow", "report"):
        getattr(dual_library, "cmd_" + cmd)(rest)
    else:
        print(usage, file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
