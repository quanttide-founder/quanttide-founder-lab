#!/usr/bin/env python3
"""算账工具 GUI：三层报表。

用法
    python3 src/ledger_gui.py

三层结构（与 `docs/ledger.md` 一致）
    第一层 结论    大字，一眼定生死：月利润 / 保本线 / 倍数
    第二层 账目    中字，可核查：收入侧与成本侧逐项拆解
    第三层 细算    折叠，想抠再看：签型比例、翻台与客流、固定成本、行业参考

与 CLI 的策略分歧（刻意为之，非疏漏）
    CLI  参数缺失 → 拒绝计算，退出码 2（脚本靠退出码判断，静默默认值危险）
    GUI  参数缺失 → 用 ESTIMATES 代填，一层加 [试算] 徽标，完整度同步下降
两边共用 `ledger.py` 的同一套公式与 GAPS 数据，不复制算术。
"""
from __future__ import annotations

import io
import sys
import contextlib
import tkinter as tk
from tkinter import ttk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger as L  # noqa: E402

# 标签配色：来源比数值更重要，先看来源再看数值
TAG_BG = {
    "锁": "#e8e8e8",      # 灰：实地校准，不可改
    "L0": "#fff3cd",      # 黄：假设
    "L1": "#f8d7da",      # 红：待回填
    "估算": "#ffe0b2",    # 橙：代填
    "推算": "#e3f2fd",    # 蓝：公式导出
    "锁+L0": "#e8e8e8",
}

DEFAULTS = {
    "mix_veg": "60", "mix_meat": "30", "mix_prem": "10",
    "daily": "", "waste": "",
    "ticket_lo": "", "ticket_hi": "", "traffic_lo": "", "traffic_hi": "",
    "rent": str(int(L.LOCK["rent_cap"])), "staff": "", "utility": "",
    "other_fixed": "", "food_rate": "",
}


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("iGuo 算账")
        self.minsize(760, 620)
        self.vars: dict[str, tk.StringVar] = {}
        self.open: dict[str, bool] = {"细算1": True, "细算2": False,
                                      "细算3": False, "细算4": False}
        self.mode = tk.StringVar(value="shop")
        self._build()
        self.mode.trace_add("write", lambda *_: self.refresh())
        for v in self.vars.values():
            v.trace_add("write", lambda *_: self.refresh())
        self.refresh()

    # ── 构建 ────────────────────────────────────────────
    def _build(self) -> None:
        self._header()
        self._layer1()
        self._layer2()
        self._layer3()
        self._footer()

    def _entry(self, parent, key: str, label: str, width: int = 9) -> tk.StringVar:
        """带标签的输入框。空 = 未回填。"""
        row = self.vars_row.setdefault(parent, 0)
        tk.Label(parent, text=label, anchor="w").grid(
            row=row, column=0, sticky="w", padx=(0, 6), pady=2)
        var = tk.StringVar(value=DEFAULTS.get(key, ""))
        self.vars[key] = var
        tk.Entry(parent, textvariable=var, width=width).grid(
            row=row, column=1, sticky="w", pady=2)
        self.vars_row[parent] = row + 1
        return var

    def _header(self) -> None:
        f = tk.Frame(self, pady=8, padx=12)
        f.pack(fill="x")
        tk.Label(f, text="模式", font=("", 11, "bold")).pack(side="left")
        for val, text in (("stall", "摆摊"), ("shop", "档口店")):
            tk.Radiobutton(f, text=text, value=val, variable=self.mode,
                           font=("", 11)).pack(side="left", padx=(6, 0))
        self.meter = tk.Label(f, text="", font=("", 11), anchor="e")
        self.meter.pack(side="right")

    def _layer1(self) -> None:
        f = tk.LabelFrame(self, text=" 第一层：结论 ", padx=14, pady=12)
        f.pack(fill="x", padx=12, pady=(0, 6))
        self.l1_rows: list[tk.Label] = []
        for _ in range(2):
            line = tk.Frame(f)
            line.pack(fill="x", pady=4)
            big = tk.Label(line, text="", font=("", 22, "bold"), width=16, anchor="w")
            big.pack(side="left")
            note = tk.Label(line, text="", font=("", 12), anchor="w")
            note.pack(side="left", padx=(12, 0))
            self.l1_rows.append((big, note))
        self.badge = tk.Label(f, text="", font=("", 10, "bold"),
                              fg="#b26a00", anchor="w")
        self.badge.pack(fill="x")

    def _layer2(self) -> None:
        box = tk.LabelFrame(self, text=" 第二层：账怎么算的 ", padx=14, pady=10)
        box.pack(fill="both", expand=False, padx=12, pady=(0, 6))
        self.side_cols: dict[str, tk.Frame] = {}
        for i, (side, title) in enumerate((("income", "收入侧"), ("cost", "成本侧"))):
            col = tk.Frame(box)
            col.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 24, 0))
            box.columnconfigure(i, weight=1)
            tk.Label(col, text=title, font=("", 11, "bold"),
                     anchor="w").pack(fill="x", pady=(0, 4))
            self.side_cols[side] = col

    def _layer3(self) -> None:
        box = tk.LabelFrame(self, text=" 第三层：细算 ", padx=14, pady=8)
        box.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.vars_row = {}
        self.groups: dict[str, tuple[tk.Frame, tk.Button]] = {}
        specs = [
            ("细算1", "签型比例", self._grp_mix),
            ("细算2", "翻台 / 客流", self._grp_traffic),
            ("细算3", "固定成本", self._grp_fixed),
            ("细算4", "行业参考", self._grp_industry),
        ]
        for i, (gid, title, build) in enumerate(specs):
            wrap = tk.Frame(box)
            wrap.pack(fill="x", pady=3)
            btn = tk.Button(wrap, text="▸ " + title, anchor="w", relief="flat",
                            command=lambda g=gid: self._toggle(g))
            btn.pack(fill="x")
            body = tk.Frame(wrap, padx=10, pady=4)
            build(body)
            self.groups[gid] = (body, btn)
            if not self.open[gid]:
                body.pack_forget()

    # ── 各折叠组 ────────────────────────────────────────
    def _grp_mix(self, p) -> None:
        tk.Label(p, text="[L0] 素:荤:招牌 比例", anchor="w").grid(
            row=0, column=0, sticky="w", padx=(0, 6), pady=2)
        for i, k in enumerate(("mix_veg", "mix_meat", "mix_prem")):
            var = tk.StringVar(value=DEFAULTS[k])
            self.vars[k] = var
            tk.Entry(p, textvariable=var, width=5).grid(row=0, column=1 + i, pady=2)
        tk.Label(p, text="探店带回荤签品类后必须更新", fg="#888",
                 anchor="w").grid(row=1, column=0, columnspan=5, sticky="w")

    def _grp_traffic(self, p) -> None:
        rows = [
            ("客单价 低/高", "ticket_lo", "ticket_hi", "元"),
            ("日客流 低/高", "traffic_lo", "traffic_hi", "人"),
        ]
        for r, (label, k1, k2, unit) in enumerate(rows):
            tk.Label(p, text=label, anchor="w").grid(
                row=r, column=0, sticky="w", padx=(0, 6), pady=2)
            for i, k in enumerate((k1, k2)):
                var = tk.StringVar(value=DEFAULTS[k])
                self.vars[k] = var
                tk.Entry(p, textvariable=var, width=7).grid(row=r, column=1 + i, pady=2)
            tk.Label(p, text=unit, fg="#888").grid(row=r, column=3, sticky="w")
        tk.Label(p, text="[L1] 摆摊实测日均签数", anchor="w").grid(
            row=2, column=0, sticky="w", padx=(0, 6), pady=2)
        var = tk.StringVar(value=DEFAULTS["daily"])
        self.vars["daily"] = var
        tk.Entry(p, textvariable=var, width=7).grid(row=2, column=1, pady=2)
        tk.Label(p, text="签", fg="#888").grid(row=2, column=2, sticky="w")

    def _grp_fixed(self, p) -> None:
        rows = [("房租 / 摊位", "rent", "锁，只读"), ("人工", "staff", "L1"),
                ("水电其他", "utility", "L1"), ("其他固定", "other_fixed", "L1")]
        for r, (label, k, tag) in enumerate(rows):
            tk.Label(p, text=label, anchor="w").grid(
                row=r, column=0, sticky="w", padx=(0, 6), pady=2)
            var = tk.StringVar(value=DEFAULTS[k])
            self.vars[k] = var
            ent = tk.Entry(p, textvariable=var, width=10,
                           state="readonly" if tag.startswith("锁") else "normal")
            ent.grid(row=r, column=1, pady=2)
            tk.Label(p, text=tag, fg="#888").grid(row=r, column=2, sticky="w", padx=(4, 0))
        tk.Label(p, text="元 / 月", fg="#888").grid(row=0, column=3, sticky="w", padx=(4, 0))

    def _grp_industry(self, p) -> None:
        tk.Label(p, text="食材成本率 %", anchor="w").grid(
            row=0, column=0, sticky="w", padx=(0, 6), pady=2)
        var = tk.StringVar(value=DEFAULTS["food_rate"])
        self.vars["food_rate"] = var
        tk.Entry(p, textvariable=var, width=8).grid(row=0, column=1, pady=2)
        tk.Label(p, text="留空按 35% 估算（行业 30–40%，未询价）",
                 fg="#888").grid(row=0, column=2, sticky="w", padx=(4, 0))
        tk.Label(p, text="实际损耗率 %", anchor="w").grid(
            row=1, column=0, sticky="w", padx=(0, 6), pady=2)
        var = tk.StringVar(value=DEFAULTS["waste"])
        self.vars["waste"] = var
        tk.Entry(p, textvariable=var, width=8).grid(row=1, column=1, pady=2)
        tk.Label(p, text="纪律上限 10%", fg="#888").grid(
            row=1, column=2, sticky="w", padx=(4, 0))

    def _footer(self) -> None:
        f = tk.Frame(self, pady=8, padx=12)
        f.pack(fill="x", side="bottom")
        tk.Button(f, text="重置", command=self._reset).pack(side="left")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = L.selftest()
        self.check = tk.Label(
            f, text=f"自检 {'PASS' if rc == 0 else 'FAIL'}",
            fg="#2e7d32" if rc == 0 else "#c62828", font=("", 10, "bold"))
        self.check.pack(side="left", padx=(12, 0))
        self.note = tk.Label(f, text="", fg="#666", anchor="e")
        self.note.pack(side="right", fill="x", expand=True)

    # ── 行为 ────────────────────────────────────────────
    def _toggle(self, gid: str) -> None:
        body, btn = self.groups[gid]
        title = btn.cget("text")[2:]
        if self.open[gid]:
            body.pack_forget()
            btn.config(text="▸ " + title)
        else:
            body.pack(fill="x")
            btn.config(text="▾ " + title)
        self.open[gid] = not self.open[gid]

    def _reset(self) -> None:
        for k, v in DEFAULTS.items():
            self.vars[k].set(v)
        self.mode.set("shop")

    def _num(self, key: str) -> float | None:
        t = self.vars[key].get().strip()
        if not t:
            return None
        try:
            return float(t)
        except ValueError:
            return None

    def _pair(self, k1: str, k2: str) -> tuple[float, float] | None:
        a, b = self._num(k1), self._num(k2)
        if a is None or b is None:
            return None
        return (a, b) if a <= b else (b, a)

    def _params(self, mode: str) -> dict:
        mix = (self._num("mix_veg") or 0, self._num("mix_meat") or 0,
               self._num("mix_prem") or 0)
        if sum(mix) <= 0:
            mix = L.DEFAULT_MIX
        p = {"mix": mix, "food_rate": None, "waste": None, "daily": None,
             "ticket": None, "traffic": None, "staff": None,
             "utility_other": None, "other_fixed": 0.0, "rent": None}
        if mode == "stall":
            p["daily"] = int(self._num("daily")) if self._num("daily") else None
            w = self._num("waste")
            p["waste"] = w / 100 if w is not None else None
        else:
            p["ticket"] = self._pair("ticket_lo", "ticket_hi")
            p["traffic"] = self._pair("traffic_lo", "traffic_hi")
            p["staff"] = self._num("staff")
            p["utility_other"] = self._num("utility")
            p["other_fixed"] = self._num("other_fixed") or 0.0
            p["rent"] = self._num("rent")
            fr = self._num("food_rate")
            p["food_rate"] = fr / 100 if fr is not None else None
        return p

    def _row(self, parent, label: str, value: str, tag: str) -> None:
        line = tk.Frame(parent)
        line.pack(fill="x", pady=1)
        tk.Label(line, text=label, anchor="w", width=14,
                 font=("", 10)).pack(side="left")
        tk.Label(line, text=value, anchor="w", font=("", 11, "bold")).pack(
            side="left", padx=(0, 8))
        bg = TAG_BG.get(tag, "#f5f5f5")
        tk.Label(line, text=tag, bg=bg, relief="flat", padx=5, pady=0,
                 font=("", 9)).pack(side="left")

    def refresh(self) -> None:
        mode = self.mode.get()
        res = L.present(mode, self._params(mode), allow_estimates=True)

        # 完整度
        pct = res["completeness"]
        blocks = round(pct * 5)
        bar = "▓" * blocks + "░" * (5 - blocks)
        self.meter.config(text=f"数据完整度 {bar} {pct * 100:.0f}%")

        # 第一层
        for i, row in enumerate(self.l1_rows):
            if i < len(res["layer1"]):
                label, value, note = res["layer1"][i]
                row[0].config(text=f"{label}  {value}")
                row[1].config(text=note)
            else:
                row[0].config(text="")
                row[1].config(text="")
        est = res["estimated"]
        if res["missing"] and not res["computable"]:
            self.badge.config(text="⚠ 部分 L1 未回填，结论待补全", fg="#c62828")
        elif est:
            self.badge.config(text=f"[试算] 含 {len(est)} 项估算值，完整度 {pct * 100:.0f}%",
                              fg="#b26a00")
        elif pct < 1:
            self.badge.config(text=f"完整度 {pct * 100:.0f}%，部分实测未回填", fg="#b26a00")
        else:
            self.badge.config(text="", fg="#b26a00")

        # 第二层
        for side, col in self.side_cols.items():
            for w in col.winfo_children():
                if isinstance(w, tk.Frame):
                    w.destroy()
            for label, value, tag in res["layer2"][side]:
                self._row(col, label, value, tag)
            if not res["layer2"][side]:
                tk.Label(col, text="（本模式不计此项）", fg="#999").pack(anchor="w")

        # 页脚提示
        if est:
            names = "、".join(L.PARAM_LABEL.get(k, k) for k in est)
            self.note.config(text=f"ⓘ {names}未填时按估算值参与计算")
        elif res["missing"]:
            names = "、".join(L.PARAM_LABEL.get(k, k) for k in res["missing"])
            self.note.config(text=f"ⓘ 缺 {names}，回填后结论才完整")
        else:
            self.note.config(text="全部参数已回填")


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
