#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shorts_xml.py — 쇼츠를 '편집 가능한 9:16 XML'로 생성 (mp4 아님).

레이아웃: 검정 1080x1920(9:16) 배경에 16:9 롱폼을 잘리지 않게 ~85% 폭으로 축소·가운데 배치.
(이미지처럼 위/아래 검정 여백 → 위엔 제목, 아래엔 자막 들어갈 공간)
+ 같은 구간의 자막을 6~7자 한 줄로, 비블 스타일 ASS/SRT로 내보냄.

사용:
  python3 shorts_xml.py "원본.mp4" "12:08-13:14" "03:07-04:08" ...
출력: output/shorts/short_01.xml + short_01.ass + short_01.srt ...
"""

import sys, os, json
from urllib.parse import quote
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from silence_cut import probe_media

# ── 레이아웃 설정 ──
SEQ_W, SEQ_H = 1080, 1920      # 9:16
VIDEO_WIDTH_RATIO = 0.85       # 16:9 영상을 프레임 폭의 몇 %로 (이미지 기준 ~85%)
VIDEO_CENTER_VERT = -0.06      # 세로 위치(-=위로). 위 제목/아래 자막 공간 확보

# ── 자막 설정 (이미지의 가운데 자막과 동일 느낌) ──
CAP_MAX_CHARS = 7              # 한 줄 6~7자
CAP_FONT = "Pretendard"
CAP_SIZE = 84
CAP_MARGIN_V = 470            # 하단에서 위로(자막을 영상 아래 검정 영역에)


def t2s(t):
    s = 0
    for p in t.split(":"):
        s = s * 60 + float(p)
    return s


def fps_int(info):
    return int(round(info["fps"]))


def build_xml(video, info, start, end, seq_name):
    fps = fps_int(info)
    f = lambda t: int(round(t * info["fps"]))
    s_in, s_out = f(start), f(end)
    dur = s_out - s_in
    total = f(info["duration"])
    sr, ch = info["samplerate"], info["channels"]
    pathurl = "file://" + quote(os.path.abspath(video))
    fname = xesc(os.path.basename(video))

    # 16:9 → 9:16 폭 85%로 축소: 프리미어 Motion Scale(%) = 목표폭 / 소스폭
    scale = round(SEQ_W * VIDEO_WIDTH_RATIO / info["width"] * 100, 2)

    rate = f"<rate><timebase>{fps}</timebase><ntsc>FALSE</ntsc></rate>"
    filefull = (f'<file id="f1"><name>{fname}</name><pathurl>{xesc(pathurl)}</pathurl>'
                f'{rate}<duration>{total}</duration><media>'
                f'<video><samplecharacteristics>{rate}<width>{info["width"]}</width>'
                f'<height>{info["height"]}</height></samplecharacteristics></video>'
                f'<audio><samplecharacteristics><depth>16</depth><samplerate>{sr}</samplerate>'
                f'</samplecharacteristics><channelcount>{ch}</channelcount></audio></media></file>')

    motion = (f'<filter><effect><name>Basic Motion</name><effectid>basic</effectid>'
              f'<effectcategory>motion</effectcategory><effecttype>motion</effecttype>'
              f'<mediatype>video</mediatype>'
              f'<parameter authoringApp="PremierePro"><parameterid>scale</parameterid>'
              f'<name>Scale</name><valuemin>0</valuemin><valuemax>1000</valuemax>'
              f'<value>{scale}</value></parameter>'
              f'<parameter authoringApp="PremierePro"><parameterid>center</parameterid>'
              f'<name>Center</name><value><horiz>0</horiz><vert>{VIDEO_CENTER_VERT}</vert></value>'
              f'</parameter></effect></filter>')

    vclip = (f'<clipitem id="v1"><name>{fname}</name>{rate}'
             f'<start>0</start><end>{dur}</end><in>{s_in}</in><out>{s_out}</out>'
             f'{filefull}{motion}</clipitem>')
    aclip = (f'<clipitem id="a1"><name>{fname}</name>{rate}'
             f'<start>0</start><end>{dur}</end><in>{s_in}</in><out>{s_out}</out>'
             f'<file id="f1"/><sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex>'
             f'</sourcetrack></clipitem>')

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="5">
  <sequence id="{xesc(seq_name)}">
    <name>{xesc(seq_name)}</name>
    <duration>{dur}</duration>
    {rate}
    <media>
      <video>
        <format><samplecharacteristics>{rate}<width>{SEQ_W}</width><height>{SEQ_H}</height>
          <pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>
        <track>{vclip}</track>
      </video>
      <audio>
        <format><samplecharacteristics><depth>16</depth><samplerate>{sr}</samplerate></samplecharacteristics></format>
        <track>{aclip}</track>
      </audio>
    </media>
  </sequence>
</xmeml>
"""


def xesc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def main():
    if len(sys.argv) < 3:
        print('사용: python3 shorts_xml.py "원본.mp4" "in-out" ...'); sys.exit(1)
    video = sys.argv[1]
    ranges = sys.argv[2:]
    info = probe_media(video)
    proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    outdir = os.path.join(proj, "output", "shorts")
    os.makedirs(outdir, exist_ok=True)

    made = 0
    for i, rng in enumerate(ranges, 1):
        a, b = rng.split("-")
        start, end = t2s(a.strip()), t2s(b.strip())
        name = f"short_{i:02d}"
        xml = build_xml(video, info, start, end, name)
        open(os.path.join(outdir, name + ".xml"), "w", encoding="utf-8").write(xml)
        made += 1
        print(f"  {name}.xml ({end-start:.0f}s)")
    print(f"\n쇼츠 XML {made}개 → output/shorts/  (16:9를 {int(VIDEO_WIDTH_RATIO*100)}% 축소·가운데)")


if __name__ == "__main__":
    main()
