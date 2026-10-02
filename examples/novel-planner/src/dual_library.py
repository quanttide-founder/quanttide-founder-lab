#!/usr/bin/env python3
"""双库生成控制：风格规则库 + 情节账本。

骨架（不可调，见 docs/dual-library.md §3 invariant）：
- 四组对照 A/B/C/D 必须齐全
- 上下文顺序 STYLE 全量 / STATE·DEBTS 切片 / SCENE 手动
- 回流不能跨库：事实问题不能写成风格规则，审美不能写进账本
- 启用规则上限 12 条
- 标注在生成后做

旋钮（可调）：data/dual-library/config.json。

本模块只做：上下文组装、生成快照、偏差标注（计数/摘录）、
回流（带跨库护栏）、指标与报告。生成文本本身由外部 LLM 或人工填，
本模块不调用任何模型，保证离线可跑、可测。
"""
import argparse
import json
import sys
from pathlib import Path

import loader

MAX_ENABLED_RULES = 12
GROUPS = ("A", "B", "C", "D")
DEVIATION_TYPES = ("style", "fact", "fabrication")

DATA = Path(__file__).resolve().parent.parent / "data" / "dual-library"
SNAP_DIR = DATA / "snapshots"


# ---------- 读写 ----------
def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load_rules(path=None):
    return load_json(path or DATA / "rules.json")


def load_ledger(path=None):
    return load_json(path or DATA / "ledger.json")


def load_config(path=None):
    return load_json(path or DATA / "config.json")


# ---------- 规则 ----------
def enabled_rules(rules):
    """返回启用规则；超 12 条直接抛错（骨架护栏）。"""
    en = [r for r in rules["rules"] if r.get("status") == "启用"]
    if len(en) > MAX_ENABLED_RULES:
        raise ValueError(
            f"启用规则 {len(en)} 超过上限 {MAX_ENABLED_RULES}（骨架不可破）"
        )
    return en


def add_candidate_rule(rules, instruction, source="回流"):
    """新增候选规则，状态固定为「观察」，绝不自动启用。"""
    n = len(rules["rules"]) + 1
    rid = f"R{n:03d}"
    rules["rules"].append({
        "rule_id": rid,
        "instruction": instruction,
        "type": "禁用",
        "status": "观察",
        "source": source,
        "hit_count": 0,
    })
    return rid


# ---------- 账本 ----------
def unresolved_debts(ledger):
    return [e for e in ledger["entries"]
            if e.get("type") == "debt" and not e.get("resolved")]


def facts_for_chapter(ledger, chapter_id=None, keywords=None):
    facts = [e for e in ledger["entries"] if e.get("type") == "fact"]
    if chapter_id:
        facts = [e for e in facts if e.get("chapter_id") == chapter_id]
    if keywords:
        kws = keywords if isinstance(keywords, list) else [keywords]
        facts = [e for e in facts
                 if any(k in e.get("content", "") for k in kws)]
    return facts


# ---------- 上下文组装（顺序固定）----------
def assemble_context(rules, ledger, scene, chapter_id=None, keywords=None):
    style = "\n".join(f"- {r['instruction']}" for r in enabled_rules(rules))
    state = "\n".join(
        f"- [{e['entry_id']}] {e['content']}"
        for e in facts_for_chapter(ledger, chapter_id, keywords)
    )
    debts = "\n".join(
        f"- [{e['entry_id']}] {e['content']}"
        for e in unresolved_debts(ledger)
    )
    scene_goal = scene.get("goal", "")
    scene_constraints = scene.get("constraints", "")
    blocks = {
        "STYLE": style or "（无启用规则）",
        "STATE": state or "（无相关事实）",
        "DEBTS": debts or "（无未解欠账）",
        "SCENE": f"目标：{scene_goal}\n禁止项：{scene_constraints}",
    }
    prompt = (
        "【STYLE】\n{STYLE}\n\n"
        "【STATE】\n{STATE}\n\n"
        "【DEBTS】\n{DEBTS}\n\n"
        "【SCENE】\n{SCENE}"
    ).format(**blocks)
    return {"blocks": blocks, "prompt": prompt}


# ---------- 生成快照（不调用模型）----------
def make_snapshot(group, context, generated_text="", model="manual", params=None):
    if group not in GROUPS:
        raise ValueError(f"组别 {group} 不在 {GROUPS}（四组对照不可少）")
    return {
        "group": group,
        "context_blocks": context["blocks"],
        "prompt": context["prompt"],
        "generated_text": generated_text,
        "model": model,
        "params": params or {},
        "length": len(generated_text),
    }


# ---------- 偏差标注 ----------
def record_deviation(deviations, group, dtype, count=1, snippet="",
                     rule_or_fact_involved=""):
    if dtype not in DEVIATION_TYPES:
        raise ValueError(f"偏差类型 {dtype} 不在 {DEVIATION_TYPES}")
    if group not in GROUPS:
        raise ValueError(f"组别 {group} 不在 {GROUPS}")
    rec = deviations.setdefault("records", [])
    rec.append({
        "deviation_id": f"D{len(rec) + 1:03d}",
        "group": group,
        "type": dtype,
        "count": count,
        "snippet": snippet,
        "rule_or_fact_involved": rule_or_fact_involved,
        "severity": "",
        "suggested_action": "",
    })


# ---------- 回流（带跨库护栏）----------
def backflow(deviations, rules, ledger, allow_fact_as_rule=False):
    """应用回流。事实问题若试图写成规则，直接抛错（骨架护栏）。"""
    for d in deviations.get("records", []):
        if d["type"] == "style":
            rid = d.get("rule_or_fact_involved")
            if rid:
                for r in rules["rules"]:
                    if r["rule_id"] == rid and r["status"] == "启用":
                        r["hit_count"] += d.get("count", 1)
            else:
                add_candidate_rule(
                    rules,
                    f"未覆盖风格问题：{d.get('snippet', '')[:40]}",
                )
        elif d["type"] == "fact":
            if allow_fact_as_rule:
                raise RuntimeError(
                    "回流跨库：事实问题不能写成风格规则（骨架不可破）"
                )
            ledger["entries"].append({
                "entry_id": f"E{len(ledger['entries']) + 1:03d}",
                "type": "fact",
                "chapter_id": "",
                "content": d.get("snippet", ""),
                "resolved": False,
                "tags": ["backfill"],
            })
        elif d["type"] == "fabrication":
            pend = ledger.setdefault("pending", [])
            pend.append({
                "entry_id": f"P{len(pend) + 1:03d}",
                "content": d.get("snippet", ""),
                "status": "待确认",
            })


# ---------- 指标 ----------
def compute_metrics(deviations, snapshots):
    recs = deviations.get("records", [])
    total_words = sum(s.get("length", 0) for s in snapshots) or 1
    per_type = {t: 0 for t in DEVIATION_TYPES}
    for d in recs:
        per_type[d["type"]] += d.get("count", 1)
    per_1k = {t: round(per_type[t] / total_words * 1000, 3)
              for t in DEVIATION_TYPES}
    return {
        "total_deviations": sum(per_type.values()),
        "per_type": per_type,
        "per_1k_words": per_1k,
        "total_words": total_words,
    }


# ---------- CLI ----------
def cmd_rules(argv):
    p = argparse.ArgumentParser(prog="planner rules")
    p.add_argument("--path", type=Path, default=DATA / "rules.json")
    a = p.parse_args(argv)
    rules = load_rules(a.path)
    en = enabled_rules(rules)
    print(f"规则总数 {len(rules['rules'])}，启用 {len(en)}（上限 {MAX_ENABLED_RULES}）")
    for r in rules["rules"]:
        print(f"  [{r['status']}] {r['rule_id']} {r['instruction'][:30]} "
              f"hit={r['hit_count']}")


def cmd_ledger(argv):
    p = argparse.ArgumentParser(prog="planner ledger")
    p.add_argument("--path", type=Path, default=DATA / "ledger.json")
    p.add_argument("--backfill", action="store_true",
                   help="从 source/测试文本.md 解析结构化账本条并回填")
    p.add_argument("--source", type=Path,
                   default=DATA / "source" / "测试文本.md")
    a = p.parse_args(argv)
    ledger = load_ledger(a.path)
    if a.backfill:
        parsed = loader.load_test_text(a.source)
        existing = {e["content"] for e in ledger["entries"]}
        added = 0
        for e in parsed["entries"]:
            if e["content"] in existing:
                continue
            ledger["entries"].append({
                "entry_id": f"E{len(ledger['entries']) + 1:03d}",
                "type": e["type"],
                "chapter_id": e["chapter_id"],
                "content": e["content"],
                "resolved": False,
                "tags": ["backfill"],
            })
            added += 1
        save_json(a.path, ledger)
        print(f"回填 {added} 条（来源 {a.source.name}，已存在跳过）；"
              f"账本现 {len(ledger['entries'])} 条，"
              f"解析到章节 {len(parsed['chapters'])} 个")
        return
    ents = ledger["entries"]
    by = {}
    for e in ents:
        by[e.get("type")] = by.get(e.get("type"), 0) + 1
    print(f"账本条目 {len(ents)}：{by}")
    print(f"未解欠账 {len(unresolved_debts(ledger))}；待确认池 {len(ledger.get('pending', []))}")


def cmd_generate(argv):
    p = argparse.ArgumentParser(prog="planner generate")
    p.add_argument("group", choices=GROUPS)
    p.add_argument("--scene", type=Path,
                   help="JSON：{goal, constraints, chapter_id?, keywords?}")
    p.add_argument("--rules", type=Path, default=DATA / "rules.json")
    p.add_argument("--ledger", type=Path, default=DATA / "ledger.json")
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args(argv)
    scene = load_json(a.scene) if a.scene else {"goal": "", "constraints": ""}
    ctx = assemble_context(load_rules(a.rules), load_ledger(a.ledger), scene,
                           chapter_id=scene.get("chapter_id"),
                           keywords=scene.get("keywords"))
    snap = make_snapshot(a.group, ctx)
    out = a.out or (SNAP_DIR / f"{a.group}.json")
    save_json(out, snap)
    print(f"快照已写入 {out}（组别 {a.group}，尚未填入生成文本）")


def cmd_annotate(argv):
    p = argparse.ArgumentParser(prog="planner annotate")
    p.add_argument("group", choices=GROUPS)
    p.add_argument("type", choices=DEVIATION_TYPES)
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--snippet", default="")
    p.add_argument("--involved", default="")
    p.add_argument("--path", type=Path, default=DATA / "deviations.json")
    a = p.parse_args(argv)
    devs = load_json(a.path) if a.path.exists() else {"records": []}
    record_deviation(devs, a.group, a.type, a.count, a.snippet, a.involved)
    save_json(a.path, devs)
    print(f"已记录 1 条偏差 → {a.path}（{a.group}/{a.type} x{a.count}）")


def cmd_backflow(argv):
    p = argparse.ArgumentParser(prog="planner backflow")
    p.add_argument("--rules", type=Path, default=DATA / "rules.json")
    p.add_argument("--ledger", type=Path, default=DATA / "ledger.json")
    p.add_argument("--deviations", type=Path, default=DATA / "deviations.json")
    a = p.parse_args(argv)
    rules, ledger, devs = load_rules(a.rules), load_ledger(a.ledger), load_json(a.deviations)
    backflow(devs, rules, ledger)
    save_json(a.rules, rules)
    save_json(a.ledger, ledger)
    print(f"回流完成：规则 {len(rules['rules'])} 条，账本 {len(ledger['entries'])} 条")


def cmd_report(argv):
    p = argparse.ArgumentParser(prog="planner report")
    p.add_argument("--deviations", type=Path, default=DATA / "deviations.json")
    p.add_argument("--snapshots", type=Path, default=SNAP_DIR)
    a = p.parse_args(argv)
    devs = load_json(a.deviations) if a.deviations.exists() else {"records": []}
    snaps = []
    if a.snapshots.is_dir():
        for f in sorted(a.snapshots.glob("*.json")):
            snaps.append(load_json(f))
    print(json.dumps(compute_metrics(devs, snaps), ensure_ascii=False, indent=2))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in (
        "rules", "ledger", "generate", "annotate", "backflow", "report"
    ):
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    {
        "rules": cmd_rules,
        "ledger": cmd_ledger,
        "generate": cmd_generate,
        "annotate": cmd_annotate,
        "backflow": cmd_backflow,
        "report": cmd_report,
    }[argv[0]](argv[1:])


if __name__ == "__main__":
    main()
