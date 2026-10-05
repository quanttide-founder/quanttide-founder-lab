#!/usr/bin/env python3
"""检索引擎：从 memory 原始日志段里为加工中的创作素材捞出相关片段。

切段与两臂打分已抽到实验室 `src/`（segmentation.py / retrieval.py），本文件只留本案例的
实验编排（评分器、报告、run_emotion）。口径与 novel-planner 第二轮一致，预注册不回调参。

中间数据一律 JSON，程序不读写文档。实验设计与结果见 docs/experiment.md。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 实验室复用模块
_LAB_SRC = Path(__file__).resolve().parents[3] / "src"
if str(_LAB_SRC) not in sys.path:
    sys.path.insert(0, str(_LAB_SRC))

from segmentation import *  # noqa: F401,F403  （切段：Doc/Unit/Segment/load_* /build_units）
from retrieval import *  # noqa: F401,F403  （两臂：Bm25/Embedder/tokenize/相关集）

# 本案例的参数
RETRIEVE_K = 8
TOP3 = 3
GROUP_DIARY = "情绪日记"


def build_queries(docs: list[Doc]) -> list[QueryDoc]:
    """语料文件 → 查询集（纯函数，只取情绪日记）。"""
    out = [
        QueryDoc(d.path, GROUP_DIARY, d.text)
        for d in docs
        if d.kind == "emotion" and not d.path.endswith("/README.md")
        and d.text.strip()
    ]
    out.sort(key=lambda q: q.path)
    return out


# ---------------------------------------------------------------- 检索执行

class LexicalScorer:
    """词法臂：BM25，段-段相似度无定义。"""

    is_vector = False

    def __init__(self, segments: list[Segment]) -> None:
        self.index = Bm25([s.text for s in segments])

    def topk(self, query: str, k: int) -> list[tuple[int, float]]:
        return self.index.search(query, k)

    def segment_pair(self, a: int, b: int) -> float | None:
        return None


class VectorScorer:
    """向量臂：每个单元的分块向量，得分取分块对最大值。"""

    is_vector = True

    def __init__(self, unit_chunks: list[list[list[float]]],
                 embedder: Embedder, cache: dict[str, list[float]]) -> None:
        self.unit_chunks = unit_chunks
        self.embedder = embedder
        self.cache = cache

    def _vector_of(self, chunk: str) -> list[float]:
        v = self.cache.get(chunk)
        if v is None:
            v = self.embedder.embed_all([chunk])[0]
            self.cache[chunk] = v
        return v

    def vecs(self, text: str) -> list[list[float]]:
        return [self._vector_of(c) for c in embed_chunks(text)]

    def topk(self, query: str, k: int) -> list[tuple[int, float]]:
        qs = self.vecs(query)
        scored: list[tuple[int, float]] = []
        for i, us in enumerate(self.unit_chunks):
            best = max((cosine(q, u) for q in qs for u in us), default=0.0)
            if best > 0.0:
                scored.append((i, best))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:k]

    def segment_pair(self, a: int, b: int) -> float:
        ca, cb = self.unit_chunks[a], self.unit_chunks[b]
        return max((cosine(x, y) for x in ca for y in cb), default=0.0)


def _build_scorer(segments: list[Segment], queries: list[QueryDoc],
                  query_mode: str, embed: bool):
    if not embed:
        return LexicalScorer(segments), None
    embedder = Embedder.from_env()
    texts: list[str] = []
    seen: set[str] = set()

    def push(t: str) -> None:
        if t not in seen:
            seen.add(t)
            texts.append(t)

    for s in segments:
        for c in embed_chunks(s.text):
            push(c)
        for c in embed_chunks(first_sentence(s.text)):
            push(c)
    for q in queries:
        source = first_sentence(q.text) if query_mode == "first" else q.text
        for c in embed_chunks(source):
            push(c)
    print(f"嵌入 {len(texts)} 条唯一文本（{embedder.model}，段 {len(segments)} / "
          f"查询 {len(queries)}）…", file=sys.stderr)
    vectors = embedder.embed_all(texts)
    cache = dict(zip(texts, vectors))
    unit_chunks = [[cache[c] for c in embed_chunks(s.text)] for s in segments]
    return VectorScorer(unit_chunks, embedder, cache), embedder


def _preview(text: str) -> str:
    """展示用截断：80 字加省略号。"""
    chars = list(text)
    t = "".join(chars[:80])
    return t + "…" if len(chars) > 80 else t


def _top1_range(scores: list[float]) -> tuple[float, float]:
    if not scores:
        return 0.0, 0.0
    return min(scores), max(scores)


def print_report(r: dict) -> None:
    """汇总打印（与 Rust 版同版式）。"""
    def f(v):
        return "n/a" if v is None else f"{v:.2f}"

    print(f"\n打分器 {r['scorer']}，查询档 {r['query_mode']}，段 {r['segments']} / "
          f"查询 {r['queries']}（有标注 {r['labeled']}）")
    print(f"主指标 top-3 相关率 {f(r['p_at_3'])}（命中率 {f(r['hit_at_3_rate'])}，"
          f"随机基线 {r['random_baseline']:.2f}，自检 {r['self_retrieval_top3']:.2f}）")
    for g in r["groups"]:
        print(f"  {g['group']:<6} 查询 {g['queries']:>2}（标注 {g['labeled']:>2}） "
              f"相关率 {f(g['p_at_3'])} 命中率 {f(g['hit_at_3_rate'])}")
    if r["rules_applied"]:
        lo, hi = _top1_range(r["top1_scores"])
        print(f"出口规则：τ₁ 丢弃 {r['tau1_drops']} 对 / τ₂ 已写过 {r['tau2_hits']} 对；"
              f"top-1 分 {lo:.3}–{hi:.3}")
    else:
        print("出口规则不适用（τ₁/τ₂ 是余弦分阈值，词法分无上界）")
    for o in r["outcomes"]:
        rank = "—" if o["rank"] is None else str(o["rank"])
        flag = "" if o["labeled"] else "（无标注）"
        print(f"\n## [{o['group']}] {o['file']} {o['query']} {flag}")
        print(f"   首个相关段名次 {rank}，top-1 分 {o['top1_score']:.3f}")
        for i, h in enumerate(o["hits"]):
            mark = "*" if (o["hit_at_3"] and o["rank"] == i + 1) else " "
            label = _VERDICT_LABEL.get(h["verdict"] or "", "—")
            rel = " [相关]" if h["id"] in o["related_ids"] else ""
            print(f"  {i + 1}. {mark} {h['id']} | 行 {h['line']} | {h['date']} | "
                  f"{h['score']:.3f} | {label}{rel}")
        for g in o["lines"]:
            print(f"   情绪线（{len(g)} 段）: {' / '.join(g)}")


def run_emotion(assets: Path, related: Path, out: Path,
                query_mode: str = "full", embed: bool = False) -> dict:
    """跑联想评测：建索引 → 检索 → 出口规则 → 指标，返回报告并写 JSON。"""
    segments = load_segments(assets)
    queries = load_queries(assets)
    if not segments:
        raise RuntimeError("日志段为空")
    if not queries:
        raise RuntimeError("查询集为空")
    labels_set = load_related(related)

    scorer, _ = _build_scorer(segments, queries, query_mode, embed)

    outcomes = []
    top1_scores: list[float] = []
    tau2_hits = tau1_drops = 0
    labeled = hits_in_top3 = hit_queries = 0
    group_stat: dict[str, list[int]] = {}

    for q in queries:
        source = first_sentence(q.text) if query_mode == "first" else q.text
        found = scorer.topk(source, RETRIEVE_K)
        labels = labels_set.labels.get(q.path)
        is_labeled = labels is not None
        if is_labeled:
            labeled += 1

        rank = None
        hit_at_3 = False
        related_in_top3 = 0
        related_ids: list[str] = []
        for i, (idx, _) in enumerate(found):
            seg_id = segments[idx].id
            if labels is not None and labels.get(seg_id, False):
                related_ids.append(seg_id)
                if rank is None:
                    rank = i + 1
                if i + 1 <= TOP3:
                    hit_at_3 = True
                    related_in_top3 += 1
        if is_labeled:
            hits_in_top3 += related_in_top3
            if hit_at_3:
                hit_queries += 1

        hits = []
        for idx, s in found:
            v = verdict(s) if embed else None
            hits.append({
                "id": segments[idx].id,
                "line": f"{segments[idx].line_start}-{segments[idx].line_end}",
                "date": segments[idx].date,
                "score": s,
                "verdict": v,
            })
        for h in hits:
            if h["verdict"] == "already_written":
                tau2_hits += 1
            elif h["verdict"] == "below_floor":
                tau1_drops += 1

        top1 = hits[0]["score"] if hits else 0.0
        top1_scores.append(top1)

        # 聚线：只在通过 τ₁ 的提醒集合内做，且只在向量臂（分块相似度才有定义）
        remind_pos = [pos for pos, (_, s) in enumerate(found)
                      if embed and verdict(s) == "remind"]
        lines: list[list[str]] = []
        if scorer.is_vector and len(remind_pos) >= 2:
            groups = cluster_lines(len(remind_pos), lambda a, b: (
                scorer.segment_pair(found[remind_pos[a]][0],
                                    found[remind_pos[b]][0]) > LINE_TAU))
            lines = [[hits[remind_pos[p]]["id"] for p in g] for g in groups]

        entry = group_stat.setdefault(q.group, [0, 0, 0, 0])
        entry[0] += 1
        if is_labeled:
            entry[1] += 1
            entry[2] += related_in_top3
            entry[3] += 1 if hit_at_3 else 0

        outcomes.append({
            "file": q.path,
            "group": q.group,
            "query": _preview(source),
            "labeled": is_labeled,
            "rank": rank,
            "hit_at_3": hit_at_3,
            "top1_score": top1,
            "note": labels_set.notes.get(q.path),
            "related_ids": related_ids,
            "hits": hits,
            "lines": lines,
        })

    # 管线自检：每段首句查自身，应落在 top-3
    self_ok = 0
    for i, s in enumerate(segments):
        if any(idx == i for idx, _ in scorer.topk(first_sentence(s.text), TOP3)):
            self_ok += 1

    groups = []
    for group in sorted(group_stat):
        n, lb, rel, hit = group_stat[group]
        groups.append({
            "group": group,
            "queries": n,
            "labeled": lb,
            "p_at_3": rel / (TOP3 * lb) if lb else None,
            "hit_at_3_rate": hit / lb if lb else None,
        })

    report = {
        "scorer": "embed" if embed else "bm25",
        "query_mode": query_mode,
        "segments": len(segments),
        "queries": len(queries),
        "labeled": labeled,
        "p_at_3": hits_in_top3 / (TOP3 * labeled) if labeled else None,
        "hit_at_3_rate": hit_queries / labeled if labeled else None,
        "groups": groups,
        "random_baseline": TOP3 / len(segments),
        "self_retrieval_top3": self_ok / len(segments),
        "top1_scores": top1_scores,
        "tau2_hits": tau2_hits,
        "tau1_drops": tau1_drops,
        "rules_applied": embed,
        "rule": {"tau1": TAU1, "tau2": TAU2, "line_tau": LINE_TAU,
                 "retrieve_k": RETRIEVE_K, "top3": TOP3},
        "outcomes": outcomes,
    }

    print_report(report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n结果已写入 {out}")
    return report


def dump_segments(segments: list[Segment]) -> str:
    """段清单 JSON（供金标标注与复现用）。"""
    return json.dumps([s.to_dict() for s in segments], ensure_ascii=False, indent=2)
