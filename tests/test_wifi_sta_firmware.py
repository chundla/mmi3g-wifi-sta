"""88W8688 helper and firmware download against a fake module and card.

The fake card follows the protocol as the factory driver uses it: the boot
ROM takes length-prefixed helper chunks until a zero length, then the helper
asks for each firmware chunk through function-1 0x10/0x11, with bit 0 asking
for the previous chunk again. The request sizes, timing and failure modes
here are choices for the tests, not observations of the chip.
"""
import ctypes as C
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from test_wifi_sta_host import (BLKSZ, CMD_RDY, DATA, FLAG, POWER, Card, FakeCard,
                                FakeModule, Host, ROOT)

OK, ECARD = 0, -3
EREQUEST, ECRC, ESTALL, ENOTREADY, ENOFW = -20, -21, -22, -23, -24
IOPORT = 0x10000


class Dl(C.Structure):
    _fields_ = [('card', C.POINTER(Card)), ('ioport', C.c_uint32), ('step', C.c_char_p),
                ('buf', C.c_uint8 * 2560), ('buf_blocks', C.c_uint),
                ('helper_chunks', C.c_uint), ('requests', C.c_uint), ('req_min', C.c_uint),
                ('req_max', C.c_uint), ('crc_retries', C.c_uint), ('aborts', C.c_uint),
                ('abort_failures', C.c_uint), ('ready_reads_max', C.c_uint),
                ('zero_polls_max', C.c_uint), ('fw_polls', C.c_uint), ('sent', C.c_uint32),
                ('fw_status', C.c_uint16), ('rx_unit', C.c_uint8), ('rx_unit_rc', C.c_int),
                ('rd_base_pre', C.c_uint16), ('rd_base_pre_rc', C.c_int),
                ('last_request', C.c_uint16), ('first_polls', C.c_uint),
                ('early_odd', C.c_uint), ('trace_n', C.c_uint),
                ('trace_poll', C.c_uint16 * 8), ('trace_value', C.c_uint16 * 8)]


def image(size, seed=7):
    return bytes((i * 131 + seed + (i >> 8)) & 0xff for i in range(size))


class DownloadCard(FakeCard):
    def __init__(self, fw_size, request=2048):
        super().__init__()
        self.mem[1].update({0x00: 0x00, 0x01: 0x00, 0x02: 0x01, 0x43: 0x20})
        self.state = 'boot'
        self.helper = bytearray()
        self.helper_writes = []         # (addr, blocks, raw bytes)
        self.fw = bytearray()
        self.fw_size = fw_size
        self.fw_writes = []             # (addr, blocks, raw bytes)
        self.request = request
        self.asked = 0
        self.next_len = 0
        self.boot_rom_len = 0           # request register under the boot ROM
        self.early = []                 # request values read after the helper, in turn
        self.crc_at = set()             # firmware writes received badly
        self.fail_write_at = set()      # firmware writes whose CMD53 times out
        self.chunk = 0
        self.stall_after = None
        self.not_ready = 0              # status reads without DL_RDY; -1: never
        self.status_reads = 0
        self.boot_reads = 2             # scratch reads before the ready magic
        self.boot_count = 0
        self.aborts = 0
        self.fail_rx_unit = False

    def ask(self):
        remaining = self.fw_size - len(self.fw)
        if remaining <= 0 or (self.stall_after is not None
                              and len(self.fw) >= self.stall_after):
            self.next_len = 0
            if remaining <= 0:
                self.state = 'booting'
            return
        self.asked = self.request
        self.next_len = self.asked

    def register(self, addr, write, value):
        if addr == 0x03 and write:
            self.aborts += value == 4
            return value
        if addr in (0x10, 0x11):
            if self.state == 'boot':
                value = self.boot_rom_len
            elif self.early and not self.fw_writes:
                value = self.early[0]
                if addr == 0x11:
                    self.early.pop(0)
            else:
                value = self.next_len
            return (value >> (8 * (addr - 0x10))) & 0xff
        if addr == 0x20:
            self.status_reads += 1
            if self.not_ready:
                self.not_ready -= self.not_ready > 0
                return 0x08
            return 0x09
        if addr in (0x40, 0x41):
            if addr == 0x40 and self.state == 'booting':
                if self.boot_count >= self.boot_reads:
                    self.state = 'running'
                self.boot_count += 1
            magic = 0xfedc if self.state == 'running' else 0
            return (magic >> (8 * (addr - 0x40))) & 0xff
        return None

    def handle(self, opcode, arg):
        if self.dead:
            return super().handle(opcode, arg)
        if (opcode == 52 and self.fail_rx_unit and (arg >> 28) & 7 == 1
                and (arg >> 9) & 0x1ffff == 0x43):
            self.commands.append((opcode, arg))
            return None
        if opcode == 52 and (arg >> 28) & 7 == 1:
            value = self.register((arg >> 9) & 0x1ffff, arg >> 31, arg & 0xff)
            if value is not None:
                self.commands.append((opcode, arg))
                return self.r5_flags << 8 | value
        if (opcode == 53 and arg >> 31 and self.state == 'helper'
                and self.chunk in self.fail_write_at):
            self.fail_write_at.discard(self.chunk)
            self.commands.append((opcode, arg))
            self.chunk += 1
            self.next_len = self.asked | 1
            return None
        return super().handle(opcode, arg)

    def receive(self, addr, blocks, raw):
        if self.state == 'boot':
            self.helper_writes.append((addr, blocks, raw))
            n = int.from_bytes(raw[:4], 'little')
            if n == 0:
                self.state = 'helper'
                self.ask()
            else:
                self.helper += raw[4:4 + n]
            return
        if self.state != 'helper':
            return
        self.fw_writes.append((addr, blocks, raw))
        index, self.chunk = self.chunk, self.chunk + 1
        if index in self.crc_at:
            self.next_len = self.asked | 1
            return
        take = min(self.asked, self.fw_size - len(self.fw))
        self.fw += raw[:take]
        self.ask()

    def power(self, value):
        if not value & 3:
            self.state = 'boot'
            self.boot_count = 0


class DownloadModule(FakeModule):
    def __init__(self, card):
        super().__init__(card)
        self.pending = None

    def execute(self):
        super().execute()
        w0, w1 = self.cmd_words[-2:]
        arg = ((w0 >> 8) & 0xff) << 24 | ((w0 >> 16) & 0xff) << 16 | (w0 >> 24) << 8 | (w1 & 0xff)
        if (w0 & 0x3f) == 53 and arg >> 31 and self.regs.get(FLAG, 0) & CMD_RDY:
            blocks = arg & 0x1ff
            self.pending = ((arg >> 9) & 0x1ffff, blocks, blocks * self.regs[BLKSZ],
                            len(self.card.data_in), arg)

    def wr32(self, ctx, off, value):
        super().wr32(ctx, off, value)
        if off == DATA and self.pending:
            addr, blocks, length, start, _ = self.pending
            if len(self.card.data_in) - start >= length:
                self.pending = None
                self.card.receive(addr, blocks,
                                  bytes(self.card.data_in[start:start + length]))
        elif off == POWER:
            self.card.power(value)


class FirmwareDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('cc')
        if not compiler:
            raise RuntimeError('C compiler required for the firmware download tests')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        so = Path(cls.tmp.name) / 'mv8688fw.so'
        subprocess.run([compiler, '-std=c99', '-Wall', '-Wextra', '-Werror', '-shared',
                        '-fPIC'] + [str(ROOT / 'wifi-sta' / name) for name in
                                    ('hbsdc.c', 'sdio.c', 'mv8688fw.c')] + ['-o', str(so)],
                       check=True, capture_output=True)
        lib = cls.lib = C.CDLL(str(so))
        lib.hbsdc_identify.argtypes = [C.POINTER(Host)]
        lib.sdio_attach.argtypes = [C.POINTER(Card), C.c_uint16, C.c_uint16]
        lib.mv8688_cksum.restype = C.c_uint32
        lib.mv8688_cksum.argtypes = [C.c_char_p, C.c_uint32]
        lib.mv8688_ioport.argtypes = [C.POINTER(Card), C.POINTER(C.c_uint32)]
        lib.mv8688_fw_status.argtypes = [C.POINTER(Card), C.POINTER(C.c_uint16)]
        for name in ('mv8688_prog_helper', 'mv8688_prog_fw'):
            getattr(lib, name).argtypes = [C.POINTER(Dl), C.c_char_p, C.c_uint32]
        lib.mv8688_wait_fw.argtypes = [C.POINTER(Dl), C.c_uint]
        lib.hbsdc_shutdown.argtypes = [C.POINTER(Host)]

    def setUp(self, fw_size=10000, request=2048):
        self.prepare(fw_size, request)

    def prepare(self, fw_size, request=2048):
        self.card_model = DownloadCard(fw_size, request)
        self.fpga = DownloadModule(self.card_model)
        self.host = Host()
        self.host.io = C.pointer(self.fpga.io)
        self.assertEqual(self.lib.hbsdc_identify(C.byref(self.host)), OK)
        self.card = Card()
        self.card.host = C.pointer(self.host)
        self.assertEqual(self.lib.sdio_attach(C.byref(self.card), 0x02df, 0x9103), OK)
        port = C.c_uint32()
        self.assertEqual(self.lib.mv8688_ioport(C.byref(self.card), C.byref(port)), OK)
        self.dl = Dl()
        self.dl.card = C.pointer(self.card)
        self.dl.ioport = port.value
        self.fpga.delays.clear()

    def helper(self, data=None):
        data = image(1200, seed=3) if data is None else data
        return self.lib.mv8688_prog_helper(C.byref(self.dl), data, len(data))

    def firmware(self, data):
        self.assertEqual(self.helper(), OK)
        return self.lib.mv8688_prog_fw(C.byref(self.dl), data, len(data))

    def cmd53_args(self):
        return [arg for op, arg in self.card_model.commands if op == 53]

    # --- image identity --------------------------------------------------------
    def test_cksum_matches_the_posix_cksum_utility(self):
        cksum = shutil.which('cksum')
        if not cksum:
            self.skipTest('no cksum utility')
        for data in (b'', b'a', image(300), image(70000, seed=1)):
            out = subprocess.run([cksum], input=data, capture_output=True, check=True)
            self.assertEqual(self.lib.mv8688_cksum(data, len(data)),
                             int(out.stdout.split()[0]))

    # --- helper ------------------------------------------------------------------
    def test_helper_goes_in_508_byte_chunks_then_one_zero_block(self):
        data = image(1200, seed=3)
        self.assertEqual(self.helper(data), OK)
        self.assertEqual(bytes(self.card_model.helper), data)
        self.assertEqual(self.dl.helper_chunks, 3)
        writes = self.card_model.helper_writes
        self.assertEqual([blocks for _, blocks, _ in writes], [2, 2, 2, 1])
        self.assertEqual([int.from_bytes(raw[:4], 'little') for _, _, raw in writes],
                         [508, 508, 184, 0])
        # As the factory, a short chunk is padded with the previous one's tail.
        self.assertEqual(writes[2][2][4 + 184:], writes[1][2][4 + 184:])
        self.assertEqual(writes[3][2], bytes(256))
        # Function 1, block mode, fixed address at the I/O port, write.
        for arg in self.cmd53_args():
            self.assertEqual(arg & ~0x1ff, 0x80000000 | 1 << 28 | 1 << 27 | IOPORT << 9)
        # The ready status is read before every write, the zero block too.
        self.assertEqual(self.card_model.status_reads, 4)

    def test_helper_waits_for_download_ready_then_gives_up_after_100_reads(self):
        self.card_model.not_ready = 5
        self.assertEqual(self.helper(), OK)
        self.assertEqual(self.dl.ready_reads_max, 6)
        self.prepare(10000)
        self.card_model.not_ready = -1
        self.assertEqual(self.helper(), ENOTREADY)
        self.assertEqual(self.dl.step, b'helper_ready')
        self.assertEqual((self.card_model.status_reads, self.dl.ready_reads_max), (100, 100))
        self.assertEqual(self.cmd53_args(), [])

    # --- firmware --------------------------------------------------------------
    def test_firmware_follows_the_requested_lengths_in_whole_blocks(self):
        data = image(10000)
        self.assertEqual(self.firmware(data), OK)
        self.assertEqual(bytes(self.card_model.fw), data)
        writes = self.card_model.fw_writes
        self.assertEqual([blocks for _, blocks, _ in writes], [8, 8, 8, 8, 8])
        self.assertEqual(writes[-1][2][1808:], writes[-2][2][1808:])
        self.assertEqual((self.dl.sent, self.dl.requests), (10000, 5))
        self.assertEqual((self.dl.req_min, self.dl.req_max), (2048, 2048))
        self.assertEqual((self.dl.crc_retries, self.dl.aborts), (0, 0))

    def test_ready_magic_is_polled_10_ms_apart_then_rx_unit_is_read(self):
        self.assertEqual(self.firmware(image(10000)), OK)
        self.fpga.delays.clear()
        self.assertEqual(self.lib.mv8688_wait_fw(C.byref(self.dl), 100), OK)
        self.assertEqual((self.dl.fw_status, self.dl.fw_polls, self.dl.rx_unit),
                         (0xfedc, 3, 0x20))
        self.assertEqual(self.fpga.delays, [10, 10])

    def test_a_failed_rx_unit_read_does_not_fail_the_download(self):
        self.assertEqual(self.firmware(image(10000)), OK)
        self.card_model.fail_rx_unit = True
        self.assertEqual(self.lib.mv8688_wait_fw(C.byref(self.dl), 100), OK)
        self.assertEqual((self.dl.fw_status, self.dl.rx_unit), (0xfedc, 0))
        self.assertEqual(self.dl.rx_unit_rc, ECARD)

    def test_ready_magic_that_never_appears_fails_after_the_tries(self):
        self.card_model.boot_reads = 1000
        self.assertEqual(self.firmware(image(10000)), OK)
        self.fpga.delays.clear()
        self.assertEqual(self.lib.mv8688_wait_fw(C.byref(self.dl), 100), ENOFW)
        self.assertEqual((self.dl.step, self.dl.fw_polls), (b'fw_status', 100))
        self.assertEqual(self.fpga.delays, [10] * 99)

    def test_an_odd_length_resends_the_previous_chunk(self):
        self.card_model.crc_at = {1}
        data = image(10000)
        self.assertEqual(self.firmware(data), OK)
        self.assertEqual(bytes(self.card_model.fw), data)
        writes = self.card_model.fw_writes
        self.assertEqual(len(writes), 6)
        self.assertEqual(writes[1], writes[2])
        self.assertEqual((self.dl.crc_retries, self.dl.requests), (1, 6))

    def test_three_crc_reports_in_a_row_fail(self):
        self.card_model.crc_at = {1, 2, 3}
        self.assertEqual(self.firmware(image(10000)), ECRC)
        self.assertEqual(self.dl.step, b'fw_crc')
        self.assertEqual(len(self.card_model.fw_writes), 4)

    def test_odd_values_before_the_first_request_are_waited_out_without_writing(self):
        # The first run on the unit read an odd value at once after the helper.
        self.card_model.early = [0x0001, 0x0001, 0, 0x0003]
        data = image(10000)
        self.assertEqual(self.firmware(data), OK)
        self.assertEqual(bytes(self.card_model.fw), data)
        self.assertEqual(len(self.card_model.fw_writes), 5)
        self.assertEqual((self.dl.early_odd, self.dl.first_polls, self.dl.crc_retries),
                         (3, 4, 0))
        self.assertEqual(self.fpga.delays, [1] * 4)
        changes = [(self.dl.trace_poll[i], self.dl.trace_value[i])
                   for i in range(self.dl.trace_n)]
        self.assertEqual(changes, [(0, 1), (2, 0), (3, 3), (4, 2048)])

    def test_an_odd_value_that_never_clears_fails_without_writing(self):
        self.card_model.early = [0x0001] * 1000
        self.assertEqual(self.firmware(image(10000)), ESTALL)
        self.assertEqual((self.dl.step, self.card_model.fw_writes), (b'fw_first_request', []))
        self.assertEqual((self.dl.early_odd, self.dl.first_polls), (1000, 1000))
        self.assertEqual((self.dl.trace_n, self.dl.last_request), (1, 1))
        self.assertFalse([a for a in self.cmd53_args() if a & 0x1ff == 0])

    def test_the_request_register_is_read_once_before_the_helper(self):
        self.card_model.boot_rom_len = 0x0101
        self.assertEqual(self.helper(), OK)
        self.assertEqual((self.dl.rd_base_pre, self.dl.rd_base_pre_rc), (0x0101, OK))

    def test_a_failed_write_is_terminated_and_recovered_by_the_resend(self):
        self.card_model.fail_write_at = {1}
        data = image(10000)
        self.assertEqual(self.firmware(data), OK)
        self.assertEqual(bytes(self.card_model.fw), data)
        self.assertEqual((self.dl.aborts, self.dl.abort_failures), (1, 0))
        self.assertEqual(self.card_model.aborts, 1)
        self.assertEqual(self.dl.crc_retries, 1)

    def test_an_oversized_request_fails_before_writing(self):
        self.prepare(10000, request=0x90a)
        self.assertEqual(self.firmware(image(10000)), EREQUEST)
        self.assertEqual((self.dl.step, self.card_model.fw_writes), (b'fw_request', []))

    def test_the_largest_request_fits_the_buffer(self):
        self.prepare(10000, request=0x908)
        data = image(10000)
        self.assertEqual(self.firmware(data), OK)
        self.assertEqual(bytes(self.card_model.fw), data)
        self.assertEqual(self.card_model.fw_writes[0][1], 10)

    def test_a_card_that_stops_asking_early_is_a_stall_not_success(self):
        self.card_model.stall_after = 4096
        self.assertEqual(self.firmware(image(10000)), ESTALL)
        self.assertEqual((self.dl.step, self.dl.sent), (b'fw_request', 4096))
        self.assertEqual(self.dl.zero_polls_max, 1000)
        self.assertEqual(self.fpga.delays, [1] * 1000)

    def test_a_power_down_returns_the_card_to_its_boot_rom(self):
        self.assertEqual(self.firmware(image(10000)), OK)
        self.assertEqual(self.lib.mv8688_wait_fw(C.byref(self.dl), 100), OK)
        self.lib.hbsdc_shutdown(C.byref(self.host))
        self.assertEqual(self.lib.sdio_attach(C.byref(self.card), 0x02df, 0x9103), OK)
        status = C.c_uint16()
        self.assertEqual(self.lib.mv8688_fw_status(C.byref(self.card), C.byref(status)), OK)
        self.assertEqual(status.value, 0)


if __name__ == '__main__':
    unittest.main()
