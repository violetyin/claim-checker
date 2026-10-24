# -*- coding: utf-8 -*-
"""
checker.py —— 核查引擎（侦探作品的主流程）

一句话进来，走四步：
    拆  一句说法 → 2~3 个可独立核验的命题
    查  每个命题 → 现代（元宝搜索）+ 传统（42 部古籍全文）
    比  数独立来源，不看条数；信源分级压掉营销号
    判  五档结论，必须挂得住来源

五档（这是作品的价值观，也是路演最值钱的一句话）：
    supported    有据      多个独立权威一致
    partial      部分有据  方向对，但程度/范围被夸大   ← 养生传言绝大多数落这里
    contrary     相反      权威结论与说法相反
    ancient_only 仅古载    古籍有记载，但古籍记载 ≠ 现代疗效证据
    not_found    查不到    给定来源里没有一条真在说这件事  ← 敢认输，是拉开差距的地方

用法：
    python checker.py "红枣补铁"
    python checker.py "隔夜菜致癌"
    python checker.py "红枣配薄荷能排肝毒" --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding="utf-8")

import guji_engine
import llm_client as L
import source_tier as st
import wsa_search as w

# ---------------------------------------------------------------------------
# 档位
# ---------------------------------------------------------------------------

VERDICTS = {
    "supported":    {"label": "有据",     "icon": "✓", "color": "#2e6b4f"},
    "partial":      {"label": "部分有据", "icon": "◐", "color": "#b07c1a"},
    "contrary":     {"label": "相反",     "icon": "✕", "color": "#b3261e"},
    "ancient_only": {"label": "仅古载",   "icon": "❖", "color": "#8a6d3b"},
    "not_found":    {"label": "查不到",   "icon": "∅", "color": "#6b6b6b"},
}

MAX_SOURCES_TO_LLM = 8      # 给模型的来源条数上限。
                            # 从 10 降到 8：生成时间基本跟输出量走，输入太长会拖慢首字；
                            # 8 条 T1/T2 已经足够判断"有没有人真在说这件事"。
MAX_SEARCH_PER_PROP = 3     # 每命题最多几条检索式（额度预算）
MAX_PROPOSITIONS = 3

# 结论落盘：同一句话第二次跑必须给出同一个答案。
# 不是为了省调用，是为了「评委面前跑两次结论不一样」这种事故不发生。
RESULT_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "check")


def _result_cache_path(claim: str, deep: bool = False) -> str:
    import hashlib
    key = ("deep:" if deep else "") + "".join(claim.split())   # 去掉空白差异
    return os.path.join(RESULT_CACHE_DIR, hashlib.sha1(key.encode("utf-8")).hexdigest()[:20] + ".json")


def _read_result(claim: str, deep: bool = False) -> dict | None:
    p = _result_cache_path(claim, deep)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return _polish(json.load(f))
    except (json.JSONDecodeError, OSError):
        return None


def _write_result(claim: str, r: dict, deep: bool = False):
    os.makedirs(RESULT_CACHE_DIR, exist_ok=True)
    p = _result_cache_path(claim, deep)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(r, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def _polish(r: dict) -> dict:
    """输出前的统一打磨。走缓存和实时计算都要过这一道，保证两种路径长得一样。

    主要是古籍标题：判「查不到」时，如果古籍区写着「在 39 部书里找到 39 处记载」，
    评委会立刻反问——都找到 39 处了，凭什么说查不到？
    那 39 处是大枣、薄荷各自的词条，不是「红枣配薄荷能排肝毒」这个说法。
    不分清这层，最值钱的那一屏反而最让人困惑。
    """
    v = (r.get("verdict") or {}).get("code")
    a = r.get("ancient") or {}
    if v == "not_found" and a.get("verdict") == "found" and a.get("books_hit"):
        a["headline"] = (f"{a['books_hit']} 部古籍里提到过这些药材，"
                         f"但没有「{r.get('claim','')}」这个说法")
        r["ancient"] = a
    return r

# ---------------------------------------------------------------------------
# 第一步：拆命题
# ---------------------------------------------------------------------------

SPLIT_SYS = """你是事实核查员。把用户给的一句养生说法，拆成 2~3 个「用一次搜索就能回答」的独立命题。

拆解必须覆盖两层，不许只挑最容易反驳的那层：
- **存在层**：这句话说「有」的那个东西，到底有没有？（例：红枣含有铁元素吗）
- **效力层**：这句话说的效果有多大，站不站得住？（例：吃红枣能有效补铁吗）

为什么要两层：一句话常常「有一半是真的」。「红枣补铁」里，红枣确实含铁（真），
但拿它当补铁办法不成立（假）。只有拆出存在层，判定才有层次，才说得清哪里真哪里假。
只拆效力层的话，一律会判成「相反」，反而把话说死了、说得不准。

硬性要求：
- **每个命题必须写成陈述句（断言），绝对不能写成问句。**
  问句没法被支持或反驳，后面的判定就没有主语了。
  反例× ：「红枣含铁量是否丰富」  正例✓：「红枣含有铁元素」
  反例× ：「红枣里的铁容易吸收吗」正例✓：「吃红枣能有效补充人体所需的铁」
- 命题要贴近原话本意，不要自行加码，也不要偷换成更弱的说法
- 每个命题给 2~3 条检索式：
  · 一条正面查（直接找支持证据）
  · 一条反面查（找「辟谣」「误区」「不成立」类证据）
  · 一条限定权威源（加「卫健委」「膳食指南」「中国营养学会」「腾讯医典」这类限定词）
- 检索式 6~20 字，像人真的会打进搜索框的话。不要引号、不要 AND/OR
- ancient_terms：涉及的食材/药材在古籍里的写法（如 红枣→大枣、薏米→薏苡仁、桂圆→龙眼肉）

示例。「红枣补铁」应拆成：
1. 红枣含有铁元素（存在层）
2. 吃红枣能有效补充人体所需的铁（效力层）

只输出 JSON，不要解释：
{"propositions":[{"text":"命题","queries":["检索式1","检索式2","检索式3"],"ancient_terms":["大枣"]}]}"""


def _fallback_split(claim: str) -> dict:
    """LLM 不可用时的兜底：整句当一个命题，用原句+权威限定词去搜。"""
    return {
        "propositions": [{
            "text": claim,
            "queries": [claim, claim + " 辟谣", claim + " 卫健委 建议"],
            "ancient_terms": guji_engine.extract_keywords(claim),
        }]
    }


def split_propositions(claim: str, max_props: int | None = None) -> tuple[dict, bool]:
    cfg = L.load_config()
    try:
        d = L.chat_json(
            [{"role": "system", "content": SPLIT_SYS},
             {"role": "user", "content": claim}],
            model=cfg.get("fast_model") or None,
            max_tokens=900,
        )
        props = d.get("propositions") or []
        props = [p for p in props if isinstance(p, dict) and p.get("text")]
        if props:
            return {"propositions": props[:max_props or MAX_PROPOSITIONS]}, False
    except L.LLMError as e:
        print(f"  ⚠ 拆命题失败（{str(e)[:80]}），退回整句检索", file=sys.stderr)
    return _fallback_split(claim), True


# ---------------------------------------------------------------------------
# 第二步：查（现代 + 传统）
# ---------------------------------------------------------------------------

def _norm_url(u: str) -> str:
    u = (u or "").strip().lower()
    for p in ("https://", "http://"):
        if u.startswith(p):
            u = u[len(p):]
    if u.startswith("www."):
        u = u[4:]
    return u.split("#")[0].rstrip("/")


def _plan_queries(prop: dict) -> list[str]:
    qs = [q for q in (prop.get("queries") or []) if isinstance(q, str) and q.strip()]
    lim = prop.get("qlimit") or MAX_SEARCH_PER_PROP
    return qs[:lim] or [prop["text"]]


def fetch_queries(queries: list[str], use_cache: bool = True, jobs: int = 6) -> dict[str, dict]:
    """把一批检索式并发打出去。

    这是整条链路最慢的一步：串行时 6~9 次搜索要干等 15~30 秒，
    每条都在等网络。并发后额度消耗完全一样，等待时间压到「最慢那一条」。
    不同命题的检索式也一起打——它们之间没有依赖，没理由排队。
    """
    out: dict[str, dict] = {}
    uniq = list(dict.fromkeys(q for q in queries if q))
    if not uniq:
        return out
    with ThreadPoolExecutor(max_workers=min(jobs, len(uniq))) as ex:
        futs = {ex.submit(w.search, q, cnt=10, use_cache=use_cache): q for q in uniq}
        for f in as_completed(futs):
            q = futs[f]
            try:
                out[q] = f.result()
            except w.WsaError as e:
                out[q] = {"_error": str(e)[:80]}
    return out


def assemble_modern(prop: dict, fetched: dict[str, dict]) -> dict:
    """合并一个命题的多条检索结果 → 去重 → 分级 → 只留能进证据链的。"""
    seen: dict[str, dict] = {}
    seen_title: dict[str, dict] = {}   # 同一篇文章常有简繁两版、PC 与移动两版，
                                       # 域名不同但内容是同一篇。光按 url 去重会把它算成两个独立来源，
                                       # 那就是自己打自己脸：说好了只数独立来源。
    raw_count = 0
    calls = 0
    errors = []
    used_queries = []

    def _merge(k: str, p: dict) -> bool:
        prev = seen.get(k)
        if prev is None:
            seen[k] = p
            return True
        if float(p.get("score") or 0) > float(prev.get("score") or 0):
            seen[k].update(p)
        return False

    for q in _plan_queries(prop):
        r = fetched.get(q)
        if r is None:
            continue
        if "_error" in r:
            errors.append(f"{q}: {r['_error']}")
            continue
        used_queries.append(q)
        if not r.get("cached"):
            calls += 1
        for p in r.get("pages", []):
            raw_count += 1
            key = _norm_url(p.get("url"))
            if not key:
                continue
            title = (p.get("title") or "").strip()
            date = (p.get("date") or "")[:10]
            tkey = f"{title}|{date}" if len(title) >= 10 else ""
            if tkey and tkey in seen_title:
                continue
            if not _merge(key, p):
                continue
            if tkey:
                seen_title[tkey] = seen[key]

    ann = st.annotate(list(seen.values()))
    trusted = [p for p in ann if p["tier"] <= 2]
    return {
        "queries": used_queries,
        "sources": trusted[:MAX_SOURCES_TO_LLM],
        "trusted_total": len(trusted),
        "discarded": [p for p in ann if p["tier"] >= 3],
        "raw_count": raw_count,
        "unique": len(seen),
        "api_calls": calls,
        "errors": errors,
        "cross": st.cross_check(list(seen.values())),
    }


def gather_modern_batch(props: list[dict], use_cache: bool = True, progress=None) -> list[dict]:
    """所有命题的检索式一次性并发打完，再逐个命题归位。"""
    all_q: list[str] = []
    for p in props:
        all_q.extend(_plan_queries(p))
    if progress:
        progress(f"并发发起 {len(set(all_q))} 条检索式…")
    fetched = fetch_queries(all_q, use_cache=use_cache)
    return [assemble_modern(p, fetched) for p in props]


def gather_modern(prop: dict, use_cache: bool = True) -> dict:
    """单个命题的便捷入口（保留给外部调用）。"""
    return gather_modern_batch([prop], use_cache=use_cache)[0]


def gather_ancient(claim: str, props: list[dict]) -> dict:
    """古籍那半边。注意：命中 ≠ 这句话有出处。
    关键词能捞到「大枣」的词条，不等于古人在说「红枣补铁」。所以原文照给，判不判由模型读。"""
    base = guji_engine.check(claim, limit=6)
    per_prop = []
    for p in props:
        terms = [t for t in (p.get("ancient_terms") or []) if isinstance(t, str) and t]
        if not terms:
            continue
        r = guji_engine.search(terms, limit=3)
        per_prop.append({
            "proposition": p.get("text", ""),
            "terms": terms,
            "books_hit": r["books_hit"],
            "passages": r["passages"][:3],
        })
    return {
        "verdict": base["verdict"],
        "books_hit": base["books_hit"],
        "headline": base["headline"],
        "earliest": base["earliest"],
        "passages": base["passages"][:5],
        "keywords": base["keywords"],
        "corpus": base["corpus"],
        "per_proposition": per_prop,
    }


# ---------------------------------------------------------------------------
# 第三步：判
# ---------------------------------------------------------------------------

JUDGE_SYS = """你是严谨的事实核查员。任务：对一条养生说法给出判定。

铁律，违反任何一条都算失败：
1. 只能依据下面给出的「编号来源」和「古籍原文」判断。不许用你自己的知识补充事实。
2. 如果给出的来源里没有任何一条直接支持该命题，必须判 not_found。
   不许因为「常识上应该是对的」就判有据。宁可认输，不许硬编。
3. 来源之间结论冲突时，判 partial 或 not_found，不要替任何一方站队。
4. 允许说不知道。判 not_found 不是失败，是诚实。

档位定义：
- supported    有据      ≥2 个独立权威来源一致支持，且没有权威来源反对
- partial      部分有据  方向对，但程度、范围或适用条件被夸大
- contrary     相反      权威来源的结论与这条说法相反
- ancient_only 仅古载    古籍有相关记载，但现代权威来源支持不足（古籍记载 ≠ 现代疗效证据）
- not_found    查不到    给定来源里没有一条直接支持该说法

整体判定怎么从命题汇总（照这个来，别自己发挥）：
- 存在层成立、效力层相反 → 整体判 partial。
  这就是「方向对、程度被夸大」，是最常见的情形，不要判成 contrary。
  例：红枣确实含铁，但拿它补铁不行 → partial，不是 contrary。
- 只有原话的核心在效力、且存在层也不成立 → 才判 contrary。
- 所有命题都查不到 → not_found。
- 现代来源支持不足、但古籍确有记载 → ancient_only。

写结论的口径：
- headline 一句话、不超过 40 字、说人话、不夸大、不要感叹号
- reason 里必须点名来源编号（如「来源[2][5]」）或书名
- 不写「建议就医」「因人而异」这类正确的废话，除非真的缺证据
- 不谈疾病治疗、不谈用药、不用「排毒」「治愈」「根治」这类词

只输出 JSON，不要解释：
{"verdict":"supported|partial|contrary|ancient_only|not_found",
 "headline":"一句话结论",
 "reason":"2~4句，说清依据在哪、分歧在哪",
 "propositions":[{"text":"原命题","verdict":"五档之一","reason":"一句话","cites":[1,2]}],
 "ancient_note":"古籍部分怎么说的，没有就直说没有，别编"}"""


def _build_judge_input(claim: str, props: list[dict], moderns: list[dict], ancient: dict) -> tuple[str, list[dict]]:
    """把编号来源和古籍原文拼成模型能读的文本。编号是后面渲染引用的锚点。"""
    lines = [f"【待核查的说法】{claim}", ""]
    lines.append("【拆出的命题】")
    for i, p in enumerate(props, 1):
        lines.append(f"{i}. {p.get('text')}")
    lines.append("")

    numbered: list[dict] = []
    lines.append("【现代来源】（编号 → 标题 / 站点 / 日期 / 摘要）")
    idx = 0
    for prop, mod in zip(props, moderns):
        if not mod["sources"]:
            lines.append(f"（命题「{prop.get('text')}」没有检索到可信来源）")
            continue
        lines.append(f"— 对应命题：{prop.get('text')}")
        for s in mod["sources"]:
            idx += 1
            excerpt = (s.get("content") or s.get("passage") or "").replace("\n", " ").strip()
            lines.append(
                f"[{idx}] {s.get('title','')} | {s.get('site','')} | "
                f"{(s.get('date') or '')[:10]} | 信源档位 T{s.get('tier')}{s.get('tier_label')}\n"
                f"    {excerpt[:220]}"
            )
            item = dict(s)
            item["id"] = idx
            item["proposition"] = prop.get("text", "")
            numbered.append(item)
    if idx == 0:
        lines.append("（所有检索式都没有拿到 T1/T2 级别的可信来源）")
    lines.append("")

    lines.append("【古籍原文】（注意：关键词命中不等于这句话有出处，要看原文到底在说什么）")
    if ancient.get("verdict") == "not_found":
        lines.append("42 部本草古籍全文检索：未找到相关记载。")
    else:
        for i, p in enumerate(ancient.get("passages", []), 1):
            lines.append(f"古{i}. 《{p.get('book')}》{p.get('dynasty')}·{p.get('author')}：{p.get('sentence','')[:240]}")
    lines.append("")
    lines.append("现在按铁律判定。")
    return "\n".join(lines), numbered


FOLLOWUP_SYS = """你在做深度核查的第二轮追问。

第一轮已经查过一轮，下面告诉你每个命题的初步结论。你的任务是：只针对**还有争议**的命题
（初步判为「部分有据」「相反」「查不到」的），再生成 1~2 条更锋利的追问式。

追问要针对分歧点，不要重复第一轮的问法：
- 如果是「部分有据」：追它的**程度**和**适用条件**（谁的结论、多大剂量、什么人群、多大效果）
- 如果是「相反」：追**有没有例外**（是不是某些条件下成立、原始研究怎么说的）
- 如果是「查不到」：追它**是不是换了说法**（同义表述、相关概念、是否出自某个具体机构）
- 已经判「有据」的命题，返回空数组，不要浪费额度

检索式 6~20 字，像人真的会打进搜索框的话。不要引号、不要 AND/OR。

只输出 JSON：
{"followups":[{"index":0,"queries":["...","..."]}]}   （index 是命题序号，从 0 开始）"""


def generate_followups(claim: str, props: list[dict], moderns: list[dict], verdict: dict) -> list[list[str]]:
    """深度调查第二轮：针对有争议的命题补刀。

    为什么要这一轮：第一轮拿到的来源往往停在「有没有」这一层，
    而养生传言的分歧几乎都在「到什么程度」。不追问，就只能给个模糊结论。
    """
    lines = [f"待核查的说法：{claim}", ""]
    dispute = []
    for i, (p, m) in enumerate(zip(props, moderns)):
        pv = ""
        for item in (verdict.get("propositions") or []):
            if isinstance(item, dict) and item.get("text", "").strip() == p.get("text", "").strip():
                pv = item.get("verdict", "")
                break
        lines.append(f"[{i}] 命题：{p.get('text')}　初步结论：{pv or '未知'}")
        if pv in ("partial", "contrary", "not_found"):
            dispute.append(i)
            lines.append(f"    已有来源 {len(m.get('sources') or [])} 条，"
                         f"其中最相关的标题：{(m.get('sources') or [{}])[0].get('title','')[:40] if m.get('sources') else '（无）'}")

    out: list[list[str]] = [[] for _ in props]
    if not dispute:
        return out

    cfg = L.load_config()
    try:
        d = L.chat_json(
            [{"role": "system", "content": FOLLOWUP_SYS},
             {"role": "user", "content": "\n".join(lines)}],
            model=cfg.get("fast_model") or None,
            max_tokens=700,
        )
    except L.LLMError:
        return out

    for item in (d.get("followups") or []):
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(out):
            qs = [q for q in (item.get("queries") or []) if isinstance(q, str) and q.strip()]
            out[idx] = qs[:2]
    return out


def judge(claim: str, props: list[dict], moderns: list[dict], ancient: dict) -> tuple[dict, int]:
    cfg = L.load_config()
    user_text, numbered = _build_judge_input(claim, props, moderns, ancient)
    calls = 0
    for attempt in (1, 2):
        try:
            d = L.chat_json(
                [{"role": "system", "content": JUDGE_SYS},
                 {"role": "user", "content": user_text}],
                model=cfg.get("model") or None,
                max_tokens=900,
            )
            calls += 1
            v = (d.get("verdict") or "").strip()
            if v not in VERDICTS:
                d["verdict"] = "not_found"
            return d, calls
        except L.LLMError as e:
            calls += 1
            if attempt == 2:
                return {
                    "verdict": "not_found",
                    "headline": "判定没跑完（模型调用失败）",
                    "reason": f"模型返回异常：{str(e)[:120]}。这一条不算核查通过。",
                    "propositions": [],
                    "ancient_note": "",
                }, calls
    return {}, calls


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def analyze(claim: str, use_cache: bool = True, verbose: bool = True, refresh: bool = False,
            deep: bool = False) -> dict:
    t0 = time.time()
    claim = (claim or "").strip()
    if not claim:
        raise ValueError("说法不能为空")

    def say(msg):
        if verbose:
            print(msg, flush=True)

    if use_cache and not refresh:
        hit = _read_result(claim, deep)
        if hit:
            hit.setdefault("stats", {})["from_cache"] = True
            say(f"　回放 → 这句之前核过，直接给结论（{hit['verdict']['label']}）")
            return hit

    say(f"　拆 → 把「{claim}」拆成可查的命题…")
    t_split = time.time()
    # 深度调查拆得更细：最多 4 条命题，每条能带更多检索式
    split, split_fallback = split_propositions(claim, max_props=4 if deep else None)
    props = split["propositions"]
    t_split = time.time() - t_split

    say(f"　查 → {len(props)} 个命题，并发走元宝搜索 + 42 部古籍…")
    t_search = time.time()
    moderns = gather_modern_batch(props, use_cache=use_cache)
    t_search = time.time() - t_search
    api_calls = sum(m["api_calls"] for m in moderns)
    for i, mod in enumerate(moderns, 1):
        say(f"　　 命题{i}：{mod['unique']} 条去重 / 可信 {mod['trusted_total']} / 剔除 {len(mod['discarded'])}")

    t_ancient = time.time()
    ancient = gather_ancient(claim, props)
    t_ancient = time.time() - t_ancient
    if ancient["verdict"] == "not_found":
        say("　  古籍：未找到相关记载")
    else:
        say(f"　  古籍：{ancient['books_hit']} 部书命中")

    say("　比 → 数独立来源，不看条数")
    say("　判 → 给结论…")
    t_judge = time.time()
    verdict, llm_calls = judge(claim, props, moderns, ancient)
    t_judge = time.time() - t_judge
    llm_calls += 0 if split_fallback else 1

    # ── 深度调查第二轮：只补有争议的命题 ──
    # 第一轮的来源多半停在「有没有」这一层，而养生传言的分歧几乎都在「到什么程度」。
    # 不追问，就只能给个模糊结论。
    t_deep = 0.0
    deep_rounds = 0
    if deep:
        say("　深 → 第二轮追问，补有争议的地方…")
        t_deep = time.time()
        fu = generate_followups(claim, props, moderns, verdict)
        if any(fu):
            for p, ex in zip(props, fu):
                if ex:
                    p["queries"] = list(p.get("queries") or []) + ex
                    p["qlimit"] = 6          # 追加后不能被原来的 3 条上限截掉
            llm_calls += 1                 # 追问生成这一次
            moderns = gather_modern_batch(props, use_cache=use_cache)
            api_calls = sum(m["api_calls"] for m in moderns)
            verdict, c2 = judge(claim, props, moderns, ancient)
            llm_calls += c2
            deep_rounds = 1
            for i, mod in enumerate(moderns, 1):
                say(f"　　 第二轮命题{i}：{mod['unique']} 条去重 / 可信 {mod['trusted_total']}")
        t_deep = time.time() - t_deep

    # 把命题判定对齐回来源编号，方便前端渲染"这句话有哪几条依据"
    prop_out = []
    for i, p in enumerate(props):
        pv = None
        for item in verdict.get("propositions", []) or []:
            if isinstance(item, dict) and item.get("text", "").strip() == p.get("text", "").strip():
                pv = item
                break
        if pv is None:
            pv = {"text": p.get("text"), "verdict": verdict.get("verdict"), "reason": "", "cites": []}
        code = pv.get("verdict") if pv.get("verdict") in VERDICTS else verdict.get("verdict")
        prop_out.append({
            "text": p.get("text", ""),
            "verdict": code,
            "label": VERDICTS[code]["label"],
            "reason": pv.get("reason", ""),
            "cites": pv.get("cites") or [],
            "queries": moderns[i]["queries"] if i < len(moderns) else [],
            "sources": moderns[i]["sources"] if i < len(moderns) else [],
            "discarded": moderns[i]["discarded"] if i < len(moderns) else [],
            "cross": moderns[i]["cross"] if i < len(moderns) else {},
        })

    all_sources = []
    for m in moderns:
        all_sources.extend(m["sources"])
    # 全局重编号（前端统一按这个 id 跳转）
    for i, s in enumerate(all_sources, 1):
        s["gid"] = i

    code = verdict.get("verdict", "not_found")
    result = {
        "claim": claim,
        "verdict": {
            "code": code,
            "label": VERDICTS[code]["label"],
            "icon": VERDICTS[code]["icon"],
            "color": VERDICTS[code]["color"],
            "headline": verdict.get("headline", ""),
            "reason": verdict.get("reason", ""),
        },
        "ancient_note": verdict.get("ancient_note", ""),
        "propositions": prop_out,
        "ancient": ancient,
        "sources": all_sources,
        "discarded_count": sum(len(m["discarded"]) for m in moderns),
        "stats": {
            "searches": api_calls,
            "llm_calls": llm_calls,
            "split_fallback": split_fallback,
            "elapsed": round(time.time() - t0, 1),
            "from_cache": False,
            # 分阶段耗时：调优和路演被问「为什么快/慢」时有据可答
            "deep": deep,
            "deep_rounds": deep_rounds,
            "stages": {
                "拆命题": round(t_split, 1),
                "并发搜索": round(t_search, 1),
                "古籍检索": round(t_ancient, 1),
                "判定": round(t_judge, 1),
                **({"第二轮追问": round(t_deep, 1)} if deep else {}),
            },
        },
    }
    result = _polish(result)
    if use_cache:
        try:
            _write_result(claim, result, deep)
        except OSError:
            pass
    return result


# ---------------------------------------------------------------------------
# 终端输出
# ---------------------------------------------------------------------------

def print_report(r: dict):
    bar = "─" * 74
    v = r["verdict"]
    print(bar)
    print(f"说法：{r['claim']}")
    print(bar)
    print(f"  {v['icon']} {v['label']}｜{v['headline']}")
    if v["reason"]:
        print(f"\n  {v['reason']}")
    print(bar)

    for i, p in enumerate(r["propositions"], 1):
        print(f"\n命题 {i}：{p['text']}")
        print(f"  判定：{p['label']}　{p['reason']}")
        print(f"  检索：{' / '.join(p['queries']) if p['queries'] else '（无）'}")
        c = p.get("cross") or {}
        if c:
            print(f"  来源：独立可信站点 {c.get('independent_domains')} 个"
                  f"（T1 权威 {c.get('t1_count')}）｜剔除低质 {len(p['discarded'])} 条")
        for s in p["sources"][:4]:
            print(f"    · [{s.get('gid','')}] {s.get('title','')[:48]}")
            print(f"      {s.get('site','')} · {(s.get('date') or '')[:10]} · T{s.get('tier')}")
            print(f"      {s.get('url','')}")

    a = r["ancient"]
    print("\n" + bar)
    print("古籍那半边：")
    print(f"  {a['headline']}")
    if r.get("ancient_note"):
        print(f"  判：{r['ancient_note']}")
    for i, p in enumerate(a["passages"][:3], 1):
        print(f"  [古{i}] 《{p['book']}》{p['dynasty']}·{p['author']}")
        print(f"       {p['sentence'][:110]}")

    print("\n" + bar)
    s = r["stats"]
    mode = "回放（之前核过）" if s.get("from_cache") else "实时"
    print(f"本次：{mode} · 搜索 {s['searches']} 次 · 模型 {s['llm_calls']} 次 · 耗时 {s['elapsed']}s")
    st = s.get("stages") or {}
    if st:
        print("分阶段：" + " · ".join(f"{k} {v}s" for k, v in st.items()))
    print(f"剔除低质来源 {r['discarded_count']} 条（营销号/偏方站，不进证据链）")
    print(bar)


def main():
    ap = argparse.ArgumentParser(description="核查引擎：拆命题 → 双源查 → 四档判")
    ap.add_argument("claim", nargs="?", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--refresh", action="store_true", help="忽略已存结论，重新核一遍")
    ap.add_argument("--deep", action="store_true", help="深度调查：多拆命题 + 第二轮追问")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not args.claim:
        ap.print_help()
        print("\n试试：python checker.py \"红枣补铁\"")
        return

    r = analyze(args.claim, use_cache=not args.no_cache, verbose=not args.quiet,
                refresh=args.refresh, deep=args.deep)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print_report(r)


if __name__ == "__main__":
    main()
