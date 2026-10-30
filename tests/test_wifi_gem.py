"""Phases 7.4a to 7.6: the Wi-Fi client's engineering (GEM) screen, its
buttons, the station files on the HDD and the start at start-up.

The definition is checked against the model of the factory `.esd` grammar,
and gem.sh, gem-install.sh, sta-install.sh, sta-boot.sh and the per-3
rollback run under
dash on the workstation with their paths moved into a temporary directory
and stand-ins for the QNX utilities. They show the definition's parse, the
scripts' decisions and output, and the install and removal. They do not show
that the GEM on the unit renders the screen, what its runner's user and
environment are, how the unit's ls dates a FIFO, or that /bin/ksh on the unit
behaves the same.
"""
from pathlib import Path
import getpass
import os
import signal
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'gem'))

import esd_grammar

ESD = ROOT / 'wifi-sta/gem/WifiClient.esd'
GEM = ROOT / 'wifi-sta/gem/gem.sh'
INSTALL = ROOT / 'wifi-sta/gem-install.sh'
STA_INSTALL = ROOT / 'wifi-sta/sta-install.sh'
STA_MODE = ROOT / 'wifi-sta/sta-mode.sh'
STA_BOOT = ROOT / 'wifi-sta/sta-boot.sh'
BOOT_LAUNCH = ROOT / 'wifi-sta/boot-launch.sh'
BUILD = ROOT / 'work/build'
# A saved copy of the unit's factory efs-system/engdefs, if you have one.
SAVED_ENGDEFS = Path(os.environ.get('MMI_SAVED_ENGDEFS', '/nonexistent'))
GEM_DIR = '/mnt/efs-persist/wifi-sta/gem'
# The first published build, whose hook was a line in the OpenSSH start-up.
FIRST_BUILD = '23b4255'


def cksum(path):
    out = subprocess.run(['cksum', str(path)], capture_output=True, text=True,
                         check=True).stdout.split()
    return f'{out[0]} {out[1]}'


def pinned(name):
    for line in INSTALL.read_text().splitlines():
        if line.startswith(name + "='"):
            return line.split("'")[1]
    raise AssertionError(name)


def code_lines(path):
    return '\n'.join(line.split('#', 1)[0] for line in path.read_text().splitlines()
                     if not line.lstrip().startswith('#'))


class Definition(unittest.TestCase):
    def test_parses_as_one_screen_under_main_with_five_script_buttons(self):
        screens, by_name, main = esd_grammar.parse(ESD.read_text())
        self.assertIsNone(main)
        self.assertEqual([(s.name, s.parent) for s in screens], [('wificlient', 'main')])
        got = [(e.model_type, e.data_source, e.namespace, e.keys, e.function_call, e.label)
               for e in screens[0].elements]
        self.assertEqual(got, [
            (esd_grammar.MODELTYPE_SCRIPT, 1, 1, [0x100], f'{GEM_DIR}/status', 'Station status'),
            (esd_grammar.MODELTYPE_SCRIPT, 1, 1, [0x100], f'{GEM_DIR}/client',
             'Start-up: join the hotspot'),
            (esd_grammar.MODELTYPE_SCRIPT, 1, 1, [0x100], f'{GEM_DIR}/ap',
             'Start-up: car access point (restart to apply)'),
            (esd_grammar.MODELTYPE_SCRIPT, 1, 1, [0x100], f'{GEM_DIR}/start',
             'Start station now (access point off until restart)'),
            (esd_grammar.MODELTYPE_SCRIPT, 1, 1, [0x100], f'{GEM_DIR}/check', 'Runner check')])

    def test_uses_the_factory_multiline_block_layout(self):
        # Upstream reports inline elements as rendering blank; each keyword
        # stands alone on its line, as in the factory Scripts.esd.
        for line in ESD.read_text().splitlines():
            tokens = esd_grammar.tokenize_line(line)
            if tokens and tokens[0] == 'script':
                self.assertEqual(tokens, ['script'])
        self.assertTrue(ESD.read_text().isascii())

    def test_the_buttons_name_the_links_the_installer_makes(self):
        text = INSTALL.read_text()
        self.assertIn(f'top=$persist/wifi-sta\ndir=$top/gem\n', text)
        self.assertIn("persist=/mnt/efs-persist\n", text)
        self.assertIn("links='status client ap start check'\n", text)
        self.assertIn(f'dir={GEM_DIR}\n', GEM.read_text())

    def test_the_installer_pins_these_files(self):
        self.assertEqual(pinned('esd_sum'), cksum(ESD))
        self.assertEqual(pinned('gem_sum'), cksum(GEM))
        # The 7.4a and 7.4b builds that ran on the unit, which remove takes
        # out.
        self.assertEqual(pinned('esd_older'), '2799610818 405,3567683713 529')
        self.assertEqual(pinned('gem_older'), '1675214043 5649,3629712146 10439')

    def test_the_start_up_marker_is_where_sta_boot_looks(self):
        self.assertIn('marker=$dir/client-at-start-up\n', GEM.read_text())
        self.assertIn('marker=$dir/client-at-start-up\n', INSTALL.read_text())
        self.assertIn(f'gem={GEM_DIR}\nmarker=$gem/client-at-start-up\n', STA_BOOT.read_text())
        self.assertIn(f'marker={GEM_DIR}/client-at-start-up\n', STA_INSTALL.read_text())
        self.assertIn('kill_switch=$gem/start.disabled\n', STA_BOOT.read_text())

    def test_script_element_refusals(self):
        def parse(body):
            return esd_grammar.parse('screen\tX Main\n   script\n' + body)
        with self.assertRaisesRegex(esd_grammar.EsdError, 'value'):
            parse('\tvalue\tsys 1 0x0100\n\tlabel\t"x"\n')
        with self.assertRaisesRegex(esd_grammar.EsdError, 'data source'):
            parse('\tvalue\tfoo 1 0x0100 "/x"\n\tlabel\t"x"\n')
        with self.assertRaisesRegex(esd_grammar.EsdError, 'BUTTON'):
            parse('\tvalue\tsys 1 0x0100 "/x"\n')
        screens, _, _ = parse('\tvalue\tsys 1 0x0100 "/x"\n\tlabel\t"x"\n\tpoll\t500\n')
        self.assertEqual(screens[0].elements[0].refresh_interval, 500)


class SavedFactorySet(unittest.TestCase):
    def setUp(self):
        if not SAVED_ENGDEFS.is_dir():
            self.skipTest('the saved factory engdefs directory is not present')

    def screen_names(self):
        names = {}
        for path in sorted(SAVED_ENGDEFS.glob('*.esd')):
            for line in path.read_text(encoding='latin-1').splitlines():
                tokens = esd_grammar.tokenize_line(line)
                if tokens and tokens[0].lower() == 'screen':
                    names.setdefault(tokens[1].lower(), []).append(path.name)
        return names

    def test_the_screen_name_and_file_name_are_new(self):
        self.assertNotIn('wificlient', self.screen_names())
        self.assertFalse((SAVED_ENGDEFS / ESD.name).exists())
        self.assertIn('main', self.screen_names())

    def test_the_factory_script_screens_parse(self):
        for name in ('Scripts.esd', 'OnlineTraffic.esd'):
            screens, _, _ = esd_grammar.parse(
                (SAVED_ENGDEFS / name).read_text(encoding='latin-1'))
            calls = [e.function_call for e in screens[0].elements]
            self.assertTrue(calls and all(c.startswith('/scripts/') for c in calls), name)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        self.calls = self.base / 'calls'
        self.tool('uname', '#!/bin/sh\necho QNX\n')
        self.tool('sleep', '#!/bin/sh\nexec /bin/sleep 0.01\n')

    def tool(self, name, text):
        path = self.bin / name
        path.write_text(text)
        path.chmod(0o755)

    def patched(self, source, pairs, name):
        text = source.read_text()
        for original, replacement in pairs:
            self.assertIn(original, text)
            text = text.replace(original, replacement)
        path = self.base / name
        path.write_text(text)
        path.chmod(0o755)
        return path


class GemScript(Base):
    def setUp(self):
        super().setUp()
        self.dir = self.base / 'gem'
        self.dir.mkdir()
        self.run_dir = self.base / 'run'
        self.run_dir.mkdir()
        self.sta = self.base / 'sta-mode.sh'
        self.home = self.base / 'home'
        self.fifo = self.base / 'script.fifo'
        self.log = self.dir / 'gem.log'
        self.script = self.patched(GEM, (
            ('dir=/mnt/efs-persist/wifi-sta/gem', f'dir={self.dir}'),
            ('run=/fs/tmpfs', f'run={self.run_dir}'),
            ('fifo=/scripts/script.fifo', f'fifo={self.fifo}'),
            ('sta_home=/mnt/persistence/wifi-sta', f'sta_home={self.home}'),
            ('sta_candidates="$sta_home/sta-mode.sh /fs/tmpfs/sta-mode.sh"',
             f'sta_candidates="{self.base}/absent.sh {self.sta}"'),
            ('/bin/ksh "$sta" status', 'dash "$sta" status'),
            ('/bin/ksh "$sta" start', 'dash "$sta" start'),
            ('start_limit=420', 'start_limit=${MOCK_LIMIT:-420}'),
            ('PATH=/bin:/sbin:/usr/bin:/usr/sbin', f'PATH={self.bin}:/bin:/usr/bin')),
            'gem/gem.sh')
        for action in ('status', 'client', 'ap', 'start', 'check'):
            (self.dir / action).symlink_to('gem.sh')
        self.tool('ifconfig', '#!/bin/sh\n[ "$1" = uap0 ]\n')
        self.tool('pidin', '''#!/bin/sh
echo "pidin $*" >> "$MOCK_CALLS"
echo "     pid tid name               prio STATE       Blocked"
case $* in
    *fds*) echo "  $2   1 ksh 10r REPLY 1"; echo "   0 rw /dev/null";;
    *family*) echo "  $2 217130 0 0";;
    *) echo "  $2   1 bin/ksh            10r REPLY 1";;
esac
''')

    def run_action(self, action, preload=True, **extra):
        # The unit's boot environment preloads libmc.so; the workstation's
        # ld.so complains about it on stderr before the script starts.
        env = {'PATH': '/usr/bin:/bin', 'MOCK_CALLS': str(self.calls), **extra}
        if preload:
            env['LD_PRELOAD'] = '/usr/lib/libmc.so'
        return subprocess.run(['dash', str(self.dir / action)], capture_output=True,
                              text=True, timeout=60, env=env)

    def test_status_without_sta_mode_reports_the_interfaces(self):
        out = self.run_action('status')
        self.assertEqual(out.returncode, 0)
        self.assertIn('sta-mode.sh is not on this unit', out.stdout)
        self.assertIn('uap0 present, en6 absent', out.stdout)
        self.assertIn('sta-mode.sh is not on this unit', self.log.read_text())

    def test_status_runs_sta_mode_status_without_the_preload(self):
        self.sta.write_text('echo "sta: preload=${LD_PRELOAD-unset} $1"\n'
                            'echo "sta: status PARTIAL: no lease;"\nexit 3\n')
        out = self.run_action('status')
        self.assertEqual(out.returncode, 0)
        lines = out.stdout.splitlines()
        self.assertEqual(lines[0], 'start-up mode: car access point')
        self.assertEqual(lines[1], str(self.sta))
        self.assertIn('preload=unset status', lines)
        self.assertEqual(lines[-2:], ['status PARTIAL: no lease;',
                                      'exit 3 (0 OK or not started, 2 FAIL, 3 PARTIAL)'])
        self.assertIn('sta: status PARTIAL', self.log.read_text())
        self.assertEqual(list(self.run_dir.iterdir()), [])

    def test_a_status_that_hangs_is_cut_off(self):
        self.sta.write_text('while :; do /bin/sleep 1; done\n')
        out = self.run_action('status')
        self.assertIn('TIMEOUT after 90s', out.stdout)
        self.assertIn('exit 1', out.stdout.splitlines()[-1])

    def test_check_reports_the_runner_and_the_console(self):
        out = self.run_action('check')
        self.assertEqual(out.returncode, 0)
        text = out.stdout
        for want in ('LD_PRELOAD /usr/lib/libmc.so', 'PATH /usr/bin:/bin',
                     'parent name bin/ksh', 'open files: 3 lines', 'fifo absent',
                     'A: now; B follows in 3 s', 'B: 3 s after A'):
            self.assertIn(want, text)
        self.assertRegex(text, r'user \S+, group \S+')
        self.assertIn('-' * 4 + '+' + '-' * 4 + '8', text)
        self.assertEqual(text.splitlines()[-1], 'check done')
        log = self.log.read_text()
        self.assertIn('family', log)
        self.assertIn('export:', log)
        self.assertEqual([p.name for p in self.dir.iterdir() if p.name.startswith('.')], [])

    def test_stderr_reaches_the_console(self):
        # The GEM redirects only stdout to its FIFO.
        self.sta.write_text('echo "to stderr" >&2\n')
        out = self.run_action('status', preload=False)
        self.assertIn('to stderr', out.stdout)
        self.assertEqual(out.stderr, '')

    # The start button (7.4b).
    def install_station(self, sta_text='echo "sta: $1"\n'):
        self.home.mkdir()
        sta = self.home / 'sta-mode.sh'
        sta.write_text(sta_text)
        driver = self.home / 'devn-mv8688sta.so'
        driver.write_bytes(b'driver')
        (self.home / 'SUMS').write_text(
            ''.join(f'{cksum(f)} {f.name}\n' for f in (driver, sta)))
        os.mkfifo(self.fifo)

    def age_fifo(self, minutes):
        os.utime(self.fifo, (os.path.getmtime(self.fifo) - minutes * 60,) * 2)

    def test_start_runs_sta_mode_start_and_shows_its_lines(self):
        self.install_station(
            'echo "sta: $1 preload=${LD_PRELOAD-unset}"\n'
            'read -r line && echo "sta: stdin not empty"\n'
            'echo "00:05:01 sta: verdict OK"\nexit 0\n')
        out = self.run_action('start')
        self.assertEqual(out.returncode, 0, out.stdout)
        lines = out.stdout.splitlines()
        self.assertIn('files match SUMS; sta-mode.sh start follows', lines[0])
        self.assertIn('If it starts', lines[1])
        self.assertEqual(lines[2:], ['start preload=unset', '00:05:01 verdict OK',
                                     'exit 0 (0 OK, 2 refused or FAIL, 3 PARTIAL)'])
        log = self.log.read_text()
        self.assertIn('press ', log)
        self.assertIn('verdict OK', log)
        self.assertEqual(list(self.run_dir.iterdir()), [])

    def test_start_reports_a_refusal_and_its_exit(self):
        self.install_station('echo "sta: a default route exists" >&2\nexit 2\n')
        out = self.run_action('start')
        self.assertEqual(out.stdout.splitlines()[-2:], [
            'a default route exists', 'exit 2 (0 OK, 2 refused or FAIL, 3 PARTIAL)'])

    def test_start_refuses_a_press_that_was_held_up(self):
        # The console lost the factory race and someone opened the FIFO
        # later: the press is old, so nothing is started.
        self.install_station('echo ran > "$0.ran"\n')
        self.age_fifo(10)
        out = self.run_action('start')
        self.assertIn('not started: this press is not recent', out.stdout)
        self.assertFalse((self.home / 'sta-mode.sh.ran').exists())
        self.assertEqual(list(self.run_dir.iterdir()), [])

    def test_start_refuses_a_press_from_another_day_and_without_a_fifo(self):
        self.install_station('echo ran > "$0.ran"\n')
        self.age_fifo(24 * 60)
        self.assertIn('not recent', self.run_action('start').stdout)
        self.fifo.unlink()
        self.fifo.write_text('')
        self.assertIn('not recent', self.run_action('start').stdout)
        self.assertFalse((self.home / 'sta-mode.sh.ran').exists())

    def test_a_press_a_minute_old_is_fresh(self):
        import time
        now = time.localtime()
        if now.tm_hour == 0 and now.tm_min < 3:
            self.skipTest('a minute ago was another day')
        self.install_station()
        self.age_fifo(1)
        self.assertIn('exit 0', self.run_action('start').stdout)

    def test_start_honours_the_kill_switch(self):
        self.install_station('echo ran > "$0.ran"\n')
        (self.dir / 'start.disabled').write_text('')
        out = self.run_action('start')
        self.assertIn('not started: the start is disabled', out.stdout)
        self.assertFalse((self.home / 'sta-mode.sh.ran').exists())

    def test_start_refuses_files_that_do_not_match_sums(self):
        self.install_station('echo ran > "$0.ran"\n')
        (self.home / 'devn-mv8688sta.so').write_bytes(b'changed')
        out = self.run_action('start')
        self.assertIn('devn-mv8688sta.so does not match its sum', out.stdout)
        self.assertIn('not started', out.stdout)
        self.assertFalse((self.home / 'sta-mode.sh.ran').exists())
        (self.home / 'SUMS').unlink()
        self.assertIn('the station files are not installed', self.run_action('start').stdout)

    def test_a_start_that_outlives_the_limit_does_not_hold_the_console(self):
        pid_file = self.base / 'sta.pid'
        self.install_station(f'echo $$ > {pid_file}\necho "sta: started"\n'
                             'exec /bin/sleep 30\n')

        def stop():
            if pid_file.exists():
                try:
                    os.kill(int(pid_file.read_text()), signal.SIGTERM)
                except ProcessLookupError:
                    pass
        self.addCleanup(stop)
        # The test's pipes stand in for the FIFO: run() returns only once
        # every writer has closed them.
        out = self.run_action('start', MOCK_LIMIT='5')
        self.assertIn('started', out.stdout)
        self.assertIn('still running after 5s', out.stdout.splitlines()[-1])
        os.kill(int(pid_file.read_text()), 0)

    # The start-up mode (7.6).
    def test_client_and_ap_set_the_start_up_mode(self):
        os.mkfifo(self.fifo)
        marker = self.dir / 'client-at-start-up'
        out = self.run_action('client')
        self.assertTrue(marker.exists())
        self.assertIn('start-up mode: hotspot client', out.stdout)
        self.assertIn('From the next start-up, the unit joins the hotspot.', out.stdout)
        self.assertIn('start-up mode: hotspot client', self.run_action('status').stdout)
        out = self.run_action('ap')
        self.assertFalse(marker.exists())
        self.assertIn('start-up mode: car access point', out.stdout)
        self.assertIn('Restart the unit', out.stdout)
        self.assertEqual([p.name for p in self.dir.iterdir() if '.new.' in p.name], [])

    def test_client_warns_of_the_kill_switch_and_refuses_a_held_up_press(self):
        os.mkfifo(self.fifo)
        (self.dir / 'start.disabled').write_text('')
        self.assertIn('but the start is disabled', self.run_action('client').stdout)
        (self.dir / 'client-at-start-up').unlink()
        self.age_fifo(10)
        out = self.run_action('client')
        self.assertIn('not changed: this press is not recent', out.stdout)
        self.assertFalse((self.dir / 'client-at-start-up').exists())

    def test_status_shows_the_last_start_up(self):
        self.home.mkdir()
        (self.home / 'boot.log').write_text(
            '00:01:00 boot: start-up, unit clock 00:01:00 (uptime)\n'
            '00:01:00 boot: mode: car access point (no marker); nothing to do.\n'
            '00:01:30 boot: start-up, unit clock 00:01:30 (uptime)\n'
            '00:02:30 boot: starting the station (sta-mode.sh start).\n'
            '    sta: verdict OK\n'
            '00:03:10 boot: sta-mode.sh start exit 0 (0 OK, 2 refused or FAIL, 3 PARTIAL).\n')
        lines = self.run_action('status').stdout.splitlines()
        self.assertEqual(lines[1:4], [
            '00:01:30 boot: start-up, unit clock 00:01:30 (uptime)',
            '00:02:30 boot: starting the station (sta-mode.sh start).',
            '00:03:10 boot: sta-mode.sh start exit 0 (0 OK, 2 refused or FAIL, 3 PARTIAL).'])

    def test_another_name_says_so(self):
        out = subprocess.run(['dash', str(self.script)], capture_output=True, text=True,
                             env={'PATH': '/usr/bin:/bin'})
        self.assertIn('unknown action gem.sh', out.stdout)

    def test_the_log_keeps_one_older_generation(self):
        self.log.write_text('x' * 40000)
        self.run_action('status')
        self.assertEqual(len((self.dir / 'gem.log.old').read_text()), 40000)
        self.assertLess(len(self.log.read_text()), 1000)


class Installer(Base):
    def setUp(self):
        super().setUp()
        b = self.base
        self.efs = b / 'efs-system'
        self.engdefs = self.efs / 'engdefs'
        self.engdefs.mkdir(parents=True)
        (self.engdefs / 'Main.esd').write_text('screen\tMain Main\n')
        (self.engdefs / 'WLAN.esd').write_text('screen   WLAN Main\n')
        self.persist = b / 'efs-persist'
        self.persist.mkdir()
        self.link = self.persist / 'engdefs'
        self.link.symlink_to(self.engdefs)
        self.run_dir = b / 'run'
        self.run_dir.mkdir()
        self.stage = b / 'stage'
        self.stage.mkdir()
        (self.stage / ESD.name).write_bytes(ESD.read_bytes())
        (self.stage / 'gem.sh').write_bytes(GEM.read_bytes())
        self.dir = self.persist / 'wifi-sta' / 'gem'
        self.esd = self.engdefs / ESD.name
        self.tool('df', '#!/bin/sh\necho "Filesystem 1K-blocks Used Available Use% Mounted"\n'
                        'echo "/dev/fs0p1 30000 28000 ${MOCK_FREE:-2000} 95% $1"\n')
        self.script = self.patched(INSTALL, (
            ('efs=/mnt/efs-system', f'efs={self.efs}'),
            ('persist=/mnt/efs-persist', f'persist={self.persist}'),
            ('gem_dir_link=/HBpersistence/engdefs', f'gem_dir_link={self.link}'),
            ('run=/fs/tmpfs', f'run={self.run_dir}'),
            # The workstation has no /bin/ksh for the shebang.
            ('ran=$("$dir/gem.sh" 2>&1)', 'ran=$(dash "$dir/gem.sh" 2>&1)'),
            ('PATH=/bin:/sbin:/usr/bin:/usr/sbin', f'PATH={self.bin}:/bin:/usr/bin')),
            'gem-install.sh')

    def run_script(self, *args, **env):
        return subprocess.run(['dash', str(self.script), *args], capture_output=True,
                              text=True, timeout=60,
                              env={'PATH': '/usr/bin:/bin', **env})

    def test_install_status_and_remove(self):
        out = self.run_script('install', str(self.stage))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn('3 definitions (was 2)', out.stdout)
        self.assertIn('gem.sh runs directly', out.stdout)
        self.assertIn('Restart the unit', out.stdout)
        self.assertEqual(self.esd.read_bytes(), ESD.read_bytes())
        self.assertEqual((self.dir / 'gem.sh').read_bytes(), GEM.read_bytes())
        for link in ('status', 'client', 'ap', 'start', 'check'):
            self.assertEqual(str((self.dir / link).readlink()), 'gem.sh')
        self.assertEqual(self.esd.stat().st_mode & 0o777, 0o644)
        self.assertEqual((self.dir / 'gem.sh').stat().st_mode & 0o777, 0o755)
        self.assertFalse((self.run_dir / 'wifi-gem.lock').exists())

        again = self.run_script('install', str(self.stage))
        self.assertEqual(again.returncode, 0)
        self.assertIn('already installed', again.stdout)

        status = self.run_script('status')
        self.assertIn('state: installed', status.stdout)
        self.assertIn(f'-> {self.engdefs}, 3 definitions', status.stdout)

        (self.dir / 'gem.log').write_text('evidence\n')
        out = self.run_script('remove')
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertFalse(self.esd.exists())
        self.assertFalse((self.persist / 'wifi-sta').exists())
        self.assertEqual((self.run_dir / 'wifi-gem.gem.log').read_text(), 'evidence\n')
        self.assertEqual(sorted(p.name for p in self.engdefs.iterdir()), ['Main.esd', 'WLAN.esd'])
        self.assertIn('nothing is installed', self.run_script('remove').stdout)

    def test_an_older_build_is_removed_before_the_install(self):
        # As the 7.4a build sits on the unit: its own definition and gem.sh,
        # and links for status and check only.
        old_esd = self.base / 'old.esd'
        old_esd.write_text('screen\tWifiClient Main\n')
        old_gem = self.base / 'old.sh'
        old_gem.write_text('echo old\n')
        text = self.script.read_text()
        for name, path in (('esd_older', old_esd), ('gem_older', old_gem)):
            text = text.replace(f"{name}='{pinned(name)}'", f"{name}='1 2,{cksum(path)}'")
        self.script.write_text(text)
        self.dir.mkdir(parents=True)
        self.esd.write_bytes(old_esd.read_bytes())
        (self.dir / 'gem.sh').write_bytes(old_gem.read_bytes())
        for link in ('status', 'check'):
            (self.dir / link).symlink_to('gem.sh')
        (self.dir / 'client-at-start-up').write_text('')
        self.assertIn('state: older', self.run_script('status').stdout)
        out = self.run_script('install', str(self.stage))
        self.assertEqual(out.returncode, 2)
        self.assertIn('an older build is installed; run remove first', out.stdout)
        self.assertEqual(self.esd.read_bytes(), old_esd.read_bytes())
        out = self.run_script('remove')
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn('removed (was older)', out.stdout)
        self.assertFalse((self.dir / 'client-at-start-up').exists())
        self.assertFalse(self.esd.exists())
        out = self.run_script('install', str(self.stage))
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(self.esd.read_bytes(), ESD.read_bytes())

    def test_refuses_a_stage_that_is_not_this_build(self):
        (self.stage / 'gem.sh').write_text('echo other\n')
        out = self.run_script('install', str(self.stage))
        self.assertEqual(out.returncode, 2)
        self.assertIn('gem.sh is not this build', out.stdout)
        self.assertFalse(self.esd.exists())

    def test_refuses_when_the_gem_reads_another_directory(self):
        self.link.unlink()
        self.link.mkdir()
        out = self.run_script('install', str(self.stage))
        self.assertEqual(out.returncode, 2)
        self.assertIn('does not lead to', out.stdout)
        self.assertFalse(self.esd.exists())
        self.assertFalse((self.persist / 'wifi-sta').exists())

    def test_refuses_a_screen_name_already_in_use(self):
        (self.engdefs / 'Other.esd').write_text('screen WifiClient Main\n')
        out = self.run_script('install', str(self.stage))
        self.assertEqual(out.returncode, 2)
        self.assertIn('already names WifiClient', out.stdout)
        self.assertFalse(self.esd.exists())

    def test_refuses_too_little_space(self):
        out = self.run_script('install', str(self.stage), MOCK_FREE='10')
        self.assertEqual(out.returncode, 2)
        self.assertIn('needs 64', out.stdout)

    def test_a_failure_midway_is_undone(self):
        self.tool('ln', '#!/bin/sh\nexit 1\n')
        out = self.run_script('install', str(self.stage))
        self.assertEqual(out.returncode, 2)
        self.assertIn('undoing the partial install', out.stdout)
        self.assertFalse(self.esd.exists())
        self.assertFalse((self.dir / 'gem.sh').exists())
        self.assertEqual([p.name for p in self.dir.iterdir()], ['install.log'])

    def test_a_gem_sh_that_does_not_run_is_undone(self):
        self.script.write_text(self.script.read_text().replace(
            'ran=$(dash "$dir/gem.sh" 2>&1)', 'ran=$(false 2>&1)'))
        out = self.run_script('install', str(self.stage))
        self.assertEqual(out.returncode, 2)
        self.assertIn('does not run directly', out.stdout)
        self.assertFalse(self.esd.exists())
        self.assertFalse((self.dir / 'gem.sh').exists())

    def test_remove_leaves_a_definition_that_is_not_ours(self):
        self.assertEqual(self.run_script('install', str(self.stage)).returncode, 0)
        self.esd.write_text('screen\tWifiClient Main\n')
        out = self.run_script('remove')
        self.assertEqual(out.returncode, 2)
        self.assertIn('left in place', out.stdout)
        self.assertTrue(self.esd.exists())
        self.assertFalse((self.dir / 'gem.sh').exists())

    def test_usage(self):
        for args in ([], ['install'], ['status', 'x'], ['purge']):
            self.assertEqual(self.run_script(*args).returncode, 1, args)


class StationInstaller(Base):
    """sta-install.sh with stand-ins for the station binaries.

    The binaries are local builds, not in the repository, so the pinned list
    is replaced by one for stand-in files; sta-mode.sh and sta-boot.sh stay
    the real ones.
    """
    NAMES = ('devn-mv8688sta.so', 'mv8688-ctl', 'mv8688-probe', 'sd8688_helper.bin',
             'sd8688.bin', 'wpa_supplicant', 'jitter-egd')
    SCRIPTS = ('sta-mode.sh', 'sta-boot.sh', 'boot-launch.sh')
    # The factory lsd.sh's first lines, after the OpenSSH installation's hook.
    LSD = ('#!/bin/ksh\n# BEGIN MMI OPENSSH\n'
           'if [ -r /mnt/efs-persist/openssh-start.sh ]; then\n'
           "    (trap '' 1; exec /bin/ksh /mnt/efs-persist/openssh-start.sh > "
           '/dev/shmem/mmi-openssh-boot.log 2>&1) &\nfi\n# END MMI OPENSSH\n'
           '(sleep 120 && route add -net 10.1 10.0.96.1) &\n'
           'mount -uw /mnt/efs-persist\n')
    BLOCK = ('# BEGIN MMI WIFI-STA\n'
             'if [ -r /mnt/efs-persist/wifi-sta-boot.sh ]; then\n'
             "    (trap '' 1; exec /bin/ksh /mnt/efs-persist/wifi-sta-boot.sh > "
             '/dev/shmem/wifi-sta-boot.log 2>&1) &\nfi\n# END MMI WIFI-STA\n')
    CONF = ('network={\n\tssid="hotspot-ssid"\n\tkey_mgmt=WPA-PSK\n'
            '\tpsk="secret-passphrase"\n}\n')

    def setUp(self):
        super().setUp()
        b = self.base
        self.persistence = b / 'persistence'
        self.persistence.mkdir()
        openssh = self.persistence / 'openssh'
        openssh.mkdir()
        (openssh / 'start.sh').write_text('')
        self.sshd = b / 'sshd'
        self.sshd.write_text('')
        self.sshd.chmod(0o755)
        self.run_dir = b / 'run'
        self.run_dir.mkdir()
        self.stage = b / 'stage'
        self.stage.mkdir()
        lines = []
        for n in self.NAMES:
            (self.stage / n).write_bytes(f'stand-in {n}\n'.encode())
            mode = '600' if n.endswith('.bin') else '700'
            lines.append(f'{n} {mode} {cksum(self.stage / n)}')
        for n, source in zip(self.SCRIPTS, (STA_MODE, STA_BOOT, BOOT_LAUNCH)):
            (self.stage / n).write_bytes(source.read_bytes())
            lines.append(f'{n} 700 {cksum(source)}')
        lines[-1] += "'"
        self.openssh_start = openssh / 'start.sh'
        self.openssh_start.write_text('#!/bin/ksh\necho "OpenSSH ready"\n')
        self.openssh_start.chmod(0o700)
        self.efs_persist = b / 'efs-persist'
        (self.efs_persist / 'wifi-sta' / 'gem').mkdir(parents=True)
        self.marker = self.efs_persist / 'wifi-sta/gem/client-at-start-up'
        self.rollback = self.efs_persist / 'wifi-sta-rollback.sh'
        self.launcher = self.efs_persist / 'wifi-sta-boot.sh'
        self.lsd_pre = self.efs_persist / 'wifi-sta-lsd.sh.pre'
        self.efs_system = b / 'efs-system'
        (self.efs_system / 'lsd').mkdir(parents=True)
        self.lsd = self.efs_system / 'lsd/lsd.sh'
        self.lsd.write_text(self.LSD)
        self.lsd.chmod(0o755)
        self.top = self.persistence / 'wifi-sta'
        self.tool('mount', '#!/bin/sh\necho "mount $*" >> "$0.calls"\n')
        self.tool('df', '#!/bin/sh\necho "Filesystem 1K-blocks Used Available Use% Mounted"\n'
                        'echo "/dev/hd0t77 300000 100000 ${MOCK_FREE:-160000} 40% $1"\n')
        text = STA_INSTALL.read_text()
        start = text.index("files='") + len("files='")
        end = text.index("'", start) + 1
        self.script = self.patched(STA_INSTALL, (
            (text[start:end], '\n'.join(lines)),
            ('persistence=/mnt/persistence', f'persistence={self.persistence}'),
            ('sshd=/mnt/ssh/sbin/sshd', f'sshd={self.sshd}'),
            ('sta_lock=/fs/tmpfs/mmi-sta/lock', f'sta_lock={self.run_dir}/mmi-sta/lock'),
            ('run=/fs/tmpfs', f'run={self.run_dir}'),
            ('conf_owner=root', f'conf_owner={getpass.getuser()}'),
            ('/bin/ksh -n', 'dash -n'),
            ('/bin/ksh "$rollback"', 'dash "$rollback"'),
            ('rollback=/mnt/efs-persist/wifi-sta-rollback.sh', f'rollback={self.rollback}'),
            ('efs_system=/mnt/efs-system', f'efs_system={self.efs_system}'),
            ('efs_persist=/mnt/efs-persist', f'efs_persist={self.efs_persist}'),
            ('marker=/mnt/efs-persist/wifi-sta/gem/client-at-start-up', f'marker={self.marker}'),
            ('PATH=/bin:/sbin:/usr/bin:/usr/sbin', f'PATH={self.bin}:/bin:/usr/bin')),
            'sta-install.sh')

    def run_script(self, *args, stdin=None, **env):
        return subprocess.run(['dash', str(self.script), *args], capture_output=True,
                              text=True, timeout=60, input=stdin,
                              env={'PATH': '/usr/bin:/bin', **env})

    def test_install_conf_status_and_remove(self):
        out = self.run_script('install', str(self.stage))
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn('Next: sta-install.sh conf', out.stdout)
        self.assertEqual(self.top.stat().st_mode & 0o777, 0o700)
        for n in self.NAMES + self.SCRIPTS:
            self.assertEqual((self.top / n).read_bytes(), (self.stage / n).read_bytes())
            want = 0o600 if n.endswith('.bin') else 0o700
            self.assertEqual((self.top / n).stat().st_mode & 0o777, want, n)
        sums = (self.top / 'SUMS').read_text().splitlines()
        self.assertEqual([line.split()[2] for line in sums], list(self.NAMES + self.SCRIPTS))
        self.assertEqual(sums[-3], f'{cksum(STA_MODE)} sta-mode.sh')
        self.assertFalse((self.run_dir / 'wifi-sta-install.lock').exists())
        self.assertIn('already installed', self.run_script('install', str(self.stage)).stdout)

        out = self.run_script('conf', stdin=self.CONF)
        self.assertEqual(out.returncode, 0, out.stdout)
        conf = self.top / 'wpa-hotspot.conf'
        self.assertEqual(conf.read_text(), self.CONF)
        self.assertEqual(conf.stat().st_mode & 0o777, 0o600)

        status = self.run_script('status')
        self.assertIn('state: installed', status.stdout)
        self.assertIn('wpa-hotspot.conf: present', status.stdout)
        self.assertIn('station mode: not running', status.stdout)
        for text in (out.stdout, status.stdout):
            self.assertNotIn('secret', text)
            self.assertNotIn('hotspot-ssid', text)

        out = self.run_script('remove')
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertFalse(self.top.exists())
        self.assertTrue((self.persistence / 'openssh').is_dir())
        self.assertIn('nothing is installed', self.run_script('remove').stdout)

    def test_the_sums_are_what_the_start_button_checks(self):
        self.assertEqual(self.run_script('install', str(self.stage)).returncode, 0)
        gem = GemScript('test_another_name_says_so')
        gem.setUp()
        self.addCleanup(gem.doCleanups)
        gem.script.write_text(gem.script.read_text().replace(
            f'sta_home={gem.home}', f'sta_home={self.top}').replace(
            'dash "$sta" start', 'echo "sta: would start $sta"'))
        os.mkfifo(gem.fifo)
        out = gem.run_action('start')
        self.assertIn('files match SUMS', out.stdout)
        self.assertIn(f'would start {self.top}/sta-mode.sh', out.stdout)
        (self.top / 'sd8688.bin').write_bytes(b'flipped')
        self.assertIn('sd8688.bin does not match its sum', gem.run_action('start').stdout)

    def test_refuses_a_stage_that_is_not_this_build(self):
        (self.stage / 'sd8688.bin').write_bytes(b'other')
        out = self.run_script('install', str(self.stage))
        self.assertEqual(out.returncode, 2)
        self.assertIn('sd8688.bin is not this build', out.stdout)
        self.assertFalse(self.top.exists())

    def test_refuses_while_station_mode_runs(self):
        (self.run_dir / 'mmi-sta' / 'lock').mkdir(parents=True)
        for args, stdin in ((('install', str(self.stage)), None), (('conf',), self.CONF),
                            (('remove',), None)):
            out = self.run_script(*args, stdin=stdin)
            self.assertEqual(out.returncode, 2, args)
            self.assertIn('station mode is running', out.stdout)
        self.assertFalse(self.top.exists())

    def test_installs_without_the_openssh_installation(self):
        self.sshd.unlink()
        out = self.run_script('install', str(self.stage))
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn('the station will have no SSH', out.stdout)
        self.assertIn('OpenSSH: absent', self.run_script('status').stdout)

    def test_refuses_too_little_space(self):
        out = self.run_script('install', str(self.stage), MOCK_FREE='100')
        self.assertIn('needs 2048', out.stdout)
        self.assertFalse(self.top.exists())

    def test_conf_refuses_what_sta_mode_would_refuse(self):
        self.assertEqual(self.run_script('install', str(self.stage)).returncode, 0)
        conf = self.top / 'wpa-hotspot.conf'
        for bad in ('', self.CONF + self.CONF, self.CONF.replace('WPA-PSK', 'NONE'),
                    self.CONF.replace('\tpsk="secret-passphrase"\n', ''), 'x' * 5000):
            out = self.run_script('conf', stdin=bad)
            self.assertEqual(out.returncode, 2, bad[:40])
            self.assertIn('nothing was changed', out.stdout)
            self.assertNotIn('secret', out.stdout)
            self.assertFalse(conf.exists())
        self.assertEqual([p.name for p in self.top.iterdir() if '.new.' in p.name], [])
        # A second conf replaces the first (a changed passphrase).
        self.assertEqual(self.run_script('conf', stdin=self.CONF).returncode, 0)
        other = self.CONF.replace('secret-passphrase', 'another-passphrase')
        self.assertEqual(self.run_script('conf', stdin=other).returncode, 0)
        self.assertEqual(conf.read_text(), other)

    def test_a_failure_midway_is_undone(self):
        self.script.write_text(self.script.read_text().replace(
            'dash -n "$top/sta-mode.sh" 2>/dev/null', 'false'))
        out = self.run_script('install', str(self.stage))
        self.assertEqual(out.returncode, 2)
        self.assertIn('undoing the partial install', out.stdout)
        self.assertEqual(list(self.top.iterdir()), [])
        self.assertIn('state: absent', self.run_script('status').stdout)

    def test_remove_leaves_a_file_that_is_not_ours(self):
        self.assertEqual(self.run_script('install', str(self.stage)).returncode, 0)
        (self.top / 'mv8688-ctl').write_bytes(b'changed')
        self.assertIn('state: other', self.run_script('status').stdout)
        out = self.run_script('remove')
        self.assertEqual(out.returncode, 2)
        self.assertIn('left in place', out.stdout)
        self.assertTrue((self.top / 'mv8688-ctl').exists())
        self.assertFalse((self.top / 'SUMS').exists())

    def test_install_completes_an_older_set(self):
        # As on the unit after 7.5: every file but sta-boot.sh, and a SUMS
        # without it.
        self.assertEqual(self.run_script('install', str(self.stage)).returncode, 0)
        (self.top / 'sta-boot.sh').unlink()
        self.assertIn('state: partial', self.run_script('status').stdout)
        self.assertEqual(self.run_script('conf', stdin=self.CONF).returncode, 0)
        out = self.run_script('install', str(self.stage))
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn('(partial)', out.stdout)
        self.assertIn('wpa-hotspot.conf is already there', out.stdout)
        self.assertIn('state: installed', self.run_script('status').stdout)

    def rollback_alone(self):
        return subprocess.run(['dash', str(self.rollback)], capture_output=True, text=True,
                              timeout=60, env={'PATH': f'{self.bin}:/usr/bin:/bin'})

    def test_hook_and_unhook(self):
        original = self.lsd.read_bytes()
        self.assertEqual(self.run_script('hook').returncode, 2)  # nothing installed
        self.assertEqual(self.lsd.read_bytes(), original)
        self.assertEqual(self.run_script('install', str(self.stage)).returncode, 0)
        out = self.run_script('hook')
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn('Nothing starts until', out.stdout)
        self.assertIn('(after line 6)', out.stdout)
        # After the OpenSSH block, the factory lines untouched.
        lines = self.LSD.splitlines(keepends=True)
        self.assertEqual(self.lsd.read_text(), ''.join(lines[:6]) + self.BLOCK + ''.join(lines[6:]))
        self.assertEqual(self.lsd.stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.lsd_pre.read_bytes(), original)
        self.assertEqual(self.launcher.read_bytes(), BOOT_LAUNCH.read_bytes())
        self.assertEqual(self.launcher.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.rollback.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.openssh_start.read_text(), '#!/bin/ksh\necho "OpenSSH ready"\n')
        self.assertIn('already hooked', self.run_script('hook').stdout)
        self.assertEqual(self.lsd.read_text().count('BEGIN MMI WIFI-STA'), 1)
        status = self.run_script('status').stdout
        self.assertIn('start-up block: in', status)
        self.assertIn('start-up mode: car access point', status)

        out = self.run_script('remove')
        self.assertEqual(out.returncode, 2)
        self.assertIn('run unhook first', out.stdout)
        self.marker.write_text('')
        out = self.run_script('unhook')
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn('is as before the hook', out.stdout)
        self.assertEqual(self.lsd.read_bytes(), original)
        self.assertEqual(self.lsd.stat().st_mode & 0o777, 0o755)
        self.assertFalse(self.marker.exists())
        self.assertFalse(self.launcher.exists())
        self.assertEqual(self.run_script('remove').returncode, 0)
        self.assertFalse(self.rollback.exists())
        self.assertFalse(self.lsd_pre.exists())
        self.assertFalse(self.top.exists())

    def test_without_the_openssh_block_the_block_follows_the_shebang(self):
        self.lsd.write_text('#!/bin/ksh\nVMOPTIONS="$VMOPTIONS -DshowScreenInfo"\n')
        self.assertEqual(self.run_script('install', str(self.stage)).returncode, 0)
        out = self.run_script('hook')
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertEqual(self.lsd.read_text(), '#!/bin/ksh\n' + self.BLOCK +
                         'VMOPTIONS="$VMOPTIONS -DshowScreenInfo"\n')

    def test_the_rollback_works_without_the_installer(self):
        self.assertEqual(self.run_script('install', str(self.stage)).returncode, 0)
        original = self.lsd.read_bytes()
        self.assertEqual(self.run_script('hook').returncode, 0)
        # Another change to lsd.sh after the hook: only our block goes.
        with self.lsd.open('a') as f:
            f.write('echo later\n')
        out = self.rollback_alone()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('changed since', out.stdout)
        self.assertEqual(self.lsd.read_bytes(), original + b'echo later\n')
        self.assertFalse(self.launcher.exists())
        self.assertIn('no start-up block', self.rollback_alone().stdout)

    def test_a_rollback_that_cannot_write_lsd_still_disarms_the_block(self):
        self.assertEqual(self.run_script('install', str(self.stage)).returncode, 0)
        self.assertEqual(self.run_script('hook').returncode, 0)
        self.rollback.write_text(self.rollback.read_text().replace('dash -n "$new"', 'false'))
        out = self.rollback_alone()
        self.assertEqual(out.returncode, 2)
        self.assertIn('the block does nothing', out.stderr)
        self.assertIn('BEGIN MMI WIFI-STA', self.lsd.read_text())
        self.assertFalse(self.launcher.exists())

    def test_hook_refuses_an_lsd_sh_it_does_not_recognise(self):
        self.assertEqual(self.run_script('install', str(self.stage)).returncode, 0)
        for text, why in (('#!/bin/sh\necho\n', 'does not begin with'),
                          (self.LSD + '# sta-boot.sh by hand\n', 'already names'),
                          (self.LSD + 'if then\n', 'does not parse')):
            self.lsd.write_text(text)
            out = self.run_script('hook')
            self.assertEqual(out.returncode, 2, why)
            self.assertIn(why, out.stdout)
            self.assertEqual(self.lsd.read_text(), text)
            self.assertFalse(self.rollback.exists())
            self.assertFalse(self.launcher.exists())
        self.lsd.write_text(self.LSD)
        out = self.run_script('hook', MOCK_FREE='10')
        self.assertIn('needs 64', out.stdout)
        self.assertEqual(self.lsd.read_text(), self.LSD)

    def first_build(self):
        """The first published build's sta-install.sh, run as on the unit
        after 7.6, with its own sta-mode.sh and sta-boot.sh."""
        shown = {}
        for name in ('sta-install.sh', 'sta-mode.sh', 'sta-boot.sh'):
            out = subprocess.run(['git', '-C', str(ROOT), 'show', f'{FIRST_BUILD}:wifi-sta/{name}'],
                                 capture_output=True)
            if out.returncode != 0:
                self.skipTest('not a git checkout with the first build')
            shown[name] = out.stdout
        stage = self.base / 'stage-first'
        stage.mkdir()
        lines = []
        for n in self.NAMES[:-1] + ('sta-mode.sh', 'sta-boot.sh'):
            source = self.stage / n if n in self.NAMES else None
            (stage / n).write_bytes(source.read_bytes() if source else shown[n])
            lines.append(f'{n} {"600" if n.endswith(".bin") else "700"} {cksum(stage / n)}')
        text = shown['sta-install.sh'].decode()
        files_at = text.index("files='") + len("files='")
        text = text[:files_at] + '\n'.join(lines) + text[text.index("'", files_at):]
        for original, replacement in (
                ('persistence=/mnt/persistence', f'persistence={self.persistence}'),
                ('sshd=/mnt/ssh/sbin/sshd', f'sshd={self.sshd}'),
                ('sta_lock=/fs/tmpfs/mmi-sta/lock', f'sta_lock={self.run_dir}/mmi-sta/lock'),
                ('run=/fs/tmpfs', f'run={self.run_dir}'),
                ('conf_owner=root', f'conf_owner={getpass.getuser()}'),
                ('/bin/ksh -n', 'dash -n'),
                ('rollback=/mnt/efs-persist/wifi-sta-rollback.sh', f'rollback={self.rollback}'),
                ('marker=/mnt/efs-persist/wifi-sta/gem/client-at-start-up', f'marker={self.marker}'),
                ('PATH=/bin:/sbin:/usr/bin:/usr/sbin', f'PATH={self.bin}:/bin:/usr/bin')):
            self.assertIn(original, text)
            text = text.replace(original, replacement)
        script = self.base / 'first-sta-install.sh'
        script.write_text(text)
        # The new build must know the first build's scripts.
        for n in ('sta-mode.sh', 'sta-boot.sh'):
            self.assertIn(f'{n} {cksum(stage / n)}', STA_INSTALL.read_text())

        def run(*args, stdin=None):
            return subprocess.run(['dash', str(script), *args], capture_output=True, text=True,
                                  timeout=60, input=stdin, env={'PATH': '/usr/bin:/bin'})
        for args, stdin in ((('install', str(stage)), None), (('conf',), self.CONF), (('hook',), None)):
            out = run(*args, stdin=stdin)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_an_older_build_is_upgraded_and_its_start_up_moved_to_lsd_sh(self):
        before = self.openssh_start.read_bytes()
        self.first_build()
        self.assertIn('sta-boot.sh', self.openssh_start.read_text())
        status = self.run_script('status').stdout
        self.assertIn('state: older', status)
        self.assertIn('an older build', status)

        out = self.run_script('install', str(self.stage))
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn('(older)', out.stdout)
        self.assertIn('wpa-hotspot.conf is already there', out.stdout)
        self.assertEqual((self.top / 'wpa-hotspot.conf').read_text(), self.CONF)
        self.assertIn('state: installed', self.run_script('status').stdout)

        out = self.run_script('hook')
        self.assertEqual(out.returncode, 2)
        self.assertIn('run unhook first', out.stdout)
        self.assertEqual(self.lsd.read_text(), self.LSD)
        self.assertEqual(self.run_script('remove').returncode, 2)
        out = self.run_script('unhook')
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn('is as before the hook', out.stdout)
        self.assertEqual(self.openssh_start.read_bytes(), before)
        out = self.run_script('hook')
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn('BEGIN MMI WIFI-STA', self.lsd.read_text())
        self.assertIn('start-up block: in', self.run_script('status').stdout)

    def test_remove_takes_out_an_older_builds_files(self):
        self.first_build()
        self.assertEqual(self.run_script('unhook').returncode, 0)
        out = self.run_script('remove')
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertFalse(self.top.exists())

    def test_usage(self):
        for args in ([], ['install'], ['conf', 'x'], ['hook', 'x'], ['purge']):
            self.assertEqual(self.run_script(*args).returncode, 1, args)


class Launcher(Base):
    """boot-launch.sh, which the lsd.sh block runs."""

    def setUp(self):
        super().setUp()
        self.boot_script = self.base / 'sta-boot.sh'
        self.script = self.patched(BOOT_LAUNCH, (
            ('boot=/mnt/persistence/wifi-sta/sta-boot.sh', f'boot={self.boot_script}'),
            ('limit=180', 'limit=10'),
            ('exec /bin/ksh "$boot"', 'exec dash "$boot"'),
            ('PATH=/bin:/sbin:/usr/bin:/usr/sbin', f'PATH={self.bin}:/bin:/usr/bin')),
            'boot-launch.sh')

    def launch(self):
        return subprocess.run(['dash', str(self.script)], capture_output=True, text=True,
                              timeout=60, env={'PATH': '/usr/bin:/bin',
                                               'LD_PRELOAD': '/usr/lib/libmc.so'})

    def test_runs_sta_boot_without_the_preload(self):
        self.boot_script.write_text('echo "boot ran preload=${LD_PRELOAD-unset}"\n')
        out = self.launch()
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout, 'boot ran preload=unset\n')

    def test_without_the_hdd_it_gives_up(self):
        out = self.launch()
        self.assertEqual(out.returncode, 0)
        self.assertIn('after 10s', out.stdout)


class StartUp(Base):
    """sta-boot.sh, with sta-mode.sh replaced by a stand-in."""

    def setUp(self):
        super().setUp()
        b = self.base
        self.home = b / 'home'
        self.home.mkdir()
        self.gem = b / 'gem'
        self.gem.mkdir()
        self.run_dir = b / 'run'
        self.run_dir.mkdir()
        self.marker = self.gem / 'client-at-start-up'
        self.openssh = b / 'openssh'
        self.openssh.mkdir()
        self.openssh_run = self.run_dir / 'mmi-openssh'
        self.sta = self.home / 'sta-mode.sh'
        self.sta.write_text('echo "sta: $1 preload=${LD_PRELOAD-unset}"\n'
                            'echo "sta: verdict OK"\necho ran > "$0.ran"\n')
        (self.home / 'SUMS').write_text(f'{cksum(self.sta)} sta-mode.sh\n')
        self.log = self.home / 'boot.log'
        self.tool('ifconfig', '#!/bin/sh\ncase $1 in\n'
                  '    uap0) echo "uap0: flags=8843<UP>"; [ -n "${MOCK_NO_ADDR-}" ] || '
                  'echo "\tinet 192.168.1.1 netmask 0xffffff00";;\n'
                  '    en5) [ -n "${MOCK_EN5-}" ];;\n'
                  '    *) exit 1;;\nesac\n')
        self.script = self.patched(STA_BOOT, (
            ('home=/mnt/persistence/wifi-sta', f'home={self.home}'),
            ('gem=/mnt/efs-persist/wifi-sta/gem', f'gem={self.gem}'),
            ('run=/fs/tmpfs', f'run={self.run_dir}'),
            ('uap0_limit=300', 'uap0_limit=10'),
            ('openssh=/mnt/persistence/openssh', f'openssh={self.openssh}'),
            ('openssh_disabled=/mnt/efs-persist/openssh.disabled',
             f'openssh_disabled={self.gem}/openssh.disabled'),
            ('openssh_limit=300', 'openssh_limit=10'),
            ('/bin/ksh "$home/sta-mode.sh" start', 'dash "$home/sta-mode.sh" start'),
            ('PATH=/bin:/sbin:/usr/bin:/usr/sbin', f'PATH={self.bin}:/bin:/usr/bin')),
            'sta-boot.sh')

    def boot(self, **env):
        out = subprocess.run(['dash', str(self.script)], capture_output=True, text=True,
                             timeout=60, env={'PATH': '/usr/bin:/bin',
                                              'LD_PRELOAD': '/usr/lib/libmc.so', **env})
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return self.log.read_text()

    def started(self):
        return (self.home / 'sta-mode.sh.ran').exists()

    def test_without_the_marker_the_access_point_stays(self):
        log = self.boot()
        self.assertIn('mode: car access point', log)
        self.assertFalse(self.started())

    def test_the_kill_switch_keeps_the_access_point(self):
        self.marker.write_text('')
        (self.gem / 'start.disabled').write_text('')
        self.assertIn('the start is disabled', self.boot())
        self.assertFalse(self.started())

    def test_en5_keeps_the_access_point(self):
        self.marker.write_text('')
        self.assertIn('en5 is present', self.boot(MOCK_EN5='1'))
        self.assertFalse(self.started())

    def test_an_access_point_that_never_comes_up_is_not_detached(self):
        self.marker.write_text('')
        self.assertIn('uap0 has no 192.168.1.1', self.boot(MOCK_NO_ADDR='1'))
        self.assertFalse(self.started())

    def test_files_that_do_not_match_are_not_started(self):
        self.marker.write_text('')
        self.sta.write_text(self.sta.read_text() + '# changed\n')
        log = self.boot()
        self.assertIn('sta-mode.sh does not match its sum', log)
        self.assertFalse(self.started())

    def test_starts_the_station_and_logs_its_lines(self):
        self.marker.write_text('')
        log = self.boot()
        self.assertTrue(self.started())
        self.assertIn('mode: hotspot client', log)
        self.assertIn('    sta: start preload=unset', log)
        self.assertIn('    sta: verdict OK', log)
        self.assertIn('sta-mode.sh start exit 0', log)

    def openssh_installed(self, up):
        (self.openssh / 'INSTALL_READY').write_text('OpenSSH\n')
        self.openssh_run.mkdir()
        if up:
            for name in ('sshd.pid', 'entropy.pid'):
                (self.openssh_run / name).write_text(f'{os.getpid()}\n')

    def test_waits_for_the_openssh_installation(self):
        self.marker.write_text('')
        self.openssh_installed(up=True)
        log = self.boot()
        self.assertIn('OpenSSH is up (waited 0s)', log)
        self.assertTrue(self.started())

    def test_an_openssh_start_up_still_running_is_waited_for(self):
        self.marker.write_text('')
        self.openssh_installed(up=True)
        (self.openssh_run / 'start.lock').mkdir()
        log = self.boot()
        self.assertIn('OpenSSH is not up after 10s; starting without it', log)
        self.assertTrue(self.started())

    def test_an_openssh_that_never_starts_does_not_stop_the_station(self):
        self.marker.write_text('')
        self.openssh_installed(up=False)
        self.assertIn('OpenSSH is not up after 10s', self.boot())
        self.assertTrue(self.started())

    def test_a_disabled_openssh_is_not_waited_for(self):
        self.marker.write_text('')
        self.openssh_installed(up=False)
        (self.gem / 'openssh.disabled').write_text('disabled\n')
        log = self.boot()
        self.assertNotIn('OpenSSH', log)
        self.assertTrue(self.started())

    def test_the_log_keeps_one_older_generation(self):
        self.log.write_text('x' * 40000)
        self.boot()
        self.assertEqual(len((self.home / 'boot.log.old').read_text()), 40000)
        self.assertLess(len(self.log.read_text()), 1000)


class StationPins(unittest.TestCase):
    def pins(self):
        text = STA_INSTALL.read_text()
        start = text.index("files='") + len("files='")
        block = text[start:text.index("'", start)]
        return {line.split()[0]: ' '.join(line.split()[2:4]) for line in block.splitlines()}

    def test_the_files_are_those_sta_mode_needs(self):
        self.assertEqual(sorted(self.pins()),
                         sorted(StationInstaller.NAMES + StationInstaller.SCRIPTS))
        self.assertEqual(self.pins()['sta-mode.sh'], cksum(STA_MODE))
        self.assertEqual(self.pins()['sta-boot.sh'], cksum(STA_BOOT))
        self.assertEqual(self.pins()['boot-launch.sh'], cksum(BOOT_LAUNCH))

    def test_the_local_builds_match_the_pins(self):
        # The binaries the 7.3 run used; present only on the workstation
        # that built them.
        local = {'devn-mv8688sta.so': BUILD / 'devn-mv8688sta.so',
                 'mv8688-ctl': BUILD / 'mv8688-ctl', 'mv8688-probe': BUILD / 'mv8688-probe',
                 'wpa_supplicant': BUILD / 'wpa/wpa_supplicant',
                 'jitter-egd': ROOT / 'openssh/work/jitter-build/jitter-egd',
                 'sd8688_helper.bin': ROOT / 'work/firmware/sd8688_helper.bin',
                 'sd8688.bin': ROOT / 'work/firmware/sd8688.bin'}
        if not all(path.is_file() for path in local.values()):
            self.skipTest('the local station builds are not present')
        for name, path in local.items():
            self.assertEqual(self.pins()[name], cksum(path), name)


class UnitUtilities(unittest.TestCase):
    def test_uses_no_utility_the_unit_lacks(self):
        # Checked on the unit on 2026-10-28/29: no wc, nohup, tail, head or
        # printf; its grep has no -o; its date has no +%s. The saved images
        # have no id or env either.
        for path in (GEM, INSTALL, STA_INSTALL, STA_BOOT, BOOT_LAUNCH):
            body = code_lines(path)
            for absent in (' wc ', 'nohup', '| tail', '| head', 'printf', '+%s',
                           '$(id', ' env ', 'mkdir -p'):
                self.assertNotIn(absent, body, (path.name, absent))
            self.assertNotRegex(body, r'\bgrep\s+(-[A-Za-z]*o|--only)')

    def test_no_bare_argument_list(self):
        # The unit's ksh rejects an empty "$@" under set -u; only bounded()
        # takes it, and it always has a command.
        for path in (GEM, INSTALL, STA_INSTALL, STA_BOOT, BOOT_LAUNCH):
            bare = [line.strip() for line in path.read_text().splitlines()
                    if '"$@"' in line and '${1+"$@"}' not in line]
            self.assertEqual(bare, ['"$@" > "$bounded_out" 2>&1 < /dev/null &']
                             if path == GEM else [], path.name)


if __name__ == '__main__':
    unittest.main()
