#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_shorts.py — 하이라이트 구간을 9:16 숏폼 클립으로 자동 추출.

리서처가 뽑은 하이라이트 타임코드(in-out)를 받아, 가로 영상을 세로(9:16)로
센터 크롭 + 1080x1920 스케일해서 숏츠/릴스용 mp4를 만든다. 음량 정리도 함께.

사용:
  python3 make_shorts.py "영상.mp4" "10:12-10:35" "13:40-14:05"
  python3 make_shorts.py "영상.mp4" "0:10:12-0:10:35"     # h:mm:ss 도 가능
출력: output/shorts/short_01.mp4 ...
"""

import sys, os, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from silence_cut import FFMPEG


def t2s(t):
    parts = [float(x) for x in t.split(":")]
    s = 0
    for p in parts:
        s = s * 60 + p
    return s


def make_one(video, start, end, out):
    dur = end - start
    if dur <= 0:
        return False
    # 가로→세로 센터 크롭(9:16) + 1080x1920 + 음량 정리
    vf = "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920,setsar=1"
    af = "highpass=f=80,acompressor=threshold=-20dB:ratio=3:attack=5:release=150:makeup=2,loudnorm=I=-14:TP=-1.5:LRA=11"
    r = subprocess.run([FFMPEG, "-hide_banner", "-y", "-ss", f"{start:.3f}", "-i", video,
                        "-t", f"{dur:.3f}", "-vf", vf, "-af", af,
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                        "-c:a", "aac", "-b:a", "192k", out], capture_output=True, text=True)
    return os.path.exists(out) and os.path.getsize(out) > 0


def main():
    if len(sys.argv) < 3:
        print('사용: python3 make_shorts.py "영상.mp4" "in-out" ["in-out" ...]')
        print('  예: python3 make_shorts.py "영상.mp4" "10:12-10:35" "13:40-14:05"')
        sys.exit(1)
    video = sys.argv[1]
    if not os.path.exists(video):
        print("파일 없음:", video); sys.exit(1)
    ranges = sys.argv[2:]

    proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    outdir = os.path.join(proj, "output", "shorts")
    os.makedirs(outdir, exist_ok=True)

    made = 0
    for i, rng in enumerate(ranges, 1):
        try:
            a, b = rng.split("-")
            start, end = t2s(a.strip()), t2s(b.strip())
        except Exception:
            print(f"  [건너뜀] 형식 오류: {rng} (예: 10:12-10:35)")
            continue
        out = os.path.join(outdir, f"short_{i:02d}.mp4")
        print(f"> 숏폼 {i}: {a}~{b} ({end-start:.0f}초) 추출 중...")
        if make_one(video, start, end, out):
            made += 1
            print(f"  완료 → shorts/short_{i:02d}.mp4")
        else:
            print(f"  실패: {rng}")

    print(f"\n숏폼 {made}/{len(ranges)}개 생성 → output/shorts/")
    if made:
        print("  세로 9:16 · 1080x1920 · -14 LUFS. 자막은 별도로 얹으세요.")


if __name__ == "__main__":
    main()
