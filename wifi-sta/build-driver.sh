#!/bin/sh
# Build only; does not connect to or modify the head unit.
# Builds the phase 5.2 station driver (an io-net driver for devnp-shim in
# io-pkt), its control tool, the phase 6.0 BPF check (with the phase 7
# connection log) and the phase 7 network check, and checks their imports
# against the libraries saved from the unit. Output stays in the
# private workspace.
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
LIBSOCKET="$SAVED_FILES/mnt/ifs-root/lib/libsocket.so.2"
OUT_DIR=${OUT_DIR:-"$TASK_ROOT/work/build"}
DRIVER="$OUT_DIR/devn-mv8688sta.so"
TOOL="$OUT_DIR/mv8688-ctl"
CHECK="$OUT_DIR/bpf-check"
NETCHECK="$OUT_DIR/netcheck"
SRC="$TASK_ROOT/wifi-sta"

for path in "$CC" "$LIBC" "$LIBSOCKET"; do
    [ -e "$path" ] || { echo "missing: $path" >&2; exit 1; }
done
mkdir -p "$OUT_DIR"

"$CC" -ml -m4 -O2 -Wall -W -Werror -fPIC -shared \
    -Wl,-soname,devn-mv8688sta.so \
    -o "$DRIVER" "$SRC/devn-mv8688sta.c" "$SRC/mv8688sta.c" "$SRC/mv8688cmd.c" \
    "$SRC/mv8688fw.c" "$SRC/sdio.c" "$SRC/hbsdc.c"
"$CC" -ml -m4 -O2 -Wall -W -Werror -o "$TOOL" "$SRC/mv8688-ctl.c"
"$CC" -ml -m4 -O2 -Wall -W -Werror -o "$CHECK" "$SRC/bpf-check.c" "$SRC/bpfl2.c" \
    "$SRC/connlog.c" -lsocket
"$CC" -ml -m4 -O2 -Wall -W -Werror -o "$NETCHECK" "$SRC/netcheck.c" -lsocket

python3 "$TASK_ROOT/openssh/verify_elf.py" --shared \
    --library "$LIBC" \
    --output "$OUT_DIR/devn-mv8688sta.verify.json" "$DRIVER"
python3 "$TASK_ROOT/openssh/verify_elf.py" \
    --library "$LIBC" \
    --output "$OUT_DIR/mv8688-ctl.verify.json" "$TOOL"
python3 "$TASK_ROOT/openssh/verify_elf.py" \
    --library "$LIBC" --library "$LIBSOCKET" \
    --output "$OUT_DIR/bpf-check.verify.json" "$CHECK"
python3 "$TASK_ROOT/openssh/verify_elf.py" \
    --library "$LIBC" --library "$LIBSOCKET" \
    --output "$OUT_DIR/netcheck.verify.json" "$NETCHECK"
# GNU as records the lowest SH ISA the instructions need, so a valid
# SH-4 build can carry sh1/sh2/sh3/sh3e/sh4 flags. Reject DSP/SH-4A/SH-2E.
for out in "$DRIVER" "$TOOL" "$CHECK" "$NETCHECK"; do
    python3 - "$out" <<'PY'
import struct, sys
flags = struct.unpack_from("<I", open(sys.argv[1], "rb").read(), 36)[0]
if flags & 0x1f not in (1, 2, 3, 8, 9):
    sys.exit("ELF e_flags %#x is not an SH-4 subset" % flags)
PY
done
sha256sum "$DRIVER" "$TOOL" "$CHECK" "$NETCHECK"
cksum "$DRIVER" "$TOOL" "$CHECK" "$NETCHECK"
