"""Exercise the phase 7.1 LAN-mode flag script with fake QNX utilities.

The script runs under dash on the workstation with its paths moved into a
temporary directory and stand-ins for cksum, df, netstat, pfctl, getconf,
pidin and ifconfig. These tests show its refusals, ownership and rollback
decisions. They do not show that /bin/ksh on the unit behaves the same, nor
what the HMI or dhcp-up do with the flag.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DHCP_UP_SUM = '3046797907 6292'


class LanMode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = self.base = Path(self.tmp.name)
        self.bin = base / 'bin'
        self.hb = base / 'HBpersistence'
        self.persist = base / 'efs-persist'
        for d in (self.bin, self.hb, self.persist):
            d.mkdir()
        self.flag = self.hb / 'DLinkReplacesPPP'
        self.state = self.persist / 'lan-mode'
        self.resolv = base / 'resolv.conf'
        self.dhcp_up = base / 'dhcp-up'
        self.dhcp_up.write_text('factory dhcp-up\n')

        text = (ROOT / 'wifi-sta/lan-mode.sh').read_text()
        for original, replacement in (
                ('flag=/HBpersistence/DLinkReplacesPPP', f'flag={self.flag}'),
                ('flag_dir=/HBpersistence', f'flag_dir={self.hb}'),
                ('rse_marker=/mnt/efs-system/RSE', f'rse_marker={base}/RSE'),
                ('dhcp_up=/etc/dhcp/dhcp-up', f'dhcp_up={self.dhcp_up}'),
                ('resolv_conf=/etc/resolv.conf', f'resolv_conf={self.resolv}'),
                ('state=/mnt/efs-persist/lan-mode', f'state={self.state}'),
                ('PATH=/bin:/sbin:/usr/bin:/usr/sbin', f'PATH={self.bin}:/bin:/usr/bin')):
            self.assertIn(original, text)
            text = text.replace(original, replacement)
        self.script = base / 'lan-mode.sh'
        self.script.write_text(text)

        self.env = dict(os.environ, MOCK_FREE='5000')
        # Only the factory dhcp-up has a pinned sum; everything else is real.
        self.tool('cksum', '#!/bin/sh\ncase $1 in\n'
                           '    *dhcp-up) echo "${MOCK_DHCP_UP_SUM:-%s} $1";;\n'
                           '    *) exec /usr/bin/cksum "$@";;\nesac\n' % DHCP_UP_SUM)
        # As the unit's df answers for "/HBpersistence/", a symlink to
        # /mnt/efs-persist/: the filesystem's line, then an error line.
        self.tool('df', '#!/bin/sh\n[ "$2" = "%s/" ] || exit 1\n'
                        'echo "/dev/fs0p3 29440 4567 $MOCK_FREE 16%%  /mnt/efs-persist"\n'
                        'echo "$2      Block device required"\n' % self.hb)
        self.tool('netstat', '#!/bin/sh\necho "default 192.168.16.1 UGS 0 0 - en5"\n')
        self.tool('pfctl', '#!/bin/sh\necho "pfctl $*"\n')
        self.tool('getconf', '#!/bin/sh\necho ""\n')
        self.tool('pidin', '#!/bin/sh\n[ "$1" = info ] && { echo "Boot time"; exit 0; }\n'
                           'echo "dhcp.client -b -i en5"\necho "io-pkt-v4-hc"\n'
                           'echo "/sbin/ifwatchd -D /etc/dhcp/dhcp-down en5"\n')
        self.tool('ifconfig', '#!/bin/sh\necho "en5: flags=8843"\n')
        self.tool('sleep', '#!/bin/sh\nexec /bin/sleep 0.01\n')

    def tool(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o755)

    def run_script(self, *args, **env):
        return subprocess.run(['dash', str(self.script), *args], capture_output=True,
                              text=True, env=dict(self.env, **env), timeout=60)

    def test_enable_sets_an_empty_flag_and_records_ownership(self):
        r = self.run_script('enable')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(self.flag.is_file())
        self.assertEqual(self.flag.stat().st_size, 0)
        self.assertTrue((self.state / 'owner').is_file())
        self.assertEqual((self.state / 'resolv.state').read_text(), 'absent\n')
        self.assertIn('Restart the unit', r.stdout)
        again = self.run_script('enable')
        self.assertEqual(again.returncode, 0)
        self.assertIn('already enabled by this script', again.stdout)

    def test_a_flag_set_by_someone_else_is_left_alone(self):
        self.flag.write_text('')
        for command in ('enable', 'disable'):
            r = self.run_script(command)
            self.assertEqual(r.returncode, 2, command)
            self.assertIn('was not set by this script', r.stdout)
            self.assertTrue(self.flag.exists())
        self.assertIn('NOT set by this script', self.run_script('status').stdout)

    def test_enable_refusals_change_nothing(self):
        cases = [
            ({'MOCK_DHCP_UP_SUM': '1 2'}, None, 'not the reviewed copy'),
            ({'MOCK_FREE': '10'}, None, 'only 10 KB free'),
            ({'MOCK_FREE': 'x'}, None, 'cannot read the free space'),
            ({}, 'rse', 'RSE branch'),
        ]
        for env, marker, message in cases:
            rse = self.base / 'RSE'
            if marker:
                rse.write_text('')
            r = self.run_script('enable', **env)
            rse.unlink(missing_ok=True)
            self.assertEqual(r.returncode, 2, message)
            self.assertIn(message, r.stdout)
            self.assertFalse(self.flag.exists(), message)
            self.assertFalse((self.state / 'owner').exists(), message)

    def test_disable_removes_the_flag_and_a_resolv_conf_that_was_absent(self):
        self.assertEqual(self.run_script('enable').returncode, 0)
        self.resolv.write_text('nameserver 192.168.16.1\n')
        r = self.run_script('disable')
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse(self.flag.exists())
        self.assertFalse(self.resolv.exists())
        self.assertFalse((self.state / 'owner').exists())
        kept = self.state / 'resolv.lan-mode.1'
        self.assertEqual(kept.read_text(), 'nameserver 192.168.16.1\n')
        # A second round keeps the first copy.
        self.assertEqual(self.run_script('enable').returncode, 0)
        self.resolv.write_text('nameserver 192.168.16.4\n')
        self.assertEqual(self.run_script('disable').returncode, 0)
        self.assertEqual(kept.read_text(), 'nameserver 192.168.16.1\n')
        self.assertEqual((self.state / 'resolv.lan-mode.2').read_text(),
                         'nameserver 192.168.16.4\n')
        self.assertIn('not enabled', self.run_script('disable').stdout)

    def test_disable_puts_an_earlier_resolv_conf_back(self):
        self.resolv.write_text('nameserver 10.0.2.100\n')
        self.assertEqual(self.run_script('enable').returncode, 0)
        self.assertEqual((self.state / 'resolv.state').read_text(), 'present\n')
        self.resolv.write_text('nameserver 192.168.16.1\nnameserver 192.168.16.1\n')
        r = self.run_script('disable')
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(self.resolv.read_text(), 'nameserver 10.0.2.100\n')
        self.assertIn('put the earlier', r.stdout)

    def test_snapshot_records_every_read(self):
        out = self.base / 'snap'
        r = self.run_script('snapshot', str(out))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn('complete', r.stdout)
        for name in ('summary.txt', 'routes.txt', 'nat.txt', 'cs-resolve.txt', 'ifconfig.txt',
                     'pidin-info.txt', 'dhcp-procs.txt',
                     *('anchor-dnsRedirect%d.txt' % n for n in range(1, 5))):
            self.assertTrue((out / name).is_file(), name)
        self.assertFalse((out / 'pidin-args.txt').exists())
        self.assertEqual((out / 'dhcp-procs.txt').read_text().splitlines(),
                         ['dhcp.client -b -i en5', '/sbin/ifwatchd -D /etc/dhcp/dhcp-down en5'])
        self.assertEqual((out / 'anchor-dnsRedirect3.txt').read_text(),
                         'pfctl -a dnsRedirect3 -sn\n')
        summary = (out / 'summary.txt').read_text()
        self.assertIn('flag: absent', summary)
        self.assertIn('resolv.conf: absent', summary)
        self.assertIn('dhcp-up: ' + DHCP_UP_SUM, summary)

    def test_a_hanging_read_makes_the_snapshot_partial(self):
        self.tool('pfctl', '#!/bin/sh\nexec /bin/sleep 30\n')
        self.tool('sleep', '#!/bin/sh\nexec /bin/sleep 0.001\n')
        r = self.run_script('snapshot', str(self.base / 'snap'))
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn('PARTIAL', r.stdout)
        self.assertIn('TIMEOUT', (self.base / 'snap' / 'nat.txt').read_text())

    def test_usage(self):
        for args in ([], ['snapshot'], ['enable', 'x'], ['reboot']):
            self.assertEqual(self.run_script(*args).returncode, 1, args)


if __name__ == '__main__':
    unittest.main()
