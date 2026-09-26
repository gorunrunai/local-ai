#!/bin/bash
# Install GoRunRun Local AI from this folder instead of GitHub: for testing before publishing.
#
#   ./install-local.sh                  install (or update) from the files in this folder
#   ./install-local.sh --dry-run        any install.sh option works the same way
#   ./install-local.sh --uninstall
#
# It copies this folder's files (committed or not; ignored files such as data/, models and build
# output are left out) into a local git repo at ~/.gorunrun/local-source, then runs this folder's
# install.sh with GORUNRUN_REPO pointing there. Nothing is fetched from github.com/gorunrunai/local-ai.
# Each run adds a new snapshot on top of the last, so re-running it updates an existing install.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
SNAP="${GORUNRUN_LOCAL_SOURCE:-$HOME/.gorunrun/local-source}"
INSTALLED="${GORUNRUN_HOME:-$HOME/.gorunrun/local}"

if ! xcode-select -p >/dev/null 2>&1; then
  echo "Apple's command line tools are needed first (they include git)."
  echo "Run:  xcode-select --install   then run this script again."
  exit 1
fi
git -C "$SRC" rev-parse --git-dir >/dev/null 2>&1 || { echo "$SRC isn't a git checkout."; exit 1; }

case " $* " in
  *" --dry-run "*) ;;              # a dry run changes nothing, so don't make a snapshot either
  *)
    if [ ! -d "$SNAP/.git" ]; then
      rm -rf "$SNAP"
      if [ -d "$INSTALLED/.git" ]; then
        # An existing install but no snapshot repo (deleted, or made elsewhere): continue its
        # history, so the update fast-forwards instead of failing on unrelated histories.
        git clone -q "$INSTALLED" "$SNAP"
        git -C "$SNAP" checkout -q -B main
      else
        mkdir -p "$SNAP" && git -C "$SNAP" init -q -b main
      fi
    fi
    find "$SNAP" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
    git -C "$SRC" ls-files -co --exclude-standard -z | rsync -a --from0 --files-from=- "$SRC/" "$SNAP/"
    git -C "$SNAP" add -A
    git -C "$SNAP" diff --cached --quiet \
      || git -C "$SNAP" -c user.name="install-local" -c user.email="install-local@localhost" \
           commit -q -m "Snapshot of $SRC ($(date '+%Y-%m-%d %H:%M'))"
    ;;
esac

status=0
GORUNRUN_REPO="$SNAP" bash "$SRC/install.sh" "$@" || status=$?
case " $* " in
  *" --dry-run "*) ;;                                   # a dry run changes nothing
  *" --uninstall "*) [ "$status" = 0 ] && rm -rf "$SNAP" ;;
esac
exit "$status"
