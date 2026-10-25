# -*- coding: utf-8 -*-
"""
pack_deploy.py —— 组装部署目录

为什么不直接把整个项目目录发上去：
    项目里 `_ref/` 有 60MB，其中大半是原参考项目的 107 张图、xlsx 和无关脚本。
    真正要跑只需要那 42 个古籍 txt（约 15MB）。

    另外 `cache/wsa/`（4MB 原始搜索结果）也没必要上传——
    判定结果已经在 `cache/check/` 里，带上它就能让常见说法秒回。

用法：
    python pack_deploy.py           # → deploy/
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "deploy")

CODE = ["app.py", "checker.py", "guji_engine.py", "wsa_search.py", "source_tier.py",
        "llm_client.py", "monitor.py", "report.py"]
SECRETS = ["_wsa_key.txt", "_llm.json"]

BOOKS_SRC = os.path.join(HERE, "_ref", "参考项目-药食同源配方推荐系统", "data", "ancient_books")
BOOKS_DST = os.path.join(OUT, "_ref", "参考项目-药食同源配方推荐系统", "data", "ancient_books")

CACHE_CHECK_SRC = os.path.join(HERE, "cache", "check")
CACHE_CHECK_DST = os.path.join(OUT, "cache", "check")


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)

    for fn in CODE:
        src = os.path.join(HERE, fn)
        if not os.path.isfile(src):
            raise SystemExit(f"缺文件：{fn}")
        shutil.copy2(src, os.path.join(OUT, fn))

    # 密钥：上传后由我们自己的服务端读，页面不会暴露这些文件
    for fn in SECRETS:
        src = os.path.join(HERE, fn)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(OUT, fn))

    os.makedirs(BOOKS_DST, exist_ok=True)
    books = glob.glob(os.path.join(BOOKS_SRC, "*.txt"))
    if not books:
        raise SystemExit(f"找不到古籍语料：{BOOKS_SRC}")
    for b in books:
        shutil.copy2(b, os.path.join(BOOKS_DST, os.path.basename(b)))

    # 判定结论缓存：带上它，常见说法秒回，不用等网关
    os.makedirs(CACHE_CHECK_DST, exist_ok=True)
    cached = 0
    if os.path.isdir(CACHE_CHECK_SRC):
        for fn in os.listdir(CACHE_CHECK_SRC):
            if fn.endswith(".json"):
                shutil.copy2(os.path.join(CACHE_CHECK_SRC, fn), os.path.join(CACHE_CHECK_DST, fn))
                cached += 1

    # 兑换码必须跟着包走：服务器上自己生成一份空的 quota.json，
    # 你在本地生成的码就等于白发了。
    qsrc = os.path.join(HERE, "cache", "quota.json")
    if os.path.isfile(qsrc):
        os.makedirs(os.path.join(OUT, "cache"), exist_ok=True)
        shutil.copy2(qsrc, os.path.join(OUT, "cache", "quota.json"))
        qd = json.load(open(qsrc, encoding="utf-8"))
        print(f"  已带上兑换码 {len(qd.get('codes', {}))} 个")

    total = 0
    for root, _, files in os.walk(OUT):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))

    print(f"✓ 部署目录：{OUT}")
    print(f"  代码 {len(CODE)} 个 · 古籍 {len(books)} 部 · 预核结论 {cached} 条")
    print(f"  体积 {total/1024/1024:.1f} MB")


if __name__ == "__main__":
    main()
