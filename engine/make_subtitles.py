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

# ── 자막 줄 묶기 설정 (의미 단위 분할) ──
MIN_CHARS  = 25     # 한 자막 최소 글자수(공백 포함) — 너무 짧으면 다음과 묶음
MAX_CHARS  = 35     # 한 자막 최대 글자수(공백 포함)
MAX_DUR    = 5.0    # 한 자막 최대 길이(초)
GAP_SPLIT  = 0.6    # 단어 사이 간격이 이보다 크면 우선 분리(초)
MODEL      = "mlx-community/whisper-large-v3-turbo"   # 한국어 정확+빠름

# ── 한국어 의미 경계 사전 ──
# 줄을 '여기서 끝내면 어색한' 어절(뒤 단어를 수식 → 다음 줄로 가야 함)
BAD_END = set((
    "이 그 저 내 제 네 우리 저희 요 그게 이게 저게 "
    "한 두 세 네 다섯 여섯 일곱 여덟 아홉 열 몇 여러 모든 각 매 온갖 갖은 "
    "이런 그런 저런 어떤 무슨 웬 이런저런 "
    "더 또 좀 즉 잘 못 안 막 곧 늘 항상 자주 가끔 너무 매우 아주 정말 진짜 완전 "
    "굉장히 되게 약간 조금 거의 그냥 이제 벌써 이미 마치 가장 제일 약 총 단 무려 "
    "바로 서로 따로 별로 제대로 그대로 새로 실제로 함부로 억지로 마음대로 "  # ~로 부사(조사 로 오인 방지)
    "많이 같이 깊이 굳이 곧이 "  # ~이 부사(조사 이 오인 방지)
    "계속 결국 오히려 어차피 차라리 드디어 마침내 역시 과연 물론 사실 그저 한참 "
    "그리고 그러나 근데 그런데 하지만 그래서 그러니까 따라서 또한 게다가 왜냐하면 "
    "만약 만일 비록 본 해당 소위 이른바 즉시"
).split())

# 줄 끝으로 '아주 자연스러운' 종결/연결어미(있으면 우선 분할)
END_STRONG = ("습니다", "습니까", "ㅂ니다", "어요", "아요", "에요", "예요", "이에요",
              "거예요", "거에요", "네요", "데요", "구요", "군요", "세요", "잖아요",
              "거든요", "더라고요", "을게요", "ㄹ게요", "을까요", "나요", "가요", "래요")
# 연결어미(절 경계 — 끊어도 자연스러움)
END_CONN = ("는데", "은데", "ㄴ데", "지만", "니까", "어서", "아서", "라서", "도록",
            "다가", "거나", "든지", "든가", "려고", "면서", "으면", "면", "고", "서",
            "며", "게", "듯", "구")
# 조사/평서 종결(보통의 경계)
END_PART = ("은", "는", "이", "가", "을", "를", "에서", "으로", "에", "로", "와", "과",
            "랑", "도", "만", "까지", "부터", "한테", "에게", "께", "의", "라고",
            "이라고", "다고", "보다", "마다", "조차", "마저", "밖에",
            "다", "요", "까", "네", "음", "함", "죠", "지")


# 관형형 어미(뒤 명사를 수식 → 줄 끝으로는 비선호)
ADNOMINAL_SUF = ("있는", "없는", "하는", "되는", "오는", "가는", "보는", "주는", "사는",
                 "쓰는", "받는", "드는", "나는", "같은", "다른", "많은", "적은", "좋은",
                 "싫은", "위한", "대한", "관한", "통한", "만든", "했던", "하던", "이런",
                 "그런", "저런", "어떤")


def end_score(word):
    """이 어절로 줄을 끝낼 때의 자연스러움 점수. 높을수록 좋은 끊는 자리. 수식어는 음수."""
    w = word.strip()
    if not w:
        return 0
    if w.endswith((".", "?", "!", "…")):
        return 5
    core = w.rstrip("\"'),.…?!").strip()
    if not core:
        return 5
    if core in BAD_END:
        return -5
    if core.endswith(END_STRONG):
        return 4
    if core.endswith(END_CONN):
        return 3
    if core.endswith(ADNOMINAL_SUF):   # 관형형(뒤 명사 수식: 있는/하는/같은…) → 끊는 자리 비선호
        return 1
    if core.endswith(END_PART):
        return 2
    return 1


def _clen(ws):
    return len(" ".join(w[2] for w in ws))


def _cue(ws):
    return (ws[0][0], ws[-1][1], " ".join(w[2] for w in ws).strip())


def _split_sentences(words):
    """단어열을 문장(. ? ! …) 단위로 나눈다."""
    sents, cur = [], []
    for w in words:
        cur.append(w)
        if w[2].rstrip().endswith((".", "?", "!", "…")):
            sents.append(cur); cur = []
    if cur:
        sents.append(cur)
    return sents


def _split_long(words, min_c, max_c, ideal=30):
    """한 문장이 길면 절·조사 경계로 '균형 있게' 분할(문장 경계는 안 넘음).
    DP로 모든 줄의 (길이 균형 + 경계 자연스러움)을 동시에 최적화한다."""
    m = len(words)
    def clen(i, j):
        return len(" ".join(w[2] for w in words[i:j]))
    INF = float("inf")
    dp = [INF] * (m + 1); back = [0] * (m + 1); dp[0] = 0
    for j in range(1, m + 1):
        for i in range(j - 1, -1, -1):
            L = clen(i, j)
            if L > max_c and i < j - 1:       # 한도 초과(어절 2개 이상) → 더 크게는 불가
                break
            sc = end_score(words[j - 1][2])   # 이 줄의 끝 어절 자연스러움
            pen = 1000 if sc < 0 else 40 if sc == 1 else 8 if sc == 2 else 2 if sc == 3 else 0
            cost = (L - ideal) ** 2 + pen
            if dp[i] + cost < dp[j]:
                dp[j] = dp[i] + cost; back[j] = i
    cuts, j = [], m
    while j > 0:
        i = back[j]; cuts.append(words[i:j]); j = i
    cuts.reverse()
    return cuts


def semantic_chunk(words, min_c=MIN_CHARS, max_c=MAX_CHARS, keep_whole=38,
                   max_dur=MAX_DUR, gap_split=GAP_SPLIT):
    """문장 단위를 보존하며 자막 줄을 만든다.
    - 문장 끝(. ? !)을 넘겨서 합치지 않음 → 한 자막에 '앞문장 끝+뒷문장 시작'이 안 섞임
    - 짧은 문장은 묶고(≤max_c), 살짝 긴 문장(≤keep_whole)은 통째로, 긴 문장만 균형 분할."""
    cues, pend = [], []
    def flush():
        if pend:
            cues.append(_cue(pend)); pend.clear()
    for sent in _split_sentences(words):
        if _clen(sent) > keep_whole:            # 긴 문장 → 절 경계로 균형 분할
            flush()
            for ch in _split_long(sent, min_c, max_c):
                cues.append(_cue(ch))
        else:                                   # 짧은/보통 문장 → 완결 문장끼리만 묶음
            if pend and _clen(pend + sent) > max_c:
                flush()
            pend.extend(sent)
    flush()
    return cues


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


def map_words(words, mapper):
    """단어들을 컷 타임라인 (cs,ce,txt)로 옮긴다. 무음/제거 구간 단어는 버린다."""
    mapped = []
    for ostart, oend, txt in words:
        cs = mapper(ostart)
        if cs is None:
            cs = mapper((ostart + oend) / 2)
        if cs is None:
            continue   # 제거된 구간(무음/추임새) → 버림
        ce = mapper(oend)
        if ce is None or ce < cs:
            ce = cs + (oend - ostart)
        mapped.append((cs, ce, txt))
    mapped.sort(key=lambda x: x[0])
    return mapped


def regroup(words, mapper):
    """단어를 컷 타임라인으로 옮기고 의미 단위(25~35자)로 묶는다."""
    mapped = map_words(words, mapper)
    lines = semantic_chunk(mapped)
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
