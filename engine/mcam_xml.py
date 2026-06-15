#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcam_xml.py — 2캠(화면+얼굴) 싱크 + 러프컷이 적용된 편집가능 FCP7 XML 생성.

전제: 먼저 OBS 화면녹화(mp4)에 auto_cut.py를 돌려
      output/<base>_cut.xml(keep 구간) + _cut_audio.wav(정리오디오)가 있어야 함.
이 스크립트는 그 keep 구간을 읽어
  V1 = 화면(mp4, 기준 타임라인)  · V2 = 얼굴캠(MOV, 오프셋만큼 당겨 싱크)
  A1 = 정리된 오디오(-14 LUFS)
로 같은 컷을 양 트랙에 동일 적용해 싱크가 유지되는 2캠 시퀀스를 만든다.

사용:
  python3 mcam_xml.py <화면.mp4> <얼굴.MOV> <face_offset초> [출력.xml]
  # face_offset = 얼굴캠이 화면녹화보다 늦게 시작한 초(sync_2cam.py의 startA)
"""
import sys, os, re
from urllib.parse import quote
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from silence_cut import probe_media

SEQ_W, SEQ_H = 1920, 1080      # 1080p 출력


def xesc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def parse_keeps_frames(cut_xml):
    """auto_cut의 _cut.xml에서 비디오 클립(id cv*)의 (in,out) 프레임을 순서대로 추출."""
    txt = open(cut_xml, encoding="utf-8").read()
    keeps = []
    # 비디오 클립만(오디오 ca*는 in/out이 같아 중복 → 제외)
    for ci in re.finditer(r'<clipitem id="cv\d+">.*?</clipitem>', txt, re.S):
        blk = ci.group(0)
        mi = re.search(r"<in>(\d+)</in>", blk)
        mo = re.search(r"<out>(\d+)</out>", blk)
        if mi and mo:
            keeps.append((int(mi.group(1)), int(mo.group(1))))
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
    sr, ch = info["samplerate"], info["channels"]
    parts = [f'<file id="{fid}"><name>{fname}</name><pathurl>{xesc(pathurl)}</pathurl>',
             rate_xml(tb, ntsc), f'<duration>{total}</duration><media>']
    if with_video:
        parts.append(f'<video><samplecharacteristics>{rate_xml(tb, ntsc)}'
                     f'<width>{info["width"]}</width><height>{info["height"]}</height>'
                     f'<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></video>')
    if with_audio:
        parts.append(f'<audio><samplecharacteristics><depth>16</depth><samplerate>{sr}</samplerate>'
                     f'</samplecharacteristics><channelcount>{ch}</channelcount></audio>')
    parts.append('</media></file>')
    return "".join(parts)


def vclip(cid, fileref, name, tl_s, tl_e, s_in, s_out, scale):
    return (f'<clipitem id="{cid}"><name>{xesc(name)}</name>'
            f'<start>{tl_s}</start><end>{tl_e}</end><in>{s_in}</in><out>{s_out}</out>'
            f'{fileref}{motion(scale)}</clipitem>')


def aclip(cid, fileref, name, tl_s, tl_e, s_in, s_out):
    return (f'<clipitem id="{cid}"><name>{xesc(name)}</name>'
            f'<start>{tl_s}</start><end>{tl_e}</end><in>{s_in}</in><out>{s_out}</out>'
            f'{fileref}<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>'
            f'</clipitem>')


def main():
    if len(sys.argv) < 4:
        print('사용: python3 mcam_xml.py <화면.mp4> <얼굴.MOV> <face_offset초> [출력.xml]'); sys.exit(1)
    screen, face = sys.argv[1], sys.argv[2]
    face_off = float(sys.argv[3])
    proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = os.path.splitext(os.path.basename(screen))[0]
    outdir = os.path.join(proj, "output")
    cut_xml = os.path.join(outdir, base + "_cut.xml")
    clean_wav = os.path.join(outdir, base + "_cut_audio.wav")
    out_xml = sys.argv[4] if len(sys.argv) > 4 else os.path.join(outdir, base + "_2cam.xml")

    for p in (cut_xml, clean_wav):
        if not os.path.exists(p):
            print("필요 파일 없음:", p, "\n먼저 auto_cut.py를 화면녹화 mp4에 돌리세요."); sys.exit(2)

    si = probe_media(screen)
    fi = probe_media(face)
    fps = si["fps"]
    tb = int(round(fps))
    ntsc = "TRUE" if abs(fps - round(fps)) > 0.01 else "FALSE"
    off_f = int(round(face_off * fps))          # 얼굴캠을 당길 프레임 수
    face_total = int(round(fi["duration"] * fi["fps"]))
    sr, ch = si["samplerate"], si["channels"]

    scale_screen = round(SEQ_W / si["width"] * 100, 4)   # 화면 채우기 비율%
    scale_face = round(SEQ_W / fi["width"] * 100, 4)     # 얼굴 채우기 비율%

    keeps = parse_keeps_frames(cut_xml)
    if not keeps:
        print("keep 구간을 못 읽음:", cut_xml); sys.exit(2)

    # 파일 정의(첫 등장 시 전체)
    f_screen = file_def("file-1", screen, si, True, True)
    f_face = file_def("file-2", face, fi, True, False)
    f_wav = file_def("file-3", clean_wav, {**si, "width": 0, "height": 0}, False, True)

    v1, v2, a1 = [], [], []   # 화면, 얼굴, 오디오
    tl = 0
    face_skipped = 0
    for i, (s_in, s_out) in enumerate(keeps):
        dur = s_out - s_in
        if dur <= 0:
            continue
        ts, te = tl, tl + dur
        tl = te
        # 화면 V1
        ref = f_screen if i == 0 else '<file id="file-1"/>'
        v1.append(vclip(f"sv{i}", ref, os.path.basename(screen), ts, te, s_in, s_out, scale_screen))
        # 오디오 A1 (정리된 wav, 화면과 같은 소스시간)
        aref = f_wav if i == 0 else '<file id="file-3"/>'
        a1.append(aclip(f"sa{i}", aref, os.path.basename(clean_wav), ts, te, s_in, s_out))
        # 얼굴 V2 (오프셋만큼 당김)
        f_in, f_out = s_in - off_f, s_out - off_f
        if f_in >= 0 and f_out <= face_total:
            fref = f_face if (i == 0 or not any('file-2' in x for x in v2)) else '<file id="file-2"/>'
            v2.append(vclip(f"fv{i}", fref, os.path.basename(face), ts, te, f_in, f_out, scale_face))
        else:
            face_skipped += 1

    seq_dur = tl
    r = rate_xml(tb, ntsc)
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="5">
  <sequence id="{xesc(base)}_2cam">
    <name>{xesc(base)} [2캠 러프컷]</name>
    <duration>{seq_dur}</duration>
    {r}
    <media>
      <video>
        <format><samplecharacteristics>{r}<width>{SEQ_W}</width><height>{SEQ_H}</height>
          <pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>
        <track>{''.join(v1)}</track>
        <track>{''.join(v2)}</track>
      </video>
      <audio>
        <format><samplecharacteristics><depth>16</depth><samplerate>{sr}</samplerate></samplecharacteristics></format>
        <track>{''.join(a1)}</track>
      </audio>
    </media>
  </sequence>
</xmeml>
"""
    open(out_xml, "w", encoding="utf-8").write(xml)
    secs = seq_dur / fps
    print(f"2캠 XML 생성: {out_xml}")
    print(f"  컷 {len(keeps)}개 · 시퀀스 {int(secs//60)}:{secs%60:04.1f} · {SEQ_W}x{SEQ_H}@{tb}")
    print(f"  V1 화면(mp4) 비율 {scale_screen}% · V2 얼굴(MOV) 비율 {scale_face}% · 얼굴 오프셋 -{off_f}f({face_off}s)")
    if face_skipped:
        print(f"  ※ 얼굴캠 범위 밖 컷 {face_skipped}개는 V2 비움(화면만)")


if __name__ == "__main__":
    main()
