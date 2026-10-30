"""os_get_random for wpa_supplicant on the unit, against a fake EGD service.

The fake answers EGD command 2 the way jitter-egd does: the requested bytes
on the same connection. It shows the client's framing, chunking and
failure handling, not jitter-egd's behaviour or the quality of its bytes.
"""
import ctypes as C
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FakeEgd:
    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.requests = []
        self.mode = 'ok'            # ok, short, close, hang, trickle
        self.counter = 0
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                request = conn.recv(2)
                self.requests.append(request)
                if self.mode == 'close' or len(request) != 2 or request[0] != 2:
                    continue
                n = request[1]
                data = bytes((self.counter + i) & 0xff for i in range(n))
                self.counter += n
                if self.mode == 'hang':
                    time.sleep(1.5)
                elif self.mode == 'trickle':
                    # A byte every 0.2 s: each read succeeds, the whole does not
                    # finish within the deadline.
                    for i in range(n):
                        try:
                            conn.sendall(data[i:i + 1])
                        except OSError:
                            break
                        time.sleep(0.2)
                else:
                    conn.sendall(data[:n - 1] if self.mode == 'short' else data)

    def stop(self):
        self.sock.close()


class EgdRandomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('cc')
        if not compiler:
            raise RuntimeError('C compiler required for the EGD tests')
        cls.egd = FakeEgd()
        cls.addClassCleanup(cls.egd.stop)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        so = Path(cls.tmp.name) / 'egd.so'
        subprocess.run([compiler, '-std=c99', '-D_DEFAULT_SOURCE', '-Wall', '-Wextra', '-Werror',
                        '-shared', '-fPIC', '-DEGD_PORT=%d' % cls.egd.port,
                        str(ROOT / 'wifi-sta' / 'wpa' / 'os_random_egd.c'), '-o', str(so)],
                       check=True, capture_output=True)
        cls.lib = C.CDLL(str(so))
        cls.lib.os_get_random.argtypes = [C.c_char_p, C.c_size_t]
        # The same code with a 1 s deadline, for the slow-service cases.
        short = Path(cls.tmp.name) / 'egd-short.so'
        subprocess.run([compiler, '-std=c99', '-D_DEFAULT_SOURCE', '-Wall', '-Wextra', '-Werror',
                        '-shared', '-fPIC', '-DEGD_PORT=%d' % cls.egd.port,
                        '-DEGD_DEADLINE_MS=1000',
                        str(ROOT / 'wifi-sta' / 'wpa' / 'os_random_egd.c'), '-o', str(short)],
                       check=True, capture_output=True)
        cls.short = C.CDLL(str(short))
        cls.short.os_get_random.argtypes = [C.c_char_p, C.c_size_t]

    def setUp(self):
        self.egd.requests.clear()
        self.egd.mode = 'ok'
        self.egd.counter = 0

    def get(self, n):
        buf = C.create_string_buffer(b'\xaa' * n, n)
        return self.lib.os_get_random(buf, n), buf.raw

    def test_a_nonce_is_one_blocking_read(self):
        rc, data = self.get(32)
        self.assertEqual((rc, data), (0, bytes(range(32))))
        self.assertEqual(self.egd.requests, [b'\x02\x20'])

    def test_more_than_255_bytes_take_several_requests(self):
        rc, data = self.get(600)
        self.assertEqual(rc, 0)
        self.assertEqual(data, bytes(i & 0xff for i in range(600)))
        self.assertEqual(self.egd.requests, [b'\x02\xff', b'\x02\xff', b'\x02\x5a'])

    def test_a_short_answer_fails_and_leaves_no_partial_bytes(self):
        self.egd.mode = 'short'
        self.assertEqual(self.get(32), (-1, bytes(32)))
        self.egd.mode = 'close'
        self.assertEqual(self.get(16), (-1, bytes(16)))

    def test_nothing_listening_fails(self):
        port_holder = socket.socket()
        port_holder.bind(('127.0.0.1', 0))
        port = port_holder.getsockname()[1]
        port_holder.close()
        so = Path(self.tmp.name) / 'egd-none.so'
        subprocess.run([shutil.which('cc'), '-std=c99', '-D_DEFAULT_SOURCE', '-shared', '-fPIC',
                        '-DEGD_PORT=%d' % port,
                        str(ROOT / 'wifi-sta' / 'wpa' / 'os_random_egd.c'), '-o', str(so)],
                       check=True, capture_output=True)
        lib = C.CDLL(str(so))
        lib.os_get_random.argtypes = [C.c_char_p, C.c_size_t]
        buf = C.create_string_buffer(8)
        self.assertEqual(lib.os_get_random(buf, 8), -1)

    def timed(self, n):
        buf = C.create_string_buffer(b'\xaa' * n, n)
        start = time.monotonic()
        rc = self.short.os_get_random(buf, n)
        return rc, buf.raw, time.monotonic() - start

    def test_a_service_that_never_answers_fails_at_the_deadline(self):
        self.egd.mode = 'hang'
        rc, data, took = self.timed(16)
        self.assertEqual((rc, data), (-1, bytes(16)))
        self.assertLess(took, 1.4)

    def test_bytes_that_trickle_in_do_not_extend_the_deadline(self):
        self.egd.mode = 'trickle'
        rc, data, took = self.timed(32)
        self.assertEqual((rc, data), (-1, bytes(32)))
        self.assertLess(took, 1.4)


if __name__ == '__main__':
    unittest.main()
