#!/usr/bin/env python3
"""Write Setup values into the emulated WY-55's NVRAM, without going through the menus.

    wysetup.py NVRAM_DIR [--from NVRAM_DIR] [SETTING=VALUE ...]
    wysetup.py NVRAM_DIR --show

NVRAM_DIR holds wy55/nvram0 and wy55/nvram1 (MAME's -nvram_directory). Without --from it edits them in place;
with --from it starts from another pair (default: the factory-default pair next to this script).

Settings: personality=vt220-7, columns=132, lines=42, baud=115200, aux-baud=9600, scroll=jump|smooth-1..8,
cursor=blink-block|steady-block|blink-line|steady-line, display=dark|light, cell=10x16|10x15|10x13,
saver=on|off, status=on|off, bell=off|1|2|3, handshake=none|xon|dtr|both, printer=serial|parallel|none, or raw FIELD:MASK=VALUE in hex
(FIELD being n in 1FDF+n, as in docs/WY-55-internals.md).

Where the firmware keeps Setup: the saved copy is in the attribute RAM, nvram1 offset 1D87+n holding Setup field n
(the working copy at 1FDF+n in nvram0 is rebuilt from it at power-up). At power-up the firmware accepts it only if
the byte at 1D9A is 42 and the bytes 1D86-1FFF add up to 0 (mod 256), 1D86 being the balancing byte; otherwise it
shows K and waits for SetUp, then loads the factory defaults.
"""
import argparse
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SAVED = 0x1D87          # nvram1 offset of Setup field 0
SUM_FROM = 0x1D86
MAGIC = (0x1D9A, 0x42)

PERSONALITIES = {
    "wyse50": 0x14, "wyse50+": 0x04, "wyse60": 0x2C, "wyse120": 0x24, "wyse120+": 0x34, "wyse150": 0x25,
    "wyse150+": 0x35, "tvi910+": 0x06, "tvi925": 0x07, "tvi905": 0x17, "pcterm": 0x67, "adds": 0x01,
    "hz1500": 0x00, "vt52": 0xA0, "vt100": 0xA8, "vt220-7": 0xAC, "vt220-8": 0xBC, "unix": 0xAD,
}
BAUDS = [115200, 57600, 38400, 19200, 9600, 4800, 2400, 2000, 1800, 1200, 600, 300, 150, 134, 110, 75]

# name -> (field, mask, {value name: bits})
SETTINGS = {
    "personality": (3, 0xFF, PERSONALITIES),
    "columns": (5, 0xC0, {"80": 0x00, "132": 0x40, "econ-80": 0x80}),
    "lines": (5, 0x30, {"24": 0x00, "25": 0x10, "42": 0x20, "43": 0x30}),
    "display": (5, 0x08, {"dark": 0x00, "light": 0x08}),
    "cursor": (8, 0x60, {"blink-block": 0x00, "steady-block": 0x20, "blink-line": 0x40, "steady-line": 0x60}),
    "saver": (2, 0x08, {"off": 0x00, "on": 0x08}),
    "cell": (2, 0x60, {"10x16": 0x60, "10x15": 0x40, "10x13": 0x00}),
    "scroll": (2, 0x07, {"jump": 0x04, "smooth-8": 0x03, "smooth-4": 0x02, "smooth-2": 0x01, "smooth-1": 0x00}),
    "status": (14, 0x08, {"on": 0x00, "off": 0x08}),
    "bell": (11, 0xC0, {"off": 0x00, "1": 0x40, "2": 0x80, "3": 0xC0}),
    "printer": (11, 0x30, {"serial": 0x00, "parallel": 0x10, "none": 0x20}),
    "handshake": (8, 0x05, {"none": 0x05, "xon": 0x04, "dtr": 0x01, "both": 0x00}),
    "baud": (14, 0xF0, {str(b): i << 4 for i, b in enumerate(BAUDS)}),
    "aux-baud": (12, 0xF0, {str(b): i << 4 for i, b in enumerate(BAUDS)}),
}


def checksum(ram1):
    ram1[SUM_FROM] = 0
    ram1[SUM_FROM] = -sum(ram1[SUM_FROM:]) & 0xFF


def valid(ram1):
    return ram1[MAGIC[0]] == MAGIC[1] and sum(ram1[SUM_FROM:]) & 0xFF == 0


def apply(ram1, setting):
    name, _, value = setting.partition("=")
    if ":" in name:
        field, mask = (int(x, 16) for x in name.split(":"))
        bits = int(value, 16)
    else:
        field, mask, values = SETTINGS[name.lower()]
        key = value.lower().replace(" ", "")
        if key not in values:
            sys.exit(f"{name}: one of {', '.join(values)}")
        bits = values[key]
    ram1[SAVED + field] = (ram1[SAVED + field] & ~mask | bits & mask) & 0xFF


def show(ram1):
    print("valid" if valid(ram1) else "NOT valid (the terminal will wait for SetUp)")
    for name, (field, mask, values) in SETTINGS.items():
        bits = ram1[SAVED + field] & mask
        print(f"  {name:12} {next((k for k, v in values.items() if v == bits), f'{bits:02X}')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("nvram", type=pathlib.Path)
    ap.add_argument("settings", nargs="*")
    ap.add_argument("--from", dest="source", type=pathlib.Path)
    ap.add_argument("--show", action="store_true")
    args = ap.parse_intermixed_args()

    src = args.source or (HERE / "nvram/factory" if args.settings and not (args.nvram / "wy55/nvram1").exists()
                          else args.nvram)
    ram0 = bytearray((src / "wy55/nvram0").read_bytes())
    ram1 = bytearray((src / "wy55/nvram1").read_bytes())
    if args.show:
        show(ram1)
        return
    if not valid(ram1):
        sys.exit(f"{src}: no valid Setup to start from (use --from)")
    for s in args.settings:
        apply(ram1, s)
    checksum(ram1)
    out = args.nvram / "wy55"
    out.mkdir(parents=True, exist_ok=True)
    (out / "nvram0").write_bytes(ram0)
    (out / "nvram1").write_bytes(ram1)
    show(ram1)


if __name__ == "__main__":
    main()
