#!/usr/bin/env bash
# Run an interactive shell on the emulated WY-55's host port (started by run-wy55.sh).
#
#   wy55-tools/wy55-shell.sh [TERM] [ROWS COLS]
#
# The defaults match run-wy55.sh's default setup (VT220-7, 132x42); with WY55_PRESET=factory use wy50 24 80.
# For a login prompt instead of a shell, use wy55-getty.sh.
set -euo pipefail

tty=${WY55_TTY:-${XDG_STATE_HOME:-$HOME/.local/state}/wy55/tty}
term=${1:-vt220}
rows=${2:-42}
cols=${3:-132}
[[ -e $tty ]] || { echo "no $tty: start run-wy55.sh first" >&2; exit 1; }

# The pty's other end carries the bytes to the emulated UART; set the shell-side line discipline here
stty -F "$tty" sane rows "$rows" cols "$cols"
exec setsid -c -w env TERM="$term" bash -l <"$tty" >"$tty" 2>&1
