# -*- coding: utf-8 -*-
"""
report.py —— 把一次核查导出成一份可下载、可打印的调查报告

为什么要这一步：
    官方点名「深度调查」是进阶项之一。深度不只是查得多，
    还要能把查的过程和每一步的依据落成一份别人能拿去用的东西。
    页面上翻着看是一回事，交一份报告是另一回事。

用法：
    python report.py "红枣补铁"              # 普通核查 → reports/*.html
    python report.py "红枣补铁" --deep       # 深度调查 → reports/*.html
    python report.py "红枣补铁" --open       # 生成并用浏览器打开
"""
from __future__ import annotations

import argparse
import html
import os
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

import checker

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "reports")

VERDICT_COLOR = {
    "supported": "#2e6b4f", "partial": "#b07c1a", "contrary": "#b3261e",
    "ancient_only": "#8a6d3b", "not_found": "#6b6b6b",
}


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def render(r: dict) -> str:
    v = r.get("verdict") or {}
    code = v.get("code", "not_found")
    color = VERDICT_COLOR.get(code, "#6b6b6b")
    a = r.get("ancient") or {}
    st = r.get("stats") or {}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    deep = st.get("deep")

    # 命题
    prop_html = []
    for i, p in enumerate(r.get("propositions", []), 1):
        c = VERDICT_COLOR.get(p.get("verdict"), "#6b6b6b")
        srcs = "".join(
            f'<tr><td class="n">[{s.get("gid","")}]</td>'
            f'<td><a href="{_e(s.get("url"))}" target="_blank" rel="noopener">{_e(s.get("title"))}</a>'
            f'<div class="sm">{_e(s.get("site"))}　{_e(s.get("date"))}　'
            f'信源档位 T{_e(s.get("tier"))} {_e(s.get("tier_label"))}</div></td></tr>'
            for s in (p.get("sources") or [])
        )
        qs = "　".join(f"<code>{_e(q)}</code>" for q in (p.get("queries") or []))
        prop_html.append(f"""
        <section class="prop">
          <h3><span class="idx">命题 {i}</span>{_e(p.get('text'))}</h3>
          <p class="pv" style="color:{c}">{_e(p.get('label'))}　<span class="pvr">{_e(p.get('reason'))}</span></p>
          <p class="qs">检索式：{qs or '（无）'}</p>
          {f'<table class="srcs">{srcs}</table>' if srcs else '<p class="sm">没有检索到可采信来源。</p>'}
        </section>""")

    ancient_html = ""
    if a:
        pas = "".join(
            f'<div class="apass"><div class="abk">《{_e(p.get("book"))}》'
            f'{_e(p.get("dynasty"))}·{_e(p.get("author"))}</div>'
            f'<div class="atx">{_e(p.get("sentence"))}</div></div>'
            for p in (a.get("passages") or [])
        )
        ancient_html = f"""
        <section class="block">
          <h2>三、古籍怎么说</h2>
          <p class="ah">{_e(a.get('headline'))}</p>
          {f'<p class="an">{_e(r.get("ancient_note"))}</p>' if r.get("ancient_note") else ''}
          {pas or '<p class="sm">查无此句。</p>'}
          <p class="note">古籍文献记载 ≠ 现代临床疗效证据。本报告把两类证据分开列出，不互相替代。</p>
        </section>"""

    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>核查报告 · {_e(r.get('claim'))}</title>
<style>
  @page {{ size: A4; margin: 18mm 16mm; }}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:#fff;color:#1c1c1e;font:10.5pt/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
       max-width:180mm;margin:0 auto;padding:10mm 4mm 20mm}}
  header{{border-bottom:2pt solid #1c1c1e;padding-bottom:5mm;margin-bottom:7mm}}
  .kicker{{font-size:8pt;letter-spacing:.18em;color:#8a6d3b;margin-bottom:3mm}}
  h1{{font-size:20pt;line-height:1.3;font-weight:700}}
  .meta{{font-size:8.5pt;color:#86868b;margin-top:3mm;line-height:1.8}}
  .verdict{{border-left:4pt solid {color};background:#fafaf8;padding:5mm 6mm;margin-bottom:7mm;border-radius:2mm}}
  .vlabel{{font-size:12pt;font-weight:700;color:{color}}}
  .vhead{{font-size:13pt;font-weight:600;margin-top:2mm;line-height:1.5}}
  .vreason{{font-size:10pt;color:#3a3a3c;margin-top:3mm;line-height:1.75}}
  h2{{font-size:11pt;letter-spacing:.06em;margin:8mm 0 3.5mm;padding-bottom:1.5mm;border-bottom:.8pt solid #ddd8cd}}
  h3{{font-size:10.5pt;font-weight:650;margin-bottom:2mm;line-height:1.55}}
  .idx{{font-size:8pt;color:#8a6d3b;letter-spacing:.12em;margin-right:3mm;font-weight:600}}
  .prop{{margin-bottom:6mm;page-break-inside:avoid}}
  .pv{{font-size:9.5pt;margin-bottom:2mm}}
  .pvr{{color:#3a3a3c}}
  .qs{{font-size:8.5pt;color:#86868b;margin-bottom:2.5mm}}
  code{{background:#f4f2ed;padding:.5mm 1.5mm;border-radius:1mm;font-size:8pt;color:#8a6d3b}}
  table.srcs{{width:100%;border-collapse:collapse;font-size:9pt;margin-top:1.5mm}}
  table.srcs td{{padding:1.8mm 2mm;border-bottom:.5pt solid #eeece7;vertical-align:top}}
  td.n{{width:9mm;color:#a8a8ad;font-size:8pt}}
  table.srcs a{{color:#1c1c1e;text-decoration:none;border-bottom:.5pt solid #d8d2c6}}
  .sm{{font-size:8pt;color:#86868b;margin-top:.8mm}}
  .ah{{font-size:9.5pt;color:#8a6d3b;margin-bottom:2mm}}
  .an{{font-size:9.5pt;color:#3a3a3c;line-height:1.75;margin-bottom:3mm}}
  .apass{{margin-top:3mm;padding-top:3mm;border-top:.5pt solid #eeece7}}
  .abk{{font-size:8.5pt;color:#8a6d3b}}
  .atx{{font-family:Georgia,"Songti SC","STSong",serif;font-size:9.5pt;line-height:1.95;margin-top:1mm}}
  .note{{font-size:8pt;color:#86868b;margin-top:4mm;line-height:1.7;background:#fafaf8;padding:3mm;border-radius:1.5mm}}
  .method{{font-size:8.5pt;color:#6b6b6b;line-height:1.85}}
  .method b{{color:#1c1c1e}}
  footer{{margin-top:9mm;padding-top:3mm;border-top:.5pt solid #ddd8cd;font-size:8pt;color:#a8a8ad;line-height:1.8}}
</style></head>
<body>
  <header>
    <div class="kicker">这句能信吗 · 双源核查{'　·　深度调查报告' if deep else ''}</div>
    <h1>{_e(r.get('claim'))}</h1>
    <div class="meta">
      核查时间 {now}　·　拆出 {len(r.get('propositions') or [])} 个命题
      　·　采信来源 {len(r.get('sources') or [])} 条　·　剔除低质 {r.get('discarded_count', 0)} 条
      {'　·　第二轮追问 ' + str(st.get('deep_rounds', 0)) + ' 轮' if deep else ''}
    </div>
  </header>

  <div class="verdict">
    <div class="vlabel">{_e(v.get('icon'))} {_e(v.get('label'))}</div>
    <div class="vhead">{_e(v.get('headline'))}</div>
    <div class="vreason">{_e(v.get('reason'))}</div>
  </div>

  <section class="block">
    <h2>一、拆成命题，逐条查证</h2>
    {''.join(prop_html)}
  </section>

  {ancient_html}

  <section class="block">
    <h2>{'四' if ancient_html else '三'}、核查方法</h2>
    <div class="method">
      <b>四步流程</b>　拆（把一句话拆成可独立核验的断言）→ 查（现代证据用元宝搜索，
      传统出处用 42 部本草古籍全文检索）→ 比（信源分四级，只采信 T1 政府/权威机构/央媒/学术
      与 T2 主流媒体/机构科普；数的是独立域名，不是结果条数）→ 判（五档结论）。<br>
      <b>语料规模</b>　古籍 42 部、93,703 段、8,774,342 字，逐段全文匹配。<br>
      <b>判定规则</b>　给定来源中没有一条直接支持该命题时，必须判「查不到」，不得依赖常识补充；
      来源互相冲突时不站队，判「部分有据」或「查不到」。<br>
      <b>本次消耗</b>　搜索 {st.get('searches', 0)} 次　·　模型调用 {st.get('llm_calls', 0)} 次
    </div>
  </section>

  <footer>
    本报告只做资料核对，不提供诊疗建议、不替代医生，不做疾病诊断、不给用药或停药建议。<br>
    古籍文献记载 ≠ 现代临床疗效证据，两类证据在本报告中分开列出。<br>
    现代证据来源：腾讯云元宝搜索。古籍语料：42 部公版本草全文。
  </footer>
</body></html>"""


def build(claim: str, deep: bool = False, refresh: bool = False) -> str:
    r = checker.analyze(claim, deep=deep, refresh=refresh, verbose=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    safe = "".join(ch for ch in claim if ch not in '\\/:*?"<>|').strip()[:40] or "report"
    path = os.path.join(OUT_DIR, f"{safe}{'_深度' if deep else ''}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(r))
    return path


def main():
    ap = argparse.ArgumentParser(description="导出核查报告")
    ap.add_argument("claim")
    ap.add_argument("--deep", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    path = build(args.claim, deep=args.deep, refresh=args.refresh)
    print(f"\n✓ 报告已生成：{path}")
    if args.open:
        os.startfile(path)  # noqa: S606  Windows 专用，本机使用


if __name__ == "__main__":
    main()
