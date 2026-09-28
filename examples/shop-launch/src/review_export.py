#!/usr/bin/env python3
"""裁决队列的构建与 Label Studio 评审卡导出。

用法
    python3 src/review_export.py build    # 对照表 + ledger.GAPS → data/裁决队列.json
    python3 src/review_export.py export   # 队列 → data/review/任务清单.json + label-config.xml

build 从三处素材生成待裁决条目（对照表 L0 假设行、其中含 C 的环节归 C 类判断、
ledger.GAPS 的 L1 缺口），重建时保留已有 feedback 与提出日期，按 title 对齐去重。

export 的清单结构与 examples/task-board 的 export 输出同构（data.title/hint/meta
齐全，title 作回写主键），标注配置复制自 task-board 的 label-config.xml（只读，
写在本目录 data/review/）。回写见 review_merge.py。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

import assess
import ledger

HERE = Path(__file__).resolve().parent          # src/
ROOT = HERE.parent                              # shop-launch/
QUEUE = ROOT / "data" / "裁决队列.json"
TABLE = ROOT / "data" / "能力对照表.csv"
REVIEW_DIR = ROOT / "data" / "review"
CONFIG_SRC = ROOT.parent / "task-board" / "label-config.xml"
PROJECT = "开店裁决队列"
SUB = "滁州热锅串串 · L0 假设与 L1 缺口的人工裁决"


def load_queue() -> dict:
    if QUEUE.exists():
        return json.loads(QUEUE.read_text(encoding="utf-8"))
    return {"title": PROJECT, "sub": SUB, "tasks": [], "state": {"feedback": {}}}


def save(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def build_tasks() -> list[dict]:
    """三处素材 → 任务条目。同一环节只出一卡：含 C 归 C 类判断，否则 L0 假设。"""
    tasks = []
    for name, score, limit, dep, level, status in assess.read_csv(TABLE):
        if level != "L0":
            continue  # 已有证据的行不进裁决队列
        kind = "C类判断" if "C" in dep.split("+ ") else "L0假设"
        if kind == "C类判断":
            title = f"{name}：C 类人工兜底裁决"
            meta = f"能力分 {score} · 依赖 {dep} · 局限指名人工的部分须确认"
        else:
            title = f"{name}：L0 分 {score} 采不采纳"
            meta = f"能力分 {score} · 依赖 {dep} · {level} {status}"
        tasks.append({"type": kind, "folder": name, "title": title,
                      "hint": limit, "meta": meta + " · 来源 docs/AI 辅助开店.md"})
    for gap_name, source, why in ledger.GAPS:
        tasks.append({"type": "L1缺口", "folder": "待回填", "title": f"{gap_name}：L1 回填",
                      "hint": why, "meta": f"来源：{source}"})
    return tasks


def cmd_build(_args) -> int:
    queue = load_queue()
    old = {t["title"]: t for t in queue.get("tasks", [])}
    today = date.today().isoformat()
    tasks = []
    for t in build_tasks():
        prev = old.get(t["title"])
        tasks.append({**t, "date": prev["date"] if prev else today})
    queue["title"] = PROJECT
    queue["sub"] = SUB
    queue["tasks"] = tasks
    queue.setdefault("state", {}).setdefault("feedback", {})
    save(QUEUE, queue)
    n_fb = sum(1 for v in queue["state"]["feedback"].values() if v.get("tag"))
    print(f"{QUEUE.name}：{len(tasks)} 条待裁决（已标注 {n_fb}），feedback 保留")
    return 0


def build_cards(queue: dict) -> list[dict]:
    """任务清单：每条一个 data 块，title 作回写主键（与 task-board export 同构）。"""
    fb = queue.get("state", {}).get("feedback", {})
    cards = []
    for i, t in enumerate(queue["tasks"], 1):
        f = fb.get(t["title"], {})
        cards.append({
            "id": i,
            "data": {
                "title": t["title"],
                "type": t["type"],
                "folder": t["folder"],
                "rec": "",
                "hint": t["hint"],
                "meta": f"{t.get('date', '')} {t.get('meta', '')}".strip(),
                "tag": f.get("tag", ""),
                "text": f.get("text", ""),
                "project": queue.get("title", PROJECT),
                "sub": queue.get("sub", ""),
            },
        })
    return cards


def cmd_export(_args) -> int:
    queue = load_queue()
    if not queue["tasks"]:
        sys.exit("队列为空，先跑 build")
    if not CONFIG_SRC.exists():
        sys.exit(f"标注配置缺失（只读复用）：{CONFIG_SRC}")
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    out = REVIEW_DIR / "任务清单.json"
    save(out, build_cards(queue))
    shutil.copyfile(CONFIG_SRC, REVIEW_DIR / "label-config.xml")
    print(f"{len(queue['tasks'])} 个评审卡已写入 {out}")
    print(f"标注配置副本 {REVIEW_DIR / 'label-config.xml'}")
    print("下一步：Label Studio 新建项目（导入配置副本），再导入任务清单")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="review_export")
    p.add_argument("cmd", choices=["build", "export"])
    a = p.parse_args(argv)
    return cmd_build(a) if a.cmd == "build" else cmd_export(a)


if __name__ == "__main__":
    sys.exit(main())
