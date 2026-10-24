# -*- coding: utf-8 -*-
"""
llm_client.py —— LLM 客户端（OpenAI 兼容，纯标准库）

判定层要用它做两件事：
    1. 拆命题：一句养生说法 → 2~3 个「用一次搜索就能回答」的命题
    2. 判档位：看完古籍原文 + 现代来源摘要，判四档 + 一句话理由

为什么自己写而不用 openai 包：
    比赛现场装包容易翻车，这个文件纯标准库，拷走就能跑。

配置：项目根目录 `_llm.json`
    {"base_url": "https://xxx/v1", "api_key": "sk-xxx", "model": "xxx"}
    也认环境变量 OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL

用法：
    python llm_client.py --probe            # 列出这个 key 能用哪些模型
    python llm_client.py --test             # 发一句，验证通道
    python llm_client.py "你好" --json      # 单轮
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(HERE, "_llm.json")

TIMEOUT = 90
MAX_ATTEMPTS = 2
RETRY_BACKOFF = 2.0


class LLMError(RuntimeError):
    pass


def load_config() -> dict:
    cfg = {}
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                cfg = json.load(f)
        except json.JSONDecodeError as e:
            raise SystemExit(f"_llm.json 不是合法 JSON：{e}")
    base = (cfg.get("base_url") or os.environ.get("OPENAI_BASE_URL") or "").strip().rstrip("/")
    key = (cfg.get("api_key") or os.environ.get("OPENAI_API_KEY") or "").strip()
    model = (cfg.get("model") or os.environ.get("OPENAI_MODEL") or "").strip()
    if not base or not key:
        raise SystemExit(
            "没配好 LLM。在项目根目录建 _llm.json：\n"
            '  {"base_url": "https://xxx/v1", "api_key": "sk-xxx", "model": "xxx"}\n'
            "或设环境变量 OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL。\n"
            "不确定 base_url 时先问赛事群或看「我的福利」页面——别猜。"
        )
    return {"base_url": base, "api_key": key, "model": model}


def _request(url: str, key: str, payload: dict | None = None, method: str = "POST") -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="ignore")[:400]
        except Exception:
            pass
        raise LLMError(f"HTTP {e.code} {e.reason} {detail}") from e
    except urllib.error.URLError as e:
        raise LLMError(f"网络错误：{e.reason}") from e


def list_models(cfg: dict | None = None) -> list[str]:
    cfg = cfg or load_config()
    data = _request(f"{cfg['base_url']}/models", cfg["api_key"], method="GET")
    items = data.get("data") or data.get("models") or []
    out = []
    for it in items:
        if isinstance(it, dict):
            mid = it.get("id") or it.get("name")
            if mid:
                out.append(mid)
        elif isinstance(it, str):
            out.append(it)
    return out


def chat(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0.0,
    json_mode: bool = False,
    max_tokens: int = 1600,
) -> str:
    """返回纯文本。json_mode 时要求模型输出 JSON（部分网关不支持，会自动降级重试一次）。"""
    cfg = load_config()
    use_model = model or cfg["model"]
    if not use_model:
        raise LLMError("没指定 model：_llm.json 里补一个，或先跑 --probe 看有哪些")

    payload: dict = {
        "model": use_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    last: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            data = _request(f"{cfg['base_url']}/chat/completions", cfg["api_key"], payload)
            choices = data.get("choices") or []
            if not choices:
                raise LLMError(f"返回里没有 choices：{json.dumps(data, ensure_ascii=False)[:300]}")
            msg = choices[0].get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):  # 少数网关返回分段结构
                content = "".join(seg.get("text", "") for seg in content if isinstance(seg, dict))
            return (content or "").strip()
        except LLMError as e:
            last = e
            # 网关不支持 response_format 时降级重试一次
            if json_mode and attempt < MAX_ATTEMPTS and "response_format" in str(e):
                payload.pop("response_format", None)
                json_mode = False
                time.sleep(RETRY_BACKOFF)
                continue
            if attempt < MAX_ATTEMPTS and ("网络错误" in str(e) or "HTTP 5" in str(e) or "429" in str(e)):
                time.sleep(RETRY_BACKOFF)
                continue
            break
    raise LLMError(f"调用失败：{last}")


def chat_json(messages: list[dict], **kw) -> dict:
    """要 JSON 结果，容错解析（模型偶尔会套 ```json 代码块）。"""
    raw = chat(messages, json_mode=True, **kw)
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    s = s.strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            try:
                return json.loads(s[i:j + 1])
            except json.JSONDecodeError:
                pass
    raise LLMError(f"模型没返回合法 JSON：{raw[:300]}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="OpenAI 兼容 LLM 客户端")
    ap.add_argument("prompt", nargs="?", default="")
    ap.add_argument("--probe", action="store_true", help="列出可用模型")
    ap.add_argument("--test", action="store_true", help="发一句验证通道")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    if args.probe:
        cfg = load_config()
        print(f"网关：{cfg['base_url']}")
        print(f"当前 model：{cfg['model'] or '(未设置)'}")
        try:
            models = list_models(cfg)
        except LLMError as e:
            print(f"✗ {e}")
            return
        print(f"\n可用模型 {len(models)} 个：")
        for m in models:
            print(f"  - {m}")
        print("\n挑一个填进 _llm.json 的 model 字段。")
        return

    if args.test:
        cfg = load_config()
        print(f"网关：{cfg['base_url']}　模型：{args.model or cfg['model']}")
        try:
            t0 = time.time()
            out = chat([{"role": "user", "content": "只回四个字：通道正常"}], model=args.model, max_tokens=20)
            print(f"✓ {out}（{time.time()-t0:.1f}s）")
        except LLMError as e:
            print(f"✗ {e}")
        return

    if not args.prompt:
        ap.print_help()
        return

    print(chat([{"role": "user", "content": args.prompt}], model=args.model))


if __name__ == "__main__":
    main()
