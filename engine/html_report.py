#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""html_report.py — 컷편집 결과를 비블 다크 톤 HTML 리포트로 (클릭 가능한 타임코드)."""

import os


def _tc(t):
    h = int(t // 3600); t -= h * 3600
    m = int(t // 60); s = t - m * 60
    return f"{h:02d}:{m:02d}:{s:05.2f}"


def generate(outpath, base, summary, choppy, report):
    """summary: dict(duration, kept, removed, pct, cuts, preset)
       choppy: [(s,e,c)] / report: {'추임새':[(s,e,t)], ...}"""
    def rows(items, n=80):
        out = []
        for it in items[:n]:
            s = it[0]; t = it[2] if len(it) > 2 else ""
            out.append(f'<tr><td class="tc">{_tc(s)}</td><td>{_esc(str(t))}</td></tr>')
        more = f'<tr><td colspan="2" class="more">… 외 {len(items)-n}개</td></tr>' if len(items) > n else ""
        return "\n".join(out) + more

    ch_rows = "\n".join(
        f'<tr><td class="tc warn">{_tc(s)}~{_tc(e)}</td><td>{c}컷 — 너무 촘촘 (확인 권장)</td></tr>'
        for s, e, c in choppy) or '<tr><td colspan="2" class="ok">없음 — 자연스러움 양호</td></tr>'

    html = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">
<title>{_esc(base)} — 컷편집 리포트</title>
<style>
:root{{--bg:#0a0c10;--card:#12151c;--line:#222833;--txt:#e6e9ef;--mut:#8a93a6;--ac:#2dd4bf;--warn:#f5a524;}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--txt);
font-family:Pretendard,-apple-system,'JetBrains Mono',monospace;padding:32px;line-height:1.5}}
h1{{font-size:20px;margin:0 0 4px}}.sub{{color:var(--mut);font-size:13px;margin-bottom:24px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:28px}}
.stat{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}}
.stat .v{{font-size:24px;font-weight:700;color:var(--ac)}}.stat .l{{color:var(--mut);font-size:12px;margin-top:4px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:18px}}
section h2{{font-size:15px;margin:0 0 12px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
td{{padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}}
.tc{{color:var(--ac);font-family:'JetBrains Mono',monospace;white-space:nowrap;width:170px;cursor:pointer}}
.tc.warn{{color:var(--warn)}}.warn h2{{color:var(--warn)}}
.ok{{color:var(--mut)}}.more{{color:var(--mut);text-align:center}}
.hint{{color:var(--mut);font-size:12px;margin-top:8px}}
</style></head><body>
<h1>🎬 {_esc(base)}</h1>
<div class="sub">컷편집 리포트 · 프리셋 {_esc(summary['preset'])} · 타임코드 클릭 시 복사</div>
<div class="grid">
  <div class="stat"><div class="v">{summary['pct']:.1f}%</div><div class="l">제거 비율</div></div>
  <div class="stat"><div class="v">{_dur(summary['removed'])}</div><div class="l">제거 길이</div></div>
  <div class="stat"><div class="v">{_dur(summary['kept'])}</div><div class="l">최종 길이</div></div>
  <div class="stat"><div class="v">{summary['cuts']}</div><div class="l">컷 개수</div></div>
</div>
<section class="warn"><h2>⚠ 자연스러움 주의 (컷 촘촘 = 부자연 위험)</h2>
<table>{ch_rows}</table>
<div class="hint">이 구간은 컷이 몰려 부자연스러울 수 있어요. 일부 컷을 되돌려 호흡을 살리는 걸 권장.</div></section>
<section><h2>추임새 ({len(report.get('추임새',[]))})</h2><table>{rows(report.get('추임새',[]))}</table></section>
<section><h2>망설임 어/음 ({len(report.get('망설임',[]))})</h2><table>{rows(report.get('망설임',[]))}</table></section>
<section><h2>더듬·중복 ({len(report.get('더듬/중복',[]))})</h2><table>{rows(report.get('더듬/중복',[]))}</table></section>
<script>
document.querySelectorAll('.tc').forEach(el=>el.addEventListener('click',()=>{{
  navigator.clipboard&&navigator.clipboard.writeText(el.textContent.split('~')[0]);
  el.style.outline='1px solid var(--ac)';setTimeout(()=>el.style.outline='',400);
}}));
</script></body></html>"""
    open(outpath, "w", encoding="utf-8").write(html)
    return outpath


def _dur(t):
    return f"{int(t//60)}:{int(t%60):02d}"


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
