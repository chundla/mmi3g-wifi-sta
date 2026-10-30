#!/usr/bin/env python3
"""Make a RAM-only copy of devn-asix.so that accepts a generic AX88772B.

Offline only; does not connect to or modify the head unit. The saved driver's
device table (data 0x21b74, file 0x10b74, 16-byte entries: vendor, device,
chip code, GPIO flags) matches adapters by their USB descriptor; the vid=/did=
mount options do not affect matching. The only AX88772B entry is the D-Link
DUB-E100 rev C1 (2001:1a02, chip 0x88772b, flags 0x80). This rewrites that
entry's vendor/device to 0b95:772b and keeps its chip code and flags, so the
adapter takes the same ax_enable_88772B path. The DUB-E100 C1 is no longer
recognised by the copy. Output stays in the private workspace.
"""
import argparse
import hashlib
import pathlib
import struct

ROOT = pathlib.Path(__file__).resolve().parents[1]
SAVED = ROOT / "work/unit-files/mnt/ifs-root/lib/dll/devn-asix.so"
OUT = ROOT / "work/build/devn-asix-772b.so"
SHA256 = "085f2658a7649fcbd1ca0e08267a83d86534855d3c86f78f3a1826e8a3df241a"
OFFSET = 0x10b94
ORIGINAL = struct.pack("<IIII", 0x2001, 0x1a02, 0x88772b, 0x80)
PATCHED = struct.pack("<IIII", 0x0b95, 0x772b, 0x88772b, 0x80)


def patch(data):
    if data[OFFSET:OFFSET + len(ORIGINAL)] != ORIGINAL:
        raise ValueError("original device-table bytes not found at %#x" % OFFSET)
    if data.count(PATCHED[:8]):
        raise ValueError("0b95:772b already present")
    return data[:OFFSET] + PATCHED + data[OFFSET + len(PATCHED):]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=pathlib.Path, default=SAVED)
    parser.add_argument("--output", type=pathlib.Path, default=OUT)
    args = parser.parse_args()
    data = args.input.read_bytes()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise SystemExit("unexpected input SHA-256; refusing a fixed-offset patch")
    out = patch(data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(out)
    changed = [i for i in range(len(data)) if data[i] != out[i]]
    print("changed bytes:", ", ".join("%#x %02x->%02x" % (i, data[i], out[i]) for i in changed))
    print("sha256", hashlib.sha256(out).hexdigest(), args.output)


if __name__ == "__main__":
    main()
