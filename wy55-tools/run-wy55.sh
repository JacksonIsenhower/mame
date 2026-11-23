#!/usr/bin/env bash
# Run the emulated WY-55 in a window, with its host (modem) port on a pseudo-terminal.
#
#   wy55-tools/run-wy55.sh [extra MAME options...]
#
# The host side of the serial line appears at $WY55_TTY (default ~/.local/state/wy55/tty). Attach programs to it
# as you would to the USB-serial adapter, for example a shell:
#
#   wy55-tools/wy55-shell.sh            # login shell on the emulated terminal
#
# The line runs at 9600 8N1 on both sides by default (the WY-55's factory setting). To match the real setup
# (115200), change Ports -> Mdm Baud Rate in the terminal's setup and the null modem's TX/RX baud in MAME's
# Machine Configuration menu (Tab), then keep both.
#
# Keys (MAME's WY-60 ASCII keyboard): Esc = SetUp, ` = Wyse Esc, Left Alt = Funct. On a fresh NVRAM the terminal
# waits for SetUp before it comes up. Scroll Lock toggles between typing into the terminal and MAME's own keys.
set -euo pipefail

here=$(cd "$(dirname "$0")/.." && pwd)
state=${XDG_STATE_HOME:-$HOME/.local/state}/wy55
tty=${WY55_TTY:-$state/tty}
port=${WY55_PORT:-23555}
mkdir -p "$state"

command -v socat >/dev/null || { echo "socat is needed (pacman -S socat)" >&2; exit 1; }

# socat listens for MAME's socket bitbanger and bridges it to a pty at $tty
socat -d -d "pty,link=$tty,rawer" "tcp-listen:$port,bind=127.0.0.1,reuseaddr" 2>"$state/socat.log" &
socat_pid=$!
trap 'kill $socat_pid 2>/dev/null || true' EXIT
for _ in $(seq 50); do
	[[ -e $tty ]] && break
	sleep 0.1
done
echo "WY-55 host port: $tty"

cd "$here"
./wy55 wy55 -rompath roms -nvram_directory "$state/nvram" -cfg_directory "$state/cfg" \
	-snapshot_directory "$state/snap" -host null_modem -bitb "socket.127.0.0.1:$port" "$@"
