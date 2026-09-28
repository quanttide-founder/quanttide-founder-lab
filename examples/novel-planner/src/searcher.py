#!/usr/bin/env python3
"""检索引擎：从 memory 原始日志段里为加工中的创作素材捞出相关片段。

移植自 knowl-searcher 第二轮（Rust 实现），切分、打分、裁决与评测口径不变：

- 索引：`assets/memory` 各集日志按 `---` 切会话段（标题行也是边界，超 900 字按段续分），
  一段 = 一个检索单元，坐标 `path#index` + `line_start-line_end` + `date`
- 查询：加工侧的情绪日记草稿（`--query full` 全文为业务主档，`first` 首句作对照）
- 打分：`bm25` 离线词法臂；`embed` 分块嵌入臂（480 字一窗、重叠 80 字，
  查询与段的相似度取分块对的最大值）
- 裁决：τ₁ 共振下限、τ₂ 重复上限、聚线（段间相似度单链接）——规则只在出口生效，
  且 τ 是余弦分阈值，词法臂 verdict 记 null
- 评判：top-3 相关率（主指标），金标 `data/related/*.json`，预注册参数不回调参

中间数据一律 JSON，程序不读写文档。实验设计与结果见 docs/experiment.md。
"""

from __future__ import annotations

import json
import math
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# 预注册参数：跑完标定，不回调参。
TAU1 = 0.55
TAU2 = 0.92
LINE_TAU = 0.7
RETRIEVE_K = 8
TOP3 = 3
GROUP_DIARY = "情绪日记"

# 嵌入分块：窗口 480 字（保守低于 512 token 上限），步长 = 窗口 - 80。
EMBED_CHARS = 480
CHUNK_STEP = EMBED_CHARS - 80
# 单元再切的字数上限（非空白字符数），超出按段落续分。
MAX_UNIT_CHARS = 900
# BM25 参数
K1 = 1.2
B = 0.75


# ---------------------------------------------------------------- 语料与单元

@dataclass(frozen=True)
class Doc:
    """一个语料文件：路径相对 `assets/`，`/` 分隔。"""

    path: str
    kind: str  # journal | emotion
    text: str


@dataclass(frozen=True)
class Unit:
    """结构化检索单元：带标题与 1-based 行区间（含端）。"""

    path: str
    kind: str
    title: str
    line_start: int
    line_end: int
    text: str

    def coord(self) -> str:
        return f"{self.kind}:{self.line_start}-{self.line_end}"


@dataclass(frozen=True)
class Segment:
    """日志检索单元：`path#index`，index 从 1 起，同文件内按行序。"""

    id: str
    path: str
    index: int
    line_start: int
    line_end: int
    date: str
    text: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "path": self.path,
            "index": self.index,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "date": self.date,
            "text": self.text,
        }


@dataclass(frozen=True)
class QueryDoc:
    """一条查询：加工侧的情绪日记。"""

    path: str
    group: str
    text: str


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path).replace("\\", "/")


def load_docs(assets: Path) -> list[Doc]:
    """装载联想检索用到的两层语料：

    - `journal`：memory 各集的原始日志（集根与 `journal/` 下的 md，README 不算）
    - `emotion`：fiction 观察站的情绪日记（查询侧）

    `intention`、`report` 等尚未建模的层不进语料。
    """
    docs: list[Doc] = []
    memory = assets / "memory"
    if memory.is_dir():
        for set_dir in sorted(p for p in memory.iterdir() if p.is_dir()):
            if set_dir.name.startswith("."):
                continue
            for folder in (set_dir, set_dir / "journal"):
                if not folder.is_dir():
                    continue
                for f in sorted(folder.glob("*.md")):
                    if f.name == "README.md":
                        continue
                    docs.append(Doc(_rel(f, assets), "journal",
                                    f.read_text(encoding="utf-8")))
    for f in sorted(assets.glob("fiction/*/1_情绪日记/*.md")):
        if f.name == "README.md":
            continue
        docs.append(Doc(_rel(f, assets), "emotion", f.read_text(encoding="utf-8")))

    docs.sort(key=lambda d: d.path)
    out: list[Doc] = []
    for d in docs:
        if not out or out[-1].path != d.path:
            out.append(d)
    return out


def heading_level(line: str) -> tuple[int, str] | None:
    """标题行 →（层级, 文本）；`#` 后必须有空格才算标题。"""
    hashes = 0
    for ch in line:
        if ch == "#":
            hashes += 1
        else:
            break
    if hashes == 0 or hashes > 6 or hashes >= len(line):
        return None
    rest = line[hashes:]
    if not rest.startswith(" "):
        return None
    return hashes, rest[1:].strip()


def is_separator(line: str) -> bool:
    """`---` / `--` 类会话分隔线（与工具箱 `JournalEntry::segments` 同语义）。"""
    t = line.strip()
    return len(t) >= 2 and all(c == "-" for c in t)


def _non_ws_count(text: str) -> int:
    return sum(1 for c in text if not c.isspace())


def build_units(docs: list[Doc]) -> list[Unit]:
    """把语料切成单元：标题行与 `---` 分隔线是边界，超长单元按段落续分。"""
    units: list[Unit] = []
    for doc in docs:
        stem = doc.path.rsplit("/", 1)[-1]
        while stem.endswith(".md"):
            stem = stem[:-3]
        lines = doc.text.split("\n")
        heading: str | None = None
        h2: str | None = None
        start = 1
        buf: list[str] = []

        for i, line in enumerate(lines):
            ln = i + 1
            heading_at = heading_level(line)
            sep = is_separator(line)

            if (heading_at is not None or sep) and buf:
                _push_unit(units, doc, stem, heading, h2, start, ln - 1, buf)
                buf = []

            if heading_at is not None:
                level, text = heading_at
                heading = text
                if level == 2:
                    h2 = heading
                start = ln
                buf.append(line)
                continue
            if sep:
                # 分隔线之后是新的会话段，标题回退到文件名
                heading = None
                h2 = None
                start = ln + 1
                continue
            if not buf:
                start = ln
            buf.append(line)
        if buf:
            _push_unit(units, doc, stem, heading, h2, start, len(lines), buf)
    return units


def _push_unit(out: list[Unit], doc: Doc, stem: str, heading: str | None,
               h2: str | None, line_start: int, line_end: int,
               lines: list[str]) -> None:
    text = "\n".join(lines).strip()
    if not text:
        return
    title = heading if heading is not None else stem
    if _non_ws_count(text) <= MAX_UNIT_CHARS:
        out.append(Unit(doc.path, doc.kind, title, line_start, line_end, text))
        return

    # 超长单元按段落打包，保持行号
    parts: list[tuple[int, int, list[str]]] = []
    cur_lines: list[str] = []
    cur_chars = 0
    cur_start = line_start
    for j, line in enumerate(lines):
        wide = _non_ws_count(line)
        if cur_chars + wide > MAX_UNIT_CHARS and cur_lines:
            parts.append((cur_start, line_start + j - 1, cur_lines))
            cur_chars = 0
            cur_start = line_start + j
            cur_lines = []
        cur_lines.append(line)
        cur_chars += wide
    if cur_lines:
        parts.append((cur_start, line_end, cur_lines))

    multi = len(parts) > 1
    for idx, (s, e, ls) in enumerate(parts):
        t = "\n".join(ls).strip()
        if not t:
            continue
        part_title = f"{title}（段{idx + 1}）" if multi else title
        out.append(Unit(doc.path, doc.kind, part_title, s, e, t))


def date_of(path: str) -> str:
    """取路径文件名（去扩展名）作为日期，形如 `2026-09-23` 才算数。"""
    stem = path.rsplit("/", 1)[-1]
    while stem.endswith(".md"):
        stem = stem[:-3]
    if len(stem) == 10 and all(
        (_is_ascii_digit(c) if i not in (4, 7) else c == "-")
        for i, c in enumerate(stem)
    ):
        return stem
    return ""


def segments_from_docs(docs: list[Doc]) -> list[Segment]:
    """语料文件 → 检索单元（保序：同文件按行序编号）。"""
    counter: dict[str, int] = {}
    out: list[Segment] = []
    for u in build_units(docs):
        counter[u.path] = counter.get(u.path, 0) + 1
        n = counter[u.path]
        out.append(Segment(
            id=f"{u.path}#{n}", path=u.path, index=n,
            line_start=u.line_start, line_end=u.line_end,
            date=date_of(u.path), text=u.text,
        ))
    return out


def load_segments(assets: Path) -> list[Segment]:
    """全部集的原始日志段（语料 = memory 的 `journal` 层）。"""
    docs = [d for d in load_docs(assets)
            if d.kind == "journal" and d.path.startswith("memory/")]
    return segments_from_docs(docs)


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


def load_queries(assets: Path) -> list[QueryDoc]:
    """查询集（情绪日记）。"""
    return build_queries([d for d in load_docs(assets) if d.kind == "emotion"])


# ---------------------------------------------------------------- 文本处理

def first_sentence(text: str) -> str:
    """首句：到第一个句末标点或换行为止；为空则取前 40 字。"""
    out = []
    for ch in text:
        if ch in ("。", "！", "？", "!", "?", "\n"):
            break
        out.append(ch)
    t = "".join(out).strip().rstrip("，、,")
    if not t:
        return "".join(list(text.strip())[:40])
    return t


def embed_chunks(text: str) -> list[str]:
    """长文本分块：窗口 [`EMBED_CHARS`]、步长 [`CHUNK_STEP`]，不足一窗则单块。"""
    chars = list(text)
    if not chars:
        return []
    out: list[str] = []
    start = 0
    while start < len(chars):
        end = min(start + EMBED_CHARS, len(chars))
        chunk = "".join(chars[start:end])
        if chunk.strip():
            out.append(chunk)
        if end == len(chars):
            break
        start += CHUNK_STEP
    return out


# ---------------------------------------------------------------- 打分器

def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF
            or 0xF900 <= o <= 0xFAFF)


def _is_ascii_alnum(ch: str) -> bool:
    return ("0" <= ch <= "9" or "a" <= ch <= "z" or "A" <= ch <= "Z")


def _is_ascii_digit(ch: str) -> bool:
    return "0" <= ch <= "9"


def tokenize(text: str) -> list[str]:
    """切词：ASCII 词小写化，CJK 连串出一元与二元。"""
    out: list[str] = []
    ascii_buf: list[str] = []
    cjk_buf: list[str] = []

    def flush_ascii() -> None:
        if ascii_buf:
            out.append("".join(ascii_buf))
            ascii_buf.clear()

    def flush_cjk() -> None:
        for i, ch in enumerate(cjk_buf):
            out.append(ch)
            if i + 1 < len(cjk_buf):
                out.append(ch + cjk_buf[i + 1])
        cjk_buf.clear()

    for ch in text:
        if _is_ascii_alnum(ch):
            flush_cjk()
            ascii_buf.append(ch.lower())
        elif _is_cjk(ch):
            flush_ascii()
            cjk_buf.append(ch)
        else:
            flush_ascii()
            flush_cjk()
    flush_ascii()
    flush_cjk()
    return out


class Bm25:
    """BM25 索引（构建后只读；检索结果按（分数降序, 序号升序）排序，同输入必同输出）。"""

    def __init__(self, docs: list[str]) -> None:
        self.doc_len: list[int] = []
        self.df: dict[str, int] = {}
        self.tfs: list[dict[str, int]] = []
        for text in docs:
            tokens = tokenize(text)
            self.doc_len.append(len(tokens))
            tf: dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            for key in tf:
                self.df[key] = self.df.get(key, 0) + 1
            self.tfs.append(tf)
        total = sum(self.doc_len)
        self.avg_dl = 1.0 if not self.doc_len else total / len(self.doc_len)
        self.n = float(len(self.doc_len))

    def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        qtf: dict[str, int] = {}
        for t in tokenize(query):
            qtf[t] = qtf.get(t, 0) + 1
        # 查询词排序后遍历：浮点求和顺序固定，同输入必同输出
        terms = sorted(qtf.items())
        scored: list[tuple[int, float]] = []
        for i, tf in enumerate(self.tfs):
            score = 0.0
            for term, q_count in terms:
                f = tf.get(term)
                if f is None:
                    continue
                df = float(self.df.get(term, 0))
                idf = math.log((self.n - df + 0.5) / (df + 0.5) + 1.0)
                tf_v = float(f)
                denom = tf_v + K1 * (1.0 - B + B * self.doc_len[i] / self.avg_dl)
                score += idf * (tf_v * (K1 + 1.0) / denom) * float(q_count)
            if score > 0.0:
                scored.append((i, score))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:top_k]


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class Embedder:
    """OpenAI 兼容的 embeddings 端点。

    环境变量 `KNOWL_EMBED_BASE_URL` / `KNOWL_EMBED_API_KEY` / `KNOWL_EMBED_MODEL`，
    缺 key 时回退 `GLM_API_KEY` + 智谱 `embedding-3`。
    """

    def __init__(self, base: str, key: str, model: str) -> None:
        self.base = base.rstrip("/")
        self.key = key
        self.model = model

    @classmethod
    def from_env(cls) -> "Embedder":
        import os
        base = os.environ.get("KNOWL_EMBED_BASE_URL",
                              "https://open.bigmodel.cn/api/paas/v4")
        key = os.environ.get("KNOWL_EMBED_API_KEY") or os.environ.get("GLM_API_KEY")
        if not key:
            raise RuntimeError("缺少 embedding key：设置 KNOWL_EMBED_API_KEY 或 GLM_API_KEY")
        model = os.environ.get("KNOWL_EMBED_MODEL", "embedding-3")
        return cls(base, key, model)

    def embed_all(self, texts: list[str]) -> list[list[float]]:
        """批量嵌入（每批 16 条），进度打到 stderr。"""
        out: list[list[float]] = []
        for i in range(0, len(texts), 16):
            batch = texts[i:i + 16]
            body = json.dumps({"model": self.model, "input": batch}).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base}/embeddings",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.key}",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    payload = json.load(resp)
            except urllib.error.URLError as e:
                raise RuntimeError(f"嵌入请求失败: {e}") from e
            data = payload.get("data")
            if not isinstance(data, list):
                raise RuntimeError("embedding 响应缺少 data")
            for item in sorted(data, key=lambda v: v.get("index", 0)):
                vec = item.get("embedding")
                if not isinstance(vec, list):
                    raise RuntimeError("embedding 响应缺少 embedding")
                out.append([float(x) for x in vec])
            print(f"\r  已嵌入 {len(out)}/{len(texts)} 条", end="", file=sys.stderr)
        print(file=sys.stderr)
        if len(out) != len(texts):
            raise RuntimeError(f"嵌入数量不符：{len(out)} != {len(texts)}")
        return out


# ---------------------------------------------------------------- 出口裁决

def verdict(score: float) -> str:
    """出口裁决：低于 τ₁ 丢弃，高于 τ₂ 标「已写过」，其余提醒。"""
    if score < TAU1:
        return "below_floor"
    if score > TAU2:
        return "already_written"
    return "remind"


_VERDICT_LABEL = {"below_floor": "BelowFloor", "remind": "Remind",
                  "already_written": "AlreadyWritten"}


def cluster_lines(n: int, similar) -> list[list[int]]:
    """聚线：候选两两相似度过 [`LINE_TAU`] 单链接成情绪线；少于 2 个成员不构成线。"""
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if similar(i, j):
                a, b = find(i), find(j)
                if a != b:
                    parent[a] = b
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out = sorted(g for g in groups.values() if len(g) >= 2)
    return out


# ---------------------------------------------------------------- 相关性标注

@dataclass
class RelatedSet:
    """相关性标注：查询路径 →（段 id → 是否相关），外加逐查询的标注理由。"""

    labels: dict[str, dict[str, bool]] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)


def merge_related(raws: list[str]) -> RelatedSet:
    """合并若干标注分片（分片间同键同值才允许重复，冲突即报错）。"""
    merged = RelatedSet()
    for raw in raws:
        part = json.loads(raw)
        for q, labels in part["related"].items():
            existing = merged.labels.setdefault(q, {})
            for seg_id, v in labels.items():
                if seg_id in existing:
                    if existing[seg_id] != v:
                        raise ValueError(f"标注冲突: {q} {seg_id}")
                else:
                    existing[seg_id] = v
        for q, note in part.get("notes", {}).items():
            merged.notes.setdefault(q, note)
    return merged


def load_related(path: Path) -> RelatedSet:
    """载入标注：路径是目录则读其中全部 `*.json` 并合并，是文件则读单个。"""
    if path.is_dir():
        parts = sorted(p for p in path.iterdir() if p.suffix == ".json")
        return merge_related([p.read_text(encoding="utf-8") for p in parts])
    return merge_related([path.read_text(encoding="utf-8")])


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
