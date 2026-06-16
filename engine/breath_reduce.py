#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
breath_reduce.py — 단어 사이 '숨소리'를 찾아 무음 구간 줄이듯 줄인다.

숨소리(들숨/날숨)는 ① 단어와 단어 사이(발화가 없는 곳)에 있고
② 말소리보다 작고 ③ 음높이가 없는 '노이즈성'(스펙트럼이 평탄)이다.
유성음(어/음 등 하모닉 구조)은 평탄도가 낮아 자동으로 제외된다 → 추임새는 안 건드림.

말소리 기준 레벨을 자동 추정(단어 구간의 중앙값)해 상대적으로 판단하므로
녹음/노멀라이즈 레벨이 달라도 동작한다.

사용(분석/캘리브레이션):
  python3 breath_reduce.py <오디오.wav> <words.json>
"""
import os, sys, json, subprocess
import numpy as np

FFMPEG = os.path.expanduser("~/bin/ffmpeg")
if not os.path.exists(FFMPEG):
    FFMPEG = "ffmpeg"

SR = 16000
WIN = 0.020
HOP = 0.010


def load_audio(path, sr=SR):
    out = subprocess.run(
        [FFMPEG, "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        capture_output=True).stdout
    return np.frombuffer(out, dtype=np.float32).astype(np.float64), sr


def framewise(x, sr, win=WIN, hop=HOP):
    """프레임별 RMS(dB) + 스펙트럼 평탄도(0=음높이 뚜렷, 1=노이즈)."""
    w = int(win * sr); h = int(hop * sr)
    if len(x) < w:
        return None
    n = 1 + (len(x) - w) // h
    idx = (np.arange(n) * h)[:, None] + np.arange(w)[None, :]
    fr = x[idx]
    rms = np.sqrt((fr ** 2).mean(1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    wd = np.hanning(w)
    mag = np.abs(np.fft.rfft(fr * wd, axis=1)) + 1e-9
    flat = np.exp(np.log(mag).mean(1)) / mag.mean(1)
    return db, flat, h / sr


def _speech_db(db, hop_s, words):
    """단어 구간 프레임들의 중앙 dB = 말소리 기준 레벨."""
    mask = np.zeros(len(db), dtype=bool)
    for s, e, _t in words:
        i0, i1 = int(s / hop_s), int(e / hop_s)
        if i1 > i0:
            mask[i0:min(i1, len(db))] = True
    vals = db[mask]
    return float(np.median(vals)) if len(vals) else -20.0


def detect_breaths(audio, sr, words, keeps, *, min_dur=0.18,
                   rel_lo=-40.0, rel_hi=-12.0, flatness=0.38,
                   keep=0.12, pad=0.04, frac=0.45, debug=False):
    """단어 사이 숨소리를 찾아 '줄일 범위(start,end)' 리스트를 돌려준다.
    keeps(무음 제거 후 살린 구간) 안에 있는 단어 간격만 대상으로 한다."""
    fr = framewise(audio, sr)
    if fr is None:
        return []
    db, flat, hop_s = fr
    spd = _speech_db(db, hop_s, words)
    lo, hi = spd + rel_lo, spd + rel_hi      # 숨소리 음량대(말소리보다 작음)

    ws = sorted(words, key=lambda w: w[0])

    def in_keep(a, b):
        return any(a >= ka and b <= kb for ka, kb in keeps)

    removes, dbg = [], []
    for i in range(len(ws) - 1):
        g0, g1 = ws[i][1], ws[i + 1][0]
        if g1 - g0 < min_dur or not in_keep(g0, g1):
            continue
        i0, i1 = int(g0 / hop_s), int(g1 / hop_s)
        if i1 <= i0:
            continue
        sd, sf = db[i0:i1], flat[i0:i1]
        breath = (sd > lo) & (sd < hi) & (sf > flatness)
        if breath.mean() < frac:             # 간격 대부분이 숨소리여야 함
            continue
        if (g1 - g0) <= keep + 2 * pad:
            continue
        cut0 = g0 + pad + keep / 2
        cut1 = g1 - pad - keep / 2
        if cut1 - cut0 > 0.05:
            removes.append((round(cut0, 3), round(cut1, 3)))
            if debug:
                dbg.append((round(g0, 2), round(g1 - g0, 2), round(float(sd.mean()), 1),
                            round(float(sf.mean()), 2), round(float(breath.mean()), 2)))
    return (removes, dbg, spd) if debug else removes


def main():
    if len(sys.argv) < 3:
        print("사용: python3 breath_reduce.py <오디오.wav> <words.json>"); sys.exit(1)
    audio, sr = load_audio(sys.argv[1])
    words = [tuple(w) for w in json.load(open(sys.argv[2], encoding="utf-8"))]
    total = len(audio) / sr
    keeps = [(0.0, total)]
    res, dbg, spd = detect_breaths(audio, sr, words, keeps, debug=True)
    cut = sum(b - a for a, b in res)
    print(f"말소리 기준 {spd:.1f} dBFS · 숨소리 {len(res)}곳 · 총 단축 {cut:.1f}s")
    print("예시(시작s, 간격s, 평균dB, 평탄도, 숨소리비율):")
    for d in dbg[:15]:
        print("  ", d)


if __name__ == "__main__":
    main()
