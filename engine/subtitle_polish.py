#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
subtitle_polish.py — 자막(SRT) 고도화 + 다중 포맷 출력

하는 일:
  · 줄 균형/길이 — 한 줄 너무 길면 자연 끊김에서 2줄로, 위아래 균형
  · 읽기속도(CPS) — 너무 빨리 지나가는 자막은 시간 확장 또는 분할
  · 맞춤법/숫자  — 공백·문장부호 정리, 안전한 표기 통일
  · 타이밍 무결성 — 시작<끝, 겹침 0 보장 (틀어지면 보정)
  · 출력 — 교정 SRT + WebVTT + 비블 스타일 ASS(폰트·외곽선·위치)

사용:
  python3 subtitle_polish.py "자막.srt"
"""

import sys, os, re

# ── 설정 ──
MAX_CHARS_LINE = 38    # 한 줄 최대 글자수(의미단위 분할은 make_subtitles.semantic_chunk가 처리; 여기선 안전망)
MAX_CPS        = 9.0   # 초당 최대 글자수(읽기속도). 초과 시 확장
MIN_DUR        = 0.7   # 자막 최소 표시시간(초)
FILL_GAPS      = True  # 자막 사이 빈칸 제거 — 각 자막 끝을 다음 자막 시작까지 연장(연속 표시)

# 비블 스타일 ASS — Pretendard Bold / 55 / 흰색 / 선 없음 / 그림자(135°·거리3·불투명98)
ASS_FONT    = "Pretendard"
ASS_SIZE    = 55
ASS_OUTLINE = 0        # 선(스트로크) 없음
ASS_SHADOW  = 3        # 그림자 거리
ASS_MARGIN_V = 80


def parse_srt(path):
    cues = []
    blocks = re.split(r"\n\s*\n", open(path, encoding="utf-8-sig").read().strip())
    for b in blocks:
        lines = b.strip().split("\n")
        if len(lines) < 2:
            continue
        mi = 0
        if re.match(r"^\d+$", lines[0].strip()):
            mi = 1
        m = re.match(r"(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)", lines[mi])
        if not m:
            continue
        text = " ".join(l.strip() for l in lines[mi + 1:] if l.strip())
        cues.append([t2s(m.group(1)), t2s(m.group(2)), text])
    return cues


def t2s(t):
    t = t.replace(",", ".")
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def s2srt(t):
    h = int(t // 3600); t -= h * 3600
    m = int(t // 60); t -= m * 60
    s = int(t); ms = round((t - s) * 1000)
    if ms == 1000:
        s += 1; ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def s2vtt(t):
    return s2srt(t).replace(",", ".")


def s2ass(t):
    h = int(t // 3600); t -= h * 3600
    m = int(t // 60); t -= m * 60
    s = int(t); cs = round((t - s) * 100)
    if cs == 100:
        s += 1; cs = 0
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def normalize(text):
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.!?…])", r"\1", text)      # 부호 앞 공백 제거
    text = text.replace("퍼센트", "%").replace("프로", "%") if False else text
    text = re.sub(r"\s*%\s*", "%", text)
    return text


def split_cue(s, e, text, max_chars):
    """한 줄 max_chars 초과 시, 맥락(공백) 단위로 끊어 '여러 개의 한 줄 자막'으로 분할.
       시간은 글자수 비례로 나눈다. (2줄로 만들지 않고 한 줄 유지)"""
    text = text.strip()
    if len(text) <= max_chars or " " not in text:
        return [[s, e, text]]
    words = text.split(" ")
    chunks, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            chunks.append(cur); cur = w
        else:
            cur = (cur + " " + w) if cur else w
    if cur:
        chunks.append(cur)
    total = sum(len(c) for c in chunks) or 1
    out, t, dur = [], s, e - s
    for c in chunks:
        ct = dur * len(c) / total
        out.append([t, t + ct, c])
        t += ct
    out[-1][1] = e
    return out


def enforce_cps(cues):
    """읽기속도 과속 자막을 시간 확장 또는 분할. 타이밍 무결성 보존."""
    out = []
    for i, (s, e, t) in enumerate(cues):
        plain = t.replace("\n", "")
        dur = e - s
        if dur < MIN_DUR:
            e = s + MIN_DUR
            dur = MIN_DUR
        cps = len(plain) / dur if dur > 0 else 0
        # 다음 자막 전까지 여유가 있으면 끝을 늘려 속도 낮춤
        nxt = cues[i + 1][0] if i + 1 < len(cues) else None
        if cps > MAX_CPS and nxt is not None:
            need = len(plain) / MAX_CPS
            e = min(s + need, nxt - 0.02)
        out.append([s, max(e, s + 0.3), t])
    return out


def sanitize(cues):
    cues = sorted(cues, key=lambda x: x[0])
    out = []
    prev = 0.0
    for i, (s, e, t) in enumerate(cues):
        if s < prev:
            s = prev
        if e <= s:
            e = s + 0.4
        nxt = cues[i + 1][0] if i + 1 < len(cues) else None
        if nxt is not None and e > nxt:
            e = max(s + 0.3, nxt - 0.02)
        out.append([round(s, 3), round(e, 3), t])
        prev = e
    return out


def fill_gaps(cues):
    """자막 사이 빈칸 제거 — 각 자막 끝을 다음 자막 시작까지 연장(연속 표시).
    마지막 자막은 자기 끝 유지. 겹침 0·정렬은 sanitize가 보장한 상태에서 호출."""
    cues = sorted(cues, key=lambda x: x[0])
    for i in range(len(cues) - 1):
        nxt = cues[i + 1][0]
        if cues[i][1] < nxt:                   # 빈칸이 있으면 다음 시작까지 채움
            cues[i][1] = round(nxt, 3)
    return cues


def polish(cues):
    cues = [[s, e, normalize(t)] for s, e, t in cues]
    split = []
    for s, e, t in cues:                       # 30자 초과는 맥락 단위로 한 줄씩 분할
        split += split_cue(s, e, t, MAX_CHARS_LINE)
    cues = enforce_cps(split)
    cues = sanitize(cues)
    if FILL_GAPS:                              # 빈칸 없이 연속 표시
        cues = fill_gaps(cues)
    return cues


def write_srt(cues, path):
    with open(path, "w", encoding="utf-8") as f:
        for i, (s, e, t) in enumerate(cues, 1):
            f.write(f"{i}\n{s2srt(s)} --> {s2srt(e)}\n{t}\n\n")


def write_vtt(cues, path):
    with open(path, "w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for s, e, t in cues:
            f.write(f"{s2vtt(s)} --> {s2vtt(e)}\n{t}\n\n")


def write_ass(cues, path):
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Bibl,{ASS_FONT},{ASS_SIZE},&H00FFFFFF,&H000000FF,&H00000000,&H05000000,-1,0,0,0,100,100,0,0,1,{ASS_OUTLINE},{ASS_SHADOW},2,80,80,{ASS_MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(head)
        for s, e, t in cues:
            txt = t.replace("\n", "\\N")
            f.write(f"Dialogue: 0,{s2ass(s)},{s2ass(e)},Bibl,,0,0,0,,{txt}\n")


def main():
    if len(sys.argv) < 2:
        print("사용: python3 subtitle_polish.py \"자막.srt\""); sys.exit(1)
    path = sys.argv[1]
    if not os.path.exists(path):
        print("파일 없음:", path); sys.exit(1)

    cues = parse_srt(path)
    n0 = len(cues)
    cues = polish(cues)

    base = os.path.splitext(path)[0]
    # 원본 백업 후 SRT 교정본 덮어쓰기
    if not os.path.exists(base + ".srt.bak"):
        import shutil; shutil.copy2(path, base + ".srt.bak")
    write_srt(cues, base + ".srt")
    write_vtt(cues, base + ".vtt")
    write_ass(cues, base + ".ass")

    # 검증
    bad = sum(1 for i in range(len(cues)) if cues[i][1] <= cues[i][0]
              or (i and cues[i][0] < cues[i-1][1] - 1e-6))
    print(f"자막 고도화 완료 ({n0}→{len(cues)}줄, 타이밍오류 {bad})")
    print(f"   SRT : {os.path.basename(base)}.srt (교정, 원본은 .srt.bak)")
    print(f"   VTT : {os.path.basename(base)}.vtt")
    print(f"   ASS : {os.path.basename(base)}.ass (비블 스타일 — 폰트/외곽선/하단중앙)")


if __name__ == "__main__":
    main()
