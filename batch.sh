#!/bin/bash
# batch.sh — 폴더 안 모든 영상을 한 번에 컷편집
#
# 사용법:
#   ./batch.sh "영상폴더" [프리셋]
#   ./batch.sh ~/Downloads/촬영본 표준
#
# 폴더 안 mp4/mov/m4v/mkv 를 순서대로 처리. 각 결과는 output/ 에 생성.

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$1"
PRESET="${2:-표준}"

if [ -z "$SRC" ] || [ ! -d "$SRC" ]; then
  echo "사용법: ./batch.sh \"영상폴더\" [보수|표준|공격]"
  exit 1
fi

shopt -s nullglob nocaseglob
FILES=("$SRC"/*.mp4 "$SRC"/*.mov "$SRC"/*.m4v "$SRC"/*.mkv)
N=${#FILES[@]}
if [ "$N" -eq 0 ]; then
  echo "영상 파일이 없습니다: $SRC"
  exit 1
fi

echo "총 $N 개 영상 · 프리셋 $PRESET"
i=0
for f in "${FILES[@]}"; do
  i=$((i+1))
  echo ""
  echo "━━━━━━━━━━ [$i/$N] $(basename "$f") ━━━━━━━━━━"
  python3 "$DIR/engine/auto_cut.py" "$f" --preset "$PRESET" || echo "⚠ 실패: $(basename "$f") (건너뜀)"
done

echo ""
echo "🎬 배치 완료 ($N개). 결과는 output/ 폴더."
