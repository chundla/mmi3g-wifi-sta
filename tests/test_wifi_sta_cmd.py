"""88W8688 command and event layer against a fake module and station firmware.

The fake firmware answers commands the way libertas expects the SD8688
station image to: a type-1 packet with the command's 0x8000 bit set, the
same sequence number and a result word, announced through the host
interrupt status (function-1 0x05) and a length in 0x42 scaled by the RX
unit. The responses, timings and failures are choices for the tests, not
observations of the chip.
"""
import ctypes as C
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_wifi_sta_host import BLKSZ, CMD_RDY, FLAG, Card, FakeCard, FakeModule, Host, ROOT

OK, EINVAL, ECARD = 0, -4, -3
ECMDREADY, ECMDWRITE, ECMDTIMEOUT, ECMDRX, ECMDRESP, ECMDRESULT = -30, -31, -32, -33, -34, -35
ETXDONE = -36
IOPORT = 0x10000
UPLD, DNLD = 0x01, 0x02
FUNC_INIT, GET_HW_SPEC, MAC_CONTROL, FUNC_SHUTDOWN = 0x00a9, 0x0003, 0x0028, 0x00aa
SCAN, MAC_ADDRESS = 0x0006, 0x004d
ASSOCIATE, DEAUTHENTICATE = 0x0050, 0x0024


class Rx(C.Structure):
    _fields_ = [('status', C.c_uint16), ('pkt_len', C.c_uint16), ('snr', C.c_uint8),
                ('nf', C.c_uint8), ('rate', C.c_uint8), ('priority', C.c_uint8),
                ('pkt_ptr', C.c_uint32), ('frame_len', C.c_uint), ('dst', C.c_uint8 * 6),
                ('src', C.c_uint8 * 6), ('ethertype', C.c_uint16)]


FRAME_CB = C.CFUNCTYPE(None, C.c_void_p, C.POINTER(Rx), C.POINTER(C.c_uint8), C.c_uint)


class Cmd(C.Structure):
    _fields_ = [('card', C.POINTER(Card)), ('ioport', C.c_uint32), ('rx_unit', C.c_uint8),
                ('seq', C.c_uint16), ('step', C.c_char_p), ('io_rc', C.c_int),
                ('out', C.c_uint8 * 256), ('in_', C.c_uint8 * 4096),
                ('resp_cmd', C.c_uint16), ('resp_size', C.c_uint16),
                ('resp_seq', C.c_uint16), ('resp_result', C.c_uint16),
                ('resp_len', C.c_uint), ('resp', C.POINTER(C.c_uint8)),
                ('ready_reads', C.c_uint), ('ready_status', C.c_uint8),
                ('write_tries', C.c_uint), ('aborts', C.c_uint), ('abort_failures', C.c_uint),
                ('polls', C.c_uint), ('rx_len_raw', C.c_uint), ('rx_size', C.c_uint),
                ('rx_chunk', C.c_uint), ('int_seen', C.c_uint8), ('dnld_acks', C.c_uint),
                ('packets', C.c_uint), ('events', C.c_uint), ('data_packets', C.c_uint),
                ('stray_responses', C.c_uint), ('event_n', C.c_uint),
                ('event', C.c_uint32 * 8), ('disconnects', C.c_uint),
                ('disconnect_cause', C.c_uint32), ('mic_ucast', C.c_uint),
                ('mic_mcast', C.c_uint),
                ('data_bytes', C.c_uint), ('data_bad', C.c_uint), ('data_n', C.c_uint),
                ('data', Rx * 8), ('on_frame', FRAME_CB), ('frame_ctx', C.c_void_p),
                ('wait_done', C.c_int), ('tx_frames', C.c_uint), ('tx_bytes', C.c_uint),
                ('tx_blocks', C.c_uint), ('tx_polls', C.c_uint), ('tx', C.c_uint8 * 2048)]


class HwSpec(C.Structure):
    _fields_ = [('hwifversion', C.c_uint16), ('version', C.c_uint16),
                ('nr_txpd', C.c_uint16), ('nr_mcast_adr', C.c_uint16),
                ('mac', C.c_uint8 * 6), ('regioncode', C.c_uint16),
                ('nr_antenna', C.c_uint16), ('fwrelease', C.c_uint32),
                ('wcb_base', C.c_uint32), ('rxpd_rdptr', C.c_uint32),
                ('rxpd_wrptr', C.c_uint32), ('fwcapinfo', C.c_uint32)]


class Bss(C.Structure):
    _fields_ = [('bssid', C.c_uint8 * 6), ('rssi', C.c_uint8), ('interval', C.c_uint16),
                ('capability', C.c_uint16), ('channel', C.c_int), ('ssid_len', C.c_int),
                ('ssid', C.c_uint8 * 32), ('rates_n', C.c_uint), ('rates_supp', C.c_uint),
                ('rates', C.c_uint8 * 14), ('rsn', C.c_int), ('ie_len', C.c_uint),
                ('ie_cut', C.c_int), ('ie', C.c_uint8 * 512)]


def le16(v):
    return v.to_bytes(2, 'little')


def le32(v):
    return v.to_bytes(4, 'little')


HW_SPEC_BODY = (le16(0x0002) + le16(0x0103) + le16(4) + le16(32)
                + bytes((0x02, 0x11, 0x22, 0x33, 0x44, 0x55)) + le16(0x10) + le16(1)
                + le32(0x19050002) + le32(0x80000000) + le32(0x1234) + le32(0x5678)
                + le32(0x00000303))


# An RSN element for WPA2-PSK with CCMP, as wpa_supplicant builds it.
RSN_CCMP = bytes.fromhex('3014 0100 000fac04 0100 000fac04 0100 000fac02 0000')


def packet(kind, payload):
    return le16(len(payload) + 4) + le16(kind) + payload


def bss_descriptor(bssid, rssi, ies, interval=100, capability=0x0411):
    fixed = bssid + bytes((rssi,)) + bytes(range(8)) + le16(interval) + le16(capability)
    return le16(len(fixed) + len(ies)) + fixed + ies


def ie(eid, data):
    return bytes((eid, len(data))) + data


def scan_response(descriptors, sets=None, tsf_type=0x0113, tsf_count=None):
    body = b''.join(descriptors)
    sets = len(descriptors) if sets is None else sets
    tsf_count = sets if tsf_count is None else tsf_count
    tsf = le16(tsf_type) + le16(8 * tsf_count) + bytes(8 * tsf_count)
    return le16(len(body)) + bytes((sets,)) + body + tsf


class StationCard(FakeCard):
    """The card with the station firmware running."""

    def __init__(self, rx_unit=5):
        super().__init__()
        self.rx_unit = rx_unit
        self.mem[1].update({0x40: 0xdc, 0x41: 0xfe, 0x43: rx_unit})
        self.mask = None
        self.int_status = 0
        self.int_writes = []
        self.uploads = []               # packets waiting for the host
        self.received = []              # (addr, blocks, raw) host-to-card writes
        self.status = 0x09
        self.not_ready = 0              # status reads without IO_RDY; -1: never
        self.status_reads = 0
        self.fail_writes = 0            # command writes whose CMD53 times out
        self.aborts = 0
        self.answer = self.respond      # command bytes -> list of packets
        self.reply_after = 0            # status reads before UPLD shows
        self.status_polls = 0
        self.rx_len_override = None
        self.data_dnld = True           # DNLD after a data write
        self.on_data = lambda frame: [] # data bytes after the header -> packets

    def respond(self, cmd):
        command, size, seq = (int.from_bytes(cmd[i:i + 2], 'little') for i in (0, 2, 4))
        body = HW_SPEC_BODY if command == GET_HW_SPEC else cmd[8:size]
        resp = le16(command | 0x8000) + le16(8 + len(body)) + le16(seq) + le16(0) + body
        return [packet(1, resp)]

    def register(self, addr, write, value):
        if addr == 0x03 and write:
            self.aborts += value == 4
            return value
        if addr == 0x04:
            if write:
                self.mask = value
            return self.mask or 0
        if addr == 0x05:
            if write:
                self.int_writes.append(value)
                self.int_status &= value
                return self.int_status
            self.status_polls += 1
            if self.uploads and self.status_polls > self.reply_after:
                self.int_status |= UPLD
            return self.int_status
        if addr == 0x20:
            self.status_reads += 1
            if self.not_ready:
                self.not_ready -= self.not_ready > 0
                return self.status & ~0x08
            return self.status
        if addr == 0x42:
            if self.rx_len_override is not None:
                return self.rx_len_override
            if not self.uploads:
                return 0
            unit = 1 << self.rx_unit
            return (len(self.uploads[0]) + unit - 1) // unit
        return None

    def handle(self, opcode, arg):
        if opcode == 52 and (arg >> 28) & 7 == 1:
            value = self.register((arg >> 9) & 0x1ffff, arg >> 31, arg & 0xff)
            if value is not None:
                self.commands.append((opcode, arg))
                return self.r5_flags << 8 | value
        if opcode == 53 and arg >> 31 and self.fail_writes:
            self.fail_writes -= 1
            self.commands.append((opcode, arg))
            return None
        return super().handle(opcode, arg)

    def receive(self, addr, blocks, raw):
        self.received.append((addr, blocks, raw))
        length = int.from_bytes(raw[:2], 'little')
        kind = int.from_bytes(raw[2:4], 'little')
        if kind == 1 or self.data_dnld:
            self.int_status |= DNLD
        if kind == 1:
            self.status_polls = 0
            self.uploads += self.answer(raw[4:length])
        elif kind == 0:
            self.uploads += self.on_data(raw[4:length])

    def upload(self, length):
        data = self.uploads.pop(0) if self.uploads else b''
        return (data + bytes(length))[:length]


class StationModule(FakeModule):
    def __init__(self, card):
        super().__init__(card)
        self.pending = None
        self.reads = []                 # (arg, length) card-to-host transfers

    def execute(self):
        super().execute()
        w0, w1 = self.cmd_words[-2:]
        arg = ((w0 >> 8) & 0xff) << 24 | ((w0 >> 16) & 0xff) << 16 | (w0 >> 24) << 8 | (w1 & 0xff)
        if (w0 & 0x3f) != 53 or not self.regs.get(FLAG, 0) & CMD_RDY:
            return
        length = (arg & 0x1ff) * self.regs[BLKSZ]
        if arg >> 31:
            self.pending = ((arg >> 9) & 0x1ffff, arg & 0x1ff, length,
                            len(self.card.data_in))
        else:
            self.reads.append((arg, length))
            raw = self.card.upload(length)
            self.data.clear()
            self.data.extend(int.from_bytes(raw[i:i + 4], 'little')
                             for i in range(0, length, 4))

    def wr32(self, ctx, off, value):
        super().wr32(ctx, off, value)
        if off == 0x00 and self.pending:
            addr, blocks, length, start = self.pending
            if len(self.card.data_in) - start >= length:
                self.pending = None
                self.card.receive(addr, blocks, bytes(self.card.data_in[start:start + length]))


class CommandLayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('cc')
        if not compiler:
            raise RuntimeError('C compiler required for the command layer tests')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        so = Path(cls.tmp.name) / 'mv8688cmd.so'
        subprocess.run([compiler, '-std=c99', '-Wall', '-Wextra', '-Werror', '-shared',
                        '-fPIC'] + [str(ROOT / 'wifi-sta' / name) for name in
                                    ('hbsdc.c', 'sdio.c', 'mv8688fw.c', 'mv8688cmd.c')]
                       + ['-o', str(so)], check=True, capture_output=True)
        lib = cls.lib = C.CDLL(str(so))
        lib.hbsdc_identify.argtypes = [C.POINTER(Host)]
        lib.sdio_attach.argtypes = [C.POINTER(Card), C.c_uint16, C.c_uint16]
        lib.sdio_set_blksz.argtypes = [C.POINTER(Card), C.c_uint, C.c_uint]
        lib.mv8688_cmd_start.argtypes = [C.POINTER(Cmd)]
        lib.mv8688_cmd_stop.argtypes = [C.POINTER(Cmd)]
        lib.mv8688_command.argtypes = [C.POINTER(Cmd), C.c_uint16, C.c_char_p, C.c_uint,
                                       C.c_uint]
        lib.mv8688_parse_hw_spec.argtypes = [C.POINTER(Cmd), C.POINTER(HwSpec)]
        lib.mv8688_parse_mac.argtypes = [C.c_char_p, C.c_uint8 * 6]
        lib.mv8688_mac_body.argtypes = [C.c_uint8 * 8, C.c_uint, C.c_uint8 * 6]
        lib.mv8688_mac_body.restype = None
        lib.mv8688_resp_mac.argtypes = [C.POINTER(Cmd), C.c_uint8 * 6]
        lib.mv8688_scan_body.argtypes = [C.c_uint8 * 100, C.c_char_p, C.c_uint, C.c_uint,
                                         C.c_char_p, C.c_uint]
        lib.mv8688_scan_body.restype = C.c_uint
        lib.mv8688_parse_scan.argtypes = [C.POINTER(Cmd), C.POINTER(Bss), C.c_uint,
                                          C.POINTER(C.c_uint)]
        lib.mv8688_wait.argtypes = [C.POINTER(Cmd), C.c_uint]
        lib.mv8688_parse_rxpd.argtypes = [C.c_char_p, C.c_uint, C.POINTER(Rx)]
        lib.mv8688_send_data.argtypes = [C.POINTER(Cmd), C.c_char_p, C.c_uint, C.c_uint]
        for name, size in (('mv8688_wep_remove_body', 72), ):
            getattr(lib, name).argtypes = [C.c_uint8 * size]
            getattr(lib, name).restype = None
        lib.mv8688_action_body.argtypes = [C.c_uint8 * 4, C.c_uint, C.c_uint]
        lib.mv8688_action_body.restype = None
        lib.mv8688_key_clear_body.argtypes = [C.c_uint8 * 44, C.c_uint]
        lib.mv8688_key_clear_body.restype = None
        lib.mv8688_auth_body.argtypes = [C.c_uint8 * 17, C.c_char_p]
        lib.mv8688_auth_body.restype = None
        lib.mv8688_deauth_body.argtypes = [C.c_uint8 * 8, C.c_char_p, C.c_uint]
        lib.mv8688_deauth_body.restype = None
        lib.mv8688_assoc_body.argtypes = [C.c_uint8 * 228, C.POINTER(Bss)]
        lib.mv8688_assoc_body.restype = C.c_uint
        lib.mv8688_parse_assoc.argtypes = [C.POINTER(Cmd)] + [C.POINTER(C.c_uint16)] * 3
        lib.mv8688_key_body.argtypes = [C.c_uint8 * 44, C.c_uint, C.c_int, C.c_char_p,
                                        C.c_uint]
        lib.mv8688_wpa_ie_check.argtypes = [C.c_char_p, C.c_uint]
        lib.mv8688_assoc_wpa_body.argtypes = [C.c_uint8 * 228, C.POINTER(Bss), C.c_char_p,
                                              C.c_uint]
        lib.mv8688_assoc_wpa_body.restype = C.c_uint

    def setUp(self):
        self.prepare()

    def prepare(self, rx_unit=5):
        self.card_model = StationCard(rx_unit)
        self.fpga = StationModule(self.card_model)
        self.host = Host()
        self.host.io = C.pointer(self.fpga.io)
        self.assertEqual(self.lib.hbsdc_identify(C.byref(self.host)), OK)
        self.card = Card()
        self.card.host = C.pointer(self.host)
        self.assertEqual(self.lib.sdio_attach(C.byref(self.card), 0x02df, 0x9103), OK)
        self.assertEqual(self.lib.sdio_set_blksz(C.byref(self.card), 1, 256), OK)
        self.cmd = Cmd()
        self.cmd.card = C.pointer(self.card)
        self.cmd.ioport = IOPORT
        self.cmd.rx_unit = rx_unit
        self.assertEqual(self.lib.mv8688_cmd_start(C.byref(self.cmd)), OK)
        self.card_model.commands.clear()
        self.fpga.delays.clear()

    def send(self, command, body=b'', timeout=5000):
        return self.lib.mv8688_command(C.byref(self.cmd), command, body, len(body), timeout)

    def cmd53(self, write):
        return [arg for op, arg in self.card_model.commands
                if op == 53 and bool(arg >> 31) == write]

    def test_start_unmasks_all_four_causes_and_stop_masks_them(self):
        self.assertEqual(self.card_model.mask, 0x0f)
        self.assertEqual(self.lib.mv8688_cmd_stop(C.byref(self.cmd)), OK)
        self.assertEqual(self.card_model.mask, 0)

    def test_a_command_is_framed_and_its_response_matched(self):
        self.assertEqual(self.send(FUNC_INIT), OK)
        addr, blocks, raw = self.card_model.received[0]
        self.assertEqual((addr, blocks, len(raw)), (IOPORT, 1, 256))
        # SDIO header {length, type 1}, then command, size, seqnum, result.
        self.assertEqual(raw[:12], bytes.fromhex('0c000100a900080001000000'))
        self.assertEqual(raw[12:], bytes(244))
        # Block mode at the fixed I/O port, function 1, one block each way.
        self.assertEqual(self.cmd53(True), [0x80000000 | 1 << 28 | 1 << 27 | IOPORT << 9 | 1])
        self.assertEqual(self.cmd53(False), [1 << 28 | 1 << 27 | IOPORT << 9 | 1])
        self.assertEqual((self.cmd.resp_cmd, self.cmd.resp_size, self.cmd.resp_seq,
                          self.cmd.resp_result, self.cmd.resp_len), (0x80a9, 8, 1, 0, 8))
        # 12 bytes in 32-byte units: one unit.
        self.assertEqual((self.cmd.rx_len_raw, self.cmd.rx_size, self.cmd.rx_chunk), (1, 32, 12))
        self.assertEqual(self.cmd.packets, 1)
        self.assertEqual(self.cmd.int_seen, UPLD | DNLD)
        self.assertEqual(self.cmd.dnld_acks, 1)

    def test_sequence_numbers_count_up_per_command(self):
        for expected in (1, 2, 3):
            self.assertEqual(self.send(FUNC_INIT), OK)
            self.assertEqual(self.cmd.resp_seq, expected)
        seqs = [int.from_bytes(raw[8:10], 'little') for _, _, raw in self.card_model.received]
        self.assertEqual(seqs, [1, 2, 3])

    def test_the_interrupt_status_is_cleared_by_writing_its_complement(self):
        self.assertEqual(self.send(FUNC_INIT), OK)
        self.assertEqual(self.card_model.int_writes, [0xff & ~(UPLD | DNLD)])
        self.assertEqual(self.card_model.int_status, 0)

    def test_get_hw_spec_body_is_sent_and_its_response_decoded(self):
        body = bytes(8) + b'\xff' * 6 + bytes(24)
        self.assertEqual(self.send(GET_HW_SPEC, body), OK)
        raw = self.card_model.received[0][2]
        self.assertEqual(raw[4:8], bytes.fromhex('03002e00'))
        self.assertEqual(raw[12:12 + 38], body)
        spec = HwSpec()
        self.assertEqual(self.lib.mv8688_parse_hw_spec(C.byref(self.cmd), C.byref(spec)), OK)
        self.assertEqual((spec.hwifversion, spec.version, spec.nr_txpd, spec.nr_mcast_adr),
                         (2, 0x0103, 4, 32))
        self.assertEqual(bytes(spec.mac), bytes((0x02, 0x11, 0x22, 0x33, 0x44, 0x55)))
        self.assertEqual((spec.regioncode, spec.nr_antenna, spec.fwrelease, spec.fwcapinfo),
                         (0x10, 1, 0x19050002, 0x303))
        self.assertEqual((spec.wcb_base, spec.rxpd_rdptr, spec.rxpd_wrptr),
                         (0x80000000, 0x1234, 0x5678))

    def test_a_short_hw_spec_response_is_rejected_by_the_parser(self):
        self.assertEqual(self.send(FUNC_INIT), OK)
        spec = HwSpec()
        self.assertEqual(self.lib.mv8688_parse_hw_spec(C.byref(self.cmd), C.byref(spec)),
                         ECMDRESP)

    def test_events_and_frames_before_the_response_are_recorded(self):
        normal = self.card_model.respond
        self.card_model.answer = lambda cmd: ([packet(3, le32(0x2c)), packet(0, bytes(60)),
                                               packet(3, le32(0x0a))] + normal(cmd))
        self.assertEqual(self.send(FUNC_INIT), OK)
        self.assertEqual((self.cmd.events, self.cmd.data_packets, self.cmd.packets), (2, 1, 4))
        self.assertEqual(list(self.cmd.event[:self.cmd.event_n]), [0x2c, 0x0a])
        self.assertEqual(self.cmd.resp_cmd, 0x80a9)
        # Each packet was read straight after the last; no wait between them.
        self.assertEqual(self.fpga.delays, [])

    def test_a_late_response_is_polled_1_ms_apart(self):
        self.card_model.reply_after = 7
        self.assertEqual(self.send(FUNC_INIT), OK)
        self.assertEqual(self.cmd.polls, 8)
        self.assertEqual(self.fpga.delays, [1] * 7)

    def test_no_response_times_out(self):
        self.card_model.answer = lambda cmd: []
        self.assertEqual(self.send(FUNC_INIT, timeout=50), ECMDTIMEOUT)
        self.assertEqual((self.cmd.step, self.cmd.polls), (b'resp_timeout', 50))
        self.assertEqual(self.fpga.delays, [1] * 49)

    def test_a_stream_of_events_still_times_out(self):
        self.card_model.answer = lambda cmd: [packet(3, le32(1))] * 100
        self.assertEqual(self.send(FUNC_INIT, timeout=20), ECMDTIMEOUT)
        self.assertEqual(self.cmd.events, 20)

    def test_the_response_timeout_is_measured_on_the_clock(self):
        # Without the module interrupt each CMD52 completes only after a
        # 10 ms wait times out, so polls cost far more than 1 ms each.
        self.card_model.answer = lambda cmd: []
        self.fpga.irq_line = False
        start = self.fpga.clock_ms
        self.assertEqual(self.send(FUNC_INIT, timeout=5000), ECMDTIMEOUT)
        self.assertEqual(self.cmd.step, b'resp_timeout')
        self.assertLess(self.cmd.polls, 1000)
        self.assertLess(self.fpga.clock_ms - start, 5200)

    def test_the_clock_may_wrap(self):
        self.card_model.answer = lambda cmd: []
        self.fpga.clock_ms = 0xffffffff - 10
        self.assertEqual(self.send(FUNC_INIT, timeout=50), ECMDTIMEOUT)
        self.assertEqual(self.cmd.polls, 50)

    def test_a_response_to_another_command_fails(self):
        self.card_model.answer = lambda cmd: self.card_model.respond(
            le16(GET_HW_SPEC) + cmd[2:])
        self.assertEqual(self.send(FUNC_INIT), ECMDRESP)
        self.assertEqual((self.cmd.step, self.cmd.stray_responses), (b'resp_command', 1))

    def test_a_response_without_the_response_bit_fails(self):
        self.card_model.answer = lambda cmd: [packet(1, cmd[:8])]
        self.assertEqual(self.send(FUNC_INIT), ECMDRESP)
        self.assertEqual(self.cmd.step, b'resp_command')

    def test_a_response_with_another_sequence_number_fails(self):
        self.card_model.answer = lambda cmd: self.card_model.respond(cmd[:4] + le16(9) + cmd[6:])
        self.assertEqual(self.send(FUNC_INIT), ECMDRESP)
        self.assertEqual((self.cmd.step, self.cmd.resp_seq), (b'resp_seq', 9))

    def test_a_response_size_beyond_the_packet_fails(self):
        self.card_model.answer = lambda cmd: [packet(1, le16(0x80a9) + le16(40) + cmd[4:8])]
        self.assertEqual(self.send(FUNC_INIT), ECMDRESP)
        self.assertEqual(self.cmd.step, b'resp_size')

    def test_a_non_zero_result_fails_and_keeps_the_response(self):
        self.card_model.answer = lambda cmd: [packet(1, le16(0x80a9) + le16(8) + cmd[4:6]
                                                     + le16(2))]
        self.assertEqual(self.send(FUNC_INIT), ECMDRESULT)
        self.assertEqual((self.cmd.step, self.cmd.resp_result), (b'resp_result', 2))
        self.assertTrue(self.cmd.resp)

    def test_an_unknown_packet_type_fails(self):
        self.card_model.answer = lambda cmd: [packet(7, bytes(8))]
        self.assertEqual(self.send(FUNC_INIT), ECMDRX)
        self.assertEqual(self.cmd.step, b'rx_type')

    def test_an_invalid_rx_length_fails_before_reading(self):
        self.card_model.rx_len_override = 0
        self.assertEqual(self.send(FUNC_INIT), ECMDRX)
        self.assertEqual(self.cmd.step, b'rx_len')
        self.assertEqual(self.cmd53(False), [])
        self.prepare()
        self.card_model.rx_len_override = 129      # 129 << 5 = 4128 > 4096
        self.assertEqual(self.send(FUNC_INIT), ECMDRX)
        self.assertEqual((self.cmd.step, self.cmd.rx_size), (b'rx_len', 4128))

    def test_a_packet_header_longer_than_the_transfer_fails(self):
        self.card_model.answer = lambda cmd: [le16(200) + le16(1) + bytes(8)]
        self.assertEqual(self.send(FUNC_INIT), ECMDRX)
        self.assertEqual((self.cmd.step, self.cmd.rx_size, self.cmd.rx_chunk),
                         (b'rx_header', 32, 200))

    def test_a_long_response_is_read_in_whole_blocks(self):
        self.card_model.answer = lambda cmd: [packet(1, le16(0x80a9) + le16(300) + cmd[4:8]
                                                     + bytes(292))]
        self.assertEqual(self.send(FUNC_INIT), OK)
        self.assertEqual(self.fpga.reads[0][1], 512)
        self.assertEqual(self.cmd53(False)[0] & 0x1ff, 2)
        self.assertEqual((self.cmd.rx_size, self.cmd.rx_chunk, self.cmd.resp_len),
                         (320, 304, 300))

    def test_rx_unit_zero_uses_the_length_register_as_bytes(self):
        self.prepare(rx_unit=0)
        self.assertEqual(self.send(FUNC_INIT), OK)
        self.assertEqual((self.cmd.rx_len_raw, self.cmd.rx_size), (12, 12))

    def test_io_ready_is_waited_for_1_ms_apart(self):
        self.card_model.not_ready = 3
        self.assertEqual(self.send(FUNC_INIT), OK)
        self.assertEqual(self.fpga.delays[:3], [1, 1, 1])
        self.assertEqual(self.cmd.ready_reads, 1)   # the read before the response

    def test_io_ready_that_never_comes_fails_without_writing(self):
        self.card_model.not_ready = -1
        self.assertEqual(self.send(FUNC_INIT), ECMDREADY)
        self.assertEqual((self.cmd.step, self.cmd.ready_reads), (b'io_ready', 1000))
        self.assertEqual(self.card_model.status_reads, 1000)
        self.assertEqual(self.cmd53(True), [])

    def test_the_io_ready_wait_is_measured_on_the_clock(self):
        self.card_model.not_ready = -1
        self.fpga.irq_line = False
        start = self.fpga.clock_ms
        self.assertEqual(self.send(FUNC_INIT), ECMDREADY)
        self.assertLess(self.cmd.ready_reads, 200)
        self.assertLess(self.fpga.clock_ms - start, 1100)
        self.assertEqual(self.cmd53(True), [])

    def test_a_failed_write_is_terminated_and_tried_again(self):
        self.card_model.fail_writes = 1
        self.assertEqual(self.send(FUNC_INIT), OK)
        self.assertEqual((self.cmd.write_tries, self.cmd.aborts), (2, 1))
        self.assertEqual(self.card_model.aborts, 1)
        self.assertEqual(len(self.card_model.received), 1)

    def test_three_failed_writes_fail(self):
        self.card_model.fail_writes = 3
        self.assertEqual(self.send(FUNC_INIT), ECMDWRITE)
        self.assertEqual((self.cmd.step, self.cmd.write_tries, self.cmd.aborts),
                         (b'cmd_write', 3, 3))
        self.assertEqual(self.cmd.io_rc, ECARD)
        self.assertEqual(self.card_model.received, [])

    def test_a_command_the_factory_would_send_in_two_blocks_is_refused(self):
        # The factory sends (length + 259) / 256 blocks: two from 253 bytes.
        self.assertEqual(self.send(FUNC_INIT, bytes(241)), EINVAL)
        self.assertEqual(self.cmd.step, b'cmd_size')
        self.assertEqual(self.card_model.commands, [])
        self.assertEqual(self.send(FUNC_INIT, bytes(240)), OK)
        self.assertEqual(self.card_model.received[0][1], 1)
        self.assertEqual(self.card_model.received[0][2][:2], le16(252))


    # --- phase 4.4: MAC address and passive scan ------------------------------
    def parse_mac(self, text):
        mac = (C.c_uint8 * 6)()
        return self.lib.mv8688_parse_mac(text.encode(), mac), bytes(mac)

    def test_a_station_mac_is_parsed_in_either_case(self):
        self.assertEqual(self.parse_mac('00:1c:d7:0a:Bc:FE'),
                         (0, bytes.fromhex('001cd70abcfe')))

    def test_malformed_mac_text_is_refused(self):
        for text in ('', '00:1c:d7:0a:bc', '00:1c:d7:0a:bc:fe:', '00-1c-d7-0a-bc-fe',
                     '001cd70abcfe', '00:1c:d7:0a:bc:f', '00:1c:d7:0a:bc:fg', ' 0:1c:d7:0a:bc:fe'):
            self.assertEqual(self.parse_mac(text)[0], -1, text)

    def test_addresses_no_station_may_use_are_refused(self):
        for text in ('00:00:00:00:00:00', 'aa:bb:cc:dd:ee:ff', 'AA:BB:CC:DD:EE:FF',
                     'ff:ff:ff:ff:ff:ff', '01:00:5e:00:00:01', '03:1c:d7:0a:bc:fe'):
            self.assertEqual(self.parse_mac(text)[0], -2, text)

    def test_the_mac_address_command_carries_action_and_address(self):
        mac = (C.c_uint8 * 6)(*bytes.fromhex('001cd70abcfe'))
        body = (C.c_uint8 * 8)()
        self.lib.mv8688_mac_body(body, 1, mac)
        self.assertEqual(bytes(body), bytes.fromhex('0100001cd70abcfe'))
        self.assertEqual(self.send(MAC_ADDRESS, bytes(body)), OK)
        raw = self.card_model.received[0][2]
        # 4 + 8 + 8 bytes; size 16 as libertas' struct cmd_ds_802_11_mac_address.
        self.assertEqual(raw[:20], bytes.fromhex('140001004d001000010000000100001cd70abcfe'))
        got = (C.c_uint8 * 6)()
        self.assertEqual(self.lib.mv8688_resp_mac(C.byref(self.cmd), got), OK)
        self.assertEqual(bytes(got), bytes.fromhex('001cd70abcfe'))

    def test_a_short_mac_address_response_is_rejected(self):
        self.assertEqual(self.send(MAC_ADDRESS, bytes.fromhex('0000')), OK)
        got = (C.c_uint8 * 6)()
        self.assertEqual(self.lib.mv8688_resp_mac(C.byref(self.cmd), got), ECMDRESP)

    def scan_body(self, channels, dwell=200, ssid=None, ssid_len=None):
        body = (C.c_uint8 * 100)()
        n = self.lib.mv8688_scan_body(body, bytes(channels), len(channels), dwell, ssid,
                                      len(ssid or b'') if ssid_len is None else ssid_len)
        return bytes(body)[:n]

    def test_an_ssid_scan_body_matches_libertas_connect_scan(self):
        body = self.scan_body([1, 2, 3, 4], dwell=40, ssid=b'mmi-bench')
        self.assertEqual(body[:7], bytes.fromhex('03000000000000'))
        self.assertEqual(body[7:20], le16(0x0000) + le16(9) + b'mmi-bench')
        self.assertEqual(body[20:24], le16(0x0101) + le16(28))
        # Active (mode 0), maximum 40 ms.
        self.assertEqual(body[24:31], bytes.fromhex('00 01 00 0000 2800'))
        self.assertTrue(all(body[24 + 7 * i + 2] == 0 for i in range(4)))
        self.assertEqual(len(body), 55 + 13)
        long = self.scan_body([1, 2, 3, 4], ssid=b'x' * 32)
        self.assertEqual(len(long), 55 + 36)
        self.assertEqual(self.send(SCAN, long), OK)
        for bad in (b'', b'x' * 33):
            self.assertEqual(self.scan_body([1], ssid=bad), b'')

    def test_a_passive_scan_body_matches_libertas_without_an_ssid(self):
        body = self.scan_body([1, 2, 3, 4])
        self.assertEqual(body[:7], bytes.fromhex('03000000000000'))      # any BSS
        self.assertEqual(body[7:11], le16(0x0101) + le16(28))
        # Radio type B/G, channel 1, passive, minimum 0, maximum 200 ms.
        self.assertEqual(body[11:18], bytes.fromhex('00 01 01 0000 c800'))
        self.assertEqual([body[11 + 7 * i + 1] for i in range(4)], [1, 2, 3, 4])
        self.assertTrue(all(body[11 + 7 * i + 2] == 1 for i in range(4)))
        self.assertEqual(body[39:], le16(0x0001) + le16(12)
                         + bytes.fromhex('82848b960c121824304860 6c'))
        self.assertEqual(len(body), 55)
        # Framed: 4 + 8 + 55 bytes, one block.
        self.assertEqual(self.send(SCAN, body), OK)
        self.assertEqual(self.card_model.received[0][2][:2], le16(67))

    def test_a_scan_of_no_or_too_many_channels_is_refused(self):
        self.assertEqual(self.scan_body([]), b'')
        self.assertEqual(self.scan_body([1, 2, 3, 4, 5]), b'')
        self.assertEqual(len(self.scan_body([9, 10, 11])), 48)

    def parse_scan(self, response, max_bss=8):
        self.card_model.answer = lambda cmd: [packet(1, le16(0x8006) + le16(8 + len(response))
                                                     + cmd[4:6] + le16(0) + response)]
        self.assertEqual(self.send(SCAN, self.scan_body([1])), OK)
        bss = (Bss * max_bss)()
        sets = C.c_uint(99)
        rc = self.lib.mv8688_parse_scan(C.byref(self.cmd), bss, max_bss, C.byref(sets))
        return rc, sets.value, bss

    def test_scan_descriptors_are_decoded(self):
        first = bss_descriptor(bytes.fromhex('020000000001'), 60,
                               ie(0, b'Hotspot') + ie(1, b'\x82\x84') + ie(3, b'\x06'))
        second = bss_descriptor(bytes.fromhex('020000000002'), 80, ie(0, b''),
                                interval=200, capability=0x0401)
        rc, sets, bss = self.parse_scan(scan_response([first, second]))
        self.assertEqual((rc, sets), (OK, 2))
        self.assertEqual(bytes(bss[0].bssid), bytes.fromhex('020000000001'))
        self.assertEqual((bss[0].rssi, bss[0].interval, bss[0].capability, bss[0].channel,
                          bss[0].ssid_len), (60, 100, 0x0411, 6, 7))
        self.assertEqual(bytes(bss[0].ssid)[:7], b'Hotspot')
        self.assertEqual((bss[1].rssi, bss[1].interval, bss[1].capability, bss[1].channel,
                          bss[1].ssid_len), (80, 200, 0x0401, -1, 0))

    def test_an_empty_scan_is_not_an_error(self):
        self.assertEqual(self.parse_scan(le16(0) + b'\x00')[:2], (OK, 0))

    def test_a_long_ssid_is_cut_to_32_bytes_and_its_length_kept(self):
        rc, sets, bss = self.parse_scan(scan_response(
            [bss_descriptor(bytes(6), 50, ie(0, bytes(range(65, 105))))]))
        self.assertEqual((rc, bss[0].ssid_len), (OK, 40))
        self.assertEqual(bytes(bss[0].ssid), bytes(range(65, 97)))

    def test_descriptors_beyond_the_limit_are_checked_but_not_kept(self):
        good = bss_descriptor(bytes(6), 50, ie(3, b'\x01'))
        rc, sets, bss = self.parse_scan(scan_response([good, good, good]), max_bss=1)
        self.assertEqual((rc, sets, bss[0].channel), (OK, 3, 1))
        bad = le16(21) + bytes(19) + b'\x00\x20'      # element runs past the descriptor
        self.assertEqual(self.parse_scan(scan_response([good, bad]), max_bss=1)[0], ECMDRESP)

    def test_malformed_scan_responses_are_rejected(self):
        good = bss_descriptor(bytes(6), 50, ie(0, b'x'))
        cases = {
            'tsf type': scan_response([good], tsf_type=0x0112),
            'tsf count': scan_response([good], tsf_count=2),
            'sets beyond the descriptors': scan_response([good], sets=2, tsf_count=2),
            'descriptor too short': scan_response([le16(18) + bytes(18)]),
            'element overrun': scan_response([bss_descriptor(bytes(6), 50, b'\x00\x05ab')]),
            'no tsf': le16(len(good)) + b'\x01' + good,
            'header only': le16(0),
        }
        for name, response in cases.items():
            self.assertEqual(self.parse_scan(response)[0], ECMDRESP, name)


    # --- phase 5.0: open association ------------------------------------

    def test_scan_keeps_supported_then_extended_rates_and_notes_rsn(self):
        ies = (ie(50, bytes.fromhex('30486c')) + ie(0, b'bench')
               + ie(1, bytes.fromhex('82848b960c121824')) + ie(3, b'\x01'))
        rsn = bss_descriptor(bytes(6), 50, ie(0, b'x') + ie(48, bytes(20)))
        wpa = bss_descriptor(bytes(6), 50, ie(221, bytes.fromhex('0050f20101')))
        other = bss_descriptor(bytes(6), 50, ie(221, bytes.fromhex('0050f20201')))
        rc, sets, bss = self.parse_scan(scan_response(
            [bss_descriptor(bytes(6), 50, ies), rsn, wpa, other]))
        self.assertEqual((rc, sets), (OK, 4))
        self.assertEqual((bss[0].rates_n, bss[0].rates_supp), (11, 8))
        self.assertEqual(bytes(bss[0].rates)[:11], bytes.fromhex('82848b960c121824 30486c'))
        self.assertEqual([b.rsn for b in bss[:4]], [0, 1, 1, 0])

    def test_a_rates_element_longer_than_libertas_accepts_is_skipped(self):
        ies = ie(1, bytes(range(2, 17))) + ie(50, bytes.fromhex('6c'))
        rc, _, bss = self.parse_scan(scan_response([bss_descriptor(bytes(6), 50, ies)]))
        self.assertEqual((rc, bss[0].rates_n, bss[0].rates_supp), (OK, 1, 0))

    def open_bss(self, ssid=b'bench', channel=1, supp=bytes.fromhex('82848b960c121824'),
                 ext=bytes.fromhex('30486c60'), capability=0x0401):
        b = Bss()
        b.bssid[:] = list(bytes.fromhex('020000aabbcc'))
        b.capability = capability
        b.channel = channel
        b.ssid_len = len(ssid)
        b.ssid[:len(ssid[:32])] = list(ssid[:32])
        rates = (supp + ext)[:14]
        b.rates[:len(rates)] = list(rates)
        b.rates_n, b.rates_supp = len(rates), min(len(supp), 14)
        return b

    def assoc_body(self, b):
        body = (C.c_uint8 * 228)()
        n = self.lib.mv8688_assoc_body(body, C.byref(b))
        return bytes(body)[:n]

    def test_the_associate_body_matches_libertas_for_an_open_bss(self):
        body = self.assoc_body(self.open_bss())
        self.assertEqual(body[:13], bytes.fromhex('020000aabbcc 0104 0a00 0000 00'))
        tlvs = body[13:]
        self.assertEqual(tlvs[:9], le16(0x0000) + le16(5) + b'bench')
        self.assertEqual(tlvs[9:14], bytes.fromhex('0300 0100 01'))
        self.assertEqual(tlvs[14:24], bytes.fromhex('0400 0600') + bytes(6))
        # 54 Mb/s (0x6c) goes after 48 (0x60), the hardware table's order.
        self.assertEqual(tlvs[24:40], bytes.fromhex('0100 0c00 82848b960c121824 3048606c'))
        # Open system, as a 16-bit value with a 2-byte length.
        self.assertEqual(tlvs[40:], bytes.fromhex('1f01 0200 0000'))

    def test_associate_rates_follow_the_hardware_order_within_each_element(self):
        # 22 Mb/s (0x2c) is not a rate this firmware's table has.
        b = self.open_bss(supp=bytes.fromhex('0c2c96828b'), ext=bytes.fromhex('6c12'))
        tlv = self.assoc_body(b)[13 + 9 + 5 + 10:]
        self.assertEqual(tlv[:4], le16(0x0001) + le16(6))
        self.assertEqual(tlv[4:10], bytes.fromhex('828b960c 126c'))

    def test_a_bss_without_a_rates_element_gets_the_11b_fallback(self):
        b = self.open_bss(supp=b'', ext=b'')
        tlv = self.assoc_body(b)[13 + 9 + 5 + 10:]
        self.assertEqual(tlv[:8], bytes.fromhex('0100 0400 82848b96'))

    def test_a_bss_without_an_ssid_or_channel_gets_no_body(self):
        for b in (self.open_bss(ssid=b''), self.open_bss(channel=-1),
                  self.open_bss(channel=15)):
            self.assertEqual(self.assoc_body(b), b'')
        long = self.open_bss(ssid=b'y' * 32)
        long.ssid_len = 33
        self.assertEqual(self.assoc_body(long), b'')

    def test_the_largest_associate_command_still_fits_one_block(self):
        b = self.open_bss(ssid=b'z' * 32, supp=bytes.fromhex('82848b960c121824'),
                          ext=bytes.fromhex('30486c608284'))
        body = self.assoc_body(b)
        self.assertEqual(len(body), 13 + 36 + 5 + 10 + 4 + 14 + 6)
        self.assertEqual(self.send(ASSOCIATE, body), OK)
        self.assertEqual(self.card_model.received[0][1], 1)

    def answer_assoc(self, status, aid=0xc001, extra=b'', code=0x8012):
        def answer(cmd):
            body = le16(0x0401) + le16(status) + le16(aid) + extra
            return [packet(1, le16(code) + le16(8 + len(body)) + cmd[4:6] + le16(0) + body)]
        self.card_model.answer = answer

    def test_associate_is_answered_with_0x8012_or_its_own_code(self):
        # The 5.0 run on the unit: 0x8012, result 0, status 0, AID 0xc001.
        for code in (0x8012, 0x8050):
            self.answer_assoc(0, code=code)
            self.assertEqual(self.send(ASSOCIATE, self.assoc_body(self.open_bss())), OK)
            self.assertEqual(self.cmd.resp_cmd, code)

    def test_0x8012_answers_no_other_command(self):
        self.answer_assoc(0)
        self.assertEqual(self.send(FUNC_INIT), ECMDRESP)
        self.assertEqual(self.cmd.step, b'resp_command')

    def parse_assoc(self):
        cap, status, aid = C.c_uint16(), C.c_uint16(), C.c_uint16()
        rc = self.lib.mv8688_parse_assoc(C.byref(self.cmd), C.byref(cap), C.byref(status),
                                         C.byref(aid))
        return rc, cap.value, status.value, aid.value

    def test_an_associate_response_is_decoded(self):
        self.answer_assoc(0, extra=ie(1, b'\x82'))
        self.assertEqual(self.send(ASSOCIATE, self.assoc_body(self.open_bss())), OK)
        self.assertEqual(self.parse_assoc(), (OK, 0x0401, 0, 0xc001))
        self.answer_assoc(3)
        self.assertEqual(self.send(ASSOCIATE, self.assoc_body(self.open_bss())), OK)
        self.assertEqual(self.parse_assoc()[2], 3)

    def test_a_short_associate_response_is_rejected(self):
        self.assertEqual(self.send(FUNC_INIT), OK)
        self.assertEqual(self.parse_assoc()[0], ECMDRESP)

    def test_the_connect_command_bodies_match_libertas(self):
        wep = (C.c_uint8 * 72)()
        self.lib.mv8688_wep_remove_body(wep)
        self.assertEqual(bytes(wep), le16(4) + bytes(70))
        key = (C.c_uint8 * 44)()
        self.lib.mv8688_key_clear_body(key, 2)
        # Action SET, key TLV 0x0100 of 38 bytes: WEP type, unicast, no key.
        self.assertEqual(bytes(key), bytes.fromhex('0100 0001 2600 0000 0200 0000') + bytes(32))
        self.lib.mv8688_key_clear_body(key, 1)
        self.assertEqual(bytes(key)[8:10], le16(1))
        auth = (C.c_uint8 * 17)()
        self.lib.mv8688_auth_body(auth, bytes.fromhex('020000aabbcc'))
        self.assertEqual(bytes(auth), bytes.fromhex('020000aabbcc 00') + bytes(10))
        deauth = (C.c_uint8 * 8)()
        self.lib.mv8688_deauth_body(deauth, bytes.fromhex('020000aabbcc'), 3)
        self.assertEqual(bytes(deauth), bytes.fromhex('020000aabbcc 0300'))
        action = (C.c_uint8 * 4)()
        self.lib.mv8688_action_body(action, 1, 0)
        self.assertEqual(bytes(action), bytes.fromhex('0100 0000'))

    # --- phase 6: what a supplicant needs -------------------------------

    def test_scan_keeps_each_bss_elements_from_the_ssid_on(self):
        ies = ie(0, b'bench') + ie(1, b'\x82') + ie(48, RSN_CCMP[2:]) + ie(221, b'\x00\x50\xf2\x02')
        rc, _, bss = self.parse_scan(scan_response([bss_descriptor(bytes(6), 50, ies)]))
        self.assertEqual((rc, bss[0].ie_len, bss[0].ie_cut), (OK, len(ies), 0))
        self.assertEqual(bytes(bss[0].ie)[:len(ies)], ies)

    def test_elements_beyond_the_buffer_are_cut_at_an_element_boundary(self):
        ies = ie(0, b'x') + b''.join(ie(221, bytes(200)) for _ in range(3))
        rc, _, bss = self.parse_scan(scan_response([bss_descriptor(bytes(6), 50, ies)]))
        # SSID (3) and two 202-byte elements fit 512; the third does not.
        self.assertEqual((rc, bss[0].ie_len, bss[0].ie_cut), (OK, 3 + 2 * 202, 1))
        self.assertEqual(bytes(bss[0].ie)[:bss[0].ie_len], ies[:bss[0].ie_len])

    def key_body(self, key_type, pairwise, key):
        body = (C.c_uint8 * 44)()
        rc = self.lib.mv8688_key_body(body, key_type, pairwise, key, len(key))
        return rc, bytes(body)

    def test_key_bodies_match_libertas_add_key(self):
        rc, body = self.key_body(2, 1, bytes(range(16)))
        # Action SET, TLV 0x0100 of 38 bytes, AES, unicast | enabled, 16 bytes.
        self.assertEqual(rc, OK)
        self.assertEqual(body, bytes.fromhex('0100 0001 2600 0200 0600 1000')
                         + bytes(range(16)) + bytes(16))
        rc, body = self.key_body(1, 0, bytes(range(32)))
        self.assertEqual((rc, body[6:12]), (OK, bytes.fromhex('0100 0500 2000')))
        self.assertEqual(body[12:], bytes(range(32)))

    def test_keys_of_another_type_or_length_are_refused(self):
        for key_type, key in ((2, bytes(32)), (1, bytes(16)), (0, bytes(13)), (2, b'')):
            self.assertEqual(self.key_body(key_type, 1, key)[0], -1, (key_type, len(key)))

    def test_only_one_whole_rsn_or_wpa_element_is_accepted(self):
        wpa = ie(221, bytes.fromhex('0050f201 0100'))
        self.assertEqual(self.lib.mv8688_wpa_ie_check(RSN_CCMP, len(RSN_CCMP)), OK)
        self.assertEqual(self.lib.mv8688_wpa_ie_check(wpa, len(wpa)), OK)
        for bad in (RSN_CCMP[:-1], RSN_CCMP + b'\x00', ie(221, bytes.fromhex('0050f202')),
                    ie(48, bytes(129)), ie(1, b'\x82'), b'\x30'):
            self.assertEqual(self.lib.mv8688_wpa_ie_check(bad, len(bad)), -1, bad.hex())

    def assoc_wpa_body(self, b, element):
        body = (C.c_uint8 * 228)()
        n = self.lib.mv8688_assoc_wpa_body(body, C.byref(b), element, len(element))
        return bytes(body)[:n]

    def test_the_wpa_element_follows_the_open_body_as_its_own_tlv(self):
        b = self.open_bss(capability=0x0411)
        body = self.assoc_wpa_body(b, RSN_CCMP)
        self.assertEqual(body[:-24], self.assoc_body(b))
        # lbs_add_wpa_tlv: the element's ID and length as 16-bit words.
        self.assertEqual(body[-24:], le16(48) + le16(20) + RSN_CCMP[2:])
        self.assertEqual(self.assoc_wpa_body(b, b''), self.assoc_body(b))
        self.assertEqual(self.assoc_wpa_body(b, RSN_CCMP[:-1]), b'')

    def test_the_largest_wpa_associate_command_still_fits_one_block(self):
        b = self.open_bss(ssid=b'z' * 32, supp=bytes.fromhex('82848b960c121824'),
                          ext=bytes.fromhex('30486c608284'))
        body = self.assoc_wpa_body(b, ie(48, bytes(128)))
        self.assertEqual(len(body), 13 + 36 + 5 + 10 + 4 + 14 + 6 + 4 + 128)
        self.assertEqual(self.send(ASSOCIATE, body), OK)
        self.assertEqual(self.card_model.received[0][1], 1)

    def test_mic_failure_events_are_counted(self):
        self.card_model.uploads += [packet(3, le32(0x0e)), packet(3, le32(0x0d)),
                                    packet(3, le32(0x0e))]
        self.assertEqual(self.lib.mv8688_wait(C.byref(self.cmd), 10), OK)
        self.assertEqual((self.cmd.mic_ucast, self.cmd.mic_mcast, self.cmd.disconnects),
                         (2, 1, 0))

    @staticmethod
    def data_frame(payload=b'', ethertype=0x0806, snap=True, pkt_ptr=20, rxpd=None):
        rxpd = rxpd or (le16(0x0001) + bytes((40, 0)) + le16(0) + bytes((0xa0, 11))
                        + le32(pkt_ptr) + le32(0) + bytes((0, 0, 0, 0)))
        llc = bytes.fromhex('aaaa03000000') + ethertype.to_bytes(2, 'big') if snap else b''
        header = b'\xff' * 6 + bytes.fromhex('020000aabbcc') + le16(len(llc + payload))
        return rxpd + bytes(pkt_ptr - 20) + header + llc + payload

    def parse_rxpd(self, frame):
        rx = Rx()
        return self.lib.mv8688_parse_rxpd(frame, len(frame), C.byref(rx)), rx

    def test_a_receive_descriptor_and_its_snap_header_are_decoded(self):
        rc, rx = self.parse_rxpd(self.data_frame(bytes(28)))
        self.assertEqual(rc, OK)
        self.assertEqual((rx.status, rx.snr, rx.nf, rx.rate, rx.pkt_ptr, rx.frame_len),
                         (1, 40, 0xa0, 11, 20, 14 + 8 + 28))
        self.assertEqual((bytes(rx.dst), bytes(rx.src), rx.ethertype),
                         (b'\xff' * 6, bytes.fromhex('020000aabbcc'), 0x0806))
        rc, rx = self.parse_rxpd(self.data_frame(bytes(10), pkt_ptr=24))
        self.assertEqual((rc, rx.pkt_ptr, rx.frame_len, rx.ethertype), (OK, 24, 32, 0x0806))
        rc, rx = self.parse_rxpd(self.data_frame(bytes(10), snap=False))
        self.assertEqual((rc, rx.ethertype), (OK, 0))

    def test_a_descriptor_that_does_not_fit_its_packet_is_rejected(self):
        frame = self.data_frame()
        cases = {
            'short': frame[:19],
            'pointer into the descriptor': self.data_frame(pkt_ptr=20)[:8] + le32(16)
            + frame[12:],
            'pointer past the end': frame[:8] + le32(len(frame) + 1) + frame[12:],
            'no room for the 802.3 header': frame[:20 + 13],
        }
        for name, data in cases.items():
            self.assertEqual(self.parse_rxpd(data)[0], ECMDRX, name)

    def test_waiting_records_events_frames_and_stray_responses(self):
        self.card_model.uploads += [packet(3, le32(0x0108)), packet(0, self.data_frame(bytes(28))),
                                    packet(0, bytes(30)), packet(1, le16(0x80a9) + bytes(6)),
                                    packet(3, le32(0x03)), packet(3, le32(0x30))]
        self.assertEqual(self.lib.mv8688_wait(C.byref(self.cmd), 40), OK)
        self.assertEqual((self.cmd.events, self.cmd.disconnects, self.cmd.stray_responses),
                         (3, 2, 1))
        self.assertEqual((self.cmd.data_packets, self.cmd.data_bad, self.cmd.data_n,
                          self.cmd.data_bytes), (2, 1, 1, 50))
        self.assertEqual(self.cmd.data[0].ethertype, 0x0806)
        # Nothing was written to the card, and the wait ran to its end.
        self.assertEqual(self.card_model.received, [])
        # 1 ms between the idle polls only, none after the last.
        self.assertEqual(self.fpga.delays, [1] * (40 - 6 - 1))

    def test_waiting_is_bounded_by_the_clock(self):
        self.fpga.irq_line = False
        start = self.fpga.clock_ms
        self.assertEqual(self.lib.mv8688_wait(C.byref(self.cmd), 3000), OK)
        self.assertLess(self.fpga.clock_ms - start, 3200)
        self.assertGreaterEqual(self.fpga.clock_ms - start, 3000)

    def test_a_card_failure_while_waiting_is_reported(self):
        self.card_model.uploads.append(packet(3, le32(1)))
        self.card_model.rx_len_override = 0
        self.assertEqual(self.lib.mv8688_wait(C.byref(self.cmd), 40), ECMDRX)
        self.assertEqual(self.cmd.step, b'rx_len')

    def collect_frames(self, stop=False):
        seen = []

        def on_frame(ctx, rx, frame, length):
            seen.append((rx.contents.ethertype, bytes(frame[:length])))
            if stop:
                self.cmd.wait_done = 1
        self.cmd.on_frame = FRAME_CB(on_frame)
        return seen

    def test_frames_with_snap_are_handed_on_as_ethernet_ii(self):
        seen = self.collect_frames()
        payload = bytes(range(28))
        self.card_model.uploads += [packet(0, self.data_frame(payload, pkt_ptr=38)),
                                    packet(0, self.data_frame(b'xyz', snap=False)),
                                    packet(0, bytes(30))]
        self.assertEqual(self.lib.mv8688_wait(C.byref(self.cmd), 10), OK)
        # libertas rx.c: the addresses, the SNAP ethertype, the payload.
        self.assertEqual(seen[0], (0x0806, b'\xff' * 6 + bytes.fromhex('020000aabbcc')
                                   + bytes.fromhex('0806') + payload))
        # Without LLC/SNAP the 802.3 frame is passed on as it came.
        self.assertEqual(seen[1], (0, b'\xff' * 6 + bytes.fromhex('020000aabbcc')
                                   + le16(3) + b'xyz'))
        # A descriptor that does not fit is counted, not handed on.
        self.assertEqual((len(seen), self.cmd.data_bad), (2, 1))

    def test_a_frame_handler_can_end_the_wait(self):
        seen = self.collect_frames(stop=True)
        self.card_model.uploads += [packet(0, self.data_frame(bytes(28))),
                                    packet(0, self.data_frame(bytes(28)))]
        start = self.fpga.clock_ms
        self.assertEqual(self.lib.mv8688_wait(C.byref(self.cmd), 1000), OK)
        self.assertEqual(len(seen), 1)
        self.assertLess(self.fpga.clock_ms - start, 50)
        # The next wait starts afresh and reads the second.
        self.assertEqual(self.lib.mv8688_wait(C.byref(self.cmd), 1000), OK)
        self.assertEqual(len(seen), 2)

    @staticmethod
    def ethernet(length):
        frame = bytes.fromhex('dca632000001 001cd7000001 0800')
        return frame + bytes((i * 7) & 0xff for i in range(length - len(frame)))

    def send_data(self, frame, timeout=100):
        return self.lib.mv8688_send_data(C.byref(self.cmd), frame, len(frame), timeout)

    def test_a_data_frame_goes_behind_a_txpd_as_libertas_sends_it(self):
        frame = self.ethernet(98)
        self.assertEqual(self.send_data(frame), OK)
        addr, blocks, raw = self.card_model.received[0]
        self.assertEqual((addr, blocks, len(raw)), (IOPORT, 1, 256))
        total = 4 + 24 + 98
        # SDIO header {length, type 0}; txpd: status and control zero,
        # location 24, length, destination, then four zero bytes.
        self.assertEqual(raw[:4], le16(total) + le16(0))
        self.assertEqual(raw[4:28], bytes(8) + le32(24) + le16(98) + frame[:6] + bytes(4))
        self.assertEqual(raw[28:total], frame)
        self.assertEqual(raw[total:], bytes(256 - total))
        self.assertEqual(self.cmd53(True), [0x80000000 | 1 << 28 | 1 << 27 | IOPORT << 9 | 1])
        self.assertEqual((self.cmd.tx_frames, self.cmd.tx_bytes, self.cmd.tx_blocks,
                          self.cmd.tx_polls, self.cmd.dnld_acks), (1, 98, 1, 1, 1))

    def test_data_writes_take_whole_blocks_up_to_seven(self):
        # The 5.1 echo frames: 42 bytes of headers and these payloads.
        for blocks, payload in enumerate((56, 248, 504, 760, 1016, 1272, 1472), 1):
            self.card_model.received.clear()
            self.assertEqual(self.send_data(self.ethernet(42 + payload)), OK)
            got = self.card_model.received[0]
            self.assertEqual((got[1], len(got[2]), self.cmd.tx_blocks),
                             (blocks, 256 * blocks, blocks), payload)
            self.assertEqual(got[2][28:28 + 42 + payload], self.ethernet(42 + payload))
        self.assertEqual(self.cmd.tx_bytes, sum(42 + p for p in
                                                (56, 248, 504, 760, 1016, 1272, 1472)))

    def test_frames_outside_ethernet_lengths_are_refused_unsent(self):
        for length in (13, 1515):
            self.assertEqual(self.send_data(self.ethernet(14)[:13] if length == 13
                                            else self.ethernet(length)), EINVAL, length)
            self.assertEqual(self.cmd.step, b'tx_size')
        self.assertEqual(self.send_data(self.ethernet(1514)), OK)
        self.assertEqual(self.card_model.received[0][1], 7)
        self.assertEqual(self.cmd.tx_frames, 1)

    def test_no_dnld_after_a_data_write_fails(self):
        self.card_model.data_dnld = False
        self.assertEqual(self.send_data(self.ethernet(60), timeout=20), ETXDONE)
        self.assertEqual((self.cmd.step, self.cmd.tx_polls), (b'tx_done', 20))
        # The write itself went out.
        self.assertEqual((len(self.card_model.received), self.cmd.tx_frames), (1, 1))

    def test_the_dnld_wait_reads_what_comes_with_it_and_leaves_the_rest(self):
        seen = self.collect_frames()
        reply = self.data_frame(bytes(28), ethertype=0x0800)
        self.card_model.on_data = lambda raw: [packet(3, le32(0x0a)), packet(0, reply)]
        self.assertEqual(self.send_data(self.ethernet(60)), OK)
        # The status that carried DNLD also announced the event.
        self.assertEqual((self.cmd.events, self.cmd.data_packets, self.cmd.tx_polls), (1, 0, 1))
        self.assertEqual(self.lib.mv8688_wait(C.byref(self.cmd), 5), OK)
        self.assertEqual((self.cmd.data_packets, len(seen), seen[0][0]), (1, 1, 0x0800))

    def test_a_failed_data_write_is_terminated_and_tried_again(self):
        self.card_model.fail_writes = 1
        self.assertEqual(self.send_data(self.ethernet(60)), OK)
        self.assertEqual((self.cmd.write_tries, self.cmd.aborts), (2, 1))
        self.card_model.fail_writes = 3
        self.assertEqual(self.send_data(self.ethernet(60)), ECMDWRITE)
        self.assertEqual(self.cmd.step, b'tx_write')
        self.assertEqual(self.cmd.tx_frames, 1)


if __name__ == '__main__':
    unittest.main()
