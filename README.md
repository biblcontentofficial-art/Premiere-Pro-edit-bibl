# Premiere Auto-Edit

**말하는 영상(롱폼·토크·라이브)을 한 줄 명령으로 러프컷까지.**
무음·추임새(아·어·음·그러니까)·말더듬을 제거하고, 음량을 유튜브 표준(-14 LUFS)으로 맞추고, 컷에 정렬된 자막까지 만들어 **프리미어에서 바로 편집 가능한 시퀀스**로 내보냅니다. 전부 맥에서 **로컬 실행**(외부 업로드 없음).

> **TL;DR (EN):** One command turns a raw talking-head recording into an editable Premiere sequence — silence/filler/stutter removal + loudness leveling + cut-aligned subtitles, all running locally on Apple Silicon. Korean-speech tuned (Whisper).

```bash
./edit.sh "원본영상.mp4" --preset 표준
# → output/ 에 한 번에: _cut.xml (시퀀스) · _cut_audio.wav · 자막 _cut.srt/.vtt/.ass
# → 프리미어에서 파일 > 가져오기 로 .xml + 자막 불러오면 끝
```

---

## 왜?

토크형 롱폼 편집에서 시간을 가장 많이 잡아먹는 건 **무음 제거·추임새 제거·음량 맞추기·자막**입니다. 이 반복 노가다를 자동화해서, **편집자는 B롤·강조·디테일에만 집중**하게 합니다. 원본은 건드리지 않고(비파괴), 결과는 평소처럼 **자유롭게 수정 가능한 일반 시퀀스**로 들어갑니다.

### 실측 (73분 라이브 토크 기준)

| 항목 | 결과 |
|------|------|
| 원본 → 컷 | 73:50 → **65:24** (약 11~14% 제거, 프리셋에 따라) |
| 제거 대상 | 무음 · 추임새 · 어/음 망설임 · 말더듬/중복 · false-start |
| 음량 | -21.6 LUFS → **-14.0 LUFS** (유튜브 표준), 트루피크 정리 |
| 자막 | 단어 단위 전사 → 컷 타임라인 정렬 SRT/VTT/ASS |

---

## 기능

- **무음 제거** — 끝음(작게 흐리는 문장 끝)까지 살리는 민감도 조절
- **추임새 제거** — `아·어·음·그러니까·뭐·뭔가·막·약간·좀` (문맥상 '좀 더'는 살림)
- **어/음 음향 검출** — Whisper가 글자로 안 적는 망설임 소리를 피치 평탄성으로 검출
- **말더듬·중복 제거** — 같은 말 반복 + **비슷한 말 다시하기(false-start)** 유사도 검출
- **음량 정리** — 압축 + -14 LUFS 노멀라이즈 (+ 옵션: 노이즈/치찰음 제거)
- **자막** — 한 줄 30자 맥락 분할, SRT/VTT/ASS(폰트·외곽선·위치 스타일)
- **자연스러움 가드** — 컷이 너무 촘촘한 구간을 자동 경고 (*"자연스러움 > 최대 제거"*)
- **안전망** — 출력 백업, 프레임 무결성 검증, 시각 HTML 리포트
- **프리셋** — `보수 / 표준 / 공격` (코드 수정 없이 `config.json`으로 세부 조절)
- **AI 편집 에이전트 팀** (Claude Code) — 기획·리서치·컷·자막·검수를 자동 협업

---

## 요구사항

| | |
|---|---|
| OS | **macOS (Apple Silicon)** — mlx-whisper가 애플 실리콘 전용 |
| 영상 편집 | **Adobe Premiere Pro 25.0+** (FCP7 XML 가져오기) |
| 런타임 | **Python 3.10+**, **ffmpeg** (ffprobe 있으면 더 정밀) |
| 자막용 | **mlx-whisper** (로컬 음성인식) |

### 설치

```bash
# 1) 받기
git clone https://github.com/biblcontentofficial-art/Premiere-Pro-edit-bibl.git
cd Premiere-Pro-edit-bibl

# 2) 의존성
brew install ffmpeg            # 없으면
pip install -r requirements.txt

# 3) 실행 권한
chmod +x edit.sh batch.sh
```

---

## 사용법

```bash
# 1) 기본 (표준 프리셋)
./edit.sh "원본영상.mp4"

# 2) 프리셋 선택
./edit.sh "원본영상.mp4" --preset 보수   # 덜 자름 (자연스러움 우선)
./edit.sh "원본영상.mp4" --preset 공격   # 최대한 타이트 (+ 어/음 음향검출)

# 3) 폴더 일괄 처리
./batch.sh "촬영본폴더" 표준
```

**프리미어에서:**
1. `파일 > 가져오기`(Cmd+I) → `output/..._cut.xml` 선택 → 생성된 시퀀스 더블클릭
2. 자막은 `output/..._cut.srt`(또는 `.ass`)을 가져와 타임라인에 드래그
3. (선택) 타임라인 전체 선택 → `Cmd+Shift+D` 로 모든 컷에 오디오 전환 적용

> `_cut_audio.wav`는 XML이 자동으로 끌어옵니다. `_report.html`은 브라우저로 열면 잘린 내용·자연스러움 주의 구간을 타임코드로 확인할 수 있어요.

---

## 튜닝

`engine/config.py`의 프리셋, 또는 프로젝트 루트에 `config.json`(→ `config.json.example` 참고)으로 모든 값을 조절합니다.

| 키 | 의미 |
|----|------|
| `NOISE_DB` | 무음 판정 임계값. 낮출수록 작은 끝음/작은 소리를 살림 |
| `MIN_SILENCE` | 이 길이(초) 이상 조용해야 컷 |
| `FILLER_PHRASES` | 제거할 추임새 단어 목록 |
| `HESITATION_MIN` | 어/음 망설임 빈틈 최소 길이 |
| `TARGET_LUFS` | 목표 라우드니스 (유튜브 -14) |
| `DENOISE` / `DEESS` | 노이즈/치찰음 제거 (기본 OFF) |

---

## 작동 원리

프리미어를 직접 조종(UXP/API)하는 대신, **FCP7 XML(편집 가능한 시퀀스 교환 포맷)을 생성해 가져오기**하는 방식입니다. 그래서:

- **비파괴** — 원본 영상은 안 건드림. 컷은 일반 클립이라 트림·이동·복구 자유
- **안정적** — 프리미어 버전 업데이트에도 안 깨짐
- **빠름** — 분석은 ffmpeg + 로컬 Whisper, 시퀀스 생성은 즉시

```
원본 영상 ─> [파이썬 엔진]
              ├─ 무음 감지(ffmpeg) + 음량 정리(loudnorm)
              ├─ 단어 단위 전사(mlx-whisper, verbatim)
              ├─ 추임새/망설임/더듬 제거 구간 계산
              └─ FCP7 XML(컷 시퀀스) + SRT(컷 정렬 자막)
                        │
                        v
            프리미어 '불러오기' ─> 편집 가능한 러프컷 + 자막
```

---

## AI 편집 에이전트 팀 (Claude Code)

[Claude Code](https://claude.com/claude-code)로 이 폴더를 열고 **"이 영상 편집해줘"** 하면, 5개 전문 에이전트가 협업해 기획~검수까지 자동 수행합니다:

| 에이전트 | 역할 |
|---------|------|
| 편집 디렉터 | 방향 설정 · 검수 · 핸드오프 |
| 콘텐츠 리서처 | 핵심 메시지 · 하이라이트 · 삭제구간 · 챕터 · 제목 후보 |
| 영상 기획자 | 인트로 훅 · 흐름 · 강조/B롤 마커 |
| 컷편집가 | 엔진 운용 · 결과 검증 |
| 자막 에디터 | 고유명사 교정 · 가독성 |

`.claude/agents/`, `.claude/skills/`에 정의돼 있습니다.

---

## [주의] 한계

- **macOS 애플 실리콘 전용** (mlx-whisper 의존)
- **한국어 발화 최적화** (추임새 목록·Whisper 프롬프트가 한국어 기준 — 다른 언어는 목록 교체 필요)
- 단일 카메라 토크형 기준. 멀티캠/VFR은 미지원
- 어/음 일부는 어떤 Whisper 모델로도 텍스트로 안 잡혀, 음향 검출(옵션)로 보조

---

## 라이선스

[MIT](LICENSE) — 자유롭게 쓰고 고치고 배포하세요. PR 환영합니다.
