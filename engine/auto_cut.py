#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auto_cut.py — 통합 자동 편집 엔진
  무음 제거 + 추임새/망설임/더듬 제거 + 음량 정리 + 자막 을 한 번에.

사용:
  python3 auto_cut.py "<원본영상.mp4>" [--preset 보수|표준|공격]

설정은 engine/config.py(프리셋) + 프로젝트 루트 config.json(사용자)에서 관리.
원본은 건드리지 않음(비파괴). 불러온 시퀀스는 전부 수정 가능.
"""

import sys, os, json, difflib, bisect, re, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import silence_cut as SC
from silence_cut import (probe_media, detect_silence, keep_ranges_from_silence,
                         make_clean_audio, build_fcp7_xml, measure_loudness,
                         compute_gain_db, fmt)
from make_subtitles import (transcribe, build_mapper, regroup, write_srt, srt_time)
import config as CONFIG

CFG = {}   # main()에서 config.load 결과로 채움


def norm(tok):
    return tok.strip(" .,!?…·\"'`~\n\t")


def is_filler(tok):
    t = norm(tok)
    if not t:
        return False
    if t in set(CFG["FILLER_PHRASES"]):
        return True
    sounds = set(CFG["FILLER_SOUND_CHARS"])
    if len(t) == 1 and t in sounds:
        return True
    if len(t) >= 2 and len(set(t)) == 1 and t[0] in sounds:
        return True
    return False


# '좀'이 '조금'의 뜻으로 쓰일 때(좀 더/좀 많이/좀 빨리…) 뒤에 오는 정도 표현
DEGREE_STEMS = ("더", "덜", "많", "적", "크", "작", "빨", "천천", "일찍", "늦",
                "높", "낮", "자주", "오래", "길", "짧", "세게", "약하", "쉽",
                "어렵", "멀", "가까", "느리", "조용", "급")


def keep_filler_in_context(i, sw):
    """문맥상 살려야 할 필러면 True. 지금은 '좀=조금' 케이스."""
    if not CFG.get("CONTEXT_FILLER"):
        return False
    t = norm(sw[i][2])
    if t == "좀" and i + 1 < len(sw):
        nxt = norm(sw[i + 1][2])
        if nxt.startswith(DEGREE_STEMS):     # "좀 더", "좀 많이", "좀 빨리" → 의미 있음
            return True
    return False


def find_repeats(sw):
    """말 더듬기·중복 감지. 반복된 앞 시도를 제거하고 마지막만 남긴다."""
    gap_max = CFG["REPEAT_GAP"]
    pad = CFG["FILLER_PAD"]
    toks = [norm(t) for (_, _, t) in sw]
    n = len(sw)
    removed = [False] * n
    def gap(i):
        return sw[i + 1][0] - sw[i][1]

    i = 0
    while i < n:                                   # A) 같은 단어 연속 반복
        j = i
        while j + 1 < n and toks[j + 1] and toks[j + 1] == toks[i] and gap(j) <= gap_max:
            j += 1
        if j > i:
            for k in range(i, j):
                removed[k] = True
        i = j + 1

    for i in range(n - 1):                          # B) 더듬 조각 "그"→"그게"
        if removed[i] or removed[i + 1]:
            continue
        a, b = toks[i], toks[i + 1]
        if a and b and a != b and len(a) <= 2 and b.startswith(a) and gap(i) <= gap_max:
            removed[i] = True

    for k in (3, 2):                                # C) 여러 단어 통째 반복
        i = 0
        while i + 2 * k <= n:
            if any(removed[i:i + 2 * k]):
                i += 1; continue
            s1, s2 = toks[i:i + k], toks[i + k:i + 2 * k]
            inner = sw[i + k][0] - sw[i + k - 1][1]
            if all(s1) and s1 == s2 and inner <= gap_max:
                for x in range(i, i + k):
                    removed[x] = True
                i += 2 * k
            else:
                i += 1

    # D) false-start: '비슷한 말 다시하기' (정확 일치 아님) → 유사도로 앞 시도 제거
    if CFG.get("FUZZY_REPEAT"):
        ratio_min = CFG.get("FUZZY_RATIO", 0.7)
        for k in (3, 2):
            i = 0
            while i + 2 * k <= n:
                if any(removed[i:i + 2 * k]):
                    i += 1; continue
                a = "".join(toks[i:i + k]); b = "".join(toks[i + k:i + 2 * k])
                inner = sw[i + k][0] - sw[i + k - 1][1]
                if a and b and a != b and len(a) >= 2 and inner <= gap_max \
                        and difflib.SequenceMatcher(None, a, b).ratio() >= ratio_min:
                    for x in range(i, i + k):
                        removed[x] = True
                    i += 2 * k
                else:
                    i += 1

    ranges, rep = [], []
    i = 0
    while i < n:
        if removed[i]:
            j = i
            while j + 1 < n and removed[j + 1]:
                j += 1
            s, e = sw[i][0], sw[j][1]
            ranges.append([max(0.0, s - pad), e + pad])
            rep.append((s, e, " ".join(sw[x][2] for x in range(i, j + 1))))
            i = j + 1
        else:
            i += 1
    return ranges, rep


def find_ng(sw):
    """NG(재촬영) 컷 제거. '다시 할게요/스톱/엔지' 같은 재촬영 신호어 뒤에
    같은 말을 다시 하면(=진짜 NG) 앞 실패 테이크 + 신호어를 통째로 제거.
    재시도가 없으면 건드리지 않아 오탐을 막는다(신호어가 본문 내용일 때 안전)."""
    markers = set(CFG.get("NG_MARKERS", []))
    if not markers:
        return [], []
    gap_max = CFG["REPEAT_GAP"]
    ratio_min = CFG.get("FUZZY_RATIO", 0.7)
    pad = CFG["FILLER_PAD"]
    toks = [norm(t) for (_, _, t) in sw]
    n = len(sw)
    removed = [False] * n

    def gap(i):
        return sw[i + 1][0] - sw[i][1]

    i = 0
    while i < n:
        if removed[i] or not toks[i]:
            i += 1; continue
        # 신호어(1~2어절 결합)가 여기서 시작하나?
        hit = 0
        for span in (2, 1):
            if i + span <= n and "".join(toks[i:i + span]) in markers:
                hit = span; break
        if not hit:
            i += 1; continue
        m_end = i + hit                             # 신호어 다음
        # 실패 테이크: 신호어 앞에서 큰 쉼(>gap_max) 전까지 거슬러 올라감
        b = i
        while b - 1 >= 0 and not removed[b - 1] and (sw[b][0] - sw[b - 1][1]) <= gap_max:
            b -= 1
        pre = "".join(toks[b:i])                    # 실패 테이크 텍스트
        # 재시도: 신호어 뒤 같은 길이 근방
        post = "".join(toks[m_end:m_end + max(2, (i - b) + 2)])
        if pre and post and len(pre) >= 2 and (m_end >= n or gap(m_end - 1) <= gap_max + 0.6):
            r = difflib.SequenceMatcher(None, pre, post[:len(pre) + 4]).ratio()
            if r >= ratio_min:                     # 진짜 재촬영 → 앞 테이크+신호어 제거
                for x in range(b, m_end):
                    removed[x] = True
                i = m_end; continue
        i = m_end

    ranges, log = [], []
    i = 0
    while i < n:
        if removed[i]:
            j = i
            while j + 1 < n and removed[j + 1]:
                j += 1
            ranges.append([max(0.0, sw[i][0] - pad), sw[j][1] + pad])
            log.append((sw[i][0], sw[j][1], "NG: " + " ".join(sw[x][2] for x in range(i, j + 1))))
            i = j + 1
        else:
            i += 1
    return ranges, log


def word_snap(keeps, sw, pad=0.05, total=None):
    """컷 경계가 전사된 단어를 관통하면 keep을 단어 경계까지 확장(어두/어미 잘림 방지).
    단어의 중심이 keep 안에 있을 때만 확장 → 의도적으로 지운 단어는 되살리지 않는다."""
    if not keeps or not sw:
        return keeps, 0
    words = sorted((s, e) for s, e, _t in sw)
    starts = [w[0] for w in words]
    fixed = 0
    out = []
    for a, b in keeps:
        # 경계가 단어 내부를 '엄격히' 관통할 때만 스냅.
        # (의도적 제거는 경계를 단어 밖 ±pad에 두므로 여기 안 걸림 —
        #  걸렸다면 이미 단어 일부가 keep에 들려 있는 상태라 온전히 살리는 게 낫다)
        i = bisect.bisect_right(starts, a) - 1
        if i >= 0:
            ws, we = words[i]
            if ws < a < we:                    # 앞 경계가 단어 머리를 자름
                a = max(0.0, ws - pad); fixed += 1
        j = bisect.bisect_right(starts, b) - 1
        if j >= 0:
            ws, we = words[j]
            if ws < b < we:                    # 뒤 경계가 단어 꼬리를 자름
                b = we + pad; fixed += 1
        if total is not None:
            b = min(b, total)
        if b > a:
            out.append([a, b])
    # 확장으로 생긴 겹침 병합
    out.sort()
    merged = []
    for a, b in out:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged, fixed


def measure_noise_floor(video, sil_keeps, total):
    """가장 긴 무음 구간의 평균 볼륨(dBFS) = 노이즈 플로어. 측정 불가면 None."""
    gaps, prev = [], 0.0
    for a, b in sil_keeps:
        if a > prev:
            gaps.append((prev, a))
        prev = b
    if prev < total:
        gaps.append((prev, total))
    gaps = [g for g in gaps if g[1] - g[0] >= 0.4]
    if not gaps:
        return None
    s, e = max(gaps, key=lambda g: g[1] - g[0])
    p = subprocess.run(
        [SC.FFMPEG, "-hide_banner", "-ss", f"{s:.2f}", "-t", f"{min(e - s, 3.0):.2f}",
         "-i", video, "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True)
    m = re.search(r"mean_volume:\s*(-?[0-9.]+)", p.stderr or "")
    return float(m.group(1)) if m else None


def merge_ranges(ranges):
    ranges = sorted(ranges)
    out = []
    for a, b in ranges:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def subtract(keeps, removes):
    """keeps 구간에서 removes 구간을 빼서 잘게 쪼갠다."""
    removes = merge_ranges(removes)
    min_keep = CFG["MIN_KEEP"]
    out = []
    for a, b in keeps:
        segs = [(a, b)]
        for c, d in removes:
            new = []
            for s, e in segs:
                if d <= s or c >= e:
                    new.append((s, e))
                else:
                    if c > s:
                        new.append((s, min(c, e)))
                    if d < e:
                        new.append((max(d, s), e))
            segs = new
        out.extend(segs)
    return [(s, e) for s, e in out if e - s >= min_keep]


def complement(keeps, total):
    """keeps의 여집합 = 제거된 구간(버린 컷용)."""
    keeps = sorted(keeps)
    out = []
    cur = 0.0
    for a, b in keeps:
        if a > cur:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < total:
        out.append((cur, total))
    return [(a, b) for a, b in out if b - a > 0.02]


def backup_outputs(outdir, base):
    """덮어쓰기 전 이전 결과(가벼운 것만)를 _backup/타임스탬프/ 로 보관. WAV는 제외(용량)."""
    import datetime, shutil
    names = [base + s for s in ["_cut.xml", "_cut.srt", "_cut_report.txt",
                                "_words.json", "_rejected.xml"]]
    existing = [n for n in names if os.path.exists(os.path.join(outdir, n))]
    if not existing:
        return None
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bdir = os.path.join(outdir, "_backup", ts)
    os.makedirs(bdir, exist_ok=True)
    for n in existing:
        shutil.copy2(os.path.join(outdir, n), os.path.join(bdir, n))
    return bdir


def verify_keeps(keeps):
    """컷 구간의 길이오류/겹침을 검사. 정상이면 빈 리스트."""
    issues = []
    prev = None
    for idx, (a, b) in enumerate(keeps):
        if b <= a:
            issues.append(f"길이오류 #{idx}: {a:.3f}~{b:.3f}")
        if prev is not None and a < prev - 1e-6:
            issues.append(f"겹침 #{idx}: 이전끝 {prev:.3f} > 시작 {a:.3f}")
        prev = b
    return issues


def find_choppy(keeps, window, max_cuts):
    """컷이 너무 촘촘해 부자연스러울 수 있는 구간을 '출력(컷 후) 타임라인' 기준으로 찾는다.
       자연스러움 > 최대 제거 원칙을 위한 가드. 반환: [(시작, 끝, 컷수), ...]"""
    cps, acc = [], 0.0
    for a, b in keeps[:-1]:
        acc += b - a
        cps.append(acc)          # 출력 타임라인상 컷(점프)이 일어나는 지점
    flagged, i, n = [], 0, len(cps)
    while i < n:
        j = i
        while j < n and cps[j] - cps[i] <= window:
            j += 1
        if j - i >= max_cuts:
            flagged.append([cps[i], cps[j - 1], j - i])
            i = j
        else:
            i += 1
    merged = []
    for s, e, c in flagged:
        if merged and s - merged[-1][1] <= window:
            merged[-1][1] = e; merged[-1][2] += c
        else:
            merged.append([s, e, c])
    return merged


def get_transcript(video, audio_src, cache):
    if os.path.exists(cache):
        print("> 받아쓰기 캐시 사용 (재전사 생략)")
        return [tuple(w) for w in json.load(open(cache, encoding="utf-8"))]
    words = transcribe(audio_src, model=CFG["STT_MODEL"],
                       initial_prompt=CFG["VERBATIM_PROMPT"], condition=True)
    json.dump(words, open(cache, "w", encoding="utf-8"), ensure_ascii=False)
    return words


def apply_config_to_modules():
    """무음/음량 파라미터를 silence_cut 모듈에 주입."""
    SC.NOISE_DB = CFG["NOISE_DB"]
    SC.MIN_SILENCE = CFG["MIN_SILENCE"]
    SC.PAD_LEAD = CFG["PAD_LEAD"]
    SC.PAD_TAIL = CFG["PAD_TAIL"]
    SC.MIN_KEEP = CFG["MIN_KEEP"]
    SC.TARGET_LUFS = CFG["TARGET_LUFS"]
    SC.TARGET_PEAK_DB = CFG["TARGET_PEAK_DB"]


def main():
    global CFG
    args = [a for a in sys.argv[1:]]
    preset = "표준"
    audio_style = None
    if "--preset" in args:
        i = args.index("--preset")
        preset = args[i + 1]
        del args[i:i + 2]
    if "--audio" in args:                       # natural | podcast
        i = args.index("--audio")
        audio_style = args[i + 1]
        del args[i:i + 2]
    if not args:
        print("사용: python3 auto_cut.py \"<원본영상>\" [--preset 보수|표준|공격] [--audio natural|podcast]"); sys.exit(1)
    video = args[0]
    if not os.path.exists(video):
        print("파일 없음:", video); sys.exit(1)

    proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    CFG = CONFIG.load(preset, project_dir=proj)
    if audio_style:
        CFG["AUDIO_STYLE"] = audio_style
    apply_config_to_modules()

    base = os.path.splitext(os.path.basename(video))[0]
    outdir = os.path.join(proj, "output")
    os.makedirs(outdir, exist_ok=True)
    xml_out = os.path.join(outdir, base + "_cut.xml")
    srt_out = os.path.join(outdir, base + "_cut.srt")
    wav_out = os.path.join(outdir, base + "_cut_audio.wav")
    rej_out = os.path.join(outdir, base + "_rejected.xml")
    words_cache = os.path.join(outdir, base + "_words.json")

    if CFG.get("BACKUP_OUTPUTS"):
        bdir = backup_outputs(outdir, base)
        if bdir:
            print(f"> 이전 결과 백업 → _backup/{os.path.basename(bdir)}/")

    src = "config.json" if CFG.get("_config_json") else f"프리셋:{CFG['_preset']}"
    print(f"> 미디어 분석 중...  (설정 {src})")
    info = probe_media(video)
    print(f"   길이 {fmt(info['duration'])} · {info['width']}x{info['height']} · {info['fps']}fps")

    print("> 무음 감지 중...")
    sil_keeps = keep_ranges_from_silence(detect_silence(video), info["duration"])
    kept_sil = sum(b - a for a, b in sil_keeps)

    print("> 음량 분석 + 음성보정 중...")
    loud = measure_loudness(video)
    # 음성보정: 무음(노이즈플로어) 측정 → 시끄러우면 자동 노이즈 제거(깨끗하면 건너뜀)
    denoise = CFG.get("DENOISE", False)
    nfloor = None
    if not denoise and CFG.get("AUTO_DENOISE"):
        nfloor = measure_noise_floor(video, sil_keeps, info["duration"])
        if nfloor is not None and nfloor > CFG.get("NOISE_FLOOR_DB", -50.0):
            denoise = True
    pre = []
    if denoise:
        pre.append(f"afftdn=nr={CFG.get('DENOISE_STRENGTH', 10)}")
    if CFG.get("DEESS"):
        pre.append("deesser")
    pre_f = (",".join(pre) + ",") if pre else ""
    lufs = CFG.get("TARGET_LUFS", -14.0)
    nf_note = f" · 노이즈플로어 {nfloor:.0f}dB" if nfloor is not None else ""
    style = CFG.get("AUDIO_STYLE", "natural")
    if style == "podcast":
        # 팟캐스트 톤: 따뜻함(저역 셸프)+박스니스 컷+프레즌스+공기감+강한 압축+타이트 라우드니스
        chain = (f"highpass=f=80,{pre_f}"
                 "equalizer=f=110:t=h:w=0.7:g=2,"
                 "equalizer=f=330:t=q:w=1.2:g=-2,"
                 "equalizer=f=4200:t=q:w=1.4:g=2.5,"
                 "equalizer=f=11000:t=h:w=0.7:g=1.5,"
                 "acompressor=threshold=-24dB:ratio=3.5:attack=6:release=160:makeup=4,"
                 f"loudnorm=I={lufs}:TP=-1.5:LRA=8")
        print(f"   음성보정: 팟캐스트 톤(따뜻+프레즌스+공기감+압축){nf_note}"
              + (" · afftdn" if denoise else "") + (" · deesser" if CFG.get('DEESS') else ""))
        ok = make_clean_audio(video, wav_out, info, chain=chain)
    else:
        if pre:
            print(f"   음성보정: {', '.join(pre)}{nf_note}")
        elif nfloor is not None:
            print(f"   음성보정: 노이즈플로어 {nfloor:.0f}dB → 깨끗, 노이즈제거 생략")
        ok = make_clean_audio(video, wav_out, info, extra_filters=pre_f)
    clean_audio = wav_out if ok else None
    if clean_audio:
        after = measure_loudness(wav_out)
        print(f"   음량 {loud['I']}→{after['I']} LUFS / 피크 {loud['TP']}→{after['TP']} dB")
    audio_for_stt = clean_audio or video

    words = get_transcript(video, audio_for_stt, words_cache)
    print(f"   받아쓴 단어 {len(words)}개")

    # ── 제거 구간 계산 ──
    keeps = sil_keeps
    removes = []
    report = {"추임새": [], "망설임": [], "더듬/중복": [], "NG": [], "숨소리": []}
    sw = sorted(words, key=lambda w: w[0])
    fpad = CFG["FILLER_PAD"]

    n_kept_ctx = 0
    if CFG["REMOVE_FILLERS"]:
        for i, (s, e, t) in enumerate(sw):
            if is_filler(t):
                if keep_filler_in_context(i, sw):   # '좀 더' 등 의미 있는 건 살림
                    n_kept_ctx += 1
                    continue
                removes.append([max(0.0, s - fpad), e + fpad])
                report["추임새"].append((s, e, t))

    # 내용 컷(에이전트/사용자 지정) — output/<base>_content_cuts.json 이 있으면 그 구간을 통째 제거
    # 형식: [[start초, end초, "이유(선택)"], ...]  (원본 타임라인 기준)
    cc_path = os.path.join(outdir, base + "_content_cuts.json")
    if os.path.exists(cc_path):
        try:
            ccs = json.load(open(cc_path, encoding="utf-8"))
            for item in ccs:
                s, e = float(item[0]), float(item[1])
                why = item[2] if len(item) > 2 else "(내용 컷)"
                if e > s:
                    removes.append([max(0.0, s), e])
                    report.setdefault("내용컷", []).append((s, e, why))
            print(f"   내용 컷 {len(ccs)}곳 반영 ({cc_path})")
        except Exception as ex:
            print(f"   [주의] 내용 컷 파일 파싱 실패: {ex}")

    if CFG["REMOVE_HESITATION"]:
        hmin, hpad = CFG["HESITATION_MIN"], CFG["HESITATION_PAD"]
        for i in range(len(sw) - 1):
            g0, g1 = sw[i][1], sw[i + 1][0]
            if g1 - g0 < hmin:
                continue
            for a, b in sil_keeps:
                lo, hi = max(g0, a) + hpad, min(g1, b) - hpad
                if hi - lo >= hmin:
                    removes.append([lo, hi])
                    report["망설임"].append((lo, hi, "(어/음 등)"))

    if CFG["REMOVE_REPEATS"]:
        rep_ranges, rep_log = find_repeats(sw)
        removes += rep_ranges
        report["더듬/중복"] = rep_log

    if CFG.get("REMOVE_NG"):
        ng_ranges, ng_log = find_ng(sw)
        removes += ng_ranges
        report["NG"] = ng_log

    if CFG.get("ACOUSTIC_FILLER") and clean_audio:
        try:
            import acoustic_filler as AF
            AF.MIN_DUR = CFG.get("ACOUSTIC_MIN_DUR", 0.20)   # 프리셋으로 민감도 조절
            print("> 어/음 음향 검출 중...")
            r_, f_, v_ = AF.analyze(AF.load_audio(clean_audio))
            checked = AF.cross_check(AF.detect(r_, f_, v_), words)
            wstarts = sorted(x[0] for x in sw)
            follow_max = CFG.get("ACOUSTIC_FOLLOW_MAX", 1.0)
            n_ac = n_tail = 0
            for s, e, d, conf, txt in checked:
                if conf != "높음(빈구간)":     # 글자 없는 지속음만 = 가장 안전
                    continue
                # 끝음 보호: 어/음 뒤에 말이 곧 이어지면 컷(=말 중간), 한참 침묵이면 보존(=문장 끝 꼬리)
                idx = bisect.bisect_left(wstarts, e)
                nxt = wstarts[idx] if idx < len(wstarts) else e + 99
                if nxt - e <= follow_max:
                    removes.append([s, e])
                    report["망설임"].append((s, e, "(음향 어/음)"))
                    n_ac += 1
                else:
                    n_tail += 1
            print(f"   음향 어/음 {n_ac}개 추가" + (f" (끝음 꼬리 {n_tail}개 보존)" if n_tail else ""))
        except Exception as ex:
            print(f"   [주의] 음향 검출 건너뜀: {ex}")

    if CFG.get("BREATH_REDUCE") and clean_audio:
        try:
            import breath_reduce as BR
            print("> 숨소리 축소 중...")
            aud, asr = BR.load_audio(clean_audio)
            br = BR.detect_breaths(
                aud, asr, words, sil_keeps,
                min_dur=CFG.get("BREATH_MIN_DUR", 0.15),
                rel_lo=CFG.get("BREATH_REL_LO", -40.0),
                rel_hi=CFG.get("BREATH_REL_HI", -12.0),
                flatness=CFG.get("BREATH_FLATNESS", 0.35),
                frac=CFG.get("BREATH_FRAC", 0.40),
                keep=CFG.get("BREATH_KEEP", 0.12),
                pad=CFG.get("BREATH_PAD", 0.04))
            cut = sum(b - a for a, b in br)
            removes += [list(r) for r in br]
            report["숨소리"] = [(a, b, "(숨소리)") for a, b in br]
            print(f"   숨소리 {len(br)}곳 축소 (약 {fmt(cut)})")
        except Exception as ex:
            print(f"   [주의] 숨소리 축소 건너뜀: {ex}")

    if removes:
        keeps = subtract(sil_keeps, removes)
        kept_now = sum(b - a for a, b in keeps)
        nf, nh, nr, nng, nb = (len(report["추임새"]), len(report["망설임"]),
                               len(report["더듬/중복"]), len(report["NG"]), len(report["숨소리"]))
        ncc = len(report.get("내용컷", []))
        ctx = f" (문맥상 '좀' {n_kept_ctx}개 살림)" if n_kept_ctx else ""
        parts = []
        if nf or nh: parts.append(f"추임새 {nf} + 망설임 {nh}")
        if nr: parts.append(f"더듬/중복 {nr}")
        if nng: parts.append(f"NG {nng}")
        if ncc: parts.append(f"내용컷 {ncc}")
        if nb: parts.append(f"숨소리 {nb}")
        print(f"   {' + '.join(parts) or '제거 없음'} 제거{ctx} "
              f"→ 추가로 {fmt(kept_sil - kept_now)} 단축")
        rep_out = os.path.join(outdir, base + "_cut_report.txt")
        with open(rep_out, "w", encoding="utf-8") as f:
            for cat in ("추임새", "망설임", "더듬/중복", "NG", "내용컷", "숨소리"):
                if cat == "내용컷" and "내용컷" not in report:
                    continue
                f.write(f"━━━ {cat} ({len(report[cat])}개) ━━━\n")
                for s, e, t in report[cat]:
                    f.write(f"  {srt_time(s)}  {t}\n")
                f.write("\n")

    # ── 단어 경계 스냅 가드: 컷 경계가 단어를 관통하면 단어 경계까지 확장(어두/어미 보호) ──
    if CFG.get("WORD_SNAP", True) and sw:
        keeps, n_snap = word_snap(keeps, sw, pad=CFG.get("WORD_SNAP_PAD", 0.05),
                                  total=info["duration"])
        if n_snap:
            print(f"   단어 경계 보호: 컷 경계 {n_snap}곳을 단어 경계로 스냅")

    kept = sum(b - a for a, b in keeps)
    removed = info["duration"] - kept
    print(f"\n   총 제거: {fmt(removed)} ({removed/info['duration']*100:.1f}%)  "
          f"| 컷 {len(keeps)}개 | 최종 {fmt(kept)}")

    # ── XML 생성 ──
    print("> 프리미어 시퀀스(XML) 생성 중...")
    gain = compute_gain_db(loud)
    xml, seq_dur = build_fcp7_xml(video, info, keeps, gain, base + " [러프컷]",
                                  clean_audio=clean_audio,
                                  fade_frames=CFG.get("AUDIO_FADE_FRAMES", 0))
    open(xml_out, "w", encoding="utf-8").write(xml)

    # ── 프레임 무결성 검증 ──
    issues = verify_keeps(keeps)
    if issues:
        print(f"   [주의] 검증: 문제 {len(issues)}건 발견")
    else:
        print(f"   검증: 갭/겹침/길이오류 0 (컷 {len(keeps)}개)")

    # ── 자연스러움 가드: 컷이 촘촘한 구간 경고 (잘라낸 뒤 부자연스러움 방지) ──
    choppy = find_choppy(keeps, CFG["CHOPPY_WINDOW"], CFG["CHOPPY_MAX"])
    if choppy:
        print(f"   [주의] 자연스러움 주의: 컷이 촘촘한 구간 {len(choppy)}곳 → 리포트에서 확인 권장")
    rep_path = os.path.join(outdir, base + "_cut_report.txt")
    with open(rep_path, "a", encoding="utf-8") as f:
        f.write(f"━━━ 검증 ━━━\n")
        f.write("  프레임: " + ("정상(갭/겹침 0)\n" if not issues else "; ".join(issues) + "\n"))
        f.write(f"━━━ 자연스러움 주의 (컷 촘촘 = 부자연 위험, {len(choppy)}곳) ━━━\n")
        for s, e, c in choppy:
            f.write(f"  {srt_time(s)}~{srt_time(e)}  {c}컷 (확인 권장)\n")
        f.write("\n")

    # ── 버린 컷 시퀀스 (잘려나간 구간만) ──
    rej_made = False
    if CFG.get("MAKE_REJECTED"):
        rej = complement(keeps, info["duration"])
        if rej:
            rej_xml, _ = build_fcp7_xml(video, info, rej, gain, base + " [버린컷]",
                                        clean_audio=clean_audio)
            open(rej_out, "w", encoding="utf-8").write(rej_xml)
            rej_made = True

    # ── 자막 생성 ──
    print("> 자막(SRT) 생성 중...")
    mapper = build_mapper(keeps)
    sub_words = [w for w in words if not is_filler(w[2])]
    lines = regroup(sub_words, mapper)
    write_srt(lines, srt_out)

    # 자막 마감(한 줄 30자) + .vtt/.ass(비블 스타일) 한 번에
    sub_extra = ""
    if CFG.get("POLISH_SUBTITLES"):
        try:
            import subtitle_polish as SP
            SP.FILL_GAPS = CFG.get("SUBTITLE_FILL_GAPS", True)   # 자막 빈칸 제거 여부
            cues = SP.polish(SP.parse_srt(srt_out))
            stem = os.path.splitext(srt_out)[0]
            SP.write_srt(cues, srt_out)
            SP.write_vtt(cues, stem + ".vtt")
            SP.write_ass(cues, stem + ".ass")
            lines = cues
            sub_extra = " (+ .vtt / .ass)"
        except Exception as ex:
            print(f"   [주의] 자막 마감 건너뜀: {ex}")

    print(f"\n완료  (설정 {src})")
    print(f"   시퀀스 : {os.path.basename(xml_out)}")
    print(f"   오디오 : {os.path.basename(wav_out)}")
    print(f"   자막   : {os.path.basename(srt_out)}{sub_extra}  ({len(lines)}줄)")
    if rej_made:
        print(f"   버린컷 : {os.path.basename(rej_out)}  (잘린 부분 검토용)")
    if CFG.get("HTML_REPORT"):
        try:
            import html_report
            summary = {"duration": info["duration"], "kept": kept, "removed": removed,
                       "pct": removed / info["duration"] * 100, "cuts": len(keeps),
                       "preset": CFG["_preset"]}
            hp = html_report.generate(os.path.join(outdir, base + "_report.html"),
                                      base, summary, choppy, report)
            print(f"   리포트 : {os.path.basename(hp)}  (브라우저로 열어 검토)")
        except Exception as ex:
            print(f"   [주의] HTML 리포트 건너뜀: {ex}")
    if CFG.get("CAPCUT_MP4"):
        try:
            import capcut_render
            cc_out = os.path.join(outdir, base + "_capcut.mp4")
            capcut_render.render(video, xml_out, wav_out, cc_out)
            print(f"   캡컷   : {os.path.basename(cc_out)}")
            print(f"\n   캡컷 > 미디어 가져오기로 _capcut.mp4 → 텍스트 > 로컬 자막 가져오기로 .srt")
            return
        except Exception as ex:
            print(f"   [주의] 캡컷 MP4 렌더 실패 — XML은 정상: {ex}")
    print(f"\n   프리미어 > 파일 > 가져오기 로 .xml 불러오세요.")
    if len(keeps) > 1:
        sc = "Ctrl+Shift+D" if os.name == "nt" else "Cmd+Shift+D"
        print(f"   자연스러움 팁: 타임라인 전체 선택 → {sc} 하면")
        print(f"      모든 컷에 기본 오디오 전환이 적용돼 클릭음 없이 부드러워집니다.")
        rep_path2 = os.path.join(outdir, base + "_cut_report.txt")
        with open(rep_path2, "a", encoding="utf-8") as f:
            f.write("━━━ 다듬기 팁 ━━━\n")
            f.write(f"  컷 부드럽게: 프리미어 타임라인 전체 선택 → {sc} (모든 컷에 기본 오디오 전환)\n")
            f.write("  자연스러움 주의 구간은 위 목록 참고 — 너무 촘촘하면 일부 컷 되돌려 호흡 살리기\n\n")


if __name__ == "__main__":
    main()
