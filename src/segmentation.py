"""切段：把记忆仓库的 Markdown 日志切成语义单元。

原本住在 novel-planner 的 searcher.py，抽到实验室 src/ 供各案例复用。
单元坐标 `path#index` + `line_start-line_end` + `date`，口径与 novel-planner 第二轮一致。
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# 单元再切的字数上限（非空白字符数），超出按段落续分。
MAX_UNIT_CHARS = 900


# 字符判定（切段与分词共用）

def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF
            or 0xF900 <= o <= 0xFAFF)


def _is_ascii_alnum(ch: str) -> bool:
    return ("0" <= ch <= "9" or "a" <= ch <= "z" or "A" <= ch <= "Z")


def _is_ascii_digit(ch: str) -> bool:
    return "0" <= ch <= "9"


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




def load_queries(assets: Path) -> list[QueryDoc]:
    """查询集（情绪日记）。"""
    return build_queries([d for d in load_docs(assets) if d.kind == "emotion"])
