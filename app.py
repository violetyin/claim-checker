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
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.stdout.reconfigure(encoding="utf-8")

import checker
import guji_engine
import llm_client as L
import monitor
import report
import source_tier as st
import wsa_search as w

HERE = os.path.dirname(os.path.abspath(__file__))

# 日预算护栏：公开链接上谁都能点，额度是我们自己的。
# 一次核查 6~9 次搜索，900 次大约够 100~150 次完整核查，正常演示碰不到。
DAILY_BUDGET = 900

# 云端反代有 ~100 秒超时（实测 524 Response Timeout by Origin Server）。
# 而一条新说法要 30~70 秒、网关拥塞时更久——同步请求会被掐断，
# 用户看到的是报错，可后台其实还在跑。
# 所以改成异步：立刻返回，后台线程干活，前端轮询；结果一落盘，轮询就读到了。
_INFLIGHT: dict[str, bool] = {}
_INFLIGHT_LOCK = threading.Lock()


def _start_job(claim: str, refresh: bool, deep: bool = False):
    key = ("deep:" if deep else "") + claim
    with _INFLIGHT_LOCK:
        if _INFLIGHT.get(key):
            return                          # 同一个说法已经在跑，别重复烧额度
        _INFLIGHT[key] = True

    def _run():
        try:
            checker.analyze(claim, refresh=refresh, deep=deep, verbose=False)
        except BaseException:
            pass                            # 失败也把在跑标记清掉，让用户能重试
        finally:
            with _INFLIGHT_LOCK:
                _INFLIGHT.pop(key, None)

    threading.Thread(target=_run, daemon=True).start()

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
  .shot{margin-top:11px;font-size:13px;color:var(--sub)}
  .shot #pick{cursor:pointer;color:var(--ink2);border-bottom:1px dashed #d8d2c6;padding-bottom:1px}
  .shot #pick:hover{color:var(--gold);border-color:var(--gold)}
  .shot .or{margin-left:10px;color:var(--faint);font-size:12.5px}
  .shotres{background:#fdfcf8;border:1px solid #e9e3d6;border-radius:12px;padding:12px 16px;
           font-size:14px;color:var(--ink2);margin-bottom:16px;line-height:1.7}
  .shotres b{color:var(--ink);font-weight:600}
  .shotres span{color:var(--sub);font-size:12.5px}
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
  .opts{margin-top:12px;font-size:13px;color:var(--sub)}
  .opts label{cursor:pointer;user-select:none;display:inline-flex;align-items:center;gap:7px}
  .opts input{accent-color:#8a6d3b;width:14px;height:14px}
  .opts .tip{color:var(--faint);margin-left:8px;font-size:12px}
  .watch{margin-top:13px;font-size:12.5px;color:var(--sub);line-height:1.95}
  .watch b{color:var(--ink2);font-weight:600}
  .watch .w{cursor:pointer;border-bottom:1px dashed #d8d2c6;margin-right:12px;color:var(--ink2)}
  .watch .w:hover{color:var(--gold);border-color:var(--gold)}
  .act{margin:15px 0 2px}
  .ghost{background:transparent;border:1px solid var(--line);color:var(--ink2);
         border-radius:9px;padding:7px 14px;font-size:13px;cursor:pointer;font-family:inherit;transition:.15s}
  .ghost:hover{border-color:#c9bda8;color:var(--gold)}
  .toast{position:fixed;left:50%;bottom:28px;transform:translateX(-50%);background:#1c1c1e;color:#fff;
         font-size:13px;padding:9px 17px;border-radius:10px;opacity:0;transition:opacity .25s;
         pointer-events:none;z-index:50}
  .toast.on{opacity:.94}
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
  <p class="shot">
    <span id="pick">📷 传一张家族群截图</span>
    <span class="or">或者直接把截图粘贴进来（Ctrl+V）</span>
    <input type="file" id="file" accept="image/*" hidden>
  </p>
  <div class="opts">
    <label><input type="checkbox" id="deep"> 深度调查</label>
    <span class="tip">多拆命题 + 第二轮追问，查得更细，也更慢</span>
  </div>
  <div class="watch" id="watchlist"></div>
  <div class="toast" id="toast"></div>

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

document.querySelectorAll('.chips b').forEach(b=>b.onclick=()=>{banner='';q.value=b.textContent;run(false)});
q.addEventListener('input',()=>{banner=''});
q.addEventListener('keydown',e=>{if(e.key==='Enter')run(false)});
go.onclick=()=>run(false);

const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// 从截图认出来的说法，单独存一份，渲染时置顶，不要被后面的结果冲掉
let banner='';

/* ── 截图查证：选图 / 粘贴 → 认出那句话 → 走同一条核查流程 ── */
const fileEl=document.getElementById('file'), pickEl=document.getElementById('pick');
pickEl.onclick=()=>fileEl.click();
fileEl.onchange=e=>{const f=e.target.files&&e.target.files[0]; if(f) handleImage(f); fileEl.value='';};
document.addEventListener('paste', e=>{
  const items=(e.clipboardData&&e.clipboardData.items)||[];
  for(const it of items){
    if(it.type && it.type.startsWith('image/')){ handleImage(it.getAsFile()); e.preventDefault(); return; }
  }
});

async function handleImage(file){
  if(!file) return;
  go.disabled=true; banner='';
  out.innerHTML='<p class="load"><span class="step">正在从截图里认出那句话…</span></p>';
  try{
    const dataUrl=await new Promise((res,rej)=>{
      const r=new FileReader(); r.onload=()=>res(r.result); r.onerror=rej; r.readAsDataURL(file);
    });
    const small=await shrink(dataUrl,1400);   // 大图先压，别把上限顶爆
    const r=await fetch('/api/extract',{method:'POST',headers:{'Content-Type':'application/json'},
                                       body:JSON.stringify({image:small})}).then(x=>x.json());
    if(r.error){ go.disabled=false; out.innerHTML='<p class="load">识别失败：'+esc(r.error)+'</p>'; return; }
    if(!r.claim){ go.disabled=false; out.innerHTML='<p class="load">这张图里没找到可以核验的说法。</p>'; return; }
    q.value=r.claim;
    banner='<div class="shotres">从截图里认出的说法：<b>'+esc(r.claim)+'</b>'+
           (r.scene?'<span>　场景：'+esc(r.scene)+'</span>':'')+
           '<span>　识别置信度 '+Math.round((r.confidence||0)*100)+'%</span></div>';
    go.disabled=false;
    run(false);
  }catch(e){
    go.disabled=false;
    out.innerHTML='<p class="load">识别失败：'+esc(e.message)+'</p>';
  }
}

function shrink(dataUrl,maxW){
  return new Promise(res=>{
    const img=new Image();
    img.onload=()=>{
      const s=Math.min(1, maxW/img.width);
      if(s>=1) return res(dataUrl);
      const c=document.createElement('canvas');
      c.width=Math.round(img.width*s); c.height=Math.round(img.height*s);
      c.getContext('2d').drawImage(img,0,0,c.width,c.height);
      res(c.toDataURL('image/jpeg',0.88));
    };
    img.onerror=()=>res(dataUrl);
    img.src=dataUrl;
  });
}

async function run(refresh){
  const v=q.value.trim(); if(!v) return;
  go.disabled=true;
  const t0=Date.now();
  const tip='拆命题 → 查古籍 → 查现代来源 → 交叉比对 → 判定';
  const show=()=>{ out.innerHTML=banner+'<p class="load"><span class="step">'+tip+'</span><br>'+
      '<span style="font-size:12.5px;color:#a8a8ad">已等待 '+Math.round((Date.now()-t0)/1000)+' 秒'+
      '（新说法要调两次模型，比赛期间网关排队会久一些）</span></p>'; };
  show();
  const tick=setInterval(show,1000);

  const stop=()=>{ clearInterval(tick); go.disabled=false; };

  const deep=document.getElementById('deep').checked;
  const extraQ=(refresh?'&refresh=1':'')+(deep?'&deep=1':'');

  try{
    // 异步：立刻返回，后台跑。云端反代约 100 秒就掐断，同步请求必然超时。
    const st=await fetch('/api/start?q='+encodeURIComponent(v)+extraQ).then(x=>x.json());
    if(st.error){ stop(); out.innerHTML='<p class="load">'+esc(st.error)+'</p>'; return; }

    for(let i=0;i<150;i++){
      await new Promise(r=>setTimeout(r,2000));
      const r=await fetch('/api/poll?q='+encodeURIComponent(v)+(deep?'&deep=1':'')).then(x=>x.json());
      if(r.error){ stop(); out.innerHTML='<p class="load">'+esc(r.error)+'</p>'; return; }
      if(r.state==='running') continue;
      if(r.state==='unknown'){ stop(); out.innerHTML='<p class="load">后台任务中断了，请再点一次核查</p>'; return; }
      stop(); out.innerHTML=banner+render(r); bindWatch(r.claim); return;
    }
    stop();
    out.innerHTML='<p class="load">超过 5 分钟还没出结果，上游网关太慢。过一会儿再点一次，已经跑完的部分会走缓存。</p>';
  }catch(e){
    stop();
    out.innerHTML='<p class="load">查询失败：'+esc(e.message)+'<br>'+
      '<span style="font-size:12.5px;color:#a8a8ad">后台可能仍在跑，等几秒再点一次核查，会直接读结果。</span></p>';
  }
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

  // 动作：盯住这条 + 导出报告
  h+=`<div class="act">
        <button class="ghost" id="watchbtn">👁 盯住这条，每天自动重核</button>
        <a class="ghost" style="margin-left:8px;text-decoration:none" target="_blank"
           href="/api/report?q=${encodeURIComponent(r.claim)}${r.stats.deep?'&deep=1':''}">导出调查报告</a>
      </div>`;

  // 底注
  h+=`<footer>古籍检索范围：${a.corpus.books} 部本草与食疗古籍，共 ${a.corpus.chars.toLocaleString()} 字，逐段全文匹配；现代来源经元宝搜索检索，
      仅采信 T1 政府/权威机构/央媒/学术 与 T2 主流媒体/机构科普 两级。本次搜索 ${r.stats.searches} 次、模型调用 ${r.stats.llm_calls} 次、耗时 ${r.stats.elapsed}s。</footer>`;
  return h;
}

/* ── 盯梢 ── */
function toast(msg){
  const t=document.getElementById('toast');
  if(!t) return;
  t.textContent=msg; t.classList.add('on');
  clearTimeout(window._tt); window._tt=setTimeout(()=>t.classList.remove('on'),2200);
}

function bindWatch(claim){
  const b=document.getElementById('watchbtn');
  if(b) b.onclick=()=>watchAdd(claim);
}

async function watchAdd(claim){
  try{
    const r=await fetch('/api/watch',{method:'POST',headers:{'Content-Type':'application/json'},
               body:JSON.stringify({action:'add',claim})}).then(x=>x.json());
    toast(r.message||'已处理');
    renderWatch(r.items||[]);
  }catch(e){ toast('操作失败：'+e.message); }
}

function renderWatch(items){
  const el=document.getElementById('watchlist');
  if(!el) return;
  if(!items.length){ el.innerHTML=''; return; }
  el.innerHTML='<b>正在盯 '+items.length+' 条</b>（每天自动重核，变了会提醒）：'+
    items.map(it=>'<span class="w" data-c="'+esc(it.claim)+'">'+esc(it.claim)+
      (it.label?' <span style="color:#a8a8ad">'+esc(it.label)+'</span>':'')+'</span>').join('');
  el.querySelectorAll('.w').forEach(w=>{
    w.onclick=()=>{ document.getElementById('deep').checked=false; q.value=w.dataset.c; banner=''; run(false); };
  });
}

async function initWatch(){
  try{
    const r=await fetch('/api/watch').then(x=>x.json());
    renderWatch(r.items||[]);
  }catch(e){}
}
initWatch();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)

        if u.path == "/api/watch":
            self._json(self._watch_payload())
            return

        # 异步核查第一步：立刻返回，后台开工。命缓存则直接说 ready。
        if u.path == "/api/start":
            q = (qs.get("q") or [""])[0].strip()
            refresh = (qs.get("refresh") or [""])[0] in ("1", "true", "yes")
            deep = (qs.get("deep") or [""])[0] in ("1", "true", "yes")
            if not q:
                self._json({"error": "说法不能为空"})
                return
            if w.usage_today() >= DAILY_BUDGET:
                self._json({"error": f"今日演示额度已用完（已调用 {w.usage_today()} 次搜索），明天自动恢复。"})
                return
            cached = checker._read_result(q, deep) if not refresh else None
            if cached:
                self._json({"state": "ready", "from_cache": True})
                return
            _start_job(q, refresh, deep)
            self._json({"state": "running", "from_cache": False})
            return

        # 异步核查第二步：轮询。结果一落盘就能读到，不必等请求活着。
        if u.path == "/api/poll":
            q = (qs.get("q") or [""])[0].strip()
            deep = (qs.get("deep") or [""])[0] in ("1", "true", "yes")
            if not q:
                self._json({"error": "说法不能为空"})
                return
            cached = checker._read_result(q, deep)
            if cached:
                cached.setdefault("stats", {})["from_cache"] = True
                self._json(cached)
                return
            with _INFLIGHT_LOCK:
                running = bool(_INFLIGHT.get(("deep:" if deep else "") + q))
            self._json({"state": "running" if running else "unknown"})
            return

        if u.path == "/api/check":
            qs = parse_qs(u.query)
            q = (qs.get("q") or [""])[0].strip()
            refresh = (qs.get("refresh") or [""])[0] in ("1", "true", "yes")

            # 公开链接意味着任何拿到链接的人都在花我们的额度。
            # 所以设一道日预算护栏：正常演示根本碰不到，被刷时也不会一次烧光。
            used = w.usage_today()
            if used >= DAILY_BUDGET:
                body = json.dumps({
                    "error": f"今日演示额度已用完（已调用 {used} 次搜索）。"
                             f"这是防止额度被刷光的保护，明天自动恢复。"
                }, ensure_ascii=False).encode("utf-8")
                self._send(body, "application/json; charset=utf-8")
                return

            try:
                body = json.dumps(checker.analyze(q, refresh=refresh, verbose=False),
                                  ensure_ascii=False).encode("utf-8")
            except SystemExit as e:
                body = json.dumps({
                    "error": f"古籍语料缺失：{e}"
                }, ensure_ascii=False).encode("utf-8")
            except Exception as e:  # 页面要能把错误看见，而不是白屏
                body = json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False).encode("utf-8")
            self._send(body, "application/json; charset=utf-8")
            return
        if u.path == "/api/report":
            q = (qs.get("q") or [""])[0].strip()
            deep = (qs.get("deep") or [""])[0] in ("1", "true", "yes")
            if not q:
                self._send("缺少说法".encode("utf-8"), "text/plain; charset=utf-8")
                return
            try:
                r = checker.analyze(q, deep=deep, verbose=False)
                body = report.render(r).encode("utf-8")
                import urllib.parse as _up
                fn = _up.quote(f"核查报告_{q}{'_深度' if deep else ''}.html")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Disposition", f"inline; filename*=UTF-8''{fn}")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                self._json({"error": f"{type(e).__name__}: {e}"})
            return

        if u.path == "/api/stats":
            body = json.dumps({
                "corpus": guji_engine.corpus_stats(),
                "wsa": w.cache_stats(),
                "budget": {"used": w.usage_today(), "cap": DAILY_BUDGET},
                "cached_claims": len([f for f in os.listdir(checker.RESULT_CACHE_DIR)
                                      if f.endswith(".json")]) if os.path.isdir(checker.RESULT_CACHE_DIR) else 0,
            }, ensure_ascii=False).encode("utf-8")
            self._send(body, "application/json; charset=utf-8")
            return
        self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")

    def do_POST(self):
        u = urlparse(self.path)

        if u.path == "/api/watch":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                data = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                act = data.get("action")
                claim = (data.get("claim") or "").strip()
                if act == "add":
                    ok, msg = monitor.add(claim)
                elif act == "remove":
                    ok, msg = monitor.remove(claim)
                else:
                    ok, msg = False, "未知操作"
                self._json({"ok": ok, "message": msg, **self._watch_payload()})
            except Exception as e:
                self._json({"ok": False, "message": f"{type(e).__name__}: {e}"})
            return

        if u.path != "/api/extract":
            self._json({"error": "不支持的接口"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 12 * 1024 * 1024:
                self._json({"error": "图片为空或超过 12MB"})
                return
            data = json.loads(self.rfile.read(n).decode("utf-8"))
            img = data.get("image") or ""
            r = L.read_claim_from_image(img)
            self._json(r)
        except SystemExit as e:
            self._json({"error": f"模型配置缺失：{e}"})
        except Exception as e:
            self._json({"error": f"{type(e).__name__}: {e}"})

    def _watch_payload(self) -> dict:
        d = monitor.load()
        return {"items": [{
            "claim": it["claim"],
            "added_at": it.get("added_at"),
            "last_checked": it.get("last_checked"),
            "label": (it.get("last") or {}).get("label"),
            "headline": (it.get("last") or {}).get("headline"),
        } for it in d.get("items", [])]}

    def _json(self, obj):
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

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
    ap.add_argument("--host", default=None)
    args = ap.parse_args()

    # 部署到公网时，平台会注入 PORT，要求监听 0.0.0.0。
    # 本地用时仍然是 127.0.0.1，不对外暴露。
    env_port = os.environ.get("PORT")
    if env_port:
        host, port = "0.0.0.0", int(env_port)
    else:
        host, port = (args.host or "127.0.0.1"), args.port

    print("正在载入古籍语料…")
    s = guji_engine.corpus_stats()
    print(f"✓ {s['books']} 部古籍 / {s['sentences']} 段 / {s['chars']//10000} 万字")
    print(f"✓ 元宝搜索：今日已调用 {w.usage_today()} 次")

    # 网关有冷启动（实测首次 13.9s、第二次 2.4s），开服先热一遍，
    # 这样第一个真实提问就不用替我们承担这一刀。放后台，不挡服务启动。
    def _warm():
        r = L.warmup()
        print("✓ 模型预热：" + " · ".join(f"{k} {v}s" for k, v in r.items()), flush=True)

    threading.Thread(target=_warm, daemon=True).start()

    print(f"\n监听 {host}:{port}\n")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
