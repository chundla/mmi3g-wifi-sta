"""Exercise the phase 7.3 mode switch with fake QNX utilities.

The script runs under dash on the workstation with its paths moved into a
temporary directory and stand-ins for ifconfig, mount, pfctl, pidin, netstat,
the probe, the control tool, wpa_supplicant, dhcp.client and sshd. Switches
that must change while the background keeper runs (the hotspot, the lease)
are files, not environment variables. These tests show the script's
refusals, ordering, keeper and rollback decisions. They do not show that
/bin/ksh on the unit behaves the same, nor what the radio, the phone or the
factory dhcp-up do.
"""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
DRIVER_SUM = '1022510863 93538'
DHCP_CLIENT_SUM = '263280565 57048'
DHCP_UP_SUM = '3046797907 6292'
KEY = '00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff'
CONF = ('ctrl_interface=\nnetwork={\n\tssid="hotspot"\n\tkey_mgmt=WPA-PSK\n'
        f'\tpairwise=CCMP\n\tpsk={KEY}\n}}\n')
PF_RULES = ('scrub in all no-df fragment reassemble\n'
            'pass in quick on uap0 inet proto tcp from 192.168.1.109 to 192.168.1.1 '
            'port = 2222 flags S/SA keep state label "mmi_openssh_pi"\n'
            'pass out all keep state\n'
            'block drop in on uap0 all\n')
NAT = ('nat on ppp0 from <natRangeTable> to any -> (ppp0) round-robin\n'
       'nat on en5 from <natRangeTable> to any -> (en5) round-robin\n'
       'rdr-anchor "dnsRedirect1" all\n')
EN6_NAT = 'nat on en6 from <natRangeTable> to any -> (en6) round-robin'
ROUTES = ('10.0.0/24          link#3             UC          0        0      -  mam0\n'
          '127.0.0.1          127.0.0.1          UH         17     1123  33192  lo0\n'
          '192.168.1/24       link#5             UC          2        0      -  uap0\n')
LEASE = '\tinet 172.20.10.13 netmask 0xfffffff0 broadcast 172.20.10.15\n'
LEASE_ROUTE = 'default            172.20.10.1        UGS         1        0      -  en6\n'
LEASE_RESOLV = 'nameserver 172.20.10.1\nnameserver 134.60.1.111\n'
SSH_RULE = ('pass in quick on en6 inet proto tcp from 172.20.10.0/28 to 172.20.10.13 '
            'port = 2222 flags S/SA keep state label "mmi_sta_ssh"')


class StaMode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = self.base = Path(self.tmp.name)
        self.bin = base / 'bin'
        self.root = base / 'files'
        self.state = base / 'state'
        for d in (self.bin, self.root):
            d.mkdir()
        self.calls = base / 'calls'
        self.ifaces = base / 'ifaces'
        self.ifaces.write_text('uap0\nmam0\nen5\n')
        (base / 'ifaces.uap0').write_text('\taddress: 00:1c:d7:00:00:01\n')
        self.routes = base / 'routes'
        self.routes.write_text(ROUTES)
        self.pf = base / 'pf.rules'
        self.pf.write_text(PF_RULES)
        self.nat = base / 'nat.rules'
        self.nat.write_text(NAT)
        self.anchors = base / 'anchors'
        self.anchors.mkdir()
        for n in range(1, 5):
            (self.anchors / f'dnsRedirect{n}').write_text('rdr on mam0 to 10.0.2.100 -> 10.0.2.1\n')
        self.resolv = base / 'resolv.conf'
        self.resolv.write_text('nameserver 172.20.10.1\n')
        self.cs = base / 'cs'
        self.cs.write_text('nameserver_10.0.2.100\n')
        self.flag = base / 'DLinkReplacesPPP'
        self.flag.write_text('')
        self.pids = base / 'pids'
        self.lease = base / 'lease'
        self.lease.write_text(LEASE)
        self.hotspot_off = base / 'hotspot-off'
        self.driver = base / 'devnp-mv8688uap.so'
        self.driver.write_bytes(b'factory driver')
        (base / 'dhcp-up').write_text('#!/bin/sh\n')
        self.template = base / 'sshd_config'
        self.template.write_text('Port 2222\nListenAddress 192.168.1.1\n'
                                 'PidFile /dev/shmem/mmi-openssh.pid\nAllowUsers root\n')
        # The OpenSSH start-up's RAM passwd, with its sshd account.
        self.passwd = base / 'passwd'
        self.passwd.write_text('root::0:0:Superuser:/mnt/persistence/openssh:/bin/ksh\n'
                               'sshd:*:15:15:OpenSSH privilege separation:/mnt/ssh/empty:/bin/ksh\n')
        for name in ('devn-mv8688sta.so', 'sd8688_helper.bin', 'sd8688.bin'):
            (self.root / name).write_bytes(name.encode())
        self.conf = self.root / 'wpa-hotspot.conf'
        self.conf.write_text(CONF)
        self.conf.chmod(0o600)

        text = (ROOT / 'wifi-sta/sta-mode.sh').read_text()
        for original, replacement in (
                ('shell=/bin/ksh', 'shell=/usr/bin/dash'),
                ('state=/fs/tmpfs/mmi-sta', f'state={self.state}'),
                ('driver=/lib/dll/devnp-mv8688uap.so', f'driver={self.driver}'),
                ('sshd=/mnt/ssh/sbin/sshd', f'sshd={self.bin}/sshd'),
                ('sshd_template=/mnt/ssh/etc/sshd_config', f'sshd_template={self.template}'),
                ('passwd_file=/etc/passwd', f'passwd_file={self.passwd}'),
                ('dhcp_client=/usr/sbin/dhcp.client', f'dhcp_client={self.bin}/dhcp.client'),
                ('dhcp_up=/etc/dhcp/dhcp-up', f'dhcp_up={base}/dhcp-up'),
                ('resolv_conf=/etc/resolv.conf', f'resolv_conf={self.resolv}'),
                ('dlink_marker=/HBpersistence/DLinkReplacesPPP', f'dlink_marker={self.flag}'),
                ('rse_marker=/mnt/efs-system/RSE', f'rse_marker={base}/RSE'),
                ('PATH=/bin:/sbin:/usr/bin:/usr/sbin', f'PATH={self.bin}:/bin:/usr/bin')):
            self.assertIn(original, text)
            text = text.replace(original, replacement)
        for line in text.splitlines():
            if '/fs/tmpfs' in line or '/mnt/ssh' in line:
                self.assertTrue(line.lstrip().startswith('#'), line)
        self.script = base / 'sta-mode.sh'
        self.script.write_text(text)
        self.addCleanup(self.kill_everything)

        self.env = dict(
            os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}',
            SSH_CONNECTION='192.168.16.1 50000 192.168.16.11 2222',
            M=str(base), MOCK_CALLS=str(self.calls), MOCK_IFACES=str(self.ifaces),
            MOCK_ROUTES=str(self.routes), MOCK_PF=str(self.pf), MOCK_NAT=str(self.nat),
            MOCK_ANCHORS=str(self.anchors), MOCK_RESOLV=str(self.resolv),
            MOCK_CS=str(self.cs), MOCK_PIDS=str(self.pids), MOCK_CONF=str(self.conf),
            MOCK_LS_MODE='-rw-------', MOCK_LS_OWNER='root', MOCK_NO_EGD='',
            MOCK_CHECK_RC='0', MOCK_DHCP_UP_SUM=DHCP_UP_SUM)
        self.tool('uname', '#!/bin/sh\necho QNX\n')
        self.tool('cksum', '#!/bin/sh\ncase $1 in\n'
                           f'    {self.driver}) echo "{DRIVER_SUM} $1";;\n'
                           f'    *dhcp.client) echo "{DHCP_CLIENT_SUM} $1";;\n'
                           '    *dhcp-up) echo "$MOCK_DHCP_UP_SUM $1";;\n'
                           '    *) exec /usr/bin/cksum "$@";;\nesac\n')
        # The keeper's 10 s tick stays longer than the 1 s waits.
        self.tool('sleep', '#!/bin/sh\ncase $1 in 10) exec /bin/sleep 0.05;; '
                           '*) exec /bin/sleep 0.01;; esac\n')
        self.tool('df', '#!/bin/sh\necho "tmpfs 1M 0 1M 0% /fs/tmpfs"\n')
        self.tool('ls', '#!/bin/sh\n'
                        'if [ "$1" = -l ] && [ "$2" = "$MOCK_CONF" ]; then\n'
                        '    echo "$MOCK_LS_MODE  1 $MOCK_LS_OWNER  root  123 Jan 01 00:00 $2"; exit 0\n'
                        'fi\nexec /bin/ls "$@"\n')
        self.tool('netstat', '#!/bin/sh\necho "Routing tables"\necho\necho "Internet:"\n'
                             'echo "Destination        Gateway            Flags    Refs      Use    Mtu  Interface"\n'
                             'cat "$MOCK_ROUTES"\n')
        self.tool('getconf', '#!/bin/sh\ncat "$MOCK_CS"\n')
        self.tool('pidin', '''#!/bin/sh
echo "pidin $*" >> "$MOCK_CALLS"
[ "$1" = -f ] || exit 0
echo "     pid Arguments"
echo "  417863 dhcp.client -b -i en5"
[ -z "$MOCK_NO_EGD" ] && echo "  708 /mnt/persistence/openssh/jitter-egd 708"
[ -f "$MOCK_PIDS" ] || exit 0
while read -r pid args; do
    kill -0 "$pid" 2>/dev/null && echo " $pid $args"
done < "$MOCK_PIDS"
exit 0
''')
        self.tool('ifconfig', '''#!/bin/sh
echo "ifconfig $*" >> "$MOCK_CALLS"
if [ "$1" = -a ]; then cat "$MOCK_IFACES"; exit 0; fi
grep -qx "$1" "$MOCK_IFACES" || exit 1
case $2 in
    destroy) [ "$1" = en6 ] && [ -e "$M/en6-stuck" ] && exit 1
             grep -vx "$1" "$MOCK_IFACES" > "$MOCK_IFACES.new"
             mv "$MOCK_IFACES.new" "$MOCK_IFACES"
             if [ "$1" = en6 ]; then
                 rm -f "$MOCK_IFACES.en6"
                 grep -v ' en6$' "$MOCK_ROUTES" > "$MOCK_ROUTES.new"
                 mv "$MOCK_ROUTES.new" "$MOCK_ROUTES"
             fi;;
    inet|up) ;;
    '') echo "$1: flags=80000843<UP,BROADCAST,RUNNING,SIMPLEX,SHIM> mtu 1500"
        [ -f "$MOCK_IFACES.$1" ] && cat "$MOCK_IFACES.$1";;
esac
exit 0
''')
        self.tool('mount', '''#!/bin/sh
echo "mount $*" >> "$MOCK_CALLS"
case $4 in
    lan=*) [ -e "$M/sta-no-attach" ] && exit 0
           echo en6 >> "$MOCK_IFACES"; exit 0;;
esac
[ -e "$M/factory-fails" ] && exit 1
echo uap0 >> "$MOCK_IFACES"
''')
        self.tool('pfctl', '''#!/bin/sh
echo "pfctl $*" >> "$MOCK_CALLS"
case "$1 $2" in
    "-sr ") cat "$MOCK_PF";;
    "-n -R") [ -f "$4" ];;
    "-R -f") [ -e "$M/pf-fails" ] && exit 1
             cp "$3" "$MOCK_PF";;
    "-sn ") cat "$MOCK_NAT";;
    "-n -N") [ -f "$4" ];;
    "-N -f") cp "$3" "$MOCK_NAT";;
    "-a "*) cat "$MOCK_ANCHORS/$2" 2>/dev/null; exit 0;;
    "-t natRangeTable") echo "   10.0.0.101"; echo "   192.168.1.0/24";;
    *) exit 2;;
esac
''')
        # Guard: a driver holds the module while uap0 or en6 exists.
        self.tool('mv8688-probe', '''#!/bin/sh
echo "probe $*" >> "$MOCK_CALLS"
if [ "$1" = -n ]; then
    if grep -qE "uap0|en6" "$MOCK_IFACES"; then
        echo "verdict=REFUSED the module is in use (a driver is attached)"; exit 1
    fi
    echo "verdict=GUARD-PASS module idle; nothing written"; exit 0
fi
[ "$1" = -c ] && exit "$MOCK_CHECK_RC"
exit 2
''', where=self.root)
        self.tool('mv8688-ctl', '''#!/bin/sh
for arg; do case $arg in status|disconnect) verb=$arg;; esac; done
echo "ctl $verb" >> "$MOCK_CALLS"
if [ "$verb" = status ] && [ ! -e "$M/hotspot-off" ]; then state=associated; else state=idle; fi
echo "state=$state requested=1 done=1 last_rc=0 last_step=-"
echo "fw_release=0x19260a26 connects=1 link_losses=0 link_cause=0x00000000"
echo "rsn=1 scans_done=1 results=1 key_sets=2 mic_ucast=0 mic_mcast=0"
''', where=self.root)
        # Connects once the hotspot is on, and runs until TERM.
        self.tool('wpa_supplicant', '''#!/bin/sh
echo "wpa $*" >> "$MOCK_CALLS"
echo "$$ $0 $*" >> "$MOCK_PIDS"
log=
while [ $# -gt 0 ]; do [ "$1" = -f ] && log=$2; shift; done
[ -e "$M/wpa-exits" ] && { echo "Failed to initialize driver interface"; exit 255; }
# wpa-kills-stop holds the state directory: the TERM also ends the stop
# worker, as the 7.3 run's first stop ended after the supplicant's TERM.
trap 'echo "wpa TERM" >> "$MOCK_CALLS"
[ -e "$M/wpa-kills-stop" ] && kill -TERM "$(cat "$(cat "$M/wpa-kills-stop")/stop.pid")"
exit 0' TERM
done=
while :; do
    if [ -z "$done" ] && [ ! -e "$M/hotspot-off" ]; then
        echo "1.5: WPA: Key negotiation completed with 02:00:00:aa:bb:cc [PTK=CCMP GTK=CCMP]" >> "$log"
        echo "1.5: CTRL-EVENT-CONNECTED - Connection to 02:00:00:aa:bb:cc completed" >> "$log"
        done=1
    fi
    /bin/sleep 0.02
done
''', where=self.root)
        # Leases what $M/lease says, does what dhcp-up's LAN branch does, and
        # stays in the foreground; TERM takes the address and the route away.
        self.tool('dhcp.client', '''#!/bin/sh
echo "dhcp $*" >> "$MOCK_CALLS"
echo "$$ $0 $*" >> "$MOCK_PIDS"
printf '\\tinet 0.0.0.0 netmask 0xff000000 broadcast 255.255.255.255\\n' > "$MOCK_IFACES.en6"
(/bin/sleep 0.05
 cp "$M/lease" "$MOCK_IFACES.en6"
 echo "default            172.20.10.1        UGS         1        0      -  en6" >> "$MOCK_ROUTES"
 printf "nameserver 172.20.10.1\\nnameserver 134.60.1.111\\n" > "$MOCK_RESOLV"
 : > "$MOCK_CS") &
on_term='grep -v "^default .* en6$" "$MOCK_ROUTES" > "$MOCK_ROUTES.n"; mv "$MOCK_ROUTES.n" "$MOCK_ROUTES"; rm -f "$MOCK_IFACES.en6"; exit 0'
exec /bin/sh -c "trap '$on_term' TERM; while :; do /bin/sleep 0.02; done"
''')
        self.tool('sshd', '''#!/bin/sh
echo "sshd $*" >> "$MOCK_CALLS"
[ "$1" = -t ] && { [ -e "$M/sshd-bad" ] && exit 1; exit 0; }
echo "$$ $0 $*" >> "$MOCK_PIDS"
trap 'exit 0' TERM
while :; do /bin/sleep 0.02; done
''')

    def tool(self, name, text, where=None):
        path = (where or self.bin) / name
        path.write_text(text)
        path.chmod(0o755)

    def kill_everything(self):
        pids = set()
        for f in list(self.state.glob('*.pid')) + list(self.state.glob('lock/pid')):
            try:
                pids.add(int(f.read_text().split()[0]))
            except (ValueError, IndexError, OSError):
                pass
        if self.pids.exists():
            pids.update(int(line.split()[0]) for line in self.pids.read_text().splitlines())
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    def run_script(self, *args, **overrides):
        env = dict(self.env, **overrides)
        return subprocess.run(['dash', str(self.script), *args], env=env,
                              capture_output=True, text=True, timeout=60)

    def start(self, *args, **overrides):
        return self.run_script('start', '-r', str(self.root), *args, **overrides)

    def recorded(self):
        return self.calls.read_text() if self.calls.exists() else ''

    def log(self):
        path = self.state / 'log'
        return path.read_text() if path.exists() else ''

    def alive(self, name):
        try:
            os.kill(int((self.state / name).read_text()), 0)
            return True
        except (OSError, ValueError):
            return False

    def wait_for(self, predicate, what, limit=10):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(0.05)
        self.fail(f'timed out waiting for {what}:\n{self.log()}')

    def daemons_alive(self):
        alive = []
        for line in (self.pids.read_text().splitlines() if self.pids.exists() else []):
            try:
                os.kill(int(line.split()[0]), 0)
                alive.append(line)
            except ProcessLookupError:
                pass
        return alive

    # --- start ------------------------------------------------------------------
    def test_start_brings_the_station_up_in_order_and_reports_ok(self):
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr + self.log())
        self.assertIn('verdict OK', result.stdout)
        calls = [c for c in self.recorded().splitlines()
                 if c.startswith(('probe', 'mount', 'wpa -', 'dhcp', 'sshd', 'pfctl -R',
                                  'pfctl -N', 'ifconfig uap0 destroy', 'ifconfig en6 up'))]
        want = ['probe -n', 'pfctl -R -f', 'ifconfig uap0 destroy', 'probe -n',
                'mount -T io-pkt -o lan=6,mac=00:1c:d7:00:00:01,helper=',
                'ifconfig en6 up', 'wpa -i en6 -D mv8688 -c %s -t -f' % self.conf,
                'dhcp -b -i en6', 'pfctl -R -f', 'sshd -t -f', 'sshd -D -e -f', 'pfctl -N -f']
        self.assertEqual(len(calls), len(want), calls)
        for got, expected in zip(calls, want):
            self.assertTrue(got.startswith(expected), (got, expected))
        # The SSH rule precedes en6's block; the factory rules are kept.
        rules = self.pf.read_text().splitlines()
        self.assertEqual(rules[2], SSH_RULE)
        self.assertEqual(rules[3:5], [
            'pass in quick on en6 inet proto udp from any port 67 to any port 68 keep state label "mmi_sta_en6"',
            'block drop in quick on en6 all label "mmi_sta_en6"'])
        self.assertEqual(rules[5], 'pass out all keep state')
        self.assertIn(EN6_NAT, self.nat.read_text().splitlines()[2])
        config = (self.state / 'sshd_config').read_text()
        self.assertIn('ListenAddress 172.20.10.13\n', config)
        self.assertIn(f'PidFile {self.state}/sshd.pidfile\n', config)
        log = self.log()
        for text in ('resolv.conf before the start:', 'pf: en6 admits only DHCP replies',
                     'uap0 is gone.', 'lease: 172.20.10.13 28',
                     'pf: SSH to 172.20.10.13:2222 from its /28 admitted',
                     'sshd: listening on 172.20.10.13:2222',
                     'en6 translates <natRangeTable>', 'RESULT: en6 joined the hotspot',
                     'resolv.conf naming 172.20.10.1 134.60.1.111', 'verdict OK'):
            self.assertIn(text, log)
        for text in (log, result.stdout, result.stderr):
            self.assertNotIn(KEY[:20], text)
            self.assertNotIn('hotspot"', text)
        self.assertTrue(self.alive('keeper.pid'))
        self.assertEqual((self.state / 'phase').read_text(), 'keeping\n')

        status = self.run_script('status')
        self.assertEqual(status.returncode, 0, status.stdout)
        self.assertIn('lease: 172.20.10.13/28', status.stdout)
        self.assertIn('sshd: pid', status.stdout)
        self.assertIn('status OK', status.stdout)

    def test_stop_takes_everything_down_and_remounts_the_factory_driver(self):
        self.assertEqual(self.start().returncode, 0, self.log())
        result = self.run_script('stop')
        self.assertEqual(result.returncode, 0, result.stdout + self.log())
        self.assertIn('verdict OK', result.stdout)
        calls = self.recorded().splitlines()
        order = [calls.index(c) for c in ('wpa TERM', 'ctl disconnect', 'ifconfig en6 destroy')]
        self.assertEqual(order, sorted(order))
        mounts = [c for c in calls if c.startswith('mount') and 'irq=13' in c]
        self.assertEqual(len(mounts), 1)
        self.assertIn('resetmask=0', mounts[0])
        self.assertLess(calls.index('probe -c'), calls.index(mounts[0]))
        self.assertIn('ifconfig uap0 inet 192.168.1.1 netmask 255.255.255.0 up', calls)
        self.assertEqual(self.pf.read_text(), PF_RULES)
        self.assertEqual(self.nat.read_text(), NAT)
        self.assertEqual(self.resolv.read_text(), 'nameserver 172.20.10.1\n')
        self.assertEqual((self.state / 'resolv.conf.run.1').read_text(), LEASE_RESOLV)
        self.assertNotIn('default', self.routes.read_text())
        self.assertFalse((self.state / 'lock').exists())
        self.assertEqual(self.daemons_alive(), [])
        self.assertFalse(self.alive('keeper.pid'))
        self.assertIn('restart the unit for the access point', self.log())
        status = self.run_script('status')
        self.assertEqual(status.returncode, 0)
        self.assertIn('not started; uap0 is present', status.stdout)
        self.assertIn('last stop: OK', status.stdout)
        # And it can start again without a restart.
        self.assertEqual(self.start().returncode, 0, self.log())

    def test_a_stop_that_is_killed_says_so_and_a_second_stop_finishes(self):
        self.assertEqual(self.start().returncode, 0, self.log())
        (self.base / 'wpa-kills-stop').write_text(str(self.state))
        first = self.run_script('stop')
        self.assertEqual(first.returncode, 2, first.stdout + self.log())
        self.assertIn('stopping wpa_supplicant (pid', self.log())
        self.assertIn('stop: signal 15 received', self.log())
        self.assertIn('ended without a verdict', first.stdout)
        self.assertTrue((self.state / 'lock').is_dir())
        (self.base / 'wpa-kills-stop').unlink()
        second = self.run_script('stop')
        self.assertEqual(second.returncode, 0, second.stdout + self.log())
        self.assertIn('wpa_supplicant was not running', self.log())
        self.assertIn('stop: verdict OK', self.log())
        self.assertFalse((self.state / 'lock').exists())
        self.assertNotIn('mmi_sta', self.pf.read_text())

    def test_a_resolv_conf_absent_before_the_start_is_removed_by_the_stop(self):
        self.resolv.unlink()
        self.assertEqual(self.start().returncode, 0, self.log())
        self.assertEqual(self.run_script('stop').returncode, 0, self.log())
        self.assertFalse(self.resolv.exists())
        self.assertIn('absent before the start', self.log())

    def test_a_second_start_only_reports_the_status(self):
        self.assertEqual(self.start().returncode, 0, self.log())
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('already started (phase keeping)', result.stdout)
        self.assertIn('status OK', result.stdout)
        self.assertEqual(self.recorded().count('ifconfig uap0 destroy'), 1)
        self.assertEqual(self.recorded().count('mount -T io-pkt -o lan=6'), 1)

    def test_dry_run_changes_nothing(self):
        result = self.start('-n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('dry run: nothing was changed', result.stdout)
        self.assertNotIn('destroy', self.recorded())
        self.assertNotIn('pfctl -R', self.recorded())
        self.assertNotIn('mount', self.recorded())
        self.assertFalse((self.state / 'lock').exists())

    def test_a_session_on_the_access_point_is_warned_not_refused(self):
        result = self.start('-n', SSH_CONNECTION='192.168.1.100 50000 192.168.1.1 2222')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('it will drop at the detach', result.stdout)

    # --- refusals -----------------------------------------------------------------
    def assert_refused(self, result, text):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(text, result.stderr)
        for call in ('destroy', 'pfctl -R', 'pfctl -N', 'mount'):
            self.assertNotIn(call, self.recorded())
        self.assertFalse((self.state / 'lock').exists())

    def test_refuses_without_the_empty_lan_mode_flag(self):
        self.flag.unlink()
        self.assert_refused(self.start(), 'needs the empty flag')
        self.flag.write_text('x')
        self.assert_refused(self.start(), 'needs the empty flag')

    def test_refuses_an_existing_default_route(self):
        with self.routes.open('a') as f:
            f.write('default            192.168.16.1       UGS         0        0      -  en5\n')
        self.assert_refused(self.start(), 'route delete default')

    def test_refuses_a_uap0_without_its_factory_mac(self):
        (self.base / 'ifaces.uap0').write_text('\taddress: 00:00:00:00:00:00\n')
        self.assert_refused(self.start(), 'no usable factory MAC')

    def test_refuses_without_jitter_egd(self):
        self.assert_refused(self.start(MOCK_NO_EGD='1'),
                            'jitter-egd is not running and ' + str(self.root / 'jitter-egd'))

    def bundled_egd(self, ready=True):
        self.tool('jitter-egd', '''#!/bin/sh
echo "egd $*" >> "$MOCK_CALLS"
echo "$$ $0 $*" >> "$MOCK_PIDS"
''' + ('echo "JITTER_EGD_READY port=$1"\n' if ready else 'echo "jent_entropy_init failed"; exit 1\n') +
                  '''trap 'exit 0' TERM
while :; do /bin/sleep 0.02; done
''', where=self.root)

    def test_without_the_openssh_jitter_egd_the_bundled_one_is_started_first(self):
        self.bundled_egd()
        result = self.start(MOCK_NO_EGD='1')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr + self.log())
        self.assertIn(f'jitter-egd starts from {self.root}/jitter-egd', result.stdout)
        calls = [c for c in self.recorded().splitlines()
                 if c.startswith(('egd', 'pfctl -R', 'ifconfig uap0 destroy', 'wpa -'))]
        self.assertEqual([' '.join(c.split()[:3]) for c in calls][:4],
                         ['egd 708', 'pfctl -R -f', 'ifconfig uap0 destroy', 'wpa -i en6'])
        self.assertIn('jitter-egd: started from', self.log())
        status = self.run_script('status', MOCK_NO_EGD='1')
        self.assertIn('status OK', status.stdout)
        # A stop leaves it for the next start.
        self.assertEqual(self.run_script('stop').returncode, 0, self.log())
        self.assertTrue(self.alive('egd.pid'))

    def test_a_bundled_jitter_egd_that_fails_keeps_uap0(self):
        self.bundled_egd(ready=False)
        result = self.start(MOCK_NO_EGD='1')
        self.assertEqual(result.returncode, 2, self.log())
        self.assertIn('jent_entropy_init failed', self.log())
        self.assertIn('nothing was changed', self.log())
        self.assertNotIn('destroy', self.recorded())
        self.assertNotIn('pfctl -R', self.recorded())
        self.assertFalse((self.state / 'lock').exists())

    def test_without_the_openssh_installation_running_there_is_no_ssh(self):
        self.passwd.write_text('root::0:0:Superuser:/:/bin/ksh\n')
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr + self.log())
        self.assertIn('the station will have no SSH', result.stdout)
        self.assertIn('with no SSH (the OpenSSH installation is not running)', self.log())
        self.assertNotIn('sshd', self.recorded())
        self.assertNotIn('mmi_sta_ssh', self.pf.read_text())
        status = self.run_script('status')
        self.assertIn('sshd: none', status.stdout)
        self.assertIn('status OK', status.stdout)

    def test_refuses_a_configuration_that_is_not_roots_alone_or_names_two_networks(self):
        self.assert_refused(self.start(MOCK_LS_MODE='-rw-r--r--'), 'mode 600')
        self.assert_refused(self.start(MOCK_LS_OWNER='mmi'), 'mode 600')
        self.conf.write_text(CONF + 'network={\n\tssid="other"\n}\n')
        self.assert_refused(self.start(), 'exactly one network')

    def test_refuses_unreviewed_factory_files_or_an_existing_en6(self):
        self.assert_refused(self.start(MOCK_DHCP_UP_SUM='1 2'), 'not the reviewed factory script')
        with self.ifaces.open('a') as f:
            f.write('en6\n')
        self.assert_refused(self.start(), 'en6 already exists')

    def test_refuses_leftover_rules(self):
        self.pf.write_text(PF_RULES + 'block drop in quick on en6 all label "mmi_sta_en6"\n')
        self.assert_refused(self.start(), 'run sta-mode.sh stop first')

    def test_refuses_a_missing_station_file(self):
        (self.root / 'sd8688.bin').unlink()
        self.assert_refused(self.start(), 'sd8688.bin is missing')

    def test_rules_that_will_not_load_keep_uap0(self):
        (self.base / 'pf-fails').touch()
        result = self.start()
        self.assertEqual(result.returncode, 2, self.log())
        self.assertNotIn('destroy', self.recorded())
        self.assertIn('uap0', self.ifaces.read_text())
        self.assertFalse((self.state / 'lock').exists())

    # --- failures after the detach ------------------------------------------------
    def test_a_driver_that_does_not_attach_fails_and_stop_brings_the_factory_driver_back(self):
        (self.base / 'sta-no-attach').touch()
        result = self.start()
        self.assertEqual(result.returncode, 2, self.log())
        self.assertIn('no access point and no station', self.log())
        self.assertEqual((self.state / 'phase').read_text(), 'failed\n')
        status = self.run_script('status')
        self.assertEqual(status.returncode, 2, status.stdout)
        stop = self.run_script('stop')
        self.assertEqual(stop.returncode, 0, stop.stdout + self.log())
        self.assertIn('uap0', self.ifaces.read_text())
        self.assertEqual(self.pf.read_text(), PF_RULES)

    def test_an_en6_that_will_not_go_keeps_the_factory_driver_out(self):
        self.assertEqual(self.start().returncode, 0, self.log())
        (self.base / 'en6-stuck').touch()
        result = self.run_script('stop')
        self.assertEqual(result.returncode, 2, self.log())
        self.assertIn('en6 is still present', self.log())
        self.assertFalse([c for c in self.recorded().splitlines()
                          if c.startswith('mount') and 'irq=13' in c])
        self.assertTrue((self.state / 'lock').exists())

    def test_firmware_left_after_the_stop_keeps_the_factory_driver_out(self):
        self.assertEqual(self.start().returncode, 0, self.log())
        result = self.run_script('stop', MOCK_CHECK_RC='4')
        self.assertEqual(result.returncode, 2, self.log())
        self.assertIn('full power-off is the recovery', self.log())
        self.assertFalse([c for c in self.recorded().splitlines()
                          if c.startswith('mount') and 'irq=13' in c])

    # --- the keeper ---------------------------------------------------------------
    def test_no_hotspot_is_partial_and_the_keeper_finishes_once_it_appears(self):
        self.hotspot_off.touch()
        result = self.start()
        self.assertEqual(result.returncode, 3, result.stdout + self.log())
        self.assertIn('not associated', result.stdout)
        self.assertNotIn('dhcp ', self.recorded())
        self.assertTrue(self.alive('keeper.pid'))
        self.hotspot_off.unlink()
        self.wait_for(lambda: 'sshd: listening on 172.20.10.13' in self.log(), 'the late lease')
        self.wait_for(lambda: self.run_script('status').returncode == 0, 'status OK')

    def test_a_new_lease_moves_sshd_and_the_ssh_rule(self):
        self.assertEqual(self.start().returncode, 0, self.log())
        self.lease.write_text('\tinet 172.20.10.4 netmask 0xfffffff0 broadcast 172.20.10.15\n')
        (self.state.parent / 'ifaces.en6').write_text(self.lease.read_text())
        self.wait_for(lambda: 'sshd: listening on 172.20.10.4:2222' in self.log(), 'the rebind')
        rules = self.pf.read_text()
        self.assertEqual(rules.count('label "mmi_sta_ssh"'), 1)
        self.assertIn('from 172.20.10.0/28 to 172.20.10.4 port = 2222', rules)
        self.assertEqual(self.recorded().count('sshd -D'), 2)
        self.assertIn('ListenAddress 172.20.10.4\n', (self.state / 'sshd_config').read_text())

    def test_a_competing_default_route_is_logged_and_makes_the_status_partial(self):
        self.assertEqual(self.start().returncode, 0, self.log())
        # en5's lease renewed at home, listed before en6's.
        text = self.routes.read_text()
        self.routes.write_text('default            192.168.16.1       UGS         0        0      -  en5\n' + text)
        self.wait_for(lambda: '192.168.16.1 en5; 172.20.10.1 en6;' in self.log(), 'the log line')
        status = self.run_script('status')
        self.assertEqual(status.returncode, 3, status.stdout)
        self.assertIn("the default route is not the station's alone", status.stdout)

    def test_a_supplicant_that_exits_is_restarted(self):
        self.assertEqual(self.start().returncode, 0, self.log())
        os.kill(int((self.state / 'wpa.pid').read_text()), signal.SIGKILL)
        self.wait_for(lambda: self.recorded().count('wpa -i en6') == 2, 'the restart')
        self.assertIn('wpa_supplicant has exited', self.log())

    def test_a_lost_nat_rule_is_put_back(self):
        self.assertEqual(self.start().returncode, 0, self.log())
        self.nat.write_text(NAT)
        self.wait_for(lambda: EN6_NAT in self.nat.read_text(), 'the NAT rule')

    def test_an_sshd_configuration_that_does_not_test_is_partial(self):
        (self.base / 'sshd-bad').touch()
        result = self.start()
        self.assertEqual(result.returncode, 3, self.log())
        self.assertIn('no sshd on the lease', result.stdout)
        self.assertNotIn('sshd -D', self.recorded())

    # --- status and stop without a start -------------------------------------------
    def test_status_and_stop_when_not_started(self):
        status = self.run_script('status')
        self.assertEqual(status.returncode, 0)
        self.assertIn('not started', status.stdout)
        stop = self.run_script('stop')
        self.assertEqual(stop.returncode, 0)
        self.assertIn('nothing to stop', stop.stdout)
        self.assertEqual([c for c in self.recorded().splitlines()
                          if not c.startswith(('ifconfig en6', 'ifconfig uap0'))], [])
        self.assertFalse(self.state.exists())

    def test_usage(self):
        for args in ([], ['start', '-x'], ['status', 'x'], ['restart']):
            self.assertEqual(self.run_script(*args).returncode, 1, args)

    # --- the unit's utilities -------------------------------------------------------
    def test_uses_no_utility_the_unit_lacks(self):
        # Checked on the unit on 2026-10-28/29: no wc, nohup, tail, head or
        # printf; its grep has no -o and rejects an empty pattern; its date
        # has no +%s.
        text = (ROOT / 'wifi-sta/sta-mode.sh').read_text()
        body = '\n'.join(line.split('#', 1)[0] for line in text.splitlines()
                         if not line.lstrip().startswith('#'))
        for absent in (' wc ', 'nohup', '| tail', '| head', 'printf', "grep -c ''", '+%s'):
            self.assertNotIn(absent, body)
        self.assertNotRegex(body, r'\bgrep\s+(-[A-Za-z]*o|--only)')

    def test_an_empty_argument_list_is_guarded(self):
        # The unit's ksh rejects an empty "$@" under set -u ("@: parameter
        # not set", 7.3 run on 2026-10-29); dash accepts it, so this is
        # checked in the text. Only bounded() takes "$@" bare: it always
        # has a command.
        text = (ROOT / 'wifi-sta/sta-mode.sh').read_text()
        bare = [line.strip() for line in text.splitlines()
                if '"$@"' in line and '${1+"$@"}' not in line]
        self.assertEqual(bare, ['"$@" > "$bounded_out" 2>&1 &'])

    def test_mask_and_network_helpers(self):
        script = (f'. /dev/null; eval "$(sed -n "/^mask_bits()/,/^}}/p;/^network_of()/,/^}}/p" '
                  f'{self.script})"; ')
        cases = {'mask_bits 0xfffffff0': '28', 'mask_bits 0xffffff00': '24',
                 'mask_bits 0xffff0000': '16', 'network_of 172.20.10.13 28': '172.20.10.0',
                 'network_of 192.168.16.11 20': '192.168.16.0',
                 'network_of 10.1.2.3 32': '10.1.2.3'}
        for call, want in cases.items():
            out = subprocess.run(['dash', '-c', script + call], capture_output=True, text=True)
            self.assertEqual(out.stdout.strip(), want, (call, out.stderr))


if __name__ == '__main__':
    unittest.main()
