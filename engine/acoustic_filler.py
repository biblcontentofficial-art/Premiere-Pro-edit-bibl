#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
acoustic_filler.py — 어/음 망설임을 '음향 신호'로 검출 (Whisper가 글자로 안 적는 것)

원리: 어/음 같은 망설임은 voiced(성대 울림) 구간이면서 피치(F0)가 평탄하게
      오래 지속된다. 일반 발화는 피치가 계속 변한다. → 평탄 피치 plateau를 찾는다.

자연스러움 원칙: 음향 추측을 무조건 자르면 위험하므로 기본은 '후보 리포트'.
      전사본(_words.json)과 교차검증해 신뢰도를 매긴다. 자동 컷은 옵션.

사용:
  python3 acoustic_filler.py "<오디오나 영상>" [words.json]
"""

import sys, os, subprocess, json
import numpy as np

FFMPEG = os.path.expanduser("~/bin/ffmpeg")
if not os.path.exists(FFMPEG):
    FFMPEG = "ffmpeg"

SR = 8000
FRAME = 256          # 32ms
HOP = 80             # 10ms
FMIN, FMAX = 80, 320 # 사람 목소리 F0 범위(Hz)

VOICING_MIN = 0.55   # 이 이상이면 voiced로 봄
FLAT_HZ = 7.0        # 피치 표준편차가 이보다 작으면 '평탄(망설임)'
FLAT_WIN = 15        # 평탄성 판단 창(프레임, 약 0.15s)
MIN_DUR = 0.30       # 후보 최소 길이(초)


def load_audio(path):
    p = subprocess.run([FFMPEG, "-v", "error", "-i", path,
                        "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                       capture_output=True)
    return np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768.0


def analyze_chunk(a):
    """청크 오디오 → 프레임별 (rms, f0, voicing)."""
    if len(a) < FRAME:
        return np.array([]), np.array([]), np.array([])
    n = 1 + (len(a) - FRAME) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    frames = a[idx]
    win = np.hanning(FRAME).astype(np.float32)
    fw = frames * win
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-9)
    nfft = 512
    F = np.fft.rfft(fw, nfft, axis=1)
    ac = np.fft.irfft(F * np.conj(F), nfft, axis=1)[:, :FRAME]
    ac0 = ac[:, 0:1].copy(); ac0[ac0 == 0] = 1
    acn = ac / ac0
    lagmin, lagmax = SR // FMAX, SR // FMIN
    seg = acn[:, lagmin:lagmax]
    lag = seg.argmax(axis=1) + lagmin
    voicing = seg.max(axis=1)
    f0 = SR / lag
    return rms, f0, voicing


def analyze(a):
    """메모리 위해 청크(120초)로 나눠 분석."""
    step = SR * 120
    rms, f0, voi = [], [], []
    pos = 0
    while pos < len(a):
        chunk = a[pos:pos + step + FRAME]
        r, f, v = analyze_chunk(chunk)
        if len(r):
            rms.append(r); f0.append(f); voi.append(v)
        pos += step
    if not rms:
        return np.array([]), np.array([]), np.array([])
    return np.concatenate(rms), np.concatenate(f0), np.concatenate(voi)


def rolling_std(x, w):
    if len(x) < w:
        return np.full(len(x), 1e9)
    c = np.cumsum(np.insert(x, 0, 0))
    c2 = np.cumsum(np.insert(x * x, 0, 0))
    s = (c[w:] - c[:-w]) / w
    s2 = (c2[w:] - c2[:-w]) / w
    var = np.maximum(s2 - s * s, 0)
    out = np.full(len(x), 1e9)
    out[w // 2: w // 2 + len(var)] = np.sqrt(var)
    return out


def detect(rms, f0, voicing):
    """평탄 피치 plateau = 어/음 후보 구간."""
    if not len(rms):
        return []
    rms_floor = np.median(rms[voicing > VOICING_MIN]) * 0.25 if (voicing > VOICING_MIN).any() else 0
    flat = rolling_std(f0, FLAT_WIN)
    cand = (voicing > VOICING_MIN) & (rms > rms_floor) & (flat < FLAT_HZ)
    # 연속 프레임 묶기
    segs = []
    i, n = 0, len(cand)
    min_frames = int(MIN_DUR * SR / HOP)
    while i < n:
        if cand[i]:
            j = i
            while j < n and cand[j]:
                j += 1
            if j - i >= min_frames:
                segs.append((i * HOP / SR, j * HOP / SR))
            i = j
        else:
            i += 1
    return segs


FILLER_SOUND = set("아어엄음으에")


def cross_check(segs, words):
    """전사본과 교차검증해 신뢰도 부여.
       빈 구간(글자 없는 지속음) = 어/음일 확률 최고. 필러글자만 겹침 = 높음.
       실제 단어가 겹치면 낮춤(과제거 방지)."""
    out = []
    for s, e in segs:
        # 단어가 후보 구간을 충분히(절반 이상) 덮는 경우만 '실제 단어 겹침'으로 본다
        covered = ""
        for w in words:
            ov = min(w[1], e) - max(w[0], s)
            if ov > 0 and ov >= 0.5 * (w[1] - w[0]):
                covered += w[2].strip()
        dur = e - s
        if not covered:
            conf = "높음(빈구간)"        # 글자 없는 지속음 = 어/음 가능성 최고
        elif all(c in FILLER_SOUND for c in covered):
            conf = "높음(필러)"
        elif len(covered) <= 1:
            conf = "중간"
        else:
            conf = "낮음"
        out.append((s, e, dur, conf, covered[:20]))
    return out


def main():
    if len(sys.argv) < 2:
        print("사용: python3 acoustic_filler.py \"<오디오/영상>\" [words.json]"); sys.exit(1)
    path = sys.argv[1]
    words = []
    if len(sys.argv) > 2 and os.path.exists(sys.argv[2]):
        words = [tuple(w) for w in json.load(open(sys.argv[2], encoding="utf-8"))]

    print("▶ 오디오 로드 + 음향 분석 중...")
    a = load_audio(path)
    rms, f0, voi = analyze(a)
    segs = detect(rms, f0, voi)
    print(f"   평탄피치 후보 {len(segs)}개")

    checked = cross_check(segs, words) if words else [(s, e, e - s, "?", "") for s, e in segs]
    hi = [c for c in checked if c[3].startswith("높음")]
    empty = [c for c in checked if c[3] == "높음(빈구간)"]
    print(f"   신뢰도 높음: {len(hi)}개 (그중 빈구간={len(empty)} → 가장 안전한 컷 후보)")

    base = os.path.splitext(path)[0]
    rep = base + "_acoustic_fillers.txt"
    def srt(t):
        h=int(t//3600);t-=h*3600;m=int(t//60);t-=m*60;return f"{h:02d}:{m:02d}:{t:06.3f}"
    with open(rep, "w", encoding="utf-8") as f:
        f.write(f"어/음 음향 후보 ({len(checked)}개) — 신뢰도 높음만 컷 검토 권장\n\n")
        for s, e, d, conf, txt in checked:
            f.write(f"  {srt(s)}~{srt(e)}  ({d:.2f}s) 신뢰도:{conf}  겹친글자:'{txt}'\n")
    print(f"✅ 리포트 → {os.path.basename(rep)}")
    print("   미리보기(높음 5개):")
    for s, e, d, conf, txt in hi[:5]:
        print(f"     {srt(s)} ({d:.2f}s) 겹친글자:'{txt}'")


if __name__ == "__main__":
    main()
