#!/bin/bash
# edit.sh — 영상 1개 → 무음제거 + 추임새제거 + 음량정리 + 자막 한 번에
#
# 사용법:
#   ./edit.sh "원본영상.mp4"
#
# 결과물(output 폴더):
#   <이름>_cut.xml        ← 프리미어에 '불러오기' (컷+음량 적용된 시퀀스)
#   <이름>_cut_audio.wav  ← 정리된 오디오 (XML이 자동 연결)
#   <이름>_cut.srt        ← 자막 (시퀀스에 끌어다 놓기)
#   <이름>_words.json     ← 받아쓰기 캐시 (추임새 설정만 바꿀 땐 재전사 생략)

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
VIDEO="$1"

if [ -z "$VIDEO" ]; then
  echo "사용법: ./edit.sh \"원본영상.mp4\""
  exit 1
fi
if [ ! -f "$VIDEO" ]; then
  echo "파일을 찾을 수 없음: $VIDEO"
  exit 1
fi

# Windows(Git Bash)의 python3는 MS Store 바로가기일 수 있어 실제로 실행되는 것을 고른다
if python3 -c "" 2>/dev/null; then PY=(python3)
elif py -3 -c "" 2>/dev/null; then PY=(py -3)
else PY=(python); fi
export PYTHONIOENCODING=utf-8

"${PY[@]}" "$DIR/engine/auto_cut.py" "$@"
