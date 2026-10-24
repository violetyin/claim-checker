# -*- coding: utf-8 -*-
"""
monitor.py —— 持续盯梢：盯住一条说法，每天重核一次，变了就提醒

为什么要这个：
    官方点名的进阶项之一。养生传言是会变的——新研究、新辟谣、来源被删，
    结论都可能变。一次核查只拍了张快照，盯梢才是真正的「持续」。

    而且它跟另外两个进阶项的组合逻辑很顺：
    截图查证解决「怎么把话送进来」，深度调查解决「查多深」，
    盯梢解决「查完之后呢」。

用法：
    python monitor.py --add "隔夜菜致癌"
    python monitor.py --list
    python monitor.py --run               # 把所有盯梢项重核一遍，输出变化
    python monitor.py --run --deep        # 深度重核
    python monitor.py --remove "隔夜菜致癌"

依赖：checker（会真的调元宝搜索和模型，注意额度）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

import checker

HERE = os.path.dirname(os.path.abspath(__file__))
WATCH_FILE = os.path.join(HERE, "watchlist.json")

# 每天重核会消耗额度。默认最多盯 8 条、单次最多跑 5 条，
# 免得一个失控的定时任务把一个月额度吃掉。
MAX_ITEMS = 8
MAX_PER_RUN = 5


def load() -> dict:
    if not os.path.isfile(WATCH_FILE):
        return {"items": []}
    try:
        with open(WATCH_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("items", [])
        return d
    except (json.JSONDecodeError, OSError):
        return {"items": []}


def save(d: dict):
    tmp = WATCH_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, WATCH_FILE)


def add(claim: str) -> tuple[bool, str]:
    claim = (claim or "").strip()
    if not claim:
        return False, "说法不能为空"
    d = load()
    if any(it["claim"] == claim for it in d["items"]):
        return False, "这条已经在盯了"
    if len(d["items"]) >= MAX_ITEMS:
        return False, f"最多盯 {MAX_ITEMS} 条，先删一条"
    d["items"].append({
        "claim": claim,
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "last_checked": None,
        "last": None,
        "history": [],
    })
    save(d)
    return True, f"已开始盯「{claim}」"


def remove(claim: str) -> tuple[bool, str]:
    d = load()
    n = len(d["items"])
    d["items"] = [it for it in d["items"] if it["claim"] != claim]
    if len(d["items"]) == n:
        return False, "没找到这条"
    save(d)
    return True, "已取消盯梢"


def _snapshot(r: dict) -> dict:
    v = r.get("verdict") or {}
    return {
        "code": v.get("code"),
        "label": v.get("label"),
        "headline": v.get("headline", ""),
        "sources": len(r.get("sources") or []),
        "discarded": r.get("discarded_count", 0),
        "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def diff(old: dict | None, new: dict) -> dict:
    """判断这次重核算不算「有变化」。

    分两级：
      结论变了（档位不同）→ 重要，一定要提醒
      档位没变但标题表述变了 → 轻微，提一句就行，别刷屏
    """
    if not old:
        return {"level": "first", "note": "首次核查"}
    if old.get("code") != new.get("code"):
        return {"level": "verdict",
                "note": f"结论变了：{old.get('label')} → {new.get('label')}"}
    if (old.get("headline") or "") != (new.get("headline") or ""):
        return {"level": "wording", "note": "档位未变，但结论表述有调整"}
    if (old.get("sources") or 0) != (new.get("sources") or 0):
        return {"level": "sources",
                "note": f"可采信来源数变化：{old.get('sources')} → {new.get('sources')}"}
    return {"level": "same", "note": "无变化"}


def run(deep: bool = False, limit: int = MAX_PER_RUN, verbose: bool = True) -> dict:
    d = load()
    items = d["items"][:limit]
    if not items:
        return {"checked": 0, "changes": [], "message": "盯梢列表是空的"}

    changes = []
    for i, it in enumerate(items, 1):
        claim = it["claim"]
        if verbose:
            print(f"[{i}/{len(items)}] 重核：{claim}", flush=True)
        try:
            # refresh=True：不走上次的结论缓存，真的重新查一遍，
            # 否则「盯梢」永远看不出变化——它只会读到同一份缓存。
            r = checker.analyze(claim, refresh=True, deep=deep, verbose=False)
        except Exception as e:
            changes.append({"claim": claim, "level": "error", "note": f"重核失败：{str(e)[:80]}"})
            continue

        new = _snapshot(r)
        dv = diff(it.get("last"), new)
        it["last"] = new
        it["last_checked"] = new["at"]
        it["history"] = (it.get("history") or [])[-29:] + [new]
        if dv["level"] != "same":
            changes.append({"claim": claim, "level": dv["level"], "note": dv["note"],
                            "label": new["label"], "headline": new["headline"]})
        if verbose:
            print(f"    → {new['label']}　{dv['note']}", flush=True)

    save(d)
    return {"checked": len(items), "changes": changes,
            "message": f"重核 {len(items)} 条，{len(changes)} 条有变化"}


def render_summary(res: dict) -> str:
    """给定时任务/终端看的纯文本摘要，别太长。"""
    lines = [f"持续盯梢 · {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    if not res.get("changes"):
        lines.append(f"重核 {res.get('checked', 0)} 条，全部无变化。")
        return "\n".join(lines)
    lines.append(f"重核 {res.get('checked', 0)} 条，{len(res['changes'])} 条有变化：")
    for c in res["changes"]:
        icon = {"verdict": "⚠", "wording": "·", "sources": "·", "first": "+", "error": "✗"}.get(c["level"], "·")
        lines.append(f"  {icon} {c['claim']}　{c['note']}")
        if c.get("headline"):
            lines.append(f"      {c['label']}｜{c['headline']}")
    if any(c["level"] == "verdict" for c in res["changes"]):
        lines.append("")
        lines.append("  说明：单次档位变化也可能来自模型表述漂移，不一定是外部证据真的变了。")
        lines.append("        判断是否持续变化，看 history 里连续几次是否一致（3 次以上同向才可信）。")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="持续盯梢")
    ap.add_argument("--add", default=None, help="盯住一条说法")
    ap.add_argument("--remove", default=None, help="取消盯梢")
    ap.add_argument("--list", action="store_true", help="列出当前盯梢")
    ap.add_argument("--run", action="store_true", help="全部重核一遍")
    ap.add_argument("--deep", action="store_true", help="深度重核")
    ap.add_argument("--limit", type=int, default=MAX_PER_RUN)
    args = ap.parse_args()

    did = False
    if args.add:
        ok, msg = add(args.add)
        print(("✓ " if ok else "✗ ") + msg); did = True
    if args.remove:
        ok, msg = remove(args.remove)
        print(("✓ " if ok else "✗ ") + msg); did = True

    if args.list or not did:
        d = load()
        if not d["items"]:
            print("盯梢列表是空的。用 --add \"某条说法\" 开始。")
        else:
            print(f"正在盯 {len(d['items'])} 条：")
            for it in d["items"]:
                last = it.get("last") or {}
                print(f"  · {it['claim']}")
                print(f"      加入 {it['added_at']}　上次 {it.get('last_checked') or '未核'}"
                      f"　当前 {last.get('label') or '—'}")
        did = True

    if args.run:
        res = run(deep=args.deep, limit=args.limit)
        print()
        print(render_summary(res))


if __name__ == "__main__":
    main()
