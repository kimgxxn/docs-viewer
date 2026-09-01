#!/usr/bin/env bash
# 배포용 zip 을 만든다.  →  dist/docs-viewer-<버전>.zip
#
# 담는 것: git 이 추적하는 파일 전부 + assets/mermaid.min.js
# 빼는 것: config.json · run.log · run.pid · cache/ · dist/ · gdrive_*.json
#          (.gitignore 에 있으므로 git ls-files 가 알아서 걸러낸다)
#
# 커밋하지 않은 수정본도 그대로 담기지만, **새로 만든 파일은 git add 를 해야**
# 포함됩니다 (추적되지 않은 파일은 배포본에서 조용히 빠집니다).
set -euo pipefail
DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR"

git rev-parse --git-dir >/dev/null 2>&1 || {
  echo "git 레포 안에서 실행해야 합니다 (담을 파일 목록을 git 에서 얻습니다)." >&2
  exit 1
}

NAME="docs-viewer"
VER=$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' docs_viewer.py | head -1)
MERMAID_VER=$(sed -n 's/^MERMAID_VER = "\(.*\)"$/\1/p' docs_viewer.py | head -1)
[ -n "$VER" ] && [ -n "$MERMAID_VER" ] || {
  echo "docs_viewer.py 에서 VERSION / MERMAID_VER 를 찾지 못했습니다." >&2
  exit 1
}
OUT="$DIR/dist/$NAME-$VER.zip"

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/$NAME"

# 1) 추적 중인 파일만 복사 (실행 권한 유지)
COUNT=0
while IFS= read -r -d '' f; do
  mkdir -p "$STAGE/$NAME/$(dirname "$f")"
  cp -p "$f" "$STAGE/$NAME/$f"
  COUNT=$((COUNT + 1))
done < <(git ls-files -z)
echo "  추적 파일 $COUNT 개를 담았습니다."

# 2) mermaid 스크립트 동봉 — 없으면 한 번 내려받아 cache/ 에 남긴다.
#    (동봉하지 않으면 받는 사람이 첫 다이어그램에서 3.5MB 를 직접 받아야 한다)
SRC="$DIR/cache/mermaid-$MERMAID_VER.min.js"
if [ ! -f "$SRC" ]; then
  echo "  mermaid $MERMAID_VER 을 내려받습니다..."
  mkdir -p "$DIR/cache"
  curl -fsSL --retry 2 \
    "https://cdn.jsdelivr.net/npm/mermaid@$MERMAID_VER/dist/mermaid.min.js" -o "$SRC.part"
  mv "$SRC.part" "$SRC"
fi
mkdir -p "$STAGE/$NAME/assets"
cp "$SRC" "$STAGE/$NAME/assets/mermaid.min.js"
echo "  mermaid $MERMAID_VER 동봉 ($(du -h "$SRC" | cut -f1))"

# 3) 개인 정보가 섞여 들어가지 않았는지 확인.
#    홈 경로나 계정명이 박힌 파일이 있으면 배포를 멈춘다 (SCAN=0 으로 건너뛸 수 있음).
if [ "${SCAN:-1}" = 1 ]; then
  HITS=$(grep -rIl -e "$HOME" -e "$(whoami)" "$STAGE/$NAME" 2>/dev/null || true)
  if [ -n "$HITS" ]; then
    echo "개인 경로·계정명이 든 파일이 있습니다. 확인 후 다시 실행하세요:" >&2
    echo "$HITS" | sed "s|$STAGE/|  |" >&2
    exit 1
  fi
  echo "  개인정보 스캔 통과."
fi

# 4) 압축 (-X: macOS 확장 속성 제외)
mkdir -p "$DIR/dist"
rm -f "$OUT"
(cd "$STAGE" && zip -r -X -q "$OUT" "$NAME")

echo
echo "  $OUT  ($(du -h "$OUT" | cut -f1))"
