#!/usr/bin/env python3
"""匹配：拿五轴档案当索引回扫日志，逐块判定「有无对应条目、对应条目、方向」。

预注册规则（写死于本文件，跑完不改；要改须另起一轮并说明）：

一、分块：直接用实验室 ../src/segmentation.py 的 load_segments，不自己切段。
二、条目：profile 五轴各篇的 `## ` 小节即条目（条目名 + 正文，正文含 `### ` 子条目），
    不另建副本。
三、判断链两问（docs/spec.md「匹配」节）：
    第一问「档案里有没有对应条目」——BM25（复用 ../src/retrieval.py 的 Bm25，
      K1=1.2 / B=0.75；doc = 条目名 + 换行 + 正文，query = 块全文）取 top1，
      同时满足才判「是」：
        ① top1 分数 ≥ SCORE_FLOOR（6.0）
        ② 块与 top1 条目共享的有区分度 CJK 二元组 ≥ SHARED_MIN（3）
           「有区分度」＝该二元组出现在不超过一半条目里（df ≤ 条目数 // 2）
      否则判「否」（全新信号）。
    第二问「方向是否一致」——默认「一致」；判「相反」需存在共享二元组锚点 A：
      块中 A 的全部出现都被否定触发词（不/没/无/非/别/未，锚点起止 ±3 字内）修饰，
      且条目中 A 的全部出现都未被否定修饰（两边极性单一且相反）。
      任一锚点成立即「相反」，否则「一致」。
四、只用离线词法（关键词 / BM25），不用 embedding、不调 LLM、不联网。

跑完自动与金标 data/gold/2026-10-05-匹配金标-20块.json 逐块比对并打印摘要。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent              # memory-factory/src
LAB = HERE.parents[2]                               # quanttide-founder-lab
sys.path.insert(0, str(LAB / "src"))

from segmentation import _is_cjk, load_segments  # noqa: E402
from retrieval import Bm25, tokenize  # noqa: E402

REPO = LAB.parent.parent                            # quanttide-founder
ASSETS = REPO / "assets"
PROFILE = ASSETS / "memory" / "default" / "profile"
CASE = HERE.parent                                   # memory-factory
OUT_PATH = CASE / "data" / "2026-10-05-匹配结果.json"
GOLD_PATH = CASE / "data" / "gold" / "2026-10-05-匹配金标-20块.json"

# 预注册阈值：跑完标定，不回调。
SCORE_FLOOR = 6.0     # 第一问①：top1 BM25 分数下限
SHARED_MIN = 3        # 第一问②：共享有区分度二元组数下限
WINDOW = 3            # 第二问：否定触发词的观察窗口（锚点起止 ±3 字）
NEG_CUES = set("不没无非别未")

AXES = ["emotions.md", "values.md", "triggers.md", "methods.md", "expressions.md"]


def load_entries() -> list[dict]:
    """五轴各篇的 `## ` 小节 → 条目列表（file / name / body / subs）。"""
    entries: list[dict] = []
    for fn in AXES:
        cur: dict | None = None
        for line in (PROFILE / fn).read_text(encoding="utf-8").split("\n"):
            if line.startswith("## ") and not line.startswith("### "):
                if cur is not None:
                    entries.append(cur)
                cur = {"file": fn, "name": line[3:].strip(), "lines": [],
                       "subs": []}
            elif cur is not None:
                cur["lines"].append(line)
                if line.startswith("### "):
                    cur["subs"].append(line[4:].strip())
        if cur is not None:
            entries.append(cur)
    for e in entries:
        e["body"] = "\n".join(e["lines"]).strip()
        e["doc"] = f"{e['name']}\n{e['body']}"
        e["label"] = f"{e['file']}「{e['name']}」"
    return entries


def cjk_bigrams(text: str) -> set[str]:
    """CJK 连串二元组（tokenize 的二元切分，取纯中文的）。"""
    return {t for t in tokenize(text)
            if len(t) == 2 and _is_cjk(t[0]) and _is_cjk(t[1])}


def occurrence_polarity(text: str, anchor: str) -> str | None:
    """锚点在文本中的极性：全被否定修饰 → neg；全无 → pos；混合或未出现 → None。"""
    total = neg = 0
    start = 0
    while True:
        i = text.find(anchor, start)
        if i < 0:
            break
        total += 1
        ctx = text[max(0, i - WINDOW):i] + text[i + len(anchor):i + len(anchor) + WINDOW]
        if any(c in NEG_CUES for c in ctx):
            neg += 1
        start = i + 1
    if total == 0:
        return None
    if neg == total:
        return "neg"
    if neg == 0:
        return "pos"
    return None


def judge(entries: list[dict], bm25: Bm25, df_bigram: dict[str, int],
          seg) -> dict:
    """对一块走判断链，产出结构化判定。"""
    text = seg.text
    n = len(entries)
    hits = bm25.search(text, top_k=1)
    top1_idx, score = (hits[0] if hits else (None, 0.0))

    shared: set[str] = set()
    if top1_idx is not None:
        shared = (cjk_bigrams(text) & cjk_bigrams(entries[top1_idx]["doc"]))
    disc = sorted(a for a in shared if df_bigram.get(a, 0) <= n // 2)

    has_entry = score >= SCORE_FLOOR and len(disc) >= SHARED_MIN
    result = {
        "id": seg.id,
        "date": seg.date,
        "path": seg.path,
        "line_start": seg.line_start,
        "line_end": seg.line_end,
        "证据_top1条目": entries[top1_idx]["label"] if top1_idx is not None else "",
        "证据_top1分数": round(score, 3),
        "证据_共享有区分度二元组": len(disc),
        "证据_共享锚点": disc[:12],
    }

    if not has_entry:
        result["判定_有无对应条目"] = "否"
        result["判定_对应条目"] = ""
        result["判定_方向"] = ""
        result["理由"] = (
            f"第一问：top1 {result['证据_top1条目'] or '（无命中）'} "
            f"分数 {result['证据_top1分数']}（阈值 {SCORE_FLOOR}）／"
            f"共享有区分度二元组 {len(disc)}（阈值 {SHARED_MIN}）→ 至少一条不达标 → 否（全新信号）"
        )
        return result

    entry = entries[top1_idx]
    direction, why = "一致", "无「块否定／条目肯定」的共享锚点"
    for a in disc:
        pb = occurrence_polarity(text, a)
        pe = occurrence_polarity(entry["doc"], a)
        if pb and pe and pb != pe:
            direction = "相反"
            why = f"锚点「{a}」块中全为否定、条目中全为肯定"
            break

    result["判定_有无对应条目"] = "是"
    result["判定_对应条目"] = entry["label"]
    result["判定_方向"] = direction
    result["理由"] = (
        f"第一问：top1 {entry['label']} 分数 {result['证据_top1分数']}（≥{SCORE_FLOOR}）且"
        f"共享有区分度二元组 {len(disc)}（≥{SHARED_MIN}）→ 是；"
        f"第二问：{why} → {direction}"
    )
    return result


# ---------------------------------------------------------------- 与金标比对

def label_map() -> dict[str, dict[str, str]]:
    """文件 → {条目名: 归一名}；`###` 子条目归一到所属 `##` 小节。"""
    m: dict[str, dict[str, str]] = {}
    for e in load_entries():
        m.setdefault(e["file"], {})[e["name"]] = e["name"]
        for s in e["subs"]:
            m[e["file"]][s] = e["name"]
    return m


def normalize_gold(label: str, lmap: dict[str, dict[str, str]]) -> list[str]:
    """金标条目串（可含多个，`＋` 分隔）→ 归一后的 (file, name) 列表。"""
    out = []
    for part in [p.strip() for p in label.replace("+", "＋").split("＋") if p.strip()]:
        if "「" not in part:
            continue
        fn, name = part.split("「", 1)
        name = name.rstrip("」")
        canon = lmap.get(fn, {}).get(name, name)
        out.append(f"{fn}「{canon}」")
    return out


def compare(mine: list[dict], gold: dict) -> dict:
    gmap = {b["id"]: b for b in gold["块"]}
    lmap = label_map()
    rows, correct = [], 0
    for g in gold["块"]:
        m = next((x for x in mine if x["id"] == g["id"]), None)
        if m is None:
            rows.append({"id": g["id"], "问题": "金标块在切段结果中不存在"})
            continue
        g_has = g["判定_有无对应条目"]
        g_lab = normalize_gold(g["判定_对应条目"], lmap)
        g_dir = g["判定_方向"]
        m_has, m_lab, m_dir = m["判定_有无对应条目"], m["判定_对应条目"], m["判定_方向"]

        ok_has = m_has == g_has
        ok_lab = (m_lab == g_lab[0]) if (g_has == "是" and g_lab) else (
            m_lab == "" if g_has == "否" else False)
        ok_dir = m_dir == g_dir if g_has == "是" else (m_dir == "")
        ok = ok_has and ok_lab and ok_dir
        correct += ok
        if not ok:
            rows.append({
                "id": g["id"],
                "我的判定": f"{m_has} / {m_lab or '—'} / {m_dir or '—'}",
                "金标": f"{g_has} / {g['判定_对应条目'] or '—'} / {g_dir or '—'}",
                "不一致栏": [k for k, v in
                             (("有无", ok_has), ("条目", ok_lab), ("方向", ok_dir)) if not v],
                "我的理由": m["理由"],
                "金标备注": g.get("判定_备注", ""),
            })
    total = len(gold["块"])
    n_dir_same = sum(1 for r in rows if "方向" in r.get("不一致栏", []))
    mine_opposite = sum(1 for x in mine if x["判定_方向"] == "相反")
    return {
        "总块数": total,
        "全对": correct,
        "准确率": f"{correct}/{total} = {correct / total:.1%}",
        "方向相反_我的判定数": mine_opposite,
        "方向相反_金标数": sum(1 for b in gold["块"] if b["判定_方向"] == "相反"),
        "方向栏不一致数": n_dir_same,
        "不一致块": rows,
    }


def main() -> None:
    segs = load_segments(ASSETS)
    entries = load_entries()
    bm25 = Bm25([e["doc"] for e in entries])

    df_bigram: dict[str, int] = {}
    for e in entries:
        for bg in cjk_bigrams(e["doc"]):
            df_bigram[bg] = df_bigram.get(bg, 0) + 1

    results = [judge(entries, bm25, df_bigram, s) for s in segs]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "_规则": (
            "第一问：BM25 top1 分数 ≥ 6.0 且共享有区分度二元组（df ≤ 条目数//2）≥ 3 → 有，否则无；"
            "第二问：默认一致，需共享锚点「块全否定／条目全肯定」（否定词窗口 ±3 字）才判相反。"
            "仅离线词法（BM25 + 关键词），无 embedding、无 LLM。"
        ),
        "_分块": "实验室 src/segmentation.py 的 load_segments",
        "语料": "assets/memory/default/",
        "条目数": len(entries),
        "段总数": len(results),
        "块": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    cmp = compare(results, gold)
    print(json.dumps(cmp, ensure_ascii=False, indent=2))
    print(f"\n结果已写入 {OUT_PATH.relative_to(CASE)}")


if __name__ == "__main__":
    main()
