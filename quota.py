# -*- coding: utf-8 -*-
"""
quota.py —— 按 IP 限次 + 兑换码加次

为什么需要：
    公开链接一贴出去，额度就是别人的了。一次核查 6~9 次搜索，
    6000 次/月的活动额度撑不住几十个人乱点。

规则（按你定的）：
    每个 IP 每天免费 5 次
    用完要兑换码才能继续
    一个码带固定次数，比如一个码 20 次，发给你想给的人

设计上的几个取舍：
    1. 命中缓存的查询不扣次数。那类请求不花一分钱额度，
       扣次数只会让用户莫名其妙（同一句话查两次掉两次额度）。
    2. 免费次数按「天」重置，不按「永久」。永久限额对真心想用的人是伤害。
    3. 兑换码用完的次数不回收，也不允许一个码无限次——
       码一旦漏出去，损失是可控的（固定次数），不是无底洞。

用法：
    python quota.py --gen 5 --uses 20 --note "小红书私信"   # 生成 5 个码，每个 20 次
    python quota.py --list                                  # 看所有码的使用情况
    python quota.py --stats                                 # 看今天各 IP 用了多少
    python quota.py --revoke ABC123                         # 作废一个码
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "cache", "quota.json")

FREE_PER_DAY = 5          # 每个 IP 每天免费次数
MAX_BONUS_PER_IP = 500    # 单个 IP 最多能兑换到的总次数，防止码被批量刷


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _load() -> dict:
    if not os.path.isfile(STORE):
        return {"ips": {}, "codes": {}}
    try:
        with open(STORE, "r", encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("ips", {})
        d.setdefault("codes", {})
        return d
    except (json.JSONDecodeError, OSError):
        return {"ips": {}, "codes": {}}


def _save(d: dict):
    os.makedirs(os.path.dirname(STORE), exist_ok=True)
    tmp = STORE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STORE)


def _ip_state(d: dict, ip: str) -> dict:
    st = d["ips"].setdefault(ip, {"date": _today(), "free_used": 0, "bonus": 0})
    if st.get("date") != _today():      # 跨天：免费额度重置，兑换来的次数保留
        st["date"] = _today()
        st["free_used"] = 0
    return st


def peek(ip: str) -> dict:
    """看这个 IP 还能查几次，不扣。"""
    d = _load()
    st = _ip_state(d, ip)
    free_left = max(0, FREE_PER_DAY - st["free_used"])
    return {
        "free_left": free_left,
        "bonus": st["bonus"],
        "total_left": free_left + st["bonus"],
        "free_per_day": FREE_PER_DAY,
    }


def consume(ip: str) -> dict:
    """扣一次。先用免费额度，再扣兑换来的次数。"""
    d = _load()
    st = _ip_state(d, ip)
    if st["free_used"] < FREE_PER_DAY:
        st["free_used"] += 1
        d["ips"][ip] = st
        _save(d)
        return {"ok": True, "from": "free", "left": FREE_PER_DAY - st["free_used"] + st["bonus"]}
    if st["bonus"] > 0:
        st["bonus"] -= 1
        d["ips"][ip] = st
        _save(d)
        return {"ok": True, "from": "bonus", "left": st["bonus"]}
    return {"ok": False, "from": None, "left": 0}


def gen_codes(n: int, uses: int, note: str = "") -> list[str]:
    """生成兑换码。去掉容易看错的 0/O、1/I。"""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    d = _load()
    out = []
    for _ in range(n):
        while True:
            code = "".join(secrets.choice(alphabet) for _ in range(6))
            if code not in d["codes"]:
                break
        d["codes"][code] = {
            "total": uses, "used": 0, "note": note,
            "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "revoked": False,
        }
        out.append(code)
    _save(d)
    return out


def redeem(ip: str, code: str) -> tuple[bool, str]:
    code = (code or "").strip().upper().replace(" ", "")
    if not code:
        return False, "请输入兑换码"
    d = _load()
    c = d["codes"].get(code)
    if not c:
        return False, "兑换码不存在"
    if c.get("revoked"):
        return False, "这个码已作废"
    if c["used"] >= c["total"]:
        return False, "这个码已经用完了"
    st = _ip_state(d, ip)
    if st["bonus"] >= MAX_BONUS_PER_IP:
        return False, "这台设备的次数已到上限"

    add = c["total"] - c["used"]
    st["bonus"] += add
    c["used"] = c["total"]
    d["ips"][ip] = st
    d["codes"][code] = c
    _save(d)
    return True, f"兑换成功，增加 {add} 次"


def revoke(code: str) -> tuple[bool, str]:
    d = _load()
    c = d["codes"].get((code or "").strip().upper())
    if not c:
        return False, "没有这个码"
    c["revoked"] = True
    _save(d)
    return True, "已作废"


def main():
    ap = argparse.ArgumentParser(description="限次与兑换码")
    ap.add_argument("--gen", type=int, default=0, help="生成几个码")
    ap.add_argument("--uses", type=int, default=20, help="每个码带几次")
    ap.add_argument("--note", default="", help="备注，比如发给谁")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--revoke", default="")
    args = ap.parse_args()

    if args.gen:
        codes = gen_codes(args.gen, args.uses, args.note)
        print(f"已生成 {len(codes)} 个码，每个 {args.uses} 次：")
        for c in codes:
            print(f"  {c}")

    if args.revoke:
        ok, msg = revoke(args.revoke)
        print(("✓ " if ok else "✗ ") + msg)

    if args.list:
        d = _load()
        if not d["codes"]:
            print("还没有生成过兑换码。")
        else:
            print(f"{'码':<8}{'已用/总数':<12}{'状态':<8}备注")
            for code, c in d["codes"].items():
                state = "作废" if c.get("revoked") else ("用完" if c["used"] >= c["total"] else "可用")
                print(f"{code:<8}{str(c['used']) + '/' + str(c['total']):<12}{state:<8}{c.get('note','')}")

    if args.stats:
        d = _load()
        today = _today()
        rows = [(ip, s) for ip, s in d["ips"].items() if s.get("date") == today]
        if not rows:
            print("今天还没有人查过。")
        else:
            print(f"今天有 {len(rows)} 个 IP 查询过：")
            for ip, s in sorted(rows, key=lambda x: -x[1].get("free_used", 0)):
                print(f"  {ip:<18} 免费已用 {s.get('free_used',0)}/{FREE_PER_DAY}　剩余兑换次数 {s.get('bonus',0)}")


if __name__ == "__main__":
    main()
