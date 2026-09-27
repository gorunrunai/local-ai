#!/bin/bash
# Download the pre-built Mac app into desktop/build (built on GitHub by .github/workflows/mac-app.yml),
# so installing it doesn't need a working Swift compiler on this Mac. `make desktop` builds it here instead.
#   GORUNRUN_APP_RELEASE  release to download from (default mac-app)
#   GORUNRUN_APP_URL      or the full address of the folder holding GoRunRun-Local-AI.zip(.sha256)
set -euo pipefail
cd "$(dirname "$0")/.."
BASE="${GORUNRUN_APP_URL:-https://github.com/gorunrunai/local-ai/releases/download/${GORUNRUN_APP_RELEASE:-mac-app}}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

curl -fsSL --retry 2 -o "$TMP/app.zip" "$BASE/GoRunRun-Local-AI.zip"
want="$(curl -fsSL --retry 2 "$BASE/GoRunRun-Local-AI.zip.sha256" | tr -d '[:space:]')"
got="$(shasum -a 256 "$TMP/app.zip" | cut -d' ' -f1)"
[ "$want" = "$got" ] || { echo "the download is damaged (checksum $got, expected $want)"; exit 1; }
ditto -x -k "$TMP/app.zip" "$TMP/out"
APP="$TMP/out/GoRunRun Local AI.app"
codesign --verify --strict "$APP"                                          # complete and signed
file -b "$APP/Contents/MacOS/GoRunRunLocalAI" | grep -q arm64 || { echo "not an Apple Silicon app"; exit 1; }
mkdir -p desktop/build
rm -rf "desktop/build/GoRunRun Local AI.app"
ditto "$APP" "desktop/build/GoRunRun Local AI.app"
echo "downloaded the Mac app from $BASE"
