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

import sys, os, json, difflib, bisect
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
    SC.PAD = CFG["PAD"]
    SC.MIN_KEEP = CFG["MIN_KEEP"]
    SC.TARGET_LUFS = CFG["TARGET_LUFS"]
    SC.TARGET_PEAK_DB = CFG["TARGET_PEAK_DB"]


def main():
    global CFG
    args = [a for a in sys.argv[1:]]
    preset = "표준"
    if "--preset" in args:
        i = args.index("--preset")
        preset = args[i + 1]
        del args[i:i + 2]
    if not args:
        print("사용: python3 auto_cut.py \"<원본영상>\" [--preset 보수|표준|공격]"); sys.exit(1)
    video = args[0]
    if not os.path.exists(video):
        print("파일 없음:", video); sys.exit(1)

    proj = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    CFG = CONFIG.load(preset, project_dir=proj)
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

    print("> 음량 분석 + 오디오 정리 중...")
    loud = measure_loudness(video)
    extra = []
    if CFG.get("DENOISE"):
        extra.append("afftdn")
    if CFG.get("DEESS"):
        extra.append("deesser")
    extra_filters = (",".join(extra) + ",") if extra else ""
    if extra:
        print(f"   오디오 후처리: {', '.join(extra)}")
    ok = make_clean_audio(video, wav_out, info, extra_filters=extra_filters)
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
    report = {"추임새": [], "망설임": [], "더듬/중복": []}
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

    if removes:
        keeps = subtract(sil_keeps, removes)
        kept_now = sum(b - a for a, b in keeps)
        nf, nh, nr = len(report["추임새"]), len(report["망설임"]), len(report["더듬/중복"])
        ctx = f" (문맥상 '좀' {n_kept_ctx}개 살림)" if n_kept_ctx else ""
        print(f"   추임새 {nf} + 망설임 {nh} + 더듬/중복 {nr} 제거{ctx} "
              f"→ 추가로 {fmt(kept_sil - kept_now)} 단축")
        rep_out = os.path.join(outdir, base + "_cut_report.txt")
        with open(rep_out, "w", encoding="utf-8") as f:
            for cat in ("추임새", "망설임", "더듬/중복"):
                f.write(f"━━━ {cat} ({len(report[cat])}개) ━━━\n")
                for s, e, t in report[cat]:
                    f.write(f"  {srt_time(s)}  {t}\n")
                f.write("\n")

    kept = sum(b - a for a, b in keeps)
    removed = info["duration"] - kept
    print(f"\n   총 제거: {fmt(removed)} ({removed/info['duration']*100:.1f}%)  "
          f"| 컷 {len(keeps)}개 | 최종 {fmt(kept)}")

    # ── XML 생성 ──
    print("> 프리미어 시퀀스(XML) 생성 중...")
    gain = compute_gain_db(loud)
    xml, seq_dur = build_fcp7_xml(video, info, keeps, gain, base + " [러프컷]",
                                  clean_audio=clean_audio)
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
    print(f"\n   프리미어 > 파일 > 가져오기 로 .xml 불러오세요.")
    if len(keeps) > 1:
        print(f"   자연스러움 팁: 타임라인 전체 선택 → Cmd+Shift+D 하면")
        print(f"      모든 컷에 기본 오디오 전환이 적용돼 클릭음 없이 부드러워집니다.")
        rep_path2 = os.path.join(outdir, base + "_cut_report.txt")
        with open(rep_path2, "a", encoding="utf-8") as f:
            f.write("━━━ 다듬기 팁 ━━━\n")
            f.write("  컷 부드럽게: 프리미어 타임라인 전체 선택 → Cmd+Shift+D (모든 컷에 기본 오디오 전환)\n")
            f.write("  자연스러움 주의 구간은 위 목록 참고 — 너무 촘촘하면 일부 컷 되돌려 호흡 살리기\n\n")


if __name__ == "__main__":
    main()
