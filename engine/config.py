#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.py — 모든 편집 파라미터 + 공격성 프리셋 한 곳에서 관리.

우선순위:  내장 기본값(표준)  <  프리셋(보수/표준/공격)  <  config.json(사용자)
사용:
  python3 auto_cut.py "영상.mp4" --preset 공격
  또는 프로젝트 루트에 config.json 두면 자동 적용.
"""

import json, os

# ── 표준(기본) 설정 — 사용자가 검증한 값 ──
DEFAULTS = {
    # 무음
    "NOISE_DB": -40.0,       # 이보다 조용하면 무음. 낮출수록 작은 끝음/작은 소리를 살림(민감도↓)
    "MIN_SILENCE": 0.5,      # 이 길이(초) 이상 조용해야 컷
    "PAD": 0.10,             # 말 앞뒤 여유(초)
    "MIN_KEEP": 0.20,        # 이보다 짧은 토막은 버림(초)

    # 음량
    "TARGET_LUFS": -14.0,
    "TARGET_PEAK_DB": -6.0,

    # 오디오 후처리 (기본 OFF — 깨끗한 녹음엔 불필요. 노이즈 많으면 켜기)
    "DENOISE": False,        # afftdn FFT 노이즈 제거 (배경 험·에어컨)
    "DEESS": False,          # deesser 치찰음(ㅅ,ㅊ) 완화

    # 추임새
    "REMOVE_FILLERS": True,
    "FILLER_SOUND_CHARS": "아어엄음으에",
    "FILLER_PHRASES": ["그러니까", "그니까", "그러니깐", "그니깐", "그까",
                       "뭐", "뭔가", "막", "약간", "좀"],
    "FILLER_PAD": 0.03,

    # 망설임 빈틈(어/음)
    "REMOVE_HESITATION": True,
    "HESITATION_MIN": 0.35,
    "HESITATION_PAD": 0.06,

    # 어/음 음향 검출 — Whisper가 글자로 안 적는 망설임 소리를 평탄피치로 잡아 컷.
    # 기본 ON(표준/공격). 안전한 '빈구간'(글자 없는 지속음)만 컷.
    "ACOUSTIC_FILLER": True,
    "ACOUSTIC_MIN_DUR": 0.20,   # 음향 어/음 최소 길이(초). 낮출수록 많이 잡음(과하면 0.25~0.3로)
    # 어/음 뒤 이 시간(초) 안에 말이 이어지면 컷(=말 중간 어/음), 한참 침묵이면 보존(=문장 끝 꼬리)
    "ACOUSTIC_FOLLOW_MAX": 1.0,

    # 말더듬·중복
    "REMOVE_REPEATS": True,
    "REPEAT_GAP": 0.8,
    "FUZZY_REPEAT": True,    # 똑같은 말뿐 아니라 '비슷한 말 다시하기'(false-start)도 검출
    "FUZZY_RATIO": 0.7,      # 두 구절 유사도가 이 이상이면 앞 시도 제거

    # 문맥 기반 필러 — '좀'이 '조금'의 뜻(좀 더/좀 많이)이면 살림(과제거 방지)
    "CONTEXT_FILLER": True,

    # 받아쓰기
    "STT_MODEL": "mlx-community/whisper-large-v3-turbo",
    "VERBATIM_PROMPT": "음... 어... 그러니까, 아 그게, 좀, 뭐, 약간, 막, 그래서, 어어, 음음, 이제, 뭔가. 네, 자.",

    # 컷 다듬기
    "CROSSFADE_FRAMES": 0,   # 컷마다 오디오 크로스페이드 프레임 수 (0=끔)

    # 안전망
    "MAKE_REJECTED": False,  # 잘려나간 구간만 모은 '버린 컷' 시퀀스 생성 여부 (기본 끔)
    "BACKUP_OUTPUTS": True,  # 덮어쓰기 전 이전 결과(xml/srt/report/words)를 _backup/에 보관
    "HTML_REPORT": True,     # 비블 다크 톤 시각 리포트(클릭 타임코드) HTML 생성
    "POLISH_SUBTITLES": True, # 자막을 한 줄 30자로 마감 + .vtt/.ass(비블 스타일) 까지 한 번에 생성

    # 자연스러움 가드 — 컷이 너무 촘촘하면 부자연스러움. 그런 구간을 찾아 경고.
    "CHOPPY_WINDOW": 8.0,    # 이 길이(초) 창 안에
    "CHOPPY_MAX": 6,         # 컷이 이 개수 이상이면 'choppy(부자연)' 주의 (실측: 중앙3/최대7 → 상위 1~2%만 잡음)
}

# ── 프리셋: 표준 대비 바뀌는 값만 ──
PRESETS = {
    "보수": {   # 덜 자름 — 자연스러움 우선
        "MIN_SILENCE": 0.7,
        "PAD": 0.15,
        "HESITATION_MIN": 0.5,
        "REPEAT_GAP": 0.4,
        "ACOUSTIC_FILLER": False,   # 보존 우선 — 음향 어/음 컷 안 함
        "FILLER_PHRASES": ["그러니까", "그니까", "그러니깐", "그니깐"],
    },
    "표준": {},  # DEFAULTS 그대로
    "공격": {   # 최대한 타이트
        "MIN_SILENCE": 0.4,
        "PAD": 0.08,
        "HESITATION_MIN": 0.28,
        "REPEAT_GAP": 1.0,
        "ACOUSTIC_FILLER": True,
        "FILLER_PHRASES": ["그러니까", "그니까", "그러니깐", "그니깐", "그까",
                           "뭐", "뭔가", "막", "약간", "좀",
                           "그래서", "이제", "그냥", "근데"],
    },
}


def load(preset="표준", project_dir=None):
    cfg = dict(DEFAULTS)
    cfg.update(PRESETS.get(preset, {}))
    cfg["_preset"] = preset if preset in PRESETS else "표준"

    # config.json 사용자 override
    if project_dir:
        p = os.path.join(project_dir, "config.json")
        if os.path.exists(p):
            try:
                user = json.load(open(p, encoding="utf-8"))
                user = {k: v for k, v in user.items() if not k.startswith("_")}
                cfg.update(user)
                cfg["_config_json"] = True
            except Exception as e:
                print(f"   [주의] config.json 읽기 실패({e}) — 무시")
    return cfg
