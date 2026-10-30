#!/bin/ksh
# Installed on the MMI HDD at /mnt/persistence/en5/start.sh and called from the
# OpenSSH persistent startup. Brings up the USB-Ethernet management interface
# and a second sshd bound to it. Only this script, the driver object and the
# install marker are persistent; every file it creates lives in RAM.
set -u
umask 077
PATH=/bin:/sbin:/usr/bin:/usr/sbin
export PATH
# The factory boot environment preloads libmc.so, which creates a second
# thread. QNX 6.3.2 cannot fork a multithreaded SSH process. Keep that
# instrumentation out of the service and every session helper.
unset LD_PRELOAD || :
LD_LIBRARY_PATH=/lib:/lib/dll:/usr/lib:/usr/lib/dll:/proc/boot
export LD_LIBRARY_PATH
root=/mnt/persistence/en5
state=/fs/tmpfs/mmi-en5
driver=$root/devn-asix-772b.so
driver_sum='2558431275 75540'
address=192.168.16.11
netmask=255.255.240.0
log() { echo "en5: $*"; }
owned_pid() {
    [ -f "$1" ] || return 1
    read -r checked_pid < "$1"
    case "$checked_pid" in ''|*[!0-9]*) return 1;; esac
    kill -0 "$checked_pid" 2>/dev/null &&
        pidin -p "$checked_pid" ar 2>/dev/null | grep -q "$2"
}
[ ! -e /mnt/efs-persist/en5.disabled ] || exit 0
[ "$(uname -s)" = QNX ] || { log "Run on the MMI."; exit 1; }
[ -f "$root/INSTALL_READY" ] || { log "Installation is incomplete."; exit 1; }
[ -s "$driver" ] || { log "Missing driver object."; exit 1; }
# Fixed-offset patched binary: refuse to load anything but the reviewed bytes.
set -- $(cksum "$driver")
[ "$1 $2" = "$driver_sum" ] || { log "Driver object differs from the reviewed original."; exit 1; }
[ -d "$state" ] || mkdir "$state"
chmod 700 "$state"

# --- interface -------------------------------------------------------------
# Repeated invocation must not mount a second copy into the shared io-pkt.
if ifconfig en5 > /dev/null 2>&1; then
    log "en5 already present; leaving the running driver alone."
else
    mount -T io-pkt -o lan=5 "$driver" || { log "Driver mount failed."; exit 1; }
    attempt=0
    until ifconfig en5 > /dev/null 2>&1; do
        [ "$attempt" -lt 30 ] || { log "en5 did not appear."; exit 1; }
        sleep 1
        attempt=$((attempt + 1))
    done
    log "driver mounted."
fi
# Carrier is not required to continue: the address is static and sshd binds
# to it regardless, so a cable connected later needs no second run.
attempt=0
until ifconfig en5 2>/dev/null | grep -q 'status: active'; do
    [ "$attempt" -lt 20 ] || { log "no carrier yet; continuing."; break; }
    sleep 1
    attempt=$((attempt + 1))
done
if ifconfig en5 2>/dev/null | grep -q "inet alias $address "; then
    log "address already present."
elif ifconfig en5 alias "$address" netmask "$netmask"; then
    log "address $address configured."
else
    log "Could not configure $address."
    exit 1
fi

# --- second sshd -----------------------------------------------------------
# The installed instance stays bound to 192.168.1.1; this one only adds the
# wired address. Its config, logs and pid file are RAM copies.
[ -x /mnt/ssh/sbin/sshd ] || { log "OpenSSH is not installed; interface is up."; exit 0; }
[ -s /mnt/ssh/etc/sshd_config ] || { log "Missing sshd config template."; exit 1; }
if owned_pid "$state/sshd.pid" '/mnt/ssh/sbin/sshd'; then
    log "second sshd is already running."
    exit 0
fi
rm -f "$state/sshd.pid"
sed -e "s|^ListenAddress .*|ListenAddress $address|" \
    -e "s|^PidFile .*|PidFile $state/sshd.pid|" \
    /mnt/ssh/etc/sshd_config > "$state/sshd_config"
chmod 600 "$state/sshd_config"
/mnt/ssh/sbin/sshd -t -f "$state/sshd_config" || { log "Config test failed."; exit 1; }
(trap '' 1; exec /mnt/ssh/sbin/sshd -D -e -f "$state/sshd_config" > "$state/sshd.log" 2>&1) &
sshd_pid=$!
sleep 2
if kill -0 "$sshd_pid" 2>/dev/null; then
    log "second sshd ready on $address:2222; PID $sshd_pid."
else
    log "SSH startup failed; see $state/sshd.log."
    exit 1
fi
