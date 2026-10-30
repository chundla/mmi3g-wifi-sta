"""Argument checks of the Pi-side phase 5 bench access point script.

Only what runs before the root check is exercised: the script starts
hostapd, dnsmasq and tcpdump and takes a wireless interface, none of which
a test should do. Whether it brings up a network a station can join is
shown only on the Pi.
"""
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'wifi-sta' / 'bench-open-ap.sh'


def run(*args):
    return subprocess.run(['bash', str(SCRIPT), *args], capture_output=True, text=True,
                          timeout=30)


class BenchOpenAp(unittest.TestCase):
    def test_the_script_parses(self):
        self.assertEqual(subprocess.run(['bash', '-n', str(SCRIPT)]).returncode, 0)

    def test_bad_arguments_are_refused_before_anything_runs(self):
        cases = [(), ('restart',), ('start', 'extra'), ('start', '-c', '12'),
                 ('start', '-c', '0'), ('start', '-c', '6a'), ('start', '-s', ''),
                 ('start', '-s', 'two words'), ('start', '-s', 'x' * 33),
                 ('start', '-s', 'semi;colon'), ('start', '-i', 'wlan0;id'),
                 ('start', '-x'), ('start', '-g', '600'), ('start', '-p', '120'),
                 ('start', '-w', '-g', '29'), ('start', '-w', '-p', '86401'),
                 ('start', '-w', '-g', '1e3'), ('start', '-w', '-p', '')]
        for args in cases:
            result = run(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn('usage:', result.stderr, args)

    @unittest.skipIf(os.geteuid() == 0, 'the root check passes when run as root')
    def test_valid_arguments_still_need_root(self):
        for args in (('start', '-i', 'wlan1', '-c', '11', '-s', 'mmi-bench_2'),
                     ('start', '-w'), ('start', '-w', '-g', '60', '-p', '120'),
                     ('stop', '-o', '/tmp/x'), ('status',)):
            result = run(*args)
            self.assertEqual(result.returncode, 1, args)
            self.assertIn('run as root', result.stderr)


    def test_the_bench_psk_is_never_printed_or_kept_with_the_logs(self):
        text = SCRIPT.read_text()
        # Generated into the private state directory, mode 600.
        self.assertIn('od -An -tx1 -N32 /dev/urandom', text)
        self.assertIn('chmod 600 "$state/psk"', text)
        # The saved configuration has the PSK line replaced; nothing echoes it.
        self.assertIn("sed 's/^wpa_psk=.*/wpa_psk=<not kept>/'", text)
        self.assertNotIn('"$state"/hostapd.conf "$dest"', text)
        for line in text.splitlines():
            if 'psk' in line and line.lstrip().startswith('echo'):
                self.assertNotIn('cat', line.replace('echo "wpa_psk=$(cat "$state/psk")"', ''),
                                 line)


if __name__ == '__main__':
    unittest.main()
