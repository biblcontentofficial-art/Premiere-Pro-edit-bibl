#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_subtitles.py — 컷된 타임라인에 정렬된 SRT 자막 생성 (로컬 Whisper)

흐름:
  1) 원본의 무음 구간을 다시 계산해 '살린 구간(keeps)'을 얻는다 (silence_cut.py 재사용)
  2) 정리된 오디오(없으면 원본)를 mlx-whisper로 단어 단위 받아쓰기
  3) 각 단어 시각을, 잘려나간 무음만큼 당겨서 '컷 타임라인'으로 재정렬
     - 무음 구간에 잡힌 환청 단어는 버린다
  4) 단어를 자연스러운 자막 줄로 묶어 SRT로 출력

사용:
  python3 make_subtitles.py "<원본영상.mp4>" [출력.srt]
"""

import sys, os, subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from silence_cut import (probe_media, detect_silence, keep_ranges_from_silence,
                         FFMPEG, run, VOICE_CHAIN)

# ── 자막 줄 묶기 설정 ──
MAX_CHARS  = 30     # 한 자막 한 줄 최대 글자수(30자 내외, 맥락 단위로 끊음)
MAX_DUR    = 5.0    # 한 자막 최대 길이(초)
GAP_SPLIT  = 0.5    # 단어 사이 간격이 이보다 크면 줄 분리(초) — 맥락 끊김 우선
MODEL      = "mlx-community/whisper-large-v3-turbo"   # 한국어 정확+빠름


def build_mapper(keeps):
    """원본 시각 → 컷 타임라인 시각. 무음(제거구간)에 있으면 None."""
    cum, acc = [], 0.0
    for a, b in keeps:
        cum.append((a, b, acc))
        acc += b - a
    def m(t):
        for a, b, base in cum:
            if a <= t <= b:
                return base + (t - a)
        return None
    return m


def srt_time(t):
    if t < 0:
        t = 0
    h = int(t // 3600); t -= h * 3600
    mn = int(t // 60); t -= mn * 60
    s = int(t); ms = int(round((t - s) * 1000))
    if ms == 1000:
        s += 1; ms = 0
    return f"{h:02d}:{mn:02d}:{s:02d},{ms:03d}"


def transcribe(audio, model=MODEL, initial_prompt=None, condition=False):
    import mlx_whisper
    print(f"> 받아쓰기 중... (모델 {model.split('/')[-1]}, 로컬)")
    r = mlx_whisper.transcribe(
        audio, path_or_hf_repo=model, language="ko",
        word_timestamps=True, fp16=True,
        condition_on_previous_text=condition,
        initial_prompt=initial_prompt,
    )
    words = []
    for seg in r["segments"]:
        for w in seg.get("words", []):
            txt = w["word"].strip()
            if txt:
                words.append((w["start"], w["end"], txt))
    return words


def regroup(words, mapper):
    """단어를 컷 타임라인으로 옮기고 자막 줄로 묶는다."""
    lines, cur, cur_text = [], [], ""
    last_end = None

    def flush():
        nonlocal cur, cur_text
        if cur:
            start = cur[0][0]; end = cur[-1][1]
            lines.append((start, end, cur_text.strip()))
        cur, cur_text = [], ""

    for ostart, oend, txt in words:
        mid = (ostart + oend) / 2
        cs = mapper(mid if mapper(ostart) is None else ostart)
        if cs is None:
            continue   # 무음 구간 환청 → 버림
        ce = mapper(oend)
        if ce is None or ce < cs:
            ce = cs + (oend - ostart)

        # 분리 조건: 큰 간격 / 글자수 초과 / 길이 초과 / 문장부호
        if cur:
            gap = cs - last_end
            too_long = len(cur_text) + len(txt) > MAX_CHARS
            too_dur = ce - cur[0][0] > MAX_DUR
            if gap > GAP_SPLIT or too_long or too_dur:
                flush()
        cur.append((cs, ce, txt))
        cur_text += (" " if cur_text and not txt.startswith((".", ",", "?", "!")) else "") + txt
        last_end = ce
        if txt.endswith((".", "?", "!", "…")):
            flush()
    flush()

    return sanitize(lines)


def sanitize(lines):
    """시작시각 순 정렬 후, 역전·겹침을 제거하고 최소 표시시간을 보장."""
    lines = sorted(lines, key=lambda x: x[0])
    out = []
    prev_end = 0.0
    for i, (s, e, t) in enumerate(lines):
        if s < prev_end:           # 이전 자막과 겹치면 시작을 뒤로
            s = prev_end
        if e <= s:                 # 역전이면 최소 길이 부여
            e = s + 0.7
        nxt = lines[i + 1][0] if i + 1 < len(lines) else None
        if nxt is not None and e > nxt:   # 다음 자막 침범 방지
            e = max(s + 0.4, nxt - 0.02)
        if e <= s:
            e = s + 0.4
        out.append((round(s, 3), round(e, 3), t))
        prev_end = e
    return out


def write_srt(lines, out):
    with open(out, "w", encoding="utf-8") as f:
        for i, (s, e, t) in enumerate(lines, 1):
            f.write(f"{i}\n{srt_time(s)} --> {srt_time(e)}\n{t}\n\n")


def main():
    if len(sys.argv) < 2:
        print("사용: python3 make_subtitles.py \"<원본영상>\" [출력.srt]"); sys.exit(1)
    path = sys.argv[1]
    if not os.path.exists(path):
        print("파일 없음:", path); sys.exit(1)

    base = os.path.splitext(os.path.basename(path))[0]
    outdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(outdir, base + "_cut.srt")

    # 정리된 오디오가 있으면 그걸로(들리는 소리와 자막 일치), 없으면 원본
    clean_wav = os.path.join(outdir, base + "_cut_audio.wav")
    audio_src = clean_wav if os.path.exists(clean_wav) else path

    print("> 무음 구간 재계산 중...")
    info = probe_media(path)
    keeps = keep_ranges_from_silence(detect_silence(path), info["duration"])
    mapper = build_mapper(keeps)
    print(f"   살린 구간 {len(keeps)}개")

    words = transcribe(audio_src)
    print(f"   받아쓴 단어 {len(words)}개")

    lines = regroup(words, mapper)
    write_srt(lines, out)
    print(f"\n자막 완료 → {out}")
    print(f"   자막 줄 {len(lines)}개")
    if lines:
        print("   미리보기:")
        for s, e, t in lines[:4]:
            print(f"     {srt_time(s)} | {t}")


if __name__ == "__main__":
    main()
