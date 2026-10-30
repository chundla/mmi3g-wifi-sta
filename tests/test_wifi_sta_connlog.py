"""The phase 7 connection log: which frames become which lines.

Frames are built here as the unit's stack would send or receive them on
en5 or en6. Nothing here shows that io-pkt's BPF hands the unit's own sent
frames to a reader; that is checked on the unit.
"""
import ctypes as C
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWN = bytes.fromhex('782d7e165f11')
ROUTER = bytes.fromhex('9ed175c729d8')
UNIT_IP = bytes([192, 168, 16, 11])
DNS_IP = bytes([192, 168, 16, 1])
FAR_IP = bytes([143, 164, 168, 19])


def qname(name):
    return b''.join(bytes([len(p)]) + p for p in name.encode().split(b'.')) + b'\0'


def ipv4(src, dst, proto, payload, frag=0):
    header = struct.pack('>BBHHHBBH4s4s', 0x45, 0, 20 + len(payload), 1, frag, 64, proto, 0,
                         src, dst)
    return header + payload


def udp(sport, dport, payload):
    return struct.pack('>HHHH', sport, dport, 8 + len(payload), 0) + payload


def tcp(sport, dport, flags):
    return struct.pack('>HHIIBBHHH', sport, dport, 1, 0, 0x50, flags, 8192, 0, 0)


def ether(src, dst, payload, ethertype=0x0800):
    return dst + src + ethertype.to_bytes(2, 'big') + payload


def sent(payload_ip):
    return ether(OWN, ROUTER, payload_ip)


def received(payload_ip):
    return ether(ROUTER, OWN, payload_ip)


def query(name='car.audi-online.com', qid=0x1234, qtype=1):
    return struct.pack('>HHHHHH', qid, 0x0100, 1, 0, 0, 0) + qname(name) \
        + struct.pack('>HH', qtype, 1)


def reply(name='car.audi-online.com', qid=0x1234, rcode=0, answers=()):
    body = struct.pack('>HHHHHH', qid, 0x8180 | rcode, 1, len(answers), 0, 0)
    body += qname(name) + struct.pack('>HH', 1, 1)
    for rtype, rdata in answers:
        body += b'\xc0\x0c' + struct.pack('>HHIH', rtype, 1, 60, len(rdata)) + rdata
    return body


class ConnlogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('cc')
        if not compiler:
            raise RuntimeError('C compiler required for the connection log tests')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        so = Path(cls.tmp.name) / 'connlog.so'
        subprocess.run([compiler, '-std=c99', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        str(ROOT / 'wifi-sta' / 'connlog.c'), '-o', str(so)],
                       check=True, capture_output=True)
        cls.lib = C.CDLL(str(so))
        cls.lib.connlog_event.argtypes = [C.c_char_p, C.c_uint, C.c_char_p, C.c_char_p]
        cls.lib.connlog_payload.argtypes = [C.c_char_p, C.c_uint, C.c_char_p, C.c_char_p,
                                            C.c_uint, C.c_char_p]

    def event(self, frame, caplen=None):
        out = C.create_string_buffer(256)
        n = self.lib.connlog_event(frame, len(frame) if caplen is None else caplen, OWN, out)
        return out.value.decode() if n == 1 else None

    def test_the_units_dns_query_is_logged(self):
        f = sent(ipv4(UNIT_IP, DNS_IP, 17, udp(40000, 53, query())))
        self.assertEqual(self.event(f),
                         'dns-query id=4660 type=1 name=car.audi-online.com to=192.168.16.1')

    def test_a_reply_shows_its_first_address(self):
        answers = [(5, qname('alias.example')), (1, FAR_IP), (1, bytes([1, 2, 3, 4]))]
        f = received(ipv4(DNS_IP, UNIT_IP, 17, udp(53, 40000, reply(answers=answers))))
        self.assertEqual(self.event(f), 'dns-reply id=4660 rcode=0 answers=3 '
                                        'a=143.164.168.19 from=192.168.16.1')

    def test_a_failed_lookup_is_logged_without_an_address(self):
        f = received(ipv4(DNS_IP, UNIT_IP, 17, udp(53, 40000, reply(rcode=3))))
        self.assertEqual(self.event(f),
                         'dns-reply id=4660 rcode=3 answers=0 a=- from=192.168.16.1')

    def test_a_truncated_reply_is_still_logged(self):
        f = received(ipv4(DNS_IP, UNIT_IP, 17, udp(53, 40000, reply(answers=[(1, FAR_IP)]))))
        self.assertEqual(self.event(f, caplen=len(f) - 6),
                         'dns-reply id=4660 rcode=0 answers=1 a=- from=192.168.16.1')

    def test_tcp_connection_attempts_and_answers(self):
        self.assertEqual(self.event(sent(ipv4(UNIT_IP, FAR_IP, 6, tcp(50000, 443, 0x02)))),
                         'tcp-syn to=143.164.168.19:443 from=192.168.16.11:50000')
        self.assertEqual(self.event(received(ipv4(FAR_IP, UNIT_IP, 6, tcp(443, 50000, 0x12)))),
                         'tcp-synack from=143.164.168.19:443')
        self.assertEqual(self.event(received(ipv4(FAR_IP, UNIT_IP, 6, tcp(443, 50000, 0x14)))),
                         'tcp-rst from=143.164.168.19:443')

    def test_other_traffic_is_not_logged(self):
        others = {
            'data segment': sent(ipv4(UNIT_IP, FAR_IP, 6, tcp(50000, 443, 0x18))),
            'inbound syn': received(ipv4(FAR_IP, UNIT_IP, 6, tcp(50000, 22, 0x02))),
            'our syn-ack': sent(ipv4(UNIT_IP, FAR_IP, 6, tcp(22, 50000, 0x12))),
            'other hosts': ether(ROUTER, bytes.fromhex('020000000001'),
                                 ipv4(DNS_IP, FAR_IP, 17, udp(40000, 53, query()))),
            'dns to us': received(ipv4(DNS_IP, UNIT_IP, 17, udp(40000, 53, query()))),
            'non-dns udp': sent(ipv4(UNIT_IP, FAR_IP, 17, udp(40000, 123, bytes(48)))),
            'later fragment': sent(ipv4(UNIT_IP, DNS_IP, 17, udp(40000, 53, query()), frag=1)),
            'arp': ether(OWN, ROUTER, bytes(28), ethertype=0x0806),
            'reply as query': sent(ipv4(UNIT_IP, DNS_IP, 17, udp(40000, 53, reply()))),
        }
        for name, f in others.items():
            self.assertIsNone(self.event(f), name)

    def test_names_are_sanitised_and_bounded(self):
        odd = struct.pack('>HHHHHH', 7, 0x0100, 1, 0, 0, 0) + b'\x04a b\xff\x03com\0' \
            + struct.pack('>HH', 28, 1)
        line = self.event(sent(ipv4(UNIT_IP, DNS_IP, 17, udp(40000, 53, odd))))
        self.assertEqual(line, 'dns-query id=7 type=28 name=a?b?.com to=192.168.16.1')
        long_name = '.'.join(['x' * 60] * 4)
        line = self.event(sent(ipv4(UNIT_IP, DNS_IP, 17, udp(40000, 53, query(long_name)))))
        name = line.split('name=')[1].split(' ')[0]
        self.assertEqual(len(name), 127)
        self.assertTrue(long_name.startswith(name))

    def test_malformed_queries_are_dropped_and_short_frames_not_read_past(self):
        loop = struct.pack('>HHHHHH', 1, 0x0100, 1, 0, 0, 0) + b'\x03abc'
        self.assertIsNone(self.event(sent(ipv4(UNIT_IP, DNS_IP, 17, udp(40000, 53, loop)))))
        f = sent(ipv4(UNIT_IP, DNS_IP, 17, udp(40000, 53, query())))
        for caplen in (13, 33, 41, 45, len(f) - 3):
            self.assertIsNone(self.event(f, caplen=caplen), caplen)


if __name__ == '__main__':
    unittest.main()


def segment(sport, dport, seq, data, flags=0x18):
    return struct.pack('>HHIIBBHHH', sport, dport, seq, 0, 0x50, flags, 8192, 0, 0) + data


class PayloadTests(unittest.TestCase):
    """bpf-check -d: the payload of segments between the unit and one peer port."""
    setUpClass = classmethod(ConnlogTests.setUpClass.__func__)

    def payload(self, frame, caplen=None, peer=FAR_IP, port=80):
        out = C.create_string_buffer(2 * 1600 + 128)
        n = self.lib.connlog_payload(frame, len(frame) if caplen is None else caplen, OWN, peer,
                                     port, out)
        return out.value.decode() if n == 1 else None

    def test_both_directions_in_hex(self):
        req = b'POST /poi HTTP/1.1\r\n'
        f = sent(ipv4(UNIT_IP, FAR_IP, 6, segment(50000, 80, 7, req)))
        self.assertEqual(self.payload(f), 'tcp-data out 192.168.16.11:50000 > 143.164.168.19:80 '
                                          'seq=7 len=%d got=%d hex=%s' % (len(req), len(req), req.hex()))
        f = received(ipv4(FAR_IP, UNIT_IP, 6, segment(80, 50000, 4294967295, b'\x1f\x8b\x00')))
        self.assertEqual(self.payload(f), 'tcp-data in 143.164.168.19:80 > 192.168.16.11:50000 '
                                          'seq=4294967295 len=3 got=3 hex=1f8b00')

    def test_other_peers_ports_and_empty_segments_are_skipped(self):
        data = b'GET / HTTP/1.0\r\n\r\n'
        self.assertIsNone(self.payload(sent(ipv4(UNIT_IP, DNS_IP, 6, segment(50000, 80, 1, data)))))
        self.assertIsNone(self.payload(sent(ipv4(UNIT_IP, FAR_IP, 6, segment(50000, 443, 1, data)))))
        self.assertIsNone(self.payload(received(ipv4(FAR_IP, UNIT_IP, 6, segment(443, 50000, 1, data)))))
        self.assertIsNone(self.payload(sent(ipv4(UNIT_IP, FAR_IP, 6, segment(50000, 80, 1, b'', 0x10)))))
        self.assertIsNone(self.payload(sent(ipv4(UNIT_IP, FAR_IP, 17, udp(50000, 80, data)))))
        other = ether(ROUTER, bytes(6), ipv4(UNIT_IP, FAR_IP, 6, segment(50000, 80, 1, data)))
        self.assertIsNone(self.payload(other))

    def test_a_cut_capture_shows_what_it_has(self):
        f = sent(ipv4(UNIT_IP, FAR_IP, 6, segment(50000, 80, 1, b'abcdef')))
        self.assertEqual(self.payload(f, caplen=len(f) - 2),
                         'tcp-data out 192.168.16.11:50000 > 143.164.168.19:80 seq=1 len=6 got=4 '
                         'hex=61626364')

    def test_a_full_size_segment_fits(self):
        data = bytes(range(256)) * 6
        line = self.payload(sent(ipv4(UNIT_IP, FAR_IP, 6, segment(50000, 80, 1, data[:1460]))))
        self.assertTrue(line.endswith('len=1460 got=1460 hex=' + data[:1460].hex()))
