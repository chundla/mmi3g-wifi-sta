"""Exercise the phase 3 radio detach/re-attach probe with fake QNX utilities.

These tests run the script under dash on the workstation with stand-ins for
ifconfig, mount, pidin and sloginfo. They show the script's decisions and
safety interlocks. They do not show that /bin/ksh on the unit behaves the same,
that ifconfig destroy reaches the driver, or what the radio does.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DRIVER_SUM = '1022510863 93538'
DHCP_CLIENT_SUM = '263280565 57048'
DHCP_UP_SUM = '3046797907 6292'
ATTACHED = 'Jan 01 00:05:01.123    5  1007     0 mv8688uap_pb_init()/568: selected DMA=0x104\n'
NO_FIRMWARE = ('Jan 01 00:05:01.456    2  1007     0 '
               'mv8688uap_probe()/1138: uap_init_fw() failed.\n')


class RadioReattach(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.bin = base / 'bin'
        self.state = base / 'state'
        self.sysregs = base / 'sysregs'
        self.driver = base / 'devnp-mv8688uap.so'
        for directory in (self.bin, self.sysregs):
            directory.mkdir()
        self.driver.write_bytes(b'factory driver')
        (self.sysregs / 'WlanEnable').write_text('0x00000000\n')
        (self.sysregs / 'BtReset').write_text('0x00000000\n')
        self.calls = base / 'calls'
        self.ifaces = base / 'ifaces'
        self.slog = base / 'slog'
        self.ifaces.write_text('uap0\nmam0\nen5\n')
        self.slog.write_text('Jan 01 00:00:01.000    5  1007     0 boot\n')

        text = (ROOT / 'wifi-sta/radio-reattach.sh').read_text()
        for original, replacement in (
                ('driver=/lib/dll/devnp-mv8688uap.so', f'driver={self.driver}'),
                ('state=/fs/tmpfs/mmi-radio', f'state={self.state}'),
                ('/dev/sysregs/', f'{self.sysregs}/'),
                ('dhcp_client=/usr/sbin/dhcp.client', f'dhcp_client={self.bin}/dhcp.client'),
                ('dhcp_up=/etc/dhcp/dhcp-up', f'dhcp_up={base}/dhcp-up'),
                ('dhcp_down_marker=/tmp/ifwatchd_dhcp_down',
                 f'dhcp_down_marker={base}/ifwatchd_dhcp_down'),
                ('dhcp_semaphore=/dev/shmem/networkscript.semaphore',
                 f'dhcp_semaphore={base}/networkscript.semaphore'),
                ('resolv_conf=/etc/resolv.conf', f'resolv_conf={base}/resolv.conf'),
                ('dlink_marker=/HBpersistence/DLinkReplacesPPP',
                 f'dlink_marker={base}/DLinkReplacesPPP'),
                ('rse_marker=/mnt/efs-system/RSE', f'rse_marker={base}/RSE'),
                ('ppp_route_marker=/tmp/ppp0_defaultroute',
                 f'ppp_route_marker={base}/ppp0_defaultroute'),
                ('PATH=/bin:/sbin:/usr/bin:/usr/sbin',
                 f'PATH={self.bin}:/bin:/usr/bin')):
            self.assertIn(original, text)
            text = text.replace(original, replacement)
        for line in text.splitlines():
            if '/fs/tmpfs' in line:
                self.assertTrue(line.lstrip().startswith('#'), line)
        self.script = base / 'radio-reattach.sh'
        self.script.write_text(text)

        self.env = dict(
            os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}',
            SSH_CONNECTION='192.168.16.1 50000 192.168.16.11 2222',
            MOCK_CALLS=str(self.calls), MOCK_IFACES=str(self.ifaces),
            MOCK_SLOG=str(self.slog), MOCK_CKSUM=DRIVER_SUM,
            MOCK_MOUNT_FAIL='', MOCK_AUTO_REMOUNT='')
        self.tool('uname', '#!/bin/sh\n[ "$1" = -n ] && { echo mmi; exit 0; }\necho QNX\n')
        # The pinned factory DHCP files have sums of their own.
        self.tool('cksum', '#!/bin/sh\ncase $1 in\n'
                           '    *dhcp.client) echo "${MOCK_DHCP_CLIENT_SUM:-%s} $1";;\n'
                           '    *dhcp-up) echo "${MOCK_DHCP_UP_SUM:-%s} $1";;\n'
                           '    *) echo "$MOCK_CKSUM $1";;\nesac\n'
                  % (DHCP_CLIENT_SUM, DHCP_UP_SUM))
        self.tool('sleep', '#!/bin/sh\nexec /bin/sleep 0.01\n')
        self.tool('df', '#!/bin/sh\necho header\necho "tmpfs 1M 0 1M 0% /fs/tmpfs"\n')
        self.tool('netstat', '#!/bin/sh\necho "no default route"\n')
        self.tool('sloginfo', '#!/bin/sh\ncat "$MOCK_SLOG"\n')
        self.tool('pidin', '''#!/bin/sh
echo "pidin $*" >> "$MOCK_CALLS"
grep -q uap0 "$MOCK_IFACES" && echo "    1  /lib/dll/devnp-mv8688uap.so"
echo "    2  /lib/dll/devnp-mlb.so"
''')
        self.tool('ifconfig', '''#!/bin/sh
echo "ifconfig $*" >> "$MOCK_CALLS"
if [ "$1" = -a ]; then cat "$MOCK_IFACES"; exit 0; fi
grep -qx "$1" "$MOCK_IFACES" || exit 1
case $2 in
    destroy) grep -vx "$1" "$MOCK_IFACES" > "$MOCK_IFACES.new"
             mv "$MOCK_IFACES.new" "$MOCK_IFACES";;
    inet) echo "address $*" >> "$MOCK_CALLS";;
    '') echo "$1: flags=8863"
        echo "	status: active"
        [ "$1" = mam0 ] && echo "	inet 10.0.0.100 netmask 0xffffff00"
        [ -f "$MOCK_IFACES.$1" ] && cat "$MOCK_IFACES.$1";;
esac
exit 0
''')
        self.tool('mount', '''#!/bin/sh
echo "mount $*" >> "$MOCK_CALLS"
params=$4
# The station driver: lan=6 gives en6, or nothing with MOCK_STA_NO_ATTACH.
case $params in
    lan=*) [ -n "$MOCK_STA_NO_ATTACH" ] && exit 0
           echo en6 >> "$MOCK_IFACES"; exit 0;;
esac
case $MOCK_MOUNT_FAIL in
    '') ;;
    *) case $params in
           $MOCK_MOUNT_FAIL)
               printf '%s' "$MOCK_NO_FIRMWARE" >> "$MOCK_SLOG"
               exit 1;;
       esac;;
esac
printf '%s' "$MOCK_ATTACHED" >> "$MOCK_SLOG"
echo uap0 >> "$MOCK_IFACES"
exit 0
''')
        self.env['MOCK_ATTACHED'] = ATTACHED
        self.env['MOCK_NO_FIRMWARE'] = NO_FIRMWARE

    def tool(self, name, text):
        path = self.bin / name
        path.write_text(text)
        path.chmod(0o755)

    def run_script(self, *args, **overrides):
        env = dict(self.env, **overrides)
        return subprocess.run(['dash', str(self.script), *args], env=env,
                              capture_output=True, text=True, timeout=180)

    def recorded(self):
        return self.calls.read_text() if self.calls.exists() else ''

    def report(self):
        return (self.state / 'report.txt').read_text()

    # --- interlocks --------------------------------------------------------
    def test_no_grep_option_the_units_grep_lacks(self):
        # The fakes run on the workstation's grep; the unit's has no -o
        # (6.2's first run printed an empty line from it).
        source = (ROOT / 'wifi-sta' / 'radio-reattach.sh').read_text()
        for line in source.splitlines():
            code = line.split('#', 1)[0]
            self.assertNotRegex(code, r'\bgrep\s+(-[A-Za-z]*o|--only)', line)

    def test_refuses_a_session_arriving_on_the_access_point(self):
        result = self.run_script(
            SSH_CONNECTION='192.168.1.100 50000 192.168.1.1 2222')
        self.assertEqual(result.returncode, 1)
        self.assertIn('arrives on uap0', result.stderr)
        self.assertNotIn('destroy', self.recorded())

    def test_refuses_a_driver_that_is_not_the_reviewed_object(self):
        result = self.run_script(MOCK_CKSUM='1 2')
        self.assertEqual(result.returncode, 1)
        self.assertIn('not the reviewed factory object', result.stderr)
        self.assertEqual(self.recorded(), '')

    def test_refuses_to_run_without_a_wired_management_path(self):
        self.ifaces.write_text('uap0\nmam0\n')
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn('en5 is absent', result.stderr)
        self.assertNotIn('destroy', self.recorded())

    def test_dry_run_touches_no_interface(self):
        result = self.run_script('-n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('dry run', result.stdout)
        self.assertNotIn('destroy', self.recorded())
        self.assertNotIn('mount', self.recorded())
        self.assertIn('uap0\n', self.ifaces.read_text())

    def test_rejects_a_deadline_below_the_minimum(self):
        result = self.run_script('-t', '30')
        self.assertEqual(result.returncode, 2)
        self.assertIn('usage', result.stderr)

    # --- sequence ----------------------------------------------------------
    def test_probe_b_claims_nothing_once_probe_a_found_no_firmware(self):
        # Observed on the unit on 2026-10-28: the detach clears the firmware,
        # so both probes fail and the second one proves nothing about the
        # reset line.
        result = self.run_script(MOCK_MOUNT_FAIL='*nofw*')
        report = self.report()
        self.assertIn('the detach alone clears the firmware', report)
        self.assertIn('no firmware verdict is possible', report)
        self.assertNotIn('resetmask=4 cleared the firmware', report)
        self.assertIn('verdict OK', report)
        self.assertEqual(result.returncode, 0)

    def test_reports_a_card_that_never_answers_sdio(self):
        self.env['MOCK_NO_FIRMWARE'] = (
            'Jan 01 00:28:00.333    2    10   1000 '
            '_hbsdc_iprocess(): Command Timeout\n')
        report_fail = self.run_script('-1', MOCK_MOUNT_FAIL='*nofw*')
        self.assertIn('did not answer SDIO at all', self.report())
        self.assertEqual(report_fail.returncode, 0)

    def test_reset_line_probe_reads_the_firmware_failure_from_slog(self):
        # Only the resetmask=4 mount fails, and it logs uap_init_fw() failed.
        result = self.run_script(MOCK_MOUNT_FAIL='*nofw*resetmask=4')
        report = self.report()
        self.assertIn('uap0 is gone', report)
        self.assertIn('the driver object was unloaded', report)
        self.assertIn('no automatic re-mount', report)
        self.assertIn('register 0x28 power-down leave the firmware running', report)
        self.assertIn('resetmask=4 cleared the firmware while resetmask=0 did not', report)
        # The restore must not be the probe's empty firmware directory.
        mounts = [line for line in self.recorded().splitlines()
                  if line.startswith('mount ')]
        self.assertIn('fwpath=/lib/dll,resetmask=4', mounts[-1])
        # A probe mount that fails for want of firmware is the expected
        # outcome, not a fault in the run.
        self.assertIn('verdict OK', report)
        self.assertEqual(result.returncode, 0)

    def test_probe_order_and_restore_address(self):
        result = self.run_script()
        report = self.report()
        self.assertIn('resetmask=4 did not clear the firmware either', report)
        self.assertIn('uap0 address 192.168.1.1 configured', report)
        self.assertIn('carries no SSID or key', report)
        self.assertIn('watchdog disarmed', report)
        self.assertIn('verdict OK', report)
        self.assertEqual(result.returncode, 0, result.stdout)
        masks = [line.split('resetmask=')[1][0] for line in
                 self.recorded().splitlines() if line.startswith('mount ')]
        self.assertEqual(masks, ['0', '4', '4'])

    def test_factory_mode_tries_the_factory_parameters_first(self):
        self.run_script('-F')
        mounts = [line for line in self.recorded().splitlines()
                  if line.startswith('mount ')]
        self.assertEqual(len(mounts), 1, mounts)
        self.assertIn('fwpath=/lib/dll,resetmask=0', mounts[0])
        report = self.report()
        self.assertIn('no probes', report)
        self.assertIn('factory parameters re-attached the radio', report)
        self.assertNotIn('probe-a', report)

    def test_factory_mode_falls_back_and_says_the_factory_mount_failed(self):
        result = self.run_script('-F', MOCK_MOUNT_FAIL='*resetmask=0*')
        mounts = [line.split('resetmask=')[1][0] for line in
                  self.recorded().splitlines() if line.startswith('mount ')]
        self.assertEqual(mounts, ['0', '4'])
        report = self.report()
        self.assertIn('did NOT re-attach', report)
        self.assertIn('cannot stand alone here', report)
        self.assertIn('verdict PARTIAL', report)
        self.assertEqual(result.returncode, 1)

    def test_single_probe_option_skips_the_reset_line(self):
        self.run_script('-1')
        masks = [line.split('resetmask=')[1][0] for line in
                 self.recorded().splitlines() if line.startswith('mount ')]
        self.assertEqual(masks, ['0', '4'])
        self.assertNotIn('probe-b', self.report())

    def test_stops_when_the_networking_service_remounts_the_driver(self):
        # A mount by someone else during the settle window: the fake ifconfig
        # reports uap0 again because this tool puts it back.
        self.tool('pidin', f'''#!/bin/sh
echo "pidin $*" >> "$MOCK_CALLS"
echo "    1  /lib/dll/devnp-mlb.so"
echo uap0 >> "{self.ifaces}"
''')
        result = self.run_script()
        report = self.report()
        self.assertIn('reappeared on its own', report)
        self.assertIn('verdict PARTIAL', report)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('mount ', self.recorded())

    def test_reports_failure_when_the_restore_cannot_mount(self):
        result = self.run_script('-1', MOCK_MOUNT_FAIL='*fwpath=/lib/dll*')
        report = self.report()
        self.assertIn('could not restore the driver', report)
        self.assertIn('leaving the watchdog armed', report)
        self.assertIn('verdict FAIL', report)
        self.assertEqual(result.returncode, 1)

    def test_watchdog_restores_the_driver_when_the_run_is_killed(self):
        # The fake destroy kills the run, as a dropped session or a crash
        # would. The watchdog waits on the process, not on a clock, so it
        # should mount the recovery parameters and nothing else should.
        self.tool('ifconfig', f'''#!/bin/sh
echo "ifconfig $*" >> "$MOCK_CALLS"
if [ "$1" = -a ]; then cat "$MOCK_IFACES"; exit 0; fi
grep -qx "$1" "$MOCK_IFACES" || exit 1
case $2 in
    destroy) grep -vx "$1" "$MOCK_IFACES" > "$MOCK_IFACES.new"
             mv "$MOCK_IFACES.new" "$MOCK_IFACES"
             kill -9 "$(cat {self.state}/alive)" 2>/dev/null;;
    '') echo "$1: flags=8863"; echo "	status: active";;
esac
exit 0
''')
        # The script does not publish its pid, so record it from the wrapper.
        wrapper = self.script.parent / 'wrapper.sh'
        wrapper.write_text(f'#!/bin/sh\nmkdir -p {self.state}\n'
                           f'echo $$ > {self.state}/alive\n'
                           f'exec dash {self.script} "$@"\n')
        wrapper.chmod(0o755)
        subprocess.run(['dash', str(wrapper)], env=self.env,
                       capture_output=True, text=True, timeout=180)
        watchdog = (self.state / 'watchdog.log').read_text()
        # Whichever it noticed first, it must not have waited for the cap.
        self.assertRegex(watchdog, r'the run is gone|heartbeat stopped advancing')
        self.assertNotIn('cap of', watchdog)
        self.assertIn('fwpath=/lib/dll,resetmask=4', watchdog)
        self.assertIn('uap0\n', self.ifaces.read_text())

    # --- host bring-up probe (-P) -------------------------------------------
    def test_watchdog_stops_a_running_probe_before_it_restores(self):
        # The probe kills the run and keeps going, as it would if the session
        # dropped mid-probe. A restore under a live probe would let it reset
        # the card beneath the newly mounted driver.
        probe = self.bin.parent / 'mv8688-probe'
        probe.write_text(f'''#!/bin/sh
echo "probe $*" >> "$MOCK_CALLS"
if [ "$1" = -n ]; then
    if grep -q uap0 "$MOCK_IFACES"; then
        echo "verdict=REFUSED the module is in use"; exit 1
    fi
    echo "verdict=GUARD-PASS module idle; nothing written"; exit 0
fi
trap 'echo "probe stopped by TERM" >> "$MOCK_CALLS"; exit 3' TERM
kill -9 "$(cat {self.state}/alive)" 2>/dev/null
i=0
while [ $i -lt 300 ]; do /bin/sleep 0.1; i=$((i + 1)); done
echo "probe outlived the restore" >> "$MOCK_CALLS"
''')
        probe.chmod(0o755)
        wrapper = self.script.parent / 'wrapper.sh'
        wrapper.write_text(f'#!/bin/sh\nmkdir -p {self.state}\n'
                           f'echo $$ > {self.state}/alive\n'
                           f'exec dash {self.script} "$@"\n')
        wrapper.chmod(0o755)
        subprocess.run(['dash', str(wrapper), '-P', str(probe)], env=self.env,
                       capture_output=True, text=True, timeout=180)
        watchdog = (self.state / 'watchdog.log').read_text()
        self.assertIn('stopping the host probe', watchdog)
        calls = self.recorded().splitlines()
        self.assertNotIn('probe outlived the restore', calls)
        stopped = calls.index('probe stopped by TERM')
        mounts = [i for i, c in enumerate(calls) if c.startswith('mount ')]
        self.assertTrue(mounts, calls)
        self.assertLess(stopped, mounts[0])
        self.assertFalse((self.state / 'probe.pid').exists())

    def host_probe(self, guard='honest', rc=0, check_rc=0, before=''):
        # The real guard reads CTRL and the power register; here uap0's
        # presence stands in for "a driver holds the module".
        probe = self.bin.parent / 'mv8688-probe'
        refuse = ('grep -qE "uap0|en6" "$MOCK_IFACES"' if guard == 'honest' else 'false')
        probe.write_text(f'''#!/bin/sh
echo "probe $*" >> "$MOCK_CALLS"
if [ "$1" = -n ]; then
    if {refuse}; then
        echo "verdict=REFUSED the module is in use (a driver is attached)"; exit 1
    fi
    echo "verdict=GUARD-PASS module idle; nothing written"; exit 0
fi
if [ "$1" = -c ]; then
    echo "check: fw_status checked"; exit {check_rc}
fi
{before}
echo "cis: vendor=0x02df device=0x9103"
echo "verdict=OK"
exit {rc}
''')
        probe.chmod(0o755)
        return str(probe)

    def calls_in_order(self):
        return [line for line in self.recorded().splitlines()
                if line.startswith(('probe', 'mount ', 'ifconfig uap0 destroy'))]

    def test_host_probe_runs_between_the_detach_and_a_factory_reattach(self):
        result = self.run_script('-P', self.host_probe())
        calls = self.calls_in_order()
        self.assertEqual([c.split(' -o ')[0].strip() for c in calls],
                         ['probe -n', 'ifconfig uap0 destroy', 'probe -n', 'probe',
                          'mount -T io-pkt'])
        self.assertIn('fwpath=/lib/dll,resetmask=0', calls[-1])
        report = self.report()
        self.assertIn('refuses while the factory driver is attached', report)
        self.assertIn('host-probe: cis: vendor=0x02df', report)
        self.assertIn('RESULT: host bring-up reached function 1', report)
        self.assertNotIn('nofw', self.recorded())
        self.assertIn('verdict OK', report)
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_host_probe_whose_guard_passes_on_an_attached_module_stops_the_run(self):
        result = self.run_script('-P', self.host_probe(guard='broken'))
        self.assertEqual(result.returncode, 1)
        self.assertIn('did not refuse the attached module', self.report())
        self.assertNotIn('destroy', self.recorded())
        self.assertNotIn('mount ', self.recorded())

    def test_a_failed_host_probe_still_restores_the_radio(self):
        result = self.run_script('-P', self.host_probe(rc=2))
        report = self.report()
        self.assertIn('failed after bring-up started', report)
        self.assertIn('restored with', report)
        self.assertIn('verdict PARTIAL', report)
        self.assertEqual(result.returncode, 1)
        self.assertIn('uap0\n', self.ifaces.read_text())

    def test_host_probe_dry_run_only_reads_the_guard(self):
        result = self.run_script('-n', '-P', self.host_probe())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls_in_order(), ['probe -n'])

    def images(self):
        helper = self.bin.parent / 'helper.bin'
        station = self.bin.parent / 'sd8688.bin'
        helper.write_bytes(b'h' * 16)
        station.write_bytes(b's' * 32)
        return str(helper), str(station)

    def test_firmware_download_passes_the_images_to_the_real_run_only(self):
        helper, station = self.images()
        result = self.run_script('-P', self.host_probe(), '-H', helper, '-S', station)
        self.assertEqual(result.returncode, 0, result.stdout)
        probes = [c for c in self.calls_in_order() if c.startswith('probe')]
        self.assertEqual(probes, ['probe -n', 'probe -n', f'probe -H {helper} -F {station}'])
        report = self.report()
        self.assertIn(f'firmware image {station} cksum', report)
        self.assertIn('with the firmware download', report)
        self.assertIn('RESULT: the firmware reached its ready magic', report)
        self.assertIn('fwpath=/lib/dll,resetmask=0', self.calls_in_order()[-1])

    def test_commands_go_to_the_real_run_only(self):
        helper, station = self.images()
        result = self.run_script('-P', self.host_probe(), '-H', helper, '-S', station, '-C')
        self.assertEqual(result.returncode, 0, result.stdout)
        probes = [c for c in self.calls_in_order() if c.startswith('probe')]
        self.assertEqual(probes, ['probe -n', 'probe -n',
                                  f'probe -H {helper} -F {station} -C'])
        report = self.report()
        self.assertIn('with the firmware download and commands', report)
        self.assertIn('RESULT: the station firmware answered its commands', report)
        self.assertIn('fwpath=/lib/dll,resetmask=0', self.calls_in_order()[-1])

    def test_commands_need_the_images(self):
        probe = self.host_probe()
        for args in (('-P', probe, '-C'), ('-F', '-C'), ('-C',)):
            result = self.run_script(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn('usage:', result.stderr)
        self.assertEqual(self.recorded(), '')

    def test_scan_gives_the_probe_the_access_points_factory_mac(self):
        helper, station = self.images()
        (self.ifaces.parent / 'ifaces.uap0').write_text('\taddress: 00:1c:d7:00:00:01\n')
        result = self.run_script('-P', self.host_probe(), '-H', helper, '-S', station, '-C',
                                 '-L')
        self.assertEqual(result.returncode, 0, result.stdout)
        probes = [c for c in self.calls_in_order() if c.startswith('probe')]
        self.assertEqual(probes, ['probe -n', 'probe -n',
                                  f'probe -H {helper} -F {station} -C -M 00:1c:d7:00:00:01 -L'])
        report = self.report()
        self.assertIn('uap0 factory MAC 00:1c:d7:00:00:01 goes to the station', report)
        self.assertIn('completed a passive scan', report)
        self.assertIn('fwpath=/lib/dll,resetmask=0', self.calls_in_order()[-1])

    def test_scan_refuses_a_uap0_without_a_usable_mac_before_the_detach(self):
        helper, station = self.images()
        for line in ('\taddress: 00:00:00:00:00:00\n', '\taddress: AA:BB:CC:DD:EE:FF\n',
                     '\taddress: 01:1c:d7:00:00:01\n', '\taddress: 00:1c:d7:00:00\n', ''):
            (self.ifaces.parent / 'ifaces.uap0').write_text(line)
            result = self.run_script('-P', self.host_probe(), '-H', helper, '-S', station,
                                     '-C', '-L')
            self.assertEqual(result.returncode, 1, line)
            self.assertIn('no usable factory MAC', result.stderr)
            self.assertNotIn('destroy', self.recorded())
            self.assertNotIn('probe', self.recorded())

    def test_timing_is_passed_to_the_real_run_after_the_other_options(self):
        helper, station = self.images()
        result = self.run_script('-P', self.host_probe(), '-H', helper, '-S', station, '-C',
                                 '-T')
        self.assertEqual(result.returncode, 0, result.stdout)
        probes = [c for c in self.calls_in_order() if c.startswith('probe')]
        self.assertEqual(probes, ['probe -n', 'probe -n',
                                  f'probe -H {helper} -F {station} -C -T'])
        report = self.report()
        self.assertIn('download and commands, timing.', report)
        self.assertIn('RESULT: the PIO timing and read benchmark completed', report)
        (self.ifaces.parent / 'ifaces.uap0').write_text('\taddress: 00:1c:d7:00:00:01\n')
        result = self.run_script('-P', self.host_probe(), '-H', helper, '-S', station, '-C',
                                 '-L', '-T')
        self.assertEqual(result.returncode, 0, result.stdout)
        probes = [c for c in self.calls_in_order() if c.startswith('probe')]
        self.assertEqual(probes[-1],
                         f'probe -H {helper} -F {station} -C -M 00:1c:d7:00:00:01 -L -T')

    def test_timing_needs_the_commands(self):
        helper, station = self.images()
        result = self.run_script('-P', self.host_probe(), '-H', helper, '-S', station, '-T')
        self.assertEqual(result.returncode, 2)
        self.assertIn('usage:', result.stderr)
        self.assertEqual(self.recorded(), '')

    def test_scan_needs_the_commands(self):
        helper, station = self.images()
        result = self.run_script('-P', self.host_probe(), '-H', helper, '-S', station, '-L')
        self.assertEqual(result.returncode, 2)
        self.assertIn('usage:', result.stderr)
        self.assertEqual(self.recorded(), '')

    def test_join_passes_the_ssid_after_the_mac_and_scan(self):
        helper, station = self.images()
        (self.ifaces.parent / 'ifaces.uap0').write_text('\taddress: 00:1c:d7:00:00:01\n')
        result = self.run_script('-P', self.host_probe(), '-H', helper, '-S', station, '-C',
                                 '-L', '-A', 'mmi-bench_1.0')
        self.assertEqual(result.returncode, 0, result.stdout)
        probes = [c for c in self.calls_in_order() if c.startswith('probe')]
        self.assertEqual(probes[-1], f'probe -H {helper} -F {station} -C -M 00:1c:d7:00:00:01 '
                                     '-L -A mmi-bench_1.0')
        report = self.report()
        self.assertIn("a join of 'mmi-bench_1.0'", report)
        self.assertIn("RESULT: the station associated with 'mmi-bench_1.0'", report)

    def test_join_refuses_an_ssid_the_shell_could_mangle_or_no_scan(self):
        helper, station = self.images()
        (self.ifaces.parent / 'ifaces.uap0').write_text('\taddress: 00:1c:d7:00:00:01\n')
        for args in (('-L', '-A', 'two words'), ('-L', '-A', 'semi;colon'),
                     ('-L', '-A', '*'), ('-L', '-A', 'x' * 33), ('-L', '-A', ''),
                     ('-A', 'mmi-bench')):
            result = self.run_script('-P', self.host_probe(), '-H', helper, '-S', station,
                                     '-C', *args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn('usage:', result.stderr)
            self.assertEqual(self.recorded(), '', args)

    def test_data_frames_pass_both_addresses_after_the_ssid(self):
        helper, station = self.images()
        (self.ifaces.parent / 'ifaces.uap0').write_text('\taddress: 00:1c:d7:00:00:01\n')
        result = self.run_script('-P', self.host_probe(), '-H', helper, '-S', station, '-C',
                                 '-L', '-A', 'mmi-bench', '-I', '10.42.88.10',
                                 '-E', '10.42.88.1', '-T')
        self.assertEqual(result.returncode, 0, result.stdout)
        probes = [c for c in self.calls_in_order() if c.startswith('probe')]
        self.assertEqual(probes[-1], f'probe -H {helper} -F {station} -C -M 00:1c:d7:00:00:01 '
                                     '-L -A mmi-bench -I 10.42.88.10 -E 10.42.88.1 -T')
        report = self.report()
        self.assertIn('data frames from 10.42.88.10 to 10.42.88.1', report)
        self.assertIn('exchanged ARP and ICMP echoes with 10.42.88.1', report)

    def test_data_frames_refuse_a_lone_or_malformed_address_or_no_join(self):
        helper, station = self.images()
        (self.ifaces.parent / 'ifaces.uap0').write_text('\taddress: 00:1c:d7:00:00:01\n')
        join = ('-L', '-A', 'mmi-bench')
        for args in (join + ('-I', '10.42.88.10'), join + ('-E', '10.42.88.1'),
                     join + ('-I', '10.42.88.10 10.42.88.11', '-E', '10.42.88.1'),
                     join + ('-I', '10.42.88', '-E', '10.42.88.1'),
                     join + ('-I', '10.42.88.10', '-E', '10.42.88.1.5'),
                     join + ('-I', '10.42.88.10', '-E', '$(id)'),
                     join + ('-I', '', '-E', '10.42.88.1'),
                     ('-L', '-I', '10.42.88.10', '-E', '10.42.88.1')):
            result = self.run_script('-P', self.host_probe(), '-H', helper, '-S', station,
                                     '-C', *args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn('usage:', result.stderr)
            self.assertEqual(self.recorded(), '', args)

    def test_failed_commands_are_followed_by_the_firmware_check(self):
        helper, station = self.images()
        result = self.run_script('-P', self.host_probe(rc=2), '-H', helper, '-S', station,
                                 '-C')
        calls = [c.split(' -o ')[0].strip() for c in self.calls_in_order()]
        self.assertEqual(calls[-2:], ['probe -c', 'mount -T io-pkt'])
        self.assertIn('failed after bring-up started', self.report())
        self.assertIn('verdict PARTIAL', result.stdout)

    def test_firmware_options_need_both_images_and_the_host_probe(self):
        helper, station = self.images()
        probe = self.host_probe()
        for args in (('-P', probe, '-H', helper),
                     ('-P', probe, '-S', station),
                     ('-F', '-H', helper, '-S', station),
                     ('-P', probe, '-F'),
                     ('-1', '-F'),
                     ('-P', probe, 'stray'),
                     ('-P', probe, '-H', '', '-S', station),
                     ('-P', '')):
            result = self.run_script(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn('usage:', result.stderr)
        self.assertEqual(self.recorded(), '')

    def withheld(self, result):
        report = self.report()
        self.assertIn('factory re-attach is withheld', report)
        self.assertIn('not mounting: firmware may still be running', report)
        self.assertIn('watchdog disarmed: the driver must stay unmounted', report)
        self.assertIn('power the unit off fully', report)
        self.assertNotIn('mount ', self.recorded())
        self.assertFalse((self.state / 'watchdog.log').exists())
        self.assertEqual(result.returncode, 1)

    def test_firmware_that_survived_the_power_down_withholds_the_restore(self):
        helper, station = self.images()
        result = self.run_script('-P', self.host_probe(rc=4), '-H', helper, '-S', station)
        self.withheld(result)
        # The probe already checked; no second check is run.
        self.assertNotIn('probe -c', self.recorded())

    def test_an_unfinished_download_is_checked_before_the_restore(self):
        helper, station = self.images()
        result = self.run_script('-P', self.host_probe(rc=3), '-H', helper, '-S', station)
        calls = [c.split(' -o ')[0].strip() for c in self.calls_in_order()]
        self.assertEqual(calls[-2:], ['probe -c', 'mount -T io-pkt'])
        self.assertIn('found no ready magic; the factory attach may run', self.report())
        self.assertIn('restored with', self.report())
        self.assertEqual(result.returncode, 1)      # PARTIAL

    def test_a_failed_firmware_check_withholds_the_restore(self):
        helper, station = self.images()
        for check_rc in (4, 1, 2):
            with self.subTest(check_rc=check_rc):
                self.calls.unlink(missing_ok=True)
                self.ifaces.write_text('uap0\nmam0\nen5\n')
                result = self.run_script('-P', self.host_probe(rc=2, check_rc=check_rc),
                                         '-H', helper, '-S', station)
                self.assertIn('probe -c', self.recorded())
                self.withheld(result)

    def test_no_firmware_check_without_a_download(self):
        self.run_script('-P', self.host_probe(rc=3))
        self.assertNotIn('probe -c', self.recorded())
        self.assertIn('restored with', self.report())

    def test_the_run_does_not_mount_when_the_watchdog_owns_the_restore(self):
        # The probe stands in for a watchdog that took ownership mid-run.
        # The run must not mount; the watchdog, finding the run gone, does.
        lock = self.state / 'restore.owner'
        result = self.run_script('-P', self.host_probe(before=f'mkdir {lock}'))
        self.assertIn('the watchdog owns the restore', self.report())
        self.assertNotIn('restored with', self.report())
        watchdog = (self.state / 'watchdog.log').read_text()
        self.assertIn('taking over the restore from a run that is gone or stalled', watchdog)
        mounts = [c for c in self.recorded().splitlines() if c.startswith('mount ')]
        self.assertEqual(len(mounts), 1, mounts)
        self.assertIn('resetmask=4', mounts[0])
        self.assertEqual(result.returncode, 1)

    def test_the_cap_stops_a_live_run_before_the_watchdog_restores(self):
        # A probe that outlasts a 60 s cap: the watchdog acts while the run
        # is alive. It must stop the run and the probe, and mount once.
        probe = self.host_probe(before='''trap 'echo "probe stopped by TERM" >> "$MOCK_CALLS"; exit 3' TERM
i=0; while [ $i -lt 100 ]; do /bin/sleep 0.1; i=$((i + 1)); done''')
        self.run_script('-t', '60', '-P', probe)
        watchdog = (self.state / 'watchdog.log').read_text()
        self.assertIn('cap of 60s', watchdog)
        self.assertIn('stopping the run', watchdog)
        # Where the cap lands depends on scheduling; either way the probe
        # must be stopped (or never started) before the single mount.
        calls = self.recorded().splitlines()
        mounts = [i for i, c in enumerate(calls) if c.startswith('mount ')]
        self.assertEqual(len(mounts), 1, calls)
        self.assertFalse([c for c in calls[mounts[0]:] if c.startswith('probe')], calls)
        if any(c.startswith('probe -H') or c.strip() == 'probe' for c in calls):
            self.assertIn('probe stopped by TERM', calls[:mounts[0]])
        self.assertNotIn('restored with', self.report())

    def test_after_a_download_the_watchdog_checks_the_firmware_first(self):
        helper, station = self.images()
        lock = self.state / 'restore.owner'
        for check_rc, mounted in ((0, True), (4, False)):
            with self.subTest(check_rc=check_rc):
                self.calls.unlink(missing_ok=True)
                (self.state / 'watchdog.log').unlink(missing_ok=True)
                self.ifaces.write_text('uap0\nmam0\nen5\n')
                self.run_script('-P', self.host_probe(check_rc=check_rc, before=f'mkdir {lock}'),
                                '-H', helper, '-S', station)
                watchdog = (self.state / 'watchdog.log').read_text()
                self.assertIn('probe -c', self.recorded())
                mounts = [c for c in self.recorded().splitlines() if c.startswith('mount ')]
                self.assertEqual(bool(mounts), mounted, watchdog)
                if not mounted:
                    self.assertIn('firmware check did not pass', watchdog)

    def test_refuses_a_missing_firmware_image(self):
        helper, _ = self.images()
        result = self.run_script('-P', self.host_probe(), '-H', helper,
                                 '-S', str(self.bin.parent / 'absent.bin'))
        self.assertEqual(result.returncode, 1)
        self.assertIn('missing or empty', result.stderr)
        self.assertEqual(self.recorded(), '')

    def test_refuses_a_missing_host_probe(self):
        result = self.run_script('-P', str(self.bin.parent / 'absent'))
        self.assertEqual(result.returncode, 1)
        self.assertIn('missing or not executable', result.stderr)
        self.assertEqual(self.recorded(), '')

    def test_uses_no_utility_the_unit_lacks(self):
        # Checked on the unit on 2026-10-28: it has no wc, nohup, tail or
        # head, and its grep rejects an empty pattern.
        text = (ROOT / 'wifi-sta/radio-reattach.sh').read_text()
        body = '\n'.join(line for line in text.splitlines()
                         if not line.lstrip().startswith('#'))
        for absent in (' wc ', ' wc\n', 'nohup', '| tail', '| head',
                       "grep -c ''"):
            self.assertNotIn(absent, body)

    def test_keeps_the_watchdog_disarm_marker_out_of_the_way(self):
        self.run_script('-n')
        self.assertFalse((self.state / 'disarm').exists())
        self.assertTrue((self.state / 'nofw').is_dir())
        self.assertFalse((self.state / 'nofw' / 'FwImage').exists())


class StationDriverMode(RadioReattach):
    """-D: the phase 5.2 station driver mounted into io-pkt as en6."""

    PF_RULES = ('scrub in all no-df fragment reassemble\n'
                'pass in quick on uap0 inet proto tcp from 192.168.1.109 to 192.168.1.1 '
                'port = 2222 flags S/SA keep state label "mmi_openssh_pi"\n'
                'pass out all keep state\n'
                'block drop in on uap0 all\n'
                'anchor "dialUpTrigger" all\n')

    def setUp(self):
        super().setUp()
        base = self.bin.parent
        self.pf = base / 'pf.rules'
        self.pf.write_text(self.PF_RULES)
        (base / 'ifaces.uap0').write_text('\taddress: 00:1c:d7:00:00:01\n')
        self.sta_driver = base / 'devn-mv8688sta.so'
        self.sta_driver.write_bytes(b'station driver')
        self.env.update(MOCK_PF=str(self.pf), MOCK_PF_FAIL='', MOCK_PING_RC='0',
                        MOCK_CONNECT_RC='0', MOCK_LINK_LOSSES='0', MOCK_STA_NO_ATTACH='',
                        MOCK_STA_STUCK='')
        self.tool('pfctl', '''#!/bin/sh
echo "pfctl $*" >> "$MOCK_CALLS"
case "$1 $2" in
    "-sr ") cat "$MOCK_PF";;
    "-n -R") [ -f "$4" ];;
    "-R -f") [ -z "$MOCK_PF_FAIL" ] || exit 1
             cp "$3" "$MOCK_PF";;
    *) exit 2;;
esac
''')
        self.tool('ping', '#!/bin/sh\necho "ping $*" >> "$MOCK_CALLS"\n'
                          '[ -n "$MOCK_PING_HOOK" ] && eval "$MOCK_PING_HOOK"\nexit $MOCK_PING_RC\n')
        self.env['MOCK_PING_HOOK'] = ''
        # Scaled by the argument, so the watchdog's 5 s ticks and its 120 s
        # stall keep their real ratio to the run's 1 s waits (30 for en6).
        self.tool('sleep', '#!/bin/sh\ncase $1 in 5) exec /bin/sleep 0.05;; '
                           '10) exec /bin/sleep 0.1;; *) exec /bin/sleep 0.01;; esac\n')
        # ifconfig: an en6 that refuses to go with MOCK_STA_STUCK.
        self.tool('ifconfig', '''#!/bin/sh
echo "ifconfig $*" >> "$MOCK_CALLS"
if [ "$1" = -a ]; then cat "$MOCK_IFACES"; exit 0; fi
grep -qx "$1" "$MOCK_IFACES" || exit 1
case $2 in
    destroy) [ "$1" = en6 ] && [ -n "$MOCK_STA_STUCK" ] && exit 1
             grep -vx "$1" "$MOCK_IFACES" > "$MOCK_IFACES.new"
             mv "$MOCK_IFACES.new" "$MOCK_IFACES"
             # The stack drops the routes through a destroyed interface.
             if [ "$1" = en6 ] && [ -n "${MOCK_ROUTES:-}" ]; then
                 grep -v ' en6$' "$MOCK_ROUTES" > "$MOCK_ROUTES.new"
                 mv "$MOCK_ROUTES.new" "$MOCK_ROUTES"
             fi;;
    inet) echo "address $*" >> "$MOCK_CALLS";;
    '') if [ "$1" = en6 ] && [ -n "$MOCK_NO_BROADCAST" ]; then
            echo "$1: flags=80000041<UP,RUNNING,SHIM> mtu 1500"
        else
            echo "$1: flags=80000843<UP,BROADCAST,RUNNING,SIMPLEX,SHIM> mtu 1500"
        fi
        echo "	status: active"
        [ "$1" = mam0 ] && echo "	inet 10.0.0.100 netmask 0xffffff00"
        [ -f "$MOCK_IFACES.$1" ] && cat "$MOCK_IFACES.$1";;
esac
exit 0
''')
        self.env['MOCK_NO_BROADCAST'] = ''
        self.ctl = base / 'mv8688-ctl'
        self.ctl.write_text('''#!/bin/sh
echo "ctl $*" >> "$MOCK_CALLS"
for arg; do case $arg in status|connect|disconnect) verb=$arg;; esac; done
[ -n "$MOCK_CTL_HOOK" ] && [ "$verb" = connect ] && eval "$MOCK_CTL_HOOK"
case $verb in
    connect) echo "request=1"
             [ "$MOCK_CONNECT_RC" = 0 ] && state=associated || state=idle;;
    disconnect) echo "request=2"; state=idle;;
    status) [ "$MOCK_CONNECT_RC" = 0 ] && state=associated || state=idle;;
esac
echo "state=$state requested=1 done=1 last_rc=0 last_step=-"
echo "fw_release=0x19260a26 connects=1 link_losses=$MOCK_LINK_LOSSES link_cause=0x00000000"
echo "rsn=${MOCK_RSN:-0} scans_done=1 results=1 key_sets=0 mic_ucast=0 mic_mcast=0"
[ "$verb" = connect ] && exit $MOCK_CONNECT_RC
exit 0
''')
        self.ctl.chmod(0o755)
        self.env['MOCK_CTL_HOOK'] = ''

    def driver_args(self, *extra):
        helper, station = self.images()
        return ['-D', str(self.sta_driver), '-K', str(self.ctl), '-P', self.host_probe(),
                '-H', helper, '-S', station, '-A', 'mmi-bench', '-I', '10.42.88.10',
                '-E', '10.42.88.1', *extra]

    def steps(self):
        out = []
        for line in self.recorded().splitlines():
            if line.startswith(('probe', 'mount ', 'ctl ', 'ping ', 'pfctl -R', 'dhcp ')) or \
                    line.startswith(('ifconfig uap0 destroy', 'ifconfig en6 destroy',
                                     'ifconfig en6 inet', 'ifconfig en6 up')):
                out.append(line)
        return out

    def test_the_station_driver_runs_between_the_detach_and_the_factory_reattach(self):
        result = self.run_script(*self.driver_args())
        self.assertEqual(result.returncode, 0, result.stdout)
        steps = self.steps()
        ctl = str(self.ctl)
        expected = [
            'probe -n', 'ifconfig uap0 destroy', 'probe -n', 'pfctl -R -f',
            'mount -T io-pkt -o lan=6,mac=00:1c:d7:00:00:01,helper=',
            'ifconfig en6 inet 10.42.88.10 netmask 255.255.255.0 up',
            f'ctl -i en6 -w 45 connect mmi-bench',
            'ping -c 3 -s 56 10.42.88.1', 'ping -c 3 -s 1472 10.42.88.1']
        for got, want in zip(steps, expected):
            self.assertTrue(got.startswith(want), (got, want))
        tail = steps[len(expected):]
        self.assertTrue(all(c.startswith('ctl -i en6 status') for c in tail[:7]), tail)
        rest = [c for c in tail if not c.startswith('ctl -i en6 status')]
        self.assertEqual([c.split(' -o ')[0].split(' /')[0] for c in rest],
                         ['ctl -i en6 -w 15 disconnect', 'ifconfig en6 destroy', 'probe -n',
                          'probe -c', 'pfctl -R -f', 'mount -T io-pkt'])
        self.assertIn('fwpath=/lib/dll,resetmask=0', rest[-1])
        self.assertIn(str(self.sta_driver), steps[4])
        report = self.report()
        self.assertIn('pf: en6 admits only echo requests from 10.42.88.1', report)
        self.assertIn('the link held for the whole of the hold', report)
        self.assertIn('RESULT: en6 associated with', report)
        self.assertIn('pf: the en6 rules are out', report)
        self.assertIn('verdict OK', report)
        self.assertEqual(self.pf.read_text(), self.PF_RULES)
        self.assertNotIn('en6', self.ifaces.read_text())

    def test_the_rules_go_in_before_pass_out_and_admit_only_the_peer(self):
        self.env['MOCK_CTL_HOOK'] = f'cp "$MOCK_PF" {self.bin.parent}/pf.during'
        self.run_script(*self.driver_args())
        during = (self.bin.parent / 'pf.during').read_text().splitlines()
        i = during.index('pass out all keep state')
        self.assertEqual(during[i - 2:i], [
            'pass in quick on en6 inet proto icmp from 10.42.88.1 to 10.42.88.10 '
            'icmp-type echoreq keep state label "mmi_sta_en6"',
            'block drop in quick on en6 all label "mmi_sta_en6"'])
        self.assertEqual(len(during), len(self.PF_RULES.splitlines()) + 2)

    def test_a_stop_request_ends_the_hold_early_with_every_end_of_run_step(self):
        # 7.2: the owner wanted a way to end a hold without the watchdog.
        self.env['MOCK_PING_HOOK'] = f': > {self.state}/stop'
        result = self.run_script(*self.driver_args('-W', '600'))
        report = self.report()
        self.assertEqual(result.returncode, 0, report)
        self.assertIn('hold: stop requested after 0s; leaving now.', report)
        self.assertNotIn('hold: 10s', report)
        self.assertIn('the link held for the whole of the hold', report)
        self.assertIn('ifconfig en6 destroy', self.recorded())
        self.assertIn('verdict OK', report)
        # A request left over from an earlier run does not end the next hold.
        self.env['MOCK_PING_HOOK'] = ''
        (self.state / 'stop').touch()
        self.ifaces.write_text('uap0\nmam0\nen5\n')
        self.run_script(*self.driver_args())
        self.assertIn('hold: 60s', self.report())

    def test_rules_that_will_not_load_keep_the_driver_out(self):
        result = self.run_script(*self.driver_args(), MOCK_PF_FAIL='1')
        self.assertNotIn('lan=6', self.recorded())
        report = self.report()
        self.assertIn('could not be loaded, so it was not mounted', report)
        self.assertIn('restored with', report)
        self.assertIn('verdict PARTIAL', report)
        self.assertEqual(result.returncode, 1)

    def test_a_failed_connect_still_leaves_destroys_and_restores(self):
        result = self.run_script(*self.driver_args(), MOCK_CONNECT_RC='2')
        steps = self.steps()
        self.assertFalse([c for c in steps if c.startswith('ping')])
        self.assertIn('ifconfig en6 destroy', steps)
        self.assertTrue(steps[-1].startswith('mount -T io-pkt -o irq=13'))
        self.assertIn('did not associate', self.report())
        self.assertNotIn('RESULT', self.report())
        self.assertEqual(result.returncode, 1)

    def test_unanswered_pings_or_a_lost_link_are_partial(self):
        for overrides, text in (({'MOCK_PING_RC': '1'}, 'was not answered'),
                                ({'MOCK_LINK_LOSSES': '1'}, 'the link did not hold')):
            with self.subTest(text=text):
                self.calls.unlink(missing_ok=True)
                self.ifaces.write_text('uap0\nmam0\nen5\n')
                result = self.run_script(*self.driver_args(), **overrides)
                self.assertIn(text, self.report())
                self.assertIn('verdict PARTIAL', self.report())
                self.assertIn('ifconfig en6 destroy', self.steps())
                self.assertEqual(result.returncode, 1)

    def test_a_driver_that_does_not_attach_is_reported_and_the_radio_restored(self):
        result = self.run_script(*self.driver_args(), MOCK_STA_NO_ATTACH='1')
        self.assertFalse([c for c in self.steps() if c.startswith('ctl')])
        self.assertIn('did not attach', self.report())
        self.assertIn('restored with', self.report())
        self.assertEqual(result.returncode, 1)

    def test_an_en6_that_will_not_go_withholds_the_factory_attach(self):
        result = self.run_script(*self.driver_args(), MOCK_STA_STUCK='1')
        mounts = [c for c in self.steps() if c.startswith('mount ')]
        self.assertEqual(len(mounts), 1)
        self.assertIn('lan=6', mounts[0])
        report = self.report()
        self.assertIn('still present after the destroy', report)
        self.assertIn('Recover with a restart', report)
        self.assertIn('the en6 rules stay while en6 exists', report)
        self.assertNotIn('probe -c', self.recorded())
        self.assertEqual(result.returncode, 1)

    def test_firmware_left_after_the_destroy_withholds_the_factory_attach(self):
        helper, station = self.images()
        args = self.driver_args()
        args[args.index('-P') + 1] = self.host_probe(check_rc=4)
        result = self.run_script(*args)
        mounts = [c for c in self.steps() if c.startswith('mount ')]
        self.assertEqual(len(mounts), 1)
        self.assertIn('firmware check after the destroy did not pass', self.report())
        self.assertIn('power the unit off fully', self.report())
        self.assertEqual(result.returncode, 1)

    def test_driver_mode_needs_all_of_its_options(self):
        full = self.driver_args()
        def without(flag):
            args = list(full)
            i = args.index(flag)
            del args[i:i + 2]
            return args
        for args in (without('-K'), without('-P'), without('-H'), without('-A'),
                     without('-I'), full + ['-C'], full + ['-W', '5'], full + ['-W', 'x'],
                     full + ['-F'], ['-K', str(self.ctl), '-P', self.host_probe()]):
            with self.subTest(args=args):
                result = self.run_script(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('usage', result.stderr)
        self.assertEqual(self.recorded(), '')

    def test_refuses_an_existing_en6_and_a_missing_driver_before_any_change(self):
        self.ifaces.write_text('uap0\nmam0\nen5\nen6\n')
        result = self.run_script(*self.driver_args())
        self.assertEqual(result.returncode, 1)
        self.assertIn('en6 already exists', result.stderr)
        self.ifaces.write_text('uap0\nmam0\nen5\n')
        self.sta_driver.unlink()
        result = self.run_script(*self.driver_args())
        self.assertEqual(result.returncode, 1)
        self.assertIn('station driver', result.stderr)
        self.assertNotIn('destroy', self.recorded())
        self.assertNotIn('pfctl -R', self.recorded())

    def test_driver_dry_run_reads_only(self):
        result = self.run_script(*self.driver_args('-n'))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual([c for c in self.steps()], ['probe -n'])
        self.assertIn('station driver', self.report())
        self.assertIn('uap0 factory MAC 00:1c:d7:00:00:01', self.report())

    def test_watchdog_destroys_en6_and_removes_the_rules_before_it_restores(self):
        wrapper = self.script.parent / 'wrapper.sh'
        wrapper.write_text(f'#!/bin/sh\nmkdir -p {self.state}\n'
                           f'echo $$ > {self.state}/alive\n'
                           f'exec dash {self.script} "$@"\n')
        wrapper.chmod(0o755)
        self.env['MOCK_CTL_HOOK'] = f'kill -9 "$(cat {self.state}/alive)"'
        subprocess.run(['dash', str(wrapper), *self.driver_args()], env=self.env,
                       capture_output=True, text=True, timeout=180)
        watchdog = (self.state / 'watchdog.log').read_text()
        self.assertIn('destroying en6 first', watchdog)
        self.assertIn('the en6 filter rules are out', watchdog)
        calls = self.recorded().splitlines()
        destroy = calls.index('ifconfig en6 destroy')
        check = calls.index('probe -c')
        mounts = [i for i, c in enumerate(calls) if c.startswith('mount ') and 'irq=13' in c]
        self.assertEqual(len(mounts), 1, calls)
        self.assertLess(destroy, check)
        self.assertLess(check, mounts[0])
        self.assertEqual(self.pf.read_text(), self.PF_RULES)
        self.assertNotIn('en6', self.ifaces.read_text())

    def test_watchdog_leaves_the_factory_driver_out_if_en6_will_not_go(self):
        wrapper = self.script.parent / 'wrapper.sh'
        wrapper.write_text(f'#!/bin/sh\nmkdir -p {self.state}\n'
                           f'echo $$ > {self.state}/alive\n'
                           f'exec dash {self.script} "$@"\n')
        wrapper.chmod(0o755)
        self.env.update(MOCK_CTL_HOOK=f'kill -9 "$(cat {self.state}/alive)"',
                        MOCK_STA_STUCK='1')
        subprocess.run(['dash', str(wrapper), *self.driver_args()], env=self.env,
                       capture_output=True, text=True, timeout=180)
        watchdog = (self.state / 'watchdog.log').read_text()
        self.assertIn('en6 is still present; not mounting', watchdog)
        self.assertFalse([c for c in self.recorded().splitlines()
                          if c.startswith('mount ') and 'irq=13' in c])


class StationDhcpMode(StationDriverMode):
    """-D with -d: the factory dhcp.client takes en6's address (phase 5.3)."""

    ROUTES = ('Routing tables\n\nInternet:\n'
              'Destination        Gateway            Flags    Refs      Use    Mtu  Interface\n'
              '10.0.0/24          link#3             UC          0        0      -  mam0\n'
              '10.0.0.255         link#3             UHLc        1        1      -  mam0\n'
              '127.0.0.1          127.0.0.1          UH         17     1123  33192  lo0\n'
              '192.168/16         192.168.16.1       UGS         0        0      -  en5\n'
              '192.168.1/24       link#5             UC          2        0      -  uap0\n'
              '192.168.16/20      link#6             UC          2        0      -  en5\n'
              '192.168.16.2       9e:d1:75:c7:29:d8  UHLc        1      873      -  en5\n')

    def setUp(self):
        super().setUp()
        base = self.bin.parent
        self.routes = base / 'routes'
        self.routes.write_text(self.ROUTES)
        self.pids = base / 'pids'
        self.resolv = base / 'resolv.conf'
        self.resolv.write_text('')
        (base / 'dhcp-up').write_text('#!/bin/sh\n')
        self.env.update(MOCK_ROUTES=str(self.routes), MOCK_PIDS=str(self.pids),
                        MOCK_RESOLV=str(self.resolv), MOCK_DHCP_RC='0',
                        MOCK_LEASE='10.42.88.57', MOCK_DHCP_ROUTE='', MOCK_DHCP_RESOLV='',
                        MOCK_DHCP_STUBBORN='', MOCK_DHCP_NOADDR='', MOCK_STOP_RESOLV='')
        self.addCleanup(self.kill_daemons)
        self.tool('netstat', '#!/bin/sh\ncat "$MOCK_ROUTES"\n')
        self.tool('getconf', '#!/bin/sh\necho "nameserver_192.168.16.1"\n')
        # pidin -f aA lists the factory's en5 client and each live daemon.
        self.tool('pidin', """#!/bin/sh
echo "pidin $*" >> "$MOCK_CALLS"
if [ "$1" = -f ]; then
    echo "     pid Arguments"
    echo "  417863 dhcp.client -b -i en5"
    [ -f "$MOCK_PIDS" ] || exit 0
    while read -r pid args; do
        kill -0 "$pid" 2>/dev/null && echo " $pid $args"
    done < "$MOCK_PIDS"
    exit 0
fi
grep -q uap0 "$MOCK_IFACES" && echo "    1  /lib/dll/devnp-mv8688uap.so"
echo "    2  /lib/dll/devnp-mlb.so"
""")
        # The client configures en6, applies what "dhcp-up" would, and stays
        # in the foreground, as the factory one does; on TERM it takes the
        # address away (or it ignores TERM).
        self.tool('dhcp.client', """#!/bin/sh
echo "dhcp $*" >> "$MOCK_CALLS"
[ "$MOCK_DHCP_RC" = 0 ] || exit "$MOCK_DHCP_RC"
printf '\\tinet 0.0.0.0 netmask 0xff000000 broadcast 255.255.255.255\\n' > "$MOCK_IFACES.en6"
[ -n "$MOCK_DHCP_NOADDR" ] || (/bin/sleep 0.05
    printf '\\tinet %s netmask 0xffffff00 broadcast 10.42.88.255\\n' "$MOCK_LEASE" > "$MOCK_IFACES.en6") &
[ -z "$MOCK_DHCP_ROUTE" ] || echo "$MOCK_DHCP_ROUTE" >> "$MOCK_ROUTES"
[ -z "$MOCK_DHCP_RESOLV" ] || echo "$MOCK_DHCP_RESOLV" >> "$MOCK_RESOLV"
[ -z "${MOCK_DHCP_HOOK:-}" ] || eval "$MOCK_DHCP_HOOK"
# MOCK_STOP_RESOLV: the stop leaves an empty resolv.conf, as dhcp-down did in 6.3.
if [ -n "$MOCK_DHCP_STUBBORN" ]; then on_term=''
elif [ -n "$MOCK_STOP_RESOLV" ]; then on_term=': > "$MOCK_RESOLV"; rm -f "$0"; exit 0'
# MOCK_STOP_ROUTE: the stop takes the lease's default route with it.
elif [ -n "${MOCK_STOP_ROUTE:-}" ]; then
    on_term='grep -v "^default" "$MOCK_ROUTES" > "$MOCK_ROUTES.n"; mv "$MOCK_ROUTES.n" "$MOCK_ROUTES"; rm -f "$0"; exit 0'
else on_term='rm -f "$0"; exit 0'; fi
echo "$$ $0 $*" >> "$MOCK_PIDS"
exec /bin/sh -c "trap '$on_term' TERM; while :; do /bin/sleep 0.05; done" "$MOCK_IFACES.en6"
""")

    def kill_daemons(self):
        if not self.pids.exists():
            return
        for line in self.pids.read_text().splitlines():
            try:
                os.kill(int(line.split()[0]), 9)
            except ProcessLookupError:
                pass

    def daemons_alive(self):
        alive = []
        for line in (self.pids.read_text().splitlines() if self.pids.exists() else []):
            pid = int(line.split()[0])
            try:
                os.kill(pid, 0)
                alive.append(pid)
            except ProcessLookupError:
                pass
        return alive

    def driver_args(self, *extra):
        args = super().driver_args(*extra)
        i = args.index('-I')
        args[i:i + 2] = ['-d']
        return args

    def test_the_lease_comes_after_the_join_and_its_client_stops_before_the_leave(self):
        result = self.run_script(*self.driver_args())
        self.assertEqual(result.returncode, 0, result.stdout)
        steps = self.steps()
        expected = [
            'probe -n', 'ifconfig uap0 destroy', 'probe -n', 'pfctl -R -f',
            'mount -T io-pkt -o lan=6,mac=00:1c:d7:00:00:01,helper=',
            'ifconfig en6 up', 'ctl -i en6 -w 45 connect mmi-bench',
            f'dhcp -d -b -T 30 -i en6',
            'ping -c 3 -s 56 10.42.88.1', 'ping -c 3 -s 1472 10.42.88.1']
        for got, want in zip(steps, expected):
            self.assertTrue(got.startswith(want), (got, want))
        self.assertFalse([c for c in steps if c.startswith('ifconfig en6 inet')])
        rest = [c for c in steps[len(expected):] if not c.startswith('ctl -i en6 status')]
        self.assertEqual([c.split(' -o ')[0].split(' /')[0] for c in rest],
                         ['ctl -i en6 -w 15 disconnect', 'ifconfig en6 destroy', 'probe -n',
                          'probe -c', 'pfctl -R -f', 'mount -T io-pkt'])
        report = self.report()
        self.assertIn('leased 10.42.88.57 on en6', report)
        self.assertIn('en6 flags=80000843<UP,BROADCAST', report)
        self.assertIn('dhcp: en6 holds 10.42.88.57 after', report)
        self.assertNotIn('0.0.0.0 after', report)
        self.assertIn('dhcp-procs-leased:', report)
        self.assertIn('hold: 60s associated; ping 10.42.88.57 from 10.42.88.1 now', report)
        self.assertIn('no default route came or went, and every mam0 route', report)
        self.assertIn('resolv.conf and _CS_RESOLVE are as before the lease', report)
        self.assertIn('en6 holds no address after the stop', report)
        self.assertIn('RESULT: en6 associated with', report)
        self.assertIn('leased 10.42.88.57 from 10.42.88.1', report)
        self.assertIn('verdict OK', report)
        # Only this run's client was stopped, never the factory's on en5.
        self.assertIn('stopping dhcp.client for en6, pid', report)
        self.assertNotIn('417863', report.split('stopping dhcp.client')[1].split('\n')[0])
        self.assertEqual(self.daemons_alive(), [])
        self.assertEqual(self.pf.read_text(), self.PF_RULES)

    def test_the_rules_also_admit_the_servers_replies(self):
        self.env['MOCK_CTL_HOOK'] = f'cp "$MOCK_PF" {self.bin.parent}/pf.during'
        self.run_script(*self.driver_args())
        during = (self.bin.parent / 'pf.during').read_text().splitlines()
        i = during.index('pass out all keep state')
        self.assertEqual(during[i - 3:i], [
            'pass in quick on en6 inet proto udp from 10.42.88.1 port 67 to any port 68 '
            'keep state label "mmi_sta_en6"',
            'pass in quick on en6 inet proto icmp from 10.42.88.1 to any '
            'icmp-type echoreq keep state label "mmi_sta_en6"',
            'block drop in quick on en6 all label "mmi_sta_en6"'])
        self.assertIn('admits only DHCP replies and echo requests', self.report())

    def test_a_lease_that_brings_a_default_route_is_partial(self):
        result = self.run_script(*self.driver_args(), MOCK_DHCP_ROUTE=(
            'default            10.42.88.1         UGS         0        0      -  en6'))
        report = self.report()
        self.assertIn("the lease added 'default 10.42.88.1 en6'", report)
        self.assertIn('changed a default route', report)
        self.assertIn('verdict PARTIAL', report)
        self.assertNotIn('RESULT', report)
        self.assertIn('ifconfig en6 destroy', self.steps())
        self.assertEqual(self.daemons_alive(), [])
        self.assertEqual(result.returncode, 1)

    def test_a_lost_mam0_route_or_a_resolver_change_is_partial(self):
        for overrides, text in (
                ({'MOCK_DHCP_RESOLV': 'nameserver 10.42.88.1'}, 'changed resolv.conf'),
                ({'MOCK_CTL_HOOK': f'grep -v mam0 {self.routes} > {self.routes}.n; '
                                   f'mv {self.routes}.n {self.routes}'},
                 "removed '10.0.0/24 link#3 mam0'")):
            with self.subTest(text=text):
                self.calls.unlink(missing_ok=True)
                self.ifaces.write_text('uap0\nmam0\nen5\n')
                self.routes.write_text(self.ROUTES)
                self.resolv.write_text('')
                result = self.run_script(*self.driver_args(), **overrides)
                self.assertIn(text, self.report())
                self.assertIn('verdict PARTIAL', self.report())
                self.assertEqual(result.returncode, 1)

    def test_a_resolv_conf_the_run_left_on_the_system_flash_is_removed(self):
        self.resolv.unlink()
        result = self.run_script(*self.driver_args(), MOCK_STOP_RESOLV='1')
        report = self.report()
        self.assertIn('resolver-baseline: resolv.conf: absent', report)
        self.assertIn('removed the %s the run left (dhcp-stopped' % self.resolv, report)
        self.assertFalse(self.resolv.exists())
        self.assertTrue((self.state / 'resolv.conf.dhcp-stopped').exists())
        self.assertIn('verdict OK', report)
        self.assertEqual(result.returncode, 0, report)

    def test_a_resolv_conf_present_before_the_run_is_kept(self):
        self.resolv.write_text('')
        self.run_script(*self.driver_args(), MOCK_STOP_RESOLV='1')
        self.assertTrue(self.resolv.exists())
        self.assertNotIn('removed the', self.report())

    def test_changes_to_cloned_and_arp_entries_are_not_route_changes(self):
        self.env['MOCK_CTL_HOOK'] = (
            f'grep -v "UHLc" {self.routes} > {self.routes}.n; mv {self.routes}.n {self.routes}; '
            f'echo "10.42.88.1   00:11:22:33:44:55  UHLc  0  0  -  en6" >> {self.routes}')
        result = self.run_script(*self.driver_args())
        self.assertNotIn('routes: the lease', self.report())
        self.assertEqual(result.returncode, 0, self.report())

    def test_no_lease_skips_the_pings_and_hold_and_still_leaves_and_restores(self):
        result = self.run_script(*self.driver_args(), MOCK_DHCP_NOADDR='1')
        steps = self.steps()
        self.assertFalse([c for c in steps if c.startswith('ping')])
        self.assertIn('ifconfig en6 destroy', steps)
        self.assertTrue(steps[-1].startswith('mount -T io-pkt -o irq=13'))
        report = self.report()
        self.assertIn('no address on en6 after 10s', report)
        self.assertIn('holds no address 40s after dhcp.client started', report)
        self.assertIn('no usable lease', report)
        # The daemon that never got an address is still stopped.
        self.assertIn('stopping dhcp.client for en6, pid', report)
        self.assertEqual(self.daemons_alive(), [])
        self.assertEqual(result.returncode, 1)

    def test_a_client_that_gives_up_is_partial(self):
        result = self.run_script(*self.driver_args(), MOCK_DHCP_RC='3')
        self.assertFalse([c for c in self.steps() if c.startswith('ping')])
        report = self.report()
        self.assertIn('dhcp.client exited (status 3; 3 is its own timeout) before en6', report)
        self.assertIn('no dhcp.client for en6 is running', report)
        self.assertEqual(result.returncode, 1)

    def test_an_en6_without_broadcast_skips_the_lease(self):
        # The first run on the unit, 2026-10-29: the 5.2 driver sent no
        # DL_ADVERT, so en6 had no BROADCAST and dhcp.client timed out.
        result = self.run_script(*self.driver_args(), MOCK_NO_BROADCAST='1')
        steps = self.steps()
        self.assertFalse([c for c in steps if c.startswith(('dhcp ', 'ping'))])
        self.assertIn('ifconfig en6 destroy', steps)
        report = self.report()
        self.assertIn('en6 flags=80000041<UP,RUNNING,SHIM>', report)
        self.assertIn('lacks BROADCAST', report)
        self.assertIn('no lease attempted', report)
        self.assertEqual(result.returncode, 1)

    def test_a_lease_off_the_peers_subnet_is_not_used(self):
        result = self.run_script(*self.driver_args(), MOCK_LEASE='10.42.89.57')
        self.assertFalse([c for c in self.steps() if c.startswith('ping')])
        self.assertIn("not on 10.42.88.1's /24", self.report())
        self.assertEqual(self.daemons_alive(), [])
        self.assertEqual(result.returncode, 1)

    def test_a_client_that_ignores_term_is_killed(self):
        result = self.run_script(*self.driver_args(), MOCK_DHCP_STUBBORN='1')
        report = self.report()
        self.assertIn('ignored TERM; sending KILL', report)
        self.assertIn('dhcp: stopped.', report)
        self.assertIn('en6 still holds 10.42.88.57 after the stop', report)
        self.assertEqual(self.daemons_alive(), [])
        self.assertEqual(result.returncode, 0, report)

    def test_refuses_unreviewed_dhcp_files_or_a_client_already_on_en6(self):
        for overrides, text in (({'MOCK_DHCP_CLIENT_SUM': '1 2'}, 'not the reviewed factory binary'),
                                ({'MOCK_DHCP_UP_SUM': '1 2'}, 'not the reviewed factory script')):
            with self.subTest(text=text):
                result = self.run_script(*self.driver_args(), **overrides)
                self.assertEqual(result.returncode, 1)
                self.assertIn(text, result.stderr)
        for marker, text in (('DLinkReplacesPPP', 'its LAN-mode branch (that is -l)'),
                             ('RSE', 'a branch this run was not reviewed for')):
            with self.subTest(marker=marker):
                (self.bin.parent / marker).write_text('')
                result = self.run_script(*self.driver_args())
                (self.bin.parent / marker).unlink()
                self.assertEqual(result.returncode, 1)
                self.assertIn(text, result.stderr)
        sleeper = subprocess.Popen(['/bin/sleep', '60'])
        self.addCleanup(sleeper.kill)
        self.pids.write_text(f'{sleeper.pid} /usr/sbin/dhcp.client -b -u -T 30 -i en6\n')
        result = self.run_script(*self.driver_args())
        self.assertEqual(result.returncode, 1)
        self.assertIn('already running', result.stderr)
        self.assertNotIn('destroy', self.recorded())
        self.assertNotIn('pfctl -R', self.recorded())

    def test_dhcp_needs_the_driver_mode_the_peer_and_no_static_address(self):
        full = self.driver_args()
        helper, station = self.images()
        for args in (full + ['-I', '10.42.88.10'],
                     [a for a in full if a not in ('-E', '10.42.88.1')],
                     ['-P', self.host_probe(), '-H', helper, '-S', station, '-d']):
            with self.subTest(args=args):
                result = self.run_script(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('usage', result.stderr)
        self.assertEqual(self.recorded(), '')

    def test_dhcp_dry_run_reads_only(self):
        result = self.run_script(*self.driver_args('-n'))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.steps(), ['probe -n'])
        report = self.report()
        self.assertIn(f'cksum {DHCP_CLIENT_SUM}', report)
        self.assertIn('ifwatchd_dhcp_down is absent', report)
        self.assertIn('resolver-baseline: _CS_RESOLVE:', report)
        self.assertIn('dhcp-preflight:   417863 dhcp.client -b -i en5', report)

    def test_watchdog_stops_the_client_before_it_destroys_en6(self):
        wrapper = self.script.parent / 'wrapper.sh'
        wrapper.write_text(f'#!/bin/sh\nmkdir -p {self.state}\n'
                           f'echo $$ > {self.state}/alive\n'
                           f'exec dash {self.script} "$@"\n')
        wrapper.chmod(0o755)
        self.env['MOCK_PING_HOOK'] = f'kill -9 "$(cat {self.state}/alive)"'
        subprocess.run(['dash', str(wrapper), *self.driver_args()], env=self.env,
                       capture_output=True, text=True, timeout=180)
        watchdog = (self.state / 'watchdog.log').read_text()
        self.assertIn('watchdog: dhcp: stopping dhcp.client for en6', watchdog)
        self.assertLess(watchdog.index('dhcp: stopped.'), watchdog.index('destroying en6 first'))
        self.assertEqual(self.daemons_alive(), [])
        self.assertNotIn('en6', self.ifaces.read_text())


# Run the interlock and sequence tests once, in RadioReattach only, and the
# static-address driver tests once, in StationDriverMode only.
for _name in dir(RadioReattach):
    if _name.startswith('test_') and _name not in StationDriverMode.__dict__:
        setattr(StationDriverMode, _name, None)
for _name in dir(StationDriverMode):
    if _name.startswith('test_') and _name not in StationDhcpMode.__dict__:
        setattr(StationDhcpMode, _name, None)



class StationLanMode(StationDhcpMode):
    """-D with -d and -l: the lease in the factory LAN mode (phase 7.2)."""

    NAT = ('nat on ppp0 from <natRangeTable> to any -> (ppp0) round-robin\n'
           'nat on en5 from <natRangeTable> to any -> (en5) round-robin\n'
           'rdr-anchor "dnsRedirect1" all\n'
           'rdr-anchor "dnsRedirect2" all\n'
           'rdr-anchor "dnsRedirect3" all\n'
           'rdr-anchor "dnsRedirect4" all\n')
    EN6_NAT = 'nat on en6 from <natRangeTable> to any -> (en6) round-robin'
    # en5's lease at home wrote these; its default route was deleted by hand.
    HOME_RESOLV = 'nameserver 192.168.16.1\nnameserver 192.168.16.1\n'
    LEASE_ROUTE = 'default            10.42.88.1         UG          1        0      -  en6'
    # What dhcp-up's LAN branch does with a lease naming 10.42.88.1.
    DHCP_UP = ('printf "nameserver 10.42.88.1\\nnameserver 10.42.88.1\\n" > "$MOCK_RESOLV"; '
               ': > "$MOCK_CS"; '
               'for a in 1 2 3 4; do echo "rdr on mam0 to 10.0.2.100 -> 10.42.88.1" '
               '> "$MOCK_ANCHORS/dnsRedirect$a"; done')

    def setUp(self):
        super().setUp()
        base = self.bin.parent
        self.flag = base / 'DLinkReplacesPPP'
        self.flag.write_text('')
        self.nat = base / 'nat.rules'
        self.nat.write_text(self.NAT)
        self.anchors = base / 'anchors'
        self.anchors.mkdir()
        for n in range(1, 5):
            (self.anchors / f'dnsRedirect{n}').write_text(
                'rdr on mam0 to 10.0.2.100 -> 192.168.16.1\n')
        self.resolv.write_text(self.HOME_RESOLV)
        self.cs = base / 'cs'
        self.cs.write_text('\n')
        self.env.update(MOCK_NAT=str(self.nat), MOCK_ANCHORS=str(self.anchors),
                        MOCK_CS=str(self.cs), MOCK_NAT_FAIL='', MOCK_NAT_FLUSHES='',
                        MOCK_DHCP_ROUTE=self.LEASE_ROUTE, MOCK_DHCP_HOOK=self.DHCP_UP)
        self.tool('getconf', '#!/bin/sh\ncat "$MOCK_CS"\n')
        self.tool('pfctl', """#!/bin/sh
echo "pfctl $*" >> "$MOCK_CALLS"
case "$1 $2" in
    "-sr ") cat "$MOCK_PF";;
    "-n -R") [ -f "$4" ];;
    "-R -f") [ -z "$MOCK_PF_FAIL" ] || exit 1
             cp "$3" "$MOCK_PF";;
    "-sn ") cat "$MOCK_NAT";;
    "-n -N") [ -f "$4" ];;
    "-N -f") [ -z "$MOCK_NAT_FAIL" ] || exit 1
             cp "$3" "$MOCK_NAT"
             # MOCK_NAT_FLUSHES: a load that empties the anchors, once.
             if [ -n "$MOCK_NAT_FLUSHES" ] && [ ! -e "$MOCK_ANCHORS.flushed" ]; then
                 for a in 1 2 3 4; do : > "${MOCK_ANCHORS:?}/dnsRedirect$a"; done
                 : > "$MOCK_ANCHORS.flushed"
             fi;;
    "-a "*) [ "$3" = -sn ] || exit 2
            cat "$MOCK_ANCHORS/$2" 2>/dev/null; exit 0;;
    "-t natRangeTable") echo "   10.0.0.101"; echo "   192.168.1.0/24";;
    *) exit 2;;
esac
""")

    def driver_args(self, *extra):
        return super().driver_args('-l', *extra)

    def test_the_lease_becomes_the_default_route_and_resolver_and_the_run_undoes_it(self):
        self.env['MOCK_PING_HOOK'] = f'cp "$MOCK_NAT" {self.bin.parent}/nat.during'
        result = self.run_script(*self.driver_args())
        report = self.report()
        self.assertEqual(result.returncode, 0, report)
        for text in ('is set (LAN mode)', 'no default route before the run',
                     "the lease added 'default 10.42.88.1 en6'",
                     "the lease's default route via 10.42.88.1 on en6 is the only one",
                     'resolv.conf names 10.42.88.1 10.42.88.1 and _CS_RESOLVE is cleared',
                     'anchors-baseline: rdr on mam0 to 10.0.2.100 -> 192.168.16.1',
                     'anchors-leased: rdr on mam0 to 10.0.2.100 -> 10.42.88.1',
                     'en6 translates <natRangeTable> as en5 does',
                     'hold: 60s associated; run netcheck',
                     "the lease's default route is still the only one",
                     'the en6 NAT rule is out',
                     'put back the %s from before the run (dhcp-stopped' % self.resolv,
                     'no default route came or went over the run',
                     'stays set; lan-mode.sh disable and a restart',
                     'RESULT: en6 associated', '(LAN mode)', 'verdict OK'):
            self.assertIn(text, report)
        self.assertNotIn('ping 10.42.88.57 from 10.42.88.1 now', report)
        # en6's rule follows en5's, and only while the station is up.
        during = (self.bin.parent / 'nat.during').read_text().splitlines()
        self.assertEqual(during[1:3], [self.NAT.splitlines()[1], self.EN6_NAT])
        self.assertEqual(self.nat.read_text(), self.NAT)
        self.assertEqual(self.pf.read_text(), self.PF_RULES)
        self.assertEqual(self.resolv.read_text(), self.HOME_RESOLV)
        self.assertEqual((self.state / 'resolv.conf.dhcp-stopped').read_text(),
                         'nameserver 10.42.88.1\nnameserver 10.42.88.1\n')
        self.assertEqual(self.flag.read_text(), '')
        calls = self.recorded().splitlines()
        self.assertLess(calls.index('dhcp -d -b -T 30 -i en6'),
                        [i for i, c in enumerate(calls) if c.startswith('pfctl -N -f')][0])
        self.assertEqual(self.daemons_alive(), [])

    def test_a_resolv_conf_absent_before_is_removed_and_a_lost_one_comes_back_mode_666(self):
        self.resolv.unlink()
        result = self.run_script(*self.driver_args())
        self.assertIn('removed the %s the run left' % self.resolv, self.report())
        self.assertFalse(self.resolv.exists())
        self.assertEqual(result.returncode, 0, self.report())
        # One there before and gone by the stop is re-created world-readable,
        # as dhcp-up creates it.
        self.resolv.write_text(self.HOME_RESOLV)
        self.resolv.chmod(0o644)
        self.calls.unlink()
        self.ifaces.write_text('uap0\nmam0\nen5\n')
        self.routes.write_text(self.ROUTES)
        self.env['MOCK_PING_HOOK'] = f'rm -f {self.resolv}'
        self.run_script(*self.driver_args())
        self.assertIn('put back the %s from before the run' % self.resolv, self.report())
        self.assertEqual(self.resolv.read_text(), self.HOME_RESOLV)
        self.assertEqual(self.resolv.stat().st_mode & 0o777, 0o666)

    def test_refuses_a_default_route_before_any_change(self):
        self.routes.write_text(self.ROUTES + (
            'default            192.168.16.1       UG          1        0      -  en5\n'))
        result = self.run_script(*self.driver_args())
        self.assertEqual(result.returncode, 1)
        self.assertIn('a default route exists (default 192.168.16.1 en5)', result.stderr)
        self.assertIn('route delete default', result.stderr)
        self.assertNotIn('destroy', self.recorded())

    def test_refuses_an_unreadable_routing_table(self):
        self.tool('netstat', '#!/bin/sh\nexit 1\n')
        result = self.run_script(*self.driver_args())
        self.assertEqual(result.returncode, 1)
        self.assertIn('routing table could not be read', result.stderr)
        self.assertNotIn('destroy', self.recorded())

    def test_the_flag_must_be_set_and_empty_and_only_lan_mode_accepts_it(self):
        for flag, args, text in (
                (None, self.driver_args(), '-l needs the empty flag'),
                ('x', self.driver_args(), '-l needs the empty flag'),
                ('', StationDhcpMode.driver_args(self), 'that is -l')):
            with self.subTest(text=text, flag=flag):
                self.flag.unlink(missing_ok=True)
                if flag is not None:
                    self.flag.write_text(flag)
                result = self.run_script(*args)
                self.assertEqual(result.returncode, 1)
                self.assertIn(text, result.stderr)
                self.assertNotIn('destroy', self.recorded())

    def test_lan_mode_needs_a_lease(self):
        result = self.run_script(*StationDriverMode.driver_args(self, '-l'))
        self.assertEqual(result.returncode, 2)
        self.assertIn('usage', result.stderr)

    def test_lan_dry_run_reads_only(self):
        result = self.run_script(*self.driver_args('-n'))
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.recorded()
        self.assertNotIn('destroy', calls)
        self.assertNotIn('pfctl -N', calls)
        self.assertNotIn('pfctl -R', calls)
        self.assertIn('anchors-baseline:', self.report())

    def test_a_lease_that_is_not_the_only_route_or_resolver_is_partial(self):
        for overrides, text in (
                ({'MOCK_DHCP_ROUTE': 'default            192.168.16.1       UG  1  0  -  en5'},
                 "the default route is not the lease's alone (default 192.168.16.1 en5)"),
                ({'MOCK_DHCP_HOOK': self.DHCP_UP.replace('10.42.88.1\\n', '134.60.1.111\\n')},
                 "resolv.conf names no server of the lease's (134.60.1.111 134.60.1.111)"),
                ({'MOCK_DHCP_HOOK': self.DHCP_UP.replace(': > "$MOCK_CS"; ', '')},
                 '_CS_RESOLVE still names a server')):
            with self.subTest(text=text):
                self.calls.unlink(missing_ok=True)
                self.ifaces.write_text('uap0\nmam0\nen5\n')
                self.routes.write_text(self.ROUTES)
                self.resolv.write_text(self.HOME_RESOLV)
                self.cs.write_text('nameserver_192.168.16.1\n')
                result = self.run_script(*self.driver_args(), **overrides)
                report = self.report()
                self.assertIn(text, report)
                self.assertIn('verdict PARTIAL', report)
                self.assertNotIn('RESULT', report)
                self.assertEqual(self.resolv.read_text(), self.HOME_RESOLV)
                self.assertEqual(result.returncode, 1)

    def test_a_nat_load_that_empties_the_anchors_is_undone_and_partial(self):
        result = self.run_script(*self.driver_args(), MOCK_NAT_FLUSHES='1')
        report = self.report()
        self.assertIn('NAT on en6 could not be added', report)
        self.assertIn('an anchor or the table changed', report)
        self.assertEqual(self.nat.read_text(), self.NAT)
        self.assertIn('verdict PARTIAL', report)
        self.assertEqual(result.returncode, 1)

    def test_a_default_route_lost_during_the_hold_is_partial(self):
        self.env['MOCK_PING_HOOK'] = (f'grep -v "^default" {self.routes} > {self.routes}.n; '
                                      f'mv {self.routes}.n {self.routes}')
        result = self.run_script(*self.driver_args())
        self.assertIn('the default route changed during the hold', self.report())
        self.assertEqual(result.returncode, 1)

    def test_a_stop_that_takes_the_default_route_is_expected(self):
        result = self.run_script(*self.driver_args(), MOCK_STOP_ROUTE='1')
        report = self.report()
        self.assertIn("stopping dhcp.client took only the lease's default route", report)
        self.assertIn('verdict OK', report)
        self.assertEqual(result.returncode, 0, report)

    def test_watchdog_removes_the_nat_rule(self):
        wrapper = self.script.parent / 'wrapper.sh'
        wrapper.write_text(f'#!/bin/sh\nmkdir -p {self.state}\n'
                           f'echo $$ > {self.state}/alive\n'
                           f'exec dash {self.script} "$@"\n')
        wrapper.chmod(0o755)
        self.env['MOCK_PING_HOOK'] = f'kill -9 "$(cat {self.state}/alive)"'
        subprocess.run(['dash', str(wrapper), *self.driver_args()], env=self.env,
                       capture_output=True, text=True, timeout=180)
        watchdog = (self.state / 'watchdog.log').read_text()
        self.assertIn('watchdog: the en6 NAT rule is out', watchdog)
        self.assertEqual(self.nat.read_text(), self.NAT)
        self.assertEqual(self.daemons_alive(), [])


for _name in dir(StationDhcpMode):
    if _name.startswith('test_') and _name not in StationLanMode.__dict__:
        setattr(StationLanMode, _name, None)


class StationWpaMode(StationDriverMode):
    """-D with -w and -c: wpa_supplicant joins instead of the control tool (phase 6)."""

    CONF = ('network={\n\tssid="mmi-bench"\n\tscan_ssid=1\n\tkey_mgmt=WPA-PSK\n'
            '\tpsk=00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff\n}\n')

    def setUp(self):
        super().setUp()
        base = self.bin.parent
        self.conf = base / 'wpa-bench.conf'
        self.conf.write_text(self.CONF)
        self.conf.chmod(0o600)
        self.wpa = base / 'wpa_supplicant'
        # Writes the connection to its -f log unless told not to, then runs
        # until TERM, as the real one does.
        self.wpa.write_text('''#!/bin/sh
echo "wpa $*" >> "$MOCK_CALLS"
log=
while [ $# -gt 0 ]; do [ "$1" = -f ] && log=$2; shift; done
echo "1.0: mv8688: en6 00:1c:d7:00:00:01" >> "$log"
echo "1.1: State: DISCONNECTED -> SCANNING" >> "$log"
if [ -z "$MOCK_WPA_NOCONNECT" ]; then
    echo "1.5: WPA: Key negotiation completed with 02:00:00:aa:bb:cc [PTK=${MOCK_PTK:-CCMP} GTK=CCMP]" >> "$log"
    echo "1.5: CTRL-EVENT-CONNECTED - Connection to 02:00:00:aa:bb:cc completed" >> "$log"
fi
[ -n "$MOCK_WPA_EXIT" ] && { echo "Failed to initialize driver interface"; exit 255; }
trap 'echo "wpa TERM" >> "$MOCK_CALLS"; echo "2.0: CTRL-EVENT-TERMINATING" >> "$log"; exit 0' TERM
while :; do /bin/sleep 0.05; done
''')
        self.wpa.chmod(0o755)
        self.env.update(MOCK_WPA_NOCONNECT='', MOCK_WPA_EXIT='', MOCK_RSN='1', MOCK_PTK='CCMP',
                        MOCK_LS_MODE='-rw-------',
                        MOCK_LS_OWNER='root', MOCK_NO_EGD='', MOCK_WPA_CONF=str(self.conf))
        self.tool('ls', '''#!/bin/sh
if [ "$1" = -l ] && [ "$2" = "$MOCK_WPA_CONF" ]; then
    echo "$MOCK_LS_MODE  1 $MOCK_LS_OWNER  root  123 Jan 01 00:00 $2"
    exit 0
fi
exec /bin/ls "$@"
''')
        self.tool('pidin', '''#!/bin/sh
echo "pidin $*" >> "$MOCK_CALLS"
grep -q uap0 "$MOCK_IFACES" && echo "    1  /lib/dll/devnp-mv8688uap.so"
echo "    2  /lib/dll/devnp-mlb.so"
[ -z "$MOCK_NO_EGD" ] && echo "  3  /mnt/persistence/openssh/jitter-egd 708"
exit 0
''')

    def wpa_args(self, *extra):
        return self.driver_args('-w', str(self.wpa), '-c', str(self.conf), *extra)

    def test_wpa_supplicant_joins_and_is_stopped_before_the_station_leaves(self):
        result = self.run_script(*self.wpa_args())
        self.assertEqual(result.returncode, 0, result.stdout)
        calls = self.recorded().splitlines()
        self.assertFalse([c for c in calls if c.startswith('ctl') and 'connect m' in c])
        start = [c for c in calls if c.startswith('wpa -')]
        self.assertEqual(len(start), 1)
        self.assertEqual(start[0], 'wpa -i en6 -D mv8688 -c %s -d -t -f %s/wpa.log'
                         % (self.conf, self.state))
        term = calls.index('wpa TERM')
        self.assertLess(calls.index('ping -c 3 -s 1472 10.42.88.1'), term)
        self.assertLess(term, calls.index('ctl -i en6 -w 15 disconnect'))
        self.assertLess(term, calls.index('ifconfig en6 destroy'))
        report = self.report()
        self.assertIn('wpa-start: 1.5: CTRL-EVENT-CONNECTED', report)
        self.assertIn('wpa-end: 2.0: CTRL-EVENT-TERMINATING', report)
        self.assertEqual(report.count('CTRL-EVENT-CONNECTED'), 1)
        self.assertIn('stopped on TERM', report)
        self.assertIn('RESULT: en6 joined', report)
        self.assertIn('verdict OK', report)
        # The key never reaches the report or the console.
        for text in (report, result.stdout, result.stderr):
            self.assertNotIn('00112233445566778899', text)

    def test_a_supplicant_that_does_not_connect_is_partial_and_still_stopped(self):
        result = self.run_script(*self.wpa_args(), MOCK_WPA_NOCONNECT='1')
        report = self.report()
        self.assertIn('wpa_supplicant did not connect', report)
        self.assertIn('did not associate', report)
        self.assertNotIn('RESULT', report)
        calls = self.recorded()
        self.assertNotIn('ping -c', calls)
        self.assertIn('wpa TERM', calls)
        self.assertIn('ifconfig en6 destroy', calls)
        self.assertEqual(result.returncode, 1)

    def test_a_connection_without_ccmp_or_an_rsn_join_is_not_success(self):
        for overrides in ({'MOCK_PTK': 'TKIP'}, {'MOCK_RSN': '0'}):
            with self.subTest(overrides=overrides):
                self.calls.unlink(missing_ok=True)
                self.ifaces.write_text('uap0\nmam0\nen5\n')
                result = self.run_script(*self.wpa_args(), **overrides)
                report = self.report()
                self.assertIn('not as WPA2-PSK with CCMP over an RSN join', report)
                self.assertNotIn('RESULT', report)
                self.assertNotIn('ping -c', self.recorded())
                self.assertEqual(result.returncode, 1)

    def test_a_supplicant_that_exits_is_reported(self):
        result = self.run_script(*self.wpa_args(), MOCK_WPA_EXIT='1', MOCK_WPA_NOCONNECT='1')
        self.assertIn('wpa_supplicant exited: Failed to initialize', self.report())
        self.assertEqual(result.returncode, 1)

    def test_the_configuration_must_be_roots_alone_and_name_the_network(self):
        for overrides, text in (({'MOCK_LS_MODE': '-rw-r--r--'}, 'mode 600'),
                                ({'MOCK_LS_OWNER': 'nobody'}, 'mode 600')):
            with self.subTest(overrides=overrides):
                result = self.run_script(*self.wpa_args(), **overrides)
                self.assertEqual(result.returncode, 1)
                self.assertIn(text, result.stderr)
        for conf in (self.CONF.replace('mmi-bench', 'other'),
                     self.CONF.replace('mmi-bench', 'mmi-bench-2'),
                     self.CONF + self.CONF.replace('mmi-bench', 'other'),
                     self.CONF.replace('WPA-PSK', 'NONE'),
                     self.CONF.replace('mmi-bench', 'mmiXbench')):
            with self.subTest(conf=conf):
                self.conf.write_text(conf)
                args = self.wpa_args()
                if 'mmiXbench' in conf:     # a dot in -A is not a pattern
                    args[args.index('-A') + 1] = 'mmi.bench'
                result = self.run_script(*args)
                self.assertEqual(result.returncode, 1)
                self.assertIn('must hold exactly one network', result.stderr)
                # The configuration is never echoed.
                self.assertNotIn('00112233445566778899', result.stderr + result.stdout)
        self.assertNotIn('destroy', self.recorded())

    def test_reconnect_mode_accepts_link_losses_if_associated_at_the_end(self):
        result = self.run_script(*self.wpa_args('-R'), MOCK_LINK_LOSSES='2')
        self.assertEqual(result.returncode, 0, result.stdout)
        report = self.report()
        self.assertIn('hold: connects=1 link_losses=2.', report)
        self.assertIn('associated again at the end of the hold', report)
        self.assertIn('verdict OK', report)
        # Without -R the same losses fail the hold.
        self.calls.unlink(missing_ok=True)
        self.ifaces.write_text('uap0\nmam0\nen5\n')
        result = self.run_script(*self.wpa_args(), MOCK_LINK_LOSSES='2')
        self.assertIn('the link did not hold', self.report())
        self.assertEqual(result.returncode, 1)

    def test_reconnect_mode_needs_the_supplicant(self):
        result = self.run_script(*self.driver_args('-R'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('usage', result.stderr)
        self.assertNotIn('destroy', self.recorded())

    def test_no_jitter_egd_refuses_before_any_change(self):
        result = self.run_script(*self.wpa_args(), MOCK_NO_EGD='1')
        self.assertEqual(result.returncode, 1)
        self.assertIn('jitter-egd is not running', result.stderr)
        self.assertNotIn('destroy', self.recorded())
        self.assertNotIn('pfctl -R', self.recorded())

    def test_the_supplicant_options_go_together_and_only_with_the_driver(self):
        for args in (self.driver_args('-w', str(self.wpa)),
                     self.driver_args('-c', str(self.conf)),
                     ['-P', self.host_probe(), '-w', str(self.wpa), '-c', str(self.conf)]):
            with self.subTest(args=args):
                result = self.run_script(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.recorded(), '')

    def test_watchdog_stops_the_supplicant_before_it_destroys_en6(self):
        wrapper = self.script.parent / 'wrapper.sh'
        wrapper.write_text(f'#!/bin/sh\nmkdir -p {self.state}\n'
                           f'echo $$ > {self.state}/alive\n'
                           f'exec dash {self.script} "$@"\n')
        wrapper.chmod(0o755)
        self.env['MOCK_PING_HOOK'] = f'kill -9 "$(cat {self.state}/alive)"'
        subprocess.run(['dash', str(wrapper), *self.wpa_args()], env=self.env,
                       capture_output=True, text=True, timeout=180)
        watchdog = (self.state / 'watchdog.log').read_text()
        self.assertIn('wpa: wpa_supplicant (pid', watchdog)
        calls = self.recorded().splitlines()
        self.assertLess(calls.index('wpa TERM'), calls.index('ifconfig en6 destroy'))
        self.assertEqual(len([c for c in calls if c.startswith('mount ') and 'irq=13' in c]), 1)


if __name__ == '__main__':
    unittest.main()
