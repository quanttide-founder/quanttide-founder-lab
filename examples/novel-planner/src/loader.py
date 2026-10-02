#!/usr/bin/env python3
"""本地文件读取 loader：把测试文本 markdown 读成结构化章节与账本候选。

不依赖网络；纯本地文件读取与解析。供 dual_library 的 ledger --backfill 使用。
"""
import re
from pathlib import Path

CHAPTER_RE = re.compile(r"^(#{1,2})\s*(.*)$")
ENTRY_RE = re.compile(r"^-\s*\[(fact|debt|inventory)\]\[([^\]]*)\]\s*(.*)$")


def load_test_text(path):
    """读取测试文本 markdown，返回 {"chapters": [...], "entries": [...]}。

    - 章节：行首 `# 第N章` 或 `## 章名` 视为新章，依次编号 c1/c2/...
    - 账本条：任意位置形如 `- [type][chapter] 内容` 的行解析为候选条目
      （type ∈ fact/debt/inventory），供回填账本。
    """
    text = Path(path).read_text(encoding="utf-8")
    chapters = []
    cur = None
    entries = []
    for ln in text.splitlines():
        m = CHAPTER_RE.match(ln.strip())
        if m:
            level, title = m.group(1), m.group(2).strip()
            if "章" in title or level == "##":
                cur = {"id": f"c{len(chapters) + 1}", "title": title, "text": ""}
                chapters.append(cur)
            continue
        em = ENTRY_RE.match(ln.strip())
        if em:
            entries.append({
                "type": em.group(1),
                "chapter_id": em.group(2),
                "content": em.group(3),
            })
            continue
        if cur is not None:
            cur["text"] += ln + "\n"
    return {"chapters": chapters, "entries": entries}


def load_text(path):
    """最朴素的本地读取：整文件按 UTF-8 读成字符串。"""
    return Path(path).read_text(encoding="utf-8")
