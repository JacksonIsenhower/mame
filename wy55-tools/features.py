#!/usr/bin/env python3
"""Measure which features each WY-55 personality supports, on the emulated firmware.

    features.py NVRAM_DIR WORK_DIR [-j N] [--only NAME,...] [--probe NAME,...]

NVRAM_DIR holds a wy55/nvram0+nvram1 pair saved after first-boot setup (Wyse 50). For every personality
it boots once with no probe (the baseline) and once per probe, sending the probe in that personality's own
command syntax, then judges the effect from registers, RAM, font RAM or what the terminal sent back.
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
CSI = ESC + "["

# name: (family, setup sequences from Wyse 50, internal RAM pokes before them)
PERSONALITIES = {
    "Wyse 50": ("wyse", [], ""),
    "Wyse 50+": ("wyse", [ESC + '~"'], ""),
    "Wyse 60": ("wyse", [ESC + "~4"], ""),
    "Wyse 120": ("wyse", [ESC + "~4"], "300:7b:24"),
    "Wyse 120+": ("wyse", [ESC + "~4"], "300:7b:34"),
    "Wyse 150": ("wyse", [ESC + "~4"], "300:7b:25"),
    "Wyse 150+": ("wyse", [ESC + "~4"], "300:7b:35"),
    "TVI 910+": ("wyse", [ESC + "~#"], ""),
    "TVI 925": ("wyse", [ESC + "~$"], ""),
    "TVI 905": ("wyse", [ESC + "~b"], ""),
    "PC Term": ("wyse", [ESC + "~5"], ""),
    "ADDS A2": ("adds", [ESC + "~%"], ""),
    "HZ 1500": ("hz", [ESC + "~&"], ""),
    "VT52": ("vt52", [ESC + "~6"], ""),
    "VT100": ("ansi", [ESC + "~;"], ""),
    "VT220-7": ("ansi", [ESC + "~<"], ""),
    "VT220-8": ("ansi", [ESC + "~="], ""),
    "UNIX Console": ("ansi", [ESC + "~]"], ""),
}


def wy_at(row, col):
    return ESC + "=" + chr(0x20 + row) + chr(0x20 + col)


def ansi_at(row, col):
    return CSI + f"{row + 1};{col + 1}H"


CLEAR = {"wyse": ESC + "+", "ansi": CSI + "2J" + CSI + "H", "vt52": ESC + "H" + ESC + "J", "adds": "\x0c", "hz": "~\x1c"}
LINES = "A\r\nB\r\nC\r\nD\r\nE\r\nF"
GLYPH = "".join("FF" if i % 2 else "81" for i in range(16))

# probe name -> family -> sequence (sent after a clear)
PROBES = {
    "base": {f: "" for f in CLEAR},
    "cols132": {"wyse": ESC + "`;", "ansi": CSI + "?3h"},
    "lines42": {"wyse": ESC + "e*", "ansi": CSI + "41h"},
    "stbm": {"ansi": LINES + CSI + "2;4r" + ansi_at(3, 0) + "\n"},
    "insline": {"wyse": LINES + wy_at(1, 0) + ESC + "E", "ansi": LINES + ansi_at(1, 0) + CSI + "L"},
    "softfont": {"wyse": ESC + "cA141" + GLYPH + "\x19",
                 "ansi": ESC + "P0;33;1;0;0;0{ @" + "~" * 8 + "/" + "~" * 8 + ESC + "\\"},
    "ident": {"wyse": ESC + " ", "ansi": CSI + "c" + CSI + ">c", "vt52": ESC + "Z"},
    "eightbit": {f: "x\xe9\xc4\xb0y" for f in CLEAR},
    "dwl": {"ansi": "Wide" + ESC + "#6"},
    "pages": {"wyse": "page1" + ESC + "K", "ansi": "page1" + CSI + "U"},
    "status": {"wyse": ESC + "Fhostmsg\r", "ansi": CSI + "2$~" + CSI + "1$}hostmsg" + CSI + "0$}"},
    "attrs": {"wyse": "n" + ESC + "G4" + "r" + ESC + "G8" + "u" + ESC + "Gp" + "d" + ESC + "G2" + "b" + ESC + "G1" + "i" + ESC + "G0" + "n",
              "ansi": "n" + CSI + "7mr" + CSI + "0;4mu" + CSI + "0;2md" + CSI + "0;5mb" + CSI + "0;8mi" + CSI + "0;1mB" + CSI + "0mn"},
    "protect": {"wyse": "n" + ESC + "`6" + ESC + ")p" + ESC + "(n", "ansi": "n" + CSI + "1\"qp" + CSI + "0\"qn"},
}


def run(name, seqs, poke, work):
    d = work
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    shutil.copytree(work.parent.parent / "nvram-base", d / "nvram")
    srv = socket.create_server(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    received = bytearray()
    sent_at = []

    def host():
        conn, _ = srv.accept()
        conn.settimeout(0.2)
        start = time.monotonic()
        pending = list(seqs)
        next_at = 5.0
        while True:
            if pending and time.monotonic() - start >= next_at:
                sent_at.append(len(received))
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

    threading.Thread(target=host, daemon=True).start()
    env = dict(os.environ, WY55_OUT=str(d), WY55_FRAMES=str(70 * (6 + len(seqs) + 2)), WY55_POKE=poke)
    subprocess.run(
        [str(MAME / "wy55"), "wy55", "-rompath", str(MAME / "roms"), "-video", "none", "-sound", "none",
         "-skip_gameinfo", "-nvram_directory", str(d / "nvram"), "-snapshot_directory", str(d / "snap"),
         "-cfg_directory", str(d / "cfg"), "-host", "null_modem", "-bitb", f"socket.127.0.0.1:{port}",
         "-autoboot_script", str(MAME / "wy55-tools/harness.lua")],
        cwd=MAME, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    r = {k: (d / f"{k}.bin").read_bytes() for k in ("ram0", "ram1", "font", "iram")}
    # Only what the terminal sent after the last (probe) sequence
    r["received"] = bytes(received[sent_at[-1]:]) if sent_at else bytes(received)
    r["personality"] = r["iram"][0x23]
    return r


def reg(r, n):
    return r["ram0"][0x1f98 + n - 0xc0]


def rows(r):
    out = []
    for i in range(44):
        base = (r["ram0"][0x1f00 + 2 * i] << 8 | r["ram0"][0x1f01 + 2 * i]) & 0x1fff
        out.append(base)
    return out


def text(r, row, width=40):
    base = rows(r)[row]
    s = ""
    for b in r["ram0"][base + 1:base + 1 + width]:
        b &= 0x7f if b >= 0xa0 else 0xff
        s += chr(b) if 0x20 <= b < 0x7f else " "
    return s.rstrip()


def cell(r, row, col):
    base = rows(r)[row]
    return r["ram0"][base + 1 + col], r["ram1"][base + 1 + col]


def judge(probe, r, base):
    if probe == "cols132":
        return "yes" if not (reg(r, 0xc7) & 0x80) else "no"
    if probe == "lines42":
        return f"yes ({r['iram'][0x3d] + 1} lines)" if reg(r, 0xd3) < 0x0e else "no"
    if probe == "stbm":
        got = [text(r, i, 3) for i in range(1, 7)]
        return "yes" if got[:4] == ["A", "C", "D", ""] else "no " + "/".join(got)
    if probe == "insline":
        got = [text(r, i, 3) for i in range(1, 6)]
        return "yes" if got[:3] == ["A", "", "B"] else "no " + "/".join(got)
    if probe == "softfont":
        diff = sum(1 for a, b in zip(r["font"], base["font"]) if a != b)
        return f"yes ({diff} bytes)" if diff else "no"
    if probe == "ident":
        return repr(r["received"]) if r["received"] else "no reply"
    if probe == "eightbit":
        t = text(r, 1, 12)
        cells = [cell(r, 1, c) for c in range(1, 5)]
        return " ".join(f"{c:02x}/{a:02x}" for c, a in cells) + f"  [{t}]"
    if probe == "dwl":
        return "yes" if r["ram0"][rows(r)[1]] & 0x80 else "no"
    if probe == "pages":
        return "yes" if rows(r)[1] != rows(base)[1] else "no"
    if probe == "status":
        return "yes" if "hostmsg" in text(r, 0, 132) else "no"
    if probe in ("attrs", "protect"):
        return " ".join(f"{c:02x}/{a:02x}" for c, a in (cell(r, 1, i) for i in range(0, 14)))
    return "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("nvram", type=pathlib.Path)
    ap.add_argument("work", type=pathlib.Path)
    ap.add_argument("-j", type=int, default=6)
    ap.add_argument("--only")
    ap.add_argument("--probe")
    args = ap.parse_args()
    shutil.rmtree(args.work / "nvram-base", ignore_errors=True)
    shutil.copytree(args.nvram, args.work / "nvram-base")
    names = args.only.split(",") if args.only else list(PERSONALITIES)
    probes = ["base"] + (args.probe.split(",") if args.probe else [p for p in PROBES if p != "base"])

    jobs = {}
    with concurrent.futures.ThreadPoolExecutor(args.j) as ex:
        for name in names:
            fam, setup, poke = PERSONALITIES[name]
            for probe in probes:
                seq = PROBES[probe].get(fam)
                if seq is None:
                    continue
                work = args.work / "runs" / f"{name.replace(' ', '_')}-{probe}"
                jobs[name, probe] = ex.submit(run, name, setup + [CLEAR[fam] + seq], poke, work)
        results = {k: f.result() for k, f in jobs.items()}

    for name in names:
        base = results[name, "base"]
        print(f"== {name}: $23={base['personality']:02X} C0={reg(base, 0xc0):02X} C5={reg(base, 0xc5):02X}"
              f" attribute plane={'yes' if reg(base, 0xc5) & 8 else 'no'}")
        for probe in probes[1:]:
            if (name, probe) in results:
                print(f"   {probe:9} {judge(probe, results[name, probe], base)}")


if __name__ == "__main__":
    main()
