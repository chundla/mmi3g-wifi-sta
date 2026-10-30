"""Phase 5.1 bench frames: ARP and ICMP echo built and checked in C.

The expected frames are assembled here independently from RFC 826, 791 and
792 layouts, and the replies are what a Linux peer would send back. None of
this is a capture from the bench.
"""
import ctypes as C
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OTHER, ARP_REPLY, ARP_ASKED, ECHO_REPLY, ECHO_BAD, ECHO_LATE, IPV4_OTHER = range(7)
MAC = bytes.fromhex('001cd7000001')
PEER_MAC = bytes.fromhex('dca632000002')
IP = bytes((10, 42, 88, 10))
PEER_IP = bytes((10, 42, 88, 1))
ID = 0x1234


class Bench(C.Structure):
    _fields_ = [('mac', C.c_uint8 * 6), ('ip', C.c_uint8 * 4), ('peer_ip', C.c_uint8 * 4),
                ('peer_mac', C.c_uint8 * 6), ('peer_known', C.c_int),
                ('id', C.c_uint16), ('seq', C.c_uint16), ('payload', C.c_uint),
                ('echo_done', C.c_int), ('arp_due', C.c_int),
                ('asker_mac', C.c_uint8 * 6), ('asker_ip', C.c_uint8 * 4),
                ('frames', C.c_uint), ('arp_replies', C.c_uint), ('arp_asked', C.c_uint),
                ('echo_replies', C.c_uint), ('echo_bad', C.c_uint), ('echo_late', C.c_uint),
                ('ipv4_other', C.c_uint), ('other', C.c_uint), ('bad', C.c_char_p)]


def checksum(data):
    if len(data) % 2:
        data += b'\0'
    total = sum(struct.unpack('!%dH' % (len(data) // 2), data))
    while total >> 16:
        total = (total & 0xffff) + (total >> 16)
    return ~total & 0xffff


def arp(op, dst, src_mac, src_ip, target_mac, target_ip):
    return (dst + src_mac + b'\x08\x06' + struct.pack('!HHBBH', 1, 0x0800, 6, 4, op)
            + src_mac + src_ip + target_mac + target_ip)


def ipv4(src, dst, payload, ident=0, flags=0x4000, proto=1, ttl=64):
    header = struct.pack('!BBHHHBBH4s4s', 0x45, 0, 20 + len(payload), ident, flags, ttl,
                         proto, 0, src, dst)
    header = header[:10] + struct.pack('!H', checksum(header)) + header[12:]
    return header + payload


def icmp(kind, ident, seq, data):
    body = struct.pack('!BBHHH', kind, 0, 0, ident, seq) + data
    return body[:2] + struct.pack('!H', checksum(body)) + body[4:]


def pattern(seq, n):
    return bytes((seq + i) & 0xff for i in range(n))


def echo_reply(seq, n, ident=ID, data=None, src=PEER_IP, dst=IP, src_mac=PEER_MAC,
               dst_mac=MAC, ip_ident=0x5555):
    data = pattern(seq, n) if data is None else data
    return dst_mac + src_mac + b'\x08\x00' + ipv4(src, dst, icmp(0, ident, seq, data),
                                                  ident=ip_ident, flags=0)


class BenchFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('cc')
        if not compiler:
            raise RuntimeError('C compiler required for the bench frame tests')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        so = Path(cls.tmp.name) / 'benchnet.so'
        subprocess.run([compiler, '-std=c99', '-Wall', '-Wextra', '-Werror', '-shared',
                        '-fPIC', str(ROOT / 'wifi-sta' / 'benchnet.c'), '-o', str(so)],
                       check=True, capture_output=True)
        lib = cls.lib = C.CDLL(str(so))
        lib.bench_parse_ip.argtypes = [C.c_char_p, C.c_uint8 * 4]
        lib.bench_init.argtypes = [C.POINTER(Bench), C.c_char_p, C.c_char_p, C.c_char_p,
                                   C.c_uint16]
        lib.bench_init.restype = None
        lib.bench_checksum.argtypes = [C.c_char_p, C.c_uint]
        lib.bench_checksum.restype = C.c_uint16
        for name in ('bench_arp_request', 'bench_arp_reply'):
            getattr(lib, name).argtypes = [C.POINTER(Bench), C.c_uint8 * 1514]
            getattr(lib, name).restype = C.c_uint
        lib.bench_echo_request.argtypes = [C.POINTER(Bench), C.c_uint8 * 1514, C.c_uint16,
                                           C.c_uint]
        lib.bench_echo_request.restype = C.c_uint
        lib.bench_rx.argtypes = [C.POINTER(Bench), C.c_char_p, C.c_uint]

    def setUp(self):
        self.b = Bench()
        self.lib.bench_init(C.byref(self.b), MAC, IP, PEER_IP, ID)
        self.frame = (C.c_uint8 * 1514)()

    def rx(self, frame):
        return self.lib.bench_rx(C.byref(self.b), frame, len(frame))

    def learn_peer(self):
        self.assertEqual(self.rx(arp(2, MAC, PEER_MAC, PEER_IP, MAC, IP)), ARP_REPLY)

    def echo(self, seq, n):
        length = self.lib.bench_echo_request(C.byref(self.b), self.frame, seq, n)
        return bytes(self.frame[:length])

    def parse_ip(self, text):
        out = (C.c_uint8 * 4)()
        return self.lib.bench_parse_ip(text.encode(), out), bytes(out)

    def test_dotted_quads_are_parsed_strictly(self):
        self.assertEqual(self.parse_ip('10.42.88.10'), (0, IP))
        self.assertEqual(self.parse_ip('192.168.1.255')[0], 0)
        for text in ('10.42.88', '10.42.88.1.2', '10.42.88.256', '10.42.088.1', '10.42.88.1 ',
                     '10.42..1', '', 'a.b.c.d', '10.42.88.1x', '-1.2.3.4'):
            self.assertEqual(self.parse_ip(text)[0], -1, text)
        for text in ('0.1.2.3', '127.0.0.1', '224.0.0.251', '255.255.255.255'):
            self.assertEqual(self.parse_ip(text)[0], -2, text)

    def test_the_checksum_matches_rfc_1071(self):
        data = bytes.fromhex('0001f203f4f5f6f7')
        self.assertEqual(self.lib.bench_checksum(data, len(data)), 0x220d)
        self.assertEqual(self.lib.bench_checksum(b'\x01', 1), checksum(b'\x01'))

    def test_the_arp_request_asks_for_the_peer(self):
        length = self.lib.bench_arp_request(C.byref(self.b), self.frame)
        self.assertEqual(bytes(self.frame[:length]),
                         arp(1, b'\xff' * 6, MAC, IP, bytes(6), PEER_IP))

    def test_an_arp_reply_from_the_peer_teaches_its_address(self):
        self.assertFalse(self.b.peer_known)
        self.learn_peer()
        self.assertEqual((self.b.peer_known, bytes(self.b.peer_mac), self.b.arp_replies),
                         (1, PEER_MAC, 1))

    def test_arp_replies_not_meant_for_this_bench_are_ignored(self):
        other_ip, other_mac = bytes((10, 42, 88, 2)), bytes.fromhex('020000000009')
        for frame in (arp(2, MAC, PEER_MAC, other_ip, MAC, IP),        # someone else
                      arp(2, other_mac, PEER_MAC, PEER_IP, other_mac, IP),
                      arp(2, MAC, PEER_MAC, PEER_IP, MAC, other_ip),
                      arp(2, MAC, b'\x01' + PEER_MAC[1:], PEER_IP, MAC, IP),  # group
                      arp(2, MAC, PEER_MAC, PEER_IP, MAC, IP)[:41]):
            self.assertEqual(self.rx(frame), OTHER)
        self.assertEqual((self.b.peer_known, self.b.other, self.b.frames), (0, 5, 5))

    def test_an_arp_request_for_our_address_is_answered(self):
        asker_ip = bytes((10, 42, 88, 1))
        self.assertEqual(self.rx(arp(1, b'\xff' * 6, PEER_MAC, asker_ip, bytes(6), IP)),
                         ARP_ASKED)
        self.assertEqual((self.b.arp_due, self.b.arp_asked), (1, 1))
        length = self.lib.bench_arp_reply(C.byref(self.b), self.frame)
        self.assertEqual(bytes(self.frame[:length]),
                         arp(2, PEER_MAC, MAC, IP, PEER_MAC, asker_ip))
        # A request for another address is not ours to answer.
        self.b.arp_due = 0
        self.assertEqual(self.rx(arp(1, b'\xff' * 6, PEER_MAC, asker_ip, bytes(6),
                                     bytes((10, 42, 88, 11)))), OTHER)
        self.assertEqual(self.b.arp_due, 0)

    def test_no_echo_before_the_peer_is_known_or_beyond_1472_bytes(self):
        self.assertEqual(self.echo(1, 56), b'')
        self.learn_peer()
        self.assertEqual(self.echo(1, 1473), b'')
        self.assertEqual(len(self.echo(1, 1472)), 1514)

    def test_echo_requests_are_well_formed_ip_and_icmp(self):
        self.learn_peer()
        for seq, n in ((1, 0), (2, 56), (3, 1471), (300, 1472)):
            frame = self.echo(seq, n)
            expected = PEER_MAC + MAC + b'\x08\x00' + ipv4(IP, PEER_IP,
                                                           icmp(8, ID, seq, pattern(seq, n)),
                                                           ident=seq)
            self.assertEqual(frame, expected, (seq, n))
            self.assertEqual(checksum(frame[14:34]), 0)
            self.assertEqual(checksum(frame[34:]), 0)
            self.assertEqual((self.b.seq, self.b.payload, self.b.echo_done), (seq, n, 0))

    def test_the_awaited_reply_is_accepted(self):
        self.learn_peer()
        self.echo(5, 1472)
        self.assertEqual(self.rx(echo_reply(5, 1472)), ECHO_REPLY)
        self.assertEqual((self.b.echo_done, self.b.echo_replies, self.b.echo_bad), (1, 1, 0))
        # The same reply again is a duplicate, not a failure.
        self.assertEqual(self.rx(echo_reply(5, 1472)), ECHO_LATE)
        self.assertEqual((self.b.echo_late, self.b.echo_bad), (1, 0))

    def test_a_reply_with_ip_options_is_accepted(self):
        self.learn_peer()
        self.echo(6, 20)
        frame = echo_reply(6, 20)
        ip = bytearray(frame[14:34]) + bytes(4)
        ip[0] = 0x46
        ip[2:4] = struct.pack('!H', 24 + 28)
        ip[10:12] = b'\0\0'
        ip[10:12] = struct.pack('!H', checksum(bytes(ip)))
        self.assertEqual(self.rx(frame[:14] + bytes(ip) + frame[34:]), ECHO_REPLY)

    def test_trailing_padding_after_the_datagram_is_ignored(self):
        self.learn_peer()
        self.echo(7, 0)
        self.assertEqual(self.rx(echo_reply(7, 0) + bytes(18)), ECHO_REPLY)

    def test_an_earlier_sequence_is_late(self):
        self.learn_peer()
        self.echo(8, 56)
        self.assertEqual(self.rx(echo_reply(7, 56)), ECHO_LATE)
        self.assertEqual(self.b.echo_done, 0)

    def test_replies_that_fail_a_check_are_bad_with_the_reason(self):
        self.learn_peer()
        good = echo_reply(9, 64)

        def flip(offset):
            frame = bytearray(good)
            frame[offset] ^= 1
            return bytes(frame)
        cases = {
            'ip_length': good[:-1],
            'ip_checksum': flip(14 + 8),                        # TTL
            'fragment': echo_reply(9, 64)[:20] + b'\x20\x00' + good[22:],
            'ip_address': echo_reply(9, 64, src=bytes((10, 42, 88, 3))),
            'mac_address': echo_reply(9, 64, src_mac=bytes.fromhex('020000000009')),
            'icmp_checksum': flip(34 + 8 + 10),                  # payload byte
            'payload_length': echo_reply(9, 63),
            'payload_bytes': echo_reply(9, 64, data=pattern(10, 64)),
        }
        # Recompute the IP checksum for the fragment case so only the flag differs.
        frag = bytearray(cases['fragment'])
        frag[24:26] = b'\0\0'
        frag[24:26] = struct.pack('!H', checksum(bytes(frag[14:34])))
        cases['fragment'] = bytes(frag)
        for reason, frame in cases.items():
            self.echo(9, 64)
            self.assertEqual(self.rx(frame), ECHO_BAD, reason)
            self.assertEqual(self.b.bad, reason.encode(), reason)
            self.assertEqual(self.b.echo_done, 0, reason)
        self.assertEqual(self.b.echo_bad, len(cases))

    def test_other_traffic_is_counted_and_left_alone(self):
        self.learn_peer()
        self.echo(10, 8)
        cases = [
            (echo_reply(10, 8, ident=ID + 1), IPV4_OTHER),           # another pinger
            (MAC + PEER_MAC + b'\x08\x00' + ipv4(PEER_IP, IP, bytes(8), proto=17),
             IPV4_OTHER),                                             # UDP
            (MAC + PEER_MAC + b'\x08\x00' + ipv4(PEER_IP, IP, icmp(8, ID, 10, b'')),
             IPV4_OTHER),                                             # a request
            (MAC + PEER_MAC + b'\x86\xdd' + bytes(40), OTHER),       # IPv6
            (MAC + PEER_MAC + b'\x08\x00' + bytes(10), IPV4_OTHER),  # truncated
            (MAC[:10], OTHER),
        ]
        for frame, kind in cases:
            self.assertEqual(self.rx(frame), kind, frame[12:14])
        self.assertEqual((self.b.ipv4_other, self.b.other, self.b.echo_done), (4, 2, 0))


if __name__ == '__main__':
    unittest.main()
