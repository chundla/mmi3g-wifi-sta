#!/bin/ksh
# Phase 3 probe: detach the factory access-point driver, establish whether a
# re-attach reloads the 88W8688 firmware, and restore the driver. Everything it
# writes lives in RAM (/fs/tmpfs); nothing is installed and nothing persists.
#
# Run it detached from a session that does NOT arrive on uap0, and read the
# report from a second session. There is no nohup on this unit; ignoring SIGHUP
# before exec survives the exec, which does the same job:
#     /bin/ksh -c 'trap "" HUP; exec /fs/tmpfs/radio-reattach.sh' \
#         > /fs/tmpfs/radio-run.log 2>&1 &
# dev_detach() quiesces the whole io-pkt stack while the driver detaches, and
# the driver waits up to 16 s for pending firmware commands, so every interface
# -- including the wired management link -- can stall for that long. A stall is
# not a failure.
#
# Oracle: with fwpath pointing at a directory that holds no FwImage, the driver
# can only attach if the firmware is still running. If it was cleared, the
# helper load fails and mv8688uap_probe logs "uap_init_fw() failed." to slog.
set -u
umask 077
PATH=/bin:/sbin:/usr/bin:/usr/sbin
export PATH
# The factory boot environment preloads libmc.so, which creates a second
# thread; keep that instrumentation out of everything this script starts.
unset LD_PRELOAD || :

driver=/lib/dll/devnp-mv8688uap.so
driver_sum='1022510863 93538'
# The factory mount, from nws.cfg. Only resetmask changes between probes.
mount_base='irq=13,dma=0x104,clock=25M'
firmware_path=/lib/dll
ap_address=192.168.1.1
ap_netmask=255.255.255.0
state=/fs/tmpfs/mmi-radio
deadline=900
settle=20
probe_b=yes
# probes: the empty-fwpath firmware probes. factory: detach and re-attach with
# the factory parameters, which only means anything when the instance being
# detached was itself mounted with resetmask=0, as the factory's is.
# host: detach, run the phase 4.1 host bring-up probe (-P), then re-attach with
# the factory parameters. The probe never drives the reset line. With -H and
# -S it also downloads those images (phase 4.2); it refuses unpinned ones.
# With -C as well it sends the station firmware its first commands (4.3).
# With -L as well it gives the station the access point's factory MAC, read
# from uap0 before the detach, and runs a passive scan (4.4). With -A as well
# it joins that open network, stays associated for 15 s and deauthenticates
# (5.0): the first run in which the radio transmits. With -I and -E as well
# it sends data frames instead of the hold: ARP and ICMP echoes from that
# station address to that peer (5.1). With -T as well
# it times every transfer and benchmarks reads of the firmware's scratch
# register before the commands (4.5).
# driver (-D): detach, then mount the phase 5.2 station driver into io-pkt as
# en6 with uap0's factory MAC, after pf rules that admit only echo requests
# from the peer on it; join the open network -A with the control tool -K, ping
# the peer -E from station address -I, hold -W seconds for the peer to ping
# back, leave, destroy en6, check with the probe -P that the module is idle
# and the firmware gone, remove the rules, then re-attach the factory driver.
# The driver runs inside the shared io-pkt: a fault there takes mam0 and en5
# down until a restart. With -d instead of -I (5.3), the factory dhcp.client
# takes en6's address from the peer's DHCP server after the join, the rules
# also admit the server's replies, and the run records what the lease and the
# factory dhcp-up script did to routes and the resolver before it stops that
# dhcp.client again. With -w and -c (phase 6), wpa_supplicant -w joins the
# network -A with configuration -c (a WPA2-PSK network; root-owned, mode 600,
# never copied into the log) instead of the control tool, and is stopped
# before the station leaves; the random bytes its handshake needs come from
# jitter-egd, which must be running. With -l as well (7.2), the factory LAN-mode
# flag is set: dhcp-up keeps the lease's default route and points resolv.conf
# and the dnsRedirect anchors at its name servers, which the run requires
# instead of forbidding; en6 also gets the factory's NAT rule, RAM only.
# A hold ends early, with every end-of-run step, once the file $state/stop
# exists (touch /fs/tmpfs/mmi-radio/stop from a second session).
mode=probes
dry=no
host_probe=
fw_helper=
fw_image=
fw_commands=no
fw_scan=no
fw_timing=no
ap_mac=
join_ssid=
station_ip=
peer_ip=
sta_driver=
sta_ctl=
hold=60
sta_if=en6
sta_lan=6
pf_label=mmi_sta_en6
stuck_driver=no
dhcp=no
wpa_bin=
wpa_conf=
reconnects=no
wpa_wait=60
lan=no
# The factory DHCP client and the script it runs after every lease, as
# reviewed (saved IFS of 2026-10-20 and the live copy of 2026-10-23). dhcp-up
# deletes the default route and rewrites resolv.conf; see the 5.3 notes.
dhcp_client=/usr/sbin/dhcp.client
dhcp_client_sum='263280565 57048'
dhcp_up=/etc/dhcp/dhcp-up
dhcp_up_sum='3046797907 6292'
dhcp_down_marker=/tmp/ifwatchd_dhcp_down
dhcp_semaphore=/dev/shmem/networkscript.semaphore
resolv_conf=/etc/resolv.conf
# With either of these, dhcp-up takes branches that rewrite pf anchors or set
# the default route to mam0; only the branch without them was reviewed.
dlink_marker=/HBpersistence/DLinkReplacesPPP
rse_marker=/mnt/efs-system/RSE
ppp_route_marker=/tmp/ppp0_defaultroute

usage() {
    echo "usage: ${0##*/} [-t watchdog-cap-seconds] [-1 | -F | -P probe [-H helper -S image [-C [-L [-A ssid [-I ip -E peer]]] [-T]]]] [-n]" >&2
    echo "       ${0##*/} [-t cap] -D driver -K ctl -P probe -H helper -S image -A ssid {-I ip | -d [-l]} -E peer [-W hold] [-w wpa_supplicant -c config] [-n]" >&2
    echo "  -1  stop after the register 0x28 probe (skip the reset-line probe)" >&2
    echo "  -F  no probes: detach, then re-attach with the factory parameters" >&2
    echo "  -P  run the host bring-up probe between the detach and the factory re-attach" >&2
    echo "  -H, -S  with -P: have the probe download this helper and firmware image" >&2
    echo "  -C  with -H and -S: then send the station firmware its first commands" >&2
    echo "  -L  with -C: set uap0's factory MAC on the station, then scan passively" >&2
    echo "  -A  with -L: join this open network (letters, digits, . _ -), hold, deauthenticate" >&2
    echo "  -I, -E  with -A: instead of the hold, ARP and ping peer -E from station address -I" >&2
    echo "  -T  with -C: time the transfers and benchmark PIO reads first" >&2
    echo "  -D  mount this station driver as $sta_if instead of a probe; -K is its control tool" >&2
    echo "  -d  with -D, instead of -I: take the station address by the factory dhcp.client" >&2
    echo "  -l  with -d: LAN mode is set; the lease must become the default route and resolver" >&2
    echo "  -W  with -D: seconds associated for the peer to ping the station (default $hold)" >&2
    echo "  -w, -c  with -D: join -A with this wpa_supplicant and configuration (root, mode 600)" >&2
    echo "  -R  with -w: the hold expects the link to drop and come back (reconnect tests)" >&2
    echo "  -n  preflight and report only; touch no interface" >&2
    exit 2
}
# -1, -F, -P and -D each pick the experiment; more than one is an operator
# error, not a choice for the last one to win. With -D, -P only names the
# probe that checks the module.
modes=0
while getopts ':t:1FP:H:S:CLA:I:E:TD:K:W:dlw:c:Rn' option; do
    case $option in
        t) deadline=$OPTARG;;
        1) probe_b=no; modes=$((modes + 1));;
        F) mode=factory; modes=$((modes + 1));;
        P) host_probe=$OPTARG; [ -n "$OPTARG" ] || usage;;
        D) mode=driver; sta_driver=$OPTARG; modes=$((modes + 1))
           [ -n "$OPTARG" ] || usage;;
        K) sta_ctl=$OPTARG; [ -n "$OPTARG" ] || usage;;
        W) hold=$OPTARG;;
        d) dhcp=yes;;
        l) lan=yes;;
        w) wpa_bin=$OPTARG; [ -n "$OPTARG" ] || usage;;
        c) wpa_conf=$OPTARG; [ -n "$OPTARG" ] || usage;;
        R) reconnects=yes;;
        H) fw_helper=$OPTARG; [ -n "$OPTARG" ] || usage;;
        S) fw_image=$OPTARG; [ -n "$OPTARG" ] || usage;;
        C) fw_commands=yes;;
        L) fw_scan=yes;;
        A) join_ssid=$OPTARG; [ -n "$OPTARG" ] || usage;;
        I) station_ip=$OPTARG; [ -n "$OPTARG" ] || usage;;
        E) peer_ip=$OPTARG; [ -n "$OPTARG" ] || usage;;
        T) fw_timing=yes;;
        n) dry=yes;;
        *) usage;;
    esac
done
shift $((OPTIND - 1))
[ $# -eq 0 ] || usage
if [ -z "$sta_driver" ] && [ -n "$host_probe" ]; then
    mode=host
    modes=$((modes + 1))
fi
[ "$modes" -le 1 ] || usage
case $hold in ''|*[!0-9]*) usage;; esac
[ "$hold" -ge 10 ] && [ "$hold" -le 600 ] || usage
# The driver mode takes everything a join with data frames does, and none
# of the probe's own steps. Its station address is -I or a lease (-d), not both.
if [ "$mode" = driver ]; then
    [ -n "$sta_ctl" ] && [ -n "$host_probe" ] && [ -n "$fw_image" ] && [ -n "$peer_ip" ] ||
        usage
    if [ "$dhcp" = yes ]; then [ -z "$station_ip" ] || usage; else [ -n "$station_ip" ] || usage; fi
    [ "$fw_commands" = no ] && [ "$fw_scan" = no ] && [ "$fw_timing" = no ] || usage
    # The supplicant and its configuration together, or neither.
    [ -z "$wpa_bin" ] && [ -z "$wpa_conf" ] || { [ -n "$wpa_bin" ] && [ -n "$wpa_conf" ]; } ||
        usage
    # Only the supplicant reconnects on its own.
    [ "$reconnects" = no ] || [ -n "$wpa_bin" ] || usage
else
    [ -z "$sta_ctl" ] && [ "$dhcp" = no ] && [ -z "$wpa_bin" ] && [ -z "$wpa_conf" ] &&
        [ "$reconnects" = no ] || usage
fi
# LAN mode is about what the lease does, so it needs one.
[ "$lan" = no ] || [ "$dhcp" = yes ] || usage
case $deadline in ''|*[!0-9]*) usage;; esac
[ "$deadline" -ge 60 ] || usage
# Both images or neither, and only for the host probe.
[ -z "$fw_helper" ] && [ -z "$fw_image" ] ||
    { [ -n "$fw_helper" ] && [ -n "$fw_image" ] &&
      { [ "$mode" = host ] || [ "$mode" = driver ]; }; } || usage
# Commands only after a download; the probe itself refuses a non-station image.
[ "$fw_commands" = no ] || [ -n "$fw_image" ] || usage
[ "$fw_scan" = no ] || [ "$fw_commands" = yes ] || usage
# The SSID reaches the probe as one word; keep it to characters no shell or
# log line treats specially. The probe itself takes any 1-32 bytes.
if [ -n "$join_ssid" ]; then
    [ "$fw_scan" = yes ] || [ "$mode" = driver ] || usage
    case $join_ssid in *[!A-Za-z0-9._-]*) usage;; esac
    [ "${#join_ssid}" -le 32 ] || usage
fi
# Both addresses or neither, with a join; digits and dots only here, the
# probe parses them strictly.
[ -z "$station_ip" ] && [ -z "$peer_ip" ] ||
    { { [ -n "$station_ip" ] || [ "$dhcp" = yes ]; } && [ -n "$peer_ip" ] && [ -n "$join_ssid" ]; } ||
    usage
dotted_quad() {
    case $1 in *[!0-9.]*|*.*.*.*.*) return 1;; *.*.*.*) return 0;; *) return 1;; esac
}
if [ -n "$peer_ip" ]; then
    dotted_quad "$peer_ip" && { [ -z "$station_ip" ] || dotted_quad "$station_ip"; } || usage
fi
[ "$fw_timing" = no ] || [ "$fw_commands" = yes ] || usage

verdict=OK
report=
beats=0
# Every stage logs, so the log is also the watchdog's heartbeat.
log() {
    echo "radio: $*"
    [ -n "$report" ] && echo "radio: $*" >> "$report"
    beats=$((beats + 1))
    [ -d "$state" ] && echo "$beats" > "$state/heartbeat"
    return 0
}
fail() {
    verdict=FAIL
    log "FAILED: $*"
}
degrade() {
    [ "$verdict" = FAIL ] || verdict=PARTIAL
    log "PARTIAL: $*"
}

# Diagnostics on this unit can hang (pidin on some processes does). Bound every
# one of them and record the timeout rather than waiting forever.
bounded() {
    bounded_limit=$1
    bounded_out=$2
    shift 2
    # Not in a subshell: $! must be the command itself, so a timeout (or the
    # watchdog, through $bounded_pidfile) stops it rather than its parent.
    "$@" > "$bounded_out" 2>&1 &
    bounded_pid=$!
    [ -n "$bounded_pidfile" ] && echo "$bounded_pid" > "$bounded_pidfile"
    bounded_waited=0
    while kill -0 "$bounded_pid" 2>/dev/null; do
        if [ "$bounded_waited" -ge "$bounded_limit" ]; then
            # TERM first: the host probe powers the card down on it.
            kill "$bounded_pid" 2>/dev/null
            sleep 3
            kill -9 "$bounded_pid" 2>/dev/null
            echo "TIMEOUT after ${bounded_limit}s: $*" >> "$bounded_out"
            [ -n "$bounded_pidfile" ] && rm -f "$bounded_pidfile"
            return 1
        fi
        sleep 1
        bounded_waited=$((bounded_waited + 1))
    done
    wait "$bounded_pid"
    bounded_rc=$?
    [ -n "$bounded_pidfile" ] && rm -f "$bounded_pidfile"
    return $bounded_rc
}
bounded_pidfile=

# sloginfo prints the whole buffer, so remember how far it reached and read only
# what follows. The buffer wraps under load; a wrap shows up as a short read.
slog_mark() {
    # This unit has no wc, and its grep rejects an empty pattern while
    # 'grep -c .' skips the blank lines slog writes. sed counts exactly.
    slog_seen=$(sloginfo -t 2>/dev/null | sed -n '$=')
    case $slog_seen in ''|*[!0-9]*) slog_seen=0;; esac
}
slog_since() {
    sloginfo -t 2>/dev/null | sed -n "$((slog_seen + 1)),\$p"
}
# The driver logs under major codes 1007 and 10.
slog_driver() {
    slog_since | grep -E ' (1007|10) ' | grep -vE 'multicored|timeError'
}

interface_present() {
    ifconfig "$1" > /dev/null 2>&1
}
# Both wait for uap0 unless given another interface.
wait_gone() {
    waited=0
    while interface_present "${2:-uap0}"; do
        [ "$waited" -lt "$1" ] || return 1
        sleep 1
        waited=$((waited + 1))
    done
    return 0
}
wait_present() {
    waited=0
    until interface_present "${2:-uap0}"; do
        [ "$waited" -lt "$1" ] || return 1
        sleep 1
        waited=$((waited + 1))
    done
    return 0
}

# The station interface's filter rules: inbound on it, only echo requests from
# the peer to the station address; everything else is blocked. With -d the
# address is not known yet, so echo requests from the peer to any address,
# and the peer's DHCP replies, which no state matches: dhcp.client asks from
# 0.0.0.0 to the broadcast address over a UDP socket, not BPF. Filter rules
# only, loaded and checked as openssh/session-firewall.sh does on this unit,
# so NAT and states are untouched. Both rules carry $pf_label, and disable
# removes exactly those. Returns non-zero, with the rules as they were, on
# any failure; $state/pf.err says why.
sta_pf() {
    : > "$state/pf.err"
    pfctl -sr > "$state/pf.current" 2>> "$state/pf.err" || return 1
    pf_count=$(grep -c "label \"$pf_label\"" "$state/pf.current")
    if [ "$1" = enable ]; then
        [ "$pf_count" = 0 ] || { echo "rules already present" >> "$state/pf.err"; return 1; }
        [ "$(grep -c '^pass out ' "$state/pf.current")" = 1 ] ||
            { echo "no single 'pass out' rule to insert before" >> "$state/pf.err"; return 1; }
        while IFS= read -r pf_line; do
            case $pf_line in
                'pass out '*)
                    if [ "$dhcp" = yes ]; then
                        echo "pass in quick on $sta_if inet proto udp from $peer_ip port 67 to any port 68 keep state label \"$pf_label\""
                        echo "pass in quick on $sta_if inet proto icmp from $peer_ip to any icmp-type echoreq keep state label \"$pf_label\""
                    else
                        echo "pass in quick on $sta_if inet proto icmp from $peer_ip to $station_ip icmp-type echoreq keep state label \"$pf_label\""
                    fi
                    echo "block drop in quick on $sta_if all label \"$pf_label\"";;
            esac
            echo "$pf_line"
        done < "$state/pf.current" > "$state/pf.next"
        pf_want=2
        [ "$dhcp" = no ] || pf_want=3
    else
        [ "$pf_count" != 0 ] || return 0
        grep -v "label \"$pf_label\"" "$state/pf.current" > "$state/pf.next"
        pf_want=0
    fi
    pfctl -n -R -f "$state/pf.next" >> "$state/pf.err" 2>&1 || return 1
    if pfctl -R -f "$state/pf.next" >> "$state/pf.err" 2>&1 &&
            pfctl -sr > "$state/pf.verified" 2>> "$state/pf.err" &&
            [ "$(grep -c "label \"$pf_label\"" "$state/pf.verified")" = "$pf_want" ]; then
        return 0
    fi
    echo "loaded rules did not verify; restoring the previous set" >> "$state/pf.err"
    pfctl -R -f "$state/pf.current" >> "$state/pf.err" 2>&1
    return 1
}

# --- wpa_supplicant on the station (-w) ---------------------------------------
# TERM makes wpa_supplicant deauthenticate through the driver and exit; KILL
# after 10 s. Defined before the watchdog forks, which calls it too.
sta_wpa_stop() {
    wpa_pid=$(cat "$state/wpa.pid" 2>/dev/null)
    if [ -z "$wpa_pid" ] || ! kill -0 "$wpa_pid" 2>/dev/null; then
        echo "wpa_supplicant was not running."
        rm -f "$state/wpa.pid"
        return 0
    fi
    kill "$wpa_pid" 2>/dev/null
    stopped=0
    while kill -0 "$wpa_pid" 2>/dev/null && [ "$stopped" -lt 10 ]; do
        sleep 1
        stopped=$((stopped + 1))
    done
    if kill -0 "$wpa_pid" 2>/dev/null; then
        kill -9 "$wpa_pid" 2>/dev/null
        echo "wpa_supplicant (pid $wpa_pid) did not stop on TERM in 10 s; killed."
        rm -f "$state/wpa.pid"
        return 1
    fi
    echo "wpa_supplicant (pid $wpa_pid) stopped on TERM after ${stopped}s."
    rm -f "$state/wpa.pid"
    return 0
}
# --- DHCP on the station (-d) ----------------------------------------------------
# The dhcp.client this run starts is the one whose arguments end in
# "-i $sta_if"; the factory's own on en5 (-b -i en5) never matches.
sta_dhcp_pids() {
    bounded 20 "$state/dhcp-pidin.txt" pidin -f aA || return 1
    sed -n "s/^ *\([0-9][0-9]*\) [^ ]*dhcp\.client .* -i $sta_if *\$/\1/p" "$state/dhcp-pidin.txt"
}
# Stops it, TERM first, and says what it did. Non-zero if one is still there.
# It runs as a daemon once configured, so it is not a child of this script.
sta_dhcp_stop() {
    dhcp_pids=$(sta_dhcp_pids) || { echo "pidin failed or timed out"; return 1; }
    if [ -z "$dhcp_pids" ]; then
        echo "no dhcp.client for $sta_if is running."
        return 0
    fi
    echo "stopping dhcp.client for $sta_if, pid" $dhcp_pids
    kill $dhcp_pids 2>/dev/null
    for dhcp_pid in $dhcp_pids; do
        dhcp_waited=0
        while kill -0 "$dhcp_pid" 2>/dev/null && [ "$dhcp_waited" -lt 10 ]; do
            sleep 1
            dhcp_waited=$((dhcp_waited + 1))
        done
        if kill -0 "$dhcp_pid" 2>/dev/null; then
            echo "pid $dhcp_pid ignored TERM; sending KILL."
            kill -9 "$dhcp_pid" 2>/dev/null
            sleep 1
        fi
    done
    dhcp_pids=$(sta_dhcp_pids) || { echo "pidin failed after the stop"; return 1; }
    [ -z "$dhcp_pids" ] || { echo "still running: pid" $dhcp_pids; return 1; }
    echo "stopped."
}
# Destination, gateway and interface of each route in a netstat -rn snapshot,
# without its counters and without the host entries the stack clones or
# learns by ARP (flags c and L), which come and go by themselves.
route_keys() {
    while read -r r_dest r_gw r_flags r_refs r_use r_mtu r_if r_rest; do
        [ -n "$r_if" ] && [ "$r_dest" != Destination ] || continue
        case $r_flags in *[cL]*) continue;; esac
        echo "$r_dest $r_gw $r_if"
    done < "$state/$1.txt" > "$state/$1.keys"
}
key_in() {
    while IFS= read -r k_line; do
        [ "$k_line" = "$1" ] && return 0
    done < "$2"
    return 1
}
# Logs every route that differs between snapshots $1 and $2, which $3 names.
# Non-zero if a default route came or went or a mam0 route went; which of the
# two is left in route_default_changed and route_mam0_lost.
route_changes() {
    route_keys "$1"
    route_keys "$2"
    routes_ok=0
    route_default_changed=no
    route_mam0_lost=no
    while IFS= read -r r_key; do
        key_in "$r_key" "$state/$2.keys" && continue
        log "routes: $3 removed '$r_key'."
        case $r_key in
            'default '*) routes_ok=1; route_default_changed=yes;;
            *' mam0') routes_ok=1; route_mam0_lost=yes;;
        esac
    done < "$state/$1.keys"
    while IFS= read -r r_key; do
        key_in "$r_key" "$state/$1.keys" && continue
        log "routes: $3 added '$r_key'."
        case $r_key in 'default '*) routes_ok=1; route_default_changed=yes;; esac
    done < "$state/$2.keys"
    return $routes_ok
}
# The default routes in snapshot $1 (after route_keys), one per line.
default_routes() {
    grep '^default ' "$state/$1.keys"
}
# What a lease can change in the resolver: resolv.conf and _CS_RESOLVE.
resolver_snapshot() {
    {
        if [ -f "$resolv_conf" ]; then
            echo "resolv.conf:"
            cat "$resolv_conf"
        else
            echo "resolv.conf: absent"
        fi
        echo "_CS_RESOLVE:"
        bounded 5 "$state/getconf.txt" getconf _CS_RESOLVE
        cat "$state/getconf.txt"
    } > "$state/resolver-$1.txt" 2>&1
}
same_resolver() {
    [ "$(cat "$state/resolver-$1.txt")" = "$(cat "$state/resolver-$2.txt")" ]
}
# /etc is the system flash (/mnt/efs-system/etc). A lease that carries DNS
# servers has dhcp.client write resolv.conf, and the factory dhcp-down
# leaves it empty (6.3, the phone's lease). When the baseline had none, the
# file is the run's: a copy goes to the state directory and it is removed.
# When it had one (LAN mode: en5's lease wrote it), the run's goes to the state
# directory and the baseline's bytes are put back; a file dhcp-up creates is
# mode 666, so a re-created one is too.
resolver_undo() {
    if grep -q '^resolv.conf: absent$' "$state/resolver-baseline.txt" 2>/dev/null; then
        [ -f "$resolv_conf" ] || return 0
        cp "$resolv_conf" "$state/resolv.conf.$1" 2>/dev/null
        if rm -f "$resolv_conf" && [ ! -f "$resolv_conf" ]; then
            log "resolver: removed the $resolv_conf the run left ($1; copy kept as resolv.conf.$1)."
        else
            degrade "resolver: $resolv_conf, absent before the run, could not be removed."
        fi
        return 0
    fi
    [ -f "$state/resolv.conf.baseline" ] || return 0
    if [ -f "$resolv_conf" ] &&
            [ "$(cat "$state/resolv.conf.baseline")" = "$(cat "$resolv_conf")" ]; then
        return 0
    fi
    [ ! -f "$resolv_conf" ] || cp "$resolv_conf" "$state/resolv.conf.$1" 2>/dev/null
    if (umask 0; cat "$state/resolv.conf.baseline" > "$resolv_conf") &&
            [ "$(cat "$state/resolv.conf.baseline")" = "$(cat "$resolv_conf")" ]; then
        log "resolver: put back the $resolv_conf from before the run ($1; the run's kept as resolv.conf.$1)."
    else
        degrade "resolver: the $resolv_conf from before the run could not be put back; see resolv.conf.baseline."
    fi
}
# --- LAN mode (-l) ---------------------------------------------------------------
# The four anchors dhcp-up fills with DNS redirects; recorded, not restored:
# the restart that brings the access point back reloads them.
lan_anchors() {
    for anchor in dnsRedirect1 dnsRedirect2 dnsRedirect3 dnsRedirect4; do
        echo "$anchor:"
        pfctl -a "$anchor" -sn 2>&1
    done > "$state/anchors-$1.txt"
}
# The factory translates <natRangeTable> (the access point's subnet and the MOST
# device 10.0.0.101) on ppp0 and en5 only. The same rule for the station goes
# in after en5's, in RAM. pfctl -N replaces the main NAT set only; it must leave
# the anchors' rules and the table's entries alone, which is checked, not
# assumed. NAT rules carry no label, so the rule is matched by its exact text as
# pfctl -sn prints it. Returns non-zero, with the set as it was, on any
# failure; $state/nat.err says why.
nat_rule="nat on $sta_if from <natRangeTable> to any -> ($sta_if) round-robin"
nat_extras() {
    lan_anchors "nat-$1"
    pfctl -t natRangeTable -T show > "$state/nat-table-$1.txt" 2>&1
}
sta_nat() {
    : > "$state/nat.err"
    pfctl -sn > "$state/nat.current" 2>> "$state/nat.err" || return 1
    nat_count=$(grep -cxF "$nat_rule" "$state/nat.current")
    if [ "$1" = enable ]; then
        [ "$nat_count" = 0 ] || { echo "the rule is already present" >> "$state/nat.err"; return 1; }
        [ "$(grep -c '^nat on en5 from <natRangeTable> ' "$state/nat.current")" = 1 ] ||
            { echo "no single en5 NAT rule to follow" >> "$state/nat.err"; return 1; }
        while IFS= read -r nat_line; do
            echo "$nat_line"
            case $nat_line in 'nat on en5 from <natRangeTable> '*) echo "$nat_rule";; esac
        done < "$state/nat.current" > "$state/nat.next"
        nat_want=1
    else
        [ "$nat_count" != 0 ] || return 0
        grep -vxF "$nat_rule" "$state/nat.current" > "$state/nat.next"
        nat_want=0
    fi
    nat_extras before
    pfctl -n -N -f "$state/nat.next" >> "$state/nat.err" 2>&1 || return 1
    if pfctl -N -f "$state/nat.next" >> "$state/nat.err" 2>&1 &&
            pfctl -sn > "$state/nat.verified" 2>> "$state/nat.err" &&
            [ "$(grep -cxF "$nat_rule" "$state/nat.verified")" = "$nat_want" ] &&
            nat_extras after &&
            [ "$(cat "$state/anchors-nat-before.txt")" = "$(cat "$state/anchors-nat-after.txt")" ] &&
            [ "$(cat "$state/nat-table-before.txt")" = "$(cat "$state/nat-table-after.txt")" ]; then
        return 0
    fi
    echo "the loaded NAT set did not verify, or an anchor or the table changed; restoring the previous set" >> "$state/nat.err"
    pfctl -N -f "$state/nat.current" >> "$state/nat.err" 2>&1
    return 1
}
# Every default route in snapshot $1 is the lease's (there may be none).
lan_defaults_ok() {
    lan_extra=$(default_routes "$1" | grep -vxF "default $peer_ip $sta_if")
    [ -z "$lan_extra" ]
}

# --- preflight -------------------------------------------------------------
[ "$(uname -s)" = QNX ] || { echo "radio: run this on the MMI." >&2; exit 1; }
[ -s "$driver" ] || { echo "radio: $driver is missing." >&2; exit 1; }
set -- $(cksum "$driver")
[ "$1 $2" = "$driver_sum" ] || {
    echo "radio: $driver is not the reviewed factory object ($1 $2)." >&2
    exit 1
}
if [ "$mode" = host ] || [ "$mode" = driver ]; then
    [ -f "$host_probe" ] && [ -x "$host_probe" ] ||
        { echo "radio: host probe $host_probe is missing or not executable." >&2; exit 1; }
    if [ -n "$fw_image" ]; then
        for image in "$fw_helper" "$fw_image"; do
            [ -f "$image" ] && [ -s "$image" ] ||
                { echo "radio: firmware image $image is missing or empty." >&2; exit 1; }
        done
    fi
fi
if [ "$mode" = driver ]; then
    [ -f "$sta_driver" ] && [ -s "$sta_driver" ] ||
        { echo "radio: station driver $sta_driver is missing or empty." >&2; exit 1; }
    [ -f "$sta_ctl" ] && [ -x "$sta_ctl" ] ||
        { echo "radio: control tool $sta_ctl is missing or not executable." >&2; exit 1; }
    if interface_present "$sta_if"; then
        echo "radio: $sta_if already exists; destroy it or restart the unit first." >&2
        exit 1
    fi
fi
# The configuration holds the network's key: it must be root's alone, and it
# must hold exactly one network, -A with WPA-PSK, so the log can name the
# network without the file (the backend scans for one named SSID only).
if [ "$mode" = driver ] && [ -n "$wpa_bin" ]; then
    [ -f "$wpa_bin" ] && [ -x "$wpa_bin" ] ||
        { echo "radio: wpa_supplicant $wpa_bin is missing or not executable." >&2; exit 1; }
    [ -f "$wpa_conf" ] && [ -s "$wpa_conf" ] ||
        { echo "radio: configuration $wpa_conf is missing or empty." >&2; exit 1; }
    set -- $(ls -l "$wpa_conf")
    [ "$1" = "-rw-------" ] && [ "$3" = root ] ||
        { echo "radio: $wpa_conf must be owned by root with mode 600 (is $1 $3)." >&2; exit 1; }
    # Read through pipes: no copy of the key is written anywhere.
    wpa_lines() { sed -e 's/^[ 	]*//' -e 's/[ 	]*$//' "$wpa_conf"; }
    wpa_networks=$(wpa_lines | grep -c '^network={')
    wpa_named=$(wpa_lines | grep -Fxc "ssid=\"$join_ssid\"")
    wpa_psk=$(wpa_lines | grep -Fxc 'key_mgmt=WPA-PSK')
    [ "$wpa_networks" = 1 ] && [ "$wpa_named" = 1 ] && [ "$wpa_psk" = 1 ] || {
        echo "radio: $wpa_conf must hold exactly one network, ssid=\"$join_ssid\" with key_mgmt=WPA-PSK." >&2
        exit 1
    }
fi
# The lease runs the factory dhcp-up; what it does to routes and the resolver
# was read from these exact copies.
if [ "$mode" = driver ] && [ "$dhcp" = yes ]; then
    [ -f "$dhcp_client" ] && [ -x "$dhcp_client" ] ||
        { echo "radio: $dhcp_client is missing or not executable." >&2; exit 1; }
    set -- $(cksum "$dhcp_client")
    [ "$1 $2" = "$dhcp_client_sum" ] || {
        echo "radio: $dhcp_client is not the reviewed factory binary ($1 $2)." >&2
        exit 1
    }
    [ -f "$dhcp_up" ] || { echo "radio: $dhcp_up is missing." >&2; exit 1; }
    set -- $(cksum "$dhcp_up")
    [ "$1 $2" = "$dhcp_up_sum" ] || {
        echo "radio: $dhcp_up is not the reviewed factory script ($1 $2)." >&2
        exit 1
    }
    if [ -e "$rse_marker" ]; then
        echo "radio: $rse_marker exists; dhcp-up would take a branch this run was not reviewed for." >&2
        exit 1
    fi
    # LAN mode is the flag's branch, and only an empty flag is lan-mode.sh's.
    if [ "$lan" = yes ]; then
        [ -f "$dlink_marker" ] && [ ! -s "$dlink_marker" ] || {
            echo "radio: -l needs the empty flag $dlink_marker (lan-mode.sh enable, then a restart)." >&2
            exit 1
        }
    elif [ -e "$dlink_marker" ]; then
        echo "radio: $dlink_marker exists; dhcp-up would take its LAN-mode branch (that is -l)." >&2
        exit 1
    fi
fi
[ -d "$state" ] || mkdir -p "$state" || { echo "radio: cannot create $state." >&2; exit 1; }
chmod 700 "$state"
# The supplicant's only source of random bytes (the unit has no /dev/random).
if [ "$mode" = driver ] && [ -n "$wpa_bin" ]; then
    if bounded 20 "$state/egd-check.txt" pidin -f aA &&
            grep -q 'jitter-egd' "$state/egd-check.txt"; then :; else
        echo "radio: jitter-egd is not running (or pidin did not answer); wpa_supplicant would have no random bytes." >&2
        echo "radio: rerun /mnt/persistence/openssh/start.sh (it starts jitter-egd) first." >&2
        exit 1
    fi
fi
rm -f "$state/disarm" "$state/probe.pid" "$state/dhcp.started" "$state/wpa.pid" \
    "$state/wpa.reported" "$state/stop"
# Whoever mounts the driver -- this run or its watchdog -- first creates this
# directory (mkdir is atomic) and writes its pid inside; the other then leaves
# the driver alone, so two mounts can never race into the shared io-pkt.
restore_lock=$state/restore.owner
rm -rf "$restore_lock"
# An empty directory is the whole oracle: no FwImage means no firmware to load.
nofw=$state/nofw
[ -d "$nofw" ] || mkdir "$nofw" || { echo "radio: cannot create $nofw." >&2; exit 1; }
rm -rf "$nofw/FwImage"
report=$state/report.txt
: > "$report" || { echo "radio: cannot write $report." >&2; exit 1; }

# The unit has no calendar time: its clock counts from the epoch at start-up,
# so this stamp is an uptime. Record it as the only available time source.
log "started at unit clock $(date '+%Y-%m-%dT%H:%M:%S') (uptime, not calendar time)"
log "driver $driver cksum $driver_sum"
# QNX df has no -h, and prints the one filesystem without a header here.
log "state volume: $(df -k "$state" 2>/dev/null | sed -n '$p')"
# Destroying uap0 drops every session that arrives on it. Refuse in that case:
# the wired management path must be carrying this session.
client=${SSH_CONNECTION-}
[ -n "$client" ] || client=${SSH_CLIENT-}
client=${client%% *}
case $client in
    192.168.1.*)
        echo "radio: this session arrives on uap0 ($client); run it over en5." >&2
        exit 1;;
    '') log "no SSH_CONNECTION in the environment; cannot confirm the session path.";;
    *) log "session client $client is not on the uap0 subnet.";;
esac
if interface_present en5; then
    if ifconfig en5 2>/dev/null | grep -q 'status: active'; then
        log "management interface en5 is up with carrier."
    else
        degrade "en5 exists but reports no carrier."
    fi
else
    echo "radio: en5 is absent; there is no management path independent of uap0." >&2
    exit 1
fi

bounded 10 "$state/baseline-ifconfig.txt" ifconfig -a ||
    degrade "ifconfig -a timed out."

# The station takes the access point's factory MAC. Only the factory mount
# programs it, so a uap0 mounted by hand since the last start-up shows all
# zeros; refuse that, and anything a station must not use, before the detach.
if [ "$fw_scan" = yes ] || [ "$mode" = driver ]; then
    bounded 10 "$state/baseline-uap0.txt" ifconfig uap0 ||
        { echo "radio: ifconfig uap0 failed or timed out." >&2; exit 1; }
    ap_mac=$(sed -n 's/^[ 	]*address: *//p' "$state/baseline-uap0.txt" | sed -e '1!d')
    hex='[0-9a-fA-F]'
    case $ap_mac in
        00:00:00:00:00:00|[aA][aA]:[bB][bB]:[cC][cC]:[dD][dD]:[eE][eE]:[fF][fF])
            ap_mac_ok=no;;
        $hex[02468aAcCeE]:$hex$hex:$hex$hex:$hex$hex:$hex$hex:$hex$hex) ap_mac_ok=yes;;
        *) ap_mac_ok=no;;
    esac
    if [ "$ap_mac_ok" = no ]; then
        echo "radio: uap0 has no usable factory MAC (${ap_mac:-none}); restart the unit first." >&2
        exit 1
    fi
    log "uap0 factory MAC $ap_mac goes to the station firmware."
fi
bounded 10 "$state/baseline-routes.txt" netstat -rn || degrade "netstat timed out."
bounded 20 "$state/baseline-iopkt.txt" pidin -p io-pkt-v4-hc mem ||
    degrade "pidin mem timed out."
if grep -q 'devnp-mv8688uap' "$state/baseline-iopkt.txt" 2>/dev/null; then
    log "io-pkt has the access-point driver object loaded."
else
    degrade "the driver object is not in io-pkt's object list."
fi
for node in /dev/sysregs/WlanEnable /dev/sysregs/BtReset; do
    if [ -e "$node" ]; then
        value=$(bounded 5 "$state/sysreg.txt" cat "$node" && cat "$state/sysreg.txt")
        log "${node##*/} reads ${value:-<unreadable>}"
    else
        degrade "$node is absent."
    fi
done
log "mam0 $(ifconfig mam0 2>/dev/null | grep 'inet ' || echo absent)"
if [ "$mode" = driver ] && [ "$dhcp" = yes ]; then
    log "dhcp.client $dhcp_client cksum $dhcp_client_sum and $dhcp_up cksum $dhcp_up_sum, as reviewed."
    dhcp_running=$(sta_dhcp_pids) || {
        echo "radio: pidin failed; cannot tell whether a dhcp.client already serves $sta_if." >&2
        exit 1
    }
    [ -z "$dhcp_running" ] || {
        echo "radio: a dhcp.client for $sta_if is already running (pid $dhcp_running)." >&2
        exit 1
    }
    # The factory's own client on en5 should be listed here, in the form the
    # stop matches: "<pid> dhcp.client <arguments>".
    grep 'dhcp\.client' "$state/dhcp-pidin.txt" | sed -e 's/^/dhcp-preflight: /' >> "$report"
    if [ "$lan" = yes ]; then
        log "$dlink_marker is set (LAN mode): dhcp-up keeps the lease's default route and points resolv.conf and the dnsRedirect anchors at its name servers."
        # A second lease would compete for the default route (en5's, at home).
        grep -q '^Destination' "$state/baseline-routes.txt" 2>/dev/null || {
            echo "radio: the routing table could not be read; cannot tell whether a default route exists." >&2
            exit 1
        }
        route_keys baseline-routes
        if default_routes baseline-routes > /dev/null; then
            echo "radio: a default route exists ($(default_routes baseline-routes | sed -e '1!d'))." >&2
            echo "radio: in LAN mode the station's lease would compete with it; delete it first (route delete default)." >&2
            exit 1
        fi
        log "no default route before the run."
        lan_anchors baseline
        sed -e 's/^/anchors-baseline: /' "$state/anchors-baseline.txt" >> "$report"
    else
        log "$dlink_marker and $rse_marker are absent: dhcp-up deletes the default route and empties resolv.conf."
    fi
    if [ -e "$ppp_route_marker" ]; then
        log "$ppp_route_marker exists: dhcp-up re-adds that default route afterwards."
    fi
    # dhcp-up starts an ifwatchd for en5 unless this marker exists.
    if [ -e "$dhcp_down_marker" ]; then
        log "$dhcp_down_marker exists: dhcp-up will not start another ifwatchd."
    else
        log "$dhcp_down_marker is absent: dhcp-up will start ifwatchd for en5."
    fi
    resolver_snapshot baseline
    sed -e 's/^/resolver-baseline: /' "$state/resolver-baseline.txt" >> "$report"
    rm -f "$state/resolv.conf.baseline"
    if [ -f "$resolv_conf" ]; then
        cp "$resolv_conf" "$state/resolv.conf.baseline" || {
            echo "radio: cannot keep a copy of $resolv_conf." >&2
            exit 1
        }
    fi
fi

# The probe's own guard reads the module registers and must refuse while the
# factory driver holds them. If it would pass now, it cannot be trusted after
# the detach either, so stop before touching anything.
if [ "$mode" = host ] || [ "$mode" = driver ]; then
    set -- $(cksum "$host_probe")
    log "host probe $host_probe cksum $1 $2"
    if [ "$mode" = driver ]; then
        set -- $(cksum "$sta_driver")
        log "station driver $sta_driver cksum $1 $2"
        set -- $(cksum "$sta_ctl")
        log "control tool $sta_ctl cksum $1 $2"
        if [ -n "$wpa_bin" ]; then
            set -- $(cksum "$wpa_bin")
            log "wpa_supplicant $wpa_bin cksum $1 $2; configuration $wpa_conf (root, mode 600; not logged)"
        fi
    fi
    # The probe checks these against its pinned values before writing.
    if [ -n "$fw_image" ]; then
        for image in "$fw_helper" "$fw_image"; do
            set -- $(cksum "$image")
            log "firmware image $image cksum $1 $2"
        done
    fi
    bounded 30 "$state/guard-attached.txt" "$host_probe" -n
    guard_rc=$?
    sed -e 's/^/guard-attached: /' "$state/guard-attached.txt" >> "$report"
    if [ "$guard_rc" -eq 1 ] &&
            grep -q 'verdict=REFUSED the module is in use' "$state/guard-attached.txt"; then
        log "the probe guard refuses while the factory driver is attached, as it must."
    else
        fail "the probe guard did not refuse the attached module (exit $guard_rc)."
        log "verdict $verdict; nothing was detached."
        exit 1
    fi
fi

if [ "$dry" = yes ]; then
    log "dry run: no interface was touched. verdict $verdict"
    exit 0
fi

# --- dead-man watchdog -----------------------------------------------------
# If this script dies, or the session goes away, the radio must come back
# without anyone logging in. resetmask=4 is deliberate: a probe that leaves the
# reset line asserted can only be cleared by an attach that releases it, and
# the factory resetmask=0 never touches that bit.
#
# It waits for this run to stop making progress rather than for a wall-clock
# deadline: firing while the script is still working could mount a second copy
# of the driver into the shared io-pkt. Each stage bumps a heartbeat, and the
# watchdog acts when the pid is gone, when the heartbeat stops advancing for
# $stall seconds (a killed process can linger as a zombie, which `kill -0`
# still reports as alive), or at the absolute cap.
recover_params="$mount_base,fwpath=$firmware_path,resetmask=4"
factory_params="$mount_base,fwpath=$firmware_path,resetmask=0"
stall=120
main_pid=$$
(
    trap '' 1 2 15
    waited=0
    unchanged=0
    last=
    reason="the cap of ${deadline}s was reached while the run was still alive"
    why=cap
    while [ "$waited" -lt "$deadline" ]; do
        [ -e "$state/disarm" ] && exit 0
        if ! kill -0 "$main_pid" 2>/dev/null; then
            reason="the run is gone"
            why=gone
            break
        fi
        now=$(cat "$state/heartbeat" 2>/dev/null)
        if [ "$now" = "$last" ]; then
            unchanged=$((unchanged + 5))
            if [ "$unchanged" -ge "$stall" ]; then
                reason="the heartbeat stopped advancing for ${stall}s"
                why=stall
                break
            fi
        else
            unchanged=0
            last=$now
        fi
        sleep 5
        waited=$((waited + 5))
    done
    [ -e "$state/disarm" ] && exit 0
    {
        echo "watchdog: $(date '+%H:%M:%S') acting because $reason."
        # Take ownership of the restore first. Give way only to a run that
        # holds it and is still making progress, i.e. one the cap caught
        # mid-restore. After a stall the run is hung or gone (an exited run
        # can linger as a zombie, which kill -0 still reports), so take over.
        if mkdir "$restore_lock" 2>/dev/null; then
            echo watchdog > "$restore_lock/owner"
        elif [ "$why" = cap ] && kill -0 "$main_pid" 2>/dev/null; then
            echo "watchdog: the run is restoring and still making progress; leaving it alone."
            exit 0
        else
            echo "watchdog: taking over the restore from a run that is gone or stalled."
        fi
        # The run may still be alive. Stop it before touching the card, so
        # it cannot start another step under the restore.
        if kill -0 "$main_pid" 2>/dev/null; then
            echo "watchdog: stopping the run (pid $main_pid) first."
            kill -9 "$main_pid" 2>/dev/null
        fi
        # A host probe still running would reset the card under the driver
        # mounted below. TERM lets it power the card down; then make sure.
        probe_pid=$(cat "$state/probe.pid" 2>/dev/null)
        if [ -n "$probe_pid" ] && kill -0 "$probe_pid" 2>/dev/null; then
            echo "watchdog: stopping the host probe (pid $probe_pid) first."
            kill "$probe_pid" 2>/dev/null
            stopped=0
            while kill -0 "$probe_pid" 2>/dev/null && [ "$stopped" -lt 5 ]; do
                sleep 1
                stopped=$((stopped + 1))
            done
            kill -9 "$probe_pid" 2>/dev/null
            rm -f "$state/probe.pid"
        fi
        # The station driver still holding the card would be reset under
        # it by the factory attach. Ask it to leave, destroy it, and do not
        # mount if it will not go; then take its filter rules out.
        # A dhcp.client of ours left running would keep asking on en6.
        if [ "$mode" = driver ] && [ "$dhcp" = yes ] && [ -e "$state/dhcp.started" ]; then
            sta_dhcp_stop | sed -e 's/^/watchdog: dhcp: /'
        fi
        # So would our wpa_supplicant, which also holds en6's control node.
        if [ "$mode" = driver ] && [ -e "$state/wpa.pid" ]; then
            sta_wpa_stop | sed -e 's/^/watchdog: wpa: /'
        fi
        if [ "$mode" = driver ] && [ -e "$state/sta.mounted" ] &&
                ifconfig "$sta_if" > /dev/null 2>&1; then
            echo "watchdog: destroying $sta_if first."
            bounded 30 "$state/watchdog-sta-disconnect.txt" \
                "$sta_ctl" -i "$sta_if" -w 15 disconnect
            sleep 2
            bounded 60 "$state/watchdog-sta-destroy.txt" ifconfig "$sta_if" destroy
            gone=0
            while ifconfig "$sta_if" > /dev/null 2>&1 && [ "$gone" -lt 30 ]; do
                sleep 1
                gone=$((gone + 1))
            done
            if ifconfig "$sta_if" > /dev/null 2>&1; then
                echo "watchdog: $sta_if is still present; not mounting. Restart the unit."
                exit 0
            fi
            rm -f "$state/sta.mounted"
        fi
        if [ "$mode" = driver ]; then
            if sta_pf disable; then
                echo "watchdog: the $sta_if filter rules are out."
            else
                echo "watchdog: the $sta_if filter rules could NOT be removed: $(cat "$state/pf.err")"
            fi
            if [ "$lan" = no ]; then :
            elif sta_nat disable; then
                echo "watchdog: the $sta_if NAT rule is out."
            else
                echo "watchdog: the $sta_if NAT rule could NOT be removed: $(cat "$state/nat.err")"
            fi
        fi
        # After a download, the factory attach must not find firmware still
        # running: it would skip its own download and drive that image.
        if [ -n "$fw_image" ]; then
            bounded 60 "$state/watchdog-check.txt" "$host_probe" -c
            check_rc=$?
            sed -e 's/^/watchdog-check: /' "$state/watchdog-check.txt"
            if [ "$check_rc" -ne 0 ]; then
                echo "watchdog: the firmware check did not pass (exit $check_rc); not mounting."
                echo "watchdog: a full power-off is the recovery; see the report."
                exit 0
            fi
        fi
        if ifconfig uap0 > /dev/null 2>&1; then
            echo "watchdog: uap0 is present; leaving it alone."
        elif mount -T io-pkt -o "$recover_params" "$driver"; then
            echo "watchdog: mounted with $recover_params"
            sleep 5
            ifconfig uap0 inet "$ap_address" netmask "$ap_netmask" up &&
                echo "watchdog: $ap_address configured."
        elif mount -T io-pkt -o "$factory_params" "$driver"; then
            echo "watchdog: fell back to $factory_params"
        else
            echo "watchdog: both mounts failed. Restart the unit."
        fi
    } >> "$state/watchdog.log" 2>&1
) &
watchdog_pid=$!
log "watchdog armed (pid $watchdog_pid): restores if pid $main_pid dies or stalls ${stall}s, capped at ${deadline}s."
disarm() {
    : > "$state/disarm"
    kill "$watchdog_pid" 2>/dev/null
    wait "$watchdog_pid" 2>/dev/null
}
trap 'log "interrupted; leaving the watchdog armed."; exit 1' 2 15

# --- detach ----------------------------------------------------------------
attach_window() {
    slog_driver > "$state/$1.slog"
    if grep -q 'selected DMA' "$state/$1.slog"; then
        log "$1: attach reached mv8688uap_pb_init."
    fi
    reset_seen=$(grep 'reset=' "$state/$1.slog" | sed -e 's/.*reset=/reset=/' |
        sed -e '$!d')
    [ -n "$reset_seen" ] && log "$1: reset register ended at $reset_seen."
    if grep -q 'uap_init_fw() failed' "$state/$1.slog"; then
        log "$1: firmware load FAILED -- the firmware was not running."
        return 1
    fi
    if grep -q 'Command Timeout' "$state/$1.slog"; then
        log "$1: the card did not answer SDIO at all (held in reset or unpowered)."
        return 2
    fi
    return 0
}

slog_mark
log "detaching uap0 with ifconfig destroy (the stack quiesces; expect a stall)."
if bounded 60 "$state/destroy.txt" ifconfig uap0 destroy; then
    log "destroy returned 0."
else
    fail "destroy did not return cleanly: $(cat "$state/destroy.txt")"
fi
if wait_gone 30; then
    log "uap0 is gone."
else
    fail "uap0 is still present after the destroy."
    slog_driver > "$state/destroy.slog"
    disarm
    log "verdict $verdict; the driver was left as it was."
    exit 1
fi
slog_driver > "$state/destroy.slog"
bounded 20 "$state/detached-iopkt.txt" pidin -p io-pkt-v4-hc mem ||
    degrade "pidin mem timed out after the detach."
if grep -q 'devnp-mv8688uap' "$state/detached-iopkt.txt" 2>/dev/null; then
    degrade "the driver object is still loaded; dev_detach did not dlclose it."
else
    log "the driver object was unloaded from io-pkt."
fi

# The networking service counts Wi-Fi failures and may re-mount the driver on
# its own. If it does, stop: its mount uses the factory parameters and fighting
# it would race two attaches onto one radio.
log "watching ${settle}s for an automatic re-mount by the networking service."
if wait_present "$settle"; then
    degrade "uap0 reappeared on its own; the networking service re-mounted the driver."
    slog_driver > "$state/remount.slog"
    disarm
    log "verdict $verdict; the radio is back on factory parameters."
    exit 0
fi
log "no automatic re-mount within ${settle}s."

# --- probe A: did the register 0x28 power-down clear the firmware? ----------
probe() {
    probe_name=$1
    probe_mask=$2
    slog_mark
    log "$probe_name: mount with resetmask=$probe_mask and no firmware available."
    if bounded 60 "$state/$probe_name-mount.txt" \
            mount -T io-pkt -o "$mount_base,fwpath=$nofw,resetmask=$probe_mask" "$driver"; then
        if wait_present 20; then
            attach_window "$probe_name"
            log "$probe_name: uap0 attached without firmware -- the firmware was still running."
            probe_result=firmware-survived
        else
            attach_window "$probe_name"
            log "$probe_name: mount returned 0 but uap0 did not appear."
            probe_result=no-interface
        fi
    else
        attach_window "$probe_name"
        log "$probe_name: mount failed: $(cat "$state/$probe_name-mount.txt")"
        probe_result=mount-failed
    fi
    if interface_present uap0; then
        slog_mark
        if bounded 60 "$state/$probe_name-destroy.txt" ifconfig uap0 destroy &&
                wait_gone 30; then
            slog_driver > "$state/$probe_name-destroy.slog"
            log "$probe_name: detached again."
        else
            slog_driver > "$state/$probe_name-destroy.slog"
            fail "$probe_name: could not detach the probe interface."
            return 1
        fi
    fi
    return 0
}

# --- host bring-up probe (-P) ------------------------------------------------
# A separate process: if it faults, it dies alone and io-pkt is untouched. It
# powers the card down again on every exit path it controls, including its own
# deadline (60 s, or 90 s with a download), so the factory mount below starts
# from a detached module. Both bounds, plus the 3 s TERM grace, stay under the
# watchdog's 120 s stall window: the heartbeat does not advance meanwhile.
run_host_probe() {
    if [ -d "$restore_lock" ]; then
        degrade "the watchdog has taken over; the host probe did not run."
        return
    fi
    if bounded 30 "$state/guard-detached.txt" "$host_probe" -n; then
        log "the probe guard passes with the driver detached."
    else
        sed -e 's/^/guard-detached: /' "$state/guard-detached.txt" >> "$report"
        degrade "the probe guard still refuses after the detach; the probe did not run."
        return
    fi
    slog_mark
    bounded_pidfile="$state/probe.pid"
    # A fixed flag, so the unquoted expansion below is one word or none.
    timing_flag=
    timing_text=
    if [ "$fw_timing" = yes ]; then
        timing_flag=-T
        timing_text=", timing"
    fi
    if [ -n "$station_ip" ]; then
        log "running the host bring-up probe with the download, commands, MAC, scan, a join of '$join_ssid' and data frames from $station_ip to $peer_ip$timing_text."
        bounded 110 "$state/host-probe.txt" "$host_probe" -H "$fw_helper" -F "$fw_image" -C \
            -M "$ap_mac" -L -A "$join_ssid" -I "$station_ip" -E "$peer_ip" $timing_flag
    elif [ -n "$join_ssid" ]; then
        log "running the host bring-up probe with the download, commands, MAC, scan and a join of '$join_ssid'$timing_text."
        bounded 110 "$state/host-probe.txt" "$host_probe" -H "$fw_helper" -F "$fw_image" -C \
            -M "$ap_mac" -L -A "$join_ssid" $timing_flag
    elif [ "$fw_scan" = yes ]; then
        log "running the host bring-up probe with the download, commands, MAC and scan$timing_text."
        bounded 110 "$state/host-probe.txt" "$host_probe" -H "$fw_helper" -F "$fw_image" -C \
            -M "$ap_mac" -L $timing_flag
    elif [ "$fw_commands" = yes ]; then
        log "running the host bring-up probe with the firmware download and commands$timing_text."
        bounded 110 "$state/host-probe.txt" "$host_probe" -H "$fw_helper" -F "$fw_image" -C \
            $timing_flag
    elif [ -n "$fw_image" ]; then
        log "running the host bring-up probe with the firmware download."
        bounded 110 "$state/host-probe.txt" "$host_probe" -H "$fw_helper" -F "$fw_image"
    else
        log "running the host bring-up probe."
        bounded 90 "$state/host-probe.txt" "$host_probe"
    fi
    host_rc=$?
    bounded_pidfile=
    sed -e 's/^/host-probe: /' "$state/host-probe.txt" >> "$report"
    sed -e 's/^/host-probe: /' "$state/host-probe.txt"
    slog_since > "$state/host-probe.slog"
    case $host_rc in
        0) if [ -n "$station_ip" ]; then
               log "RESULT: the station associated with '$join_ssid', exchanged ARP and ICMP echoes with $peer_ip and deauthenticated (exit 0); compare the tx:, echo: and bench: lines with the access point's capture."
           elif [ -n "$join_ssid" ]; then
               log "RESULT: the station associated with '$join_ssid', held the link and deauthenticated (exit 0); compare the join: lines with the access point's log."
           elif [ "$fw_timing" = yes ]; then
               log "RESULT: the PIO timing and read benchmark completed and the firmware still answered (exit 0); read the timing: and bench: lines."
           elif [ "$fw_scan" = yes ]; then
               log "RESULT: the station took the MAC and completed a passive scan (exit 0); read the scan: lines for the hotspot."
           elif [ "$fw_commands" = yes ]; then
               log "RESULT: the station firmware answered its commands and was cleared again (exit 0)."
           elif [ -n "$fw_image" ]; then
               log "RESULT: the firmware reached its ready magic and was cleared again (exit 0)."
           else
               log "RESULT: host bring-up reached function 1 (exit 0)."
           fi;;
        1) degrade "the host probe refused before writing anything (exit 1).";;
        2) degrade "the host probe failed after bring-up started (exit 2).";;
        4) withhold "the probe found the firmware still answering after its power-down, or could not read it (exit 4).";;
        *) degrade "the host probe hit its deadline or a signal (exit $host_rc).";;
    esac
    # A download that did not finish cleanly may have left firmware the
    # probe never checked for. Check again in a process of its own.
    case $host_rc in 0|1|4) return;; esac
    [ -n "$fw_image" ] || return
    bounded_pidfile="$state/probe.pid"
    bounded 60 "$state/firmware-check.txt" "$host_probe" -c
    check_rc=$?
    bounded_pidfile=
    sed -e 's/^/firmware-check: /' "$state/firmware-check.txt" >> "$report"
    if [ "$check_rc" -eq 0 ]; then
        log "the separate firmware check found no ready magic; the factory attach may run."
    else
        withhold "the separate firmware check did not pass (exit $check_rc)."
    fi
}
# --- station driver (-D) -------------------------------------------------------
# The first run of code of ours inside io-pkt. Every step is bounded and logs
# (the heartbeat), and the slices stay under the watchdog's 120 s stall.
# A disconnect and a pause go before the destroy, so no received frame is
# still with the stack when the shim unloads the driver.
sta_status() {
    bounded 10 "$state/sta-status-$1.txt" "$sta_ctl" -i "$sta_if" status
    sed -e "s/^/sta-status-$1: /" "$state/sta-status-$1.txt" >> "$report"
}
sta_session() {
    slog_mark
    log "mounting $sta_driver as $sta_if with lan=$sta_lan and the factory MAC (the stack stalls during the download)."
    : > "$state/sta.mounted"
    if bounded 60 "$state/sta-mount.txt" mount -T io-pkt -o \
            "lan=$sta_lan,mac=$ap_mac,helper=$fw_helper,firmware=$fw_image" "$sta_driver" &&
            wait_present 20 "$sta_if"; then
        log "$sta_if attached."
    else
        slog_since | grep 'devn-mv8688sta' > "$state/sta-attach.slog"
        sed -e 's/^/sta-attach: /' "$state/sta-attach.slog" >> "$report"
        interface_present "$sta_if" || rm -f "$state/sta.mounted"
        degrade "the station driver did not attach: $(cat "$state/sta-mount.txt")"
        return
    fi
    sta_ok=yes
    # The shim takes the interface flags from the driver's DL_ADVERT; without
    # BROADCAST the stack refuses every DHCP request (the first 5.3 run).
    bounded 10 "$state/sta-flags.txt" ifconfig "$sta_if"
    log "$sta_if $(sed -n -e 's/^[^ ]*: *//' -e '1p' "$state/sta-flags.txt")"
    if [ "$dhcp" = yes ] && ! sed -n '1p' "$state/sta-flags.txt" | grep -q 'BROADCAST'; then
        sta_ok=no
        degrade "$sta_if lacks BROADCAST, so dhcp.client cannot send; the lease is skipped."
    fi
    if [ "$dhcp" = yes ]; then
        bounded 10 "$state/sta-address.txt" ifconfig "$sta_if" up ||
            { sta_ok=no; degrade "$sta_if could not be brought up."; }
    elif ! bounded 10 "$state/sta-address.txt" \
            ifconfig "$sta_if" inet "$station_ip" netmask 255.255.255.0 up; then
        sta_ok=no
        degrade "$station_ip could not be set on $sta_if."
    fi
    if [ -n "$wpa_bin" ]; then
        sta_wpa_start
        connect_rc=$?
    else
        log "connecting to '$join_ssid'."
        bounded 60 "$state/sta-connect.txt" "$sta_ctl" -i "$sta_if" -w 45 connect "$join_ssid"
        connect_rc=$?
        sed -e 's/^/sta-connect: /' "$state/sta-connect.txt" >> "$report"
    fi
    if [ "$connect_rc" -ne 0 ]; then
        sta_ok=no
        degrade "the station did not associate with '$join_ssid' (exit $connect_rc)."
    else
        log "associated with '$join_ssid'."
    fi
    if [ "$connect_rc" -eq 0 ] && [ "$dhcp" = yes ] && [ "$sta_ok" = no ]; then
        log "no lease attempted: no pings and no hold."
    elif [ "$connect_rc" -eq 0 ] && [ "$dhcp" = yes ] && ! sta_dhcp_lease; then
        sta_ok=no
        log "no usable lease: no pings and no hold."
    elif [ "$connect_rc" -eq 0 ]; then
        for size in 56 1472; do
            if bounded 20 "$state/sta-ping-$size.txt" ping -c 3 -s "$size" "$peer_ip"; then
                log "ping $peer_ip with $size bytes answered."
            else
                sta_ok=no
                degrade "ping $peer_ip with $size bytes was not answered."
            fi
            sed -e "s/^/sta-ping-$size: /" "$state/sta-ping-$size.txt" >> "$report"
        done
        if [ "$lan" = yes ]; then
            log "hold: ${hold}s associated; run netcheck and use the online services now."
        else
            log "hold: ${hold}s associated; ping $station_ip from $peer_ip now."
        fi
        held=0
        while [ "$held" -lt "$hold" ]; do
            if [ -e "$state/stop" ]; then
                log "hold: stop requested after ${held}s; leaving now."
                break
            fi
            sleep 10
            held=$((held + 10))
            bounded 10 "$state/sta-hold.txt" "$sta_ctl" -i "$sta_if" status
            log "hold: ${held}s $(grep '^state=' "$state/sta-hold.txt" | sed -e 's/ requested.*//')"
        done
        sta_status held
        # A renewal of en5's lease, say, could take the default route back.
        if [ "$lan" = yes ]; then
            bounded 10 "$state/routes-held.txt" netstat -rn ||
                degrade "netstat timed out at the end of the hold."
            route_keys routes-held
            if [ "$(default_routes routes-held)" = "default $peer_ip $sta_if" ]; then
                log "hold: the lease's default route is still the only one."
            else
                sta_ok=no
                degrade "hold: the default route changed during the hold ($(default_routes routes-held | sed -e '1!d'))."
            fi
        fi
        if [ "$reconnects" = yes ]; then
            # -R: losses are the test; the supplicant must have rejoined by the end.
            # The unit's grep has no -o.
            log "hold: $(sed -n -e 's/.*\(connects=[0-9]* link_losses=[0-9]*\).*/\1/p' \
                "$state/sta-status-held.txt")."
            if grep -q '^state=associated' "$state/sta-status-held.txt"; then
                log "associated again at the end of the hold."
            else
                sta_ok=no
                degrade "not associated at the end of the hold; see sta-status-held."
            fi
        elif grep -q '^state=associated' "$state/sta-status-held.txt" &&
                grep -q ' link_losses=0 ' "$state/sta-status-held.txt"; then
            log "the link held for the whole of the hold."
        else
            sta_ok=no
            degrade "the link did not hold; see sta-status-held."
        fi
    fi
    [ "$dhcp" = no ] || sta_dhcp_end
    [ -z "$wpa_bin" ] || sta_wpa_end
    bounded 30 "$state/sta-disconnect.txt" "$sta_ctl" -i "$sta_if" -w 15 disconnect ||
        degrade "the disconnect request failed: $(cat "$state/sta-disconnect.txt")"
    sleep 2
    sta_status final
    log "destroying $sta_if."
    bounded 60 "$state/sta-destroy.txt" ifconfig "$sta_if" destroy ||
        degrade "the destroy did not return cleanly: $(cat "$state/sta-destroy.txt")"
    if wait_gone 30 "$sta_if"; then
        rm -f "$state/sta.mounted"
        log "$sta_if is gone."
    else
        stuck_driver=yes
        withhold "$sta_if is still present after the destroy: the station driver may hold the card."
    fi
    slog_since | grep 'devn-mv8688sta' > "$state/sta.slog"
    sed -e 's/^/sta-slog: /' "$state/sta.slog" >> "$report"
    [ "$sta_ok" = yes ] && [ "$stuck_driver" = no ] || return
    if [ "$lan" = yes ]; then
        lan_join=associated
        lan_stopped="its dhcp.client"
        if [ -n "$wpa_bin" ]; then
            lan_join="joined with WPA2-PSK through wpa_supplicant"
            lan_stopped="its dhcp.client and wpa_supplicant"
        fi
        log "RESULT: $sta_if $lan_join '$join_ssid', leased $station_ip from $peer_ip as the only default route with resolv.conf naming its servers (LAN mode), translated <natRangeTable>, answered ping at 56 and 1472 bytes, held ${hold}s, stopped $lan_stopped and was destroyed; the netcheck and HMI observations during the hold are the operator's."
    elif [ -n "$wpa_bin" ]; then
        log "RESULT: $sta_if joined '$join_ssid' with WPA2-PSK through wpa_supplicant, $([ "$dhcp" = yes ] && echo "leased $station_ip from $peer_ip, " || :)answered ping at 56 and 1472 bytes, held ${hold}s, stopped wpa_supplicant and was destroyed; check the access point's log for the handshakes and the peer's ping of $station_ip during the hold."
    elif [ "$dhcp" = yes ]; then
        log "RESULT: $sta_if associated with '$join_ssid', leased $station_ip from $peer_ip without a default-route, mam0 or resolver change, answered ping at 56 and 1472 bytes, held ${hold}s, stopped its dhcp.client and was destroyed; check the peer's DHCP log and its ping of $station_ip during the hold."
    else
        log "RESULT: $sta_if associated with '$join_ssid', answered ping to $peer_ip at 56 and 1472 bytes, held ${hold}s and was destroyed; check the peer's ping of $station_ip during the hold."
    fi
}
# Phase 6: wpa_supplicant joins instead of the control tool. It goes in the
# background with its debug log (-d: no key material without -K) in the
# state directory, and the join counts once it reports CTRL-EVENT-CONNECTED,
# that is after the 4-way and group handshakes, and the driver reports the
# association. Its log lines about the handshake go to the report.
sta_wpa_start() {
    log "starting wpa_supplicant on $sta_if for '$join_ssid'."
    rm -f "$state/wpa.log"      # -f appends
    "$wpa_bin" -i "$sta_if" -D mv8688 -c "$wpa_conf" -d -t -f "$state/wpa.log" \
        > "$state/wpa.out" 2>&1 &
    echo $! > "$state/wpa.pid"
    waited=0
    connected=no
    while [ "$waited" -lt "$wpa_wait" ]; do
        sleep 1
        waited=$((waited + 1))
        if grep -q 'CTRL-EVENT-CONNECTED' "$state/wpa.log" 2>/dev/null; then
            connected=yes
            break
        fi
        kill -0 "$(cat "$state/wpa.pid")" 2>/dev/null || break
        [ $((waited % 10)) -eq 0 ] && log "wpa: ${waited}s, $(grep -c 'State: ' "$state/wpa.log" 2>/dev/null) state changes so far"
    done
    sta_wpa_report start
    sta_status wpa
    # Connected is not enough: WPA2 with CCMP both ways, and a join that
    # carried the RSN element.
    if [ "$connected" = yes ] && grep -q '^state=associated' "$state/sta-status-wpa.txt" &&
            grep -q '^rsn=1 ' "$state/sta-status-wpa.txt" &&
            grep -q 'Key negotiation completed with .*PTK=CCMP GTK=CCMP' "$state/wpa.log"; then
        log "wpa_supplicant reported the connection after ${waited}s with CCMP; $sta_if is associated with RSN."
        return 0
    fi
    if [ "$connected" = yes ]; then
        log "wpa_supplicant connected, but not as WPA2-PSK with CCMP over an RSN join; see wpa-start and sta-status-wpa."
        return 1
    fi
    if kill -0 "$(cat "$state/wpa.pid")" 2>/dev/null; then
        log "wpa_supplicant did not connect in ${waited}s."
    else
        log "wpa_supplicant exited: $(cat "$state/wpa.out")"
    fi
    return 1
}
# What the supplicant logged about the join and the handshakes since the
# last report, at most 400 lines a stage.
sta_wpa_report() {
    wpa_from=$(cat "$state/wpa.reported" 2>/dev/null)
    wpa_from=${wpa_from:-0}
    wpa_lines=$(sed -n '$=' "$state/wpa.log" 2>/dev/null)
    wpa_lines=${wpa_lines:-0}
    echo "$wpa_lines" > "$state/wpa.reported"
    [ "$wpa_lines" -gt "$wpa_from" ] || return 0
    sed -n "$((wpa_from + 1)),${wpa_lines}p" "$state/wpa.log" |
        grep -E 'CTRL-EVENT|WPA: |EAPOL|State: |mv8688|l2_packet|Associated|Trying to|Michael|key' |
        grep -v 'hexdump' | sed -n '1,400p' | sed -e "s/^/wpa-$1: /" >> "$report"
}
sta_wpa_end() {
    sta_wpa_stop > "$state/wpa-stop.txt" 2>&1 || degrade "$(cat "$state/wpa-stop.txt")"
    log "$(cat "$state/wpa-stop.txt")"
    sta_wpa_report end
}
# 5.3: the factory dhcp.client on en6, after the join. It does not daemonize
# (its usage reads "dhcp.client [options...] &"; with or without -u it stayed
# in the foreground after configuring en6 in the second and third 5.3 runs),
# so it goes in the background here, as the factory scripts put it. -b asks
# the server to broadcast its replies: the stack would drop a unicast to an
# address en6 does not hold yet. -T 30 ends it (exit 3) if no server ACKs in
# time, and -d asks for its debug log.
# Each lease runs dhcp-up, which waits up to 6 s for its semaphore.
# Returns non-zero only when en6 has no usable address; route and resolver
# changes count against the run but leave the pings to run.
dhcp_lease_wait=40
sta_dhcp_procs() {
    bounded 20 "$state/dhcp-procs-$1.txt.all" pidin -f aenA
    grep -E 'dhcp|ifwatchd|waitfor|/route |route add|route delete' "$state/dhcp-procs-$1.txt.all" |
        grep -v 'pidin' > "$state/dhcp-procs-$1.txt"
    sed -e "s/^/dhcp-procs-$1: /" "$state/dhcp-procs-$1.txt" >> "$report"
    ls -l "$dhcp_semaphore" 2>&1 | sed -e "s/^/dhcp-semaphore-$1: /" >> "$report"
}
sta_dhcp_lease() {
    bounded 10 "$state/routes-before-dhcp.txt" netstat -rn ||
        degrade "netstat timed out before the lease."
    resolver_snapshot before-dhcp
    sta_dhcp_procs before
    # The session reads the driver's slog lines from its own mark later.
    sta_slog_seen=$slog_seen
    slog_mark
    : > "$state/dhcp.started"
    "$dhcp_client" -d -b -T 30 -i "$sta_if" < /dev/null > "$state/sta-dhcp.txt" 2>&1 &
    dhcp_pid=$!
    log "started $dhcp_client -d -b -T 30 -i $sta_if in the background (pid $dhcp_pid)."
    waited=0
    station_ip=
    while [ "$waited" -lt "$dhcp_lease_wait" ]; do
        sleep 1
        waited=$((waited + 1))
        bounded 10 "$state/sta-leased.txt" ifconfig "$sta_if"
        # While it negotiates, the client holds 0.0.0.0/8 on the interface.
        station_ip=$(sed -n 's/^[ 	]*inet \([0-9.]*\) .*/\1/p' "$state/sta-leased.txt" |
            grep -v '^0\.0\.0\.0$' | sed -e '1!d')
        [ -z "$station_ip" ] || break
        if ! kill -0 "$dhcp_pid" 2>/dev/null; then
            wait "$dhcp_pid"
            dhcp_rc=$?
            sed -e 's/^/sta-dhcp: /' "$state/sta-dhcp.txt" >> "$report"
            degrade "dhcp.client exited (status $dhcp_rc; 3 is its own timeout) before $sta_if had an address."
            return 1
        fi
        [ $((waited % 10)) -ne 0 ] || log "dhcp: no address on $sta_if after ${waited}s."
    done
    sed -e 's/^/sta-dhcp: /' "$state/sta-dhcp.txt" >> "$report"
    if [ -n "$station_ip" ]; then
        log "dhcp: $sta_if holds $station_ip after ${waited}s; waiting 10 s for dhcp-up."
        sleep 10
        bounded 10 "$state/sta-leased.txt" ifconfig "$sta_if"
        station_ip=$(sed -n 's/^[ 	]*inet \([0-9.]*\) .*/\1/p' "$state/sta-leased.txt" |
            grep -v '^0\.0\.0\.0$' | sed -e '1!d')
    fi
    sed -e 's/^/sta-leased: /' "$state/sta-leased.txt" >> "$report"
    sta_dhcp_procs leased
    slog_since | grep -i -E 'dhcp|DISCOVER|OFFER|REQUEST|ACK|lease' > "$state/dhcp.slog"
    sed -e 's/^/dhcp-slog: /' "$state/dhcp.slog" >> "$report"
    slog_seen=$sta_slog_seen
    case $station_ip in
        '') degrade "$sta_if holds no address ${dhcp_lease_wait}s after dhcp.client started."
            return 1;;
        "${peer_ip%.*}".*) log "leased $station_ip on $sta_if.";;
        *) degrade "the leased $station_ip is not on $peer_ip's /24."
           return 1;;
    esac
    bounded 10 "$state/routes-leased.txt" netstat -rn ||
        degrade "netstat timed out after the lease."
    if [ "$lan" = yes ]; then
        sta_lan_lease
    elif route_changes routes-before-dhcp routes-leased "the lease"; then
        log "routes: no default route came or went, and every mam0 route is still there."
    else
        sta_ok=no
        degrade "the lease changed a default route or removed a mam0 route."
    fi
    [ "$lan" = yes ] && return 0
    resolver_snapshot leased
    sed -e 's/^/resolver-leased: /' "$state/resolver-leased.txt" >> "$report"
    if same_resolver before-dhcp leased; then
        log "resolver: resolv.conf and _CS_RESOLVE are as before the lease."
    else
        sta_ok=no
        degrade "the lease changed resolv.conf or _CS_RESOLVE; compare resolver-baseline and resolver-leased."
    fi
    return 0
}
# LAN mode: the lease must be the only default route, resolv.conf must name
# its servers and _CS_RESOLVE be cleared, as dhcp-up does with the flag. A
# lease without DNS servers makes dhcp-up fall back to 134.60.1.111. Then the
# station gets en5's NAT rule.
sta_lan_lease() {
    route_changes routes-before-dhcp routes-leased "the lease"
    lan_default=$(default_routes routes-leased)
    if [ "$route_mam0_lost" = no ] && [ "$lan_default" = "default $peer_ip $sta_if" ]; then
        log "routes: the lease's default route via $peer_ip on $sta_if is the only one, and every mam0 route is still there."
    else
        sta_ok=no
        degrade "LAN mode: the default route is not the lease's alone (${lan_default:-none}), or a mam0 route went."
    fi
    resolver_snapshot leased
    sed -e 's/^/resolver-leased: /' "$state/resolver-leased.txt" >> "$report"
    lan_servers=$(sed -n 's/^nameserver[ 	]*//p' "$resolv_conf" 2>/dev/null)
    lan_own=$(echo "$lan_servers" | grep -v '^134\.60\.1\.111$' | grep .)
    if grep -q 'nameserver' "$state/getconf.txt"; then
        sta_ok=no
        degrade "LAN mode: _CS_RESOLVE still names a server after the lease."
    elif [ -z "$lan_own" ]; then
        sta_ok=no
        degrade "LAN mode: resolv.conf names no server of the lease's ($(echo ${lan_servers:-none}))."
    else
        log "resolver: resolv.conf names $(echo $lan_servers) and _CS_RESOLVE is cleared."
    fi
    lan_anchors leased
    sed -e 's/^/anchors-leased: /' "$state/anchors-leased.txt" >> "$report"
    if sta_nat enable; then
        log "pf: $sta_if translates <natRangeTable> as en5 does (RAM only)."
    else
        sta_ok=no
        degrade "NAT on $sta_if could not be added: $(cat "$state/nat.err")"
    fi
}
# Stops this run's dhcp.client and records what that undid, before the leave.
sta_dhcp_end() {
    [ -e "$state/dhcp.started" ] || return 0
    sta_dhcp_stop > "$state/dhcp-stop.txt" 2>&1
    dhcp_stop_rc=$?
    while IFS= read -r dhcp_line; do
        log "dhcp: $dhcp_line"
    done < "$state/dhcp-stop.txt"
    if [ "$dhcp_stop_rc" -eq 0 ]; then
        rm -f "$state/dhcp.started"
    else
        sta_ok=no
        degrade "the dhcp.client for $sta_if could not be stopped; a restart ends it."
    fi
    sleep 2
    bounded 10 "$state/sta-dhcp-stopped.txt" ifconfig "$sta_if"
    sed -e 's/^/sta-dhcp-stopped: /' "$state/sta-dhcp-stopped.txt" >> "$report"
    if grep -q '^[ 	]*inet ' "$state/sta-dhcp-stopped.txt"; then
        log "$sta_if still holds $(sed -n 's/^[ 	]*inet \([0-9.]*\) .*/\1/p' "$state/sta-dhcp-stopped.txt" | sed -e '1!d') after the stop."
    else
        log "$sta_if holds no address after the stop."
    fi
    [ -f "$state/routes-leased.txt" ] || return 0
    bounded 10 "$state/routes-dhcp-stopped.txt" netstat -rn ||
        degrade "netstat timed out after the stop."
    if route_changes routes-leased routes-dhcp-stopped "stopping dhcp.client"; then :
    elif [ "$lan" = yes ] && [ "$route_mam0_lost" = no ] && lan_defaults_ok routes-dhcp-stopped; then
        log "routes: stopping dhcp.client took only the lease's default route with it."
    else
        degrade "stopping dhcp.client changed a default route or removed a mam0 route."
    fi
    resolver_snapshot dhcp-stopped
    same_resolver before-dhcp dhcp-stopped ||
        log "resolver: differs from before the lease after the stop; see resolver-dhcp-stopped."
    sed -e 's/^/resolver-dhcp-stopped: /' "$state/resolver-dhcp-stopped.txt" >> "$report"
    resolver_undo dhcp-stopped
}
run_driver() {
    if [ -d "$restore_lock" ]; then
        degrade "the watchdog has taken over; the station driver was not mounted."
        return
    fi
    if bounded 30 "$state/guard-detached.txt" "$host_probe" -n; then
        log "the probe guard passes with the factory driver detached."
    else
        sed -e 's/^/guard-detached: /' "$state/guard-detached.txt" >> "$report"
        degrade "the probe guard still refuses after the detach; the station driver was not mounted."
        return
    fi
    if sta_pf enable; then
        if [ "$dhcp" = yes ]; then
            log "pf: $sta_if admits only DHCP replies and echo requests from $peer_ip (label $pf_label)."
        else
            log "pf: $sta_if admits only echo requests from $peer_ip to $station_ip (label $pf_label)."
        fi
    else
        degrade "pf rules for $sta_if could not be loaded, so it was not mounted: $(cat "$state/pf.err")"
        return
    fi
    sta_session
    if [ "$stuck_driver" = no ]; then
        # The driver powered the card down; the module must be idle and the
        # firmware gone before the factory attach, as after a probe.
        if bounded 30 "$state/guard-after.txt" "$host_probe" -n; then
            log "the module is idle after the destroy."
            bounded_pidfile="$state/probe.pid"
            bounded 60 "$state/firmware-check.txt" "$host_probe" -c
            check_rc=$?
            bounded_pidfile=
            sed -e 's/^/firmware-check: /' "$state/firmware-check.txt" >> "$report"
            if [ "$check_rc" -eq 0 ]; then
                log "the firmware check found no ready magic; the factory attach may run."
            else
                withhold "the firmware check after the destroy did not pass (exit $check_rc)."
            fi
        else
            sed -e 's/^/guard-after: /' "$state/guard-after.txt" >> "$report"
            withhold "the module is still in use after the destroy."
        fi
        if sta_pf disable; then
            log "pf: the $sta_if rules are out."
        else
            degrade "the $sta_if filter rules could not be removed: $(cat "$state/pf.err")"
        fi
        if [ "$lan" = no ]; then :
        elif sta_nat disable; then
            log "pf: the $sta_if NAT rule is out."
        else
            degrade "the $sta_if NAT rule could not be removed: $(cat "$state/nat.err")"
        fi
    else
        log "pf: the $sta_if rules stay while $sta_if exists."
    fi
}

# The factory attach skips its download when it finds the ready magic, so it
# must not run while firmware of ours may still be on the card.
withheld=no
withhold() {
    withheld=yes
    fail "$* The factory re-attach is withheld."
}

firmware_after_detach=unknown
if [ "$mode" = factory ]; then
    probe_b=no
    log "factory mode: no probes; going straight to the factory re-attach."
elif [ "$mode" = host ]; then
    probe_b=no
    run_host_probe
elif [ "$mode" = driver ]; then
    probe_b=no
    run_driver
elif probe probe-a 0; then
    case $probe_result in
        firmware-survived)
            firmware_after_detach=running
            log "RESULT: the detach and the register 0x28 power-down leave the firmware running.";;
        mount-failed|no-interface)
            firmware_after_detach=cleared
            log "RESULT: the detach alone clears the firmware; a re-attach has to download it."
            log "So the reset line is not needed to reload firmware.";;
    esac
else
    probe_b=no
fi

# --- probe B: does the WlanEnable reset line clear the firmware? ------------
if [ "$verdict" = FAIL ] && [ "$probe_b" = yes ]; then
    probe_b=no
    log "skipping the reset-line probe after a failure."
fi
if [ "$probe_b" = yes ]; then
    if probe probe-b 4; then
        # With the firmware already gone, this probe cannot attribute anything
        # to the reset line: it would fail either way. Say so rather than
        # claiming the line did it.
        if [ "$firmware_after_detach" = cleared ]; then
            log "probe-b: no firmware verdict is possible -- probe-a already found it cleared."
            log "Its value is the reset register trace above, not a firmware result."
        else
            case $probe_result in
                firmware-survived)
                    log "RESULT: resetmask=4 did not clear the firmware either."
                    log "Either the bit is not a radio reset or its polarity is inverted here.";;
                mount-failed)
                    if grep -q 'uap_init_fw() failed' "$state/probe-b.slog" 2>/dev/null; then
                        log "RESULT: resetmask=4 cleared the firmware while resetmask=0 did not."
                    else
                        degrade "probe-b failed before the firmware step; the card may not have answered."
                    fi;;
                no-interface)
                    degrade "probe-b left no interface and no firmware verdict.";;
            esac
        fi
    fi
fi

# --- restore ---------------------------------------------------------------
# After the probes, resetmask=4 goes first: it is the only mount that releases
# the reset bit a failed probe may have left asserted. In factory mode the
# whole point is to try the factory parameters first, with resetmask=4 kept
# only as the recovery.
if [ "$mode" = factory ] || [ "$mode" = host ] || [ "$mode" = driver ]; then
    first_params=$factory_params
    second_params=$recover_params
else
    first_params=$recover_params
    second_params=$factory_params
fi
# Each attempt gets its own slog window: a failed first mount must not make
# the attach that actually succeeded look like a firmware failure.
try_mount() {
    slog_mark
    bounded 60 "$state/$2.txt" mount -T io-pkt -o "$1" "$driver" && wait_present 30
}
restoring=yes
if [ "$withheld" = yes ] && [ "$stuck_driver" = yes ]; then
    restoring=no
    log "not mounting: the station driver may still hold the card."
    log "Recover with a restart; it starts io-pkt, and the factory attach, afresh."
elif [ "$withheld" = yes ]; then
    restoring=no
    log "not mounting: firmware may still be running on the card."
    log "Recover with a full power-off: ignition off until the MMI shuts down (about 25 min)."
elif ! mkdir "$restore_lock" 2>/dev/null; then
    restoring=no
    fail "the watchdog owns the restore; this run does not mount the driver."
elif echo "$$" > "$restore_lock/owner" && interface_present uap0; then
    slog_mark
    log "uap0 is already present; not mounting a second copy."
elif try_mount "$first_params" restore; then
    log "restored with $first_params."
    [ "$mode" = factory ] &&
        log "RESULT: the factory parameters re-attached the radio on their own."
elif log "the first restore mount did not attach; trying $second_params." &&
        try_mount "$second_params" restore-second; then
    if [ "$mode" = factory ]; then
        degrade "the factory parameters did NOT re-attach; recovered with $second_params."
        log "RESULT: a factory-parameter re-attach cannot stand alone here."
    elif [ "$mode" = host ] || [ "$mode" = driver ]; then
        degrade "the factory parameters did NOT re-attach after the $mode run; recovered with $second_params."
    else
        degrade "restored with the factory $second_params after the resetmask=4 mount failed."
    fi
else
    fail "could not restore the driver. Restart the unit; power off if that fails."
fi
[ "$restoring" = no ] ||
    attach_window restore || fail "the restore attach could not load firmware."
if [ "$restoring" = yes ] && interface_present uap0; then
    if ifconfig uap0 2>/dev/null | grep -q "inet $ap_address "; then
        log "uap0 already holds $ap_address."
    elif ifconfig uap0 inet "$ap_address" netmask "$ap_netmask" up; then
        log "uap0 address $ap_address configured."
    else
        degrade "uap0 is up but $ap_address could not be set."
    fi
fi
for node in /dev/sysregs/WlanEnable /dev/sysregs/BtReset; do
    [ -e "$node" ] || continue
    value=$(bounded 5 "$state/sysreg.txt" cat "$node" && cat "$state/sysreg.txt")
    log "${node##*/} now reads ${value:-<unreadable>}"
done
log "mam0 $(ifconfig mam0 2>/dev/null | grep 'inet ' || echo absent)"
# dhcp-up adds routes of its own and removes none it did not; a restart
# clears whatever stayed.
if [ "$mode" = driver ] && [ "$dhcp" = yes ]; then
    bounded 10 "$state/routes-after.txt" netstat -rn || degrade "netstat timed out after the run."
    if route_changes baseline-routes routes-after "the run"; then
        log "routes: no default route came or went over the run, and every mam0 route is still there."
    else
        degrade "the run changed a default route or removed a mam0 route; compare baseline-routes and routes-after."
    fi
    resolver_undo after-run
    if [ "$lan" = yes ]; then
        lan_anchors after-run
        sed -e 's/^/anchors-after-run: /' "$state/anchors-after-run.txt" >> "$report"
        log "LAN mode: the dnsRedirect anchors keep the lease's servers and $dlink_marker stays set; lan-mode.sh disable and a restart roll both back."
    fi
fi
if interface_present en5; then
    log "en5 still present."
else
    fail "en5 disappeared during the experiment."
fi
if [ "$withheld" = yes ]; then
    # Armed, it would mount once this run exits.
    disarm
    if [ "$stuck_driver" = yes ]; then
        log "watchdog disarmed: the factory driver must stay unmounted until a restart."
    else
        log "watchdog disarmed: the driver must stay unmounted until a power-off."
    fi
elif [ "$verdict" = FAIL ]; then
    log "leaving the watchdog armed: it still has time to restore the driver."
else
    disarm
    log "watchdog disarmed."
fi

# A manual mount attaches the radio but programs no SSID, channel or key: the
# networking service does that over driver ioctls after its own mount. Do not
# read a present uap0 as a working access point. Phase 3a established that the
# HMI Wi-Fi switch only stops and starts the BSS, so it will not reconfigure a
# driver that was re-attached by hand.
if [ "$stuck_driver" = yes ]; then
    log "uap0 stays down until a restart."
elif [ "$withheld" = yes ]; then
    # A restart's attach would find the same firmware and skip its download.
    log "uap0 stays down. A restart is not enough; power the unit off fully."
else
    log "uap0 carries no SSID or key after a manual mount. Restart the unit to get"
    log "a configured access point back; the HMI Wi-Fi switch will not do it."
fi
log "verdict $verdict"
[ "$verdict" = OK ]
