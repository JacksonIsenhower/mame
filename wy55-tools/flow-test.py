#!/usr/bin/env python3
"""End-to-end flow control on the emulated WY-55: flood it with numbered lines and see whether any are lost.

    flow-test.py WORK_DIR [--lines N] [--rom wy55|wy55p] [-j N]

Each case boots the VT220-7 / 132x42 / 115200 NVRAM with the given Setup changes, sends N numbered lines at
once from the host side, and when the terminal has caught up reads the screen back: it should show the last
41 numbers in order, then the empty line the cursor is on. The host side stops sending while the line named by "flow" is off
(harness.lua's WY55_FLOW), as a host with CTS flow control does through a cable bringing the terminal's DTR
(stock firmware) or RTS (the RTS patch) to its CTS. The DTR/RTS changes are counted from the harness log.
"""
import argparse
import concurrent.futures
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import threading
import time

MAME = pathlib.Path(__file__).resolve().parent.parent
TOOLS = MAME / "wy55-tools"

# (name, Setup changes, host flow control, expected: lines lost?)
CASES = [
    ("no handshake", ["handshake=none"], "off", True),
    ("DTR handshake, host ignores it", ["handshake=dtr"], "off", True),
    ("DTR handshake, host on DTR", ["handshake=dtr"], "dtr", False),
    ("DTR handshake, host on RTS", ["handshake=dtr"], "rts", None),   # stock: lost; RTS patch: none lost
]


def screen_lines(ram0):
    out = []
    for i in range(1, 43):
        base = (ram0[0x1F00 + 2 * i] << 8 | ram0[0x1F01 + 2 * i]) & 0x1FFF
        out.append(bytes(ram0[base + 1:base + 133]).decode("latin-1").rstrip(" \0"))
    return out


def run(name, setup, flow, nlines, rom, work):
    d = work / name.replace(" ", "_").replace(",", "")
    subprocess.run([sys.executable, str(TOOLS / "wysetup.py"), str(d / "nvram"), "--from",
                    str(TOOLS / "nvram/vt220-132x42-115200"), *setup], check=True, stdout=subprocess.DEVNULL)
    if rom != "wy55":   # MAME keeps a clone's NVRAM under its own name
        shutil.copytree(d / "nvram/wy55", d / "nvram" / rom, dirs_exist_ok=True)
    srv = socket.create_server(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    data = "".join(f"line {i:05d}\r\n" for i in range(nlines)).encode()

    def host():
        conn, _ = srv.accept()
        time.sleep(5)
        conn.sendall(data)
        try:
            while conn.recv(4096):
                pass
        except OSError:
            pass

    threading.Thread(target=host, daemon=True).start()
    # Enough frames for the data at the terminal's own pace (about 2400 characters a second)
    frames = 70 * (8 + len(data) // 2000)
    env = dict(os.environ, WY55_OUT=str(d), WY55_FRAMES=str(frames), WY55_BAUD="115200", WY55_FLOW=flow,
               WY55_LINES="1")
    subprocess.run(
        [str(MAME / "wy55"), rom, "-rompath", str(MAME / "roms"), "-video", "none", "-sound", "none",
         "-skip_gameinfo", "-nvram_directory", str(d / "nvram"), "-snapshot_directory", str(d / "snap"),
         "-cfg_directory", str(d / "cfg"), "-host", "null_modem", "-bitb", f"socket.127.0.0.1:{port}",
         "-autoboot_script", str(TOOLS / "harness.lua")],
        cwd=MAME, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    lines = screen_lines((d / "ram0.bin").read_bytes())
    # The last line ends with CR LF, so the cursor sits on an empty bottom line
    want = [f"line {i:05d}" for i in range(nlines - 41, nlines)] + [""]
    log = (d / "personality.log").read_text().splitlines()
    # Times each line went off after power-up (frame 1 logs the reset state, both off)
    stops = {k: sum(1 for line in log if f"{k}=off" in line and not line.startswith("frame 1:")) for k in ("DTR", "RTS")}
    return lines == want, stops


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work", type=pathlib.Path)
    ap.add_argument("--lines", type=int, default=600)
    ap.add_argument("--rom", default="wy55")
    ap.add_argument("-j", type=int, default=4)
    args = ap.parse_args()
    with concurrent.futures.ThreadPoolExecutor(args.j) as ex:
        jobs = [(c, ex.submit(run, c[0], c[1], c[2], args.lines, args.rom, args.work)) for c in CASES]
        for (name, _, flow, lossy), job in jobs:
            intact, stops = job.result()
            # Whether each line went off at all; how many times depends on timing by a frame or two
            held = ", ".join(f"{k} {'went off' if n else 'stayed on'}" for k, n in stops.items())
            print(f"{name:32} {'all lines arrived' if intact else 'LINES LOST':17}  {held}")


if __name__ == "__main__":
    main()
