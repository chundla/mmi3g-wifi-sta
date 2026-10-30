#!/bin/ksh
# Phases 7.5 and 7.6: install, inspect or remove the Wi-Fi client's station
# files on this unit's HDD (9411, HN+R_US_AU_K0942_3), for the engineering
# screen's buttons (gem/gem.sh) and the start at start-up (sta-boot.sh). See
# docs/wifi-client/wifi-client-mode.md, "The start button and persistent
# files (7.4b, 7.5)" and "Station mode at start-up (7.6)".
#
#   status           what is installed, whether each file matches its sum,
#                    whether the hotspot configuration is there (never its
#                    contents), free space
#   install <dir>    from a directory holding the files below (each checked
#                    against its sum) into /mnt/persistence/wifi-sta; SUMS
#                    is written last. Completes a set of which some files
#                    are already this build's (an upgrade)
#   conf             the hotspot's wpa_supplicant configuration, from stdin,
#                    into wpa-hotspot.conf there (root, mode 600). It is
#                    checked as sta-mode.sh checks it, and never printed
#   hook             append one line to the OpenSSH start-up
#                    (/mnt/persistence/openssh/start.sh) that runs
#                    sta-boot.sh in the background; first writes
#                    /mnt/efs-persist/wifi-sta-rollback.sh, which takes the
#                    line out again, and keeps a copy of start.sh here
#   unhook           run that rollback
#   remove           take all of it out again, the configuration included;
#                    refused while hooked
#
# Nothing factory is changed. Even when hooked, sta-boot.sh starts nothing
# unless the GEM's "Start-up: join the hotspot" button has left its marker.
# Refuses while station mode is running. A dropped SSH session (HUP) does not
# stop it.
# Exit: 0 done, 1 usage, 2 refused or failed.
set -u
umask 077
PATH=/bin:/sbin:/usr/bin:/usr/sbin
export PATH
unset LD_PRELOAD || :

persistence=/mnt/persistence
top=$persistence/wifi-sta
sums=$top/SUMS
conf=$top/wpa-hotspot.conf
conf_owner=root
# What sta-mode.sh needs besides these files; the OpenSSH installation
# provides both (docs/access/openssh-persistent.md).
sshd=/mnt/ssh/sbin/sshd
openssh_start=$persistence/openssh/start.sh
# sta-mode.sh's lock: station mode is running.
sta_lock=/fs/tmpfs/mmi-sta/lock
# name, mode, cksum. The binaries are the ones the 7.3 run used.
files='devn-mv8688sta.so 700 3938534867 64931
mv8688-ctl 700 3277080864 11450
mv8688-probe 700 2747790827 77195
sd8688_helper.bin 600 1538620194 2616
sd8688.bin 600 251454034 259172
wpa_supplicant 700 2366746706 491332
sta-mode.sh 700 2913500418 43139
sta-boot.sh 700 1916130123 3248'
# The start-up line, its rollback and the copy of start.sh before it.
hook_line='[ ! -x /mnt/persistence/wifi-sta/sta-boot.sh ] || (/bin/ksh /mnt/persistence/wifi-sta/sta-boot.sh > /fs/tmpfs/wifi-sta-boot.out 2>&1 &) || :'
rollback=/mnt/efs-persist/wifi-sta-rollback.sh
pre_hook=$top/start.sh.pre-wifi-sta
marker=/mnt/efs-persist/wifi-sta/gem/client-at-start-up
# RAM: gone after a restart.
run=/fs/tmpfs
lock=$run/wifi-sta-install.lock
min_free_kb=2048
conf_max=4096
locked=no
txn=none

usage() {
    echo "usage: sta-install.sh status | install <dir> | conf | hook | unhook | remove" >&2
    exit 1
}

say() {
    echo "wifi-sta: $*"
}

fail() {
    say "FAIL: $*"
    exit 2
}

sum_of() {
    cksum "$1" 2>/dev/null | { read -r a b rest && echo "$a $b"; }
}

free_kb() {
    df -k "$1/" 2>/dev/null | grep '^/dev/' | sed -n 1p |
        { read -r fs total used avail rest && echo "$avail"; }
}

names() {
    echo "$files" | while read -r n m s z; do echo "$n"; done
}

# The pinned sum of name $1.
pin_of() {
    echo "$files" | while read -r n m s z; do
        [ "$n" = "$1" ] && echo "$s $z"
    done
}

mode_of() {
    echo "$files" | while read -r n m s z; do
        [ "$n" = "$1" ] && echo "$m"
    done
}

take_lock() {
    if ! mkdir "$lock" 2>/dev/null; then
        holder=$(cat "$lock/pid" 2>/dev/null)
        if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
            fail "another sta-install.sh (pid $holder) holds $lock"
        fi
        say "removing a stale lock (pid ${holder:-unknown})"
        rm -rf "$lock"
        mkdir "$lock" 2>/dev/null || fail "cannot take $lock"
    fi
    locked=yes
    echo $$ > "$lock/pid"
}

not_running() {
    [ ! -d "$sta_lock" ] ||
        fail "station mode is running ($sta_lock); restart the unit first"
}

# put <source> <target> <mode> <expected sum>: copy beside the target,
# check it, then rename it into place and check again.
put() {
    put_new=$2.new.$$
    rm -f "$put_new"
    cp "$1" "$put_new" 2>/dev/null || { rm -f "$put_new"; return 1; }
    chmod "$3" "$put_new" || { rm -f "$put_new"; return 1; }
    if [ "$(sum_of "$put_new")" != "$4" ]; then
        rm -f "$put_new"
        return 1
    fi
    mv -f "$put_new" "$2" || { rm -f "$put_new"; return 1; }
    [ "$(sum_of "$2")" = "$4" ]
}

# The station files, as one word: absent, installed (SUMS and every file
# match), partial (some of this build's files) or other.
state_of() {
    so_s=
    for n in $(names); do
        if [ ! -f "$top/$n" ]; then so_s=${so_s}a
        elif [ "$(sum_of "$top/$n")" = "$(pin_of "$n")" ]; then so_s=${so_s}i
        else so_s=${so_s}x; fi
    done
    case $so_s in
        *x*) echo other ;;
        *i*a*|*a*i*) echo partial ;;
        *a*) if [ -f "$sums" ]; then echo partial; else echo absent; fi ;;
        *) if [ "$(sums_text)" = "$(cat "$sums" 2>/dev/null)" ]; then
               echo installed
           else
               echo partial
           fi ;;
    esac
}

# SUMS: "<cksum> <bytes> <name>" per file, which gem.sh start checks.
sums_text() {
    echo "$files" | while read -r n m s z; do echo "$s $z $n"; done
}

# The checks sta-mode.sh makes, without printing a line of it.
conf_ok() {
    [ -f "$1" ] && [ -s "$1" ] || return 1
    set -- "$1" $(ls -l "$1")
    [ "$2" = "-rw-------" ] && [ "$4" = "$conf_owner" ] || return 1
    [ "${6:-0}" -le "$conf_max" ] 2>/dev/null || return 1
    cl() { sed -e 's/^[ 	]*//' -e 's/[ 	]*$//' "$1"; }
    [ "$(cl "$1" | grep -c '^network={')" = 1 ] &&
        [ "$(cl "$1" | grep -c '^ssid=')" = 1 ] &&
        [ "$(cl "$1" | grep -c '^psk=')" = 1 ] &&
        [ "$(cl "$1" | grep -Fxc 'key_mgmt=WPA-PSK')" = 1 ]
}

remove_ours() {
    remove_rc=0
    # SUMS first: without it the start button refuses.
    rm -f "$sums"
    [ ! -f "$sums" ] || { say "could not remove $sums"; remove_rc=1; }
    for n in $(names); do
        [ -f "$top/$n" ] || continue
        if [ "$(sum_of "$top/$n")" = "$(pin_of "$n")" ]; then
            rm -f "$top/$n"
        else
            say "$top/$n is not this build's ($(sum_of "$top/$n")); left in place"
            remove_rc=1
        fi
    done
    return "$remove_rc"
}

finish() {
    finish_rc=$?
    trap '' 1 2 15
    if [ "$txn" = active ]; then
        txn=none
        say "undoing the partial install"
        remove_ours || { say "FAIL: the partial install was not fully undone"; finish_rc=2; }
    fi
    rm -f "$top"/*.new.$$ 2>/dev/null
    [ "$locked" = yes ] && rm -rf "$lock"
    exit "$finish_rc"
}

status() {
    say "state: $(state_of)"
    if [ ! -d "$persistence" ]; then
        say "$persistence: absent (the HDD is not mounted)"
        return 0
    fi
    if [ -d "$top" ]; then
        for n in $(names); do
            if [ -f "$top/$n" ]; then
                set -- $(ls -l "$top/$n")
                say "$n: $(sum_of "$top/$n") $1 (this build $(pin_of "$n"))"
            else
                say "$n: absent"
            fi
        done
        if [ -f "$sums" ]; then say "SUMS: present"; else say "SUMS: absent"; fi
        if conf_ok "$conf"; then
            say "wpa-hotspot.conf: present, $conf_owner, mode 600, one WPA-PSK network"
        elif [ -f "$conf" ]; then
            set -- $(ls -l "$conf")
            say "wpa-hotspot.conf: NOT USABLE ($1 $3; sta-mode.sh would refuse it)"
        else
            say "wpa-hotspot.conf: absent"
        fi
    else
        say "$top: absent"
    fi
    if [ -x "$sshd" ]; then say "$sshd: present"; else say "$sshd: ABSENT"; fi
    if hooked; then say "start-up line: in $openssh_start"; else say "start-up line: absent"; fi
    if [ -f "$marker" ]; then say "start-up mode: hotspot client"; else say "start-up mode: car access point"; fi
    if [ -f "$top/boot.log" ]; then say "last boot.log line: $(sed -n '$p' "$top/boot.log")"; fi
    if [ -d "$sta_lock" ]; then say "station mode: running"; else say "station mode: not running"; fi
    say "$persistence: $(free_kb "$persistence") KB free"
}

install() {
    stage=$1
    [ -d "$stage" ] || fail "$stage is not a directory"
    for n in $(names); do
        [ "$(sum_of "$stage/$n")" = "$(pin_of "$n")" ] ||
            fail "$stage/$n is not this build's ($(pin_of "$n")): $(sum_of "$stage/$n")"
    done
    take_lock
    not_running
    state=$(state_of)
    case $state in
        absent|partial) ;;
        installed) say "already installed from this build"; return 0 ;;
        *) fail "the files are $state; remove them first" ;;
    esac
    [ -d "$persistence" ] || fail "$persistence is absent (the HDD is not mounted)"
    [ -f "$openssh_start" ] && [ -x "$sshd" ] ||
        fail "the OpenSSH installation ($openssh_start, $sshd) is missing; the station's SSH needs it"
    free=$(free_kb "$persistence")
    case $free in
        ''|*[!0-9]*) fail "cannot read the free space of $persistence" ;;
    esac
    [ "$free" -ge "$min_free_kb" ] || fail "$persistence has only ${free} KB free, needs $min_free_kb"
    [ -d "$top" ] || mkdir "$top" 2>/dev/null
    [ -d "$top" ] || fail "cannot create $top"
    chmod 700 "$top" || fail "cannot make $top private"
    say "install from $stage into $top ($state); $free KB free"
    # Without SUMS the start button and sta-boot.sh refuse until the end.
    rm -f "$sums"
    txn=active
    for n in $(names); do
        put "$stage/$n" "$top/$n" "$(mode_of "$n")" "$(pin_of "$n")" || fail "could not install $n"
    done
    /bin/ksh -n "$top/sta-mode.sh" 2>/dev/null || fail "$top/sta-mode.sh does not parse"
    # SUMS last: it is what the start button looks for.
    sums_text > "$sums.new.$$" && chmod 600 "$sums.new.$$" &&
        mv -f "$sums.new.$$" "$sums" || fail "could not write $sums"
    [ "$(state_of)" = installed ] || fail "the files are not as installed"
    txn=none
    if conf_ok "$conf"; then
        say "installed; wpa-hotspot.conf is already there."
    else
        say "installed. Next: sta-install.sh conf < the hotspot configuration."
    fi
}

conf_in() {
    take_lock
    not_running
    [ -d "$top" ] || fail "$top is absent; install first"
    new=$conf.new.$$
    rm -f "$new"
    # umask 077: the file is root's and private from the first byte.
    cat > "$new" || { rm -f "$new"; fail "could not write $new"; }
    chmod 600 "$new"
    if ! conf_ok "$new"; then
        rm -f "$new"
        fail "the configuration needs one network={, one ssid=, one psk= and key_mgmt=WPA-PSK, at most $conf_max bytes; nothing was changed"
    fi
    mv -f "$new" "$conf" || { rm -f "$new"; fail "could not move it into place"; }
    conf_ok "$conf" || fail "$conf is not as written"
    say "wpa-hotspot.conf written (root, mode 600); its contents are not shown."
}

hooked() {
    [ -f "$openssh_start" ] && grep -q -x -F -- "$hook_line" "$openssh_start"
}

hook() {
    take_lock
    [ "$(state_of)" = installed ] || fail "the station files are not installed"
    [ -f "$openssh_start" ] || fail "$openssh_start is missing"
    if hooked; then
        say "already hooked"
        return 0
    fi
    ! grep -q 'sta-boot\.sh' "$openssh_start" ||
        fail "$openssh_start names sta-boot.sh in another line; not changing it"
    pre_sum=$(sum_of "$openssh_start")
    cp -p "$openssh_start" "$pre_hook" && [ "$(sum_of "$pre_hook")" = "$pre_sum" ] ||
        fail "could not keep a copy of $openssh_start"
    # The rollback first, outside the HDD payload.
    {
        echo '#!/bin/ksh'
        echo '# Written by sta-install.sh hook. Takes the Wi-Fi station line out of'
        echo '# the OpenSSH start-up again and clears the GEM start-up marker.'
        echo "start=$openssh_start"
        echo "line='$hook_line'"
        echo "pre_sum='$pre_sum'"
        echo "marker=$marker"
        cat <<'ROLLBACK'
set -u
PATH=/bin:/sbin:/usr/bin:/usr/sbin
export PATH
rm -f "$marker"
if ! grep -q -x -F -- "$line" "$start"; then
    echo "wifi-sta rollback: no start-up line in $start; the marker is gone."
    exit 0
fi
new=$start.new.$$
cp -p "$start" "$new" && grep -v -x -F -- "$line" "$start" > "$new" &&
    /bin/ksh -n "$new" && mv -f "$new" "$start" ||
    { rm -f "$new"; echo "wifi-sta rollback: FAIL: $start unchanged" >&2; exit 2; }
set -- $(cksum "$start")
if [ "$1 $2" = "$pre_sum" ]; then
    echo "wifi-sta rollback: start-up line removed; $start is as before the hook ($pre_sum)."
else
    echo "wifi-sta rollback: start-up line removed; $start is $1 $2, not $pre_sum as before the hook (changed since)."
fi
ROLLBACK
    } > "$rollback.new.$$" && chmod 700 "$rollback.new.$$" &&
        /bin/ksh -n "$rollback.new.$$" && mv -f "$rollback.new.$$" "$rollback" ||
        { rm -f "$rollback.new.$$"; fail "could not write $rollback"; }
    new=$openssh_start.new.$$
    { cp -p "$openssh_start" "$new" && cat "$openssh_start" > "$new" &&
        echo "$hook_line" >> "$new" && /bin/ksh -n "$new"; } ||
        { rm -f "$new"; fail "could not prepare the new $openssh_start; unchanged"; }
    mv -f "$new" "$openssh_start" || { rm -f "$new"; fail "could not replace $openssh_start"; }
    hooked || fail "the line is not in $openssh_start; run $rollback"
    say "hooked: $openssh_start $pre_sum -> $(sum_of "$openssh_start"); copy $pre_hook; rollback $rollback."
    say "Nothing starts until the GEM's \"Start-up: join the hotspot\" is pressed."
}

unhook() {
    take_lock
    if [ -f "$rollback" ]; then
        /bin/ksh "$rollback" || fail "the rollback failed"
    elif hooked; then
        fail "$rollback is missing; take the line out of $openssh_start by hand"
    else
        rm -f "$marker"
        say "not hooked"
    fi
    hooked && fail "the line is still in $openssh_start"
    return 0
}

remove() {
    take_lock
    not_running
    state=$(state_of)
    hooked && fail "the start-up line is still in $openssh_start; run unhook first"
    if [ "$state" = absent ] && [ ! -f "$conf" ]; then
        say "nothing is installed"
    else
        remove_ours || fail "removal incomplete (state $(state_of))"
        rm -f "$conf"
        [ ! -f "$conf" ] || fail "could not remove $conf"
        say "removed (was $state), with the configuration."
    fi
    rm -f "$top/boot.log" "$top/boot.log.old" "$pre_hook" "$rollback"
    if [ -d "$top" ]; then
        rmdir "$top" 2>/dev/null || say "$top is not empty; left in place"
    fi
    return 0
}

trap finish 0
# A dropped SSH session must not stop a copy halfway; an interrupt or a
# kill undoes it.
trap '' 1
trap 'say "interrupted"; exit 2' 2 15
[ $# -ge 1 ] || usage
case $1 in
    status) [ $# = 1 ] || usage; status ;;
    install) [ $# = 2 ] || usage; install "$2" ;;
    conf) [ $# = 1 ] || usage; conf_in ;;
    hook) [ $# = 1 ] || usage; hook ;;
    unhook) [ $# = 1 ] || usage; unhook ;;
    remove) [ $# = 1 ] || usage; remove ;;
    *) usage ;;
esac
