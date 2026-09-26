#!/usr/bin/env bash
# Build the Mac app into desktop/build/GoRunRun Local AI.app (ad-hoc signed).
set -euo pipefail
cd "$(dirname "$0")"
# Assemble and sign outside the project folder: iCloud-synced folders (e.g. ~/Desktop) re-add
# Finder metadata to bundles, which codesign rejects.
OUT="build/GoRunRun Local AI.app"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
APP="$WORK/GoRunRun Local AI.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
swiftc -O -swift-version 5 -target arm64-apple-macos14.0 -framework AppKit -framework WebKit \
  -o "$APP/Contents/MacOS/GoRunRunLocalAI" main.swift
cp Info.plist "$APP/Contents/Info.plist"

# App icon from the brand kit (assets/brand/app/icon.icns).
cp ../assets/brand/app/icon.icns "$APP/Contents/Resources/AppIcon.icns"

xattr -cr "$APP"
codesign --force --sign - "$APP" >/dev/null
mkdir -p build && rm -rf "$OUT" && ditto "$APP" "$OUT"
echo "built $OUT"
