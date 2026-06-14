# Premiere Pro 자동 편집 프로젝트

## 하네스: 비블 유튜브 영상 편집

**목표:** 원본 영상 1개 → 기획·리서치·컷편집·자막·검수를 전문 에이전트 팀으로 처리해 프리미어 핸드오프까지 자동화.

**트리거:** 비블 영상 편집 전반(기획~자막) 요청 시 `video-edit-pipeline` 스킬을 사용하라. 단일 작업은 전문 스킬 직접 호출 가능 — 컷편집만=`cut-editing`, 자막만=`subtitle-editing`, 리서치만=`content-research`, 기획만=`video-planning`, 검수만=`edit-direction`. 단순 질문은 직접 응답.

**핵심 엔진:** `engine/auto_cut.py` (= `./edit.sh "영상.mp4" --preset 보수|표준|공격`). 무음·추임새·말더듬 제거 + -14 LUFS 음량정리 + 컷정렬 자막을 한 번에 생성. 설정은 `engine/config.py`(프리셋) + `config.json`(사용자 override). 개선 계획은 `ROADMAP.md`.

**변경 이력:**
| 날짜 | 변경 내용 | 대상 | 사유 |
|------|----------|------|------|
| 2026-06-14 | 초기 구성 (에이전트 5 + 스킬 6) | 전체 | 영상 편집 워크플로우 자동화 |
| 2026-06-14 | 설정 프리셋 + 버린컷/백업/검증 + 자연스러움 가드 | engine, config | 로드맵 Task1·3 |
| 2026-06-14 | 제1원칙 '자연스러움 > 최대 제거' 격상 | edit-director, cut-editing | 비블 피드백 |
| 2026-06-14 | 자막 한줄30자+Pretendard Bold 스타일, 어/음 음향검출, 오디오후처리, 문맥필러+false-start, 전사export, 배치, HTML리포트 | engine 전반 | 로드맵 Task5~10 |
