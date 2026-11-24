#!/usr/bin/env bash
# Run the emulated WY-55 with a Linux login prompt (agetty) on its host port, like a real terminal on a serial line.
#
#   wy55-tools/wy55-getty.sh [extra MAME options...]
#
# Starts run-wy55.sh (same settings and environment variables: WY55_ROM, WY55_PRESET, WY55_BAUD, WY55_FLOW, ...),
# waits for its pty, and runs agetty on it with sudo, which asks for your password: agetty and login need root.
# After you log out agetty starts again with a fresh prompt; closing the MAME window ends everything.
#
# The terminal type and screen size given to the login session match the default setup (VT220-7, 132x42); set
# WY55_TERM, WY55_ROWS and WY55_COLS if the terminal's Setup differs.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
state=${XDG_STATE_HOME:-$HOME/.local/state}/wy55
tty=${WY55_TTY:-$state/tty}
term=${WY55_TERM:-vt220}
rows=${WY55_ROWS:-42}
cols=${WY55_COLS:-132}
agetty=$(command -v agetty || echo /sbin/agetty)

# Ask for the password now, before the MAME window opens
sudo -v

rm -f "$tty"
"$here/run-wy55.sh" "$@" &
emu=$!
trap 'kill $emu 2>/dev/null || true' EXIT
for _ in $(seq 100); do
	[[ -e $tty ]] && break
	sleep 0.1
done
[[ -e $tty ]] || { echo "the emulator's pty didn't appear at $tty" >&2; exit 1; }
# agetty wants the real device (/dev/pts/N), not the link
pts=$(readlink -f "$tty")
echo "agetty on $pts ($term, ${cols}x$rows)"

# Give the terminal time to finish its power-up self test, so the prompt isn't lost
sleep 4
while kill -0 $emu 2>/dev/null; do
	# A pty has no baud rate or carrier: -L ignores carrier, -8 passes 8-bit characters. The window size isn't part
	# of termios, so set it on the pty each time (it lasts while the emulator holds the pty open).
	stty -F "$pts" rows "$rows" cols "$cols" 2>/dev/null || true
	sudo "$agetty" --noclear -L -8 "$pts" "${WY55_BAUD:-115200}" "$term" || sleep 1
done
