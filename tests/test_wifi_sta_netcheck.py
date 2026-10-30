"""The phase 7 network check, built for the host and run against local servers.

These show the program's parsing, deadline and reporting with Linux's
resolver and sockets. Nothing here shows how the unit's libsocket resolver
or io-pkt behave, or that a route through the hotspot works.
"""
import ctypes as C
import http.server
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'wifi-sta' / 'netcheck.c'
BODY = bytes(range(256)) * 400


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/missing':
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Length', str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, *args):
        pass


def compile_c(args, out):
    compiler = shutil.which('cc')
    if not compiler:
        raise RuntimeError('C compiler required for the network check tests')
    subprocess.run([compiler, '-std=c99', '-D_DEFAULT_SOURCE', '-Wall', '-Wextra', '-Werror',
                    *args, '-o', str(out)], check=True, capture_output=True)


class ParserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        so = Path(cls.tmp.name) / 'netcheck.so'
        compile_c(['-DNETCHECK_NO_MAIN', '-shared', '-fPIC', str(SOURCE)], so)
        lib = cls.lib = C.CDLL(str(so))
        lib.nc_parse_url.argtypes = [C.c_char_p, C.c_char_p, C.POINTER(C.c_uint),
                                     C.POINTER(C.c_char_p)]
        lib.nc_status.argtypes = [C.c_char_p, C.c_size_t]
        lib.nc_header_end.argtypes = [C.c_char_p, C.c_size_t]
        lib.nc_header_end.restype = C.c_long

    def parse(self, url):
        host = C.create_string_buffer(128)
        port, path = C.c_uint(), C.c_char_p()
        rc = self.lib.nc_parse_url(url.encode(), host, C.byref(port), C.byref(path))
        return None if rc else (host.value.decode(), port.value, path.value.decode())

    def test_plain_http_urls_are_split(self):
        self.assertEqual(self.parse('http://poi.audi-online.com/poi'),
                         ('poi.audi-online.com', 80, '/poi'))
        self.assertEqual(self.parse('http://10.42.88.1:8080'), ('10.42.88.1', 8080, '/'))
        self.assertEqual(self.parse('http://a-b.example:65535/x?y=1'),
                         ('a-b.example', 65535, '/x?y=1'))

    def test_other_urls_are_refused(self):
        for url in ('https://car.audi-online.com/', 'http://', 'http://host:0/',
                    'http://host:65536/', 'http://host:/', 'http://ho_st/', 'http://host x',
                    'http://host/a b', 'ftp://host/', 'http://' + 'a' * 128 + '/'):
            self.assertIsNone(self.parse(url), url)

    def test_status_lines(self):
        status = lambda b: self.lib.nc_status(b, len(b))
        self.assertEqual(status(b'HTTP/1.1 404 Not Found\r\n'), 404)
        self.assertEqual(status(b'HTTP/1.0 200 OK\r\n'), 200)
        for bad in (b'HTTP/1.1 20', b'HTTP/2 200 OK\r\n', b'SSH-2.0-OpenSSH\r\n',
                    b'HTTP/1.1 600 x\r\n', b'HTTP/1.1  200\r\n'):
            self.assertEqual(status(bad), -1, bad)

    def test_header_end(self):
        end = lambda b: self.lib.nc_header_end(b, len(b))
        self.assertEqual(end(b'HTTP/1.0 200 OK\r\nA: b\r\n\r\nbody'), 25)
        self.assertEqual(end(b'HTTP/1.0 200 OK\r\nA: b\r\n'), -1)


class ProgramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.exe = Path(cls.tmp.name) / 'netcheck'
        compile_c([str(SOURCE)], cls.exe)
        cls.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.addClassCleanup(cls.server.server_close)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.server.shutdown)
        cls.port = cls.server.server_address[1]

    def run_check(self, *args):
        return subprocess.run([str(self.exe), *args], capture_output=True, text=True,
                              timeout=30)

    def test_a_download_is_counted_and_timed(self):
        r = self.run_check('get', 'http://127.0.0.1:%d/file' % self.port)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn('get: status line "HTTP/1.0 200 OK"', r.stdout)
        result = re.search(r'result: status=200 header_bytes=(\d+) body_bytes=(\d+) '
                           r'total_bytes=(\d+) first_byte_ms=\d+ elapsed_ms=\d+ rate=\d+ B/s\n',
                           r.stdout)
        self.assertIsNotNone(result, r.stdout)
        header, body, total = map(int, result.groups())
        self.assertEqual(body, len(BODY))
        self.assertEqual(header + body, total)
        self.assertIn('local 127.0.0.1:', r.stdout)

    def test_the_peer_and_local_addresses_are_both_shown(self):
        # Both through inet_ntoa's one static buffer would print the same.
        with socket.socket() as s:
            s.bind(('127.0.0.2', 0))
            s.listen(1)
            port = s.getsockname()[1]
            r = self.run_check('connect', '127.0.0.2', str(port))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.stdout, r'connect: 127\.0\.0\.2:%d in \d+ ms, local 127\.0\.0\.1:'
                         % port)

    def test_an_error_status_still_counts_as_an_answer(self):
        r = self.run_check('get', 'http://127.0.0.1:%d/missing' % self.port)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn('result: status=404 ', r.stdout)

    def test_the_byte_limit_stops_the_download(self):
        r = self.run_check('-m', '4096', 'get', 'http://127.0.0.1:%d/file' % self.port)
        self.assertIn('(stopped at -m)', r.stdout)
        total = int(re.search(r'total_bytes=(\d+)', r.stdout).group(1))
        self.assertGreaterEqual(total, 4096)
        self.assertLess(total, 4096 + 8192)

    def test_a_refused_connection_fails(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        r = self.run_check('connect', '127.0.0.1', str(port))
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn('FAILED', r.stdout)

    def test_the_deadline_ends_a_silent_server(self):
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            s.listen(1)
            r = self.run_check('-w', '1', 'get', 'http://127.0.0.1:%d/' % s.getsockname()[1])
        self.assertEqual(r.returncode, 3, r.stdout)
        self.assertTrue(r.stdout.endswith('result: deadline reached\n'), r.stdout)

    def test_resolve_and_connect(self):
        r = self.run_check('resolve', 'localhost')
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.stdout, r'resolve: localhost -> 127\.0\.0\.1\n')
        r = self.run_check('connect', 'localhost', str(self.port))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.stdout, r'connect: 127\.0\.0\.1:%d in \d+ ms, local ' % self.port)

    def test_usage_errors(self):
        for args in ([], ['get'], ['resolve'], ['connect', 'h'], ['connect', 'h', '0'],
                     ['-w', '0', 'resolve', 'h'], ['-w', '601', 'resolve', 'h'],
                     ['-m', '0', 'get', 'http://h/'], ['ping', 'h']):
            self.assertEqual(self.run_check(*args).returncode, 1, args)
        r = self.run_check('get', 'https://car.audi-online.com/')
        self.assertEqual(r.returncode, 1, r.stdout)


if __name__ == '__main__':
    unittest.main()
