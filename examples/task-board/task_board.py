#!/usr/bin/env python3
"""任务评审看板 — 读取 data/write/*.json，一次展示一张任务卡，评审意见写回 json。

用法：
    python3 src/task_board.py [json路径]
默认打开 data/write/ 最新一份。分类严格取自数据中的 folder 字段（仓库文件夹名）。
每次按键、点选即写盘，关闭窗口前再保存一次。
"""

import json
import sys
import time
from pathlib import Path

import tkinter as tk
from tkinter import ttk

TAGS = ["先做", "缓做", "不做", "有异议"]
TYPE_COLORS = {
    "补写": "#5e8a5e", "定稿": "#4a6fa5", "取舍": "#b0684f", "扩写": "#4a6fa5",
    "归位": "#7d6ba0", "提炼": "#a58a3f", "讨论": "#8a857d", "对齐": "#5f7d8a",
}
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "data" / "write"


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


class App(tk.Tk):
    def __init__(self, path: Path):
        super().__init__()
        self.title("任务评审 — 量潮创始人实验室")
        self.geometry("640x560")
        self.path = path
        self.data = load(path)
        self.state = self.data.setdefault("state", {"feedback": {}})
        self.folder = "全部"
        self.pos = 0  # 当前任务在可见列表中的位置
        head = tk.Frame(self, bg="#f7f6f3")
        head.pack(fill="x", padx=18, pady=(14, 0))
        tk.Label(head, text=self.data["title"], bg="#f7f6f3",
                 font=("", 14, "bold")).pack(anchor="w")
        tk.Label(head, text=self.data["sub"], bg="#f7f6f3",
                 fg="#8a857d", font=("", 9), wraplength=600, justify="left").pack(anchor="w")
        self.folder_frame = tk.Frame(self, bg="#f7f6f3")
        self.folder_frame.pack(fill="x", padx=18, pady=(10, 2))
        self.card_holder = tk.Frame(self, bg="#f7f6f3")
        self.card_holder.pack(fill="both", expand=True, padx=18, pady=6)
        nav = tk.Frame(self, bg="#f7f6f3")
        nav.pack(fill="x", padx=18, pady=(0, 4))
        tk.Button(nav, text="← 上一个", relief="flat", bg="white", font=("", 10),
                  command=self.prev).pack(side="left")
        self.pos_label = tk.Label(nav, text="", bg="#f7f6f3", fg="#8a857d", font=("", 10))
        self.pos_label.pack(side="left", expand=True)
        tk.Button(nav, text="下一个 →", relief="flat", bg="white", font=("", 10),
                  command=self.next).pack(side="right")
        bar = tk.Frame(self, bg="white", bd=1, relief="solid")
        bar.pack(fill="x", side="bottom")
        self.status = tk.Label(bar, text="", bg="white", fg="#2d2a26", font=("", 10), anchor="w")
        self.status.pack(side="left", padx=16, pady=8)
        self.bind("<Left>", lambda e: self.prev())
        self.bind("<Right>", lambda e: self.next())
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.render_folders()
        self.render_card()

    # ---- 数据 ----
    def visible(self):
        tasks = self.data["tasks"]
        if self.folder == "全部":
            return list(enumerate(tasks))
        return [(i, t) for i, t in enumerate(tasks) if t["folder"] == self.folder]

    def persist(self):
        prune_feedback(self.state)
        save(self.path, self.data)
        self.status.config(text=f"已保存 {time.strftime('%H:%M:%S')} · 意见 {len(self.state['feedback'])} 条")

    def on_close(self):
        self.persist()
        self.destroy()

    # ---- 渲染 ----
    def render_folders(self):
        for w in self.folder_frame.winfo_children():
            w.destroy()
        folders = ["全部"] + sorted({t["folder"] for t in self.data["tasks"]})
        tk.Label(self.folder_frame, text="分类", bg="#f7f6f3", fg="#8a857d",
                 font=("", 9)).pack(side="left", padx=(0, 8))
        for f in folders:
            tk.Button(self.folder_frame, text=f, relief="flat",
                      bg="#2d2a26" if f == self.folder else "white",
                      fg="white" if f == self.folder else "#8a857d",
                      font=("", 9), padx=10, pady=1,
                      command=lambda f=f: self.set_folder(f)).pack(side="left", padx=(0, 6))

    def render_card(self):
        for w in self.card_holder.winfo_children():
            w.destroy()
        vis = self.visible()
        if not vis:
            self.pos_label.config(text="该分类下暂无任务")
            return
        self.pos = min(self.pos, len(vis) - 1)
        i, task = vis[self.pos]
        self.pos_label.config(text=f"{self.pos + 1} / {len(vis)} · {task['folder']}")
        TaskCard(self.card_holder, self, i, task).pack(fill="both", expand=True)

    def set_folder(self, f):
        self.folder = f
        self.pos = 0
        self.render_folders()
        self.render_card()

    def prev(self):
        self.pos = max(0, self.pos - 1)
        self.render_card()

    def next(self):
        self.pos = min(len(self.visible()) - 1, self.pos + 1)
        self.render_card()


class TaskCard(tk.Frame):
    def __init__(self, master, app, index, task):
        super().__init__(master, bg="white", bd=1, relief="solid", padx=18, pady=14)
        self.app, self.index, self.task = app, index, task
        color = TYPE_COLORS.get(task["type"], "#8a857d")
        head = tk.Frame(self, bg="white")
        head.pack(fill="x")
        tk.Label(head, text=task["type"], bg=color, fg="white",
                 font=("", 10), padx=10, pady=1).pack(side="left")
        if task.get("rec"):
            tk.Label(head, text="推荐", bg="#eef2f8", fg="#4a6fa5",
                     font=("", 10, "bold"), padx=8, pady=1).pack(side="left", padx=(6, 0))
        tk.Label(self, text=task["title"], bg="white", fg="#2d2a26",
                 font=("", 14, "bold"), wraplength=540, justify="left").pack(anchor="w", pady=(10, 0))
        tk.Label(self, text="建议 " + task["hint"], bg="white", fg="#57524a",
                 font=("", 11), wraplength=540, justify="left").pack(anchor="w", pady=(6, 0))
        tk.Label(self, text=task["meta"], bg="white", fg="#8a857d",
                 font=("", 9), wraplength=540, justify="left").pack(anchor="w", pady=(6, 0))
        controls = tk.Frame(self, bg="white")
        controls.pack(fill="x", pady=(14, 0))
        tk.Label(controls, text="标注", bg="white", fg="#8a857d", font=("", 9)).pack(side="left")
        fb = app.state["feedback"].get(task["title"], {})
        self.tag_vars = {}
        for g in TAGS:
            var = tk.BooleanVar(value=fb.get("tag") == g)
            tk.Checkbutton(controls, text=g, variable=var, bg="white", fg="#57524a",
                           activebackground="white", selectcolor="white",
                           font=("", 9), indicatoron=False, padx=5, pady=1,
                           command=lambda v=var, g=g: self.on_tag(v, g)).pack(side="left", padx=(4, 0))
            self.tag_vars[g] = var
        tk.Label(self, text="详细理由（随输入自动保存，点「记录」存入历史）", bg="white",
                 fg="#8a857d", font=("", 9)).pack(anchor="w", pady=(10, 2))
        self.text = tk.Text(self, height=6, font=("", 10), bd=1, relief="solid", wrap="word")
        self.text.insert("1.0", fb.get("text", ""))
        self.text.pack(fill="both", expand=True)
        self.text.bind("<KeyRelease>", lambda e: self.on_text())
        self.text.bind("<FocusOut>", lambda e: self.on_text())
        foot = tk.Frame(self, bg="white")
        foot.pack(fill="x", pady=(6, 0))
        self.history_label = tk.Label(foot, text=self.history_text(fb), bg="white",
                                      fg="#8a857d", font=("", 9))
        self.history_label.pack(side="left")
        tk.Button(foot, text="保存并下一个 →", bg="#4a6fa5", fg="white", relief="flat",
                  font=("", 10, "bold"), padx=12, pady=2, command=self.on_save).pack(side="right")

    def history_text(self, fb):
        n = len(fb.get("history", []))
        return f"历史 {n} 条" if n else "暂无历史"

    def current_tag(self):
        for g, var in self.tag_vars.items():
            if var.get():
                return g
        return ""

    def on_tag(self, var, g):
        if var.get():
            for other, v in self.tag_vars.items():
                if other != g:
                    v.set(False)
        self.app.state["feedback"].setdefault(self.task["title"], {})["tag"] = self.current_tag()
        self.app.persist()

    def on_text(self):
        self.app.state["feedback"].setdefault(self.task["title"], {})["text"] = self.text.get("1.0", "end").strip()
        self.app.persist()

    def on_save(self):
        tag, text = self.current_tag(), self.text.get("1.0", "end").strip()
        if tag or text:
            entry = self.app.state["feedback"].setdefault(self.task["title"], {})
            record(entry, tag, text, time.strftime("%m-%d %H:%M"))
        self.app.persist()
        self.app.next()


def main():
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        files = sorted(DEFAULT_DIR.glob("*.json"))
        if not files:
            sys.exit(f"未找到数据文件：{DEFAULT_DIR}/*.json")
        path = files[-1]
    App(path).mainloop()


if __name__ == "__main__":
    main()
