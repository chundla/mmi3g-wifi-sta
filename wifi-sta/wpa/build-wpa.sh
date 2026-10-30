#!/bin/sh
# Build only; does not connect to or modify the head unit.
# Builds wpa_supplicant 2.11 and wpa_cli for the MMI (phase 6): the release
# archive, pinned by SHA-256 (its signature was checked against Jouni
# Malinen's key when it was downloaded), plus qnx632.patch and the files
# beside this script. Checks their imports against the libraries saved
# from the unit. The archive is not fetched here; put it in
# work/build/downloads/ first:
#   https://w1.fi/releases/wpa_supplicant-2.11.tar.gz (and .asc)
set -eu

TASK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
OPENSSH_WORK=${OPENSSH_WORK:-"$TASK_ROOT/work"}
QNX_SDK_ROOT=${QNX_SDK_ROOT:-"$OPENSSH_WORK/sdk"}
QNX_HOST="$QNX_SDK_ROOT/host/linux/x86"
QNX_TARGET="$QNX_SDK_ROOT/target/qnx6"
export QNX_HOST QNX_TARGET
TARGET=sh-unknown-nto-qnx6.3.0
COMPILER_PATH="$QNX_HOST/usr/$TARGET/bin"
PATH="$COMPILER_PATH:$PATH"
export COMPILER_PATH PATH
SAVED_FILES=${SAVED_FILES:-"$TASK_ROOT/work/unit-files"}
LIBC="$SAVED_FILES/proc/boot/libc.so.2"
LIBSOCKET="$SAVED_FILES/mnt/ifs-root/lib/libsocket.so.2"
BUILD=${WPA_BUILD:-"$TASK_ROOT/work/build"}
ARCHIVE="$BUILD/downloads/wpa_supplicant-2.11.tar.gz"
SOURCE_DIR="$BUILD/work/wpa_supplicant-2.11"
OUT_DIR="$BUILD/wpa"
HERE="$TASK_ROOT/wifi-sta/wpa"
STA="$TASK_ROOT/wifi-sta"

for path in "$QNX_HOST/usr/bin/$TARGET-gcc" "$LIBC" "$LIBSOCKET" "$ARCHIVE"; do
    [ -e "$path" ] || { echo "missing: $path" >&2; exit 1; }
done
mkdir -p "$BUILD/work" "$OUT_DIR"
python3 - "$ARCHIVE" "$BUILD/work" <<'PY'
import hashlib, sys, tarfile
from pathlib import Path
source, work = map(Path, sys.argv[1:])
expected = "912ea06f74e30a8e36fbb68064d6cdff218d8d591db0fc5d75dee6c81ac7fc0a"
if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
    raise SystemExit("wpa_supplicant archive checksum mismatch")
if not (work / "wpa_supplicant-2.11").exists():
    with tarfile.open(source) as archive:
        archive.extractall(work, filter="data")
PY

cd "$SOURCE_DIR"
if patch -p1 --forward --dry-run < "$HERE/qnx632.patch" >/dev/null 2>&1; then
    patch -p1 < "$HERE/qnx632.patch"
else
    # Refuse partial/stale patches, while allowing an incremental rebuild.
    patch -p1 --reverse --dry-run < "$HERE/qnx632.patch" >/dev/null
fi
cp "$HERE/driver_mv8688.c" "$STA/mv8688sta-ctl.h" src/drivers/
cp "$HERE/l2_packet_qnx.c" "$STA/bpfl2.c" "$STA/bpfl2.h" src/l2_packet/
cp "$HERE/os_random_egd.c" src/utils/
cp "$HERE/wpa_supplicant.config" wpa_supplicant/.config

# CFLAGS from the environment: the Makefile appends its own to it.
CFLAGS="-O2 -std=gnu99" make -C wpa_supplicant -j"${JOBS:-4}" \
    CC="$QNX_HOST/usr/bin/$TARGET-gcc -ml -m4" wpa_supplicant wpa_cli \
    > "$OUT_DIR/make.log" 2>&1 || { tail -20 "$OUT_DIR/make.log" >&2; exit 1; }
for program in wpa_supplicant wpa_cli; do
    "$QNX_HOST/usr/bin/$TARGET-strip" -o "$OUT_DIR/$program" "wpa_supplicant/$program"
done
cp COPYING README "$OUT_DIR/"

python3 "$TASK_ROOT/openssh/verify_elf.py" \
    --library "$LIBC" --library "$LIBSOCKET" \
    --output "$OUT_DIR/wpa.verify.json" "$OUT_DIR/wpa_supplicant" "$OUT_DIR/wpa_cli"
# GNU as records the lowest SH ISA the instructions need, so a valid
# SH-4 build can carry sh1/sh2/sh3/sh3e/sh4 flags. Reject DSP/SH-4A/SH-2E.
for out in "$OUT_DIR/wpa_supplicant" "$OUT_DIR/wpa_cli"; do
    python3 - "$out" <<'PY'
import struct, sys
flags = struct.unpack_from("<I", open(sys.argv[1], "rb").read(), 36)[0]
if flags & 0x1f not in (1, 2, 3, 8, 9):
    sys.exit("ELF e_flags %#x is not an SH-4 subset" % flags)
PY
done
sha256sum "$OUT_DIR/wpa_supplicant" "$OUT_DIR/wpa_cli"
cksum "$OUT_DIR/wpa_supplicant" "$OUT_DIR/wpa_cli"
