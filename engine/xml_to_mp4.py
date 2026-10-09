#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xml_to_mp4.py — 쇼츠 프리미어 XML(비블쇼츠_프리미어.xml)을 그대로 업로드용 MP4로 렌더.

시퀀스 구성(V1 클립 MOV + 자막/오버레이/제목/워터마크 PNG)을 ffmpeg overlay로 합성해
프리미어 내보내기와 동일한 결과를 얻는다. 인코딩은 업로드 방탄 스펙
(30fps CFR·yuv420p·-bf 0·faststart·AAC 192k) 그대로.

사용: python3 xml_to_mp4.py <비블쇼츠_프리미어.xml> [--out DIR] [--only 이름조각]
출력: <out>/<시퀀스이름>.mp4  (기본 out = XML 폴더의 상위)
"""
import sys, os, subprocess, argparse
import xml.etree.ElementTree as ET
from urllib.parse import unquote

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from shorts_render import verify_timestamps, verify_audio_head

FPS = 30


def p_of(fileel):
    pu = fileel.find(".//pathurl")
    from silence_cut import url_to_path
    return url_to_path(pu.text) if pu is not None else None


def render_sequence(seq, files_by_id, outdir):
    name = seq.find("name").text
    F = int(seq.find("duration").text)
    tracks = seq.findall("./media/video/track")
    mov, overlays = None, []
    for ti, tr in enumerate(tracks):
        for ci in tr.findall("clipitem"):
            fe = ci.find("file")
            fid = fe.get("id")
            if fe.find("pathurl") is not None:
                files_by_id[fid] = p_of(fe)
            path = files_by_id.get(fid)
            if path is None:
                continue
            s, e = int(ci.find("start").text), int(ci.find("end").text)
            if path.endswith(".mov"):
                mov = path
            else:
                overlays.append((path, s / FPS, e / FPS))
    if mov is None:
        print(f"[{name}] V1 MOV 없음 — 건너뜀"); return None
    out = os.path.join(outdir, name + ".mp4")
    # 필터: [0:v]에 PNG를 시간창(enable)으로 차례로 overlay
    inputs = ["-i", mov]
    fc, cur = [], "[0:v]"
    for i, (png, s, e) in enumerate(overlays, 1):
        inputs += ["-loop", "1", "-i", png]
        nxt = f"[v{i}]"
        fc.append(f"{cur}[{i}:v]overlay=0:0:enable='between(t,{s:.3f},{e:.3f})'{nxt}")
        cur = nxt
    fc.append(f"{cur}format=yuv420p,fps={FPS}[vout]")
    cmd = (["ffmpeg", "-y"] + inputs +
           ["-filter_complex", ";".join(fc), "-map", "[vout]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-bf", "0", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", "-t", f"{F / FPS:.3f}", out])
    print(f"[{name}] PNG {len(overlays)}개 합성 ({F / FPS:.0f}s) → 렌더...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("  에러:\n" + r.stderr[-1000:]); return None
    ok1, m1 = verify_timestamps(out)
    ok2, m2 = verify_audio_head(out)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", out], capture_output=True, text=True).stdout.strip())
    ok3 = abs(dur - F / FPS) < 0.15
    print(f"  {'완료' if ok1 and ok2 and ok3 else '[검증실패!]'} {m1} {m2} (길이 {dur:.2f}s/{F/FPS:.2f}s)")
    return out if ok1 and ok2 and ok3 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xml"); ap.add_argument("--out", default=None)
    ap.add_argument("--only", default=None)
    a = ap.parse_args()
    outdir = a.out or os.path.dirname(os.path.dirname(os.path.abspath(a.xml)))
    os.makedirs(outdir, exist_ok=True)
    root = ET.parse(a.xml).getroot()
    files_by_id = {}
    done = 0
    for seq in root.findall(".//sequence"):
        nm = seq.find("name").text
        if a.only and a.only not in nm:
            # 파일 정의(첫 등장)가 스킵된 시퀀스에 있을 수 있어 등록만 수행
            for fe in seq.findall(".//file"):
                if fe.find("pathurl") is not None:
                    files_by_id[fe.get("id")] = p_of(fe)
            continue
        if render_sequence(seq, files_by_id, outdir):
            done += 1
    print(f"\nMP4 {done}개 → {outdir}")


if __name__ == "__main__":
    main()
