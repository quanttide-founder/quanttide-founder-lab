#!/usr/bin/env python3
"""图谱一致性校验：用记忆集的 `index.ttl` 当契约，校验 journal 与 profile 等组成部分是否对得上。

用法：
    uv run --with rdflib python3 src/graph_check.py [--set default] [--no-report]

检查项（只做机械判定，不碰档案、不改档案）：
    A 图谱结构：关系端点已声明、态射签名合法、节点都有 rdfs:label 与 pf:file。
    B 图谱 ↔ 档案：每个节点能在 pf:file 指向的档案里找到同名条目；
      档案里有、图里没有的标题/粗体条目单列（含分组标题，交人看）。
    C 图谱 ↔ 日志：每个节点的名字在日志段里逐字出现的段数；零命中的节点列出。
    D 关系 ↔ 日志：每条关系的两端在同一日志段里同现的次数；零证据的关系列出。

只读；产出 JSON（`data/<日期>-图谱一致性.json`）与报告（`data/report/<日期>-图谱一致性校验.md`）。
"""

from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # memory-factory/src
LAB = HERE.parents[2]                            # quanttide-founder-lab
sys.path.insert(0, str(LAB / "src"))

from segmentation import load_segments  # noqa: E402

try:
    from rdflib import Graph, Namespace, RDF, RDFS
except ImportError:  # pragma: no cover
    sys.exit("需要 rdflib：uv run --with rdflib python3 src/graph_check.py"
             "（或 pip install rdflib）")

REPO = LAB.parent.parent                         # quanttide-founder
ASSETS = REPO / "assets"
MEMORY = ASSETS / "memory"
CASE = HERE.parent                               # memory-factory
DATA = CASE / "data"
REPORT = DATA / "report"

# 档案文件 → 该文件里的条目应有的类（校验 kind 与 file 是否对得上）。
# 值与图里的类名（`pf:` 后的本地名）对应；一条档案可承载多个类。
FILE_KINDS = {
    "triggers.md": {"Trigger", "Method"},        # 方法也可出现为描述（刻意引导等）
    "emotions.md": {"Emotion"},
    "methods.md": {"Method"},
    "expressions.md": {"Expression"},
    "values.md": {"Value", "Anti"},
    "1_创作动机.md": {"Motivation"},
    "2_创作方法.md": {"Method"},
    "3_创作困境.md": {"Dilemma"},
}


def pf_namespace(g: Graph) -> Namespace:
    for prefix, uri in g.namespaces():
        if prefix == "pf":
            return Namespace(str(uri))
    raise SystemExit("index.ttl 里找不到 pf: 命名空间")


def load_graph(set_dir: Path) -> tuple[Graph, Namespace, dict, dict, dict]:
    """解析 index.ttl →（图, 命名空间, 节点→类, 关系→range, 关系→domain）。"""
    g = Graph()
    g.parse(set_dir / "index.ttl", format="turtle")
    pf = pf_namespace(g)
    classes = {s for s in g.subjects(RDFS.subClassOf, pf.Object)}
    node_kind = {s: c for c in classes for s in g.subjects(RDF.type, c)}
    relations: dict = {}
    domains: dict = {}
    for p in g.subjects(RDF.type, RDF.Property):
        rng = g.value(p, RDFS.range)
        if rng in classes:
            relations[p] = rng
            domains[p] = g.value(p, RDFS.domain)
    return g, pf, node_kind, relations, domains


def local(term) -> str:
    return str(term).rsplit(":", 1)[-1].rsplit("#", 1)[-1]


def parse_profile_entries(path: Path) -> list[tuple[str, str]]:
    """档案里的条目候选 → [(来源, 名称)]：`## `/`### ` 标题与 `- **X → Y**` 的 Y。

    标题里的分组名（思维框架等）也会出现，单列时注明，交人判断。
    """
    out: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").split("\n"):
        m = re.match(r"^#{2,6} (.+?)\s*$", line)
        if m:
            out.append(("标题", m.group(1)))
            continue
        b = re.match(r"^- \*\*(.+?)\*\*", line)
        if b:
            name = b.group(1).split("→")[-1].strip()
            out.append(("粗体条目", name))
    return out


def set_of(path: str) -> str:
    return path.split("/")[1]


def check_set(set_dir: Path, segments: list) -> dict:
    g, pf, node_kind, relations, domains = load_graph(set_dir)
    label = {s: str(g.value(s, RDFS.label) or "") for s in node_kind}
    file = {s: str(g.value(s, pf.file) or "") for s in node_kind}

    result: dict = {"集": set_dir.name, "节点数": len(node_kind),
                    "关系数": sum(1 for p in relations for _ in g.subject_objects(p))}

    # ---- A 图谱结构
    structure = {"端点未声明": [], "签名违例": [], "缺 label": [], "缺 file": []}
    for p, rng in relations.items():
        dom = domains.get(p)
        for s, o in g.subject_objects(p):
            if s not in node_kind or o not in node_kind:
                structure["端点未声明"].append(f"{local(s)} -{local(p)}-> {local(o)}")
                continue
            if dom is not None and node_kind[s] != dom:
                structure["签名违例"].append(
                    f"{label[s]} 起点应为 {local(dom)}，实为 {node_kind[s]}")
            if node_kind[o] != rng:
                structure["签名违例"].append(
                    f"{label[o]} 终点应为 {local(rng)}，实为 {node_kind[o]}")
    for s in node_kind:
        if not label[s]:
            structure["缺 label"].append(local(s))
        if not file[s]:
            structure["缺 file"].append(label[s] or local(s))

    # ---- B 图谱 ↔ 档案
    profile_dir = set_dir / "profile"
    file_text: dict[str, str] = {}
    for fn in {file[s] for s in node_kind if file[s]}:
        p = profile_dir / fn
        file_text[fn] = p.read_text(encoding="utf-8") if p.exists() else ""
    ghost, kind_mismatch, missing_file = [], [], []
    for s in node_kind:
        fn = file[s]
        if not fn or not (profile_dir / fn).exists():
            missing_file.append(f"{label[s]}（{fn or '未填 file'}）")
            continue
        if label[s] and label[s] not in file_text[fn]:
            ghost.append(f"{label[s]}（{fn}）")
        allowed = FILE_KINDS.get(fn)
        if allowed and local(node_kind[s]) not in allowed:
            kind_mismatch.append(
                f"{label[s]}：{local(node_kind[s])} 不应出现在 {fn}")

    registered = set(label.values())
    unregistered = []
    for fn in sorted({file[s] for s in node_kind if file[s]}):
        for source, name in parse_profile_entries(profile_dir / fn):
            if name not in registered:
                unregistered.append(f"{fn}｜{source}｜{name}")

    # ---- C 图谱 ↔ 日志
    covered, uncovered_nodes = {}, []
    node_hits = {s: 0 for s in node_kind}
    seg_hits = {seg.id: 0 for seg in segments}
    for seg in segments:
        for s in node_kind:
            if label[s] and label[s] in seg.text:
                node_hits[s] += 1
                seg_hits[seg.id] += 1
    for s in node_kind:
        if node_hits[s] == 0:
            uncovered_nodes.append(label[s])
        else:
            covered[label[s]] = node_hits[s]
    zero_segments = [seg.id for seg in segments if seg_hits[seg.id] == 0]

    # ---- D 关系 ↔ 日志
    zero_rel, rel_evidence = [], {}
    for p, rng in relations.items():
        for s, o in g.subject_objects(p):
            n = sum(1 for seg in segments
                    if label.get(s) and label.get(o)
                    and label[s] in seg.text and label[o] in seg.text)
            key = f"{label.get(s, local(s))} -{local(p)}-> {label.get(o, local(o))}"
            rel_evidence[key] = n
            if n == 0:
                zero_rel.append(key)

    result.update({
        "A_图谱结构": structure,
        "B_图谱↔档案": {"幽灵节点": ghost, "类与档案不符": kind_mismatch,
                        "file 不存在": missing_file, "档案里未登记的条目": unregistered},
        "C_图谱↔日志": {"日志段数": len(segments),
                        "有日志证据的节点数": len(covered),
                        "零命中节点": uncovered_nodes,
                        "零命中日志段数": len(zero_segments),
                        "零命中日志段": zero_segments},
        "D_关系↔日志": {"零证据关系": zero_rel,
                        "关系证据段数": rel_evidence},
    })
    return result


def find_sets(only: str | None) -> list[Path]:
    out = [p for p in sorted(MEMORY.iterdir())
           if p.is_dir() and (p / "index.ttl").exists()]
    return [p for p in out if only is None or p.name == only]


def _bullets(items: list[str], cap: int = 12) -> list[str]:
    out = [f"- {x}" for x in items[:cap]]
    if len(items) > cap:
        out.append(f"- ……共 {len(items)} 条，全量见 JSON")
    return out


def build_report(results: list[dict], date: str) -> str:
    lines = [f"# 图谱一致性校验（{date}）", "",
             "## 意图", "",
             "把每个记忆集的 `index.ttl` 当契约，校验 journal 与 profile 是否与它对得上。"
             "只做机械判定：图谱结构、条目登记、日志证据。判不出的（意义、矛盾）留给人。",
             "", "## 结果", ""]
    for r in results:
        a, b, c, d = (r["A_图谱结构"], r["B_图谱↔档案"],
                      r["C_图谱↔日志"], r["D_关系↔日志"])
        n_struct = sum(len(v) for v in a.values())
        n_mismatch = (len(b["幽灵节点"]) + len(b["类与档案不符"])
                      + len(b["file 不存在"]) + n_struct)
        lines += [f"### {r['集']}（{r['节点数']} 节点 / {r['关系数']} 条边）", "",
                  f"**图谱与档案对不上的有 {n_mismatch} 处。**" if n_mismatch
                  else "**图谱与档案全部对得上。**", ""]
        for title, items in (
                ("幽灵节点（图里有、档案里找不到同名条目）", b["幽灵节点"]),
                ("类与档案不符", b["类与档案不符"]),
                ("file 不存在", b["file 不存在"]),
                ("签名违例", a["签名违例"]),
                ("端点未声明", a["端点未声明"]),
                ("缺 label", a["缺 label"]), ("缺 file", a["缺 file"])):
            if items:
                lines += [f"**{title}**"] + _bullets(items) + [""]
        lines += [
            f"日志证据（逐字）：{r['节点数']} 个节点里 "
            f"{c['有日志证据的节点数']} 个在日志里逐字出现"
            f"（有 {len(c['零命中节点'])} 个没有）；"
            f"{c['零命中日志段数']}/{c['日志段数']} 段日志没命中任何节点名。",
            f"关系证据（两端逐字同段）：{len(d['关系证据段数'])} 条关系里 "
            f"{len(d['零证据关系'])} 条没有。", ""]
        if b["档案里未登记的条目"]:
            lines += ["**档案里未登记的条目（含分组标题，交人看）**"] \
                + _bullets(b["档案里未登记的条目"]) + [""]
    lines += [
        "## 边界", "",
        "- 日志与关系证据用**节点名逐字出现**判定：命中即真；未命中不等于档案缺口"
        "（档案是蒸馏，措辞会变），只作提示。",
        "- 「档案里未登记的条目」按 `## `/`### ` 标题与 `- **X → Y**` 的 Y 收，"
        "分组标题（思维框架、应对策略）也在其中，需要人剔。",
        "- 校验只认 `index.ttl` 声称的结构；图谱本身写错，校验查不出来。",
        "", "## 下一步", "",
        "- 幽灵节点与类不符，按「图谱对齐档案」还是「档案改口径」逐条定；"
        "这次只报不改。",
        "- 零逐字证据的节点与关系，人工过一遍是措辞差异还是真缺口。",
        "- 若要覆盖「关系在档案里有出处」，补收 `methods.md` 的「适用条件」"
        "与 `values.md` 的「这条线排除的是」。",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    argv = sys.argv[1:]
    only = None
    if "--set" in argv:
        only = argv[argv.index("--set") + 1]
    no_report = "--no-report" in argv

    sets = find_sets(only)
    if not sets:
        sys.exit("没有找到带 index.ttl 的记忆集")

    all_segments = load_segments(ASSETS)
    results = []
    for set_dir in sets:
        segs = [s for s in all_segments
                if set_of(s.path) == set_dir.name and not s.path.endswith("/index.md")]
        r = check_set(set_dir, segs)
        results.append(r)
        print(f"[{r['集']}] 节点 {r['节点数']} / 关系 {len(r['D_关系↔日志']['关系证据段数'])}"
              f" | 幽灵 {len(r['B_图谱↔档案']['幽灵节点'])}"
              f" | 零日志证据节点 {len(r['C_图谱↔日志']['零命中节点'])}")

    date = datetime.date.today().isoformat()
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / f"{date}-图谱一致性.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if not no_report:
        REPORT.mkdir(parents=True, exist_ok=True)
        (REPORT / f"{date}-图谱一致性校验.md").write_text(
            build_report(results, date), encoding="utf-8")
        print(f"报告：data/report/{date}-图谱一致性校验.md")


if __name__ == "__main__":
    main()
