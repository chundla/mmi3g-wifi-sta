#!/bin/sh
# Build only; does not connect to or modify the head unit.
# Builds the phase 4.1-5.1 host bring-up, firmware download, command, scan,
# timing, join and data frame probe (a standalone root process, not an
# io-pkt driver) and checks its imports against the libraries saved from
# the unit. Output stays in the private workspace.
set -eu

TASK_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OPENSSH_WORK=${OPENSSH_WORK:-"$TASK_ROOT/work"}
QNX_SDK_ROOT=${QNX_SDK_ROOT:-"$OPENSSH_WORK/sdk"}
QNX_HOST="$QNX_SDK_ROOT/host/linux/x86"
QNX_TARGET="$QNX_SDK_ROOT/target/qnx6"
export QNX_HOST QNX_TARGET
TARGET=sh-unknown-nto-qnx6.3.0
COMPILER_PATH="$QNX_HOST/usr/$TARGET/bin"
PATH="$COMPILER_PATH:$PATH"
export COMPILER_PATH PATH
CC="$QNX_HOST/usr/bin/$TARGET-gcc"
SAVED_FILES=${SAVED_FILES:-"$TASK_ROOT/work/unit-files"}
LIBC="$SAVED_FILES/proc/boot/libc.so.2"
OUT_DIR=${OUT_DIR:-"$TASK_ROOT/work/build"}
OUT="$OUT_DIR/mv8688-probe"
SRC="$TASK_ROOT/wifi-sta"

for path in "$CC" "$LIBC"; do
    [ -e "$path" ] || { echo "missing: $path" >&2; exit 1; }
done
mkdir -p "$OUT_DIR"

"$CC" -ml -m4 -O2 -Wall -W -Werror \
    -o "$OUT" "$SRC/sdio-probe.c" "$SRC/mv8688cmd.c" "$SRC/benchnet.c" "$SRC/mv8688fw.c" \
    "$SRC/sdio.c" "$SRC/hbsdc.c"

python3 "$TASK_ROOT/openssh/verify_elf.py" \
    --library "$LIBC" \
    --output "$OUT_DIR/mv8688-probe.verify.json" "$OUT"
# GNU as records the lowest SH ISA the instructions need, so a valid
# SH-4 build can carry sh1/sh2/sh3/sh3e/sh4 flags. Reject DSP/SH-4A/SH-2E.
python3 - "$OUT" <<'PY'
import struct, sys
flags = struct.unpack_from("<I", open(sys.argv[1], "rb").read(), 36)[0]
if flags & 0x1f not in (1, 2, 3, 8, 9):
    sys.exit("ELF e_flags %#x is not an SH-4 subset" % flags)
PY
sha256sum "$OUT"
cksum "$OUT"
