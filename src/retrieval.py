"""检索：词法与语义两臂打分，以及相关性集合的读写。

原本住在 novel-planner 的 searcher.py，抽到实验室 src/ 供各案例复用。
bm25 离线词法臂 + 分块嵌入臂（480 字窗口、重叠 80 字，取分块对最大相似度）。
"""

from __future__ import annotations

from segmentation import _is_ascii_alnum, _is_ascii_digit, _is_cjk

import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# 预注册参数：跑完标定，不回调参。
TAU1 = 0.55
TAU2 = 0.92
LINE_TAU = 0.7

# 嵌入分块：窗口 480 字（保守低于 512 token 上限），步长 = 窗口 - 80。
EMBED_CHARS = 480
CHUNK_STEP = EMBED_CHARS - 80
# BM25 参数
K1 = 1.2
B = 0.75


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
