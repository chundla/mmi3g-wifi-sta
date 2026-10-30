"""The BPF filter and record parser behind the phase 6 EAPOL path.

The filter program is run here by a small interpreter for the four
instructions it uses, with classic BPF semantics (absolute loads are
big-endian; a load past the frame rejects it). Records are laid out as
bpf_hdr on a 32-bit stack. Nothing here shows how io-pkt's BPF behaves.
"""
import ctypes as C
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWN = bytes.fromhex('001cd7000001')
PEER = bytes.fromhex('020000aabbcc')
EAPOL = 0x888e


class Insn(C.Structure):
    _fields_ = [('code', C.c_uint16), ('jt', C.c_uint8), ('jf', C.c_uint8), ('k', C.c_uint32)]


def run_filter(prog, frame):
    """Classic BPF for ld w/h abs, jeq k and ret k."""
    acc, pc = 0, 0
    while True:
        i = prog[pc]
        if i.code in (0x20, 0x28):
            size = 4 if i.code == 0x20 else 2
            if i.k + size > len(frame):
                return 0
            acc = int.from_bytes(frame[i.k:i.k + size], 'big')
        elif i.code == 0x15:
            pc += i.jt if acc == i.k else i.jf
        elif i.code == 0x06:
            return i.k
        else:
            raise AssertionError('unexpected opcode %#x' % i.code)
        pc += 1


def record(frame, caplen=None, hdrlen=18):
    caplen = len(frame) if caplen is None else caplen
    header = bytes(8) + caplen.to_bytes(4, 'little') + len(frame).to_bytes(4, 'little')
    header += hdrlen.to_bytes(2, 'little') + bytes(hdrlen - 18)
    body = header + frame[:caplen]
    return body + bytes(-len(body) % 4)


class BpfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('cc')
        if not compiler:
            raise RuntimeError('C compiler required for the BPF tests')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        so = Path(cls.tmp.name) / 'bpfl2.so'
        subprocess.run([compiler, '-std=c99', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        str(ROOT / 'wifi-sta' / 'bpfl2.c'), '-o', str(so)],
                       check=True, capture_output=True)
        lib = cls.lib = C.CDLL(str(so))
        lib.bpfl2_filter.argtypes = [Insn * 8, C.c_uint16, C.c_char_p]
        lib.bpfl2_filter.restype = None
        lib.bpfl2_next.argtypes = [C.c_char_p, C.c_size_t, C.POINTER(C.c_size_t),
                                   C.POINTER(C.c_void_p), C.POINTER(C.c_uint)]

    def program(self, ethertype=EAPOL, own=OWN):
        prog = (Insn * 8)()
        self.lib.bpfl2_filter(prog, ethertype, own)
        return prog

    @staticmethod
    def frame(src=PEER, dst=OWN, ethertype=EAPOL, payload=bytes(99)):
        return dst + src + ethertype.to_bytes(2, 'big') + payload

    def test_frames_of_the_type_from_others_are_accepted_whole(self):
        prog = self.program()
        self.assertEqual(run_filter(prog, self.frame()), 0xffffffff)
        self.assertEqual(run_filter(prog, self.frame(dst=b'\xff' * 6)), 0xffffffff)

    def test_other_types_and_the_stations_own_frames_are_dropped(self):
        prog = self.program()
        self.assertEqual(run_filter(prog, self.frame(ethertype=0x0800)), 0)
        self.assertEqual(run_filter(prog, self.frame(src=OWN)), 0)
        self.assertEqual(run_filter(prog, self.frame()[:13]), 0)

    def test_a_source_matching_only_part_of_the_own_address_is_accepted(self):
        prog = self.program()
        for src in (OWN[:4] + b'\x00\x02', b'\x02' + OWN[1:], OWN[:5] + b'\x00'):
            self.assertEqual(run_filter(prog, self.frame(src=src)), 0xffffffff, src.hex())

    def test_the_filter_takes_its_type_from_the_argument(self):
        prog = self.program(ethertype=0x0806)
        self.assertEqual(run_filter(prog, self.frame(ethertype=0x0806)), 0xffffffff)
        self.assertEqual(run_filter(prog, self.frame()), 0)

    def walk(self, data):
        off, out = C.c_size_t(0), []
        frame, caplen = C.c_void_p(), C.c_uint()
        while True:
            rc = self.lib.bpfl2_next(data, len(data), C.byref(off), C.byref(frame),
                                     C.byref(caplen))
            if rc != 1:
                return rc, out
            start = frame.value - C.cast(C.c_char_p(data), C.c_void_p).value
            out.append(data[start:start + caplen.value])

    def test_records_are_walked_on_long_boundaries(self):
        frames = [self.frame(payload=bytes(n)) for n in (46, 47, 1500, 99)]
        data = b''.join(record(f) for f in frames)
        self.assertEqual(self.walk(data), (0, frames))

    def test_a_longer_header_and_a_short_capture_are_followed(self):
        f = self.frame()
        data = record(f, hdrlen=20) + record(f, caplen=20)
        self.assertEqual(self.walk(data), (0, [f, f[:20]]))

    def test_records_that_do_not_fit_the_read_are_rejected(self):
        f = self.frame()
        good = record(f)
        cases = {
            'short header': good + bytes(10),
            'capture past the end': good + record(f)[:-8],
            'header length below the struct': record(f)[:16] + (17).to_bytes(2, 'little')
            + record(f)[18:],
            'header length past the end': record(f)[:16] + (999).to_bytes(2, 'little')
            + record(f)[18:],
        }
        for name, data in cases.items():
            rc, _ = self.walk(data)
            self.assertEqual(rc, -1, name)

    def test_an_empty_read_has_no_records(self):
        self.assertEqual(self.walk(b''), (0, []))


class BenchBroadcastTests(unittest.TestCase):
    """bpf-check's listen mode: which frames count as numbered bench broadcasts."""

    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('cc')
        if not compiler:
            raise RuntimeError('C compiler required for the BPF tests')
        source = (ROOT / 'wifi-sta' / 'bpf-check.c').read_text()
        tag = re.search(r'#define BCAST_TAG .*', source).group(0)
        start = source.index('static long bcast_number(')
        body = source[start:source.index('\n}\n', start) + 3]
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        c = Path(cls.tmp.name) / 'bcast.c'
        c.write_text('#include <stdint.h>\n#include <string.h>\n' + tag + '\n'
                     + body.replace('static long', 'long'))
        so = Path(cls.tmp.name) / 'bcast.so'
        subprocess.run([compiler, '-std=c99', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        str(c), '-o', str(so)], check=True, capture_output=True)
        cls.lib = C.CDLL(str(so))
        cls.lib.bcast_number.argtypes = [C.c_char_p, C.c_uint, C.c_uint]
        cls.lib.bcast_number.restype = C.c_long

    @staticmethod
    def frame(payload=b'mmi-bench-bcast 042', port=9, dst=b'\xff' * 6, ethertype=0x0800,
              proto=17, ihl=5):
        options = bytes((ihl - 5) * 4)
        udp = (40000).to_bytes(2, 'big') + port.to_bytes(2, 'big') \
            + (8 + len(payload)).to_bytes(2, 'big') + bytes(2) + payload
        ip = bytes([0x40 | ihl, 0]) + (20 + len(options) + len(udp)).to_bytes(2, 'big') \
            + bytes(5) + bytes([proto]) + bytes(2) + bytes([10, 42, 88, 1]) \
            + bytes([10, 42, 88, 255]) + options
        return dst + PEER + ethertype.to_bytes(2, 'big') + ip + udp

    def number(self, frame, port=9, caplen=None):
        return self.lib.bcast_number(frame, len(frame) if caplen is None else caplen, port)

    def test_the_number_is_read_from_a_bench_broadcast(self):
        self.assertEqual(self.number(self.frame()), 42)
        self.assertEqual(self.number(self.frame(b'mmi-bench-bcast 7')), 7)
        self.assertEqual(self.number(self.frame(b'mmi-bench-bcast 1234 tail')), 1234)
        self.assertEqual(self.number(self.frame(ihl=6)), 42)

    def test_other_frames_are_not_counted(self):
        cases = {
            'unicast': self.frame(dst=OWN),
            'not IPv4': self.frame(ethertype=0x86dd),
            'not UDP': self.frame(proto=6),
            'other port': self.frame(port=137),
            'other payload': self.frame(b'NBSTAT 042'),
            'no digits': self.frame(b'mmi-bench-bcast x'),
            'tag only': self.frame(b'mmi-bench-bcast '),
        }
        for name, f in cases.items():
            self.assertEqual(self.number(f), -1, name)

    def test_a_short_capture_is_not_read_past(self):
        f = self.frame()
        for caplen in (13, 33, 41, len(f) - 4):
            self.assertEqual(self.number(f, caplen=caplen), -1, caplen)


if __name__ == '__main__':
    unittest.main()
