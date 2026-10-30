#!/bin/ksh
# Run on the MMI from /mnt/persistence/en5 once the payload is staged.
# Hooks the en5 bring-up into the existing OpenSSH persistent startup.
# lsd.sh and pf.conf are deliberately untouched.
set -eu
umask 077
PATH=/bin:/sbin:/usr/bin:/usr/sbin
export PATH
root=/mnt/persistence/en5
sshd_start=/mnt/persistence/openssh/start.sh
driver_sum='2558431275 75540'
hook='[ ! -x /mnt/persistence/en5/start.sh ] || (/bin/ksh /mnt/persistence/en5/start.sh > /fs/tmpfs/en5-boot.log 2>&1 &) || :'
changed=0
fail() { echo "en5 install: $*" >&2; exit 1; }
[ "$(uname -s)" = QNX ] || fail "Run on the MMI."
[ -s "$root/start.sh" ] || fail "Missing staged start.sh."
[ -s "$root/devn-asix-772b.so" ] || fail "Missing staged driver."
set -- $(cksum "$root/devn-asix-772b.so")
[ "$1 $2" = "$driver_sum" ] || fail "Staged driver differs from the reviewed original."
/bin/ksh -n "$root/start.sh"
[ -s "$sshd_start" ] || fail "OpenSSH persistent startup is not installed."
if grep -q 'persistence/en5/start.sh' "$sshd_start"; then
    fail "Hook is already present; nothing to do."
fi
[ ! -e "$sshd_start.pre-en5" ] || fail "Backup already exists: $sshd_start.pre-en5"
[ -d "$root/backup" ] || mkdir "$root/backup"
# Disable startup until a supervised first launch succeeds.
echo disabled > /mnt/efs-persist/en5.disabled
# Rollback lives outside the payload so it works if the payload is broken.
cat > /mnt/efs-persist/en5-rollback.sh <<'ROLLBACK'
#!/bin/ksh
# Restores the OpenSSH persistent startup and disables the en5 path.
set -eu
PATH=/bin:/sbin:/usr/bin:/usr/sbin
export PATH
sshd_start=/mnt/persistence/openssh/start.sh
echo disabled > /mnt/efs-persist/en5.disabled
if [ -s "$sshd_start.pre-en5" ]; then
    /bin/ksh -n "$sshd_start.pre-en5"
    cp -p "$sshd_start.pre-en5" "$sshd_start"
    rm -f "$sshd_start.pre-en5"
    echo "Restored $sshd_start from its pre-en5 backup."
else
    echo "No pre-en5 backup found; startup left as is and en5 disabled." >&2
fi
rm -f /mnt/persistence/en5/INSTALL_READY
echo "en5 rollback complete. A reboot leaves no en5 interface or second sshd."
ROLLBACK
chmod 700 /mnt/efs-persist/en5-rollback.sh
cksum "$sshd_start" > "$root/backup/start.sh.original.cksum"
cp -p "$sshd_start" "$sshd_start.pre-en5"
cp -p "$sshd_start" "$root/backup/start.sh.original"
rollback() {
    result=$?
    trap - 0
    if [ "$result" != 0 ] && [ "$changed" = 1 ]; then
        cp -p "$sshd_start.pre-en5" "$sshd_start"
        echo 'Install failed; OpenSSH startup restored and en5 remains disabled.' >&2
    fi
    exit "$result"
}
trap rollback 0
cp -p "$sshd_start" "$sshd_start.en5-new"
echo "$hook" >> "$sshd_start.en5-new"
/bin/ksh -n "$sshd_start.en5-new"
changed=1
mv -f "$sshd_start.en5-new" "$sshd_start"
chmod 700 "$root/start.sh"
echo 'en5 USB-Ethernet management path' > "$root/INSTALL_READY"
echo 'Staged and hooked. Startup remains disabled pending supervised launch.'
echo "Installed start.sh cksum: $(cksum "$sshd_start")"
