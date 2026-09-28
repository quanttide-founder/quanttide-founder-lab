#!/usr/bin/env python3
"""判例复核 GUI：人在本地标注 维持/改判，写回对照表并追加纠偏记录。

用法
    python3 src/review_gui.py

标注规则（AGENTS.md「人机对齐」判例层）
    维持 —— 判决值不变，复核列置「维持」
    改判 —— 必填新分数与理由；依据法条写改判出处；理由追加进 data/纠偏记录.md
    保存前跑一致性检查（assess.validate_rows），不通过拒绝写盘；
    改判是终审，--seed 重放不覆盖（assess.preserve_reviewed）。

数据层（apply_review / save_rows / append_log / commit）与界面分离，
无图形环境可跑测试；无第三方依赖（tkinter 内置）。
"""
from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

import assess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TABLE = ROOT / "data" / "能力对照表.csv"
LOG = ROOT / "data" / "纠偏记录.md"
VERDICTS = ("维持", "改判")


def find_row(rows: list[list[str]], name: str) -> list[str] | None:
    return next((r for r in rows if r[0] == name), None)


def apply_review(rows: list[list[str]], name: str, verdict: str,
                 score: str = "", basis: str = "", reason: str = "") -> tuple[list, str]:
    """对一行施加复核标注，返回 (新行集, 说明)。非法输入抛 ValueError。"""
    if verdict not in VERDICTS:
        raise ValueError(f"复核取值只能是 {'/'.join(VERDICTS)}：{verdict}")
    row = find_row(rows, name)
    if row is None:
        raise ValueError(f"判例不存在：{name}")

    if verdict == "维持":
        row[7] = "维持"
        return rows, f"{name}：维持（判决值不变）"

    # 改判：必填新分数与理由
    if not score.strip():
        raise ValueError("改判必须给新分数")
    if not reason.strip():
        raise ValueError("改判必须给理由（记入纠偏记录）")
    try:
        s = int(score)
    except ValueError as exc:
        raise ValueError(f"分数须为整数：{score}") from exc
    if not 0 <= s <= 100:
        raise ValueError(f"分数须在 0–100：{s}")
    old = row[1]
    row[1] = str(s)
    row[6] = basis.strip() or f"改判·{date.today().isoformat()}"
    row[7] = "改判"
    return rows, f"{name}：改判 {old} → {s}"


def save_rows(rows: list[list[str]], path: Path = TABLE) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(assess.HEADER)
        w.writerows(rows)
    tmp.replace(path)


def append_log(name: str, change: str, reason: str, path: Path = LOG) -> None:
    """向 data/纠偏记录.md 表格追加一行（判例层：分诊=个例，影响行数=1）。"""
    line = f"| {date.today().isoformat()} | 判例 | {name} | {change} | 个例 | 1 | {reason} |\n"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if ln.startswith("|------")), None)
    if start is None:
        raise ValueError(f"纠偏记录表格结构不符：{path}")
    i = start + 1
    while i < len(lines) and lines[i].startswith("|"):
        i += 1  # 扫到表格末尾，新行追加在最后
    lines.insert(i, line)
    path.write_text("".join(lines), encoding="utf-8")


def commit(rows: list[list[str]], name: str, verdict: str, score: str = "",
           basis: str = "", reason: str = "", table: Path = TABLE,
           log: Path = LOG) -> str:
    """标注 → 一致性检查 → 写盘 → 改判记纠偏记录。任一校验失败不落盘。"""
    rows, msg = apply_review(rows, name, verdict, score, basis, reason)
    bad = assess.validate_rows(rows, str(table))
    if bad:
        raise ValueError("一致性检查未过，拒绝写盘：\n" + "\n".join(bad))
    save_rows(rows, table)
    if verdict == "改判":
        append_log(name, msg.split("：", 1)[-1], reason, log)
    return msg


def launch() -> None:
    """tkinter 界面：左判例列表，右复核表单，底部状态栏。"""
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("判例复核 — 开店助手")
    root.geometry("960x540")

    status = tk.StringVar(value="选中左侧判例开始复核")

    def load() -> list[list[str]]:
        return assess.read_csv(TABLE)

    # 左：判例树
    cols = ("score", "dep", "level", "status", "review")
    tree = ttk.Treeview(root, columns=cols, show="tree headings", selectmode="browse")
    for col, text, width in (("#0", "环节", 130), ("score", "分数", 50),
                             ("dep", "依赖", 70), ("level", "证据", 50),
                             ("status", "验证状态", 80), ("review", "复核", 70)):
        tree.heading(col, text=text)
        tree.column(col, width=width, anchor="center" if col != "#0" else "w")
    tree.grid(row=0, column=0, rowspan=4, sticky="nswe", padx=8, pady=8)

    # 右：详情与表单
    right = ttk.Frame(root)
    right.grid(row=0, column=1, sticky="nswe", padx=8, pady=8)
    detail = tk.Text(right, width=58, height=6, wrap="word", state="disabled",
                     background="#f4f4f4")
    detail.pack(fill="x")

    form = ttk.Frame(right)
    form.pack(fill="x", pady=6)
    verdict = tk.StringVar(value="维持")
    ttk.Radiobutton(form, text="维持", variable=verdict, value="维持").grid(row=0, column=0)
    ttk.Radiobutton(form, text="改判", variable=verdict, value="改判").grid(row=0, column=1, padx=8)

    ttk.Label(form, text="新分数").grid(row=0, column=2)
    score_var = tk.StringVar()
    ttk.Entry(form, textvariable=score_var, width=5).grid(row=0, column=3, padx=4)
    ttk.Label(form, text="依据法条").grid(row=0, column=4)
    basis_var = tk.StringVar()
    ttk.Entry(form, textvariable=basis_var, width=34).grid(row=0, column=5, padx=4)

    ttk.Label(right, text="理由（改判必填，记入纠偏记录）").pack(anchor="w")
    reason_box = tk.Text(right, width=58, height=5, wrap="word")
    reason_box.pack(fill="x")

    rows = load()
    current = {"name": None}

    def refresh(keep: str | None = None) -> None:
        nonlocal rows
        rows = load()
        tree.delete(*tree.get_children())
        for r in rows:
            tree.insert("", "end", iid=r[0], text=r[0],
                        values=(r[1], r[3], r[4], r[5], r[7]))
        if keep and tree.exists(keep):
            tree.selection_set(keep)

    def on_select(_event) -> None:
        sel = tree.selection()
        if not sel:
            return
        name = sel[0]
        current["name"] = name
        r = find_row(rows, name)
        detail.configure(state="normal")
        detail.delete("1.0", "end")
        detail.insert("end", f"局限：{r[2]}\n依据法条：{r[6]}")
        detail.configure(state="disabled")
        verdict.set("维持")
        score_var.set(r[1])
        basis_var.set(r[6])
        reason_box.delete("1.0", "end")
        status.set(f"当前判例：{name}（复核：{r[7]}）")

    def save() -> None:
        name = current["name"]
        if not name:
            messagebox.showwarning("未选择", "先在左侧选一个判例")
            return
        try:
            msg = commit(rows, name, verdict.get(), score_var.get(),
                         basis_var.get(), reason_box.get("1.0", "end").strip())
        except ValueError as exc:
            messagebox.showerror("拒绝写盘", str(exc))
            status.set("保存失败（见提示）")
            return
        refresh(keep=name)
        status.set(f"已保存：{msg}")

    btns = ttk.Frame(right)
    btns.pack(fill="x", pady=6)
    ttk.Button(btns, text="保存标注", command=save).pack(side="left")
    ttk.Label(btns, text="保存即过一致性检查；改判终审，重放不覆盖",
              foreground="#888").pack(side="left", padx=10)

    root.rowconfigure(0, weight=1)
    root.columnconfigure(1, weight=1)
    tree.bind("<<TreeviewSelect>>", on_select)
    refresh()
    root.mainloop()


def main(argv: list[str] | None = None) -> int:
    launch()
    return 0


if __name__ == "__main__":
    sys.exit(main())
