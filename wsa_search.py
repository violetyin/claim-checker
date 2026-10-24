# -*- coding: utf-8 -*-
"""
wsa_search.py —— 元宝搜索客户端（1024 超能 Coding 周末 · 侦探赛道）

这是「现代证据」那半边：拆出来的每个命题，都要去网上查它有没有人这么说过、
说的是不是一回事。没有它，作品只剩古籍，等于废了一半。

鉴权：服务 API KEY（Bearer），比 TC3 签名省事得多
      POST https://api.wsa.cloud.tencent.com/SearchPro

用法：
    # 探一下开通的是哪一档、字段全不全（先跑这个）
    python wsa_search.py --probe

    # 查一条
    python wsa_search.py "亚硝酸盐 隔夜菜 限量标准"
    python wsa_search.py "红枣 铁含量 吸收率" --cnt 20 --industry acad
    python wsa_search.py "红枣补铁" --json          # 原始 JSON，给上层调用

    # 缓存与额度
    python wsa_search.py --stats                    # 看缓存命中与今日调用数

依赖：纯标准库
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
API_BASE = "https://api.wsa.cloud.tencent.com/SearchPro"

KEY_FILE = os.path.join(HERE, "_wsa_key.txt")
CACHE_DIR = os.path.join(HERE, "cache", "wsa")
USAGE_FILE = os.path.join(CACHE_DIR, "_usage.json")

# 作战卡红线：一次完整核查 = 2~3 命题 × 2~3 query ≈ 6~9 次调用
# 所以这里必须是「可预算」的，不能有无限重试。
TIMEOUT = 20
MAX_ATTEMPTS = 2          # 只允许一次重试
RETRY_BACKOFF = 1.5       # 秒

# 优先信源（写死，别让模型自由发挥）
PREFERRED_SITES = [
    "腾讯医典", "国家卫生健康委", "中国营养学会", "中国疾控中心",
    "中国居民膳食指南", "中国食物成分表", "人民日报", "科普中国",
]

DIM = "\033[2m"
END = "\033[0m"


# ---------------------------------------------------------------------------
# 密钥
# ---------------------------------------------------------------------------

def load_key() -> str:
    """优先环境变量，其次本地 _wsa_key.txt（单行，别提交到仓库）。"""
    k = os.environ.get("WSA_API_KEY", "").strip()
    if k:
        return k
    if os.path.isfile(KEY_FILE):
        with open(KEY_FILE, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
    raise SystemExit(
        "没找到 API KEY。两种给法：\n"
        f"  1) 写进 {KEY_FILE}（单行，纯 key，不要带 Bearer）\n"
        "  2) 设环境变量 WSA_API_KEY\n"
        "控制台：https://console.cloud.tencent.com/wsa"
    )


# ---------------------------------------------------------------------------
# 缓存：省额度 + 演示防抖（现场网络抖一下，有缓存就不翻车）
# ---------------------------------------------------------------------------

def _cache_path(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
    return os.path.join(CACHE_DIR, f"{h}.json")


def _cache_read(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _cache_write(path: str, data: dict):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 额度计数：别在调试里把 6000 次烧光
# ---------------------------------------------------------------------------

def _usage_bump(n: int = 1) -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    data = {"days": {}}
    if os.path.isfile(USAGE_FILE):
        try:
            with open(USAGE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    days = data.setdefault("days", {})
    days[today] = days.get(today, 0) + n
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(USAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return days[today]


def usage_today() -> int:
    if not os.path.isfile(USAGE_FILE):
        return 0
    try:
        with open(USAGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("days", {}).get(datetime.now().strftime("%Y-%m-%d"), 0)
    except (json.JSONDecodeError, OSError):
        return 0


def cache_stats() -> dict:
    n, size = 0, 0
    if os.path.isdir(CACHE_DIR):
        for fn in os.listdir(CACHE_DIR):
            if fn.endswith(".json") and not fn.startswith("_"):
                n += 1
                size += os.path.getsize(os.path.join(CACHE_DIR, fn))
    return {"entries": n, "kb": round(size / 1024, 1), "calls_today": usage_today()}


# ---------------------------------------------------------------------------
# 请求
# ---------------------------------------------------------------------------

class WsaError(RuntimeError):
    pass


def _post(payload: dict, key: str) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(API_BASE, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="ignore")[:500]
        except Exception:
            pass
        raise WsaError(f"HTTP {e.code} {e.reason} {detail}") from e
    except urllib.error.URLError as e:
        raise WsaError(f"网络错误：{e.reason}") from e
    except json.JSONDecodeError as e:
        raise WsaError(f"返回不是合法 JSON：{e}") from e


def _unwrap(data: dict) -> dict:
    """两种网关的回包形状可能不同，统一成内层 dict。"""
    if isinstance(data, dict) and "Response" in data and isinstance(data["Response"], dict):
        return data["Response"]
    return data


def _parse_pages(pages) -> list[dict]:
    """Pages 是「字符串数组」，每条还得再 json.loads 一次。
    漏了这一步，拿到的是转义乱码——这是这个接口最容易踩的坑。"""
    out = []
    for item in pages or []:
        if isinstance(item, dict):
            out.append(item)
            continue
        if not isinstance(item, str):
            continue
        try:
            obj = json.loads(item)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def search(
    query: str,
    *,
    cnt: int = 10,
    mode: int | None = 2,
    industry: str | None = None,
    freshness: str | None = None,
    site: str | None = None,
    use_cache: bool = True,
) -> dict:
    """查一条，返回统一结构。带缓存、带一次重试、带额度计数。

    cnt/mode/industry 极速版不支持——传了会被忽略或报参数错，
    所以先跑 --probe 确认版本；这里遇到参数错会自动降级重试一次。
    """
    payload: dict = {"Query": query, "Cnt": cnt}
    if mode is not None:
        payload["Mode"] = mode
    if industry:
        payload["Industry"] = industry
    if freshness:
        payload["Freshness"] = freshness
    if site:
        payload["Site"] = site

    cpath = _cache_path(payload)
    if use_cache:
        hit = _cache_read(cpath)
        if hit:
            hit = dict(hit)
            hit["cached"] = True
            return hit

    key = load_key()

    last_err: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = _unwrap(_post(payload, key))
            calls = _usage_bump(1)
            err = raw.get("Error")
            if err:
                # 极速版不支持 Cnt/Mode 这类参数时，降级成最简 payload 再来一次
                msg = f"{err.get('Code')}: {err.get('Message')}"
                if attempt < MAX_ATTEMPTS and ("InvalidParameter" in msg or "UnsupportedOperation" in msg):
                    payload = {"Query": query}
                    cpath = _cache_path(payload)
                    last_err = WsaError(msg)
                    time.sleep(RETRY_BACKOFF)
                    continue
                raise WsaError(msg)

            result = {
                "query": query,
                "version": raw.get("Version", ""),
                "msg": raw.get("Msg", ""),
                "request_id": raw.get("RequestId", ""),
                "pages": _parse_pages(raw.get("Pages")),
                "cached": False,
                "calls_today": calls,
            }
            _cache_write(cpath, result)
            return result
        except WsaError as e:
            last_err = e
            if attempt >= MAX_ATTEMPTS:
                break
            # 只在网络/限流类错误上退避一次；参数错不重试
            if not any(k in str(e) for k in ("网络错误", "HTTP 5", "RequestLimitExceeded", "InternalError")):
                break
            time.sleep(RETRY_BACKOFF)

    raise WsaError(f"查询失败：{last_err}")


# ---------------------------------------------------------------------------
# 结果整理
# ---------------------------------------------------------------------------

def rank_pages(pages: list[dict]) -> list[dict]:
    """按「权威度 + 相关性 + 是否优先信源」重排。
    旗舰版有 authority_level（0~5），极速/标准版没有，就退回 score + 站点白名单。"""
    def key(p: dict):
        score = float(p.get("score") or 0)
        auth = p.get("authority_level")
        auth_val = float(auth) if isinstance(auth, (int, float)) else 0.0
        site = (p.get("site") or "") + (p.get("url") or "")
        pref = 1.0 if any(s in site for s in PREFERRED_SITES) else 0.0
        has_date = 1 if p.get("date") else 0
        return (-(auth_val * 2 + score + pref * 1.5), -has_date)
    return sorted(pages, key=key)


def format_result(r: dict, limit: int = 10) -> str:
    line = "─" * 72
    out = [line]
    out.append(f"查询：{r['query']}")
    tag = "缓存" if r.get("cached") else "实时"
    out.append(f"版本：{r.get('version') or '未知'}　来源：{tag}　条数：{len(r['pages'])}")
    out.append(line)
    for i, p in enumerate(rank_pages(r["pages"])[:limit], 1):
        title = (p.get("title") or "(无标题)").strip()
        site = p.get("site") or ""
        date = (p.get("date") or "")[:10]
        score = p.get("score")
        auth = p.get("authority_level")
        meta = " · ".join(x for x in [site, date] if x)
        if score is not None:
            meta += f" · 相关{float(score):.2f}"
        if auth is not None:
            meta += f" · 权威{auth}"
        excerpt = (p.get("content") or p.get("passage") or "").replace("\n", " ").strip()
        url = p.get("url") or ""
        out.append(f"[{i}] {title}")
        out.append(f"    {DIM}{meta}{END}")
        if excerpt:
            out.append(f"    {excerpt[:180]}")
        out.append(f"    {url}")
        out.append("")
    out.append(line)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 探针：确认版本 / 字段 / 额度
# ---------------------------------------------------------------------------

def probe():
    """先用最省的参数打一发，摸清开通的是哪一档、字段给到哪。"""
    print("正在探测元宝搜索…")
    try:
        r = search("中国居民膳食指南 每日食盐摄入量建议", cnt=10, mode=2, use_cache=False)
    except WsaError as e:
        print(f"✗ 调用失败：{e}")
        print("\n排查顺序：")
        print("  1) key 是否带多余空格/换行，或误加了 'Bearer '")
        print("  2) 控制台是否已开通并处于有效期内（活动期 6000 次/月）")
        print("  3) 域名是否被网络环境拦截（api.wsa.cloud.tencent.com）")
        return

    v = r.get("version") or "(接口未返回 Version)"
    print(f"✓ 通了。RequestId={r.get('request_id')}")
    print(f"  版本：{v}")
    print(f"  返回条数：{len(r['pages'])}")
    print(f"  今日已调用：{r.get('calls_today')} 次")

    if r["pages"]:
        sample = r["pages"][0]
        fields = sorted(sample.keys())
        print(f"  字段：{', '.join(fields)}")
        caps = {
            "content（动态摘要）": "content" in sample,
            "authority_level（权威度）": "authority_level" in sample,
            "pics（配图）": "pics" in sample,
            "deeplinks（附件子链）": "deeplinks" in sample,
        }
        print("  能力：")
        for k, ok in caps.items():
            print(f"    {'✓' if ok else '✗'} {k}")

        if "authority_level" not in sample:
            print("\n  提示：没有 authority_level，说明不是旗舰版——")
            print("        判定层会退回 score + 站点白名单加权，逻辑照跑。")
        if "content" not in sample:
            print("  提示：没有 content，说明是极速版——摘要用 passage，够用。")
    else:
        print("  ⚠ 返回 0 条，检查 Query 是否被过滤，或额度是否用完。")

    print("\n前 3 条：")
    for i, p in enumerate(rank_pages(r["pages"])[:3], 1):
        print(f"  [{i}] {p.get('title', '')[:60]}")
        print(f"      {p.get('site', '')} {p.get('url', '')}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="元宝搜索客户端")
    ap.add_argument("query", nargs="?", default="")
    ap.add_argument("--cnt", type=int, default=10, choices=[10, 20, 30, 40, 50])
    ap.add_argument("--mode", type=int, default=2, choices=[0, 1, 2],
                    help="0 网页 / 1 垂直权威 / 2 混合")
    ap.add_argument("--industry", default=None, choices=["gov", "news", "acad", "finance"])
    ap.add_argument("--freshness", default=None, help="d7 / m3 / y2 / 2026-08-20")
    ap.add_argument("--site", default=None, help="限定站点，如 cnsoc.org")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    if args.stats:
        s = cache_stats()
        print(f"缓存条目：{s['entries']} 条 / {s['kb']} KB")
        print(f"今日调用：{s['calls_today']} 次（活动额度 6000 次/月）")
        return

    if args.probe:
        probe()
        return

    if not args.query:
        ap.print_help()
        return

    r = search(
        args.query, cnt=args.cnt, mode=args.mode, industry=args.industry,
        freshness=args.freshness, site=args.site, use_cache=not args.no_cache,
    )
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(format_result(r))


if __name__ == "__main__":
    main()
