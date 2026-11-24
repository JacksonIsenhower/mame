#!/usr/bin/env python3
"""Regression run for the emulated WY-55: every test, compared with the expected results in expected/.

    regress.py WORK_DIR [--rom wy55|wy55p] [--update] [--only personality,features,flow,screens]

- personality: personality-tests.py (host-driven personality switches)
- features:    features.py (what each personality supports)
- flow:        flow-test.py (lines lost with and without DTR/RTS flow control)
- screens:     golden screenshots of a few test screens (attributes, line drawing, 132x42, double size,
               light screen with 13-line cells and an underline cursor), compared pixel for pixel

The text results are diffed against expected/<rom>-<test>.txt and the screens against expected/screens/; with
--update the current results become the expected ones. A patch that is meant to change nothing should pass
against the wy55 expectations: run with --rom wy55p and --expect wy55.
"""
import argparse
import concurrent.futures
import difflib
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import threading
import time

TOOLS = pathlib.Path(__file__).resolve().parent
MAME = TOOLS.parent
EXPECTED = TOOLS / "expected"
ESC = "\x1b"
CSI = ESC + "["

# name: (Setup changes from the VT220-7 / 132x42 / 115200 NVRAM, what the host sends)
SCREENS = {
    "attributes": ([], CSI + "2J" + CSI + "H" + "normal " + CSI + "1mbold" + CSI + "0m " + CSI + "4munderline"
                   + CSI + "0m " + CSI + "7mreverse" + CSI + "0m " + CSI + "5mblink" + CSI + "0m " + CSI
                   + "1;4;7mall" + CSI + "0m " + CSI + "8minvisible" + CSI + "0m|\r\n"
                   + "".join(chr(c) for c in range(0x21, 0x7f)) + "\r\n" + CSI + "1\"qprotected" + CSI + "0\"q"),
    "linedraw": ([], CSI + "2J" + CSI + "H" + ESC + "(0" + "lqqqqwqqqqk\r\nx    x    x\r\ntqqqqnqqqqu\r\n"
                 "x    x    x\r\nmqqqqvqqqqj\r\n`afgjklmnopqrstuvwxyz{|}~" + ESC + "(B"),
    "132x42": ([], CSI + "2J" + CSI + "H" + "top left" + CSI + "1;124Htop right" + CSI + "42;1Hbottom left"
               + CSI + "42;121Hbottom right" + CSI + "21;60Hmiddle"),
    "doublesize": ([], CSI + "2J" + CSI + "H" + "Double width" + ESC + "#6\r\nDouble height" + ESC + "#3\r\n"
                   "Double height" + ESC + "#4\r\nnormal"),
    "light-80x24-cell13": (["display=light", "columns=80", "lines=24", "cell=10x13", "cursor=steady-line"],
                           CSI + "2J" + CSI + "H" + "Light screen " + CSI + "4munderline" + CSI + "0m "
                           + CSI + "7mreverse" + CSI + "0m gjpqy_"),
}


def run_tool(script, args, rom, work):
    out = subprocess.run([sys.executable, str(TOOLS / script), *args, str(work), "--rom", rom], cwd=MAME,
                         capture_output=True, text=True, timeout=1800)
    if out.returncode:
        sys.exit(f"{script} failed:\n{out.stderr}")
    return out.stdout


def screen(name, setup, text, rom, work):
    d = work / name
    shutil.rmtree(d, ignore_errors=True)
    subprocess.run([sys.executable, str(TOOLS / "wysetup.py"), str(d / "nvram"), "--from",
                    str(TOOLS / "nvram/vt220-132x42-115200"), *setup], check=True, stdout=subprocess.DEVNULL)
    if rom != "wy55":   # MAME keeps a clone's NVRAM under its own name
        shutil.copytree(d / "nvram/wy55", d / "nvram" / rom, dirs_exist_ok=True)
    srv = socket.create_server(("127.0.0.1", 0))
    port = srv.getsockname()[1]

    def host():
        conn, _ = srv.accept()
        time.sleep(5)
        conn.sendall(text.encode("latin-1"))
        try:
            while conn.recv(4096):
                pass
        except OSError:
            pass

    threading.Thread(target=host, daemon=True).start()
    # Two snapshots half a blink period apart (the blink and cursor phases change every 32 frames)
    env = dict(os.environ, WY55_OUT=str(d), WY55_FRAMES="640", WY55_SNAPS="608", WY55_BAUD="115200")
    subprocess.run(
        [str(MAME / "wy55"), rom, "-rompath", str(MAME / "roms"), "-video", "none", "-sound", "none",
         "-skip_gameinfo", "-nvram_directory", str(d / "nvram"), "-snapshot_directory", str(d / "snap"),
         "-cfg_directory", str(d / "cfg"), "-host", "null_modem", "-bitb", f"socket.127.0.0.1:{port}",
         "-autoboot_script", str(TOOLS / "harness.lua")],
        cwd=MAME, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    return [d / f"snap/{rom}/000{i}.png" for i in (0, 1)]


def differing_pixels(a, b):
    r = subprocess.run(["magick", "compare", "-metric", "AE", str(a), str(b), "null:"], capture_output=True, text=True)
    return r.stderr.strip().split()[0] if r.stderr else "?"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("work", type=pathlib.Path)
    ap.add_argument("--rom", default="wy55")
    ap.add_argument("--expect", help="compare with another ROM's expected results (default: --rom's)")
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--nvram", type=pathlib.Path, default=TOOLS / "nvram/factory",
                    help="NVRAM for personality-tests.py and features.py (default: factory, Wyse 50)")
    args = ap.parse_args()
    expect = args.expect or args.rom
    tests = args.only.split(",") if args.only else ["personality", "features", "flow", "screens"]
    args.work.mkdir(parents=True, exist_ok=True)
    (EXPECTED / "screens").mkdir(parents=True, exist_ok=True)
    failed = []

    # One tool at a time: each runs several emulators in parallel already, and the tests time the host's
    # sequences in real seconds, so an overloaded machine would shift them against the emulated frames
    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        jobs = {}
        if "personality" in tests:
            jobs["personality"] = ex.submit(run_tool, "personality-tests.py", [str(args.nvram)], args.rom,
                                            args.work / "personality")
        if "features" in tests:
            jobs["features"] = ex.submit(run_tool, "features.py", [str(args.nvram)], args.rom, args.work / "features")
        if "flow" in tests:
            jobs["flow"] = ex.submit(run_tool, "flow-test.py", [], args.rom, args.work / "flow")
        for name, job in jobs.items():
            got = job.result()
            exp = EXPECTED / f"{expect}-{name}.txt"
            if args.update:
                exp.write_text(got)
                print(f"{name}: recorded {exp.name}")
            elif not exp.exists():
                print(f"{name}: no {exp.name} (run with --update)")
                failed.append(name)
            elif got == exp.read_text():
                print(f"{name}: same as {exp.name}")
            else:
                print(f"{name}: DIFFERS from {exp.name}")
                sys.stdout.writelines(difflib.unified_diff(exp.read_text().splitlines(True), got.splitlines(True),
                                                           exp.name, "now"))
                failed.append(name)

    if "screens" in tests:
        with concurrent.futures.ThreadPoolExecutor(5) as ex:
            shots = {n: ex.submit(screen, n, s, t, args.rom, args.work / "screens") for n, (s, t) in SCREENS.items()}
            for (name, i), got in ((k, v) for n, job in shots.items() for k, v in zip(((n, 0), (n, 1)), job.result())):
                name = f"{name}-{'ab'[i]}"
                exp = EXPECTED / "screens" / f"{name}.png"
                if args.update:
                    shutil.copy(got, exp)
                    print(f"screen {name}: recorded")
                elif not exp.exists():
                    print(f"screen {name}: no expected image (run with --update)")
                    failed.append(name)
                elif got.read_bytes() == exp.read_bytes():
                    print(f"screen {name}: same")
                else:
                    n = differing_pixels(got, exp)
                    if n == "0":
                        print(f"screen {name}: same pixels")
                    else:
                        print(f"screen {name}: DIFFERS in {n} pixels ({got})")
                        failed.append(name)

    if not args.update:
        print("PASS" if not failed else f"FAIL: {', '.join(failed)}")
        sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
