#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
transcript_export.py — 전사본(_words.json)을 LLM 분석용 타임코드 전사본(.md)으로 변환

콘텐츠 리서처/기획자 에이전트가 챕터·하이라이트·삭제구간·제목/썸네일 문구를
판단하기 좋게, 원본 타임라인 기준 문단 단위로 묶어 타임코드를 붙인다.

사용:
  python3 transcript_export.py "output/<base>_words.json"
"""

import sys, os, json

GAP_PARA = 1.0    # 이 간격(초) 이상 쉬면 문단 분리
MAX_PARA = 280    # 문단 최대 글자수(너무 길면 강제 분리)


def mmss(t):
    h = int(t // 3600); t -= h * 3600
    m = int(t // 60); s = int(t - m * 60)
    return f"{h:01d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def to_paragraphs(words):
    paras, cur, start, last = [], "", None, None
    for s, e, t in words:
        t = t.strip()
        if not t:
            continue
        if start is None:
            start = s
        if last is not None and (s - last > GAP_PARA or len(cur) > MAX_PARA) and cur:
            paras.append((start, cur.strip())); cur, start = "", s
        cur += (" " if cur and not t.startswith((",", ".", "?", "!")) else "") + t
        last = e
        if t.endswith((".", "?", "!")) and len(cur) > 60:
            paras.append((start, cur.strip())); cur, start = "", None
    if cur.strip():
        paras.append((start or 0, cur.strip()))
    return paras


def main():
    if len(sys.argv) < 2:
        print("사용: python3 transcript_export.py \"<base>_words.json\""); sys.exit(1)
    wp = sys.argv[1]
    if not os.path.exists(wp):
        print("파일 없음:", wp); sys.exit(1)
    words = [tuple(w) for w in json.load(open(wp, encoding="utf-8"))]
    words.sort(key=lambda w: w[0])
    paras = to_paragraphs(words)

    base = wp.replace("_words.json", "")
    out = base + "_transcript.md"
    total = words[-1][1] if words else 0
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# 전사본 (원본 타임라인 기준)\n\n")
        f.write(f"- 총 길이: {mmss(total)} · 단어 {len(words)}개 · 문단 {len(paras)}개\n")
        f.write(f"- 용도: 챕터·하이라이트·삭제구간·제목/썸네일 문구 분석\n\n---\n\n")
        for s, txt in paras:
            f.write(f"**[{mmss(s)}]** {txt}\n\n")
    print(f"✅ 전사본 → {os.path.basename(out)}  (문단 {len(paras)}개, {mmss(total)})")
    print("   미리보기:")
    for s, txt in paras[:3]:
        print(f"     [{mmss(s)}] {txt[:50]}...")


if __name__ == "__main__":
    main()
