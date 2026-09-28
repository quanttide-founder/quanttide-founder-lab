#!/usr/bin/env python3
"""裁决队列构建：对照表 L0 行 → data/裁决队列.json。

用法
    python3 src/review_export.py

只收可裁决项：对照表中证据等级 L0 的行，含 C 的归「C类判断」，其余「L0假设」。
L1 缺口（翻台率、询价等）不进队列——它们要数据不要投票，走 `ledger gaps` 与
实测回填（TODO.md 3.3）。行推进到 L1/L2 后自动退出队列。

作答方式（2026-09-28 设计纠偏，见 data/review/2026-09-28-裁决往返为何没用.md）：
不走 Label Studio。直接在队列 JSON 的 state.feedback 里写（或在对话中给出由
agent 代填），字段 tag ∈ {采纳, 否决, 存疑} + text 理由；写回见 review_merge.py。
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import assess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
QUEUE = ROOT / "data" / "裁决队列.json"
TABLE = ROOT / "data" / "能力对照表.csv"
PROJECT = "开店裁决队列"
SUB = "滁州热锅串串 · L0 假设与 C 类判断的人工裁决"
TAGS = ("采纳", "否决", "存疑")


def load_queue() -> dict:
    if QUEUE.exists():
        return json.loads(QUEUE.read_text(encoding="utf-8"))
    return {"title": PROJECT, "sub": SUB, "tasks": [], "state": {"feedback": {}}}


def save(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def build_tasks() -> list[dict]:
    """对照表 L0 行 → 待裁决条目，同一环节只出一卡。"""
    tasks = []
    for name, score, limit, dep, level, _status in assess.read_csv(TABLE):
        if level != "L0":
            continue  # 已有证据的行不进裁决队列
        if "C" in dep.split(" + "):
            tasks.append({"type": "C类判断", "folder": name,
                          "title": f"{name}：C 类人工兜底裁决",
                          "hint": f"{limit} —— 含 C 的部分由谁兜底、怎么兜底？",
                          "meta": f"能力分 {score} · 依赖 {dep} · 来源 docs/AI 辅助开店.md"})
        else:
            tasks.append({"type": "L0假设", "folder": name,
                          "title": f"{name}：L0 分 {score} 采不采纳",
                          "hint": limit,
                          "meta": f"能力分 {score} · 依赖 {dep} · {level} 待验证 · 来源 docs/AI 辅助开店.md"})
    return tasks


def cmd_build(_argv: list[str]) -> int:
    queue = load_queue()
    old = {t["title"]: t for t in queue.get("tasks", [])}
    today = date.today().isoformat()
    queue["title"] = PROJECT
    queue["sub"] = SUB
    queue["tasks"] = [{**t, "date": prev["date"] if (prev := old.get(t["title"])) else today}
                      for t in build_tasks()]
    queue.setdefault("state", {}).setdefault("feedback", {})
    save(QUEUE, queue)
    fb = queue["state"]["feedback"]
    answered = sum(1 for t in queue["tasks"]
                   if fb.get(t["title"], {}).get("tag") in TAGS)
    print(f"{QUEUE.name}：{len(queue['tasks'])} 条待裁决，已答 {answered}"
          f"（作答写 state.feedback：tag ∈ {'/'.join(TAGS)} + text 理由）")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] not in ("build", "-h", "--help"):
        sys.exit(f"未知命令 {argv[0]}：Label Studio 导出已废弃，只保留 build"
                 "（设计反思见 data/review/2026-09-28-裁决往返为何没用.md）")
    return cmd_build([])


if __name__ == "__main__":
    sys.exit(main())
