#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_video.py — 영상을 측정해 컷편집 프리셋(보수/표준/공격)을 자동 추천.

측정: 길이 · 라우드니스(LUFS)·트루피크 · 무음 비율 · 평균 음량
추천 근거: 무음 비율이 높으면(빈 시간 많음) 공격, 낮으면(이미 촘촘) 보수.

사용:
  python3 analyze_video.py "원본영상.mp4"
  python3 analyze_video.py "원본영상.mp4" --json   # 에이전트용 JSON
"""

import sys, os, re, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from silence_cut import FFMPEG, run, probe_media


def measure(path):
    info = probe_media(path)
    dur = info["duration"]

    # 라우드니스 + 트루피크
    r = run([FFMPEG, "-hide_banner", "-i", path,
             "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"])
    def g(k):
        m = re.search(rf'"{k}"\s*:\s*"?(-?\d+\.?\d*)"?', r.stderr)
        return float(m.group(1)) if m else None
    lufs, tp = g("input_i"), g("input_tp")

    # 평균 음량
    r2 = run([FFMPEG, "-hide_banner", "-i", path, "-af", "volumedetect", "-f", "null", "-"])
    mv = re.search(r"mean_volume:\s*(-?\d+\.?\d*)", r2.stderr)
    mean_vol = float(mv.group(1)) if mv else None

    # 무음 비율 (-40dB, 0.5초 기준)
    r3 = run([FFMPEG, "-hide_banner", "-nostats", "-i", path,
              "-af", "silencedetect=noise=-40dB:d=0.5", "-f", "null", "-"])
    sil = sum(float(x) for x in re.findall(r"silence_duration:\s*(\d+\.?\d*)", r3.stderr))
    sil_ratio = sil / dur if dur else 0

    return {"duration": dur, "lufs": lufs, "tp": tp, "mean_vol": mean_vol,
            "silence_sec": round(sil, 1), "silence_ratio": round(sil_ratio, 3)}


def recommend(m):
    sr = m["silence_ratio"]
    reasons = []
    # 대부분의 토크 영상은 표준이 안전. 무음이 아주 많을 때만 공격, 거의 없을 때만 보수.
    if sr >= 0.20:
        preset = "공격"
        reasons.append(f"무음 비율 {sr*100:.0f}% — 빈 시간이 많아 타이트하게 잘라도 됨")
    elif sr >= 0.06:
        preset = "표준"
        reasons.append(f"무음 비율 {sr*100:.0f}% — 일반적인 토크 밀도(표준 권장)")
    else:
        preset = "보수"
        reasons.append(f"무음 비율 {sr*100:.0f}% — 빈 시간이 거의 없어 덜 건드리는 게 안전")
    # 무음 비율은 추임새(아/어/음) 밀도를 못 본다 — 캐비엇
    reasons.append("주의: 무음 비율만 측정. 말이 빠르고 어/음·그러니까가 많으면 한 단계 공격적으로(표준→공격)")

    cfg = {}
    # 음량: -14에서 많이 벗어나면 정리 강조(엔진이 자동 처리하나 참고)
    if m["lufs"] is not None and m["lufs"] < -19:
        reasons.append(f"입력 {m['lufs']} LUFS — 작음, 음량 정리 효과 큼")
    # 무음 임계값: 평균 음량 기준으로 끝음 보존 여유 제안
    if m["mean_vol"] is not None:
        suggest_noise = round(m["mean_vol"] - 15)
        suggest_noise = max(-45, min(-32, suggest_noise))
        cfg["NOISE_DB"] = suggest_noise
        reasons.append(f"평균 {m['mean_vol']}dB → 끝음 보존 위해 NOISE_DB {suggest_noise} 권장")
    return preset, reasons, cfg


def main():
    args = [a for a in sys.argv[1:] if a != "--json"]
    as_json = "--json" in sys.argv
    if not args:
        print('사용: python3 analyze_video.py "영상.mp4" [--json]'); sys.exit(1)
    path = args[0]
    if not os.path.exists(path):
        print("파일 없음:", path); sys.exit(1)

    m = measure(path)
    preset, reasons, cfg = recommend(m)

    if as_json:
        print(json.dumps({"measured": m, "preset": preset, "reasons": reasons,
                          "config_suggest": cfg}, ensure_ascii=False, indent=2))
        return

    dmin = int(m["duration"] // 60)
    print(f"=== 영상 분석 ===")
    print(f"  길이 {dmin}분 · 라우드니스 {m['lufs']} LUFS · 트루피크 {m['tp']} dB")
    print(f"  평균 음량 {m['mean_vol']} dB · 무음 {m['silence_sec']}초 ({m['silence_ratio']*100:.0f}%)")
    print(f"\n>> 추천 프리셋: {preset}")
    for r in reasons:
        print(f"   - {r}")
    if cfg:
        print(f"\n  config.json 제안: {json.dumps(cfg, ensure_ascii=False)}")
    print(f"\n  실행: python3 engine/auto_cut.py \"{os.path.basename(path)}\" --preset {preset}")


if __name__ == "__main__":
    main()
