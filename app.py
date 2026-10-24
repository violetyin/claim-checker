# -*- coding: utf-8 -*-
"""
app.py —— 《这句能信吗》核查页面（侦探赛道作品本体）

一屏三块：输入 → 结论 → 证据（现代来源 + 古籍原文）

为什么必须是这一屏：
    评委 30 秒要看懂的东西只有一件——它不只是"搜给你看"，
    它是把一句话拆开、分别去查、然后敢说"这条我查不到"。

启动：
    python app.py                 # http://127.0.0.1:8080
    python app.py --port 9000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.stdout.reconfigure(encoding="utf-8")

import checker
import guji_engine
import source_tier as st
import wsa_search as w

HERE = os.path.dirname(os.path.abspath(__file__))

PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>这句能信吗 · 双源核查</title>
<style>
  :root{
    --ink:#1c1c1e; --ink2:#3a3a3c; --sub:#86868b; --line:#e8e6e1;
    --bg:#fbfaf8; --card:#ffffff; --gold:#8a6d3b;
    --ok:#2e6b4f; --warn:#b07c1a; --bad:#b3261e; --gray:#8e8e93;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
       font:16px/1.72 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
       -webkit-font-smoothing:antialiased}
  .wrap{max-width:820px;margin:0 auto;padding:44px 22px 90px}
  header{margin-bottom:26px}
  h1{font-size:25px;margin:0 0 8px;letter-spacing:.3px;font-weight:650}
  .tag{display:inline-block;font-size:12px;color:var(--gold);border:1px solid #e3d9c8;
       background:#fdfbf6;border-radius:100px;padding:2px 10px;margin-left:8px;vertical-align:2px}
  .sub{color:var(--sub);font-size:14px;margin:0}
  .box{display:flex;gap:10px;margin-top:22px}
  input{flex:1;padding:15px 17px;font-size:16px;border:1px solid var(--line);border-radius:13px;
        background:#fff;outline:none;transition:border-color .15s, box-shadow .15s}
  input:focus{border-color:#c9bda8;box-shadow:0 0 0 3px rgba(138,109,59,.07)}
  button{padding:15px 24px;font-size:16px;border:0;border-radius:13px;background:var(--ink);
         color:#fff;cursor:pointer;font-weight:500;transition:opacity .15s}
  button:hover{opacity:.88} button:disabled{opacity:.35;cursor:default}
  .chips{margin-top:14px;font-size:13px;color:var(--sub)}
  .chips b{font-weight:400;cursor:pointer;border-bottom:1px dashed #d8d2c6;margin-right:14px;
           padding-bottom:1px;color:var(--ink2)}
  .chips b:hover{color:var(--gold);border-color:var(--gold)}
  .stage{margin-top:30px}
  .load{color:var(--sub);font-size:14px;padding:18px 0}
  .load .step{animation:pulse 1.4s infinite}
  @keyframes pulse{0%,100%{opacity:.35}50%{opacity:1}}

  .verdict{background:var(--card);border:1px solid var(--line);border-radius:16px;
           padding:20px 22px;margin-bottom:20px}
  .vhead{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .vchip{font-size:13px;font-weight:600;padding:3px 11px;border-radius:100px;color:#fff}
  .vtitle{font-size:19px;font-weight:650;line-height:1.45;letter-spacing:.2px}
  .vreason{margin-top:12px;color:var(--ink2);font-size:15px;line-height:1.78}
  .vmeta{margin-top:14px;padding-top:12px;border-top:1px solid var(--line);
         font-size:12.5px;color:var(--sub);display:flex;gap:16px;flex-wrap:wrap}
  .vmeta .k{color:var(--gray)}

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
  .snum{color:var(--gray);margin-right:6px;font-variant-numeric:tabular-nums}
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
  .quoting{font-size:14px;color:var(--sub);margin-top:8px;line-height:1.7}

  .cut{margin-top:12px;font-size:12.5px;color:var(--sub);background:#f6f5f2;border-radius:9px;
       padding:9px 12px;line-height:1.6}
  footer{margin-top:46px;padding-top:18px;border-top:1px solid var(--line);
         font-size:12px;color:var(--sub);line-height:1.85}
  footer b{font-weight:600;color:var(--ink2)}
</style></head>
<body><div class="wrap">
  <header>
    <h1>这句能信吗<span class="tag">双源核查</span></h1>
    <p class="sub">一句话进来，拆成命题，分别去查：古书里有没有这句话，现代权威怎么说。查不到就直说查不到。</p>
  </header>

  <div class="box">
    <input id="q" placeholder="贴一句你听来的说法，比如：红枣补铁" autocomplete="off">
    <button id="go">核查</button>
  </div>
  <p class="chips">试试：
    <b>红枣补铁</b><b>隔夜菜致癌</b><b>红枣配薄荷能排肝毒</b><b>绿豆解百毒</b>
  </p>

  <div class="stage" id="out"></div>

  <footer>
    <b>边界说明</b>　本工具只做资料核对，不提供诊疗建议、不替代医生，不做疾病诊断、不给用药或停药建议。
    古籍文献记载 ≠ 现代临床疗效证据，两者在本页分开标注、不互相替代。<br>
    证据只采信政府、卫健委、疾控、央媒、学术机构与主流媒体来源；营销号、偏方站、无署名转载一律剔除，不计入结论。
  </footer>
</div>
<script>
const q=document.getElementById('q'), go=document.getElementById('go'), out=document.getElementById('out');
const C={supported:'#2e6b4f',partial:'#b07c1a',contrary:'#b3261e',ancient_only:'#8a6d3b',not_found:'#8e8e93'};
const ICON={supported:'✓',partial:'◐',contrary:'✕',ancient_only:'❖',not_found:'∅'};

document.querySelectorAll('.chips b').forEach(b=>b.onclick=()=>{q.value=b.textContent;run(false)});
q.addEventListener('keydown',e=>{if(e.key==='Enter')run(false)});
go.onclick=()=>run(false);

const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

async function run(refresh){
  const v=q.value.trim(); if(!v) return;
  go.disabled=true;
  out.innerHTML='<p class="load"><span class="step">拆命题 → 查古籍 → 查现代来源 → 交叉比对 → 判定</span></p>';
  try{
    const r=await fetch('/api/check?q='+encodeURIComponent(v)+(refresh?'&refresh=1':''))
              .then(x=>{if(!x.ok)throw new Error('HTTP '+x.status);return x.json()});
    if(r.error){ out.innerHTML='<p class="load">出错：'+esc(r.error)+'</p>'; go.disabled=false; return; }
    out.innerHTML=render(r);
  }catch(e){ out.innerHTML='<p class="load">查询失败：'+esc(e.message)+'</p>'; }
  go.disabled=false;
}

function render(r){
  const v=r.verdict, col=C[v.code]||'#8e8e93', ic=ICON[v.code]||'';
  let h='';

  // 结论
  h+=`<div class="verdict" style="border-left:3px solid ${col}">
    <div class="vhead">
      <span class="vchip" style="background:${col}">${ic} ${esc(v.label)}</span>
      <span class="vtitle">${esc(v.headline)}</span>
    </div>
    ${v.reason?`<div class="vreason">${esc(v.reason)}</div>`:''}
    <div class="vmeta">
      <span><span class="k">说法</span> ${esc(r.claim)}</span>
      <span><span class="k">拆出命题</span> ${r.propositions.length} 个</span>
      <span><span class="k">可信来源</span> ${r.sources.length} 条</span>
      <span><span class="k">剔除低质</span> ${r.discarded_count} 条</span>
      <span><span class="k">古籍语料</span> ${r.ancient.corpus.books} 部 / ${Math.round(r.ancient.corpus.chars/10000)} 万字</span>
      <span><span class="k">模式</span> ${r.stats.from_cache?'回放':'实时'}</span>
    </div>
  </div>`;

  // 命题
  h+='<div class="sect">拆成命题，逐条查证</div>';
  r.propositions.forEach((p,i)=>{
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
           <div class="smeta">${esc(s.site||'')} · ${esc((s.date||'').slice(0,10))}</div>
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

  // 古籍
  const a=r.ancient;
  h+='<div class="sect">同一句话，古书里怎么说</div>';
  h+=`<div class="ancient"><div class="ahead">${esc(a.headline)}</div>`;
  // 说明文字有时和标题说的是同一句，别重复占两行
  const note=(r.ancient_note||'').trim();
  const sq=s=>String(s).replace(/\s/g,'');
  if(note && sq(note).slice(0,12)!==sq(a.headline).slice(0,12)) h+=`<div class="anote">${esc(note)}</div>`;
  if(a.passages&&a.passages.length){
    a.passages.slice(0,3).forEach(p=>{
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

  // 底注
  h+=`<footer>古籍检索范围：${a.corpus.books} 部本草与食疗古籍，共 ${a.corpus.chars.toLocaleString()} 字，逐段全文匹配；现代来源经元宝搜索检索，
      仅采信 T1 政府/权威机构/央媒/学术 与 T2 主流媒体/机构科普 两级。本次搜索 ${r.stats.searches} 次、模型调用 ${r.stats.llm_calls} 次、耗时 ${r.stats.elapsed}s。</footer>`;
  return h;
}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/check":
            qs = parse_qs(u.query)
            q = (qs.get("q") or [""])[0].strip()
            refresh = (qs.get("refresh") or [""])[0] in ("1", "true", "yes")
            try:
                body = json.dumps(checker.analyze(q, refresh=refresh, verbose=False),
                                  ensure_ascii=False).encode("utf-8")
            except Exception as e:  # 页面要能把错误看见，而不是白屏
                body = json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False).encode("utf-8")
            self._send(body, "application/json; charset=utf-8")
            return
        if u.path == "/api/stats":
            body = json.dumps({
                "corpus": guji_engine.corpus_stats(),
                "wsa": w.cache_stats(),
                "cached_claims": len([f for f in os.listdir(checker.RESULT_CACHE_DIR)
                                      if f.endswith(".json")]) if os.path.isdir(checker.RESULT_CACHE_DIR) else 0,
            }, ensure_ascii=False).encode("utf-8")
            self._send(body, "application/json; charset=utf-8")
            return
        self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")

    def _send(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser(description="这句能信吗 · 核查页面")
    ap.add_argument("--port", type=int, default=8080)
    args = ap.parse_args()

    print("正在载入古籍语料…")
    s = guji_engine.corpus_stats()
    print(f"✓ {s['books']} 部古籍 / {s['sentences']} 段 / {s['chars']//10000} 万字")
    print(f"✓ 元宝搜索：今日已调用 {w.usage_today()} 次")
    print(f"\n打开 →  http://127.0.0.1:{args.port}\n")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
