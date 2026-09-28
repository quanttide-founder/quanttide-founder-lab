#!/usr/bin/env python3
"""任务评审看板 → Label Studio 标注往返。

用法（推荐走 planner.py，亦可单独运行本文件）：
    python3 examples/novel-planner/src/planner.py export [json路径] [--out 任务清单.json]
    python3 examples/novel-planner/src/planner.py merge <label-studio导出.json> [json路径]

export：把 data/ 下最新一份任务扫描（文件名含「任务扫描」）连同已有意见导出为 Label Studio 可导入的任务清单。
merge ：读取 Label Studio 导出的标注，按 title 写回 state.feedback（追加历史，重复导入不重复记账）。

标注配置见同目录 label-config.xml：建项目时导入它，标签固定四枚。
中间数据一律 JSON，程序不碰文档。
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

TAGS = ["先做", "缓做", "不做", "有异议"]
# 任务扫描与标注中间数据一律落项目根的 data/（代码在 src/，本目录可写，外部只读）
DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_OUT = DEFAULT_DIR / "label-studio" / "tasks.json"


def load(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def prune_feedback(state: dict) -> dict:
    """丢弃无标签、无文字、也无历史的空意见条目。"""
    state["feedback"] = {k: v for k, v in state["feedback"].items()
                         if v.get("tag") or v.get("text") or v.get("history")}
    return state


def record(entry: dict, tag: str, text: str, when: str) -> None:
    """把一次标注写入条目：更新当前值，并快照进历史。"""
    if tag:
        entry["tag"] = tag
    entry["text"] = text
    entry.setdefault("history", []).append({"time": when, "tag": tag or entry.get("tag", ""), "text": text})


# 任务扫描文件名模式：data/ 同时放检索与看板的中间 JSON，靠名字区分
TASK_GLOB = "*任务扫描*.json"


def latest_data() -> Path:
    files = sorted(DEFAULT_DIR.glob(TASK_GLOB))
    if not files:
        sys.exit(f"未找到数据文件：{DEFAULT_DIR}/{TASK_GLOB}")
    return files[-1]


def build_tasks(data: dict) -> list:
    """任务清单：每条任务一个 data 块，title 作回写主键。"""
    tasks = []
    for i, t in enumerate(data["tasks"], 1):
        fb = data.get("state", {}).get("feedback", {}).get(t["title"], {})
        tasks.append({
            "id": i,
            "data": {
                "title": t["title"],
                "type": t["type"],
                "folder": t["folder"],
                "rec": "推荐" if t.get("rec") else "",
                "hint": t["hint"],
                "meta": t.get("meta", ""),
                "tag": fb.get("tag", ""),
                "text": fb.get("text", ""),
                "project": data["title"],
                "sub": data.get("sub", ""),
            },
        })
    return tasks


def ann_time(ann: dict) -> str:
    """标注时间转 `MM-DD HH:MM`；解析不了就用当前时间。"""
    raw = ann.get("created_at", "")
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%m-%d %H:%M")
    except ValueError:
        return time.strftime("%m-%d %H:%M")


def apply_annotations(data: dict, exported: list, now: str = None) -> tuple:
    """把 Label Studio 导出的标注写回 feedback，返回 (写回数, 未匹配数)。

    按 title 对齐；只取每条任务最后一个标注；内容与当前值一致时不重复记账。
    """
    feedback = data.setdefault("state", {}).setdefault("feedback", {})
    titles = {t["title"] for t in data["tasks"]}
    done = unmatched = 0
    for task in exported:
        title = (task.get("data") or {}).get("title")
        if title not in titles:
            unmatched += 1
            continue
        anns = task.get("annotations") or []
        if not anns:
            continue
        ann = anns[-1]
        tag, text = "", ""
        for r in ann.get("result") or []:
            if r.get("type") == "choices" and r.get("value", {}).get("choices"):
                tag = r["value"]["choices"][0]
            elif r.get("type") == "textarea" and r.get("value", {}).get("text"):
                text = "\n".join(r["value"]["text"])
        if not tag and not text:
            continue
        entry = feedback.setdefault(title, {})
        if entry.get("tag") == tag and entry.get("text") == text:
            continue
        record(entry, tag, text, now or ann_time(ann))
        done += 1
    return done, unmatched


def cmd_export(argv: list) -> None:
    pos, out = _parse(argv, allow_out=True)
    src = Path(pos[0]) if pos else latest_data()
    out = out or DEFAULT_OUT
    tasks = build_tasks(load(src))
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)
    print(f"{len(tasks)} 个任务已写入 {out}")
    print("下一步：Label Studio 新建项目（导入同目录 label-config.xml），再导入本清单")


def cmd_merge(argv: list) -> None:
    pos, _ = _parse(argv, allow_out=False)
    if not pos:
        sys.exit("merge 需要 Label Studio 导出的 JSON")
    exp_path = Path(pos[0])
    data_path = Path(pos[1]) if len(pos) > 1 else latest_data()
    data = load(data_path)
    done, unmatched = apply_annotations(data, load(exp_path))
    if unmatched:
        print(f"警告：{unmatched} 条标注的 title 不在任务清单中，已跳过")
    if done:
        prune_feedback(data["state"])
        save(data_path, data)
    print(f"写回 {done} 条 → {data_path}")


def _parse(argv: list, allow_out: bool) -> tuple:
    pos, out, i = [], None, 0
    while i < len(argv):
        if argv[i] == "--out" and allow_out:
            i += 1
            if i >= len(argv):
                sys.exit("--out 缺值")
            out = Path(argv[i])
        else:
            pos.append(argv[i])
        i += 1
    return pos, out


def main(argv: list = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 1 or argv[0] not in ("export", "merge"):
        sys.exit(__doc__)
    cmd, rest = argv[0], argv[1:]
    {"export": cmd_export, "merge": cmd_merge}[cmd](rest)


if __name__ == "__main__":
    main()
