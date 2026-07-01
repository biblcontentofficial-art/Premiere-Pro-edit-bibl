#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shorts_cut.py — '컷(무음·숨소리 정리)된' 9:16 쇼츠 클립 + 6~7자 자막 생성.

전제: 먼저 마스터 영상에 auto_cut.py를 돌려
      output/<base>_cut.xml(keep) + _cut_audio.wav + _words.json 가 있어야 함.
컷 타임라인 기준 구간을 받아, 그 구간에 들어가는 keep 들을 모아
세로(1080x1920) 시퀀스로 만든다. 무음·숨소리가 빠진 타이트한 쇼츠가 된다.

사용:
  python3 shorts_cut.py <마스터영상> "MM:SS-MM:SS" "MM:SS-MM:SS" ...
  (구간은 '컷 타임라인'(=_cut.srt) 기준. 출력: output/shorts/short_NN.xml + .srt)
"""
import sys, os, re, json
from urllib.parse import quote
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from silence_cut import probe_media

SEQ_W, SEQ_H = 1080, 1920
CAP_MAX = 7          # 자막 한 줄 최대 글자수(공백 제외 기준 6~7자)


def xesc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def t2s(t):
    p = t.strip().split(":")
    return sum(float(x) * 60 ** i for i, x in enumerate(reversed(p)))


def srt_t(t):
    h = int(t // 3600); t -= h * 3600
    m = int(t // 60); t -= m * 60
    s = int(t); ms = int(round((t - s) * 1000))
    if ms == 1000: s += 1; ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_keeps(cut_xml, fps):
    """_cut.xml의 cv 클립 → (tl0,tl1,src0,src1) 초 단위 리스트(컷타임라인+원본시각)."""
    txt = open(cut_xml, encoding="utf-8").read()
    ks = []
    for ci in re.finditer(r'<clipitem id="cv\d+">.*?</clipitem>', txt, re.S):
        b = ci.group(0)
        g = lambda tag: int(re.search(rf"<{tag}>(\d+)</{tag}>", b).group(1))
        try:
            ks.append((g("start") / fps, g("end") / fps, g("in") / fps, g("out") / fps))
        except AttributeError:
            continue
    ks.sort()
    return ks


def chunk_caps(words, maxc=CAP_MAX):
    """(local_start, local_end, text) 단어들을 6~7자 한 줄로 묶는다."""
    cues, cur = [], []
    def flush():
        if cur:
            cues.append((cur[0][0], cur[-1][1], " ".join(w[2] for w in cur).strip()))
            cur.clear()
    for w in words:
        cand = (" ".join(x[2] for x in cur) + " " + w[2]).strip()
        if cur and len(cand.replace(" ", "")) > maxc:
            flush()
        cur.append(w)
        if w[2].rstrip().endswith((".", "?", "!")):
            flush()
    flush()
    # 빈칸 없이 이어붙임
    for i in range(len(cues) - 1):
        s, e, t = cues[i]
        if e < cues[i + 1][0]:
            cues[i] = (s, cues[i + 1][0], t)
    return cues


def motion(scale, vert=0.0):
    return (f'<filter><effect><name>Basic Motion</name><effectid>basic</effectid>'
            f'<effectcategory>motion</effectcategory><effecttype>motion</effecttype><mediatype>video</mediatype>'
            f'<parameter authoringApp="PremierePro"><parameterid>scale</parameterid><name>Scale</name>'
            f'<valuemin>0</valuemin><valuemax>1000</valuemax><value>{scale}</value></parameter>'
            f'<parameter authoringApp="PremierePro"><parameterid>center</parameterid><name>Center</name>'
            f'<value><horiz>0</horiz><vert>{vert}</vert></value></parameter></effect></filter>')


def build_short(video, info, wav, subclips, fps, scale):
    tb = int(round(fps)); ntsc = "TRUE" if abs(fps - round(fps)) > 0.01 else "FALSE"
    rate = f"<rate><timebase>{tb}</timebase><ntsc>{ntsc}</ntsc></rate>"
    total = int(round(info["duration"] * fps)); sr, ch = info["samplerate"], info["channels"]
    vpath = "file://" + quote(os.path.abspath(video)); vname = xesc(os.path.basename(video))
    apath = "file://" + quote(os.path.abspath(wav)); aname = xesc(os.path.basename(wav))
    vfile = (f'<file id="fv"><name>{vname}</name><pathurl>{xesc(vpath)}</pathurl>{rate}'
             f'<duration>{total}</duration><media><video><samplecharacteristics>{rate}'
             f'<width>{info["width"]}</width><height>{info["height"]}</height>'
             f'<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></video></media></file>')
    afile = (f'<file id="fa"><name>{aname}</name><pathurl>{xesc(apath)}</pathurl>{rate}'
             f'<duration>{total}</duration><media><audio><samplecharacteristics><depth>16</depth>'
             f'<samplerate>{sr}</samplerate></samplecharacteristics><channelcount>{ch}</channelcount></audio></media></file>')
    vclips, aclips = [], []
    for j, (l0, l1, s0, s1) in enumerate(subclips):
        ts, te = int(round(l0 * fps)), int(round(l1 * fps))
        si, so = int(round(s0 * fps)), int(round(s1 * fps))
        vref = vfile if j == 0 else '<file id="fv"/>'
        aref = afile if j == 0 else '<file id="fa"/>'
        vclips.append(f'<clipitem id="v{j}"><name>{vname}</name><start>{ts}</start><end>{te}</end>'
                      f'<in>{si}</in><out>{so}</out>{vref}{motion(scale)}</clipitem>')
        aclips.append(f'<clipitem id="a{j}"><name>{aname}</name><start>{ts}</start><end>{te}</end>'
                      f'<in>{si}</in><out>{so}</out>{aref}<sourcetrack><mediatype>audio</mediatype>'
                      f'<trackindex>1</trackindex></sourcetrack></clipitem>')
    dur = int(round(sum(l1 - l0 for l0, l1, _, _ in subclips) * fps))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="5">
  <sequence>
    <name>{xesc(os.path.splitext(os.path.basename(video))[0])}_short</name>
    <duration>{dur}</duration>{rate}
    <media>
      <video><format><samplecharacteristics>{rate}<width>{SEQ_W}</width><height>{SEQ_H}</height>
        <pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>
        <track>{''.join(vclips)}</track></video>
      <audio><format><samplecharacteristics><depth>16</depth><samplerate>{sr}</samplerate></samplecharacteristics></format>
        <track>{''.join(aclips)}</track></audio>
    </media>
  </sequence>
</xmeml>
"""


def main():
    if len(sys.argv) < 3:
        print('사용: python3 shorts_cut.py <마스터영상> "MM:SS-MM:SS" ...'); sys.exit(1)
    video = sys.argv[1]; ranges = sys.argv[2:]
    proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = os.path.splitext(os.path.basename(video))[0]
    outdir = os.path.join(proj, "output", "shorts"); os.makedirs(outdir, exist_ok=True)
    cut_xml = os.path.join(proj, "output", base + "_cut.xml")
    wav = os.path.join(proj, "output", base + "_cut_audio.wav")
    words_json = os.path.join(proj, "output", base + "_words.json")
    for p in (cut_xml, wav, words_json):
        if not os.path.exists(p):
            print("필요 파일 없음:", p); sys.exit(2)

    info = probe_media(video); fps = info["fps"]
    scale = round(SEQ_W / info["width"] * 100, 3)   # 가로 채우기(레터박스)
    keeps = parse_keeps(cut_xml, fps)
    words = [tuple(w) for w in json.load(open(words_json, encoding="utf-8"))]  # 원본시각 (s,e,txt)

    made = 0
    for i, rng in enumerate(ranges, 1):
        ca, cb = [t2s(x) for x in rng.split("-")]
        # 구간에 걸치는 keep → 서브클립(로컬타임/원본시각)
        sub = []; L = 0.0
        for tl0, tl1, s0, s1 in keeps:
            if tl1 <= ca or tl0 >= cb:
                continue
            o0, o1 = max(ca, tl0), min(cb, tl1)
            d = o1 - o0
            if d <= 0.02:
                continue
            sa = s0 + (o0 - tl0); sb = s0 + (o1 - tl0)
            sub.append((L, L + d, sa, sb)); L += d
        if not sub:
            print(f"  short_{i:02d}: 구간 {rng} 비어있음 — 건너뜀"); continue
        name = f"short_{i:02d}"
        open(os.path.join(outdir, name + ".xml"), "w", encoding="utf-8").write(
            build_short(video, info, wav, sub, fps, scale))
        # 자막: 원본시각 단어 → 서브클립 로컬타임으로 매핑
        loc = []
        for ws, we, tx in words:
            wm = (ws + we) / 2
            for l0, l1, sa, sb in sub:
                if sa <= wm < sb:
                    loc.append((l0 + (max(ws, sa) - sa), l0 + (min(we, sb) - sa), tx)); break
        loc.sort()
        cues = chunk_caps(loc)
        with open(os.path.join(outdir, name + ".srt"), "w", encoding="utf-8") as f:
            for k, (s, e, t) in enumerate(cues, 1):
                f.write(f"{k}\n{srt_t(s)} --> {srt_t(max(e, s + 0.4))}\n{t}\n\n")
        made += 1
        print(f"  {name}.xml ({L:.0f}s · {len(sub)}컷) + {name}.srt ({len(cues)}자막)")
    print(f"\n쇼츠 {made}개 → output/shorts/  (9:16 · 비율 {scale}% · 컷 적용 타이트)")


if __name__ == "__main__":
    main()
