---
name: video-edit-pipeline
description: 비블 유튜브 영상 1개를 받아 기획→리서치→컷편집→자막→검수 전체 편집 파이프라인을 전문 에이전트 팀으로 실행한다. "영상 편집해줘", "이 영상 편집 파이프라인", "자동 편집 돌려줘", "기획부터 자막까지", "편집 에이전트 돌려줘", "영상 편집 다시/재실행/업데이트", "컷편집만 다시", "자막만 다시", "리서치만" 등 비블 영상 편집 전반 요청 시 반드시 사용. 단순 단일작업(컷만/자막만)은 해당 전문 스킬 직접 호출도 가능.
---

# 비블 영상 편집 파이프라인 (오케스트레이터)

원본 영상 1개를 받아 5개 전문 에이전트를 파이프라인으로 지휘해 프리미어 핸드오프까지 끝낸다.

**실행 모드:** 서브 에이전트 파이프라인 + 감독자(편집 디렉터 검수). 작업이 순차 의존적이고(전사→분석→기획→재컷→자막) 무거운 연산은 `auto_cut.py`가 결정적으로 처리하므로, 파일 기반 핸드오프가 가장 안정적이다.

모든 에이전트 호출은 `Agent` 도구 + `subagent_type` + **`model: "opus"`**. 산출물은 `output/_workspace/`에 파일로 주고받는다.

## Phase 0: 컨텍스트 확인
1. `output/_workspace/` 존재 여부 확인.
   - 없음 → **초기 실행** (전체 Phase)
   - 있음 + 비블이 부분 수정 요청("자막만 다시", "더 보수적으로") → **부분 재실행** (해당 에이전트만)
   - 있음 + 새 영상/전면 재작업 → 기존을 `_workspace_prev/`로 옮기고 **새 실행**
2. 부분 재실행이면 아래 Phase 중 해당 단계만 수행하고 디렉터 검수로 마무리.

## Phase 1: 방향 설정 (편집 디렉터)
- `Agent(edit-director, model:opus)` → `00_director_brief.md` (영상 성격, 초기 프리셋, 주의점).

## Phase 2: 러프컷 + 전사 (컷편집가)
- `Agent(cut-editor, model:opus)` → `engine/auto_cut.py "영상" --preset {brief의 프리셋}` 실행(백그라운드, 완료 대기).
- 산출: `_cut.xml/_cut_audio.wav/_cut.srt/_cut_report.txt/_words.json` + `30_cut_result.md`.
- **여기서 전사(`_words.json`)가 먼저 나와야** 리서처·기획자가 일한다.

## Phase 3: 내용 분석 (콘텐츠 리서처)
- `Agent(content-researcher, model:opus)` → `10_research.md` (핵심메시지·하이라이트·삭제추천·챕터).

## Phase 4: 편집 기획 (영상 기획자) + 쇼츠 제작 (숏폼 PD) — 병렬
리서치(`10_research.md`)가 나오면 둘은 독립적이라 같이 돌린다:
- `Agent(video-planner, model:opus)` → `20_plan.md` (인트로훅·흐름·프리셋추천·강조/B롤마커).
- `Agent(shorts-producer, model:opus)` → 핵심 기반 1분 쇼츠 5개(9:16) 제작 → `output/shorts/short_01~05.mp4` + `50_shorts.md`.

## Phase 5: 최종 컷 (컷편집가, 조건부)
- 기획의 프리셋/설정이 Phase 2와 다르면 `Agent(cut-editor, model:opus)`로 재실행. 같으면 건너뜀.
- 내용상 삭제추천(`10_research.md`)이 있으면 config.json 또는 수동 구간으로 반영.

## Phase 6: 자막 교정 (자막 에디터)
- `Agent(subtitle-editor, model:opus)` → `_cut.srt` 교정본 + `40_subtitle_notes.md` (고유명사·줄균형·가독성).

## Phase 7: 검수 & 핸드오프 (편집 디렉터)
- `Agent(edit-director, model:opus)` → 전체 산출물 검수 → `99_director_handoff.md` (잘린 요약, 비블이 손볼 지점, 불러올 파일).
- 비블에게 핸드오프 노트를 요약 보고.

## 데이터 흐름 (파일 기반)
```
00_director_brief → 10_research → 20_plan
        ↓              ↓            ↓
   [cut-editor 엔진] → _words.json/_cut.srt/_cut_report.txt
        ↓
   [subtitle-editor] → _cut.srt(교정)
        ↓
   99_director_handoff (← 전부 종합)
```
`output/_workspace/{단계}_{에이전트}_{산출물}` 컨벤션. 최종 산출물(_cut.xml/.srt/.wav)만 `output/`에, 중간물은 `_workspace/`에 보존(감사·재실행용).

## 에러 핸들링
- 에이전트 산출물 누락 → 1회 재호출, 재실패 시 누락을 핸드오프에 명시하고 진행.
- 엔진 실행 실패(코덱/모델/경로) → 컷편집가가 원인과 함께 디렉터에 반려, 파이프라인 일시정지하고 비블에 보고.
- 과제거 감지(제거율>16% 또는 접속사 잘림) → 디렉터가 한 단계 보수 프리셋으로 Phase 5 재실행 지시.
- 상충하는 판단(리서처 vs 기획자)은 삭제하지 않고 디렉터가 핸드오프에 양쪽 병기.

## 테스트 시나리오
- **정상 흐름**: 73분 라이브 토크 → brief(표준) → 러프컷(13% 제거) → 리서치(하이라이트 5·삭제추천 3) → 기획(인트로훅+표준유지) → 자막교정(닉네임 4건) → 핸드오프. 기대: `_cut.xml`+교정 SRT+핸드오프 노트.
- **에러 흐름**: 전사 모델 다운로드 실패 → 컷편집가 1회 재시도 → 재실패 시 디렉터가 비블에 네트워크/모델 문제 보고, 나머지 Phase 보류.
- **부분 재실행**: "자막만 더 다듬어" → Phase 0에서 _workspace 감지 → Phase 6만 실행 → 디렉터 간단 검수.
