#!/usr/bin/env python3
"""匹配（第二轮 · 语义判定）：逐块调用语言模型走判断链两问。

预注册（规则与参数写死于本文件，跑完不改；要改须另起一轮并说明）：

一、范围：只判 assets/memory/default/ 的段（load_segments 后按 path 前缀
    memory/default/ 过滤，应为 62 段），不碰 fiction / work —— 同一记忆集内闭环。
二、分块：直接用实验室 ../../src/segmentation.py 的 load_segments，不自己切段。
三、条目：profile 五轴各篇的 `## ` 小节（子条目 `### ` 归属所在 `##`），条目全文给模型，
    不另建副本。
四、判定：每块一次 OpenAI 兼容 chat/completions 调用，语言模型答判断链两问——
    ① 档案里有没有对应条目（没有 → 全新信号）；
    ② 若有，方向是 一致 / 相反 / 例外。
    判据（docs/assumption.md「方向相反」定义 + 三类 + 四条判定要求）原文写进 system prompt；
    每次把全部条目全文与碎片全文给模型。prompt 见下方 SYSTEM_PROMPT / build_user，跑完不改。
五、参数（预注册）：
    base = 环境变量 MF_LLM_BASE_URL；key = MF_LLM_API_KEY，缺则回退 XIAOMI_API_KEY；
    model = 环境变量 MF_LLM_MODEL；
    max_tokens 依次 3000 / 6000 / 9000（首试 + 重试 2 次 = 3 次；≥800，规避
    mimo-v2.6-pro 思考吃光预算导致 content 空串的坑）；
    取回答先读 choices[0].message.content，为空再读 reasoning_content；
    JSON 不可解析、或字段/取值不合约束 → 计为失败并重试；对应条目接受完整标签
    （file「name」）或唯一裸条目名，裸名归一到完整标签（冒烟实测模型常只写裸名）；
    【跑后修订 2026-10-05，已说明】首趟全量跑完后发现解析层与金标归一口径不一致：
    金标把 `###` 子条目归一到所属 `##`，解析层却只认 `##` 标签，导致 5 个块
    （均不在金标 20 块内）因引用子条目被判失败。仅修归一层：子条目标签 → 所属 `##`，
    `」` 后的附注截去；prompt、模型、max_tokens、重试次数、比对口径全部未动，
    修正后只重跑失败块；三栏准确率不受影响（失败块不在金标分母内）。
    三试都失败 → 该块记「状态=失败」+ 错误原文，如实进结果文件，绝不写空判定。
六、无词法参与：第一轮的 BM25、SCORE_FLOOR、SHARED_MIN、否定词 ±3 字窗口全部删除。
七、与金标比对口径：
    有无 = 字符串相等；
    条目 = 模型标签（子条目归一到 ## 后）命中金标条目集合任一条即对
           （金标多条目块 `＋` 分隔，任一命中即算；另报第一轮口径「只比首条」供对照）；
    方向 = 模型「例外」按金标二值口径折算为「一致」（例外＝同向，金标无例外档）；
    状态=失败或缺失的块，三栏全错。
跑完自动与金标 data/gold/2026-10-05-匹配金标-20块.json 逐块比对并打印摘要。

用法：python3 src/match.py [--smoke] [--fresh]
    --smoke 只判第一块并打印原始回答（开跑前冒烟，不写结果文件）
    --fresh 忽略已有结果文件从头跑（默认断点续跑：已判对的块跳过）
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent              # memory-factory/src
LAB = HERE.parents[2]                               # quanttide-founder-lab
sys.path.insert(0, str(LAB / "src"))

from segmentation import load_segments  # noqa: E402

REPO = LAB.parent.parent                            # quanttide-founder
ASSETS = REPO / "assets"
PROFILE = ASSETS / "memory" / "default" / "profile"
CASE = HERE.parent                                   # memory-factory
OUT_PATH = CASE / "data" / "2026-10-05-匹配结果-语义.json"
GOLD_PATH = CASE / "data" / "gold" / "2026-10-05-匹配金标-20块.json"

SEG_PREFIX = "memory/default/"   # 范围收窄：只判 default 集
AXES = ["emotions.md", "values.md", "triggers.md", "methods.md", "expressions.md"]

# 预注册参数（跑完不改）
MAX_TOKENS_SCHEDULE = (3000, 6000, 9000)   # 首试 + 重试 2 次
HTTP_TIMEOUT = 300

SYSTEM_PROMPT = """你是记忆档案的匹配判定器。你会看到一个人的日志碎片，以及这个人的档案（五轴条目全文）。对每一块碎片做两问判定。

【判断链】
第一问：档案里有没有与碎片讲同一件事的条目？
- 没有 → 全新信号：判定_有无对应条目填「否」，判定_对应条目与判定_方向都填空字符串。
第二问（仅当第一问为「是」）：碎片方向与该条目是什么关系？三类：
- 一致（含复述、补充、具体化）：块复述条目 → 忽略；块补充条目 → 补进；块是条目的具体场景 → 补进。
- 相反：条目说 A，块说非 A，且 A 与非 A 不能同时成立。
- 例外（不是相反，单独一类）：块与条目同向，但块是条目的反例场景。例：条目「带人必须有商业收益」对块「这次带人没收益但学到了东西」→ 例外，不改条目。

【方向相反的判据】
块的核心主张与条目的核心主张互斥，即两者不能同时为真。
例：条目「精英社交没意思」对块「一次行业聚会聊出了两个客户」→ 相反。

【判定要求】
1. 需要语义理解，不能靠词面极性。
2. 词面极性不同 ≠ 语义相反。
3. 否定词出现 ≠ 语义相反。
4. ±3 字观察窗内的否定词，不构成判据。

【输出】只输出一个 JSON 对象，不要输出 JSON 以外的任何文字：
{"判定_有无对应条目": "是 或 否", "判定_对应条目": "判是时填条目清单里的标签原文（如 emotions.md「高压与喘息」，只写条目名也可以），否则空字符串", "判定_方向": "判是时填 一致/相反/例外，否则空字符串", "理由": "一两句中文说明依据"}
"""


class LLMError(Exception):
    """单次调用失败（网络、空回答、不可解析、取值不合约束）。"""


# ---------------------------------------------------------------- 条目与提示词

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
        e["label"] = f"{e['file']}「{e['name']}」"
    return entries


def build_user(seg, entries: list[dict]) -> str:
    """一次调用的 user 消息：全部条目全文 + 碎片全文。"""
    parts = ["【档案五轴条目清单】",
             f"判「是」时，判定_对应条目必须从下列 {len(entries)} 个标签中原样选取其一。\n"]
    for e in entries:
        parts.append(f"### {e['label']}\n{e['body']}\n")
    parts.append("【日志碎片】")
    parts.append(f"编号：{seg.id}    日期：{seg.date}")
    parts.append(f"正文：\n{seg.text}")
    return "\n".join(parts)


# ---------------------------------------------------------------- 调模型

def chat(system: str, user: str, max_tokens: int) -> tuple[str, str]:
    """一次 chat/completions 调用，返回 (回答文本, 来源)。

    先读 content，为空再读 reasoning_content；两者皆空视为失败。
    """
    base = os.environ.get("MF_LLM_BASE_URL")
    if not base:
        raise LLMError("缺少环境变量 MF_LLM_BASE_URL")
    key = os.environ.get("MF_LLM_API_KEY") or os.environ.get("XIAOMI_API_KEY")
    if not key:
        raise LLMError("缺少密钥：设置 MF_LLM_API_KEY 或 XIAOMI_API_KEY")
    model = os.environ.get("MF_LLM_MODEL", "mimo-v2.6-pro")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base.rstrip('/')}/chat/completions",
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise LLMError(f"HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"请求失败: {e.reason}") from e
    try:
        msg = payload["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"响应缺 choices: {json.dumps(payload)[:300]}") from e
    content = (msg.get("content") or "").strip()
    if content:
        return content, "content"
    reasoning = (msg.get("reasoning_content") or "").strip()
    if reasoning:
        return reasoning, "reasoning_content"
    raise LLMError("content 与 reasoning_content 均为空串")


def make_resolver(entries: list[dict]) -> tuple[dict[str, str], set[str]]:
    """可接受的条目写法 → 归一后的 ## 完整标签，以及合法标签集合。

    接受：## 完整标签、## 裸名、### 子条目标签（归到所属 ##）、### 裸名。
    """
    resolver: dict[str, str] = {e["label"]: e["label"] for e in entries}
    for e in entries:
        resolver.setdefault(e["name"], e["label"])
    for e in entries:                       # 子条目不覆盖 ## 的同名写法
        for s in e["subs"]:
            resolver.setdefault(f"{e['file']}「{s}」", e["label"])
            resolver.setdefault(s, e["label"])
    return resolver, {e["label"] for e in entries}


def resolve_label(lab: str, resolver: dict[str, str]) -> str:
    """标签归一：精确 → 截去 `」` 后的附注；归不了则原样返回。"""
    if lab in resolver:
        return resolver[lab]
    if "」" in lab:
        head = lab.split("」", 1)[0] + "」"
        if head in resolver:
            return resolver[head]
    return lab


def parse_answer(text: str, resolver: dict[str, str],
                 canonical: set[str]) -> dict:
    """从回答里抠出 JSON 对象并校验字段与取值；不合格抛 LLMError。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:].strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < i:
        raise LLMError(f"回答中找不到 JSON 对象: {text[:120]!r}")
    try:
        obj = json.loads(t[i:j + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"JSON 解析失败: {e}; 片段 {t[:120]!r}") from e
    if not isinstance(obj, dict):
        raise LLMError("回答不是 JSON 对象")
    has = str(obj.get("判定_有无对应条目", "")).strip()
    lab = str(obj.get("判定_对应条目", "")).strip()
    dire = str(obj.get("判定_方向", "")).strip()
    why = str(obj.get("理由", "")).strip()
    if has not in ("是", "否"):
        raise LLMError(f"判定_有无对应条目 取值非法: {has!r}")
    if has == "是":
        lab = resolve_label(lab, resolver)
        if lab not in canonical:
            raise LLMError(f"判定_对应条目 不在条目清单内: {lab!r}")
        if dire not in ("一致", "相反", "例外"):
            raise LLMError(f"判定_方向 取值非法: {dire!r}")
    else:
        if lab or dire:
            raise LLMError(f"判「否」时条目/方向须为空: {lab!r}/{dire!r}")
    return {"判定_有无对应条目": has, "判定_对应条目": lab,
            "判定_方向": dire, "理由": why}


def judge_block(seg, entries: list[dict]) -> dict:
    """对一块调模型判定；三试失败则记「状态=失败」，不写空判定。"""
    user = build_user(seg, entries)
    resolver, canonical = make_resolver(entries)
    errors: list[str] = []
    for attempt, max_tok in enumerate(MAX_TOKENS_SCHEDULE, 1):
        try:
            text, source = chat(SYSTEM_PROMPT, user, max_tok)
            ans = parse_answer(text, resolver, canonical)
        except LLMError as e:
            errors.append(f"第{attempt}试(max_tokens={max_tok}): {e}")
            continue
        return {
            "id": seg.id, "date": seg.date, "path": seg.path,
            "line_start": seg.line_start, "line_end": seg.line_end,
            "状态": "ok",
            "判定_有无对应条目": ans["判定_有无对应条目"],
            "判定_对应条目": ans["判定_对应条目"],
            "判定_方向": ans["判定_方向"],
            "理由": ans["理由"],
            "尝试次数": attempt, "max_tokens": max_tok,
            "回答来源": source, "错误": "",
        }
    return {
        "id": seg.id, "date": seg.date, "path": seg.path,
        "line_start": seg.line_start, "line_end": seg.line_end,
        "状态": "失败",
        "判定_有无对应条目": "", "判定_对应条目": "", "判定_方向": "",
        "理由": "",
        "尝试次数": len(MAX_TOKENS_SCHEDULE), "max_tokens": 0,
        "回答来源": "", "错误": " || ".join(errors),
    }


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
    """金标条目串（可含多个，`＋` 分隔）→ 归一后的标签列表。"""
    out = []
    for part in [p.strip() for p in
                 label.replace("+", "＋").split("＋") if p.strip()]:
        if "「" not in part:
            continue
        fn, name = part.split("「", 1)
        name = name.rstrip("」")
        canon = lmap.get(fn, {}).get(name, name)
        out.append(f"{fn}「{canon}」")
    return out


def normalize_one(label: str, lmap: dict[str, dict[str, str]]) -> str:
    """单个标签归一（子条目 → 所属 ## 小节）。"""
    got = normalize_gold(label, lmap)
    return got[0] if got else label


def compare(mine: list[dict], gold: dict) -> dict:
    lmap = label_map()
    n_has = n_lab = n_lab_r1 = n_dir = n_all = 0
    rows: list[dict] = []
    for g in gold["块"]:
        m = next((x for x in mine if x["id"] == g["id"]), None)
        if m is None or m.get("状态") != "ok":
            rows.append({
                "id": g["id"],
                "我的判定": "缺失或判定失败",
                "金标": f"{g['判定_有无对应条目']} / "
                        f"{g['判定_对应条目'] or '—'} / {g['判定_方向'] or '—'}",
                "不一致栏": ["有无", "条目", "方向"],
                "我的理由": (m or {}).get("错误", "结果文件中无此块"),
                "金标备注": g.get("判定_备注", ""),
            })
            continue
        g_has = g["判定_有无对应条目"]
        g_labs = normalize_gold(g["判定_对应条目"], lmap)
        g_dir = g["判定_方向"]
        m_has = m["判定_有无对应条目"]
        m_lab = normalize_one(m["判定_对应条目"], lmap) if m["判定_对应条目"] else ""
        m_dir_raw = m["判定_方向"]
        m_dir = "一致" if m_dir_raw == "例外" else m_dir_raw   # 例外 → 同向（金标二值口径）

        ok_has = m_has == g_has
        if g_has == "是":
            ok_lab = m_lab in g_labs if g_labs else False
            ok_lab_r1 = bool(g_labs) and m_lab == g_labs[0]
            ok_dir = m_dir == g_dir
        else:
            ok_lab = m_lab == ""
            ok_lab_r1 = ok_lab
            ok_dir = m_dir == ""
        ok = ok_has and ok_lab and ok_dir
        n_has += ok_has
        n_lab += ok_lab
        n_lab_r1 += ok_lab_r1
        n_dir += ok_dir
        n_all += ok
        if not ok:
            rows.append({
                "id": g["id"],
                "我的判定": f"{m_has} / {m['判定_对应条目'] or '—'} / "
                            f"{m_dir_raw or '—'}",
                "金标": f"{g_has} / {g['判定_对应条目'] or '—'} / {g_dir or '—'}",
                "不一致栏": [k for k, v in
                             (("有无", ok_has), ("条目", ok_lab), ("方向", ok_dir))
                             if not v],
                "我的理由": m["理由"],
                "金标备注": g.get("判定_备注", ""),
            })
    total = len(gold["块"])
    n_fail = sum(1 for x in mine if x.get("状态") != "ok")
    return {
        "总块数": total,
        "有无正确": f"{n_has}/{total} = {n_has / total:.1%}",
        "条目正确_主口径_命中任一": f"{n_lab}/{total} = {n_lab / total:.1%}",
        "条目正确_第一轮口径_仅首条": f"{n_lab_r1}/{total} = {n_lab_r1 / total:.1%}",
        "方向正确": f"{n_dir}/{total} = {n_dir / total:.1%}",
        "三栏全对": f"{n_all}/{total} = {n_all / total:.1%}",
        "全62段_判相反数": sum(1 for x in mine
                              if x.get("判定_方向") == "相反"),
        "全62段_判例外数": sum(1 for x in mine
                              if x.get("判定_方向") == "例外"),
        "全62段_判失败数": n_fail,
        "不一致块数": len(rows),
        "不一致块": rows,
    }


# ---------------------------------------------------------------- 主流程

def save(results: list[dict], n_total: int) -> None:
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "_规则": (
            "第二轮·语义判定：每块一次 chat/completions 调语言模型答判断链两问"
            "（①有无对应条目 ②方向 一致/相反/例外），判据原文写进 system prompt；"
            "max_tokens 3000/6000/9000 三试；范围收窄 default 集；无 BM25 无词法阈值。"
            "预注册见 src/match.py 文件头。"
        ),
        "_范围": "assets/memory/default/",
        "_模型": os.environ.get("MF_LLM_MODEL", "mimo-v2.6-pro"),
        "段总数": n_total,
        "已判块数": len(results),
        "块": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    argv = set(sys.argv[1:])
    smoke = "--smoke" in argv
    fresh = "--fresh" in argv

    segs = [s for s in load_segments(ASSETS) if s.path.startswith(SEG_PREFIX)]
    entries = load_entries()
    print(f"default 集段数 {len(segs)}，条目 {len(entries)} 条", file=sys.stderr)

    if smoke:
        seg = segs[0]
        resolver, canonical = make_resolver(entries)
        text, source = chat(SYSTEM_PROMPT, build_user(seg, entries),
                            MAX_TOKENS_SCHEDULE[0])
        print(f"[冒烟] 块 {seg.id} | 来源={source} | 回答长度={len(text)}")
        print(f"[冒烟] 回答前 600 字：\n{text[:600]}")
        ans = parse_answer(text, resolver, canonical)
        print(f"[冒烟] 解析通过：{json.dumps(ans, ensure_ascii=False)}")
        return

    prev: dict[str, dict] = {}
    if OUT_PATH.exists() and not fresh:
        try:
            prev = {b["id"]: b for b in
                    json.loads(OUT_PATH.read_text(encoding="utf-8"))["块"]}
        except (json.JSONDecodeError, KeyError):
            prev = {}

    results: list[dict] = []
    for i, seg in enumerate(segs, 1):
        old = prev.get(seg.id)
        if old is not None and old.get("状态") == "ok":
            results.append(old)
            print(f"[{i}/{len(segs)}] {seg.id} 已有判定，跳过", file=sys.stderr)
            continue
        entry = judge_block(seg, entries)
        results.append(entry)
        save(results, len(segs))
        print(f"[{i}/{len(segs)}] {seg.id} 状态={entry['状态']} "
              f"{entry['判定_有无对应条目']} "
              f"{entry['判定_对应条目'] or ''} {entry['判定_方向'] or ''}",
              file=sys.stderr, flush=True)

    save(results, len(segs))
    gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    cmp = compare(results, gold)
    print(json.dumps(cmp, ensure_ascii=False, indent=2))
    print(f"\n结果已写入 {OUT_PATH.relative_to(CASE)}")


if __name__ == "__main__":
    main()
