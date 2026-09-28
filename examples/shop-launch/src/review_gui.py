#!/usr/bin/env python3
"""判例复核 GUI：标注 AI 的实质决策与它暴露的隐含准则。

用法
    python3 src/review_gui.py          # 启动 GUI
    python3 src/review_gui.py check    # 两表校验（互链、枚举、必填），违规退出码 1
    python3 src/review_gui.py sync     # 处置=采纳 的准则 → docs/决策准则.md（幂等重写）

要标注的两类信息（AGENTS.md「人机对齐」）
    决策判例 data/决策判例.csv —— AI 按知识库做出的开店决策；标 维持 / 改判（新内容+理由）
    准则候选 data/准则候选.csv —— 决策暴露的隐含准则（成文法候选）；标 采纳 / 否决（+理由）
        采纳即渲染进 docs/决策准则.md（成文法·业务）

规则：保存前全量校验（含两表互链），不通过拒绝写盘；改判理由自动追加
data/纠偏记录.md；改判是终审（元评估表的重放不覆盖已复核判例）。
数据层与界面分离，无图形环境可跑测试；无第三方依赖（tkinter 内置）。
"""
from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DECISIONS = ROOT / "data" / "决策判例.csv"
CANON_CAND = ROOT / "data" / "准则候选.csv"
CANON = ROOT / "docs" / "决策准则.md"
LOG = ROOT / "data" / "纠偏记录.md"

DHEADER = ["ID", "决策", "内容", "依据材料", "暴露准则", "证据等级", "复核", "理由"]
GHEADER = ["ID", "准则", "说明", "来源决策", "处置", "理由"]
VERDICTS = ("维持", "改判")
DISPOSITIONS = ("候选", "采纳", "否决")
LEVELS = ("L0", "L1", "L2")


# ── 表读写 ──────────────────────────────────────────────

def load_table(path: Path, header: list[str]) -> list[list[str]]:
    with open(path, encoding="utf-8", newline="") as f:
        rows = [r for r in csv.reader(f) if r]
    if not rows or rows[0] != header:
        sys.exit(f"表头不符（{path}），应为：{','.join(header)}")
    return rows[1:]


def save_table(path: Path, header: list[str], rows: list[list[str]]) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    tmp.replace(path)


def _refs(text: str, prefix: str) -> set[str]:
    return {t.strip() for t in text.replace("；", ";").split(";") if t.strip().startswith(prefix)}


def validate_all(d_rows: list[list[str]], g_rows: list[list[str]]) -> list[str]:
    """两表全量校验：结构、枚举、必填、互链。返回违规清单（空=通过）。"""
    bad: list[str] = []
    d_ids, g_ids = set(), set()

    for i, r in enumerate(d_rows, start=2):
        if len(r) != len(DHEADER):
            bad.append(f"决策判例.csv:{i} 字段数错误：{r}")
            continue
        did, _name, content, _src, exposed, level, review, reason = r
        if not did or not content.strip():
            bad.append(f"决策判例.csv:{i} ID/内容不得为空")
            continue
        if did in d_ids:
            bad.append(f"决策判例.csv:{i} ID 重复：{did}")
        d_ids.add(did)
        if level not in LEVELS:
            bad.append(f"决策判例.csv:{i} [{did}] 证据等级非法：{level}")
        if review not in VERDICTS + ("未复核",):
            bad.append(f"决策判例.csv:{i} [{did}] 复核非法：{review}")
        if review == "改判" and not reason.strip():
            bad.append(f"决策判例.csv:{i} [{did}] 改判必须填理由")

    for i, r in enumerate(g_rows, start=2):
        if len(r) != len(GHEADER):
            bad.append(f"准则候选.csv:{i} 字段数错误：{r}")
            continue
        gid, _rule, _note, src, disp, reason = r
        if not gid or not _rule.strip():
            bad.append(f"准则候选.csv:{i} ID/准则不得为空")
            continue
        if gid in g_ids:
            bad.append(f"准则候选.csv:{i} ID 重复：{gid}")
        g_ids.add(gid)
        if disp not in DISPOSITIONS:
            bad.append(f"准则候选.csv:{i} [{gid}] 处置非法：{disp}")
        if disp == "采纳" and not reason.strip():
            bad.append(f"准则候选.csv:{i} [{gid}] 采纳必须填理由")

    # 互链：决策暴露的准则、准则的来源决策都必须存在
    for i, r in enumerate(d_rows, start=2):
        if len(r) == len(DHEADER):
            missing = _refs(r[4], "G") - g_ids
            if missing:
                bad.append(f"决策判例.csv:{i} [{r[0]}] 暴露准则悬空：{sorted(missing)}")
    for i, r in enumerate(g_rows, start=2):
        if len(r) == len(GHEADER):
            missing = _refs(r[3], "D") - d_ids
            if missing:
                bad.append(f"准则候选.csv:{i} [{r[0]}] 来源决策悬空：{sorted(missing)}")
    return bad


# ── 标注 ────────────────────────────────────────────────

def find(rows: list[list[str]], key: str) -> list[str] | None:
    return next((r for r in rows if r[0] == key), None)


def apply_decision(rows: list[list[str]], did: str, verdict: str,
                   content: str = "", reason: str = "") -> tuple[list, str]:
    """决策复核：维持（值不变）/ 改判（必填新内容与理由）。"""
    if verdict not in VERDICTS:
        raise ValueError(f"复核取值只能是 {'/'.join(VERDICTS)}：{verdict}")
    row = find(rows, did)
    if row is None:
        raise ValueError(f"决策不存在：{did}")
    if verdict == "维持":
        row[6] = "维持"
        return rows, f"{did}：维持（决策内容不变）"
    if not content.strip():
        raise ValueError("改判必须给新内容")
    if not reason.strip():
        raise ValueError("改判必须给理由（记入纠偏记录）")
    row[2] = content.strip()
    row[6] = "改判"
    row[7] = reason.strip()
    return rows, f"{did}：改判"


def apply_canon(rows: list[list[str]], gid: str, disposition: str,
                reason: str = "") -> tuple[list, str]:
    """准则处置：候选（未标）/ 采纳（升格成文法，必填理由）/ 否决（必填理由）。"""
    if disposition not in DISPOSITIONS:
        raise ValueError(f"处置取值只能是 {'/'.join(DISPOSITIONS)}：{disposition}")
    row = find(rows, gid)
    if row is None:
        raise ValueError(f"准则不存在：{gid}")
    if disposition in ("采纳", "否决") and not reason.strip():
        raise ValueError(f"{disposition}必须给理由")
    row[4] = disposition
    row[5] = reason.strip()
    return rows, f"{gid}：{disposition}"


# ── 成文法渲染与写盘 ─────────────────────────────────────

def render_canon(g_rows: list[list[str]]) -> str:
    lines = [
        "# 开店决策准则（成文法·业务）", "",
        "> 由 `data/准则候选.csv` 中处置=采纳的条目自动渲染（`python3 src/review_gui.py sync`），"
        "幂等重写，勿手改；改准则回准则候选表。", "",
    ]
    adopted = [r for r in g_rows if r[4] == "采纳"]
    if not adopted:
        lines += ["_尚无采纳条目——在 GUI 或 CSV 中把候选标为「采纳」后运行 sync。_", ""]
    else:
        lines += [f"共 {len(adopted)} 条。", ""]
        for r in adopted:
            gid, rule, note, src, _disp, reason = r
            lines += [f"## {gid} {rule}", "", note, "",
                      f"- 来源决策：{src.replace(';', '、')}",
                      f"- 采纳理由：{reason}", ""]
    return "\n".join(lines)


def sync_canon(g_rows: list[list[str]]) -> str:
    """渲染采纳准则到 docs/决策准则.md，返回写入的行数变化说明。"""
    new = render_canon(g_rows)
    old = CANON.read_text(encoding="utf-8") if CANON.exists() else ""
    CANON.write_text(new, encoding="utf-8")
    return f"docs/决策准则.md 已同步（采纳 {sum(1 for r in g_rows if r[4] == '采纳')} 条）"


def append_log(layer: str, obj: str, change: str, reason: str,
               path: Path = LOG) -> None:
    line = f"| {date.today().isoformat()} | {layer} | {obj} | {change} | 个例 | 1 | {reason} |\n"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if ln.startswith("|------")), None)
    if start is None:
        raise ValueError(f"纠偏记录表格结构不符：{path}")
    i = start + 1
    while i < len(lines) and lines[i].startswith("|"):
        i += 1
    lines.insert(i, line)
    path.write_text("".join(lines), encoding="utf-8")


def commit(kind: str, key: str, value: str, extra: str = "", reason: str = "",
           log: Path | None = None) -> str:
    """kind='decision' | 'canon'。标注 → 全量校验 → 写盘 → 记纠偏 → 准则变动同步渲染。

    log 运行时解析（不用默认参数），保证测试可替换路径。"""
    log = log or LOG
    if kind == "decision":
        d_rows, g_rows = load_table(DECISIONS, DHEADER), load_table(CANON_CAND, GHEADER)
        d_rows, msg = apply_decision(d_rows, key, value, extra, reason)
        bad = validate_all(d_rows, g_rows)
        if bad:
            raise ValueError("校验未过，拒绝写盘：\n" + "\n".join(bad))
        save_table(DECISIONS, DHEADER, d_rows)
        if value == "改判":
            append_log("判例", key, "改判决策", reason, log)
        return msg

    d_rows, g_rows = load_table(DECISIONS, DHEADER), load_table(CANON_CAND, GHEADER)
    g_rows, msg = apply_canon(g_rows, key, value, reason)
    bad = validate_all(d_rows, g_rows)
    if bad:
        raise ValueError("校验未过，拒绝写盘：\n" + "\n".join(bad))
    save_table(CANON_CAND, GHEADER, g_rows)
    if value in ("采纳", "否决"):
        append_log("成文法", key, value, reason, log)
    return msg + "；" + sync_canon(g_rows)


# ── 界面 ────────────────────────────────────────────────

def launch() -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("判例复核 — AI 决策与隐含准则")
    root.geometry("1080x600")
    status = tk.StringVar(value="左侧选中标注对象：决策标维持/改判，准则标采纳/否决")

    tree = ttk.Treeview(root, show="tree", selectmode="browse")
    tree.grid(row=0, column=0, rowspan=4, sticky="nswe", padx=8, pady=8)

    right = ttk.Frame(root)
    right.grid(row=0, column=1, sticky="nswe", padx=8, pady=8)

    title = tk.StringVar(value="未选择")
    ttk.Label(right, textvariable=title, font=("", 11, "bold")).pack(anchor="w")
    body = tk.Text(right, width=74, height=10, wrap="word", state="disabled",
                   background="#f4f4f4")
    body.pack(fill="x", pady=4)

    form = ttk.Frame(right)
    form.pack(fill="x")
    choice = tk.StringVar(value="维持")
    radio_frame = ttk.Frame(form)
    radio_frame.pack(side="left")
    label = ttk.Label(form, text="")
    label.pack(side="left", padx=8)
    value_box = tk.Text(right, width=74, height=4, wrap="word")
    value_box.pack(fill="x", pady=2)
    ttk.Label(right, text="理由（必填项见提示，记入纠偏记录）").pack(anchor="w")
    reason_box = tk.Text(right, width=74, height=3, wrap="word")
    reason_box.pack(fill="x", pady=2)

    state = {"key": None, "kind": None}

    def radios(options):
        for child in radio_frame.winfo_children():
            child.destroy()
        choice.set(options[0])
        for opt in options:
            ttk.Radiobutton(radio_frame, text=opt, variable=choice, value=opt).pack(side="left")

    def refresh():
        d_rows = load_table(DECISIONS, DHEADER)
        g_rows = load_table(CANON_CAND, GHEADER)
        tree.delete(*tree.get_children())
        p1 = tree.insert("", "end", text=f"决策判例（{len(d_rows)}）", open=True)
        for r in d_rows:
            tree.insert(p1, "end", iid="d:" + r[0], text=f"{r[0]} {r[1]}")
        p2 = tree.insert("", "end", text=f"隐含准则（{len(g_rows)}）", open=True)
        for r in g_rows:
            tree.insert(p2, "end", iid="g:" + r[0], text=f"{r[0]} {r[1]}")
        return d_rows, g_rows

    d_rows, g_rows = refresh()

    def show_body(lines):
        body.configure(state="normal")
        body.delete("1.0", "end")
        body.insert("end", "\n".join(lines))
        body.configure(state="disabled")

    def on_select(_e):
        sel = tree.selection()
        if not sel or ":" not in sel[0]:
            return
        kind, key = sel[0].split(":", 1)
        state.update(kind=kind, key=key)
        value_box.delete("1.0", "end")
        reason_box.delete("1.0", "end")
        if kind == "d":
            r = find(d_rows, key)
            title.set(f"{r[0]} · {r[1]}    [{r[5]} · 复核：{r[6]}]")
            show_body([f"内容：{r[2]}", f"依据材料：{r[3]}",
                       f"暴露准则：{r[4] or '—'}", f"理由：{r[7] or '—'}"])
            radios(list(VERDICTS))
            label.config(text="改判填新内容 →")
            choice.set(r[6] if r[6] in VERDICTS else "维持")
        else:
            r = find(g_rows, key)
            title.set(f"{r[0]} · {r[1]}    [{r[4]}]")
            show_body([f"说明：{r[2]}", f"来源决策：{r[3]}", f"理由：{r[5] or '—'}"])
            radios(list(DISPOSITIONS))
            label.config(text="处置理由 →")
            choice.set(r[4] if r[4] in DISPOSITIONS else "候选")

    def save():
        if not state["key"]:
            messagebox.showwarning("未选择", "先在左侧选一条")
            return
        extra = value_box.get("1.0", "end").strip()
        reason = reason_box.get("1.0", "end").strip()
        try:
            if state["kind"] == "d":
                msg = commit("decision", state["key"], choice.get(), extra, reason)
            else:
                msg = commit("canon", state["key"], choice.get(), "", reason)
        except ValueError as exc:
            messagebox.showerror("拒绝写盘", str(exc))
            status.set("保存失败（见提示）")
            return
        d_rows, g_rows = refresh()
        on_select(None)
        status.set(f"已保存：{msg}")

    btns = ttk.Frame(right)
    btns.pack(fill="x", pady=6)
    ttk.Button(btns, text="保存标注", command=save).pack(side="left")
    ttk.Label(btns, text="保存前全量校验（含两表互链）；改判终审",
              foreground="#888").pack(side="left", padx=10)

    root.rowconfigure(0, weight=1)
    root.columnconfigure(1, weight=1)
    tree.bind("<<TreeviewSelect>>", on_select)
    root.mainloop()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["sync"]:
        print(sync_canon(load_table(CANON_CAND, GHEADER)))
        return 0
    if argv == ["check"]:
        bad = validate_all(load_table(DECISIONS, DHEADER), load_table(CANON_CAND, GHEADER))
        if bad:
            print("\n".join(bad), file=sys.stderr)
            return 1
        print("判例/准则两表校验通过")
        return 0
    if argv:
        sys.exit("用法：review_gui.py [check|sync]")
    launch()
    return 0


if __name__ == "__main__":
    sys.exit(main())
