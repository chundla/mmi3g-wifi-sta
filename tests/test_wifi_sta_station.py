"""Station control for the io-net driver against the fake module and firmware.

The fake firmware below keeps the MAC it was given, answers scans with the
descriptors a test chooses and ASSOCIATE with 0x8012, as the station image
did on the unit in 5.0. Everything else is the command-layer fake. The
responses are choices for the tests, not observations of the chip.
"""
import ctypes as C
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_wifi_sta_cmd import (ASSOCIATE, DEAUTHENTICATE, ECMDRESULT, FUNC_INIT, FUNC_SHUTDOWN,
                               GET_HW_SPEC, HW_SPEC_BODY, IOPORT, MAC_ADDRESS, MAC_CONTROL, OK,
                               RSN_CCMP, SCAN, Bss, Cmd, HwSpec, StationCard, StationModule,
                               bss_descriptor, ie, le16, le32, packet, scan_response)
from test_wifi_sta_host import Card, Host, ROOT

SET_WEP, ENABLE_RSN, AUTHENTICATE, RADIO_CONTROL, KEY_MATERIAL = 0x13, 0x2f, 0x11, 0x1c, 0x5e
ENOTFOUND, ENOTOPEN, EASSOC, ESTATE, EMAC, EINVAL, ENOTSCANNED = -40, -41, -42, -43, -44, -45, -46
DOWN, IDLE, ASSOCIATED = 0, 1, 2
MAC = bytes.fromhex('001cd7000001')
BSSID = bytes.fromhex('020000aabbcc')
OPEN_IES = ie(0, b'bench') + ie(1, bytes.fromhex('82848b96')) + ie(3, b'\x01')
WPA2_IES = OPEN_IES + RSN_CCMP

LOG_CB = C.CFUNCTYPE(None, C.c_void_p, C.c_char_p)


class Sta(C.Structure):
    _fields_ = [('cmd', Cmd), ('state', C.c_int), ('func_init', C.c_int),
                ('mac', C.c_uint8 * 6), ('spec', HwSpec), ('bss', Bss),
                ('assoc_status', C.c_uint16), ('aid', C.c_uint16),
                ('assoc_capability', C.c_uint16), ('scans', C.c_uint), ('heard', C.c_uint),
                ('found', C.c_uint), ('link_mark', C.c_uint), ('link_cause', C.c_uint32),
                ('connects', C.c_uint), ('link_losses', C.c_uint),
                ('scans_done', C.c_uint), ('key_sets', C.c_uint), ('rsn', C.c_int),
                ('step', C.c_char_p), ('rc', C.c_int), ('log', LOG_CB),
                ('log_ctx', C.c_void_p), ('seen', Bss * 32), ('result_n', C.c_uint),
                ('result', Bss * 32)]


class Firmware:
    """Command answers for the station image, by command code."""

    def __init__(self):
        self.mac = bytes(6)
        self.results = {}           # command -> result word
        self.status = 0             # ASSOCIATE status
        self.scans = []             # one descriptor list per SCAN command
        self.mac_get_refused = False
        self.mac_get_override = None

    def __call__(self, cmd):
        command, size, seq = (int.from_bytes(cmd[i:i + 2], 'little') for i in (0, 2, 4))
        body = cmd[8:size]
        code, result = command | 0x8000, self.results.get(command, 0)
        if command == GET_HW_SPEC:
            body = HW_SPEC_BODY
        elif command == MAC_ADDRESS:
            action = int.from_bytes(body[:2], 'little')
            if action == 1:
                self.mac = body[2:8]
            elif self.mac_get_refused:
                result = 1
            body = body[:2] + (self.mac_get_override or self.mac)
        elif command == SCAN:
            descriptors = self.scans.pop(0) if self.scans else []
            body = scan_response(descriptors)
        elif command == ASSOCIATE:
            code = 0x8012
            body = le16(0x0401) + le16(self.status) + le16(0xc001)
        return [packet(1, le16(code) + le16(8 + len(body)) + le16(seq) + le16(result) + body)]


class StationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('cc')
        if not compiler:
            raise RuntimeError('C compiler required for the station tests')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        so = Path(cls.tmp.name) / 'mv8688sta.so'
        subprocess.run([compiler, '-std=c99', '-Wall', '-Wextra', '-Werror', '-shared',
                        '-fPIC'] + [str(ROOT / 'wifi-sta' / name) for name in
                                    ('hbsdc.c', 'sdio.c', 'mv8688fw.c', 'mv8688cmd.c',
                                     'mv8688sta.c')]
                       + ['-o', str(so)], check=True, capture_output=True)
        lib = cls.lib = C.CDLL(str(so))
        lib.hbsdc_identify.argtypes = [C.POINTER(Host)]
        lib.sdio_attach.argtypes = [C.POINTER(Card), C.c_uint16, C.c_uint16]
        lib.sdio_set_blksz.argtypes = [C.POINTER(Card), C.c_uint, C.c_uint]
        lib.mv8688_sta_start.argtypes = [C.POINTER(Sta), C.c_char_p]
        lib.mv8688_sta_connect.argtypes = [C.POINTER(Sta), C.c_char_p, C.c_uint]
        lib.mv8688_sta_disconnect.argtypes = [C.POINTER(Sta)]
        lib.mv8688_sta_poll.argtypes = [C.POINTER(Sta), C.c_uint]
        lib.mv8688_sta_send.argtypes = [C.POINTER(Sta), C.c_char_p, C.c_uint]
        lib.mv8688_sta_stop.argtypes = [C.POINTER(Sta)]
        lib.mv8688_sta_scan.argtypes = [C.POINTER(Sta), C.c_char_p, C.c_uint]
        lib.mv8688_sta_join.argtypes = [C.POINTER(Sta), C.c_char_p, C.c_char_p, C.c_uint,
                                        C.c_char_p, C.c_uint]
        lib.mv8688_sta_set_key.argtypes = [C.POINTER(Sta), C.c_uint, C.c_int, C.c_char_p,
                                           C.c_uint]
        lib.mv8688_sta_event_name.argtypes = [C.c_uint32]
        lib.mv8688_sta_event_name.restype = C.c_char_p

    def setUp(self):
        self.card_model = StationCard(5)
        self.fw = Firmware()
        self.card_model.answer = self.fw
        self.fpga = StationModule(self.card_model)
        self.host = Host()
        self.host.io = C.pointer(self.fpga.io)
        self.assertEqual(self.lib.hbsdc_identify(C.byref(self.host)), OK)
        self.card = Card()
        self.card.host = C.pointer(self.host)
        self.assertEqual(self.lib.sdio_attach(C.byref(self.card), 0x02df, 0x9103), OK)
        self.assertEqual(self.lib.sdio_set_blksz(C.byref(self.card), 1, 256), OK)
        self.sta = Sta()
        self.sta.cmd.card = C.pointer(self.card)
        self.sta.cmd.ioport = IOPORT
        self.sta.cmd.rx_unit = 5
        self.lines = []
        self.log_cb = LOG_CB(lambda ctx, line: self.lines.append(line.decode()))
        self.sta.log = self.log_cb

    def written(self):
        """Command codes written to the card, in order; 0 stands for a data frame."""
        out = []
        for _, _, raw in self.card_model.received:
            kind = int.from_bytes(raw[2:4], 'little')
            out.append(int.from_bytes(raw[4:6], 'little') if kind == 1 else 0)
        return out

    def start(self):
        self.assertEqual(self.lib.mv8688_sta_start(C.byref(self.sta), MAC), OK)
        self.card_model.received.clear()

    def connect(self, ssid=b'bench'):
        return self.lib.mv8688_sta_connect(C.byref(self.sta), ssid, len(ssid))

    def open_bss(self, rssi=40, bssid=BSSID, ies=OPEN_IES, capability=0x0401):
        return bss_descriptor(bssid, rssi, ies, capability=capability)

    def test_start_sets_the_firmware_up_as_the_probe_did(self):
        self.assertEqual(self.lib.mv8688_sta_start(C.byref(self.sta), MAC), OK)
        self.assertEqual(self.written(), [FUNC_INIT, GET_HW_SPEC, MAC_ADDRESS, MAC_ADDRESS,
                                          MAC_CONTROL])
        self.assertEqual((self.sta.state, self.sta.func_init), (IDLE, 1))
        self.assertEqual(self.fw.mac, MAC)
        self.assertEqual(self.card_model.mask, 0x0f)
        # MAC_CONTROL: RX and TX on, nothing else.
        raw = self.card_model.received[-1][2]
        self.assertEqual(raw[12:16], bytes((3, 0, 0, 0)))
        self.assertEqual(self.sta.spec.fwcapinfo, 0x303)
        self.assertTrue(any('set and read back' in line for line in self.lines))

    def test_a_refused_mac_read_back_is_tolerated(self):
        self.fw.mac_get_refused = True
        self.assertEqual(self.lib.mv8688_sta_start(C.byref(self.sta), MAC), OK)
        self.assertEqual(self.sta.state, IDLE)
        self.assertIsNone(self.sta.step)

    def test_a_different_mac_read_back_fails_and_stop_still_shuts_down(self):
        self.fw.mac_get_override = bytes.fromhex('aabbccddeeff')
        self.assertEqual(self.lib.mv8688_sta_start(C.byref(self.sta), MAC), EMAC)
        self.assertEqual((self.sta.state, self.sta.step), (DOWN, b'mac_readback'))
        self.card_model.received.clear()
        self.assertEqual(self.lib.mv8688_sta_stop(C.byref(self.sta)), OK)
        self.assertEqual(self.written(), [FUNC_SHUTDOWN])
        self.assertEqual(self.card_model.mask, 0)

    def test_a_failed_func_init_owes_no_shutdown(self):
        self.fw.results[FUNC_INIT] = 1
        self.assertEqual(self.lib.mv8688_sta_start(C.byref(self.sta), MAC), ECMDRESULT)
        self.assertEqual(self.sta.func_init, 0)
        self.card_model.received.clear()
        self.assertEqual(self.lib.mv8688_sta_stop(C.byref(self.sta)), OK)
        self.assertEqual(self.written(), [])

    def test_connect_scans_for_the_ssid_then_joins_in_libertas_order(self):
        self.start()
        self.fw.scans = [[self.open_bss()], [], []]
        self.assertEqual(self.connect(), OK)
        self.assertEqual(self.written(), [SCAN] * 3 + [SET_WEP, KEY_MATERIAL, KEY_MATERIAL,
                                                       ENABLE_RSN, AUTHENTICATE, RADIO_CONTROL,
                                                       ASSOCIATE])
        self.assertEqual((self.sta.state, self.sta.aid, self.sta.assoc_status), (ASSOCIATED,
                                                                                 0xc001, 0))
        self.assertEqual((self.sta.scans, self.sta.found, self.sta.connects), (1, 1, 1))
        self.assertEqual(bytes(self.sta.bss.bssid), BSSID)
        # Each scan is active, for this SSID only: its SSID TLV leads the body
        # after the BSS type and the any-BSSID.
        for _, _, raw in self.card_model.received[:3]:
            self.assertEqual(raw[19:28], le16(0) + le16(5) + b'bench')

    def test_the_strongest_bss_with_the_ssid_is_chosen(self):
        self.start()
        weak = self.open_bss(rssi=70, bssid=bytes.fromhex('020000000001'))
        other = bss_descriptor(bytes.fromhex('020000000002'), 20, ie(0, b'other') + ie(3, b'\x06'))
        strong = self.open_bss(rssi=45, bssid=bytes.fromhex('020000000003'))
        self.fw.scans = [[weak, other], [strong], []]
        self.assertEqual(self.connect(), OK)
        self.assertEqual(bytes(self.sta.bss.bssid), bytes.fromhex('020000000003'))
        self.assertEqual((self.sta.found, self.sta.heard), (2, 3))

    def test_an_ssid_heard_on_the_second_scan_is_joined(self):
        self.start()
        self.fw.scans = [[], [], [], [self.open_bss()], [], []]
        self.assertEqual(self.connect(), OK)
        self.assertEqual(self.sta.scans, 2)

    def test_an_absent_ssid_fails_after_three_scans_without_joining(self):
        self.start()
        self.assertEqual(self.connect(), ENOTFOUND)
        self.assertEqual(self.written(), [SCAN] * 9)
        self.assertEqual((self.sta.state, self.sta.step), (IDLE, b'ssid_absent'))

    def test_a_scan_refused_on_every_group_is_reported_as_refused(self):
        self.start()
        self.fw.results[SCAN] = 1
        self.assertEqual(self.connect(), ECMDRESULT)
        self.assertEqual(self.sta.step, b'scan_refused')
        self.assertEqual(self.written(), [SCAN] * 3)

    def test_one_refused_group_does_not_end_the_scan(self):
        self.start()
        refusing = self.fw.__call__

        def answer(cmd):
            self.fw.results[SCAN] = 1 if len(self.written()) == 1 else 0
            return refusing(cmd)
        self.card_model.answer = answer
        self.fw.scans = [[], [self.open_bss()]]
        self.assertEqual(self.connect(), OK)

    def test_a_protected_bss_is_refused_before_authentication(self):
        self.start()
        for capability, ies in ((0x0411, OPEN_IES), (0x0401, OPEN_IES + ie(48, bytes(20))),
                                (0x0402, OPEN_IES)):
            with self.subTest(capability=capability):
                self.card_model.received.clear()
                self.fw.scans = [[self.open_bss(capability=capability, ies=ies)], [], []]
                self.assertEqual(self.connect(), ENOTOPEN)
                self.assertNotIn(AUTHENTICATE, self.written())
                self.assertEqual(self.sta.state, IDLE)

    def test_a_bss_off_the_scanned_channels_is_refused(self):
        self.start()
        ies = ie(0, b'bench') + ie(1, b'\x82') + ie(3, b'\x0d')
        self.fw.scans = [[self.open_bss(ies=ies)], [], []]
        self.assertEqual(self.connect(), ENOTOPEN)

    def test_a_refused_association_is_followed_by_a_deauthentication(self):
        self.start()
        self.fw.status = 1
        self.fw.scans = [[self.open_bss()], [], []]
        self.assertEqual(self.connect(), EASSOC)
        self.assertEqual(self.written()[-2:], [ASSOCIATE, DEAUTHENTICATE])
        self.assertEqual((self.sta.state, self.sta.step, self.sta.assoc_status),
                         (IDLE, b'assoc_status', 1))

    def test_a_failure_before_associate_sends_no_deauthentication(self):
        self.start()
        self.fw.results[AUTHENTICATE] = 1
        self.fw.scans = [[self.open_bss()], [], []]
        self.assertEqual(self.connect(), ECMDRESULT)
        self.assertEqual(self.written()[-1], AUTHENTICATE)

    def test_refused_key_clears_are_tolerated(self):
        self.start()
        self.fw.results.update({SET_WEP: 1, KEY_MATERIAL: 1})
        self.fw.scans = [[self.open_bss()], [], []]
        self.assertEqual(self.connect(), OK)
        self.assertIsNone(self.sta.step)

    def test_connect_needs_the_idle_state_and_a_valid_ssid(self):
        self.assertEqual(self.connect(), ESTATE)
        self.start()
        self.assertEqual(self.connect(b''), EINVAL)
        self.assertEqual(self.connect(b'x' * 33), EINVAL)
        self.fw.scans = [[self.open_bss()], [], []]
        self.assertEqual(self.connect(), OK)
        self.assertEqual(self.connect(), ESTATE)
        self.assertEqual(self.written().count(ASSOCIATE), 1)

    def associated(self):
        self.start()
        self.fw.scans = [[self.open_bss()], [], []]
        self.assertEqual(self.connect(), OK)
        self.card_model.received.clear()

    def test_an_event_that_ends_the_link_returns_to_idle(self):
        self.associated()
        self.card_model.uploads += [packet(3, le32(0x30)), packet(3, le32(0x0108))]
        self.assertEqual(self.lib.mv8688_sta_poll(C.byref(self.sta), 20), OK)
        self.assertEqual((self.sta.state, self.sta.link_losses, self.sta.link_cause),
                         (IDLE, 1, 0x0108))
        self.assertTrue(any('deauthenticated' in line for line in self.lines))

    def test_a_disconnect_event_before_the_association_does_not_end_the_link(self):
        self.start()
        self.card_model.uploads.append(packet(3, le32(0x09)))
        self.assertEqual(self.lib.mv8688_sta_poll(C.byref(self.sta), 5), OK)
        self.fw.scans = [[self.open_bss()], [], []]
        self.assertEqual(self.connect(), OK)
        self.assertEqual(self.lib.mv8688_sta_poll(C.byref(self.sta), 5), OK)
        self.assertEqual((self.sta.state, self.sta.link_losses), (ASSOCIATED, 0))

    def test_the_link_end_is_seen_after_the_event_record_is_full(self):
        self.associated()
        self.card_model.uploads += [packet(3, le32(0x30))] * 10 + [packet(3, le32(0x03))]
        self.assertEqual(self.lib.mv8688_sta_poll(C.byref(self.sta), 30), OK)
        self.assertEqual((self.sta.state, self.sta.link_cause), (IDLE, 0x03))

    def test_frames_go_out_only_while_associated(self):
        frame = b'\xff' * 6 + MAC + b'\x08\x06' + bytes(28)
        self.start()
        self.assertEqual(self.lib.mv8688_sta_send(C.byref(self.sta), frame, len(frame)), ESTATE)
        self.assertEqual(self.written(), [])
        self.fw.scans = [[self.open_bss()], [], []]
        self.assertEqual(self.connect(), OK)
        self.card_model.received.clear()
        self.assertEqual(self.lib.mv8688_sta_send(C.byref(self.sta), frame, len(frame)), OK)
        self.assertEqual(self.written(), [0])
        raw = self.card_model.received[0][2]
        self.assertEqual(raw[4 + 24:4 + 24 + len(frame)], frame)

    def test_a_link_end_during_a_send_is_noticed(self):
        self.associated()
        self.card_model.on_data = lambda data: [packet(3, le32(0x09))]
        frame = b'\xff' * 6 + MAC + b'\x08\x06' + bytes(28)
        self.assertEqual(self.lib.mv8688_sta_send(C.byref(self.sta), frame, len(frame)), OK)
        self.assertEqual(self.sta.state, IDLE)

    def test_disconnect_deauthenticates_the_joined_bss(self):
        self.associated()
        self.assertEqual(self.lib.mv8688_sta_disconnect(C.byref(self.sta)), OK)
        self.assertEqual(self.written(), [DEAUTHENTICATE])
        raw = self.card_model.received[0][2]
        self.assertEqual(raw[12:20], BSSID + le16(3))
        self.assertEqual(self.sta.state, IDLE)
        self.card_model.received.clear()
        self.assertEqual(self.lib.mv8688_sta_disconnect(C.byref(self.sta)), OK)
        self.assertEqual(self.written(), [])

    def test_a_refused_deauthentication_still_leaves_the_station_idle(self):
        self.associated()
        self.fw.results[DEAUTHENTICATE] = 1
        self.assertEqual(self.lib.mv8688_sta_disconnect(C.byref(self.sta)), ECMDRESULT)
        self.assertEqual(self.sta.state, IDLE)

    def test_stop_leaves_shuts_down_and_masks(self):
        self.associated()
        self.assertEqual(self.lib.mv8688_sta_stop(C.byref(self.sta)), OK)
        self.assertEqual(self.written(), [DEAUTHENTICATE, FUNC_SHUTDOWN])
        self.assertEqual((self.sta.state, self.sta.func_init, self.card_model.mask), (DOWN, 0, 0))

    def test_stop_tries_every_step_and_reports_the_first_failure(self):
        self.associated()
        self.fw.results[DEAUTHENTICATE] = 1
        self.assertEqual(self.lib.mv8688_sta_stop(C.byref(self.sta)), ECMDRESULT)
        self.assertEqual(self.written(), [DEAUTHENTICATE, FUNC_SHUTDOWN])
        self.assertEqual((self.sta.state, self.card_model.mask), (DOWN, 0))

    def test_event_names(self):
        self.assertEqual(self.lib.mv8688_sta_event_name(0x0108), b'deauthenticated')
        self.assertEqual(self.lib.mv8688_sta_event_name(0x30), b'other')

    # --- phase 6: scan, join and keys for a supplicant -------------------

    def scan(self, ssid=b''):
        return self.lib.mv8688_sta_scan(C.byref(self.sta), ssid, len(ssid))

    def join(self, element=RSN_CCMP, bssid=BSSID, ssid=b'bench'):
        return self.lib.mv8688_sta_join(C.byref(self.sta), bssid, ssid, len(ssid), element,
                                        len(element))

    def wpa_bss(self, **kw):
        kw.setdefault('ies', WPA2_IES)
        kw.setdefault('capability', 0x0411)
        return self.open_bss(**kw)

    def test_a_scan_without_an_ssid_is_passive_and_keeps_every_bss(self):
        self.start()
        other = bss_descriptor(bytes.fromhex('020000000002'), 60, ie(0, b'other') + ie(3, b'\x06'))
        self.fw.scans = [[self.wpa_bss()], [other], []]
        self.assertEqual(self.scan(), OK)
        self.assertEqual(self.written(), [SCAN] * 3)
        self.assertEqual((self.sta.result_n, self.sta.scans_done, self.sta.state), (2, 1, IDLE))
        self.assertEqual(bytes(self.sta.result[0].ie)[:self.sta.result[0].ie_len], WPA2_IES)
        # No SSID TLV: the channel list follows the BSS type and any-BSSID,
        # each channel passive (bit 0) with the 200 ms dwell.
        raw = self.card_model.received[0][2]
        self.assertEqual(raw[19:21], le16(0x0101))
        self.assertEqual(raw[23:30], bytes((0, 1, 1)) + le16(0) + le16(200))

    def test_a_scan_with_an_ssid_is_active(self):
        self.start()
        self.assertEqual(self.scan(b'bench'), OK)
        raw = self.card_model.received[0][2]
        self.assertEqual(raw[19:28], le16(0) + le16(5) + b'bench')

    def test_one_entry_per_bssid_the_strongest_report_kept(self):
        self.start()
        self.fw.scans = [[self.wpa_bss(rssi=70)], [self.wpa_bss(rssi=40)],
                         [self.wpa_bss(rssi=60)]]
        self.assertEqual(self.scan(), OK)
        self.assertEqual((self.sta.result_n, self.sta.result[0].rssi), (1, 40))
        self.fw.scans = [[]]
        self.assertEqual(self.scan(), OK)
        self.assertEqual(self.sta.result_n, 0)

    def test_scan_needs_the_idle_state(self):
        self.assertEqual(self.scan(), ESTATE)
        self.associated()
        self.assertEqual(self.scan(), ESTATE)
        self.assertEqual(self.written(), [])

    def test_join_with_an_rsn_element_follows_libertas_for_ccmp(self):
        self.start()
        self.fw.scans = [[self.wpa_bss()], [], []]
        self.assertEqual(self.scan(), OK)
        self.card_model.received.clear()
        self.assertEqual(self.join(), OK)
        self.assertEqual(self.written(), [SET_WEP, KEY_MATERIAL, KEY_MATERIAL, ENABLE_RSN,
                                          AUTHENTICATE, RADIO_CONTROL, ASSOCIATE])
        self.assertEqual((self.sta.state, self.sta.rsn, self.sta.connects), (ASSOCIATED, 1, 1))
        raws = [raw for _, _, raw in self.card_model.received]
        # ENABLE_RSN SET 1; the association ends with the element as a TLV.
        self.assertEqual(raws[3][12:16], le16(1) + le16(1))
        assoc = raws[6][:int.from_bytes(raws[6][:2], 'little')]     # blocks are padded
        self.assertTrue(assoc.endswith(le16(48) + le16(20) + RSN_CCMP[2:]))

    def test_join_without_an_element_joins_an_open_bss_with_rsn_off(self):
        self.start()
        self.fw.scans = [[self.open_bss()], [], []]
        self.assertEqual(self.scan(), OK)
        self.card_model.received.clear()
        self.assertEqual(self.join(b''), OK)
        self.assertEqual(self.card_model.received[3][2][12:16], le16(1) + le16(0))
        self.assertEqual(self.sta.rsn, 0)

    def test_join_refuses_a_bss_the_last_scan_did_not_report(self):
        self.start()
        self.fw.scans = [[self.wpa_bss()], [], []]
        self.assertEqual(self.scan(), OK)
        self.card_model.received.clear()
        self.assertEqual(self.join(bssid=bytes.fromhex('020000000009')), ENOTSCANNED)
        self.assertEqual(self.join(ssid=b'other'), ENOTSCANNED)
        self.assertEqual(self.written(), [])

    def test_join_refuses_a_bss_that_does_not_match_the_request(self):
        self.start()
        cases = ((self.open_bss(), RSN_CCMP), (self.wpa_bss(), b''),
                 (self.wpa_bss(capability=0x0401), RSN_CCMP),
                 (self.open_bss(ies=ie(0, b'bench') + ie(3, b'\x0c') + RSN_CCMP,
                                capability=0x0411), RSN_CCMP))
        for descriptor, element in cases:
            with self.subTest(element=element.hex()):
                self.fw.scans = [[descriptor], [], []]
                self.assertEqual(self.scan(), OK)
                self.card_model.received.clear()
                self.assertEqual(self.join(element), ENOTOPEN)
                self.assertEqual(self.written(), [])

    def test_join_refuses_a_malformed_element_before_any_command(self):
        self.start()
        self.fw.scans = [[self.wpa_bss()], [], []]
        self.assertEqual(self.scan(), OK)
        self.card_model.received.clear()
        self.assertEqual(self.join(RSN_CCMP[:-1]), EINVAL)
        self.assertEqual(self.written(), [])

    def test_a_refused_wpa_association_is_followed_by_a_deauthentication(self):
        self.start()
        self.fw.status = 1
        self.fw.scans = [[self.wpa_bss()], [], []]
        self.assertEqual(self.scan(), OK)
        self.assertEqual(self.join(), EASSOC)
        self.assertEqual(self.written()[-2:], [ASSOCIATE, DEAUTHENTICATE])
        self.assertEqual(self.sta.state, IDLE)

    def set_key(self, key_type=2, pairwise=1, key=bytes(range(16))):
        return self.lib.mv8688_sta_set_key(C.byref(self.sta), key_type, pairwise, key, len(key))

    def test_keys_are_installed_and_cleared_with_key_material(self):
        self.associated()
        self.assertEqual(self.set_key(), OK)
        self.assertEqual(self.set_key(pairwise=0, key=bytes(range(16, 32))), OK)
        self.assertEqual(self.set_key(key=b''), OK)
        self.assertEqual(self.written(), [KEY_MATERIAL] * 3)
        raws = [raw[12:12 + 44] for _, _, raw in self.card_model.received]
        self.assertEqual(raws[0][6:12], le16(2) + le16(6) + le16(16))
        self.assertEqual(raws[0][12:28], bytes(range(16)))
        self.assertEqual(raws[1][6:12], le16(2) + le16(5) + le16(16))
        self.assertEqual(raws[2][6:12], le16(0) + le16(2) + le16(0))
        self.assertEqual(self.sta.key_sets, 3)

    def test_a_bad_key_or_state_writes_nothing(self):
        self.assertEqual(self.set_key(), ESTATE)
        self.start()
        self.assertEqual(self.set_key(key=bytes(15)), EINVAL)
        self.assertEqual(self.set_key(key_type=0, key=bytes(13)), EINVAL)
        self.assertEqual(self.written(), [])
        self.assertEqual(self.set_key(), OK)

    def test_a_refused_key_is_reported(self):
        self.associated()
        self.fw.results[KEY_MATERIAL] = 1
        self.assertEqual(self.set_key(), ECMDRESULT)
        self.assertEqual((self.sta.key_sets, self.sta.state), (0, ASSOCIATED))


if __name__ == '__main__':
    unittest.main()
