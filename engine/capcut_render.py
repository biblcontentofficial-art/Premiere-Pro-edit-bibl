#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
capcut_render.py — 컷 XML을 캡컷용 MP4로 렌더 (캡컷은 FCP7 XML을 못 연다).

컷 구간(keep)을 원본에서 이어붙이고, 정리된 오디오(_cut_audio.wav, 원본 시각 정렬)를 입힌다.
원본 해상도·fps 유지, H.264 8bit(캡컷 호환), 48kHz AAC. 컷 이음매마다 5ms 오디오 페이드로 클릭음 방지.
자막은 굽지 않는다 → 캡컷 '텍스트 > 로컬 자막 가져오기'로 _cut.srt 를 불러온다
(같은 keep 구간으로 매핑된 자막이라 타이밍이 일치).

사용:
  python3 capcut_render.py "원본.mp4" "output/<base>_cut.xml" "output/<base>_cut_audio.wav" [출력.mp4]
"""
import sys, os, re, subprocess, shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from silence_cut import FFMPEG

FADE = 0.005   # 이음매 오디오 페이드(초)


def keeps_from_xml(xml_path):
    """_cut.xml 비디오 클립(cv*)의 원본 in/out(초)을 타임라인 순서로."""
    txt = open(xml_path, encoding="utf-8").read()
    tb = int(re.search(r"<timebase>(\d+)</timebase>", txt).group(1))
    fps = tb * 1000 / 1001 if "<ntsc>TRUE</ntsc>" in txt else tb
    clips = []
    for m in re.finditer(r'<clipitem id="cv\d+">(.*?)</clipitem>', txt, re.S):
        b = m.group(1)
        g = lambda k: int(re.search(rf"<{k}>(-?\d+)</{k}>", b).group(1))
        clips.append((g("start"), g("in") / fps, g("out") / fps))
    clips.sort()
    return [(a, b) for _, a, b in clips]


def _has_nvenc():
    r = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True)
    return "h264_nvenc" in r.stdout


def render(video, xml_path, audio, out):
    keeps = keeps_from_xml(xml_path)
    n = len(keeps)
    sel = "+".join(f"between(t,{a:.4f},{b:.4f})" for a, b in keeps)
    parts = [f"[0:v]select='{sel}',setpts=N/FRAME_RATE/TB,format=yuv420p[v]",
             f"[1:a]asplit={n}" + "".join(f"[s{i}]" for i in range(n))]
    for i, (a, b) in enumerate(keeps):
        d = b - a
        parts.append(f"[s{i}]atrim={a:.4f}:{b:.4f},asetpts=PTS-STARTPTS,"
                     f"afade=t=in:d={FADE},afade=t=out:st={max(0, d - FADE):.4f}:d={FADE}[a{i}]")
    parts.append("".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1,aresample=48000[a]")

    if _has_nvenc():
        venc = ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "23", "-b:v", "0",
                "-maxrate", "60M", "-bufsize", "120M"]   # 4K60 기준 ~원본 수준 용량
        hw = ["-hwaccel", "cuda"]
    else:
        venc = ["-c:v", "libx264", "-preset", "medium", "-crf", "18"]
        hw = []
    cmd = ([FFMPEG, "-hide_banner", "-y"] + hw + ["-i", video, "-i", audio,
           "-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]"] + venc +
           ["-c:a", "aac", "-b:a", "256k", "-ar", "48000",
            "-dn", "-map_metadata", "-1", "-movflags", "+faststart", out])
    print(f"> 캡컷용 MP4 렌더 중... ({n}개 구간, {sum(b - a for a, b in keeps):.1f}초)")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-1500:])
    return out


def main():
    if len(sys.argv) < 4:
        print('사용: python3 capcut_render.py "원본.mp4" "<base>_cut.xml" "<base>_cut_audio.wav" [출력.mp4]')
        sys.exit(1)
    video, xml_path, audio = sys.argv[1:4]
    out = sys.argv[4] if len(sys.argv) > 4 else xml_path.replace("_cut.xml", "_capcut.mp4")
    render(video, xml_path, audio, out)
    print(f"완료: {out}")


if __name__ == "__main__":
    main()
