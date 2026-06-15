#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
silence_cut.py — 무음제거 + 음량 보정 러프컷 생성기 (프리미어용 FCP7 XML)

원본 영상 1개를 입력받아:
  1) 무음 구간을 감지하고 (ffmpeg silencedetect)
  2) 말하는 구간만 갭 없이 이어붙인 '점프컷' 시퀀스를 만들고
  3) 목소리 음량을 목표 레벨로 맞추는 게인을 계산해 클립에 넣고
  4) 프리미어에서 '불러오기'로 열리는 .xml(FCP7 XML)을 출력한다.

원본은 건드리지 않는다(비파괴). 불러온 시퀀스는 평소처럼 전부 수정 가능.

사용:
  python3 silence_cut.py "<영상경로.mp4>" [출력.xml]
"""

import sys, os, re, subprocess, math
from urllib.parse import quote

# ─────────────────────────────────────────────────────────────
# 설정 (여기 숫자만 바꾸면 동작이 달라짐)
# ─────────────────────────────────────────────────────────────
NOISE_DB      = -30.0   # 이 데시벨보다 조용하면 '무음'으로 봄 (-30~-35 권장)
MIN_SILENCE   = 0.5     # 이 길이(초) 이상 조용해야 컷한다
PAD_LEAD      = 0.10    # 말이 끝난 뒤 남기는 여유(초)
PAD_TAIL      = 0.16    # 다음 말이 시작되기 전 남기는 여유(초) — 비대칭(tail↑)으로 끊김 완화
MIN_KEEP      = 0.25    # 이보다 짧게 남는 토막(초)은 버린다

TARGET_LUFS   = -14.0   # 유튜브 표준 라우드니스
TARGET_PEAK_DB = -6.0   # 트루피크가 이 값을 넘지 않게 (둘 다 만족시키는 게인 선택)

CLEAN_AUDIO   = True    # True=압축+노멀라이즈한 오디오 따로 만들어 연결 / False=정적 게인만

FFMPEG = os.path.expanduser("~/bin/ffmpeg")
if not os.path.exists(FFMPEG):
    FFMPEG = "ffmpeg"

# ─────────────────────────────────────────────────────────────


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


import shutil as _shutil
FFPROBE = _shutil.which("ffprobe") or os.path.expanduser("~/bin/ffprobe")
HAS_FFPROBE = os.path.exists(FFPROBE) if "/" in FFPROBE else bool(_shutil.which("ffprobe"))


def probe_media(path):
    """미디어 정보 파싱. ffprobe 있으면 정밀하게, 없으면 ffmpeg -i 파싱."""
    if HAS_FFPROBE:
        try:
            import json as _json
            r = run([FFPROBE, "-v", "error", "-show_entries",
                     "format=duration:stream=codec_type,width,height,r_frame_rate,sample_rate,channels",
                     "-of", "json", path])
            d = _json.loads(r.stdout)
            info = {"duration": float(d["format"]["duration"])}
            for s in d["streams"]:
                if s.get("codec_type") == "video":
                    info["width"] = int(s["width"]); info["height"] = int(s["height"])
                    num, den = s["r_frame_rate"].split("/")
                    info["fps"] = round(int(num) / int(den), 3)
                elif s.get("codec_type") == "audio":
                    info["samplerate"] = int(s.get("sample_rate", 48000))
                    info["channels"] = int(s.get("channels", 2))
            info.setdefault("samplerate", 48000); info.setdefault("channels", 2)
            if "width" in info and "fps" in info:
                return info
        except Exception:
            pass  # 실패하면 아래 ffmpeg 파싱으로 폴백

    r = run([FFMPEG, "-hide_banner", "-i", path])
    err = r.stderr
    info = {}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", err)
    h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    info["duration"] = h * 3600 + mi * 60 + s
    mv = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", err)
    info["width"], info["height"] = int(mv.group(1)), int(mv.group(2))
    mf = re.search(r"(\d+(?:\.\d+)?)\s*fps", err)
    info["fps"] = float(mf.group(1))
    ma = re.search(r"Audio:.*?(\d+) Hz.*?(mono|stereo|(\d+) channels)", err)
    if ma:
        info["samplerate"] = int(ma.group(1))
        info["channels"] = 1 if ma.group(2) == "mono" else (2 if ma.group(2) == "stereo" else int(ma.group(3)))
    else:
        info["samplerate"], info["channels"] = 48000, 2
    return info


def detect_silence(path):
    """무음 구간 [(start,end), ...] 반환."""
    r = run([FFMPEG, "-hide_banner", "-nostats", "-i", path,
             "-af", f"silencedetect=noise={NOISE_DB}dB:d={MIN_SILENCE}",
             "-f", "null", "-"])
    err = r.stderr
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?\d+\.?\d*)", err)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*(-?\d+\.?\d*)", err)]
    sil = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        sil.append((max(0.0, s), e))
    return sil


def measure_loudness(path, which="input"):
    """loudnorm 분석 패스로 통합 라우드니스(I)와 트루피크(TP) 측정."""
    r = run([FFMPEG, "-hide_banner", "-i", path,
             "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
             "-f", "null", "-"])
    err = r.stderr
    def g(key):
        m = re.search(rf'"{which}_{key}"\s*:\s*"?(-?\d+\.?\d*)"?', err)
        return float(m.group(1)) if m else None
    return {"I": g("i"), "TP": g("tp"), "LRA": g("lra")}


# 목소리 정리 체인: 럼블 제거 → 압축(튀는 소리 억제) → -14 LUFS 노멀라이즈
VOICE_CHAIN = ("highpass=f=80,"
               "acompressor=threshold=-20dB:ratio=3:attack=5:release=150:makeup=2,"
               f"loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11")


def make_clean_audio(path, out_wav, info, extra_filters=""):
    """원본 전체 길이를 그대로 두고 목소리만 정리한 WAV 생성(비파괴 사이드카).
       extra_filters: 노이즈제거/디에서 등 앞단에 끼울 추가 ffmpeg 필터(콤마 포함)."""
    chain = (f"{extra_filters}" if extra_filters else "") + (
        "highpass=f=80,"
        "acompressor=threshold=-20dB:ratio=3:attack=5:release=150:makeup=2,"
        f"loudnorm=I={TARGET_LUFS}:TP=-1.5:LRA=11")
    r = run([FFMPEG, "-hide_banner", "-y", "-i", path,
             "-af", chain, "-vn",
             "-c:a", "pcm_s16le", "-ar", str(info["samplerate"]),
             "-ac", str(info["channels"]), out_wav])
    return os.path.exists(out_wav) and os.path.getsize(out_wav) > 0


def keep_ranges_from_silence(silences, total):
    """무음의 여집합 = 살릴 구간. 패딩/최소길이 적용 후 병합."""
    keeps = []
    cursor = 0.0
    for s, e in silences:
        if e is None:
            e = total
        if s > cursor:
            keeps.append([cursor, s])
        cursor = e
    if cursor < total:
        keeps.append([cursor, total])

    # 패딩(앞뒤 여유) 적용
    padded = []
    for a, b in keeps:
        # 비대칭: 말 시작 전(tail)은 넉넉히, 말 끝 뒤(lead)는 조금 — 끊김 소리 완화
        padded.append([max(0.0, a - PAD_TAIL), min(total, b + PAD_LEAD)])

    # 겹치면 병합
    merged = []
    for seg in padded:
        if merged and seg[0] <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], seg[1])
        else:
            merged.append(seg)

    # 너무 짧은 토막 제거
    return [(a, b) for a, b in merged if (b - a) >= MIN_KEEP]


def compute_gain_db(loud):
    """LUFS와 피크를 둘 다 만족시키는 단일 게인(dB) 계산."""
    I, TP = loud["I"], loud["TP"]
    if I is None or TP is None:
        return 0.0
    lufs_gain = TARGET_LUFS - I          # -14 LUFS 맞추는 게인
    peak_gain = TARGET_PEAK_DB - TP      # 피크가 -6 넘지 않게 하는 상한
    return round(min(lufs_gain, peak_gain), 2)


def f2frames(t, fps):
    return int(round(t * fps))


def audio_fade_filter(dur_frames, fade_frames):
    """컷마다 오디오 클립에 페이드 인/아웃(레벨 키프레임) — 클릭/팝 제거."""
    f = min(fade_frames, max(1, dur_frames // 2))
    if fade_frames < 1 or dur_frames < 2:
        return ""
    return ("<filter><effect>"
            "<name>Audio Levels</name><effectid>audiolevels</effectid>"
            "<effecttype>audiolevels</effecttype><mediatype>audio</mediatype>"
            "<parameter><name>Level</name><parameterid>level</parameterid><value>1</value>"
            f"<keyframe><when>0</when><value>0</value></keyframe>"
            f"<keyframe><when>{f}</when><value>1</value></keyframe>"
            f"<keyframe><when>{dur_frames - f}</when><value>1</value></keyframe>"
            f"<keyframe><when>{dur_frames}</when><value>0</value></keyframe>"
            "</parameter></effect></filter>")


def build_fcp7_xml(path, info, keeps, gain_db, seq_name, clean_audio=None, fade_frames=0):
    fps = info["fps"]
    timebase = int(round(fps))
    ntsc = "TRUE" if abs(fps - timebase * 1000 / 1001) < 0.01 else "FALSE"
    total_src_frames = f2frames(info["duration"], fps)
    w, h = info["width"], info["height"]
    sr, ch = info["samplerate"], info["channels"]

    abspath = os.path.abspath(path)
    pathurl = "file://" + quote(abspath)
    fname = os.path.basename(abspath)
    gain_lin = round(10 ** (gain_db / 20.0), 6)

    # 정리한 오디오를 따로 연결할지 여부
    use_clean = clean_audio is not None
    if use_clean:
        a_abspath = os.path.abspath(clean_audio)
        a_pathurl = "file://" + quote(a_abspath)
        a_fname = os.path.basename(a_abspath)

    def rate(tb=timebase):
        return f"<rate><timebase>{tb}</timebase><ntsc>{ntsc}</ntsc></rate>"

    # 파일 정의(첫 등장 시 전체, 이후 id 참조)
    file_full = f"""<file id="file-1">
              <name>{xesc(fname)}</name>
              <pathurl>{xesc(pathurl)}</pathurl>
              {rate()}
              <duration>{total_src_frames}</duration>
              <media>
                <video><samplecharacteristics>{rate()}<width>{w}</width><height>{h}</height>
                  <pixelaspectratio>square</pixelaspectratio></samplecharacteristics></video>
                <audio><samplecharacteristics><depth>16</depth><samplerate>{sr}</samplerate></samplecharacteristics>
                  <channelcount>{ch}</channelcount></audio>
              </media>
            </file>"""

    audio_filter = f"""<filter>
                <effect>
                  <name>Audio Levels</name>
                  <effectid>audiolevels</effectid>
                  <effectcategory>audiolevels</effectcategory>
                  <effecttype>audiolevels</effecttype>
                  <mediatype>audio</mediatype>
                  <parameter>
                    <name>Level</name><parameterid>level</parameterid>
                    <value>{gain_lin}</value>
                  </parameter>
                </effect>
              </filter>"""

    # 정리한 오디오 파일(file-2) 정의 — 사용할 때만
    a_file_full = f"""<file id="file-2">
              <name>{xesc(a_fname)}</name>
              <pathurl>{xesc(a_pathurl)}</pathurl>
              {rate()}
              <duration>{total_src_frames}</duration>
              <media>
                <audio><samplecharacteristics><depth>16</depth><samplerate>{sr}</samplerate></samplecharacteristics>
                  <channelcount>{ch}</channelcount></audio>
              </media>
            </file>""" if use_clean else ""

    v_items, a_items = [], []
    tl = 0  # 타임라인 누적 프레임
    for idx, (a, b) in enumerate(keeps):
        s_in = f2frames(a, fps)
        s_out = f2frames(b, fps)
        dur = s_out - s_in
        if dur <= 0:
            continue
        tl_start = tl
        tl_end = tl + dur
        tl = tl_end
        fileref = file_full if idx == 0 else '<file id="file-1"/>'
        vid = f"cv{idx}"
        aid = f"ca{idx}"
        link = f"""<link><linkclipref>{vid}</linkclipref><mediatype>video</mediatype><trackindex>1</trackindex><clipindex>{idx+1}</clipindex></link>
            <link><linkclipref>{aid}</linkclipref><mediatype>audio</mediatype><trackindex>1</trackindex><clipindex>{idx+1}</clipindex></link>"""

        v_items.append(f"""<clipitem id="{vid}">
            <name>{xesc(fname)}</name>
            {rate()}
            <start>{tl_start}</start><end>{tl_end}</end>
            <in>{s_in}</in><out>{s_out}</out>
            {fileref}
            {link}
          </clipitem>""")

        if use_clean:
            a_fileref = a_file_full if idx == 0 else '<file id="file-2"/>'
            a_filter = audio_fade_filter(dur, fade_frames)   # 클릭 제거용 페이드
        else:
            a_fileref = '<file id="file-1"/>'
            a_filter = audio_filter
        a_items.append(f"""<clipitem id="{aid}">
            <name>{xesc(a_fname if use_clean else fname)}</name>
            {rate()}
            <start>{tl_start}</start><end>{tl_end}</end>
            <in>{s_in}</in><out>{s_out}</out>
            {a_fileref}
            <sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>
            {a_filter}
            {link}
          </clipitem>""")

    seq_dur = tl
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="5">
  <sequence id="sequence-1">
    <name>{xesc(seq_name)}</name>
    <duration>{seq_dur}</duration>
    {rate()}
    <media>
      <video>
        <format>
          <samplecharacteristics>
            {rate()}
            <width>{w}</width><height>{h}</height>
            <pixelaspectratio>square</pixelaspectratio>
          </samplecharacteristics>
        </format>
        <track>
          {''.join(v_items)}
        </track>
      </video>
      <audio>
        <format><samplecharacteristics><depth>16</depth><samplerate>{sr}</samplerate></samplecharacteristics></format>
        <track>
          {''.join(a_items)}
        </track>
      </audio>
    </media>
  </sequence>
</xmeml>
"""
    return xml, seq_dur


def xesc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def fmt(t):
    return f"{int(t//60)}분 {t%60:0.1f}초"


def main():
    if len(sys.argv) < 2:
        print("사용: python3 silence_cut.py \"<영상경로>\" [출력.xml]")
        sys.exit(1)
    path = sys.argv[1]
    if not os.path.exists(path):
        print("파일 없음:", path); sys.exit(1)

    base = os.path.splitext(os.path.basename(path))[0]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", base + "_cut.xml")

    print("> 미디어 분석 중...")
    info = probe_media(path)
    print(f"   길이 {fmt(info['duration'])} · {info['width']}x{info['height']} · {info['fps']}fps · {info['channels']}ch")

    print("> 무음 감지 중... (오디오 1패스)")
    sil = detect_silence(path)
    keeps = keep_ranges_from_silence(sil, info["duration"])
    kept = sum(b - a for a, b in keeps)
    removed = info["duration"] - kept
    print(f"   무음 구간 {len(sil)}개 발견 → 살린 구간 {len(keeps)}개")
    print(f"   제거된 무음: {fmt(removed)}  ({removed/info['duration']*100:.1f}%)")
    print(f"   최종 길이:   {fmt(kept)}")

    print("> 음량 분석 중... (오디오 1패스)")
    loud = measure_loudness(path)
    gain = compute_gain_db(loud)
    print(f"   원본: {loud['I']} LUFS / 트루피크 {loud['TP']} dB")

    clean_audio = None
    if CLEAN_AUDIO:
        wav = os.path.splitext(out)[0] + "_audio.wav"
        print("> 오디오 정리 중... (럼블제거→압축→-14 LUFS 노멀라이즈, 인코딩 1패스)")
        if make_clean_audio(path, wav, info):
            after = measure_loudness(wav)
            clean_audio = wav
            print(f"   정리됨: {after['I']} LUFS / 트루피크 {after['TP']} dB  → {os.path.basename(wav)}")
        else:
            print("   [주의] 오디오 정리 실패 — 정적 게인으로 폴백")

    print("> 프리미어 XML 생성 중...")
    xml, seq_dur = build_fcp7_xml(path, info, keeps, gain, base + " [러프컷]",
                                  clean_audio=clean_audio)
    with open(out, "w", encoding="utf-8") as f:
        f.write(xml)
    print(f"\n완료 → {out}")
    print(f"   프리미어에서 파일 > 불러오기 → 이 .xml 선택하면 컷·음량 적용된 시퀀스가 열립니다.")


if __name__ == "__main__":
    main()
