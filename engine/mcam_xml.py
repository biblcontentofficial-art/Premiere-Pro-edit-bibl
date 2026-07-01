#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcam_xml.py — N캠(2캠·3캠…) 싱크 + 러프컷이 적용된 편집가능 FCP7 XML 생성.

전제: 먼저 '마스터'(오디오·컷 기준이 될 영상)에 auto_cut.py를 돌려
      output/<master_base>_cut.xml(keep 구간) + _cut_audio.wav(정리오디오)가 있어야 함.
이 스크립트는 그 keep 구간을 읽어, 마스터와 각 카메라를 오프셋만큼 당겨 싱크한
다중 비디오 트랙 + 정리오디오 시퀀스를 만든다. 같은 컷을 모든 트랙에 동일 적용하므로
싱크가 유지된다. **소스마다 fps가 달라도(예: 카메라 29.97 + OBS 30) 시간 기반으로 정확히 맞춘다.**

사용:
  python3 mcam_xml.py <마스터> [<카메라> <offset초>]...  [--out 출력.xml]
    offset초 = 그 카메라가 마스터보다 '늦게' 시작한 초 (먼저 시작했으면 음수)
  예) python3 mcam_xml.py master.mp4 camB.mp4 3.19 obs.mp4 -43.5
"""
import sys, os, re
from urllib.parse import quote
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from silence_cut import probe_media

SEQ_W, SEQ_H = 1920, 1080      # 1080p 출력


def xesc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def parse_keeps_seconds(cut_xml, fps):
    """마스터 _cut.xml의 비디오 클립(cv*) (in,out) 프레임 → 초 단위 keep 구간."""
    txt = open(cut_xml, encoding="utf-8").read()
    keeps = []
    for ci in re.finditer(r'<clipitem id="cv\d+">.*?</clipitem>', txt, re.S):
        b = ci.group(0)
        mi = re.search(r"<in>(\d+)</in>", b)
        mo = re.search(r"<out>(\d+)</out>", b)
        if mi and mo:
            keeps.append((int(mi.group(1)) / fps, int(mo.group(1)) / fps))
    return keeps


def motion(scale):
    return (f'<filter><effect><name>Basic Motion</name><effectid>basic</effectid>'
            f'<effectcategory>motion</effectcategory><effecttype>motion</effecttype>'
            f'<mediatype>video</mediatype>'
            f'<parameter authoringApp="PremierePro"><parameterid>scale</parameterid>'
            f'<name>Scale</name><valuemin>0</valuemin><valuemax>1000</valuemax>'
            f'<value>{scale}</value></parameter></effect></filter>')


def rate_xml(tb, ntsc):
    return f"<rate><timebase>{tb}</timebase><ntsc>{ntsc}</ntsc></rate>"


def file_def(fid, path, info, with_video=True, with_audio=True):
    pathurl = "file://" + quote(os.path.abspath(path))
    fname = xesc(os.path.basename(path))
    tb = int(round(info["fps"]))
    ntsc = "TRUE" if abs(info["fps"] - round(info["fps"])) > 0.01 else "FALSE"
    total = int(round(info["duration"] * info["fps"]))
    sr = info["samplerate"]
    parts = [f'<file id="{fid}"><name>{fname}</name><pathurl>{xesc(pathurl)}</pathurl>',
             rate_xml(tb, ntsc), f'<duration>{total}</duration><media>']
    if with_video:
        parts.append(f'<video><samplecharacteristics>{rate_xml(tb, ntsc)}'
                     f'<width>{info["width"]}</width><height>{info["height"]}</height>'
                     f'<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></video>')
    if with_audio:
        parts.append(f'<audio><samplecharacteristics><depth>16</depth><samplerate>{sr}</samplerate>'
                     f'</samplecharacteristics><channelcount>{info["channels"]}</channelcount></audio>')
    parts.append('</media></file>')
    return "".join(parts)


def main():
    args = sys.argv[1:]
    out_xml = None
    if "--out" in args:
        i = args.index("--out"); out_xml = args[i + 1]; del args[i:i + 2]
    if len(args) < 1:
        print('사용: python3 mcam_xml.py <마스터> [<카메라> <offset초>]... [--out 출력.xml]'); sys.exit(1)

    master = args[0]
    cams = []   # (path, offset초)  offset = 마스터보다 늦게 시작한 초(먼저면 음수)
    rest = args[1:]
    for k in range(0, len(rest), 2):
        cams.append((rest[k], float(rest[k + 1])))

    proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = os.path.splitext(os.path.basename(master))[0]
    outdir = os.path.join(proj, "output")
    cut_xml = os.path.join(outdir, base + "_cut.xml")
    clean_wav = os.path.join(outdir, base + "_cut_audio.wav")
    if out_xml is None:
        out_xml = os.path.join(outdir, base + "_mcam.xml")
    for p in (cut_xml, clean_wav):
        if not os.path.exists(p):
            print("필요 파일 없음:", p, "\n먼저 auto_cut.py를 마스터 영상에 돌리세요."); sys.exit(2)

    mi = probe_media(master)
    seq_fps = mi["fps"]
    tb = int(round(seq_fps))
    ntsc = "TRUE" if abs(seq_fps - round(seq_fps)) > 0.01 else "FALSE"
    sr, ch = mi["samplerate"], mi["channels"]

    # 소스 목록: 마스터(offset 0) + 카메라들
    sources = [(master, 0.0, mi)]
    for path, off in cams:
        sources.append((path, off, probe_media(path)))

    keeps = parse_keeps_seconds(cut_xml, seq_fps)
    if not keeps:
        print("keep 구간을 못 읽음:", cut_xml); sys.exit(2)

    # 비디오 트랙(소스별), 클립을 시간 기반으로 배치
    vtracks = []     # 각 원소 = (clip xml 리스트, 표시용 라벨, skip 수)
    fid_map = {}
    for si, (path, off, info) in enumerate(sources):
        fid = f"file-{si+1}"
        fid_map[si] = fid
        s_fps = info["fps"]
        s_dur = info["duration"]
        scale = round(SEQ_W / info["width"] * 100, 4)
        name = os.path.basename(path)
        clips, first = [], True
        tl_t = 0.0
        skip = 0
        for k, (ta, tb_) in enumerate(keeps):
            dur_t = tb_ - ta
            if dur_t <= 0:
                continue
            ts = round(tl_t * seq_fps); te = round((tl_t + dur_t) * seq_fps)
            tl_t += dur_t
            # 이 소스의 소스시간 = 공통시간 - off
            si_in = ta - off
            si_out = tb_ - off
            if si_in < 0 or si_out > s_dur:      # 이 컷 시점에 이 소스 영상이 없음
                skip += 1
                continue
            inf = round(si_in * s_fps); outf = round(si_out * s_fps)
            ref = file_def(fid, path, info, True, (si == 0)) if first else f'<file id="{fid}"/>'
            first = False
            clips.append(f'<clipitem id="{fid}_{k}"><name>{xesc(name)}</name>'
                         f'<start>{ts}</start><end>{te}</end><in>{inf}</in><out>{outf}</out>'
                         f'{ref}{motion(scale)}</clipitem>')
        vtracks.append((clips, name, scale, skip))

    # 오디오: 마스터 정리 wav (마스터 소스시간 = 공통시간)
    a_info = {**mi, "width": 0, "height": 0}
    a_clips = []; tl_t = 0.0; first = True
    for k, (ta, tb_) in enumerate(keeps):
        dur_t = tb_ - ta
        if dur_t <= 0:
            continue
        ts = round(tl_t * seq_fps); te = round((tl_t + dur_t) * seq_fps); tl_t += dur_t
        inf = round(ta * seq_fps); outf = round(tb_ * seq_fps)
        ref = file_def("file-a", clean_wav, a_info, False, True) if first else '<file id="file-a"/>'
        first = False
        a_clips.append(f'<clipitem id="a_{k}"><name>{xesc(os.path.basename(clean_wav))}</name>'
                       f'<start>{ts}</start><end>{te}</end><in>{inf}</in><out>{outf}</out>'
                       f'{ref}<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack></clipitem>')

    seq_dur = round(sum(b - a for a, b in keeps) * seq_fps)
    r = rate_xml(tb, ntsc)
    # 트랙 순서: 마스터를 맨 위(마지막 track)로 → 기본 표시 = 마스터 + 마스터 오디오
    ordered = vtracks[1:] + vtracks[:1]
    vtrack_xml = "".join(f"<track>{''.join(c)}</track>" for c, _n, _s, _sk in ordered)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="5">
  <sequence id="{xesc(base)}_mcam">
    <name>{xesc(base)} [{len(sources)}캠 러프컷]</name>
    <duration>{seq_dur}</duration>
    {r}
    <media>
      <video>
        <format><samplecharacteristics>{r}<width>{SEQ_W}</width><height>{SEQ_H}</height>
          <pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>
        {vtrack_xml}
      </video>
      <audio>
        <format><samplecharacteristics><depth>16</depth><samplerate>{sr}</samplerate></samplecharacteristics></format>
        <track>{''.join(a_clips)}</track>
      </audio>
    </media>
  </sequence>
</xmeml>
"""
    open(out_xml, "w", encoding="utf-8").write(xml)
    secs = seq_dur / seq_fps
    print(f"{len(sources)}캠 XML 생성: {out_xml}")
    print(f"  컷 {len(keeps)}개 · 시퀀스 {int(secs//60)}:{secs%60:04.1f} · {SEQ_W}x{SEQ_H}@{tb}{'(ntsc)' if ntsc=='TRUE' else ''}")
    for si, (path, off, info) in enumerate(sources):
        _c, _n, sc, sk = vtracks[si]
        tag = "마스터" if si == 0 else f"offset {off:+.2f}s"
        note = f" · 범위밖 {sk}컷 비움" if sk else ""
        print(f"  - {os.path.basename(path)} ({tag}) 비율 {sc}% · {info['fps']:.3f}fps{note}")
    print("  ※ 트랙 맨 위 = 마스터(기본 표시) · 아래 트랙들 = 다른 앵글(전환용)")


if __name__ == "__main__":
    main()
