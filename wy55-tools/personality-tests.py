#!/usr/bin/env python3
"""Check which host sequences switch the emulated WY-55's personality.

    personality-tests.py NVRAM_DIR WORK_DIR [-j N]

NVRAM_DIR holds a wy55/nvram0+nvram1 pair saved after first-boot setup. Each test boots a fresh copy, sends
its sequences from the host side once the terminal is up, and records every change of the personality byte.
"""
import argparse
import concurrent.futures
import os
import pathlib
import shutil
import socket
import subprocess
import threading
import time

MAME = pathlib.Path(__file__).resolve().parent.parent
ESC = "\x1b"
ROM = "wy55"          # MAME system: wy55, or wy55p for the patched firmware (--rom)
CSI = ESC + "["

# (name, [sequences sent 1 s apart, starting 5 s after power-up])
TESTS = [
    ("TVI 925, then ESC ~ 1", [ESC + "~$", ESC + "~1"]),
    ("TVI 925, then ESC ~ 0", [ESC + "~$", ESC + "~0"]),
    ("VT100, then CSI 93 % p, CSI 49 % p", [ESC + "~;", CSI + "93%p", CSI + "49%p"]),
    ("PC Term, then ESC ~ 1", [ESC + "~5", ESC + "~1"]),
]


def run_test(index, name, seqs, nvram, work):
    d = work / f"t{index:02d}"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    shutil.copytree(nvram, d / "nvram")
    if ROM != "wy55":   # MAME keeps a clone's NVRAM under its own name
        shutil.copytree(d / "nvram/wy55", d / "nvram" / ROM, dirs_exist_ok=True)
    srv = socket.create_server(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    received = bytearray()

    def host():
        conn, _ = srv.accept()
        conn.settimeout(0.2)
        start = time.monotonic()
        pending = list(seqs)
        next_at = 5.0
        while True:
            if pending and time.monotonic() - start >= next_at:
                conn.sendall(pending.pop(0).encode("latin-1"))
                next_at += 1.0
            try:
                data = conn.recv(4096)
                if not data:
                    break
                received.extend(data)
            except socket.timeout:
                pass
            except OSError:
                break

    t = threading.Thread(target=host, daemon=True)
    t.start()
    env = dict(os.environ, WY55_OUT=str(d), WY55_FRAMES=str(70 * (6 + len(seqs) + 2)))
    subprocess.run(
        [str(MAME / "wy55"), ROM, "-rompath", str(MAME / "roms"), "-video", "none", "-sound", "none",
         "-skip_gameinfo", "-nvram_directory", str(d / "nvram"), "-snapshot_directory", str(d / "snap"),
         "-cfg_directory", str(d / "cfg"), "-host", "null_modem", "-bitb", f"socket.127.0.0.1:{port}",
         "-autoboot_script", str(MAME / "wy55-tools/harness.lua")],
        cwd=MAME, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    log = (d / "personality.log").read_text().splitlines()
    # Skip the power-up lines (frame 1 reads 00 before the firmware sets it)
    changes = [line.split(": ", 1)[1] for line in log if int(line.split()[1].rstrip(":")) > 60]
    return name, changes, bytes(received)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("nvram", type=pathlib.Path)
    ap.add_argument("work", type=pathlib.Path)
    ap.add_argument("-j", type=int, default=4)
    ap.add_argument("--rom", default="wy55", help="wy55, or wy55p for the patched firmware")
    args = ap.parse_args()
    global ROM
    ROM = args.rom
    with concurrent.futures.ThreadPoolExecutor(args.j) as ex:
        futures = [ex.submit(run_test, i, n, s, args.nvram, args.work) for i, (n, s) in enumerate(TESTS)]
        for f in futures:
            name, changes, received = f.result()
            print(f"{name:28} -> {' -> '.join(changes) if changes else '(no change: stays WYSE 50)'}"
                  + (f"   [sent back {received!r}]" if received.strip(b"\x06") else ""))


if __name__ == "__main__":
    main()
