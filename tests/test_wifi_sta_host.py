"""FPGA SDIO host and card layer against a fake module and card.

The fake decodes the command FIFO words and returns responses in the layout
the factory driver's completion path reads, so a packing error on either side
shows up here. It models the documented register behaviour only; it does not
show that the FPGA latches FLAG, orders FIFO data or times responses the way
this model does. Only the probe on the unit can show that.
"""
import ctypes as C
from collections import deque
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

CTRL, STAT, IEN, FLAG, POWER = 0x10, 0x14, 0x18, 0x1c, 0x28
CMD, RESP, DATA, CMDTY, BLKCNT, BLKSZ, INDEX, CAPS = 0x04, 0x08, 0x00, 0x0c, 0x2c, 0x30, 0x3c, 0x5c
CMD_RDY, CMD_TIMEOUT, DT_RDY = 0x1, 0x200, 0x8000
STROBES = 0x0008 | 0x0010 | 0x0020 | 0x0040 | 0x0100 | 0x0200
OK, EBUSY, ETIMEDOUT, ECARD, EINVAL, EDATA = 0, -1, -2, -3, -4, -5
ER5, EID = -10, -12

RD32 = C.CFUNCTYPE(C.c_uint32, C.c_void_p, C.c_uint)
WR32 = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint32)
RD8 = C.CFUNCTYPE(C.c_uint8, C.c_void_p, C.c_uint)
WR8 = C.CFUNCTYPE(None, C.c_void_p, C.c_uint, C.c_uint8)
WAIT = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint)
VOID = C.CFUNCTYPE(None, C.c_void_p)
DELAY = C.CFUNCTYPE(None, C.c_void_p, C.c_uint)
NOW = C.CFUNCTYPE(C.c_uint, C.c_void_p)
TICKS = C.CFUNCTYPE(C.c_uint32, C.c_void_p)
BUCKETS = 9


class Io(C.Structure):
    _fields_ = [('rd32', RD32), ('wr32', WR32), ('rd8', RD8), ('wr8', WR8),
                ('wait_irq', WAIT), ('irq_done', VOID), ('delay_ms', DELAY),
                ('spin_us', DELAY), ('now_ms', NOW), ('ticks', TICKS)]


class Phase(C.Structure):
    _fields_ = [('n', C.c_uint), ('bytes', C.c_uint), ('setup', C.c_uint64),
                ('data', C.c_uint64), ('drain', C.c_uint64), ('data_max', C.c_uint32),
                ('total_max', C.c_uint32)]


class Stats(C.Structure):
    _fields_ = [('xfer', (Phase * BUCKETS) * 2), ('cmd_n', C.c_uint),
                ('cmd_ticks', C.c_uint64), ('cmd_max', C.c_uint32), ('failures', C.c_uint)]


class Host(C.Structure):
    _fields_ = [('io', C.POINTER(Io)), ('ctx', C.c_void_p),
                ('major', C.c_uint8), ('change', C.c_uint8), ('irq_index', C.c_uint8),
                ('irq', C.c_uint), ('clock_hz', C.c_uint32), ('caps', C.c_uint32),
                ('fifo_bytes', C.c_uint), ('bus_hz', C.c_uint), ('flags', C.c_uint32),
                ('resp_index', C.c_uint8), ('by_irq', C.c_int),
                ('interrupts', C.c_uint), ('polls', C.c_uint), ('changes', C.c_uint),
                ('stats', C.POINTER(Stats))]


class Cmd(C.Structure):
    _fields_ = [('opcode', C.c_uint8), ('resp', C.c_uint8), ('xfer', C.c_uint16),
                ('arg', C.c_uint32), ('response', C.c_uint32)]


class Card(C.Structure):
    _fields_ = [('host', C.POINTER(Host)), ('power_raw', C.c_uint32), ('ocr', C.c_uint32),
                ('nfunc', C.c_uint), ('rca', C.c_uint16), ('cccr', C.c_uint8 * 0x14),
                ('width', C.c_uint), ('cis', C.c_uint32 * 8), ('fn_code', C.c_uint8 * 8),
                ('blksz', C.c_uint16 * 8), ('vendor', C.c_uint16), ('device', C.c_uint16),
                ('manf', C.c_char * 64), ('ready_ms', C.c_uint), ('r5', C.c_uint32),
                ('step', C.c_char_p)]


def cis_bytes():
    vers = b'\x01\x00Marvell\x00802.11 SDIO ID: 0B\x00\xff'
    return (bytes((0x21, 2, 0x0c, 0x00)) + bytes((0x15, len(vers))) + vers
            + bytes((0x20, 4, 0xdf, 0x02, 0x03, 0x91)) + b'\xff')


class FakeCard:
    """An SD8688-like card: one function, CIS in function-0 space."""

    def __init__(self):
        self.mem = {fn: {} for fn in range(8)}
        self.mem[0].update({0x00: 0x32, 0x01: 0x02, 0x08: 0x13, 0x09: 0x00,
                            0x0a: 0x10, 0x0b: 0x00, 0x13: 0x00,
                            0x100: 0x07, 0x109: 0x00, 0x10a: 0x20, 0x10b: 0x00})
        for offset, value in enumerate(cis_bytes()):
            self.mem[0][0x1000 + offset] = value
        self.mem[1].update({0x801c: 0x31, 0x801d: 0x30, 0x00: 0x00, 0x01: 0x00,
                            0x02: 0x01, 0x40: 0x00, 0x41: 0x00, 0x20: 0x09, 0x43: 0x00})
        self.ocr_ready_after = 0
        self.ocr_window_after = 0  # ready responses with no voltage window
        self.fail_once = set()     # opcodes that time out once
        self.ior_after = 2
        self.dead = False
        self.r5_flags = 0x10       # CMD state
        self.commands = []
        self.data_in = bytearray()

    def handle(self, opcode, arg):
        self.commands.append((opcode, arg))
        if self.dead:
            return None
        if opcode in self.fail_once:
            self.fail_once.discard(opcode)
            return None
        if opcode == 5:
            ready = 0x80000000 if self.ocr_ready_after <= 0 else 0
            self.ocr_ready_after -= 1
            if ready and self.ocr_window_after > 0:
                self.ocr_window_after -= 1
                return ready | 1 << 28
            return ready | 1 << 28 | 0xff8000
        if opcode == 3:
            return 0x0001 << 16 | 0x0500
        if opcode == 7:
            return 0x00000700
        if opcode == 52:
            write, fn, addr, value = arg >> 31, (arg >> 28) & 7, (arg >> 9) & 0x1ffff, arg & 0xff
            if write:
                self.mem[fn][addr] = value
            if fn == 0 and addr == 0x03:
                self.ior_after -= 1
                value = self.mem[0].get(0x02, 0) if self.ior_after <= 0 else 0
            else:
                value = self.mem[fn].get(addr, 0)
            return self.r5_flags << 8 | value
        if opcode == 53:
            return self.r5_flags << 8
        return None


class FakeModule:
    def __init__(self, card):
        self.card = card
        self.regs = {CAPS: 0x0103}
        self.index = {0: 0x53, 1: 0x03, 0xf: 0x38, 8: 0xff, 9: 0xff}
        self.selected = 0
        self.cmd_words = []
        self.resp = deque()
        self.data = deque()
        self.writes = []
        self.irq_line = True
        self.irq_done = 0
        self.delays = []
        self.starts = 0
        self.silent = False
        self.waited_ms = 0          # time spent in waits that timed out
        self.clock_ms = 0           # fake monotonic clock: delays and timed-out waits
        self.wait_slices = set()
        self.data_error = 0         # latched during the next CMD53 data phase
        self.data_phase = 0
        self.tick = 0               # fake counter: one tick per register access
        self.keep = [RD32(self.rd32), WR32(self.wr32), RD8(self.rd8), WR8(self.wr8),
                     WAIT(self.wait), VOID(self.done), DELAY(self.delay), DELAY(self.spin),
                     NOW(self.now), TICKS(self.ticks)]
        self.io = Io(*self.keep)

    def rd32(self, ctx, off):
        self.tick += 1
        if off == STAT and self.data_phase:
            self.regs[FLAG] = self.regs.get(FLAG, 0) | self.data_phase
            self.data_phase = 0
            return 0x8000           # DT_RUN, once
        if off == RESP:
            return self.resp.popleft() if self.resp else 0
        if off == DATA:
            return self.data.popleft() if self.data else 0
        return self.regs.get(off, 0)

    def wr32(self, ctx, off, value):
        self.tick += 1
        self.writes.append((off, value))
        if off == FLAG:
            self.regs[FLAG] = self.regs.get(FLAG, 0) & ~value
        elif off == CMD:
            self.cmd_words.append(value)
        elif off == DATA:
            self.card.data_in += value.to_bytes(4, 'little')
        elif off == POWER:
            # Bit 7 (change) is taken to clear when written back, as the
            # factory acknowledges it; the other bits read back as written.
            self.regs[POWER] = value & ~0x80 | self.regs.get(POWER, 0) & 0x80 & ~value
        elif off == CTRL:
            # Strobes do not read back: runtime CTRL reads 0x7003 although
            # power-up wrote CR_CLR and DATA_CLR into it.
            self.regs[CTRL] = value & ~STROBES
            if value & 0x100:
                self.starts += 1
                if not self.silent:
                    self.execute()
        else:
            self.regs[off] = value

    def rd8(self, ctx, off):
        if off == INDEX:
            return self.index.get(self.selected, 0)
        return self.regs.get(off, 0) & 0xff

    def wr8(self, ctx, off, value):
        self.writes.append((off, value))
        if off == INDEX:
            self.selected = value
        else:
            self.regs[off] = value

    def execute(self):
        w0, w1 = self.cmd_words[-2:]
        opcode = w0 & 0x3f
        arg = ((w0 >> 8) & 0xff) << 24 | ((w0 >> 16) & 0xff) << 16 | (w0 >> 24) << 8 | (w1 & 0xff)
        status = self.card.handle(opcode, arg)
        if status is None:
            self.regs[FLAG] = self.regs.get(FLAG, 0) | CMD_TIMEOUT
            return
        index = 0x3f if opcode == 5 else opcode
        self.resp.extend([index | (status >> 24) << 8 | ((status >> 16) & 0xff) << 16
                          | ((status >> 8) & 0xff) << 24, (status & 0xff) | 0x5a00])
        self.regs[FLAG] = self.regs.get(FLAG, 0) | CMD_RDY
        if opcode == 53:
            self.data_phase, self.data_error = self.data_error, 0
        if opcode == 53 and not arg >> 31:
            fn, addr = (arg >> 28) & 7, (arg >> 9) & 0x1ffff
            length = (arg & 0x1ff) * self.regs[BLKSZ]
            step = (arg >> 26) & 1          # 0: fixed address
            raw = bytes(self.card.mem[fn].get(addr + i * step, i & 0xff)
                        for i in range(length))
            self.data.extend(int.from_bytes(raw[i:i + 4], 'little') for i in range(0, length, 4))

    def wait(self, ctx, timeout_ms):
        self.wait_slices.add(timeout_ms)
        if self.irq_line and self.regs.get(FLAG, 0) & self.regs.get(IEN, 0):
            return 0
        self.waited_ms += timeout_ms
        self.clock_ms += timeout_ms
        return -1

    def done(self, ctx):
        self.irq_done += 1

    def delay(self, ctx, ms):
        self.delays.append(ms)
        self.clock_ms += ms

    def spin(self, ctx, us):
        pass

    def now(self, ctx):
        return self.clock_ms & 0xffffffff

    def ticks(self, ctx):
        return self.tick & 0xffffffff

    def written(self, off):
        return [value for where, value in self.writes if where == off]


class HostLayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('cc')
        if not compiler:
            raise RuntimeError('C compiler required for the SDIO host tests')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        so = Path(cls.tmp.name) / 'sdio.so'
        subprocess.run([compiler, '-std=c99', '-Wall', '-Wextra', '-Werror', '-shared',
                        '-fPIC', str(ROOT / 'wifi-sta/hbsdc.c'),
                        str(ROOT / 'wifi-sta/sdio.c'), '-o', str(so)],
                       check=True, capture_output=True)
        lib = cls.lib = C.CDLL(str(so))
        lib.hbsdc_cmd_word.restype = C.c_uint32
        lib.hbsdc_cmd_word.argtypes = [C.c_uint8, C.c_uint32]
        lib.hbsdc_cmdty.restype = C.c_uint32
        lib.hbsdc_cmdty.argtypes = [C.c_uint8, C.c_uint16]
        lib.hbsdc_response.restype = C.c_uint32
        lib.hbsdc_response.argtypes = [C.c_uint32, C.c_uint32]
        for name in ('hbsdc_identify', 'hbsdc_powerup'):
            getattr(lib, name).argtypes = [C.POINTER(Host)]
        lib.hbsdc_detect.argtypes = [C.POINTER(Host), C.POINTER(C.c_uint32)]
        lib.hbsdc_set_clock.argtypes = [C.POINTER(Host), C.c_uint]
        lib.hbsdc_command.argtypes = [C.POINTER(Host), C.POINTER(Cmd)]
        lib.hbsdc_shutdown.argtypes = [C.POINTER(Host)]
        lib.sdio_attach.argtypes = [C.POINTER(Card), C.c_uint16, C.c_uint16]
        lib.sdio_cmd52.argtypes = [C.POINTER(Card), C.c_uint, C.c_uint, C.c_int,
                                   C.c_uint8, C.POINTER(C.c_uint8)]
        lib.sdio_cmd53.argtypes = [C.POINTER(Card), C.c_uint, C.c_uint, C.c_int,
                                   C.c_int, C.c_void_p, C.c_uint, C.c_uint]

    def setUp(self):
        self.card_model = FakeCard()
        self.fpga = FakeModule(self.card_model)
        self.host = Host()
        self.host.io = C.pointer(self.fpga.io)

    def identified(self):
        self.assertEqual(self.lib.hbsdc_identify(C.byref(self.host)), OK)
        return self.host

    def attach(self, vendor=0x02df, device=0x9103):
        self.identified()
        card = Card()
        card.host = C.pointer(self.host)
        rc = self.lib.sdio_attach(C.byref(card), vendor, device)
        return rc, card

    # --- encodings -----------------------------------------------------------
    def test_command_word_puts_the_index_then_the_argument_msb_first(self):
        self.assertEqual(self.lib.hbsdc_cmd_word(52, 0x12345678), 0x56341274)
        self.assertEqual(self.lib.hbsdc_cmd_word(5, 0), 0x45)

    def test_response_is_the_32_status_bits_of_a_48_bit_frame(self):
        # FIFO bytes: index, status 31..24, 23..16, 15..8 | status 7..0, CRC.
        self.assertEqual(self.lib.hbsdc_response(0x56341234, 0xff78), 0x12345678)

    def test_command_type_matches_the_factory_encoding(self):
        cmdty = self.lib.hbsdc_cmdty
        self.assertEqual(cmdty(0x15, 0), 0x06)          # R1/R5/R6
        self.assertEqual(cmdty(0x01, 0), 0x02)          # R4, no CRC
        self.assertEqual(cmdty(0x15, 0x50), 0x26)       # single-block read
        self.assertEqual(cmdty(0x15, 0x30), 0xb6)       # multi-block write
        self.assertEqual(cmdty(0x17, 0), 0x03)          # 136-bit drops CRC
        self.assertEqual(cmdty(0x1d, 0), 0x0e)          # busy

    # --- module identification ----------------------------------------------
    def test_identify_reads_clock_interrupt_and_fifo_from_the_index_registers(self):
        host = self.identified()
        self.assertEqual((host.major, host.clock_hz, host.irq), (3, 99000000, 56))
        self.assertEqual(host.fifo_bytes, 512)
        self.assertTrue(all(off == INDEX for off, _ in self.fpga.writes))

    def test_identify_refuses_another_module_without_further_writes(self):
        self.fpga.index[0] = 0x49      # the PicoBlaze engine
        self.assertEqual(self.lib.hbsdc_identify(C.byref(self.host)), EINVAL)
        self.assertEqual(self.fpga.writes, [(INDEX, 0)])

    def test_identify_applies_the_old_fpga_rules(self):
        self.fpga.index.update({1: 2, 3: 4})
        self.assertEqual(self.lib.hbsdc_identify(C.byref(self.host)), EINVAL)
        self.fpga.index.update({3: 5, 0xf: 0x0c})
        self.assertEqual(self.lib.hbsdc_identify(C.byref(self.host)), OK)
        self.assertEqual((self.host.clock_hz, self.host.irq), (66000000, 13))

    # --- commands --------------------------------------------------------------
    def test_attach_follows_the_factory_sequence_and_reads_the_cis(self):
        rc, card = self.attach()
        self.assertEqual(rc, OK, card.step)
        self.assertEqual((card.vendor, card.device), (0x02df, 0x9103))
        self.assertEqual(card.manf, b'Marvell/802.11 SDIO ID: 0B')
        self.assertEqual((card.nfunc, card.rca, card.width), (1, 1, 4))
        self.assertEqual((card.cis[0], card.cis[1], card.fn_code[1]), (0x1000, 0x2000, 7))
        mem = self.card_model.mem[0]
        self.assertEqual((mem[0x02], mem[0x04], mem[0x07]), (0x02, 0x03, 0x82))
        self.assertEqual(card.cccr[0x04], 0x03)     # kept from the read-back
        opcodes = [op for op, _ in self.card_model.commands]
        self.assertEqual(opcodes[:4], [5, 5, 3, 7])
        self.assertEqual(self.card_model.commands[1][1], 0xff8000)
        self.assertEqual(self.card_model.commands[3][1], 0x00010000)
        # Power: down, 10 ms, DEV_RESET pulse, up with bits 0-1, 10 ms.
        self.assertEqual(self.fpga.written(POWER)[:2], [0, 3])
        self.assertEqual(self.fpga.written(CTRL)[:3], [0x80, 0, 1])
        self.assertEqual(self.fpga.delays[:3], [10, 1, 10])
        self.assertTrue(self.fpga.regs[CTRL] & 0x2)    # host switched to 4-bit
        self.assertEqual(card.ready_ms, 1)
        self.assertEqual(self.host.interrupts, self.fpga.irq_done)
        self.assertEqual(self.host.polls, 0)

    def test_each_completion_masks_and_clears_only_the_fired_bits(self):
        self.attach()
        self.assertEqual(self.fpga.regs.get(FLAG, 0), 0)
        self.assertEqual(self.fpga.regs[IEN], 0x1843 & ~CMD_RDY)
        self.assertEqual(self.host.resp_index, 52)

    def test_a_dead_interrupt_line_still_completes_by_polling(self):
        self.fpga.irq_line = False
        rc, card = self.attach()
        self.assertEqual(rc, OK, card.step)
        self.assertEqual(self.host.interrupts, 0)
        self.assertGreater(self.host.polls, 10)
        self.assertEqual(self.fpga.irq_done, 0)
        # Each latched completion is seen after one 10 ms slice, so a whole
        # attach stays far inside the probe's 60 s deadline.
        self.assertEqual(self.fpga.wait_slices, {10})
        self.assertLessEqual(self.fpga.waited_ms, 10 * self.host.polls)
        self.assertLess(self.fpga.waited_ms, 5000)

    def test_a_card_that_never_answers_fails_at_read_ocr(self):
        self.card_model.dead = True
        rc, card = self.attach()
        self.assertEqual((rc, card.step), (ECARD, b'read_ocr'))
        self.assertEqual(len(self.card_model.commands), 100)

    def test_a_silent_host_gives_up_after_three_unanswered_waits(self):
        self.fpga.silent = True                    # no FLAG bit ever latches
        rc, card = self.attach()
        self.assertEqual((rc, card.step), (ETIMEDOUT, b'read_ocr'))
        self.assertEqual(self.fpga.starts, 3)
        self.assertEqual(self.fpga.waited_ms, 3000)   # 1 s per command

    def test_r5_error_flags_fail_the_register_access(self):
        rc, card = self.attach()
        self.assertEqual(rc, OK)
        self.card_model.r5_flags = 0x50          # ILLEGAL_COMMAND in CMD state
        out = C.c_uint8()
        self.assertEqual(self.lib.sdio_cmd52(C.byref(card), 1, 0x40, 0, 0, C.byref(out)), ER5)
        self.assertEqual(card.r5 >> 8, 0x50)
        self.card_model.r5_flags = 0x00          # DIS state
        self.assertEqual(self.lib.sdio_cmd52(C.byref(card), 1, 0x40, 0, 0, C.byref(out)), ER5)

    def test_identity_mismatch_is_reported_after_reading_the_cis(self):
        rc, card = self.attach(device=0x9104)
        self.assertEqual((rc, card.step), (EID, b'identity'))
        self.assertEqual(card.device, 0x9103)

    def test_a_missing_card_is_never_powered(self):
        self.fpga.regs[POWER] = 0x2000
        rc, card = self.attach()
        self.assertEqual((rc, card.step), (ECARD, b'detect'))
        self.assertEqual(self.fpga.written(CTRL), [])
        self.assertEqual(self.fpga.written(POWER), [])

    def test_a_pending_change_flag_is_acknowledged_then_detect_reread(self):
        self.fpga.regs[POWER] = 0x0080
        rc, card = self.attach()
        self.assertEqual(rc, OK, card.step)
        self.assertEqual(card.power_raw, 0x80)
        self.assertEqual(self.fpga.written(POWER)[0], 0x80)
        self.assertEqual(self.host.changes, 1)

    def test_a_change_that_reveals_no_card_fails_detect_unpowered(self):
        self.fpga.regs[POWER] = 0x2080
        rc, card = self.attach()
        self.assertEqual((rc, card.step), (ECARD, b'detect'))
        self.assertEqual(self.fpga.written(POWER), [0x2080])
        self.assertEqual(self.fpga.written(CTRL), [])

    def test_ocr_without_a_voltage_window_is_reread_as_the_factory_does(self):
        self.card_model.ocr_window_after = 2
        rc, card = self.attach()
        self.assertEqual(rc, OK, card.step)
        reads = [arg for op, arg in self.card_model.commands if op == 5]
        self.assertEqual(reads[:3], [0, 0, 0])
        self.assertEqual(reads[3], 0xff8000)
        # No delay after a response that arrived, as in the factory.
        baseline = FakeModule(FakeCard())
        host = Host(); host.io = C.pointer(baseline.io)
        self.assertEqual(self.lib.hbsdc_identify(C.byref(host)), OK)
        other = Card(); other.host = C.pointer(host)
        self.assertEqual(self.lib.sdio_attach(C.byref(other), 0x02df, 0x9103), OK)
        self.assertEqual(self.fpga.delays, baseline.delays)

    def test_ocr_that_never_shows_a_voltage_window_is_refused_after_five_reads(self):
        self.card_model.ocr_window_after = 99
        rc, card = self.attach()
        self.assertEqual((rc, card.step), (EID, b'read_ocr'))
        self.assertEqual(len(self.card_model.commands), 5)

    def test_rca_is_reissued_once_after_a_failed_cmd3(self):
        self.card_model.fail_once = {3}
        rc, card = self.attach()
        self.assertEqual(rc, OK, card.step)
        self.assertEqual([op for op, _ in self.card_model.commands].count(3), 2)
        self.assertEqual(card.rca, 1)

    def test_a_busy_host_refuses_without_touching_the_fifo(self):
        self.identified()
        self.fpga.regs[STAT] = 0x8000
        cmd = Cmd(opcode=52, resp=0x15)
        self.assertEqual(self.lib.hbsdc_command(C.byref(self.host), C.byref(cmd)), EBUSY)
        self.assertEqual(self.fpga.cmd_words, [])

    def test_cis_walk_is_bounded_without_a_terminator(self):
        self.card_model.mem[0] = {k: v for k, v in self.card_model.mem[0].items()
                                  if k < 0x1000}
        for offset in range(0, 600, 2):
            self.card_model.mem[0][0x1000 + offset] = 0x10
        rc, card = self.attach()
        self.assertEqual((rc, card.step), (EID, b'read_cis'))
        tuple_reads = [arg for op, arg in self.card_model.commands
                       if op == 52 and ((arg >> 9) & 0x1ffff) >= 0x1000]
        self.assertLessEqual(len(tuple_reads), 2 * 64)

    # --- clock, data and shutdown ----------------------------------------------
    def test_25_mhz_uses_the_fast_clock_path(self):
        self.identified()
        self.assertEqual(self.lib.hbsdc_set_clock(C.byref(self.host), 25000000), OK)
        self.assertEqual((self.host.bus_hz, self.fpga.regs[0x20]), (25000000, 2))
        self.assertTrue(self.fpga.regs[CTRL] & 0x1000)
        self.assertEqual(self.lib.hbsdc_set_clock(C.byref(self.host), 400000), OK)
        self.assertEqual((self.fpga.regs[0x20], self.host.bus_hz), (124, 399193))
        self.assertFalse(self.fpga.regs[CTRL] & 0x1800)

    def test_cmd53_moves_whole_blocks_by_pio(self):
        rc, card = self.attach()
        self.assertEqual(rc, OK)
        payload = bytes(range(256)) * 2
        buf = C.create_string_buffer(payload, len(payload))
        self.assertEqual(self.lib.sdio_cmd53(C.byref(card), 1, 0x10000, 0, 1, buf, 2, 256), OK)
        self.assertEqual(bytes(self.card_model.data_in), payload)
        self.assertEqual((self.fpga.regs[BLKCNT], self.fpga.regs[BLKSZ]), (2, 256))
        self.assertEqual((self.card_model.mem[0][0x110], self.card_model.mem[0][0x111]), (0, 1))
        op, arg = self.card_model.commands[-1]
        self.assertEqual((op, arg), (53, 0x80000000 | 1 << 28 | 1 << 27 | 0x10000 << 9 | 2))
        self.assertEqual(self.fpga.regs[CMDTY] & 0x80, 0)
        self.assertEqual(self.fpga.regs[CTRL] & 0x4, 0)
        out = C.create_string_buffer(256)
        self.assertEqual(self.lib.sdio_cmd53(C.byref(card), 1, 0x300, 1, 0, out, 1, 256), OK)
        self.assertEqual(out.raw, bytes(range(256)))
        self.assertTrue(self.fpga.regs[CTRL] & 0x4)

    def test_cmd53_reports_a_data_error_that_latches_after_the_command(self):
        rc, card = self.attach()
        self.assertEqual(rc, OK)
        self.fpga.data_error = 0x0008               # data CRC
        out = C.create_string_buffer(256)
        self.assertEqual(self.lib.sdio_cmd53(C.byref(card), 1, 0x300, 1, 0, out, 1, 256), EDATA)
        self.assertEqual(self.fpga.regs.get(FLAG, 0) & 0x0008, 0)   # acknowledged
        self.assertEqual(self.lib.sdio_cmd53(C.byref(card), 1, 0x300, 1, 0, out, 1, 256), OK)

    def test_cmd53_rejects_partial_words(self):
        rc, card = self.attach()
        buf = C.create_string_buffer(6)
        self.assertEqual(self.lib.sdio_cmd53(C.byref(card), 1, 0, 0, 1, buf, 1, 6), EINVAL)

    def test_a_fixed_address_read_returns_one_register_throughout(self):
        rc, card = self.attach()
        self.assertEqual(rc, OK)
        self.card_model.mem[1][0x40] = 0xdc
        out = C.create_string_buffer(512)
        self.assertEqual(self.lib.sdio_cmd53(C.byref(card), 1, 0x40, 0, 0, out, 2, 256), OK)
        self.assertEqual(out.raw, b'\xdc' * 512)
        op, arg = self.card_model.commands[-1]
        self.assertEqual((op, arg), (53, 1 << 28 | 1 << 27 | 0x40 << 9 | 2))

    # --- transfer timing -------------------------------------------------------
    def timed(self, with_counter=True):
        rc, card = self.attach()
        self.assertEqual(rc, OK)
        if not with_counter:
            self.fpga.io.ticks = TICKS()
        # The first CMD53 sets the block size with CMD52s; keep that out.
        warm = C.create_string_buffer(256)
        self.assertEqual(self.lib.sdio_cmd53(C.byref(card), 1, 0x300, 1, 0, warm, 1, 256), OK)
        stats = Stats()
        self.host.stats = C.pointer(stats)
        return card, stats

    def test_transfers_are_timed_by_direction_and_size(self):
        card, stats = self.timed()
        buf = C.create_string_buffer(12 * 256)
        cmd53 = self.lib.sdio_cmd53
        self.assertEqual(cmd53(C.byref(card), 1, 0x10000, 0, 1, buf, 2, 256), OK)
        self.assertEqual(cmd53(C.byref(card), 1, 0x10000, 0, 0, buf, 1, 256), OK)
        self.assertEqual(cmd53(C.byref(card), 1, 0x10000, 0, 0, buf, 1, 256), OK)
        self.assertEqual(cmd53(C.byref(card), 1, 0x10000, 0, 0, buf, 12, 256), OK)
        write2, read1, read_many = stats.xfer[0][1], stats.xfer[1][0], stats.xfer[1][8]
        self.assertEqual((write2.n, write2.bytes), (1, 512))
        self.assertEqual((read1.n, read1.bytes), (2, 512))
        self.assertEqual((read_many.n, read_many.bytes), (1, 3072))
        # The data phase is the PIO loop alone: one register access per word.
        self.assertEqual(write2.data, 128)
        self.assertEqual((read1.data, read1.data_max), (128, 64))
        self.assertEqual(read_many.data, 768)
        for phase in (write2, read1, read_many):
            self.assertGreater(phase.setup, 0)
            self.assertGreater(phase.drain, 0)
            self.assertGreaterEqual(phase.total_max, phase.data_max)
        others = [stats.xfer[d][b].n for d in range(2) for b in range(BUCKETS)]
        self.assertEqual(sum(others), 4)
        self.assertEqual((stats.cmd_n, stats.failures), (0, 0))

    def test_commands_without_data_are_timed_apart_from_transfers(self):
        card, stats = self.timed()
        out = C.c_uint8()
        for _ in range(3):
            self.assertEqual(self.lib.sdio_cmd52(C.byref(card), 1, 0x40, 0, 0, C.byref(out)), OK)
        self.assertEqual(stats.cmd_n, 3)
        self.assertGreater(stats.cmd_ticks, 0)
        self.assertLessEqual(stats.cmd_max, stats.cmd_ticks)
        self.assertGreaterEqual(stats.cmd_max * 3, stats.cmd_ticks)
        self.assertEqual(sum(stats.xfer[d][b].n for d in range(2) for b in range(BUCKETS)), 0)

    def test_a_failed_transfer_counts_as_a_failure_not_a_timing(self):
        card, stats = self.timed()
        self.fpga.data_error = 0x0008
        out = C.create_string_buffer(256)
        self.assertEqual(self.lib.sdio_cmd53(C.byref(card), 1, 0x300, 1, 0, out, 1, 256), EDATA)
        self.assertEqual((stats.xfer[1][0].n, stats.failures), (0, 1))
        self.card_model.fail_once = {52}
        value = C.c_uint8()
        self.assertEqual(self.lib.sdio_cmd52(C.byref(card), 1, 0x40, 0, 0, C.byref(value)), OK)
        self.assertEqual((stats.cmd_n, stats.failures), (1, 2))   # the retry succeeded

    def test_nothing_is_timed_without_a_counter_or_without_stats(self):
        card, stats = self.timed(with_counter=False)
        buf = C.create_string_buffer(256)
        self.assertEqual(self.lib.sdio_cmd53(C.byref(card), 1, 0x300, 1, 0, buf, 1, 256), OK)
        self.assertEqual(stats.xfer[1][0].n, 0)
        self.fpga.io.ticks = TICKS(self.fpga.ticks)
        self.host.stats = None
        self.assertEqual(self.lib.sdio_cmd53(C.byref(card), 1, 0x300, 1, 0, buf, 1, 256), OK)
        self.assertEqual(stats.xfer[1][0].n, 0)

    def test_shutdown_resets_the_device_and_powers_down(self):
        self.identified()
        self.fpga.writes.clear()
        self.lib.hbsdc_shutdown(C.byref(self.host))
        self.assertEqual(self.fpga.writes, [(IEN, 0), (CTRL, 0x80), (POWER, 0)])
        self.assertEqual(self.fpga.delays, [1])


if __name__ == '__main__':
    unittest.main()
