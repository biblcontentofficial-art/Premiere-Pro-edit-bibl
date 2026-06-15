#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_2cam.py — 2캠(예: 얼굴 카메라 + OBS 화면녹화) 오디오 싱크 오프셋 계산.

서로 다른 마이크/장치로 녹음돼도 맞도록 '진폭 엔벨로프 교차상관'을 사용한다.
각 파일에서 모노 오디오를 뽑아 정류(abs)→블록평균(엔벨로프)→FFT 교차상관으로
가장 잘 맞는 지연(lag)을 찾는다.

사용:
  python3 sync_2cam.py "A.mov" "B.mp4"
출력(JSON): 두 파일을 한 타임라인에 놓을 때 각자의 start(초), 어느 쪽이 먼저인지, 신뢰도.
"""
import sys, os, json, subprocess
import numpy as np
from scipy.signal import correlate

FFMPEG = os.path.expanduser("~/bin/ffmpeg")
if not os.path.exists(FFMPEG):
    FFMPEG = "ffmpeg"

SR = 4000          # 오디오 추출 샘플레이트
ENV_RATE = 200     # 엔벨로프 샘플레이트(블록평균 후)
MAX_LAG_S = 120    # 탐색할 최대 오프셋(초)


def extract_env(path, stream="a:0", sr=SR, env_rate=ENV_RATE):
    """파일의 오디오 한 스트림 → 모노 → 진폭 엔벨로프(env_rate Hz)."""
    cmd = [FFMPEG, "-v", "error", "-i", path, "-map", "0:" + stream,
           "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0 or not p.stdout:
        return None
    x = np.frombuffer(p.stdout, dtype=np.float32).astype(np.float64)
    if x.size < sr:
        return None
    rect = np.abs(x)
    block = max(1, sr // env_rate)
    n = (rect.size // block) * block
    env = rect[:n].reshape(-1, block).mean(axis=1)
    # 정규화
    env = env - env.mean()
    s = env.std()
    if s > 0:
        env = env / s
    return env


def best_lag(envA, envB, env_rate=ENV_RATE, max_lag_s=MAX_LAG_S):
    """envA를 기준으로 envB가 얼마나 지연됐는지(초) + 정규화 피크(0~1)."""
    corr = correlate(envA, envB, mode="full", method="fft")
    lags = np.arange(-(len(envB) - 1), len(envA))
    max_lag = int(max_lag_s * env_rate)
    keep = np.abs(lags) <= max_lag
    corr_k = corr[keep]
    lags_k = lags[keep]
    i = int(np.argmax(corr_k))
    lag_samples = int(lags_k[i])
    # 신뢰도: 피크 / (정규화에너지) — 대략적 SNR 지표
    denom = np.sqrt(len(envA) * len(envB))
    peak = float(corr_k[i]) / denom
    lag_sec = lag_samples / env_rate
    return lag_sec, peak


def main():
    if len(sys.argv) < 3:
        print('사용: python3 sync_2cam.py "A.mov" "B.mp4"'); sys.exit(1)
    fileA, fileB = sys.argv[1], sys.argv[2]
    envA = extract_env(fileA, "a:0")
    if envA is None:
        print(json.dumps({"error": f"A 오디오 추출 실패: {fileA}"}, ensure_ascii=False)); sys.exit(2)

    # B는 오디오 트랙이 여러 개일 수 있음 → 음성 있는 트랙 자동 선택(피크 최대)
    best = None
    for ti in range(4):
        envB = extract_env(fileB, f"a:{ti}")
        if envB is None:
            continue
        lag_sec, peak = best_lag(envA, envB)
        if best is None or peak > best["peak"]:
            best = {"b_track": ti, "lag_sec": lag_sec, "peak": peak}
    if best is None:
        print(json.dumps({"error": f"B 오디오 추출 실패: {fileB}"}, ensure_ascii=False)); sys.exit(2)

    lag = best["lag_sec"]
    # 검증된 관계(2026-06-15 실측): A[i] ↔ B[i-lag] → startA - startB = -lag.
    # 즉 lag<0 이면 A가 -lag 만큼 늦게 시작(B가 먼저), lag>0 이면 B가 늦게 시작.
    if lag <= 0:
        startA, startB = -lag, 0.0    # A가 더 늦게 시작 → B가 리더(기준 0)
        leader = os.path.basename(fileB)
    else:
        startA, startB = 0.0, lag
        leader = os.path.basename(fileA)

    out = {
        "fileA": fileA, "fileB": fileB,
        "b_audio_track": best["b_track"],
        "lag_sec": round(lag, 4),
        "startA_sec": round(startA, 4),
        "startB_sec": round(startB, 4),
        "leader": leader,
        "confidence_peak": round(best["peak"], 4),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
