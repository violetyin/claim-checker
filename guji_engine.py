# -*- coding: utf-8 -*-
"""
guji_engine.py —— 古籍原文核查引擎（1024 超能 Coding 周末 · 侦探赛道）

作用：给一句话，去 42 部本草古籍全文里查——这句话到底有没有出处？原文怎么写？
这是「网上流传 → 古籍验真 → 现代验伪」三步里的第二步，不依赖任何 API、不烧额度。

用法：
    # 命令行查询
    python guji_engine.py "红枣补铁"
    python guji_engine.py "绿豆解百毒" --kw 绿豆 --limit 5
    python guji_engine.py "红枣补铁" --json          # 输出 JSON（给前端/接口用）

    # 起本地网页演示（给评委看的那一屏）
    python guji_engine.py --serve --port 8000
    # 浏览器打开 http://127.0.0.1:8000

依赖：纯标准库 + 无第三方包
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.stdout.reconfigure(encoding="utf-8")

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
BOOKS_DIR = os.path.join(HERE, "_ref", "参考项目-药食同源配方推荐系统", "data", "ancient_books")

# 朝代序（用于判断"最早出处"）
DYNASTY_ORDER = {
    "先秦": 0, "汉": 1, "晋": 2, "南朝": 3, "隋": 4, "唐": 5, "五代": 6,
    "宋": 7, "金": 8, "元": 9, "明": 10, "清": 11, "近代": 12,
}

# 药食同源 99 味（来自项目数据 整合.xlsx「按中药统计」）
HERBS = [
    "人参", "甘草", "灵芝", "菊花", "山银花", "金银花", "桑叶", "白芷", "紫苏", "山茱萸",
    "枸杞子", "砂仁", "沙棘", "当归", "薄荷", "党参", "槐花", "枣", "西洋参", "杜仲叶",
    "芫荽", "决明子", "火麻仁", "桔梗", "丁香", "玫瑰花", "罗汉果", "肉苁蓉", "栀子", "肉桂",
    "香薷", "覆盆子", "黄芪", "茯苓", "木瓜", "花椒", "高良姜", "山药", "白果", "黑芝麻",
    "西红花", "桑椹", "菊苣", "桃仁", "荜茇", "余甘子", "姜黄", "佛手", "荷叶", "麦芽",
    "山楂", "桔红", "肉豆蔻", "马齿苋", "夏枯草", "白扁豆", "薤白", "橘皮", "酸枣仁", "乌梅",
    "百合", "鱼腥草", "青果", "香橼", "藿香", "葛根", "昆布", "莱菔子", "草果", "八角茴香",
    "黄精", "玉竹", "胖大海", "蒲公英", "薏苡仁", "芡实", "小蓟", "天麻", "小茴香", "枳椇子",
    "益智仁", "淡豆豉", "赤小豆", "布渣叶", "刀豆", "杏仁", "郁李仁", "莲子", "鸡内金",
    "松花粉", "粉葛", "蜂蜜", "淡竹叶", "牡蛎", "阿胶", "乌梢蛇", "龙眼肉", "铁皮石斛", "榧子",
]

# 常见俗称 → 古籍写法（演示时最容易踩的就是这个）
ALIASES = {
    "红枣": "大枣", "大枣": "大枣", "黑枣": "大枣", "酸枣": "酸枣仁", "枣": "大枣",
    "薏米": "薏苡仁", "薏仁": "薏苡仁", "枸杞": "枸杞子", "杞子": "枸杞子",
    "生姜": "姜", "老姜": "姜", "淮山": "山药", "怀山药": "山药",
    "桂圆": "龙眼肉", "龙眼": "龙眼肉",
    "石斛": "铁皮石斛", "陈皮": "橘皮", "橘皮": "橘皮",
    "核桃": "胡桃", "芝麻": "黑芝麻", "杏仁": "杏仁",
    "猪皮": "猪", "骨头": "骨",
}

# 补充：古籍里常见但不在 99 味里的高频食材（让核查更实用）
EXTRA_HERBS = [
    "绿豆", "赤小豆", "姜", "蒜", "葱", "葱白", "韭", "萝卜", "冬瓜", "丝瓜", "苦瓜",
    "胡桃", "栗", "梨", "柿", "橘", "柚", "梅", "桃", "李", "杏", "石榴", "甘蔗",
    "牛乳", "羊乳", "猪", "牛", "羊", "鸡", "鸭", "鱼", "鳖", "蟹", "虾",
    "酒", "醋", "酱", "盐", "蜜", "茶", "粳米", "糯米", "粟", "麦", "面", "豆",
]

# 名书权重：0 最先展示（这几部是大家最常引用的）
BOOK_WEIGHT = {
    "本草纲目": 0,
    "神农本草经": 1,
    "证类本草": 2,
    "新修本草": 3,
    "本草经集注": 4,
    "食疗本草": 5,
    "食鉴本草": 6,
}

STOP_CHARS = set("的了是在有个和与或之其所以能可会要多很就都也还被把让对从到为以于")

# ---------------------------------------------------------------------------
# 语料加载
# ---------------------------------------------------------------------------

SENT_SPLIT = re.compile(r"[。！？；\n\r]+")


@dataclass
class Passage:
    book: str
    dynasty: str
    author: str
    sentence: str
    context: str
    score: int
    is_entry: bool = False


@dataclass
class Book:
    title: str
    dynasty: str
    author: str
    path: str
    text: str


def parse_filename(fn: str) -> tuple[str, str, str]:
    """本草纲目-明-李时珍.txt → (书名, 朝代, 作者)"""
    stem = os.path.splitext(fn)[0]
    # 去掉下载工具加的日期尾巴
    stem = re.sub(r"_\d{8,}.*$", "", stem)
    stem = re.sub(r"\(\d+\)$", "", stem)
    parts = stem.split("-")
    title = parts[0].strip()
    dynasty = parts[1].strip() if len(parts) > 1 else "未知"
    author = parts[2].strip() if len(parts) > 2 else "佚名"
    return title, dynasty, author


def read_text(path: str) -> str:
    """古籍 txt 编码混杂（实测 42 部全部是 GB18030），先试 UTF-8 再回落。"""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def load_books(books_dir: str = BOOKS_DIR) -> list[Book]:
    if not os.path.isdir(books_dir):
        raise SystemExit(f"找不到古籍目录：{books_dir}\n（请确认 _ref/ 已克隆到本文件同级目录）")
    books = []
    for fn in sorted(os.listdir(books_dir)):
        if not fn.lower().endswith(".txt"):
            continue
        path = os.path.join(books_dir, fn)
        text = read_text(path)
        if not text.strip():
            continue
        title, dynasty, author = parse_filename(fn)
        books.append(Book(title=title, dynasty=dynasty, author=author, path=path, text=text))
    return books


# 全局语料（首次使用时加载）
_BOOKS: list[Book] | None = None
_PARAS: list[tuple[str, str, str, int, str]] | None = None  # (book, dynasty, author, idx, para)
_BY_BOOK: dict[str, list[str]] = {}


# 本草古籍是「一段一个词条」，段内用特殊字符分隔药名与性味。
# 所以按行切段，而不是按句号切，否则词条会被切碎、上下文全错。
PUA = re.compile(r"[\ue000-\uf8ff\u3000\ufeff\u200b]")


def clean_line(s: str) -> str:
    return PUA.sub("", s).strip()


def ensure_loaded():
    global _BOOKS, _PARAS, _BY_BOOK
    if _BOOKS is not None:
        return
    _BOOKS = load_books()
    paras: list[tuple[str, str, str, int, str]] = []
    by_book: dict[str, list[str]] = {}
    for b in _BOOKS:
        rows = []
        buf = ""
        for line in b.text.splitlines():
            line = clean_line(line)
            if not line:
                continue
            # 纲目这类书「药名」单独一行、下一行才是【气味】。
            # 所以把短行累积成段（>=60 字再收），词条头才不会被切断；
            # 本身就很长的行（神农本草经那种）单独成段，不参与合并。
            if len(line) >= 40:
                if buf:
                    rows.append(buf)
                    buf = ""
                rows.append(line)
                continue
            buf += line
            if len(buf) >= 60:
                rows.append(buf)
                buf = ""
        if buf:
            rows.append(buf)
        by_book[b.title] = rows
        for i, line in enumerate(rows):
            paras.append((b.title, b.dynasty, b.author, i, line))
    _PARAS = paras
    _BY_BOOK = by_book


def corpus_stats() -> dict:
    ensure_loaded()
    return {
        "books": len(_BOOKS or []),
        "sentences": len(_PARAS or []),
        "chars": sum(len(b.text) for b in (_BOOKS or [])),
    }


# ---------------------------------------------------------------------------
# 关键词抽取
# ---------------------------------------------------------------------------

def extract_keywords(query: str) -> list[str]:
    """从一句话里找出「古籍该怎么查」的词。先查别名表，再查本草名，最后兜底。"""
    q = query.strip()
    kws: list[str] = []

    # 1) 俗称优先（红枣 → 枣）
    for alias, real in ALIASES.items():
        if alias in q and real not in kws:
            kws.append(real)

    # 2) 本草名 / 补充食材（长词优先，避免"枣"抢了"酸枣仁"）
    pool = sorted(set(HERBS + EXTRA_HERBS), key=len, reverse=True)
    for h in pool:
        if h in q and h not in kws:
            # 若已有更长的词包含它，跳过
            if any(h != k and h in k for k in kws):
                continue
            kws.append(h)

    # 3) 兜底：把原句当成一个整体去精确匹配（多半查不到，但流程不能断）
    if not kws:
        cleaned = "".join(ch for ch in q if ch not in STOP_CHARS and ch not in "，,。.、 　")
        if len(cleaned) >= 2:
            kws.append(cleaned)
    return kws


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------

ENTRY_HEAD = re.compile(r"^.{0,6}【(释名|集解|气味|修治)")
ENTRY_MARK = re.compile(r"[^【]{0,24}【(释名|气味|主治|集解|发明|附方|正误)")


def make_excerpt(para: str, kw: str, width: int = 260) -> tuple[str, bool]:
    """从一大段里截出可读的原文窗口，并判断它是不是本草词条。
    纲目这类书一行很长，词条埋在半页纸里，硬切会切碎，所以按命中位置开窗。"""
    i = para.find(kw)
    if i < 0:
        return para[:width], False

    # 词条头只认「药名 + 【释名/集解/气味/修治】」或行首，避免把"畏绿豆粉者【主治】"误判
    is_entry = i <= 2 or bool(ENTRY_HEAD.match(para[i:i + 30]))
    start = i if is_entry else max(0, i - 30)
    end = min(len(para), start + width)
    frag = para[start:end]

    cut = max(frag.rfind("。"), frag.rfind("；"))
    if is_entry:
        # 词条本身就该读全（释名／气味／主治），只在明显过长时才收
        if len(frag) > 220:
            cut = max(frag.rfind("。", 0, 220), frag.rfind("；", 0, 220))
            if cut > 80:
                frag = frag[:cut + 1]
    elif cut > width * 0.5:
        frag = frag[:cut + 1]

    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(para) else ""
    return f"{prefix}{frag}{suffix}", is_entry


def search(keywords: list[str], limit: int = 8) -> dict:
    """在 42 部古籍里逐条检。排序：词条优先，其次散见提及；同档按朝代早晚。"""
    ensure_loaded()
    assert _PARAS is not None
    hits: list[Passage] = []
    books_hit: set[str] = set()

    for book, dynasty, author, idx, para in _PARAS:
        matched = [kw for kw in keywords if kw and kw in para]
        if not matched:
            continue
        books_hit.add(book)
        kw = matched[0]
        excerpt, is_entry = make_excerpt(para, kw)
        hits.append(Passage(book, dynasty, author, excerpt, para[:200], len(matched), is_entry))

    hits.sort(key=lambda p: (0 if p.is_entry else 1, -p.score, len(p.sentence) < 30))

    # 同一部书只留最能说明问题的一条，避免一本书刷屏、把纲目挤掉
    best: dict[str, Passage] = {}
    for p in hits:
        cur = best.get(p.book)
        if cur is None or (p.is_entry and not cur.is_entry) or (p.is_entry == cur.is_entry and len(p.sentence) > len(cur.sentence)):
            best[p.book] = p
    hits = list(best.values())

    # 名书优先（大家最常引用的那几本），其余按朝代
    hits.sort(key=lambda p: (BOOK_WEIGHT.get(p.book, 9), DYNASTY_ORDER.get(p.dynasty, 99)))

    earliest = None
    if hits:
        e = min(hits, key=lambda p: DYNASTY_ORDER.get(p.dynasty, 99))
        earliest = {"book": e.book, "dynasty": e.dynasty, "author": e.author, "sentence": e.sentence}

    return {
        "keywords": keywords,
        "verdict": "found" if hits else "not_found",
        "total_hits": len(hits),
        "books_hit": len(books_hit),
        "earliest": earliest,
        "passages": [asdict(p) for p in hits[:limit]],
        "corpus": corpus_stats(),
    }

    earliest = None
    if hits:
        e = min(hits, key=lambda p: DYNASTY_ORDER.get(p.dynasty, 99))
        earliest = {"book": e.book, "dynasty": e.dynasty, "author": e.author, "sentence": e.sentence}

    return {
        "keywords": keywords,
        "verdict": "found" if hits else "not_found",
        "total_hits": len(hits),
        "books_hit": len(books_hit),
        "earliest": earliest,
        "passages": [asdict(p) for p in hits[:limit]],
        "corpus": corpus_stats(),
    }


def check(query: str, limit: int = 8, keywords: list[str] | None = None) -> dict:
    kws = keywords or extract_keywords(query)
    r = search(kws, limit=limit)
    r["query"] = query
    # 一句话结论（演示时直接念出来）
    if r["verdict"] == "not_found":
        r["headline"] = f"42 部本草古籍全文检索，未找到与「{query}」相关的记载"
    else:
        e = r["earliest"]
        r["headline"] = (
            f"在 {r['books_hit']} 部古籍中找到 {r['total_hits']} 处记载，"
            f"最早见于《{e['book']}》（{e['dynasty']}·{e['author']}）"
        )
    return r


# ---------------------------------------------------------------------------
# 终端输出
# ---------------------------------------------------------------------------

def print_result(r: dict):
    line = "─" * 66
    print(line)
    print(f"查询：{r['query']}")
    print(f"检索词：{'、'.join(r['keywords'])}")
    print(f"语料：{r['corpus']['books']} 部古籍 / {r['corpus']['sentences']} 句 / {r['corpus']['chars']//10000} 万字")
    print(line)
    print(f"■ {r['headline']}")
    if r["verdict"] == "found":
        print(f"■ 涉及书目：{r['books_hit']} 部")
        print(line)
        for i, p in enumerate(r["passages"], 1):
            print(f"[{i}] 《{p['book']}》{p['dynasty']}·{p['author']}")
            print(f"    原文：{p['sentence'][:120]}")
            if p.get("context") and p["context"] != p["sentence"]:
                print(f"    上下文：{p['context'][:200]}")
            print()
    else:
        print("■ 这条说法没有古籍依据——不是「另一个说法」，是「古人没这么讲过」。")
    print(line)


# ---------------------------------------------------------------------------
# 本地网页演示
# ---------------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>古籍核查 · 这句话古人真说过吗</title>
<style>
  :root{--ink:#1a1a1a;--sub:#6b6b6b;--line:#e6e2da;--bg:#faf8f4;--card:#fff;--gold:#8a6d3b;--red:#b3261e;--green:#2e6b4f}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
  .wrap{max-width:780px;margin:0 auto;padding:32px 20px 80px}
  h1{font-size:26px;margin:0 0 6px;letter-spacing:.5px}
  .sub{color:var(--sub);font-size:14px;margin-bottom:24px}
  .box{display:flex;gap:10px}
  input{flex:1;padding:14px 16px;font-size:16px;border:1px solid var(--line);border-radius:12px;background:#fff;outline:none}
  input:focus{border-color:var(--gold)}
  button{padding:14px 22px;font-size:16px;border:0;border-radius:12px;background:var(--ink);color:#fff;cursor:pointer}
  button:disabled{opacity:.4;cursor:default}
  .hint{color:var(--sub);font-size:13px;margin:10px 0 0}
  .hint b{cursor:pointer;border-bottom:1px dashed var(--line);margin-right:12px}
  .head{margin:26px 0 16px;padding:16px 18px;border-radius:14px;background:#fff;border:1px solid var(--line)}
  .head .k{font-size:13px;color:var(--sub);letter-spacing:1px}
  .head .v{font-size:18px;font-weight:600;margin-top:4px}
  .head.found{border-left:4px solid var(--green)}
  .head.none{border-left:4px solid var(--red)}
  .card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin-bottom:12px}
  .card .bk{font-size:13px;color:var(--gold);letter-spacing:.5px}
  .card .tx{margin-top:8px}
  .meta{color:var(--sub);font-size:13px;margin-top:24px;border-top:1px solid var(--line);padding-top:14px}
  .load{color:var(--sub);font-size:14px}
</style></head>
<body><div class="wrap">
  <h1>这句话，古人真说过吗</h1>
  <div class="sub">42 部本草古籍全文检索 · 不做推断，只给原文</div>
  <div class="box">
    <input id="q" placeholder="输入一句养生说法，例如：红枣补铁" autocomplete="off">
    <button id="go">核查</button>
  </div>
  <p class="hint">试试：<b>红枣补铁</b><b>绿豆解百毒</b><b>隔夜菜致癌</b><b>阿胶补血</b></p>
  <div id="out"></div>
  <div class="meta" id="corpus"></div>
</div>
<script>
const q=document.getElementById('q'),go=document.getElementById('go'),out=document.getElementById('out');
document.querySelectorAll('.hint b').forEach(b=>b.onclick=()=>{q.value=b.textContent;run()});
q.addEventListener('keydown',e=>{if(e.key==='Enter')run()});
go.onclick=run;
async function run(){
  const v=q.value.trim(); if(!v) return;
  go.disabled=true; out.innerHTML='<p class="load">正在 42 部古籍里逐句查…</p>';
  try{
    const r=await fetch('/api/check?q='+encodeURIComponent(v)).then(x=>x.json());
    document.getElementById('corpus').textContent=`语料：${r.corpus.books} 部古籍 / ${r.corpus.sentences} 句 / ${Math.round(r.corpus.chars/10000)} 万字　检索词：${r.keywords.join('、')}`;
    let h=`<div class="head ${r.verdict}"><div class="k">核查结论</div><div class="v">${r.headline}</div></div>`;
    if(r.verdict==='found'){
      h+=r.passages.map((p,i)=>`<div class="card"><div class="bk">《${p.book}》${p.dynasty}·${p.author}</div><div class="tx">${p.sentence}</div></div>`).join('');
    }else{
      h+=`<div class="card">这条说法没有古籍依据。<br><span style="color:#6b6b6b">不是"另一个说法"，是"古人没这么讲过"。</span></div>`;
    }
    out.innerHTML=h;
  }catch(e){ out.innerHTML='<p class="load">查询失败：'+e+'</p>'; }
  go.disabled=false;
}
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/check":
            qs = parse_qs(u.query)
            q = (qs.get("q") or [""])[0]
            limit = int((qs.get("limit") or ["8"])[0])
            body = json.dumps(check(q, limit=limit), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        page = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, *a):
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="古籍原文核查引擎")
    ap.add_argument("query", nargs="?", default="", help="要核查的一句话")
    ap.add_argument("--kw", action="append", default=None, help="指定检索词（可多次）")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--serve", action="store_true", help="起本地网页演示")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--stats", action="store_true", help="只看语料规模")
    args = ap.parse_args()

    if args.stats:
        s = corpus_stats()
        print(f"{s['books']} 部古籍 / {s['sentences']} 句 / {s['chars']} 字")
        return

    if args.serve:
        s = corpus_stats()
        print(f"语料已载入：{s['books']} 部 / {s['sentences']} 句 / {s['chars']} 字")
        print(f"打开 http://127.0.0.1:{args.port}")
        ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
        return

    if not args.query:
        ap.print_help()
        return

    r = check(args.query, limit=args.limit, keywords=args.kw)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print_result(r)


if __name__ == "__main__":
    main()
