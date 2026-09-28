#!/usr/bin/env python3
"""能力边界评估流水：决策环节 + 材料 → 能力分、输入依赖、证据等级。

用法
    python3 src/assess.py --seed "docs/AI 辅助开店.md" [--out data/能力对照表.csv]
    python3 src/assess.py eval 证照合规 --material "常见证照清单能列全，本地执法口径未核实"
    python3 src/assess.py check data/能力对照表.csv

推导规则（AGENTS.md 评分标尺的可执行化）
    输入依赖：局限文本命中 C 词表（现场、感官…）记 C，命中 B 词表（本地、锚定…）记 B；
              能力分 ≥ 60 视作 AI 可交付的 A 类，低于此只由缺失的输入决定分数；
              两类都没命中 → A（纯抽象推理）。
    分数带（eval 未显式给 --score 时）：纯 A 70 / A+C 60 / A+B 50 / B 45 / 纯 C 25。
    证据等级默认 L0、验证状态默认待验证 —— 机器推导一律是待验证假设，不是结论。

与 AGENTS.md 的关系（判例与成文法，见其「人机对齐」节）：
    本脚本是成文法的可执行投影——词表、分数带、检查规则与 AGENTS.md 条文对应，
    改条文先改那里，再同步这里。
    输出是判例：`依据法条` 列引用条文（依A·标尺·检查§2），`复核` 列留给人的标注。
    重放（--seed）不覆盖复核为 维持/改判 的行——改判是终审。
    L1/L2 只由数据到达推进，与人的表态脱钩，不在此臆造。
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from pathlib import Path

HEADER = ["环节", "AI 能力分", "局限", "输入依赖", "证据等级",
          "验证状态", "依据法条", "复核"]
REVIEW_STATES = ("未复核", "维持", "改判")
TABLE = Path(__file__).resolve().parent.parent / "data" / "能力对照表.csv"
REPLAY_KEEP = ("维持", "改判")  # 判例优先：重放不覆盖

C_WORDS = ["现场", "蹲点", "试吃", "闻", "尝", "口味", "感官", "身体",
           "突发", "情绪", "随机", "在场", "碰不到"]
B_WORDS = ["本地", "滁州", "锚定", "人流", "当地", "实地", "街区", "商圈", "微观"]
LEVELS = ["L0", "L1", "L2"]
STATUSES = ["待验证", "已验证", "已证伪"]
# 含 C 的行，局限必须点名需人工的部分
HUMAN = re.compile(r"人工|兜底|验证|闻|尝|口味|现场|蹲点|试吃|身体|突发|情绪|碰不到|你")
# 能力描述里没有让步词的，视为只有能力没写局限
CONTRAST = re.compile(r"但|缺|不|无法|碰不到|判断不了|未核实|未验证|验证")


def basis_of(deps: set[str]) -> str:
    """判例的法条引用：命中的依X + 标尺 + 适用的检查条文。"""
    parts = [f"依{d}" for d in sorted(deps, key="ABC".index)] or ["依A"]
    parts.append("标尺")
    if deps == {"A"}:
        parts.append("检查§1")
    elif deps == {"C"}:
        parts.append("检查§3")
    elif "C" in deps:
        parts.append("检查§2")
    if "B" in deps:
        parts.append("检查§4")
    return "·".join(parts)


def derive(name: str, text: str, score: int | None = None, dep: str | None = None) -> list[str]:
    """材料文本 → 一行八字段。分数与依赖可显式给出，缺省按规则推导。"""
    if dep is None:
        deps = []
        if any(w in text for w in C_WORDS):
            deps.append("C")
        if any(w in text for w in B_WORDS):
            deps.append("B")
        if score is None:
            if not deps:
                score = 70          # 纯 A
            elif "C" in deps:
                score = 60 if "B" not in deps else 55
            else:
                score = 50 if "A" in deps else 45
        if score >= 60:
            deps.insert(0, "A")
        if not deps:
            deps = ["A"]
        deps.sort(key="ABC".index)
        dep = " + ".join(deps)
    elif score is None:
        raise SystemExit("显式 --dep 时必须同时给 --score（机器不猜分数）")

    if not CONTRAST.search(text):
        text = f"{text}；仅通用知识，未喂本地数据"
    deps = {d.strip() for d in dep.split("+")}
    return [name, str(score), text, dep, "L0", "待验证", basis_of(deps), "未复核"]


def parse_seed(path: Path) -> list[list[str]]:
    """解析对照表原文（制表符分隔：环节 / 星级分 / 局限）。"""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if "\t★" not in line:
            continue
        name, rating, text = line.split("\t", 2)
        m = re.search(r"(\d+)\s*分", rating)
        if not m:
            continue
        rows.append(derive(name.strip(), text.strip(), score=int(m.group(1))))
    if not rows:
        sys.exit(f"种子表未解析到任何行：{path}")
    return rows


def preserve_reviewed(rows: list[list[str]], table: Path) -> list[list[str]]:
    """判例优先：重放时保留复核为 维持/改判 的行，未复核行重算。"""
    if not table.exists():
        return rows
    with open(table, encoding="utf-8", newline="") as f:
        existing = [r for r in csv.reader(f) if r]
    if not existing or existing[0] != HEADER or any(len(r) != len(HEADER) for r in existing[1:]):
        return rows  # 旧表头或不完整，无可保留判例
    reviewed = {r[0]: r for r in existing if r[7] in REPLAY_KEEP}
    out, kept = [], set()
    for r in rows:
        if r[0] in reviewed:
            out.append(reviewed[r[0]])
            kept.add(r[0])
        else:
            out.append(r)
    extra = [r for n, r in reviewed.items() if n not in kept]
    if extra:
        print(f"判例保留：{[r[0] for r in extra]} 不在种子源中，不删除", file=sys.stderr)
    return out + extra


def to_csv(rows: list[list[str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(HEADER)
    w.writerows(rows)
    return buf.getvalue()


def read_csv(path: Path) -> list[list[str]]:
    with open(path, encoding="utf-8", newline="") as f:
        rows = [r for r in csv.reader(f) if r]
    if not rows or rows[0] != HEADER:
        sys.exit(f"表头不符，应为：{','.join(HEADER)}")
    return rows[1:]


def validate_rows(rows: list[list[str]], label: str = "") -> list[str]:
    """对内存中的行跑一致性检查，返回违规清单（GUI 保存前复用，不复制规则）。"""
    bad: list[str] = []
    for i, row in enumerate(rows, start=2):
        where = f"{label}:{i}" if label else f"行 {i}"
        if len(row) != len(HEADER) or any(not c.strip() for c in row):
            bad.append(f"{where} 八字段必须齐全非空：{row}")
            continue
        name, score_s, limit, dep_s, level, status = row[:6]
        review = row[7]
        try:
            score = int(score_s)
        except ValueError:
            bad.append(f"{where} [{name}] 能力分非整数：{score_s}")
            continue
        deps = {d.strip() for d in dep_s.split("+")}
        if not deps <= {"A", "B", "C"}:
            bad.append(f"{where} [{name}] 输入依赖取值非法：{dep_s}")
            continue
        if level not in LEVELS:
            bad.append(f"{where} [{name}] 证据等级非法：{level}（B 类必须标出 L0/L1/L2）")
        if status not in STATUSES:
            bad.append(f"{where} [{name}] 验证状态非法：{status}")
        if review not in REVIEW_STATES:
            bad.append(f"{where} [{name}] 复核标注非法：{review}（{'/'.join(REVIEW_STATES)}）")
        if deps == {"A"} and score < 60:
            bad.append(f"{where} [{name}] 纯 A 应 ≥ 60，实为 {score}")
        if "C" in deps and score > 80:
            bad.append(f"{where} [{name}] 含 C 应 ≤ 80，实为 {score}")
        if "C" in deps and not HUMAN.search(limit):
            bad.append(f"{where} [{name}] 含 C 的局限须点名人工兜底部分：{limit}")
        if deps == {"C"} and score > 30:
            bad.append(f"{where} [{name}] 纯 C 应 ≤ 30，实为 {score}")
    return bad


def check(path: Path) -> int:
    """AGENTS.md 一致性检查：入库前必过，违规退出码 1。"""
    rows = read_csv(path)
    bad = validate_rows(rows, str(path))
    if bad:
        print("\n".join(bad), file=sys.stderr)
        print(f"{len(bad)} 处违规 / {len(rows)} 行", file=sys.stderr)
        return 1
    print(f"一致性检查通过：{len(rows)} 行")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "check":
        p = argparse.ArgumentParser(prog="assess check")
        p.add_argument("csv", type=Path)
        a = p.parse_args(argv[1:])
        return check(a.csv)

    if argv and argv[0] == "eval":
        p = argparse.ArgumentParser(prog="assess eval")
        p.add_argument("name", help="决策环节，如 证照合规")
        p.add_argument("--material", required=True, help="材料/局限描述")
        p.add_argument("--score", type=int, help="能力分，缺省按依赖带推导")
        p.add_argument("--dep", help="输入依赖，显式给出时必须配 --score")
        a = p.parse_args(argv[1:])
        print(to_csv([derive(a.name, a.material, a.score, a.dep)]), end="")
        return 0

    p = argparse.ArgumentParser(prog="assess")
    p.add_argument("--seed", type=Path, help="从对照表原文推导")
    p.add_argument("--out", type=Path, help="写出 CSV，缺省打印到 stdout")
    a = p.parse_args(argv)
    if not a.seed:
        p.error("需要 --seed、eval 或 check")
    rows = parse_seed(a.seed)
    merged = preserve_reviewed(rows, TABLE)
    preserved = [r for r in merged if r[7] in REPLAY_KEEP]
    rows = merged
    out = to_csv(rows)
    if a.out:
        a.out.write_text(out, encoding="utf-8")
        note = f"，保留已复核判例 {len(preserved)} 行" if preserved else ""
        print(f"{a.out}：{len(rows)} 行{note}")
    else:
        print(out, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
