#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
emphasis_subs.py — 자막에서 강조할 단어(숫자·단위·핵심어)를 자동 검출해 색을 입힌 ASS 생성.

비블 스타일 ASS에, 숫자/퍼센트/만·억·배·위 같은 임팩트 단어를 강조색(teal)으로.
기획자/리서처가 지정한 키워드도 함께 강조 가능.

사용:
  python3 emphasis_subs.py "자막.srt"
  python3 emphasis_subs.py "자막.srt" 구체성 영업사원 치트키   # 추가 강조어
출력: <자막>_emphasis.ass
"""

import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import subtitle_polish as SP

EMPH_COLOR = "&H00BFD42D"   # teal #2dd4bf (ASS는 &H00BBGGRR)
WHITE = "&H00FFFFFF"

# 자동 강조: 숫자 + 단위/퍼센트
AUTO = re.compile(r"\d[\d,\.]*\s?(?:%|퍼센트|만|억|천|배|위|명|개|원|초|분|시간|점|위|등|회|년|월|일)?")


def colorize(text, keywords):
    """text 안의 강조 대상에 ASS 색 태그를 씌운다."""
    spans = []  # (start, end)
    for m in AUTO.finditer(text):
        if any(ch.isdigit() for ch in m.group()):
            spans.append((m.start(), m.end()))
    for kw in keywords:
        if not kw:
            continue
        for m in re.finditer(re.escape(kw), text):
            spans.append((m.start(), m.end()))
    if not spans:
        return text
    # 겹침 정리 + 정렬
    spans.sort()
    merged = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    out, cur = [], 0
    for s, e in merged:
        out.append(text[cur:s])
        out.append("{\\c" + EMPH_COLOR + "&}" + text[s:e] + "{\\c" + WHITE + "&}")
        cur = e
    out.append(text[cur:])
    return "".join(out)


def write_ass(cues, path, keywords):
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Bibl,{SP.ASS_FONT},{SP.ASS_SIZE},{WHITE},&H000000FF,&H00000000,&H05000000,-1,0,0,0,100,100,0,0,1,{SP.ASS_OUTLINE},{SP.ASS_SHADOW},2,80,80,{SP.ASS_MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    n_emph = 0
    with open(path, "w", encoding="utf-8") as f:
        f.write(head)
        for s, e, t in cues:
            colored = colorize(t.replace("\n", " "), keywords)
            if "{\\c" in colored:
                n_emph += 1
            f.write(f"Dialogue: 0,{SP.s2ass(s)},{SP.s2ass(e)},Bibl,,0,0,0,,{colored}\n")
    return n_emph


def main():
    if len(sys.argv) < 2:
        print('사용: python3 emphasis_subs.py "자막.srt" [강조어...]'); sys.exit(1)
    srt = sys.argv[1]
    if not os.path.exists(srt):
        print("파일 없음:", srt); sys.exit(1)
    keywords = sys.argv[2:]

    cues = SP.parse_srt(srt)
    out = os.path.splitext(srt)[0] + "_emphasis.ass"
    n = write_ass(cues, out, keywords)
    print(f"강조 자막 완료 → {os.path.basename(out)}")
    print(f"  강조 적용 자막 {n}개 / 전체 {len(cues)}개 (숫자·단위 자동 + 키워드 {len(keywords)}개)")
    print(f"  강조색 teal(#2dd4bf). 색은 파일 상단 EMPH_COLOR 로 변경.")


if __name__ == "__main__":
    main()
