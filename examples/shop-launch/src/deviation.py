#!/usr/bin/env python3
"""偏差地图汇总：对照表 + 实测记录 → 跨场景「预测值 vs 实测值」。

用法
    python3 src/deviation.py            # 汇总 data/偏差地图.csv，按品类/城市标可信度
    python3 src/deviation.py --check    # 只校验 schema 与偏差率算术

schema（data/偏差地图.csv，见 AGENTS.md 产出 ②）
    环节, 品类, 城市, 预测值, 实测值, 偏差率, 证据等级, 来源日期
    偏差率 = (实测 − 预测) / 预测；预测值为 0 时留空（不算，不编）。

无实测数据时明确报缺数据，不输出空结论。实测行入库即证据等级 L2。
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MAP = ROOT / "data" / "偏差地图.csv"
HEADER = ["环节", "品类", "城市", "预测值", "实测值", "偏差率", "证据等级", "来源日期"]


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return []
    if rows[0] != HEADER:
        sys.exit(f"表头不符，应为：{','.join(HEADER)}")
    return [dict(zip(HEADER, r)) for r in rows[1:] if r]


def dev_pct(pred: str, actual: str) -> str:
    """偏差率，两位小数百分比；预测值无法作分母时留空。"""
    try:
        p, a = float(pred), float(actual)
    except ValueError:
        return ""
    if p == 0:
        return ""
    return f"{(a - p) / p * 100:+.1f}%"


def validate(rows: list[dict]) -> list[str]:
    bad = []
    for i, r in enumerate(rows, start=2):
        if any(not r[k].strip() for k in HEADER if k != "偏差率"):
            bad.append(f"行 {i}：字段缺失 {r}")
            continue
        if r["证据等级"] != "L2":
            bad.append(f"行 {i} [{r['环节']}]：实测行证据等级须为 L2，实为 {r['证据等级']}")
        want = dev_pct(r["预测值"], r["实测值"])
        if r["偏差率"] and want and r["偏差率"] != want:
            bad.append(f"行 {i} [{r['环节']}]：偏差率 {r['偏差率']} 与算术 {want} 不符")
    return bad


def credibility(rows: list[dict]) -> list[tuple[str, str, int, float]]:
    """按 (品类, 城市) 聚合：行数与平均 |偏差率|，给环节可信度参考。"""
    groups: dict[tuple[str, str], list[float]] = {}
    for r in rows:
        try:
            v = abs(float(r["偏差率"].rstrip("%")))
        except (ValueError, AttributeError):
            continue
        groups.setdefault((r["品类"], r["城市"]), []).append(v)
    out = []
    for (cat, city), vals in sorted(groups.items()):
        out.append((cat, city, len(vals), sum(vals) / len(vals)))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="deviation")
    p.add_argument("--check", action="store_true", help="只校验 schema")
    a = p.parse_args(argv)

    rows = load(MAP)
    bad = validate(rows)
    if bad:
        print("\n".join(bad), file=sys.stderr)
        return 1
    if a.check:
        print(f"schema 校验通过：{len(rows)} 行")
        return 0

    if not rows:
        print("缺数据：data/偏差地图.csv 尚无实测行。")
        print("等探店 4 数据与丰全巷摆摊记录回填（TODO.md 3.3）后再汇总。")
        return 2

    print(f"── 偏差地图 {len(rows)} 行（证据等级 L2）──")
    for r in rows:
        print(f"  [{r['环节']}] {r['品类']}/{r['城市']}  "
              f"预测 {r['预测值']} → 实测 {r['实测值']}  偏差 {r['偏差率'] or '—'}")
    print("\n── 品类/城市可信度（平均 |偏差|，越低越可信）──")
    for cat, city, n, avg in credibility(rows):
        print(f"  {cat}/{city}  {n} 个环节  平均偏差 {avg:.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
