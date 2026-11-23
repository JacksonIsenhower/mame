#!/usr/bin/env bash
# Run an interactive shell on the emulated WY-55's host port (started by run-wy55.sh).
#
#   wy55-tools/wy55-shell.sh [TERM] [ROWS COLS]
#
# TERM defaults to wy50 (the WY-55's factory personality); use vt220 after switching the terminal to VT220.
set -euo pipefail

tty=${WY55_TTY:-${XDG_STATE_HOME:-$HOME/.local/state}/wy55/tty}
term=${1:-wy50}
rows=${2:-24}
cols=${3:-80}
[[ -e $tty ]] || { echo "no $tty: start run-wy55.sh first" >&2; exit 1; }

# The pty's other end carries the bytes to the emulated UART; set the shell-side line discipline here
stty -F "$tty" sane rows "$rows" cols "$cols"
exec setsid -c -w env TERM="$term" bash -l <"$tty" >"$tty" 2>&1
