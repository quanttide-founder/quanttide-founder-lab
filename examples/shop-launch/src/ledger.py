#!/usr/bin/env python3
"""算账工具：串串店三阶段达标线、人均结构与盈亏平衡测算。

用法
    python3 src/ledger.py all [--mix 60:30:10]
    python3 src/ledger.py per-head [--mix 60:30:10]
    python3 src/ledger.py pot
    python3 src/ledger.py stall [--mix 60:30:10] [--daily 180]
    python3 src/ledger.py shop --staff 4000 --utility 1500 --food-rate 0.42
    python3 src/ledger.py gaps
    python3 src/ledger.py selftest

参数来源分三级，输出逐行标注
    [锁] 取自 data/火锅串串.md，已由实地探店校准，不得覆盖
    [L0] 未验证假设，必须显式给出；拿到探店数据后更新
    [L1] 待回填。缺失时本工具拒绝计算该段，不编造数值

与 `AGENTS.md` 的关系：本工具只承担框架中 A 类（抽象推理）环节——
财务测算与盈亏平衡。B 类（本地数据）由 `--mix` 等参数喂入，
C 类（现场、感官、随机性）不在计算范围内，其结果一律不进本工具。
"""
from __future__ import annotations

import argparse
import sys

# ── [锁] 取自 data/火锅串串.md；若该文件的锁定项变动，同步改这里 ──
LOCK = {
    "head": (55.0, 65.0),            # 人均目标
    "pot": (15.0, 20.0),             # 锅底/位
    "veg": (0.5, 0.8),               # 素签
    "meat": (1.0, 1.5),              # 常规荤签
    "premium": (2.0, 3.0),           # 招牌鲜切牛肉签
    "stall_daily": 150,              # 摆摊达标：日均签数
    "stall_weeks": 4,                # 摆摊达标：连续周数
    "stall_invest": (15_000.0, 30_000.0),
    "loss_cap": 20_000.0,            # 风险底线：第一阶段最多亏
    "waste_cap": 0.10,               # 纪律：损耗上限
    "rent_cap": 3_500.0,             # 档口：月租上限
    "rev_floor": 45_000.0,           # 档口：月营业额达标线
    "net_floor": 0.15,               # 档口：净利率达标线
    "big_cash_floor": 80_000.0,      # 大店：流动资金达标线
    "broth_cost": (6.0, 8.0),        # 一锅底料成本
    "broth_skews": (80, 100),        # 一锅可煮签数
}

# [L0] 占位假设，未验证；探店带回荤签品类后必须更新
DEFAULT_MIX = (60, 30, 10)

# ── 待回填清单：`gaps` 与 `report` 同一份数据 ──
GAPS = [
    ("荤签品类比例", "探店：荤签品类", "直接决定均价与全部营收测算"),
    ("翻台率", "探店：翻台率", "决定档口店日接待上限，是月营业额的天花板"),
    ("客单价实测", "探店：服务短板/现场记录", "校验人均 55–65 是否站得住"),
    ("食材成本率", "供应链询价", "变动成本率里最大的一项"),
    ("人工 / 水电 / 杂费", "当地用工与场地询价", "盈亏平衡点的分子"),
    ("实际损耗率", "摆摊期每日盘点", "纪律上限 10%，实际值决定净利率"),
    ("底料盲测冠军款", "盲测评分表汇总", "定下后底料成本从区间变定值"),
]

# 参与完整度统计的 L1 参数（分母），按模式区分
COMPLETENESS = {
    "shop": ("ticket", "traffic", "staff", "utility_other", "food_rate"),
    "stall": ("daily", "waste"),
}

PARAM_LABEL = {
    "ticket": "客单价", "traffic": "日客流", "staff": "人工月成本",
    "utility_other": "水电其他", "food_rate": "食材成本率",
    "daily": "摆摊日均签数", "waste": "实际损耗率",
}

DAYS = 30  # 月天数：保本线与月利润的换算基数


def parse_mix(text: str) -> tuple[float, float, float]:
    """解析 `素:荤:招牌` 比例。"""
    parts = text.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("mix 须为 素:荤:招牌 三段，如 60:30:10")
    try:
        vals = tuple(float(p) for p in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"mix 含非数字：{text}") from exc
    if any(v < 0 for v in vals) or sum(vals) <= 0:
        raise argparse.ArgumentTypeError("mix 须为非负且总和 > 0")
    return vals  # type: ignore[return-value]


def price_range(mix: tuple[float, float, float]) -> tuple[float, float]:
    """按签型比例求平均签价区间（各档取区间上下界加权）。"""
    total = sum(mix)
    lo = (mix[0] * LOCK["veg"][0] + mix[1] * LOCK["meat"][0] + mix[2] * LOCK["premium"][0]) / total
    hi = (mix[0] * LOCK["veg"][1] + mix[1] * LOCK["meat"][1] + mix[2] * LOCK["premium"][1]) / total
    return lo, hi


def broth_per_skew() -> tuple[float, float]:
    """每签摊到的底料成本。取极端组合：最省 (低配/多签) 与最贵 (高配/少签)。"""
    return LOCK["broth_cost"][0] / LOCK["broth_skews"][1], LOCK["broth_cost"][1] / LOCK["broth_skews"][0]


def per_head(mix: tuple[float, float, float]) -> dict:
    """人均拆解：锅底扣掉后，签子部分能吃多少签。"""
    skew_low = LOCK["head"][0] - LOCK["pot"][1]   # 最不利：人均最低、锅底最高
    skew_high = LOCK["head"][1] - LOCK["pot"][0]
    p_lo, p_hi = price_range(mix)
    return {
        "skew_spend": (skew_low, skew_high),
        "price": (p_lo, p_hi),
        "skew_count": (skew_low / p_hi, skew_high / p_lo),
    }


def stall(mix: tuple[float, float, float], daily: int | None = None) -> dict:
    """摆摊阶段：达标判定 + 收入测算。"""
    p_lo, p_hi = price_range(mix)
    target = LOCK["stall_daily"]
    n = daily if daily is not None else target
    days = LOCK["stall_weeks"] * 7
    return {
        "n": n,
        "target": target,
        "met": (daily is None) or (daily >= target),
        "known": daily is not None,
        "day_rev": (n * p_lo, n * p_hi),
        "period_rev": (n * p_lo * days, n * p_hi * days),
        "days": days,
    }


def shop(
    mix: tuple[float, float, float],
    rev: float,
    rent: float,
    staff: float | None,
    utility: float | None,
    other_fixed: float | None,
    food_rate: float | None,
) -> tuple[dict | None, list[str]]:
    """档口阶段：净利率与盈亏平衡。L1 缺失则返回 (None, 缺口清单)。"""
    missing = []
    if staff is None:
        missing.append("人工月成本 (--staff)")
    if utility is None:
        missing.append("水电杂费月成本 (--utility)")
    if other_fixed is None:
        missing.append("其他固定成本 (--other-fixed)")
    if food_rate is None:
        missing.append("食材成本率 (--food-rate，占营业额)")
    if missing:
        return None, missing

    p_lo, p_hi = price_range(mix)
    b_lo, b_hi = broth_per_skew()
    broth_rate = ((b_lo + b_hi) / 2) / ((p_lo + p_hi) / 2)

    fixed = rent + staff + utility + other_fixed
    waste_rate = LOCK["waste_cap"]
    var_rate = food_rate * (1 + waste_rate) + broth_rate
    net = rev * (1 - var_rate) - fixed
    break_even = fixed / (1 - var_rate) if var_rate < 1 else float("inf")

    return {
        "rev": rev,
        "fixed": fixed,
        "fixed_parts": (rent, staff, utility, other_fixed),
        "var_rate": var_rate,
        "broth_rate": broth_rate,
        "net": net,
        "net_margin": net / rev,
        "break_even": break_even,
        "net_floor_abs": rev * LOCK["net_floor"],
    }, []


def r(x: float) -> str:
    """金额格式：整数不带小数。"""
    return f"{x:,.0f}"


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def income(ticket: tuple[float, float] | None, traffic: tuple[float, float] | None) -> dict | None:
    """收入侧：客单价 × 日客流 → 日/月流水。任一缺失返回 None（不代填）。"""
    if ticket is None or traffic is None:
        return None
    day = (ticket[0] * traffic[0], ticket[1] * traffic[1])
    return {"day": day, "month": (day[0] * DAYS, day[1] * DAYS)}


def var_rate(food_rate: float, mix: tuple[float, float, float]) -> tuple[float, float]:
    """变动成本率 = 食材率×(1+损耗) + 底料率。返回 (总, 底料占比)。"""
    p_lo, p_hi = price_range(mix)
    b_lo, b_hi = broth_per_skew()
    broth_rate = ((b_lo + b_hi) / 2) / ((p_lo + p_hi) / 2)
    return food_rate * (1 + LOCK["waste_cap"]) + broth_rate, broth_rate


def pnl(month_rev: tuple[float, float], fixed: float, varr: float) -> dict:
    """利润三件套，三个数字同源导出，避免头条互相矛盾。"""
    be_month = fixed / (1 - varr) if varr < 1 else float("inf")
    return {
        "net": (month_rev[0] * (1 - varr) - fixed, month_rev[1] * (1 - varr) - fixed),
        "be_month": be_month,
        "be_day": be_month / DAYS,
        "multiple": (month_rev[0] / be_month, month_rev[1] / be_month),
    }


def verdict(multi: tuple[float, float]) -> str:
    """安全倍数区间 → 一句话判定。区间跨过 1 时不给确定性结论。"""
    lo, hi = multi
    if lo >= 1:
        return f"✓ 高于保本线 {lo:.2f}~{hi:.2f} 倍"
    if hi < 1:
        return f"✗ 低于保本线 {lo:.2f}~{hi:.2f} 倍"
    return f"⚠ 跨过保本线 {lo:.2f}~{hi:.2f} 倍"


def present(mode: str, params: dict) -> dict:
    """三层报表数据层。CLI `report` 使用。

    params 取值为 None 表示未回填。不代填、不估算：缺失项进 missing，
    完整度 = 已填 / 该模式 L1 参数总数。
    返回 {completeness, missing, computable, layer1, layer2}，
    layerN 每行是 (标签, 数值, 标签来源)。
    """
    p = dict(params)
    req = COMPLETENESS[mode]
    missing = [k for k in req if p.get(k) is None]
    filled = len(req) - len(missing)
    completeness = max(0.0, filled / len(req)) if req else 1.0

    mix = p.get("mix") or DEFAULT_MIX
    out: dict = {
        "completeness": completeness,
        "missing": missing,
        "computable": not missing,
        "layer1": [],
        "layer2": {"income": [], "cost": []},
    }

    if mode == "stall":
        daily = p.get("daily")
        s = stall(mix, daily)
        known = daily is not None
        diff = s["target"] - s["n"]
        if known:
            line1 = ("达标判定", f"{s['n']} 签/天", "✓ 达标" if s["met"] else f"✗ 差 {diff} 签/天")
        else:
            line1 = ("达标判定", "未回填", "以达标线代入，非实测")
        out["layer1"] = [
            line1,
            ("28 天累计", f"¥{r(s['period_rev'][0])}~{r(s['period_rev'][1])}",
             f"投入 ¥{r(LOCK['stall_invest'][0])}~{r(LOCK['stall_invest'][1])}"),
        ]
        out["layer2"]["income"] = [
            ("日均签数", f"{s['n']} 签", "锁" if not known else "L1"),
            ("日流水", f"¥{r(s['day_rev'][0])}~{r(s['day_rev'][1])}", "锁+L0"),
            ("28 天累计", f"¥{r(s['period_rev'][0])}~{r(s['period_rev'][1])}", "推算"),
        ]
        waste = p.get("waste")
        # 摆摊的公式恒可算（daily 缺失则以达标线代入），
        # waste 只影响成本侧一行，不阻断结论
        out["computable"] = True
        out["layer2"]["cost"] = [
            ("投入(一次性)", f"¥{r(LOCK['stall_invest'][0])}~{r(LOCK['stall_invest'][1])}", "锁"),
            ("风险底线", f"最多亏 ¥{r(LOCK['loss_cap'])}", "锁"),
            ("实际损耗率", "未回填" if waste is None else pct(waste),
             "L1" if waste is not None else "纪律 ≤10%"),
        ]
        return out

    # mode == "shop"
    inc = income(p.get("ticket"), p.get("traffic"))
    fixed_parts = (p.get("rent") if p.get("rent") is not None else LOCK["rent_cap"],
                   p.get("staff"), p.get("utility_other"),
                   p.get("other_fixed") or 0)  # 其他固定成本可为 0，不计入完整度
    if inc is None or any(v is None for v in fixed_parts) or p.get("food_rate") is None:
        out["computable"] = False
        out["layer1"] = [("月利润", "无法计算", "缺 L1，见待回填"),
                          ("保本线", "—", "回填后自动出")]
        return out

    fixed = sum(fixed_parts)
    varr, broth_rate = var_rate(p["food_rate"], mix)
    d = pnl(inc["month"], fixed, varr)
    out["layer1"] = [
        ("月利润", f"¥{r(d['net'][0])}~{r(d['net'][1])}", verdict(d["multiple"])),
        ("保本线", f"每天卖 ¥{r(d['be_day'])}",
         f"你的日均 ¥{r(inc['day'][0])}~{r(inc['day'][1])}"),
    ]
    out["layer2"]["income"] = [
        ("日均流水", f"¥{r(inc['day'][0])}~{r(inc['day'][1])}", "推算"),
        ("├ 客单价", f"¥{p['ticket'][0]:g}~{p['ticket'][1]:g}", "L1"),
        ("└ 日客流", f"{p['traffic'][0]:g}~{p['traffic'][1]:g} 人", "L1"),
        ("月流水", f"¥{r(inc['month'][0])}~{r(inc['month'][1])}", "推算"),
    ]
    out["layer2"]["cost"] = [
        ("固定成本/月", f"¥{r(fixed)}", "推算"),
        ("├ 房租/摊位", f"¥{r(fixed_parts[0])}", "锁"),
        ("├ 人工", f"¥{r(fixed_parts[1])}", "L1"),
        ("└ 水电其他", f"¥{r(fixed_parts[2] + fixed_parts[3])}", "L1"),
        ("食材成本率", pct(p["food_rate"]), "L1"),
        ("变动成本率", pct(varr), f"食材+损耗+底料 {pct(broth_rate)}"),
        ("保本月流水", f"¥{r(d['be_month'])}", f"占月流水 {d['be_month'] / inc['month'][1] * 100:.0f}~{d['be_month'] / inc['month'][0] * 100:.0f}%"),
    ]
    return out


def print_head(mix) -> None:
    d = per_head(mix)
    print("── 人均拆解 ──")
    print(f"  人均目标      [锁] {r(LOCK['head'][0])}–{r(LOCK['head'][1])} 元")
    print(f"  锅底/位       [锁] {r(LOCK['pot'][0])}–{r(LOCK['pot'][1])} 元")
    print(f"  签子可花      [锁] {r(d['skew_spend'][0])}–{r(d['skew_spend'][1])} 元")
    print(f"  平均签价      [锁+L0] {d['price'][0]:.2f}–{d['price'][1]:.2f} 元（比例 {mix[0]:g}:{mix[1]:g}:{mix[2]:g} 为 L0）")
    print(f"  人均签数      推算 {d['skew_count'][0]:.0f}–{d['skew_count'][1]:.0f} 签")
    print()


def print_pot() -> None:
    lo, hi = broth_per_skew()
    print("── 底料摊薄 ──")
    print(f"  一锅底料      [锁] {r(LOCK['broth_cost'][0])}–{r(LOCK['broth_cost'][1])} 元")
    print(f"  一锅煮签      [锁] {LOCK['broth_skews'][0]}–{LOCK['broth_skews'][1]} 签")
    print(f"  每签底料      推算 {lo:.3f}–{hi:.3f} 元")
    print()


def print_stall(mix, daily: int | None) -> None:
    s = stall(mix, daily)
    print("── 阶段一：摆摊 ──")
    print(f"  达标线        [锁] 连续 {LOCK['stall_weeks']} 周日均 {LOCK['stall_daily']} 签")
    if s["known"]:
        verdict = "达标" if s["met"] else f"差 {s['target'] - s['n']} 签/天"
        print(f"  实测日均      {s['n']} 签 → {verdict}")
    else:
        print("  实测日均      未回填 [L1]（以达标线代入测算）")
    print(f"  日营收        推算 {r(s['day_rev'][0])}–{r(s['day_rev'][1])} 元")
    print(f"  {s['days']} 天累计   推算 {r(s['period_rev'][0])}–{r(s['period_rev'][1])} 元")
    print(f"  投入          [锁] {r(LOCK['stall_invest'][0])}–{r(LOCK['stall_invest'][1])} 元")
    print(f"  风险底线      [锁] 最多亏 {r(LOCK['loss_cap'])} 元出局")
    print()


def print_shop(mix, rev, rent, staff, utility, other_fixed, food_rate) -> list[str]:
    d, missing = shop(mix, rev, rent, staff, utility, other_fixed, food_rate)
    print("── 阶段二：档口店 ──")
    print(f"  营业额达标线  [锁] {r(LOCK['rev_floor'])} 元/月")
    print(f"  净利率达标线  [锁] {pct(LOCK['net_floor'])} → 净利 ≥ {r(rev * LOCK['net_floor'])} 元")
    print(f"  月租上限      [锁] {r(LOCK['rent_cap'])} 元")
    if d is None:
        print(f"  盈亏平衡      无法计算：缺 {'、'.join(missing)}")
        print("                [L1] 本工具不代填，探店/询价后回填再算")
        print()
        return missing

    rent_, staff_, util_, other_ = d["fixed_parts"]
    print(f"  固定成本      {r(rent_)}(租) + {r(staff_)}(人工) + {r(util_)}(水电) + {r(other_)}(其他) = {r(d['fixed'])} 元")
    print(f"  变动成本率    {pct(d['var_rate'])}（食材 {pct(food_rate)}×(1+损耗 {pct(LOCK['waste_cap'])}) + 底料 {pct(d['broth_rate'])}）")
    print(f"  预估净利      {r(d['net'])} 元 → 净利率 {pct(d['net_margin'])}  {'✔ 达标' if d['net_margin'] >= LOCK['net_floor'] else '✘ 未达标'}")
    print(f"  盈亏平衡点    {r(d['break_even'])} 元/月（达成营业额的 {d['break_even'] / d['rev'] * 100:.0f}%）")
    print()
    return []


def print_gaps(mix) -> None:
    print("── 待回填 [L1] ──")
    for name, source, why in GAPS:
        print(f"  · {name}")
        print(f"      来源：{source}")
        print(f"      影响：{why}")
    print()


def selftest() -> int:
    """固定数学关系，防止改代码时悄悄算错。"""
    checks = []

    def ck(label: str, got, want, tol: float = 1e-9) -> None:
        ok = abs(got - want) <= tol if isinstance(want, float) else got == want
        checks.append((label, ok, got, want))

    # 人均：55–65 扣锅底 15–20 → 签子 35–50
    d = per_head(DEFAULT_MIX)
    ck("签子可花下界", d["skew_spend"][0], 35.0)
    ck("签子可花上界", d["skew_spend"][1], 50.0)

    # 底料：6/100 = 0.06，8/80 = 0.10
    lo, hi = broth_per_skew()
    ck("底料下界", lo, 0.06, 1e-12)
    ck("底料上界", hi, 0.10, 1e-12)

    # 均价随招牌占比上升
    ck("均价单调性(低端)", price_range((100, 0, 0))[0] < price_range((0, 0, 100))[0], True)

    # 摆摊：150 签/天 × 28 天
    s = stall(DEFAULT_MIX)
    ck("摆摊天数", s["days"], 28)
    ck("摆摊达标(未回填)", s["met"], True)

    # 达标线判定
    ck("摆摊 149 未达标", stall(DEFAULT_MIX, 149)["met"], False)
    ck("摆摊 150 达标", stall(DEFAULT_MIX, 150)["met"], True)

    # L1 缺失必须拒绝
    _, missing = shop(DEFAULT_MIX, 45_000, 3_500, None, None, None, None)
    ck("L1 缺失返回缺口", len(missing) == 4, True)

    # 盈亏平衡恒等式：净利为 0 时营业额 == break_even
    d2, miss = shop(DEFAULT_MIX, 45_000, 3_500, 4_000, 1_500, 500, 0.42)
    ck("shop 无缺口", miss, [])
    if d2:
        var = d2["var_rate"]
        fixed = d2["fixed"]
        at_be = fixed / (1 - var) * (1 - var) - fixed
        ck("盈亏平衡点净利为0", at_be, 0.0, 1e-6)

    failed = [(l, g, w) for l, ok, g, w in checks if not ok]
    for label, ok, got, want in checks:
        print(f"  [{'ok' if ok else 'FAIL'}] {label}: got={got} want={want}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} passed")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="串串店算账工具")
    sub = p.add_subparsers(dest="cmd")

    def add_mix(sp):
        sp.add_argument("--mix", type=parse_mix, default=DEFAULT_MIX,
                        metavar="素:荤:招牌", help="签型比例 [L0]，默认 60:30:10")

    def add_shop_args(sp):
        sp.add_argument("--rev", type=float, default=LOCK["rev_floor"], help="月营业额 [锁]，默认达标线")
        sp.add_argument("--rent", type=float, default=LOCK["rent_cap"], help="月租 [锁]，默认上限")
        sp.add_argument("--staff", type=float, default=None, help="人工月成本 [L1]")
        sp.add_argument("--utility", type=float, default=None, help="水电杂费月成本 [L1]")
        sp.add_argument("--other-fixed", type=float, default=None, help="其他固定成本 [L1]")
        sp.add_argument("--food-rate", type=float, default=None, help="食材成本率 [L1]，占营业额")

    sp_all = sub.add_parser("all", help="全部测算")
    add_mix(sp_all)
    add_shop_args(sp_all)
    add_mix(sub.add_parser("per-head", help="人均拆解"))
    sub.add_parser("pot", help="底料摊薄")
    sp = sub.add_parser("stall", help="摆摊阶段")
    add_mix(sp)
    sp.add_argument("--daily", type=int, default=None, help="实测日均签数 [L1]")

    sp = sub.add_parser("shop", help="档口店阶段")
    add_mix(sp)
    add_shop_args(sp)

    sub.add_parser("gaps", help="列出待回填项")
    sub.add_parser("selftest", help="自检")
    sp = sub.add_parser("report", help="三层报表（结论→账目→细算）")
    sp.add_argument("--mode", choices=["stall", "shop"], default="shop")
    add_mix(sp)
    sp.add_argument("--daily", type=int, default=None, help="摆摊实测日均签数 [L1]")
    sp.add_argument("--ticket", help="客单价区间，如 32:45 [L1]")
    sp.add_argument("--traffic", help="日客流区间，如 15:20 [L1]")
    add_shop_args(sp)

    args = p.parse_args(argv)
    cmd = args.cmd or "all"
    mix = getattr(args, "mix", DEFAULT_MIX)

    if cmd == "selftest":
        return selftest()

    missing: list[str] = []
    if cmd in ("all", "per-head"):
        print_head(mix)
    if cmd == "pot" or cmd == "all":
        print_pot()
    if cmd in ("all", "stall"):
        print_stall(mix, getattr(args, "daily", None))
    if cmd in ("all", "shop"):
        missing = print_shop(
            mix,
            getattr(args, "rev", LOCK["rev_floor"]),
            getattr(args, "rent", LOCK["rent_cap"]),
            getattr(args, "staff", None),
            getattr(args, "utility", None),
            getattr(args, "other_fixed", None),
            getattr(args, "food_rate", None),
        )
    if cmd == "gaps" or cmd == "all":
        print_gaps(mix)

    if cmd == "report":
        return print_report(args, mix)

    return 2 if missing else 0


def parse_range(text: str | None) -> tuple[float, float] | None:
    """解析 `32:45` 区间。"""
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 2:
        raise SystemExit(f"区间须为 低:高 两段：{text}")
    lo, hi = float(parts[0]), float(parts[1])
    if lo > hi:
        raise SystemExit(f"区间下界大于上界：{text}")
    return lo, hi


def print_report(args, mix) -> int:
    """三层报表：present() 数据层的文本渲染。"""
    mode = args.mode
    params = {
        "mix": mix,
        "daily": getattr(args, "daily", None),
        "ticket": parse_range(getattr(args, "ticket", None)),
        "traffic": parse_range(getattr(args, "traffic", None)),
        "rent": getattr(args, "rent", None),
        "staff": getattr(args, "staff", None),
        "utility_other": getattr(args, "utility", None),
        "other_fixed": getattr(args, "other_fixed", None),
        "food_rate": getattr(args, "food_rate", None),
    }
    res = present(mode, params)

    print(f"══ 第一层：结论 ══    数据完整度 {res['completeness'] * 100:.0f}%")
    for label, value, note in res["layer1"]:
        print(f"  {label:<10} {value:<22} {note}")
    print("\n══ 第二层：账怎么算的 ══")
    for side in ("income", "cost"):
        title = "收入侧" if side == "income" else "成本侧"
        print(f"  [{title}]")
        for label, value, tag in res["layer2"][side]:
            print(f"    {label:<12} {value:<20} {tag}")
    if res["missing"]:
        print(f"\n  ⚠ 缺 L1：{'、'.join(PARAM_LABEL[k] for k in res['missing'])}")
        print("    本工具不代填，探店/询价后回填再算")
    return 2 if res["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
