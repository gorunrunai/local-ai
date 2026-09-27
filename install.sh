#!/bin/bash
# GoRunRun Local AI installer for Apple Silicon Macs.
#
#   curl -fsSL https://raw.githubusercontent.com/gorunrunai/local-ai/main/install.sh | bash
#
# Options (after `bash -s --` when piping, e.g. `... | bash -s -- --with-video`):
#   --model=qwen     the chat model: qwen (default; needs 64 GB of memory) or gemma (lighter)
#   --video=ltx      video creation engines: ltx (the default on 64 GB Macs; with sound), wan, or both
#   --with-video     same as the default: video creation with LTX-2.3 (asks which engines)
#   --without-video  skip video creation without asking
#   --dry-run        ask the questions, show what would be installed, and change nothing
#   --machine=M1Max-32GB  with --dry-run: simulate a fresh install on another Mac (chip-memory);
#                    add --macos=14 to simulate a macOS version. Used to test each configuration.
#   --uninstall      remove the app, the background service and the program files
#   --yes            don't ask questions; use the defaults
# Re-running the installer updates an existing installation.
#
# Environment: GORUNRUN_HOME (install folder, default ~/.gorunrun/local),
#              GORUNRUN_REPO (git URL or local path to install from), GORUNRUN_BRANCH (default main),
#              NO_COLOR (plain output).
#
# Written for the bash 3.2 that ships with macOS: no dependencies beyond the base system.
set -euo pipefail
case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in *UTF-8*|*utf8*) ;; *) export LC_CTYPE=en_US.UTF-8 ;; esac

REPO="${GORUNRUN_REPO:-https://github.com/gorunrunai/local-ai.git}"
BRANCH="${GORUNRUN_BRANCH:-main}"
HOME_DIR="${GORUNRUN_HOME:-$HOME/.gorunrun/local}"
LOG="$HOME/.gorunrun/install.log"
MIN_MEM_GB=32
WITH_VIDEO=0
NO_VIDEO=0
VIDEO=""        # ltx | wan | both
MODEL=""
UNINSTALL=0
ASSUME_YES=0
DRY_RUN=0
SIM=""          # --machine: simulated Mac for dry runs
SIM_MACOS=""
for arg in "$@"; do
  case "$arg" in
    --model=qwen|--model=gemma) MODEL="${arg#--model=}" ;;
    --with-video) WITH_VIDEO=1 ;;
    --video=ltx|--video=wan|--video=both) WITH_VIDEO=1; VIDEO="${arg#--video=}" ;;
    --without-video) NO_VIDEO=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --machine=*) SIM="${arg#--machine=}" ;;
    --macos=*) SIM_MACOS="${arg#--macos=}" ;;
    *) echo "Unknown option: $arg"; exit 2 ;;
  esac
done
if [ -n "$SIM$SIM_MACOS" ] && [ "$DRY_RUN" = 0 ]; then
  echo "--machine and --macos only work with --dry-run (they simulate another Mac)."; exit 2
fi
SHOWN_HOME="$HOME_DIR"
# A simulated Mac is always a fresh install: point at a folder that doesn't exist (and is never made).
if [ -n "$SIM" ]; then HOME_DIR="${TMPDIR:-/tmp}/gorunrun-simulated-$$/local"; fi

# ============================================================================================
# Terminal UI
# ============================================================================================
FANCY=0        # colors, menus, spinners (a real terminal)
INTERACTIVE=0  # we may ask questions (a keyboard is attached)
if [ "$ASSUME_YES" = 0 ] && { : </dev/tty; } 2>/dev/null; then INTERACTIVE=1; fi
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != dumb ] && [ "$INTERACTIVE" = 1 ]; then FANCY=1; fi
term_cols() {   # the window's width. Ask the terminal itself: under `curl | bash`, tput has none to ask
  local c; c=$( (stty size </dev/tty) 2>/dev/null | awk '{print $2}')
  [ "${c:-0}" -gt 0 ] 2>/dev/null || c=$(tput cols 2>/dev/null)
  [ "${c:-0}" -gt 0 ] 2>/dev/null || c=80
  echo "$c"
}
COLS=$(term_cols)
W=$COLS; [ "$W" -gt 76 ] && W=76

ESC=$'\033'
if [ "$FANCY" = 1 ]; then
  B="${ESC}[1m"; D="${ESC}[2m"; R="${ESC}[0m"
  GREEN="${ESC}[32m"; YELLOW="${ESC}[33m"; RED="${ESC}[31m"
  if [ "${COLORTERM:-}" = truecolor ] || [ "${COLORTERM:-}" = 24bit ]; then
    ORANGE="${ESC}[38;2;226;85;45m"; MUTED="${ESC}[38;2;150;144;132m"
    FG_K="${ESC}[38;2;23;23;26m"; FG_P="${ESC}[38;2;244;241;234m"; FG_O="$ORANGE"
    BG_K="${ESC}[48;2;23;23;26m"; BG_P="${ESC}[48;2;244;241;234m"; BG_O="${ESC}[48;2;226;85;45m"
  else   # 256 colors (macOS Terminal)
    ORANGE="${ESC}[38;5;202m"; MUTED="${ESC}[38;5;245m"
    FG_K="${ESC}[38;5;234m"; FG_P="${ESC}[38;5;255m"; FG_O="$ORANGE"
    BG_K="${ESC}[48;5;234m"; BG_P="${ESC}[48;5;255m"; BG_O="${ESC}[48;5;202m"
  fi
else
  B=""; D=""; R=""; BG_P=""; FG_K=""; GREEN=""; YELLOW=""; RED=""; ORANGE=""; MUTED=""
fi

TTY_SAVED=""
SESSION=0   # 1 while the installer has its own screen (the terminal's alternate screen, like vim)
FINAL=""    # printed on the normal screen once the installer's screen closes
cleanup() {
  [ -n "$TTY_SAVED" ] && stty "$TTY_SAVED" </dev/tty 2>/dev/null
  [ "$FANCY" = 1 ] && printf '%s' "${ESC}[?25h${R}"
  if [ "$SESSION" = 1 ]; then printf '%s' "${ESC}[?1049l"; SESSION=0; fi   # the earlier screen comes back
  if [ -n "$FINAL" ]; then printf '%s\n' "$FINAL"; FINAL=""; fi
  return 0
}
# Own screen for the installer, without clearing the terminal: on exit it switches back.
open_session() {
  if [ "$FANCY" = 1 ]; then printf '%s' "${ESC}[?1049h${ESC}[H${ESC}[2J"; SESSION=1; fi
}
# Leave this on the normal screen after the installer's own screen closes (only needed then).
keep() { if [ "$SESSION" = 1 ]; then FINAL=$1; fi; }
# Keep the last screen readable until the person is done with it.
close_session() {
  if [ "$SESSION" = 1 ] && [ "$INTERACTIVE" = 1 ]; then
    printf '\n  %sPress Return to close the installer%s' "$D" "$R"
    keys_raw; printf '%s' "${ESC}[?25l"
    while :; do read_key; [ "$KEY" = enter ] && break; done
    keys_normal
  fi
}
# While a menu is open, keys must not echo (an echoed Enter moves the cursor and breaks redraws).
keys_raw() { TTY_SAVED=$(stty -g </dev/tty); stty -echo -icanon </dev/tty; }
keys_normal() { [ -n "$TTY_SAVED" ] && stty "$TTY_SAVED" </dev/tty; TTY_SAVED=""; }
trap cleanup EXIT
trap 'FINAL=$(printf "\n  %s\n" "Installation cancelled. Nothing more will be changed."); exit 130' INT

# The app icon, drawn for 20x20 pixels (two per character): k ink, p paper, o orange, . clear.
ICON=(
"..kkkkkkkkkkkkkkkk.." ".kkkkkkkkkkkkkkkkkk." "kkkkkkkkkkkkkkkkkkkk" "kkkppppppppppppppkkk"
"kkkpkkkkkkkkkkkkpkkk" "kkkpkppppppppppkpkkk" "kkkpkpkppppppppkpkkk" "kkkpkpppkppppppkpkkk"
"kkkpkpkppppppoopkkkk" "kkkpkpppkppppoopkkkk" "kkkpkpkppppppppkpkkk" "kkkpkppppppppppkpkkk"
"kkkpkpkkkkkkkkkkpkkk" "kkkppppppppppppppkkk" "kkppppppppppppppppkk" "kkkppppppppppppppkkk"
"kkkkkkkkkkkkkkkkkkkk" "kkkkkkkkkkkkkkkkkkkk" ".kkkkkkkkkkkkkkkkkk." "..kkkkkkkkkkkkkkkk.."
)
# "GoRunRun", bold pixel letters (# = ink in light terminals, paper in dark: the text color).
WORD=(
".####........#####..............#####............."
"##..##.......##..##.............##..##............"
"##......###..##..##.##.##.####..##..##.##.##.####."
"##.###.##.##.#####..##.##.##.##.#####..##.##.##.##"
"##..##.##.##.##.##..##.##.##.##.##.##..##.##.##.##"
"##..##.##.##.##..##.##.##.##.##.##..##.##.##.##.##"
".####...###..##..##..####.##.##.##..##..####.##.##"
".................................................."
)

fg_of() { case "$1" in k) printf '%s' "$FG_K" ;; p) printf '%s' "$FG_P" ;; o) printf '%s' "$FG_O" ;; esac; }
bg_of() { case "$1" in k) printf '%s' "$BG_K" ;; p) printf '%s' "$BG_P" ;; o) printf '%s' "$BG_O" ;; esac; }

# One terminal row from two pixel rows, using half blocks.
icon_row() {
  local top=$1 bot=$2 i t b out=""
  for ((i = 0; i < ${#top}; i++)); do
    t=${top:i:1}; b=${bot:i:1}
    if [ "$t" = . ] && [ "$b" = . ]; then out+="${R} "
    elif [ "$t" = . ]; then out+="${R}$(fg_of "$b")▄"
    elif [ "$b" = . ]; then out+="${R}$(fg_of "$t")▀"
    else out+="$(fg_of "$t")$(bg_of "$b")▀"; fi
  done
  printf '%s%s' "$out" "$R"
}
word_row() {
  local top=$1 bot=$2 i t b out=""
  for ((i = 0; i < ${#top}; i++)); do
    t=${top:i:1}; b=${bot:i:1}
    if [ "$t" = "#" ] && [ "$b" = "#" ]; then out+="█"
    elif [ "$t" = "#" ]; then out+="▀"
    elif [ "$b" = "#" ]; then out+="▄"
    else out+=" "; fi
  done
  printf '%s%s%s' "$B" "$out" "$R"
}

banner() {
  echo
  if [ "$FANCY" = 1 ] && [ "$COLS" -ge 80 ]; then
    local r right
    for r in 0 1 2 3 4 5 6 7 8 9; do
      case $r in
        2|3|4|5) right=$(word_row "${WORD[$(( (r - 2) * 2 ))]}" "${WORD[$(( (r - 2) * 2 + 1 ))]}") ;;
        6) right="${MUTED}L O C A L    A I${R}" ;;
        8) right="${D}Your private AI assistant, running entirely on your Mac${R}" ;;
        *) right="" ;;
      esac
      printf '  %s   %s\n' "$(icon_row "${ICON[$((r * 2))]}" "${ICON[$((r * 2 + 1))]}")" "$right"
    done
  else
    echo "  ${B}GoRunRun${R} ${MUTED}LOCAL AI${R}"
    echo "  ${D}Your private AI assistant, running entirely on your Mac${R}"
  fi
}

rule() { local n=$1 s=""; while [ "$n" -gt 0 ]; do s+="─"; n=$((n - 1)); done; printf '%s' "$s"; }
section() {  # section "Title"
  local t=$1 fill=$(( W - ${#1} - 7 )); [ "$fill" -lt 3 ] && fill=3
  printf '\n  %s◆%s %s%s%s %s%s%s\n\n' "$ORANGE" "$R" "$B" "$t" "$R" "$D" "$(rule "$fill")" "$R"
}
ok()   { printf '  %s✓%s %s\n' "$GREEN" "$R" "$1"; }
info() { printf '  %s·%s %s\n' "$D" "$R" "$1"; }
warn() { printf '  %s!%s %s\n' "$YELLOW" "$R" "$1"; }
die() {  # die "message" ["detail lines"]: shown after the installer's screen closes
  FINAL=$(printf '\n  %s✗ %s%s\n' "$RED$B" "$1" "$R"; [ -n "${2:-}" ] && printf '\n%s\n' "$2"; echo)
  [ "$SESSION" = 1 ] || { printf '%s\n' "$FINAL" >&2; FINAL=""; }
  exit 1
}
pad()  { local s=$1 n=$2; printf '%s' "$s"; n=$(( n - ${#s} )); while [ "$n" -gt 0 ]; do printf ' '; n=$((n - 1)); done; }
strip_ansi() { LC_ALL=C sed -e 's/\x1b\[[0-9;?]*[A-Za-z]//g'; }
# The last line a step wrote to the log, for the spinner. Works on bytes (LC_ALL=C): a cut through a
# multi-byte character (npm prints "…" and box drawing) must not print "Illegal byte sequence".
last_log_line() {
  tail -c 3000 "$LOG" 2>/dev/null | LC_ALL=C tr '\r' '\n' 2>/dev/null | strip_ansi 2>/dev/null \
    | LC_ALL=C grep -a -v -e '^[[:space:]]*$' -e '^=== ' -e '^\[progress\] ' 2>/dev/null | tail -n 1 \
    | iconv -c -f UTF-8 -t UTF-8 2>/dev/null
}
# A download's progress as a bar, from the downloader's `[progress] <done> <total>` lines (bytes).
# Empty when the current step reports none.
progress_bar() {   # progress_bar <columns available>: percentage and GB, with as much bar as fits
  local room=${1:-80} line got total pct text width fill i bar=""
  line=$(tail -c 20000 "$LOG" 2>/dev/null | LC_ALL=C tr '\r' '\n' \
    | LC_ALL=C awk '/^=== /{p=""} /^\[progress\] [0-9]+ [0-9]+$/{p=$0} END{print p}')
  [ -n "$line" ] || return 0
  got=${line#\[progress\] }; total=${got#* }; got=${got% *}
  [ "$total" -gt 0 ] 2>/dev/null || return 0
  pct=$(( got * 100 / total ))
  text="$pct% $(awk -v a="$got" -v b="$total" 'BEGIN{printf "%.1f/%.1f GB", a/1073741824, b/1073741824}')"
  [ "$room" -ge "${#text}" ] || text="$pct%"
  width=$(( room - ${#text} - 1 )); [ "$width" -gt 20 ] && width=20
  if [ "$width" -lt 6 ]; then printf '%s' "$text"; return 0; fi
  fill=$(( pct * width / 100 ))
  for ((i = 0; i < width; i++)); do if [ "$i" -lt "$fill" ]; then bar+="█"; else bar+="░"; fi; done
  printf '%s %s' "$bar" "$text"
}

# card "Title" "line" ... : a rounded box; lines may contain color codes (width is measured without them)
card() {
  local title=$1; shift
  local inner=$(( W - 6 )) line plain
  plain=$(printf '%s' "$title" | strip_ansi)
  printf '  %s╭%s╮%s\n' "$D" "$(rule $((inner + 2)))" "$R"
  printf '  %s│%s %s%s%s%s %s│%s\n' "$D" "$R" "$B" "$title" "$R" "$(pad "" $((inner - ${#plain})))" "$D" "$R"
  printf '  %s│%s %s %s│%s\n' "$D" "$R" "$(pad "" "$inner")" "$D" "$R"
  for line in "$@"; do
    plain=$(printf '%s' "$line" | strip_ansi)
    if [ "${#plain}" -gt "$inner" ]; then line="${plain:0:$((inner - 1))}…"; plain=$line; fi
    printf '  %s│%s %s%s %s│%s\n' "$D" "$R" "$line" "$(pad "" $((inner - ${#plain})))" "$D" "$R"
  done
  printf '  %s╰%s╯%s\n' "$D" "$(rule $((inner + 2)))" "$R"
}

read_key() {  # sets KEY to up/down/left/right/enter/y/n/1-9/other
  local k rest
  IFS= read -rsn1 k </dev/tty || k=""
  if [ "$k" = "$ESC" ]; then
    IFS= read -rsn2 rest </dev/tty || rest=""
    case "$rest" in "[A") KEY=up ;; "[B") KEY=down ;; "[C") KEY=right ;; "[D") KEY=left ;; *) KEY=other ;; esac
  else
    case "$k" in "") KEY=enter ;; k|K) KEY=up ;; j|J) KEY=down ;; h|H) KEY=left ;; l|L) KEY=right ;; *) KEY=$k ;; esac
  fi
}

# select_one VAR "Question" DEFAULT_INDEX "Label|detail|more detail" ... ; VAR gets the chosen index
select_one() {
  local __var=$1 question=$2 sel=$3; shift 3
  local n=$# i lines item label rest part tag wrapped
  local items=("$@")
  if [ "$INTERACTIVE" = 0 ]; then
    item=${items[$sel]}; label=${item%%|*}
    printf '  %s✓%s %s  %s%s%s\n' "$GREEN" "$R" "$question" "$B" "${label%%~*}" "$R"
    eval "$__var=$sel"; return
  fi
  if [ "$FANCY" = 0 ]; then   # plain numbered prompt
    printf '  %s\n' "$question"
    for ((i = 0; i < n; i++)); do item=${items[$i]//\~/, }; printf '    %d) %s\n' $((i + 1)) "${item//|/ — }"; done
    printf '  Choose 1-%d [%d]: ' "$n" $((sel + 1))
    local reply; read -r reply </dev/tty || reply=""
    case "$reply" in [1-9]) [ "$reply" -le "$n" ] && sel=$((reply - 1)) ;; esac
    eval "$__var=$sel"; return
  fi
  keys_raw   # before printing, so even an instant Enter isn't echoed
  printf '  %s?%s %s%s%s  %s↑↓ to move · Enter to choose%s\n' "$ORANGE" "$R" "$B" "$question" "$R" "$D" "$R"
  printf '%s' "${ESC}[?25l"
  while :; do
    lines=0
    for ((i = 0; i < n; i++)); do
      item=${items[$i]}; label=${item%%|*}; rest=${item#"$label"}
      tag=""; case "$label" in *~*) tag="  ${MUTED}${label#*~}${R}"; label=${label%%~*} ;; esac
      if [ "$i" = "$sel" ]; then printf '  %s❯%s %s%s%s%s\n' "$ORANGE" "$R" "$B" "$label" "$R" "$tag"
      else printf '    %s%s\n' "$label" "$tag"; fi
      lines=$((lines + 1))
      while [ -n "$rest" ]; do
        rest=${rest#|}; part=${rest%%|*}; rest=${rest#"$part"}
        while IFS= read -r wrapped; do
          printf '    %s%s%s\n' "$D" "$wrapped" "$R"; lines=$((lines + 1))
        done < <(printf '%s\n' "$part" | fold -s -w $((COLS - 6)))
      done
    done
    read_key
    case "$KEY" in
      up) sel=$(( (sel + n - 1) % n )) ;;
      down) sel=$(( (sel + 1) % n )) ;;
      enter) break ;;
      [1-9]) if [ "$KEY" -le "$n" ]; then sel=$((KEY - 1)); break; fi ;;
    esac
    printf '%s' "${ESC}[${lines}A${ESC}[J"
  done
  keys_normal; printf '%s' "${ESC}[$((lines + 1))A${ESC}[J${ESC}[?25h"
  item=${items[$sel]}; label=${item%%|*}
  printf '  %s✓%s %s  %s%s%s\n' "$GREEN" "$R" "$question" "$B" "${label%%~*}" "$R"
  eval "$__var=$sel"
}

# confirm "Question" yes|no  -> exit status 0 for Yes
confirm() {
  local question=$1 default=$2 sel
  [ "$default" = yes ] && sel=0 || sel=1
  if [ "$INTERACTIVE" = 0 ]; then return 0; fi   # --yes: proceed
  if [ "$FANCY" = 0 ]; then
    local reply hint; [ "$sel" = 0 ] && hint="[Y/n]" || hint="[y/N]"
    printf '  %s %s ' "$question" "$hint"; read -r reply </dev/tty || reply=""
    case "$reply" in y|Y|yes|YES) return 0 ;; n|N|no|NO) return 1 ;; *) [ "$sel" = 0 ]; return ;; esac
  fi
  keys_raw; printf '%s' "${ESC}[?25l"
  while :; do
    local yes=" Yes " no=" No "
    if [ "$sel" = 0 ]; then yes="${BG_P}${FG_K}${B} Yes ${R}"; else no="${BG_P}${FG_K}${B} No ${R}"; fi
    printf '\r%s  %s?%s %s%s%s   %s  %s   %s←→ · Enter%s' "${ESC}[K" "$ORANGE" "$R" "$B" "$question" "$R" "$yes" "$no" "$D" "$R"
    read_key
    case "$KEY" in
      left|right|up|down) sel=$((1 - sel)) ;;
      y|Y) sel=0; break ;;
      n|N) sel=1; break ;;
      enter) break ;;
    esac
  done
  keys_normal
  local answer; [ "$sel" = 0 ] && answer=Yes || answer=No
  printf '\r%s  %s✓%s %s  %s%s%s\n%s' "${ESC}[K" "$GREEN" "$R" "$question" "$B" "$answer" "$R" "${ESC}[?25h"
  [ "$sel" = 0 ]
}

elapsed() { local s=$1; printf '%d:%02d' $((s / 60)) $((s % 60)); }

TASK_SOFT=0   # set to 1 around a task whose failure the install can continue without
# task "Label" command... : runs quietly with a spinner and live status; output goes to $LOG
task() {
  local label=$1; shift
  mkdir -p "$(dirname "$LOG")"
  printf '\n=== %s (%s)\n' "$label" "$(date)" >>"$LOG"
  local start; start=$(date +%s)
  if [ "$FANCY" = 0 ]; then
    printf '  … %s\n' "$label"
    if "$@" >>"$LOG" 2>&1; then ok "$label ($(elapsed $(( $(date +%s) - start ))))"; return; fi
    if [ "$TASK_SOFT" = 1 ]; then printf '  ✗ %s\n' "$label"; return 1; fi
    die "$label failed. The full log is in $LOG" \
      "$(awk '/^=== /{buf=""; next} {buf=buf $0 "\n"} END{printf "%s", buf}' "$LOG" | tail -n 25 | sed 's/^/      /')"
  fi
  ( "$@" ) >>"$LOG" 2>&1 &
  local pid=$! frames="⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" f=0 detail="" tick=0 room
  printf '%s' "${ESC}[?25l"
  while kill -0 "$pid" 2>/dev/null; do
    if [ $((tick % 5)) = 0 ]; then
      COLS=$(term_cols)   # follows the window if it is resized
      room=$(( COLS - ${#label} - 12 )); [ "$room" -lt 0 ] && room=0   # the rest of the line: spinner, time (up to 5 chars), last column left free
      detail=$(progress_bar "$room" || true)
      [ -n "$detail" ] || detail=$(last_log_line || true)
      detail=$(printf '%s' "$detail" | sed 's/^[[:space:]]*//' 2>/dev/null | cut -c1-"$room" 2>/dev/null)
    fi
    printf '\r%s  %s%s%s %s %s%s%s %s%s' "${ESC}[K" "$ORANGE" "${frames:f:1}" "$R" "$label" \
      "$D" "$(elapsed $(( $(date +%s) - start )))" "$R" "$MUTED" "${detail}${R}"
    f=$(( (f + 1) % 10 )); tick=$((tick + 1))
    sleep 0.1
  done
  local rc=0; wait "$pid" || rc=$?
  printf '\r%s%s' "${ESC}[K" "${ESC}[?25h"
  if [ "$rc" = 0 ]; then
    printf '  %s✓%s %s %s%s%s\n' "$GREEN" "$R" "$label" "$D" "$(elapsed $(( $(date +%s) - start )))" "$R"
  else
    printf '  %s✗ %s%s\n' "$RED$B" "$label" "$R"
    if [ "$TASK_SOFT" = 1 ]; then return 1; fi
    echo
    local tail_lines   # this step's own output only (the log also holds earlier runs)
    tail_lines=$(awk '/^=== /{buf=""; next} {buf=buf $0 "\n"} END{printf "%s", buf}' "$LOG" \
      | strip_ansi | LC_ALL=C tr '\r' '\n' | LC_ALL=C grep -a -v '^[[:space:]]*$' | tail -n 20 \
      | iconv -c -f UTF-8 -t UTF-8 2>/dev/null | sed 's/^/      /')
    printf '%s\n' "$tail_lines"
    die "$label failed. The full log is in $LOG" "$tail_lines"
  fi
}

# ============================================================================================
# Install
# ============================================================================================
APPS_DIR=/Applications
[ -w /Applications ] || APPS_DIR="$HOME/Applications"
APP="$APPS_DIR/GoRunRun Local AI.app"

open_session
banner

# --- uninstall ------------------------------------------------------------------------------
if [ "$UNINSTALL" = 1 ]; then
  if [ "$DRY_RUN" = 1 ]; then
    out=$(card "Dry run: uninstalling would" \
      "• quit the app and remove its background service" \
      "• delete the app ($APP)" \
      "  and its settings and caches" \
      "• ask before deleting your chats and settings in" \
      "  $HOME_DIR" \
      "• leave the AI models in ~/.cache/huggingface and" \
      "  ~/.cache/gorunrun, so a reinstall doesn't download them" ; echo; info "Nothing was changed.")
    printf '%s\n' "$out"; close_session; keep "$(printf '\n%s\n' "$out")"; exit 0
  fi
  section "Uninstalling"
  osascript -e 'quit app "GoRunRun Local AI"' >/dev/null 2>&1 || true
  if [ -x "$HOME_DIR/scripts/install-agent.sh" ]; then "$HOME_DIR/scripts/install-agent.sh" --remove >/dev/null; fi
  ok "Removed the background service"
  rm -rf "$APP" && ok "Removed the app"
  # The app's own settings, browser data (login cookie, page state) and caches.
  # The program folder and your chats are handled below.
  id=ai.gorunrun.local.desktop
  defaults delete "$id" >/dev/null 2>&1 || true
  rm -rf "$HOME/Library/Preferences/$id.plist" "$HOME/Library/Caches/$id" "$HOME/Library/WebKit/$id" \
         "$HOME/Library/HTTPStorages/$id" "$HOME/Library/Saved Application State/$id.savedState"
  ok "Removed the app's settings and caches"
  kept=""
  if [ -d "$HOME_DIR" ]; then
    if [ "$INTERACTIVE" = 0 ] || confirm "Also delete your chats and settings?" no; then
      rm -rf "$HOME_DIR" && ok "Removed $HOME_DIR"
    else
      kept="Your chats and settings are still in $HOME_DIR"; info "$kept"
    fi
  fi
  out=$(echo; ok "GoRunRun Local AI was uninstalled"; [ -n "$kept" ] && info "$kept"
    info "The AI models stay in ~/.cache/huggingface (other AI apps may share it)"
    info "and in ~/.cache/gorunrun, so reinstalling doesn't download them again. Delete those"
    info "folders to free the space if nothing else uses them.")
  printf '%s\n' "$out"; close_session; keep "$(printf '%s\n' "$out")"; exit 0
fi

printf '\n  This sets up %sGoRunRun Local AI%s on this Mac: the tools it needs, the AI models\n' "$B" "$R"
printf '  you choose, and the app. Program files go to %s%s%s.\n' "$D" "$SHOWN_HOME" "$R"
[ "$DRY_RUN" = 1 ] && printf '\n  %sDry run: it asks its questions and shows the plan, but changes nothing.%s\n' "$YELLOW" "$R"

# --- 1. check the Mac -----------------------------------------------------------------------
section "Checking your Mac"
if [ -n "$SIM" ]; then   # --machine=M1Max-32GB: pretend to be that Mac
  sim_name=${SIM%-*}; mem_gb=${SIM##*-}; mem_gb=${mem_gb%[Gg][Bb]}
  case "$mem_gb" in ''|*[!0-9]*) die "--machine needs a memory size at the end, like --machine=M1Max-32GB" ;; esac
  case "$sim_name" in [Ii]ntel*) sys_arch=x86_64; chip="Intel Mac" ;;
    *) sys_arch=arm64; chip="Apple $(printf '%s' "$sim_name" | sed -E 's/-/ /g; s/([0-9])(Max|Pro|Ultra)/\1 \2/')" ;; esac
  sys_name=Darwin; os_ver=${SIM_MACOS:-$(sw_vers -productVersion)}
  warn "Simulating ${B}$chip with ${mem_gb} GB${R}: a fresh install, nothing on this Mac is used or changed"
else
  sys_name=$(uname -s); sys_arch=$(uname -m); os_ver=$(sw_vers -productVersion)
  chip=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "Apple silicon")
  mem_gb=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
fi
os_major=${os_ver%%.*}
[ "$sys_name" = Darwin ] || die "GoRunRun Local AI currently runs on macOS only. Windows and Linux support is planned."
[ "$sys_arch" = arm64 ] || die "An Apple Silicon Mac (M1 or newer) is required; Intel Macs can't run the AI models fast enough."
[ "$os_major" -ge 15 ] || die "macOS 15 or newer is required (this Mac has $os_ver)."
free_gb=$(( $(df -k "$HOME" | awk 'NR==2 {print $4}') / 1024 / 1024 ))
ok "$chip"
if [ "$os_major" -ge 26 ]; then ok "macOS $os_ver"; else warn "macOS $os_ver (tested on macOS 26; it should work)"; fi
[ "$mem_gb" -ge "$MIN_MEM_GB" ] || die "At least ${MIN_MEM_GB} GB of memory is required (this Mac has ${mem_gb} GB)."
ok "${mem_gb} GB memory"
ok "${free_gb} GB free disk space"

# What this Mac can run. Same tiers as the table on local.gorunrun.ai/macs:
#   32–47 GB  Gemma 4, no video
#   48–63 GB  Gemma 4; LTX-2.3 video optional (untested at this size)
#   64 GB +   everything (tested on 64 GB); 96 GB+ keeps the chat model loaded during Wan renders
CAN_QWEN=0; CAN_LTX=0; CAN_WAN=0; TESTED=0
if [ "$mem_gb" -ge 64 ]; then CAN_QWEN=1; CAN_LTX=1; CAN_WAN=1; elif [ "$mem_gb" -ge 48 ]; then CAN_LTX=1; fi
if [ "$mem_gb" -ge 64 ] && [ "$mem_gb" -lt 96 ]; then TESTED=1; fi
if [ "$TESTED" = 1 ]; then ok "Tested configuration for ${mem_gb} GB Macs"
else warn "Untested memory size. The setup below is our best estimate; please tell us how it goes"
  printf '    %s\n' "(local.gorunrun.ai/macs)."; fi

# --- 2. choices ---------------------------------------------------------------------------------
section "Your setup"
LOCAL_CFG="$HOME_DIR/config/models.local.yaml"
if [ -z "$MODEL" ] && [ -f "$LOCAL_CFG" ] && grep -q "llm: gemma-4-12b" "$LOCAL_CFG"; then
  MODEL=gemma   # updating: keep the earlier choice unless one was passed
fi
if [ "$CAN_QWEN" = 0 ]; then
  [ "$MODEL" = qwen ] && warn "Qwen 3.5 needs 64 GB of memory and this Mac has ${mem_gb} GB, so it can't be installed here."
  MODEL=gemma
  ok "AI model  ${B}Gemma 4 (12B)${R}"
  info "Qwen 3.5 isn't offered: it needs 64 GB of memory (this Mac has ${mem_gb} GB)."
elif [ -z "$MODEL" ]; then
  pick=0; select_one pick "Which AI model should it use?" 0 \
    "Qwen 3.5 (35B)~recommended|The most capable answers. Comes with Gemma 4, which listens to voice messages (Qwen can't hear audio). About 32 GB in all, 20 GB of memory." \
    "Gemma 4 (12B)|Lighter, quicker to install, good for everyday use. 13 GB; 8 GB of memory."
  if [ "$pick" = 1 ]; then MODEL=gemma; else MODEL=qwen; fi
else
  if [ "$MODEL" = gemma ]; then ok "AI model  ${B}Gemma 4 (12B)${R}"; else ok "AI model  ${B}Qwen 3.5 (35B)${R}"; fi
fi

# An install that finished (the last step leaves a marker; installs from before the marker have the
# app). A first install that was stopped part way still counts as new, so it gets new-install defaults.
INSTALLED=0
if [ -d "$HOME_DIR/.git" ] && { [ -f "$HOME_DIR/.install-complete" ] || [ -d "$APP" ]; }; then INSTALLED=1; fi

# Video engines an earlier run already set up (updates keep them).
HAVE_VIDEO=""
if [ -x "$HOME_DIR/videogen/.venv/bin/python" ]; then
  HF_HUB="${HF_HOME:-$HOME/.cache/huggingface}/hub"
  if compgen -G "$HF_HUB/models--dgrauet--ltx-2.3-mlx-q4/snapshots/*/transformer-distilled-1.1.safetensors" >/dev/null; then
    HAVE_VIDEO="LTX-2.3"
  fi
  if [ -f "${GORUNRUN_MODEL_CACHE:-$HOME/.cache/gorunrun/models}/video/wan2.2-ti2v-5b-mlx/config.json" ] ||
     [ -f "$HOME_DIR/models/video/wan2.2-ti2v-5b-mlx/config.json" ]; then
    HAVE_VIDEO="${HAVE_VIDEO:+$HAVE_VIDEO, }Wan 2.2"
  fi
fi

confirm_wan_only() {  # Wan makes silent video; make sure that's what the person wants
  echo
  warn "${B}Wan 2.2 makes video without any sound.${R}"
  printf '    %s\n' "Clips are silent: no speech, music or sound effects, and a person in a" \
    "photo can't be made to talk. Only LTX-2.3 creates sound."
  confirm "Install Wan 2.2 only, without sound?" no
}

# video_menu "Question" INCLUDE_NONE PRESELECTED: only the engines this Mac can run are listed
video_menu() {
  local question=$1 none=$2 want=${3:-ltx} first=0 i key ltx_tag=recommended
  local keys=() items=()
  [ "$mem_gb" -lt 64 ] && ltx_tag="untested at ${mem_gb} GB"
  if [ "$CAN_LTX" = 1 ]; then keys+=(ltx)
    items+=("LTX-2.3~$ltx_tag|Short videos with matching sound and speech, about a minute each. 28 GB."); fi
  if [ "$CAN_WAN" = 1 ]; then keys+=(both wan)
    items+=("LTX-2.3 and Wan 2.2|Also adds Wan: sharper but silent video, 6–7 minutes each. 24 GB more."
            "Wan 2.2 only~no sound|Sharp video with no sound at all, 6–7 minutes per clip. 24 GB."); fi
  if [ "$none" = 1 ]; then keys+=(none); items+=("No video creation|Skip it for now. Run the installer again to add it later."); fi
  for ((i = 0; i < ${#keys[@]}; i++)); do [ "${keys[$i]}" = "$want" ] && first=$i; done
  while :; do
    vpick=0; select_one vpick "$question" "$first" "${items[@]}"
    key=${keys[$vpick]}
    case "$key" in
      wan) if confirm_wan_only; then VIDEO=wan; return; fi ;;   # declined: show the menu again
      none) VIDEO=""; return ;;
      *) VIDEO=$key; return ;;
    esac
  done
}

if [ "$CAN_LTX" = 0 ]; then
  [ -n "$VIDEO" ] || [ "$WITH_VIDEO" = 1 ] && warn "Video creation needs at least 48 GB of memory and this Mac has ${mem_gb} GB, so it can't be installed here."
  VIDEO=""
  info "Video creation isn't offered: it needs 48 GB of memory or more (this Mac has ${mem_gb} GB)."
else
  if [ "$CAN_WAN" = 0 ]; then
    case "$VIDEO" in
      wan|both) warn "Wan 2.2 needs 64 GB of memory and this Mac has ${mem_gb} GB, so it can't be installed here."
        if [ "$VIDEO" = both ]; then VIDEO=ltx; else VIDEO=""; NO_VIDEO=1; fi ;;
    esac
    info "Wan 2.2 isn't offered: it needs 64 GB of memory (this Mac has ${mem_gb} GB)."
  fi
  if [ -n "$HAVE_VIDEO" ] && [ "$WITH_VIDEO" = 0 ]; then
    ok "Video creation  ${B}already set up (${HAVE_VIDEO})${R}, kept"
    if [ "$NO_VIDEO" = 0 ] && [ "$INTERACTIVE" = 1 ] && confirm "Add a video engine?" no; then
      video_menu "Which video engine to add?" 0 ltx   # engines already set up stay installed
    fi
  elif [ "$NO_VIDEO" = 1 ]; then
    VIDEO=""
  elif [ -z "$VIDEO" ] && [ "$WITH_VIDEO" = 0 ] && { [ "$INSTALLED" = 1 ] || [ "$CAN_WAN" = 0 ]; }; then
    VIDEO=""   # updating an install without video, or a 48 GB Mac (untested): off unless picked
    if [ "$INTERACTIVE" = 1 ]; then video_menu "Video creation" 1 none; fi
  elif [ -z "$VIDEO" ]; then
    VIDEO=ltx  # new installs on 64 GB+ include it by default, with sound
    if [ "$INTERACTIVE" = 1 ]; then video_menu "Video creation" 1 ltx; fi
  elif [ "$VIDEO" = wan ]; then   # chosen with --video=wan: same warning (shown, not asked, with --yes)
    confirm_wan_only || die "Nothing was installed. Run it again with --video=ltx or --video=both for video with sound."
  fi
fi
case "$VIDEO" in
  ltx) VIDEO_IDS="ltx-2.3"; video_disk=30; video_text="LTX-2.3 (about 28 GB)" ;;
  wan) VIDEO_IDS="wan-2.2-5b"; video_disk=60; video_text="Wan 2.2 only, no sound (about 24 GB)" ;;
  both) VIDEO_IDS="ltx-2.3 wan-2.2-5b"; video_disk=90; video_text="LTX-2.3 and Wan 2.2 (about 52 GB)" ;;
  *) VIDEO_IDS=""; video_disk=0
     if [ -n "$HAVE_VIDEO" ] && [ "$CAN_LTX" = 1 ]; then video_text="keep what's set up (${HAVE_VIDEO})"
     else video_text="not included"; fi ;;
esac
if [ "$MODEL" = gemma ]; then need_disk=20; model_text="Gemma 4 (12B), about 13 GB"
else need_disk=40; model_text="Qwen 3.5 (35B) + Gemma 4 for voice messages, about 32 GB"; fi
need_disk=$((need_disk + video_disk))

# This Mac's choices, merged over config/models.yaml (the file isn't tracked, so updates stay clean).
local_config() {
  local budget=$(( mem_gb * 7 / 10 ))   # leaves ~30% for macOS and other apps
  echo "# Written by install.sh for this Mac. Delete this file to use config/models.yaml as is."
  echo "memory_budget_gb: $budget"
  local wan_default=0
  if [ "$VIDEO" = wan ] || { [ -z "$VIDEO" ] && [ "$HAVE_VIDEO" = "Wan 2.2" ]; }; then wan_default=1; fi
  if [ "$MODEL" = gemma ] || [ "$wan_default" = 1 ]; then echo "defaults:"; fi
  if [ "$MODEL" = gemma ]; then
    echo "  llm: gemma-4-12b"
    echo "  title_llm: gemma-4-12b"
    echo "  audio_listener: gemma-4-12b"
  fi
  if [ "$wan_default" = 1 ]; then echo "  video: wan-2.2-5b"; fi
  if [ "$MODEL" = gemma ]; then
    echo "llms:"
    echo "  gemma-4-12b:"
    echo "    ttl_s: 0   # the main model on this Mac: keep it loaded, like Qwen elsewhere"
  fi
}

# --- 3. review ------------------------------------------------------------------------------------
missing=""
xcode-select -p >/dev/null 2>&1 || missing="$missing Apple's command line tools,"
command -v brew >/dev/null 2>&1 || [ -x /opt/homebrew/bin/brew ] || missing="$missing Homebrew,"
for tool in git ffmpeg uv node; do command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool,"; done
if [ -n "$missing" ]; then tools_text="${missing# }"; tools_text="${tools_text%,}"; else tools_text="all present"; fi
if [ "$INSTALLED" = 1 ]; then files_text="update $SHOWN_HOME"
elif [ -d "$HOME_DIR/.git" ]; then files_text="finish the setup in $SHOWN_HOME"
else files_text="new, in $SHOWN_HOME"; fi
if [ "$TESTED" = 1 ]; then mac_text="$chip, ${mem_gb} GB (tested)"
else mac_text="$chip, ${mem_gb} GB ${YELLOW}(untested: please report)${R}"; fi
if [ "$free_gb" -ge "$need_disk" ]; then disk_text="about ${need_disk} GB of ${free_gb} GB free"
else disk_text="${RED}about ${need_disk} GB needed, only ${free_gb} GB free${R}"; fi

section "Review"
lab() { printf '%s%s%s' "$MUTED" "$(pad "$1" 12)" "$R"; }
section_card() {
card "$( [ "$DRY_RUN" = 1 ] && echo "Dry run: a real install would" || echo "Ready to install" )" \
  "$(lab "Mac")$mac_text" \
  "$(lab "AI model")$model_text" \
  "$(lab "Video")$video_text" \
  "$(lab "Tools")$tools_text" \
  "$(lab "Program")$files_text" \
  "$(lab "App")$APP" \
  "$(lab "Disk")$disk_text" \
  "" \
  "${D}Plus speech, search and memory models, and private web search.${R}"
}
section_card

if [ "$DRY_RUN" = 1 ]; then
  out=$(echo; info "Settings it would save (config/models.local.yaml in the program folder):"
    local_config | fold -s -w $((COLS - 8)) | sed "s/^/      ${D}/;s/\$/${R}/"
    echo; info "Dry run: nothing was changed.")
  printf '%s\n' "$out"; close_session
  keep "$(printf '\n'; section_card; printf '%s\n' "$out")"; exit 0
fi
[ "$free_gb" -ge "$need_disk" ] || die "About ${need_disk} GB of free disk space is needed (${free_gb} GB free)."
echo
confirm "Install now?" yes || { keep "$(printf '\n'; info "Installation cancelled. Nothing was changed.")"; exit 0; }
case "$MODEL" in gemma) models_size="about 13 GB" ;; *) models_size="about 32 GB" ;; esac

# --- 4. install -----------------------------------------------------------------------------------
section "Installing"
info "Details go to ~/.gorunrun/install.log. Big downloads take a while."
echo

wait_for_clt() { until xcode-select -p >/dev/null 2>&1; do sleep 5; done; }
if ! xcode-select -p >/dev/null 2>&1; then
  info "Apple's command line tools are needed: a window opens, click ${B}Install${R} and wait."
  xcode-select --install >/dev/null 2>&1 || true
  task "Installing Apple's command line tools" wait_for_clt
fi
if ! command -v brew >/dev/null 2>&1; then
  if ! [ -x /opt/homebrew/bin/brew ]; then
    info "Installing Homebrew, the standard Mac package manager. It may ask for your Mac password."
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" </dev/tty
    ok "Homebrew"
  fi
  eval "$(/opt/homebrew/bin/brew shellenv)"
fi
command -v git >/dev/null 2>&1 || task "Installing git" brew install git

get_program() {
  if [ -d "$HOME_DIR/.git" ]; then
    git -C "$HOME_DIR" remote set-url origin "$REPO"   # follow GORUNRUN_REPO if it changed
    git -C "$HOME_DIR" fetch -q origin "$BRANCH"
    git -C "$HOME_DIR" checkout -q "$BRANCH"
    git -C "$HOME_DIR" merge -q --ff-only "origin/$BRANCH" \
      || { echo "$HOME_DIR has local changes; move them aside and run the installer again."; return 1; }
  else
    mkdir -p "$(dirname "$HOME_DIR")"
    git clone -q --branch "$BRANCH" "$REPO" "$HOME_DIR"
  fi
}
task "Getting GoRunRun Local AI" get_program
cd "$HOME_DIR"
local_config > config/models.local.yaml

task "Installing tools (ffmpeg, uv, Node.js)" make system-deps
task "Setting up Python" make deps
task "Getting the model server" make llama-swap
export GORUNRUN_PROGRESS=1     # the downloader reports bytes, for the progress bar
task "Downloading AI models ($models_size)" make models
task "Setting up private web search" make search-setup
task "Building the app" make app-frontend
if [ -n "$VIDEO_IDS" ]; then
  task "Setting up the video engines" make video-setup
  video_size=${video_text#* (}; video_size=${video_size%)}
  task "Downloading video models ($video_size)" make models-video VIDEO="$VIDEO_IDS"
fi
osascript -e 'quit app "GoRunRun Local AI"' >/dev/null 2>&1 || true
# The Mac app is compiled here, with Apple's command line tools. If that fails, everything else still
# works in the browser: set up the background service without the app and start it.
APP_OK=1
TASK_SOFT=1; task "Installing the GoRunRun Local AI app" make desktop-install || APP_OK=0; TASK_SOFT=0
AGENT="gui/$(id -u)/ai.gorunrun.local"
if [ "$APP_OK" = 0 ]; then
  app_log=$(awk '/^=== /{buf=""; next} {buf=buf $0 "\n"} END{printf "%s", buf}' "$LOG")
  wait_for_backend() {
    launchctl kickstart "$AGENT"
    for _ in $(seq 1 120); do curl -fs -o /dev/null http://127.0.0.1:8000/api/health && return 0; sleep 1; done
    echo "The backend didn't answer within 2 minutes; see $HOME_DIR/data/logs/backend.log"; return 1
  }
  task "Setting up the background service" ./scripts/install-agent.sh
  task "Starting GoRunRun Local AI" wait_for_backend
fi
touch "$HOME_DIR/.install-complete"

# --- 5. done ----------------------------------------------------------------------------------------
if [ "$APP_OK" = 1 ]; then
  ready=$(card "${GREEN}✓${R}${B} GoRunRun Local AI is ready${R}" \
    "$(lab "Open")GoRunRun Local AI in Applications (opening now)" \
    "$(lab "Browser")http://127.0.0.1:8000 while the app is open" \
    "$(lab "Guides")https://local.gorunrun.ai" \
    "$(lab "Update")run this installer again" \
    "$(lab "Uninstall")add ${B}--uninstall${R} to the install command")
else
  if printf '%s' "$app_log" | grep -q -e "SDK is not supported by the compiler" -e "redefinition of module 'SwiftBridging'"; then
    why=("Apple's command line tools on this Mac are damaged, so the Mac" "app couldn't be built. To fix them, run in Terminal:"
         "  sudo rm -rf /Library/Developer/CommandLineTools" "  xcode-select --install")
  else
    why=("The Mac app couldn't be built (details in ~/.gorunrun/install.log).")
  fi
  ready=$(card "${GREEN}✓${R}${B} GoRunRun Local AI is ready, in your browser${R}" \
    "$(lab "Open")http://127.0.0.1:8000 (opening now)" \
    "$(lab "Start")launchctl kickstart $AGENT" \
    "$(lab "")${D}(after a restart or log out; it keeps running until then)${R}" \
    "$(lab "Stop")launchctl kill TERM $AGENT" \
    "$(lab "Guides")https://local.gorunrun.ai" \
    "$(lab "Uninstall")add ${B}--uninstall${R} to the install command" \
    "" "${why[@]}" "Then run this installer again to add the Mac app.")
fi
printf '\n%s\n' "$ready"
if [ "$APP_OK" = 1 ]; then open "$APP"; else open http://127.0.0.1:8000; fi
close_session
keep "$(printf '\n%s\n' "$ready")"
