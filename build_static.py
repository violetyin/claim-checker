# -*- coding: utf-8 -*-
"""
build_static.py —— 把已锁定的核查结论导出成一份静态页面

为什么要这个：
    app.py 跑在 127.0.0.1，只有本机能打开。官方要的是「能打开的作品」，
    评委事后点你提交的链接，不能是 404，也不能是一个跑不起来的仓库。

    所以导出一份纯前端页：无后端、无密钥、无 60MB 语料，
    四条结论直接固化进去，谁点开都能看到完整效果。

    诚实标注：这是静态演示版，结论已固化；完整版能现场核任意说法。

用法：
    python build_static.py              # → demo/index.html
"""
from __future__ import annotations

import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK_DIR = os.path.join(HERE, "cache", "check")
OUT_DIR = os.path.join(HERE, "demo")
OUT_FILE = os.path.join(OUT_DIR, "index.html")

# 展示顺序：先给最典型的「部分有据」，杀手锏放最后但要有红点提示
ORDER = ["红枣补铁", "隔夜菜致癌", "绿豆解百毒", "红枣配薄荷能排肝毒"]

HIGHLIGHT = {"红枣配薄荷能排肝毒": "我编的"}


def slim(r: dict) -> dict:
    """只留页面要用的字段。原始 JSON 一条约 350KB，压完几十 KB。"""
    props = []
    for p in r.get("propositions", []):
        cross = p.get("cross") or {}
        props.append({
            "text": p.get("text", ""),
            "verdict": p.get("verdict"),
            "label": p.get("label"),
            "reason": p.get("reason", ""),
            "queries": p.get("queries") or [],
            "sources": [{
                "gid": s.get("gid"),
                "title": s.get("title", ""),
                "url": s.get("url", ""),
                "site": s.get("site", ""),
                "date": (s.get("date") or "")[:10],
                "tier": s.get("tier"),
                "tier_label": s.get("tier_label", ""),
            } for s in (p.get("sources") or [])],
            "discarded": len(p.get("discarded") or []),
            "domains": cross.get("independent_domains", 0),
            "t1": cross.get("t1_count", 0),
        })

    a = r.get("ancient") or {}
    corpus = a.get("corpus") or {}
    return {
        "claim": r["claim"],
        "verdict": r["verdict"],
        "ancient_note": r.get("ancient_note", ""),
        "propositions": props,
        "ancient": {
            "verdict": a.get("verdict", ""),
            "books_hit": a.get("books_hit", 0),
            "headline": a.get("headline", ""),
            "passages": [{
                "book": p.get("book", ""),
                "dynasty": p.get("dynasty", ""),
                "author": p.get("author", ""),
                "sentence": p.get("sentence", ""),
            } for p in (a.get("passages") or [])[:3]],
            "books": corpus.get("books", 0),
            "chars": corpus.get("chars", 0),
        },
        "discarded_count": r.get("discarded_count", 0),
        "stats": {
            "searches": (r.get("stats") or {}).get("searches", 0),
            "llm_calls": (r.get("stats") or {}).get("llm_calls", 0),
        },
    }


def collect() -> list[dict]:
    if not os.path.isdir(CHECK_DIR):
        raise SystemExit(f"找不到结论缓存目录：{CHECK_DIR}\n先跑 python checker.py \"红枣补铁\" 生成。")

    # 只导出白名单里的四条。
    # 缓存里可能有随手试的说法（比如涉及疾病治疗的），那些不进公开演示页——
    # 我们自己写了「不碰疾病、不给诊疗建议」，就不能自己第一个破这条线。
    by_claim: dict[str, dict] = {}
    for fn in os.listdir(CHECK_DIR):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(CHECK_DIR, fn), "r", encoding="utf-8") as f:
            d = slim(json.load(f))
        by_claim[d["claim"]] = d

    items = [by_claim[c] for c in ORDER if c in by_claim]
    missing = [c for c in ORDER if c not in by_claim]
    if missing:
        print(f"⚠ 缓存里缺这几条，未导出：{'、'.join(missing)}")
    if not items:
        raise SystemExit("白名单里的说法一条都没核过，先跑 checker.py。")
    return items


TEMPLATE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>这句能信吗 · 双源核查</title>
<style>
  :root{
    --ink:#1c1c1e; --ink2:#3a3a3c; --sub:#86868b; --faint:#a8a8ad;
    --line:#e8e6e1; --bg:#fbfaf8; --card:#fff; --gold:#8a6d3b;
    --ok:#2e6b4f; --warn:#b07c1a; --bad:#b3261e; --gray:#8e8e93;
  }
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--ink);
    font:16px/1.72 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
    -webkit-font-smoothing:antialiased;padding:40px 20px 80px}
  .wrap{max-width:820px;margin:0 auto}

  header{margin-bottom:22px}
  h1{font-size:26px;font-weight:660;letter-spacing:.3px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .tag{font-size:12px;color:var(--gold);border:1px solid #e3d9c8;background:#fdfbf6;
       border-radius:100px;padding:2px 10px;font-weight:500}
  .sub{color:var(--sub);font-size:14px;margin-top:8px;line-height:1.7}

  .bar{background:#fdfbf6;border:1px solid #ece5d6;border-radius:11px;
       padding:10px 14px;font-size:12.5px;color:var(--gold);margin:18px 0 20px;line-height:1.65}
  .bar b{font-weight:650}

  .tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:24px}
  .tab{padding:9px 15px;border:1px solid var(--line);background:#fff;border-radius:100px;
       font-size:14px;cursor:pointer;color:var(--ink2);transition:all .15s;font-family:inherit}
  .tab:hover{border-color:#cfc7b8}
  .tab.on{background:var(--ink);color:#fff;border-color:var(--ink)}
  .tab .dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--bad);
            margin-left:6px;vertical-align:2px}

  .verdict{background:var(--card);border:1px solid var(--line);border-radius:16px;
           padding:20px 22px;margin-bottom:20px}
  .vhead{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .vchip{font-size:13px;font-weight:600;padding:3px 11px;border-radius:100px;color:#fff}
  .vtitle{font-size:19px;font-weight:650;line-height:1.45}
  .vreason{margin-top:12px;color:var(--ink2);font-size:15px;line-height:1.78}
  .vmeta{margin-top:14px;padding-top:12px;border-top:1px solid var(--line);
         font-size:12.5px;color:var(--sub);display:flex;gap:15px;flex-wrap:wrap}
  .vmeta .k{color:var(--faint)}

  .sect{font-size:13px;letter-spacing:1.5px;color:var(--sub);margin:30px 0 12px;font-weight:500}

  .prop{background:var(--card);border:1px solid var(--line);border-left-width:3px;
        border-radius:13px;padding:15px 18px;margin-bottom:11px}
  .ptop{display:flex;align-items:flex-start;gap:9px}
  .pdot{width:7px;height:7px;border-radius:50%;margin-top:9px;flex:none}
  .ptext{font-size:15.5px;font-weight:550;flex:1;line-height:1.6}
  .pverdict{font-size:11.5px;padding:2px 8px;border-radius:6px;white-space:nowrap;margin-top:3px}
  .preason{font-size:14px;color:var(--ink2);margin:9px 0 0 16px;line-height:1.7}
  .srcs{margin:11px 0 0 16px;font-size:13px}
  .src{display:block;padding:7px 0;border-top:1px dashed #efede8;color:var(--ink2);
       text-decoration:none;line-height:1.55}
  .src:first-child{border-top:0}
  .src:hover .stitle{color:var(--gold);text-decoration:underline}
  .snum{color:var(--faint);margin-right:6px;font-variant-numeric:tabular-nums}
  .stitle{font-weight:450}
  .smeta{color:var(--sub);font-size:12px;margin-left:16px}
  .tier{display:inline-block;font-size:10.5px;border-radius:4px;padding:0 5px;margin-left:6px;
        background:#f0eee9;color:var(--gold);vertical-align:1px}

  .ancient{background:#fdfcf8;border:1px solid #e9e3d6;border-radius:16px;padding:20px 22px}
  .ahead{font-size:14px;color:var(--gold);font-weight:500}
  .anote{font-size:14px;color:var(--ink2);margin-top:9px;line-height:1.7}
  .apass{margin-top:14px;padding-top:13px;border-top:1px solid #ece6d9}
  .abook{font-size:12.5px;color:var(--gold);letter-spacing:.4px}
  .atext{font-family:Georgia,"Songti SC","STSong","SimSun",serif;font-size:15.5px;
         color:#2c2a26;margin-top:5px;line-height:1.9}
  .quoting{font-size:14px;color:var(--sub);margin-top:10px;line-height:1.75}

  .cut{margin-top:12px;font-size:12.5px;color:var(--sub);background:#f6f5f2;
       border-radius:9px;padding:9px 12px;line-height:1.6}

  footer{margin-top:44px;padding-top:18px;border-top:1px solid var(--line);
         font-size:12px;color:var(--sub);line-height:1.9}
  footer b{font-weight:600;color:var(--ink2)}
</style></head>
<body><div class="wrap">
  <header>
    <h1>这句能信吗 <span class="tag">双源核查</span></h1>
    <p class="sub">一句话进来，拆成命题，分别去查：古书里有没有这句话，现代权威怎么说。<br>查得到就给来源，查不到就直说查不到。</p>
  </header>

  <div class="bar">
    <b>这是静态演示版。</b>四条结论已固化——不需要后端、不需要联网、断网也能看。
    完整版能现场核任意说法（输入框 → 拆命题 → 双源搜 → 出结论）。
  </div>

  <div class="tabs" id="tabs"></div>
  <div id="out"></div>

  <footer>
    <b>边界说明</b>　本工具只做资料核对，不提供诊疗建议、不替代医生，不做疾病诊断、不给用药或停药建议。
    古籍文献记载 ≠ 现代临床疗效证据，两者在本页分开标注、不互相替代。<br>
    证据只采信政府、卫健委、疾控、央媒、学术机构与主流媒体来源；营销号、偏方站、无署名转载一律剔除，不计入结论。<br>
    语料：42 部本草古籍全文，共 8,774,342 字，逐段匹配。现代证据来自腾讯云元宝搜索。
  </footer>
</div>
<script>
const DATA = __DATA__;
const C={supported:'#2e6b4f',partial:'#b07c1a',contrary:'#b3261e',ancient_only:'#8a6d3b',not_found:'#8e8e93'};
const ICON={supported:'✓',partial:'◐',contrary:'✕',ancient_only:'❖',not_found:'∅'};
const HL=__HL__;
const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// 标题前加"我编的"小红点的两条
const tabsEl=document.getElementById('tabs'), outEl=document.getElementById('out');
DATA.forEach((d,i)=>{
  const b=document.createElement('button');
  b.className='tab'+(i===0?' on':'');
  b.innerHTML=esc(d.claim)+(HL[d.claim]?'<span class="dot"></span>':'');
  b.onclick=()=>{document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
                 b.classList.add('on'); render(i);};
  tabsEl.appendChild(b);
});

function render(i){
  const r=DATA[i], v=r.verdict, col=C[v.code]||'#8e8e93', ic=ICON[v.code]||'';
  let h='';

  h+=`<div class="verdict" style="border-left:3px solid ${col}">
    <div class="vhead">
      <span class="vchip" style="background:${col}">${ic} ${esc(v.label)}</span>
      <span class="vtitle">${esc(v.headline)}</span>
    </div>
    ${v.reason?`<div class="vreason">${esc(v.reason)}</div>`:''}
    <div class="vmeta">
      <span><span class="k">说法</span> ${esc(r.claim)}</span>
      <span><span class="k">拆出命题</span> ${r.propositions.length} 个</span>
      <span><span class="k">剔除低质</span> ${r.discarded_count} 条</span>
      <span><span class="k">古籍语料</span> ${r.ancient.books} 部</span>
    </div>
  </div>`;

  h+='<div class="sect">拆成命题，逐条查证</div>';
  r.propositions.forEach(p=>{
    const c=C[p.verdict]||'#8e8e93';
    h+=`<div class="prop" style="border-left-color:${c}">
      <div class="ptop">
        <span class="pdot" style="background:${c}"></span>
        <span class="ptext">${esc(p.text)}</span>
        <span class="pverdict" style="color:${c};background:${c}14">${ICON[p.verdict]||''} ${esc(p.label)}</span>
      </div>
      ${p.reason?`<div class="preason">${esc(p.reason)}</div>`:''}`;
    if(p.sources&&p.sources.length){
      h+='<div class="srcs">'+p.sources.slice(0,4).map(s=>
        `<a class="src" href="${esc(s.url)}" target="_blank" rel="noopener">
           <span class="snum">[${s.gid||''}]</span><span class="stitle">${esc(s.title)}</span>
           <span class="tier">T${s.tier} ${esc(s.tier_label)}</span>
           <div class="smeta">${esc(s.site||'')} · ${esc(s.date||'')}</div>
         </a>`).join('')+'</div>';
    }else{
      h+='<div class="srcs"><span class="smeta" style="margin:0">没有检索到可采信来源</span></div>';
    }
    h+='</div>';
  });

  if(r.discarded_count>0){
    h+=`<div class="cut">另有 ${r.discarded_count} 条结果被剔除，未计入结论：
        偏方站、营销号、无署名转载。这类内容在养生话题里占比最高，也是谣言的主要来源。</div>`;
  }

  const a=r.ancient;
  h+='<div class="sect">同一句话，古书里怎么说</div>';
  h+=`<div class="ancient"><div class="ahead">${esc(a.headline)}</div>`;
  const note=(r.ancient_note||'').trim(), sq=s=>String(s).replace(/\s/g,'');
  if(note && sq(note).slice(0,12)!==sq(a.headline).slice(0,12)) h+=`<div class="anote">${esc(note)}</div>`;
  if(a.passages&&a.passages.length){
    a.passages.forEach(p=>{
      h+=`<div class="apass">
        <div class="abook">《${esc(p.book)}》${esc(p.dynasty)}·${esc(p.author)}</div>
        <div class="atext">${esc(p.sentence)}</div>
      </div>`;
    });
  }else{
    h+=`<div class="quoting">查无此句，意思是「古人没这么讲过」。<br>
      注意这不等于「现代科学上不成立」——古籍只能证明这句话没有出处，不能证明它错。
      它错没错，看上面现代来源那半边。</div>`;
  }
  h+='</div>';
  outEl.innerHTML=h;
}
render(0);
</script></body></html>
"""


def build():
    items = collect()
    data_js = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    data_js = data_js.replace("</", "<\\/")          # 防止把 </script> 提前闭合
    hl_js = json.dumps(HIGHLIGHT, ensure_ascii=False)

    html = TEMPLATE.replace("__DATA__", data_js).replace("__HL__", hl_js)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    kb = os.path.getsize(OUT_FILE) / 1024
    print(f"✓ 已生成 {OUT_FILE}")
    print(f"  {len(items)} 条结论 / {kb:.0f} KB / 单文件、零外链、无后端")
    for d in items:
        print(f"    · {d['verdict']['label']:5s} {d['claim']}")


if __name__ == "__main__":
    build()
