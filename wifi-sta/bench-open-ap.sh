#!/bin/bash
# Phase 5 bench access point. Runs on the Pi, never on the head unit: an open
# 2.4 GHz network on the Pi's Wi-Fi interface for mv8688-probe -A to join,
# with a DHCP server that offers an address but no router or DNS, and a
# capture of the data frames. Phase 6: with -w the network is WPA2-PSK with
# CCMP instead, under a random 256-bit PSK that start writes to
# /run/mmi-bench-ap/psk (mode 600) and never prints; -g and -p set the
# group and pairwise rekey intervals. hostapd's log is the access point's record of
# the station's authentication, association and deauthentication; the
# capture shows only data frames (ARP, DHCP, IP), not management frames.
#
# Nothing is installed and nothing persists: start keeps its state in
# /run/mmi-bench-ap, and stop ends the processes, moves the logs and the
# capture to an output directory, clears the interface address and hands the
# interface back to whichever manager start took it from: NetworkManager, or
# netplan's systemd-networkd and netplan-wpa-<interface> service, and a
# system-wide dhcpcd running alongside either. Taking the
# interface ends any client connection it had (such as the Pi's link to the
# car's access point); the head unit's management path for phase 5 is en5.
#
# usage: sudo bench-open-ap.sh start [-i wlan0] [-c 1] [-s mmi-bench]
#                                    [-w [-g seconds] [-p seconds]]
#        sudo bench-open-ap.sh stop [-o output-dir]
#        sudo bench-open-ap.sh status
set -u
umask 077
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

state=/run/mmi-bench-ap
iface=wlan0
channel=1
ssid=mmi-bench
outdir=
wpa2=
group_rekey=
ptk_rekey=
# Distinct from the unit's uap0 (192.168.1/24), mam0 (10.0.0/24), the en5
# LAN (192.168.16/20) and the AX88772 default (172.16/16).
ap_address=10.42.88.1/24
networkd_override=/run/systemd/network/05-mmi-bench-ap.network
dhcp_range=10.42.88.50,10.42.88.99,255.255.255.0,10m

usage() {
    echo "usage: ${0##*/} start [-i interface] [-c channel 1-11] [-s ssid]" \
        "[-w [-g seconds] [-p seconds]]" >&2
    echo "       ${0##*/} stop [-o output-dir]" >&2
    echo "       ${0##*/} status" >&2
    exit 2
}

[ $# -ge 1 ] || usage
action=$1
shift
case $action in start|stop|status) ;; *) usage;; esac
while getopts ':i:c:s:o:wg:p:' option; do
    case $option in
        i) iface=$OPTARG;;
        c) channel=$OPTARG;;
        s) ssid=$OPTARG;;
        o) outdir=$OPTARG;;
        w) wpa2=1;;
        g) group_rekey=$OPTARG; [ -n "$OPTARG" ] || usage;;
        p) ptk_rekey=$OPTARG; [ -n "$OPTARG" ] || usage;;
        *) usage;;
    esac
done
shift $((OPTIND - 1))
[ $# -eq 0 ] || usage
# The same characters radio-reattach.sh -A accepts.
case $ssid in ''|*[!A-Za-z0-9._-]*) usage;; esac
[ "${#ssid}" -le 32 ] || usage
case $channel in [1-9]|1[01]) ;; *) usage;; esac
case $iface in ''|*[!A-Za-z0-9_.-]*) usage;; esac
for rekey in "$group_rekey" "$ptk_rekey"; do
    [ -z "$rekey" ] && continue
    [ -n "$wpa2" ] || usage
    case $rekey in *[!0-9]*) usage;; esac
    [ "$rekey" -ge 30 ] && [ "$rekey" -le 86400 ] || usage
done

if [ "$(id -u)" -ne 0 ]; then
    echo "${0##*/}: run as root" >&2
    exit 1
fi

alive() {
    [ -s "$state/$1.pid" ] && kill -0 "$(cat "$state/$1.pid")" 2>/dev/null
}

# Ends one process started here: TERM, then KILL after 5 s.
end() {
    local pid waited=0
    [ -s "$state/$1.pid" ] || return 0
    pid=$(cat "$state/$1.pid")
    kill "$pid" 2>/dev/null || return 0
    while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 50 ]; do
        sleep 0.1
        waited=$((waited + 1))
    done
    kill -9 "$pid" 2>/dev/null
    return 0
}

stop() {
    local dest=${outdir:-/var/tmp/mmi-bench-ap-$(date -u +%Y%m%dT%H%M%SZ)}
    if [ ! -d "$state" ]; then
        echo "not running (no $state)"
        return 0
    fi
    end tcpdump
    end dnsmasq
    end hostapd
    stopped_iface=$(cat "$state/iface" 2>/dev/null || echo "$iface")
    ip addr flush dev "$stopped_iface" 2>/dev/null
    if [ -f "$state/nm-was-managed" ] && command -v nmcli > /dev/null; then
        nmcli device set "$stopped_iface" managed yes ||
            echo "WARNING: could not hand $stopped_iface back to NetworkManager" >&2
    fi
    if [ -f "$state/netplan-was-managed" ]; then
        rm -f "$networkd_override"
        networkctl reload && networkctl reconfigure "$stopped_iface" ||
            echo "WARNING: networkd did not take $stopped_iface back" >&2
        systemctl start "netplan-wpa-$stopped_iface.service" ||
            echo "WARNING: netplan-wpa-$stopped_iface.service did not restart" >&2
    fi
    # -n adds an interface that -x removed back to the running manager.
    if [ -f "$state/dhcpcd-was-running" ]; then
        dhcpcd -n "$stopped_iface" > /dev/null 2>&1 ||
            echo "WARNING: dhcpcd did not take $stopped_iface back" >&2
    fi
    mkdir -p "$dest" && cp -p "$state"/*.log "$state"/*.pcap "$state"/leases "$dest"/ \
        2>/dev/null
    # The configuration is kept without the bench PSK.
    [ -f "$state/hostapd.conf" ] &&
        sed 's/^wpa_psk=.*/wpa_psk=<not kept>/' "$state/hostapd.conf" > "$dest/hostapd.conf"
    # A start that failed before hostapd ran has no log to keep.
    if [ -s "$state/hostapd.log" ] && [ ! -s "$dest/hostapd.log" ]; then
        echo "WARNING: the logs did not reach $dest; they are still in $state" >&2
        return 1
    fi
    rm -rf "$state"
    echo "stopped; logs and capture in $dest"
}

start_failed() {
    echo "start FAILED: $*" >&2
    [ -f "$state/hostapd.log" ] && tail -n 20 "$state/hostapd.log" >&2
    outdir=${outdir:-/var/tmp/mmi-bench-ap-failed-$(date -u +%Y%m%dT%H%M%SZ)}
    stop > /dev/null
    exit 1
}

start() {
    local tool waited=0
    for tool in hostapd dnsmasq ip; do
        command -v "$tool" > /dev/null || { echo "missing: $tool" >&2; exit 1; }
    done
    if [ -e "$state" ]; then
        echo "already running or not cleaned up: $state exists; run stop first" >&2
        exit 1
    fi
    [ -d "/sys/class/net/$iface/wireless" ] || [ -d "/sys/class/net/$iface/phy80211" ] || {
        echo "$iface is not a wireless interface" >&2
        exit 1
    }
    # A client manager would fight hostapd for the interface. NetworkManager,
    # and netplan's networkd plus its per-interface wpa_supplicant, can be
    # told to let go and to take it back; anything else is left to the
    # operator, because how to restore it is not known here.
    manager=none
    if command -v nmcli > /dev/null &&
        nmcli -t -f DEVICE,STATE device 2>/dev/null | grep -q "^$iface:" &&
        ! nmcli -t -f DEVICE,STATE device | grep -q "^$iface:unmanaged"; then
        manager=networkmanager
    elif systemctl is-active --quiet "netplan-wpa-$iface.service" 2>/dev/null; then
        manager=netplan
    elif pgrep -f "wpa_supplicant.*$iface" > /dev/null; then
        echo "wpa_supplicant is running on $iface outside NetworkManager and netplan;" \
            "stop it first" >&2
        exit 1
    fi
    mkdir -m 700 "$state" || exit 1
    echo "$iface" > "$state/iface"
    # A system-wide dhcpcd manager keeps soliciting on the interface, and
    # once its lease expires adds an IPv4 link-local address and route
    # there. -x removes the interface from the manager without releasing
    # the lease (-k would send a release); with dhcpcd's "persistent" it
    # leaves the addresses, which the flush below removes.
    if [ -S /run/dhcpcd/sock ] && pgrep -f '^dhcpcd: \[manager\]' > /dev/null; then
        touch "$state/dhcpcd-was-running"
        dhcpcd -x "$iface" > /dev/null 2>&1 || start_failed "dhcpcd kept $iface"
    fi
    case $manager in
        networkmanager)
            touch "$state/nm-was-managed"
            nmcli device set "$iface" managed no || start_failed "NetworkManager kept $iface";;
        netplan)
            # Sorts before netplan's 10-netplan-*.network, so it matches first;
            # /run does not survive a reboot. World-readable, unlike everything
            # else here: systemd-networkd does not run as root.
            touch "$state/netplan-was-managed"
            { printf '[Match]\nName=%s\n\n[Link]\nUnmanaged=yes\n' "$iface" \
                > "$networkd_override" && chmod 644 "$networkd_override"; } ||
                start_failed "could not write $networkd_override"
            networkctl reload || start_failed "networkd did not reload"
            systemctl stop "netplan-wpa-$iface.service" ||
                start_failed "netplan-wpa-$iface.service did not stop";;
    esac
    command -v rfkill > /dev/null && rfkill unblock wlan
    ip addr flush dev "$iface"
    ip addr add "$ap_address" dev "$iface" || start_failed "could not address $iface"

    # Open, 802.11g, no WMM: the simplest network libertas' open-connect path
    # can join. The country code keeps channels 1-11 legal. With -w, WPA2
    # with CCMP only, no PMF, under a fresh random PSK.
    if [ -n "$wpa2" ]; then
        od -An -tx1 -N32 /dev/urandom | tr -d ' \n' > "$state/psk" &&
            [ "$(wc -c < "$state/psk")" -eq 64 ] || start_failed "no PSK from /dev/urandom"
        chmod 600 "$state/psk"
    fi
    cat > "$state/hostapd.conf" <<EOF
interface=$iface
driver=nl80211
ctrl_interface=$state/hostapd
ssid=$ssid
hw_mode=g
channel=$channel
country_code=US
ieee80211d=1
auth_algs=1
wmm_enabled=0
ignore_broadcast_ssid=0
logger_stdout=-1
logger_stdout_level=1
EOF
    if [ -z "$wpa2" ]; then
        echo "wpa=0" >> "$state/hostapd.conf"
    else
        {
            echo "wpa=2"
            echo "wpa_key_mgmt=WPA-PSK"
            echo "rsn_pairwise=CCMP"
            echo "ieee80211w=0"
            echo "wpa_psk=$(cat "$state/psk")"
            [ -n "$group_rekey" ] && echo "wpa_group_rekey=$group_rekey"
            [ -n "$ptk_rekey" ] && echo "wpa_ptk_rekey=$ptk_rekey"
        } >> "$state/hostapd.conf"
    fi
    hostapd -t "$state/hostapd.conf" > "$state/hostapd.log" 2>&1 &
    echo $! > "$state/hostapd.pid"
    until grep -q 'AP-ENABLED' "$state/hostapd.log"; do
        alive hostapd || start_failed "hostapd exited"
        [ "$waited" -lt 100 ] || start_failed "hostapd did not enable the AP in 10 s"
        sleep 0.1
        waited=$((waited + 1))
    done
    # Options 3 and 6 with no value: no router and no DNS server, so a
    # station's DHCP client gets an address without a default route. As
    # root, so the lease file and log can stay in the private state
    # directory; it serves DHCP only (--port=0).
    dnsmasq --conf-file=/dev/null --interface="$iface" --bind-interfaces \
        --except-interface=lo --port=0 --dhcp-range="$dhcp_range" \
        --dhcp-option=3 --dhcp-option=6 --dhcp-authoritative \
        --dhcp-leasefile="$state/leases" --log-dhcp --user=root \
        --log-facility="$state/dnsmasq.log" --pid-file="$state/dnsmasq.pid" ||
        start_failed "dnsmasq did not start"
    if command -v tcpdump > /dev/null; then
        tcpdump -i "$iface" -n -U -w "$state/capture.pcap" > "$state/tcpdump.log" 2>&1 &
        echo $! > "$state/tcpdump.pid"
    else
        echo "tcpdump is missing; there will be no capture" | tee "$state/tcpdump.log"
    fi
    if [ -n "$wpa2" ]; then
        echo "WPA2-PSK (CCMP) network '$ssid' on $iface channel $channel, address $ap_address;"
        echo "PSK in $state/psk (mode 600; not printed);" \
            "group rekey ${group_rekey:-default}, PTK rekey ${ptk_rekey:-off}."
    else
        echo "open network '$ssid' on $iface channel $channel, address $ap_address;"
    fi
    echo "DHCP $dhcp_range without router or DNS; state in $state."
    echo "Run '${0##*/} status' during the probe run and '${0##*/} stop' after it."
}

status() {
    local name
    if [ ! -d "$state" ]; then
        echo "not running"
        return 1
    fi
    if grep -q '^wpa=2' "$state/hostapd.conf" 2>/dev/null; then
        echo "security: WPA2-PSK (CCMP)"
    else
        echo "security: open"
    fi
    for name in hostapd dnsmasq tcpdump; do
        if alive "$name"; then echo "$name: running"; else echo "$name: NOT running"; fi
    done
    command -v hostapd_cli > /dev/null &&
        hostapd_cli -p "$state/hostapd" all_sta 2>/dev/null
    echo "--- station events (hostapd)"
    grep -E 'STA |AP-STA-|WPA: |EAPOL' "$state/hostapd.log" | tail -n 30
    echo "--- leases"
    cat "$state/leases" 2>/dev/null
}

case $action in
    start) start;;
    stop) stop;;
    status) status;;
esac
