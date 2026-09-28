#!/usr/bin/env python3
"""标注写回：Label Studio 导出 → 裁决队列 feedback + 推进对照表证据等级。

用法
    python3 src/review_merge.py <Label Studio 导出.json>

写回语义（照 task-board 的 merge：按 title 对齐、取最后一条标注、追加 history、
重复导入幂等），多一条推进规则：
    tag = 有异议            → 对照表该环节 验证状态 = 已证伪
    其余标签且带理由 text    → 证据等级 L0 → L1（人的本地判断已喂入）
    证据等级 L2 的行拒绝覆盖 —— 实地校准的结论不被标注改写（AGENTS.md 硬约束）

L1 缺口条目（folder=待回填）不在对照表中，只写 feedback。
"""
from __future__ import annotations

import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import assess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
QUEUE = ROOT / "data" / "裁决队列.json"
TABLE = ROOT / "data" / "能力对照表.csv"


def ann_time(ann: dict) -> str:
    """标注时间转 `MM-DD HH:MM`；解析不了就用当前时间。"""
    raw = ann.get("created_at", "")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%m-%d %H:%M")
    except ValueError:
        return time.strftime("%m-%d %H:%M")


def extract(ann: dict) -> tuple[str, str]:
    """取 choices 标签与 textarea 理由。"""
    tag, text = "", ""
    for r in ann.get("result") or []:
        if r.get("type") == "choices" and r.get("value", {}).get("choices"):
            tag = r["value"]["choices"][0]
        elif r.get("type") == "textarea" and r.get("value", {}).get("text"):
            text = "\n".join(r["value"]["text"])
    return tag, text


def advance_table(folder: str, tag: str, text: str, rows: list[list[str]]) -> str:
    """对照表推进。返回 'advanced' / 'refused' / 'skipped'（条目不在表中）。"""
    for i, row in enumerate(rows):
        if row[0] != folder:
            continue
        level, status = row[4], row[5]
        if level == "L2":
            return "refused"
        if tag == "有异议":
            if status == "已证伪":
                return "advanced"
            rows[i][5] = "已证伪"
            return "advanced"
        if text and level == "L0":
            rows[i][4] = "L1"
            return "advanced"
        if text and level == "L1":
            return "advanced"
        return "skipped"
    return "skipped"


def write_csv(path: Path, rows: list[list[str]]) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(assess.HEADER)
        w.writerows(rows)
    tmp.replace(path)


def merge(exported: list, queue: dict, rows: list[list[str]]) -> tuple[int, int, int, list[str]]:
    """返回 (写回数, 推进数, 未匹配数, 拒绝清单)。"""
    feedback = queue.setdefault("state", {}).setdefault("feedback", {})
    titles = {t["title"] for t in queue["tasks"]}
    done = advanced = unmatched = 0
    refused: list[str] = []
    for task in exported:
        title = (task.get("data") or {}).get("title")
        if title not in titles:
            unmatched += 1
            continue
        anns = task.get("annotations") or []
        if not anns:
            continue
        tag, text = extract(anns[-1])
        if not tag and not text:
            continue
        entry = feedback.setdefault(title, {})
        if entry.get("tag") == tag and entry.get("text") == text:
            continue  # 幂等：同标注不重复记账
        entry["tag"] = tag
        entry["text"] = text
        entry.setdefault("history", []).append(
            {"time": ann_time(anns[-1]), "tag": tag, "text": text})
        done += 1
        folder = next(t["folder"] for t in queue["tasks"] if t["title"] == title)
        result = advance_table(folder, tag, text, rows)
        if result == "advanced":
            advanced += 1
        elif result == "refused":
            refused.append(f"{folder}：L2 实测结论不被标注覆盖，已跳过")
    return done, advanced, unmatched, refused


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        sys.exit("需要 Label Studio 导出的 JSON 路径")
    exported = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    queue = json.loads(QUEUE.read_text(encoding="utf-8"))
    rows = assess.read_csv(TABLE)
    done, advanced, unmatched, refused = merge(exported, queue, rows)
    if done:
        tmp = QUEUE.with_suffix(".tmp")
        tmp.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(QUEUE)
        write_csv(TABLE, rows)
    if unmatched:
        print(f"警告：{unmatched} 条标注的 title 不在裁决队列中，已跳过")
    for msg in refused:
        print(f"拒绝：{msg}")
    print(f"写回 {done} 条，对照表推进 {advanced} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
