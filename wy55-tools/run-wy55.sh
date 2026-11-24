#!/usr/bin/env bash
# Run the emulated WY-55 in a window, with its host (modem) port on a pseudo-terminal.
#
#   wy55-tools/run-wy55.sh [extra MAME options...]
#
# The host side of the serial line appears at $WY55_TTY (default ~/.local/state/wy55/tty). Attach programs to it
# as you would to the USB-serial adapter, for example a shell:
#
#   wy55-tools/wy55-shell.sh            # login shell on the emulated terminal
#   wy55-tools/wy55-getty.sh            # or instead of run-wy55.sh: the emulator with agetty on its port
#
# On first run the NVRAM is seeded from wy55-tools/nvram/$WY55_PRESET (default vt220-132x42-115200, the real
# terminal's setup: VT220-7, 132x42, 115200 8N1; "factory" is the factory defaults at 9600). wysetup.py changes
# Setup values in $state/nvram without the menus. MAME's side of the line runs at $WY55_BAUD (default 115200);
# keep it equal to the Setup's Mdm Baud Rate. MAME's side stops sending while the terminal holds DTR off
# ($WY55_FLOW: dtr by default, rts for the RTS patch, off), so with Setup's Mdm Rc Hsk at DTR
# (wysetup.py handshake=dtr) programs writing to the pty block instead of losing characters.
#
# WY55_ROM=wy55p runs the patched firmware: roms/wy55p/wy55p.bin, a link to tmux-wy55's build/251352-12.bin
# (python3 -I tools/wy55-patch.py there). Its NVRAM is kept apart from the original's.
#
# Keys (MAME's WY-60 ASCII keyboard): Esc = SetUp, ` = Wyse Esc, Left Alt = Funct. On a fresh NVRAM the terminal
# waits for SetUp before it comes up. Scroll Lock toggles between typing into the terminal and MAME's own keys.
set -euo pipefail

here=$(cd "$(dirname "$0")/.." && pwd)
state=${XDG_STATE_HOME:-$HOME/.local/state}/wy55
tty=${WY55_TTY:-$state/tty}
port=${WY55_PORT:-23555}
preset=${WY55_PRESET:-vt220-132x42-115200}
export WY55_BAUD=${WY55_BAUD:-115200} WY55_FLOW=${WY55_FLOW:-dtr} WY55_FRAMES=0 WY55_OUT=$state
mkdir -p "$state"
rom=${WY55_ROM:-wy55}
if [[ ! -e $state/nvram/$rom/nvram1 ]]; then
	mkdir -p "$state/nvram/$rom"
	cp "$here/wy55-tools/nvram/$preset/wy55/"nvram[01] "$state/nvram/$rom/"
fi

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
./wy55 "$rom" -rompath roms -nvram_directory "$state/nvram" -cfg_directory "$state/cfg" \
	-snapshot_directory "$state/snap" -host null_modem -bitb "socket.127.0.0.1:$port" \
	-autoboot_script "$here/wy55-tools/harness.lua" "$@"
