#!/usr/bin/env python3
"""裁决写回：data/裁决队列.json 的 feedback → data/能力对照表.csv。

用法
    python3 src/review_merge.py

作答：队列 JSON 的 state.feedback，按 title 对齐
    {"<title>": {"tag": "采纳|否决|存疑", "text": "理由…"}}
（可直接编辑文件，也可在对话中给出由 agent 代填。）

写回规则（状态式，天然幂等——重复跑不会重复推进）
    采纳 + 理由 → 对照表证据等级 L0 → L1（人的本地判断已喂入）
    否决 + 理由 → 验证状态 = 已证伪
    存疑        → 不动，留 L0 等数据
    证据等级 L2 的行拒绝覆盖（AGENTS.md 硬约束：实地校准不被改写）
    无理由的采纳/否决不生效（打印提示）

分数调整不走标签：直接改 data/能力对照表.csv，`assess check` 把关。
设计纠偏背景见 data/review/2026-09-28-裁决往返为何没用.md。
"""
from __future__ import annotations

import sys
from pathlib import Path

import assess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
QUEUE = ROOT / "data" / "裁决队列.json"
TABLE = ROOT / "data" / "能力对照表.csv"
TAGS = ("采纳", "否决", "存疑")


def advance(row: list[str], tag: str, text: str) -> str:
    """一行对照表按作答推进。返回 advanced / refused / noop。"""
    if row[4] == "L2":
        return "refused"
    if not text:
        return "noop"  # 无理由不生效
    if tag == "否决":
        if row[5] == "已证伪":
            return "noop"
        row[5] = "已证伪"
        return "advanced"
    if tag == "采纳" and row[4] == "L0":
        row[4] = "L1"
        return "advanced"
    return "noop"


def apply(queue: dict, rows: list[list[str]]) -> tuple[list[str], list[str], int]:
    """feedback → 对照表。返回 (推进说明, 拒绝说明, 未答数)。"""
    fb = queue.get("state", {}).get("feedback", {})
    folder_by_title = {t["title"]: t["folder"] for t in queue["tasks"]}
    advanced, refused, pending = [], [], 0
    for title in folder_by_title:
        f = fb.get(title, {})
        tag, text = f.get("tag", ""), f.get("text", "")
        if tag not in TAGS:
            pending += 1
            continue
        folder = folder_by_title[title]
        row = next((r for r in rows if r[0] == folder), None)
        if row is None:
            continue  # L1 缺口类条目不在对照表，feedback 即终点
        if tag in ("采纳", "否决") and not text:
            print(f"提示：{title} 作答「{tag}」无理由，不生效")
            continue
        result = advance(row, tag, text)
        if result == "advanced":
            advanced.append(f"{folder}：{tag} → {row[4]}/{row[5]}")
        elif result == "refused":
            refused.append(f"{folder}：L2 实测结论不被改写，已跳过")
    return advanced, refused, pending


def main(argv: list[str] | None = None) -> int:
    import json
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    rows = assess.read_csv(TABLE)
    advanced, refused, pending = apply(queue, rows)
    if advanced:
        write_csv(TABLE, rows)
    for msg in advanced:
        print(f"推进：{msg}")
    for msg in refused:
        print(f"拒绝：{msg}")
    total = len(queue["tasks"])
    print(f"已写回 {len(advanced)} 项，未答 {pending}/{total}")
    if pending:
        print("（作答：编辑 data/裁决队列.json 的 state.feedback，或在对话中给出）")
    return 0


def write_csv(path: Path, rows: list[list[str]]) -> None:
    import csv
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(assess.HEADER)
        w.writerows(rows)
    tmp.replace(path)


if __name__ == "__main__":
    sys.exit(main())
