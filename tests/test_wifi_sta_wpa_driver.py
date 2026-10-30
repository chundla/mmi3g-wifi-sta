"""The wpa_supplicant backend for the station driver, against a fake driver.

driver_mv8688.c is compiled on the host against the headers of the pinned
wpa_supplicant 2.11 release, with devctl, open, the event loop and the
supplicant's event entry replaced by a shim. The fake driver below answers
the control interface as mv8688sta-ctl.h describes it: one request at a
time, finished when the test says so. It shows the backend's requests,
events and their order, not that wpa_supplicant or the unit behave alike.

The release archive is private (work/build/downloads/);
without it these tests are skipped.
"""
import ctypes as C
import hashlib
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / 'work' / 'build' / 'downloads' / 'wpa_supplicant-2.11.tar.gz'
ARCHIVE_SHA256 = '912ea06f74e30a8e36fbb68064d6cdff218d8d591db0fc5d75dee6c81ac7fc0a'

STATUS, CONNECT, DISCONNECT, SCAN, SCAN_RESULT, JOIN, SET_KEY = range(0x1e0, 0x1e7)
DOWN, IDLE, CONNECTING, ASSOCIATED, FAILED = range(5)
EOK, ENOENT, EBUSY = 0, 2, 16
MAC = bytes.fromhex('001cd7000001')
BSSID = bytes.fromhex('020000aabbcc')
RSN = bytes.fromhex('3014 0100 000fac04 0100 000fac04 0100 000fac02 0000')
EXT_CAPAB = bytes.fromhex('7f080000000000000040')
IES = bytes.fromhex('0005') + b'bench' + bytes.fromhex('010482848b96 030101') + RSN

SHIM = r'''
#include "includes.h"
#include "common.h"
#include "eloop.h"
#include "driver.h"

extern const struct wpa_driver_ops wpa_driver_mv8688_ops;

int (*fake_devctl_cb)(int dcmd, void *data, size_t size);
int fake_devctl(int fd, int dcmd, void *data, size_t size, int *info)
{
    (void) fd; (void) info;
    return fake_devctl_cb(dcmd, data, size);
}
int fake_open(const char *path, int flags, ...) { (void) path; (void) flags; return 7; }
int fake_close(int fd) { (void) fd; return 0; }
int fake_usleep(unsigned us) { (void) us; return 0; }

static eloop_timeout_handler timer;
static void *timer_ctx;
int timers_armed;
int eloop_register_timeout(unsigned int secs, unsigned int usecs,
                           eloop_timeout_handler handler, void *eloop_data, void *user_data)
{
    (void) secs; (void) usecs; (void) user_data;
    timer = handler; timer_ctx = eloop_data; timers_armed++;
    return 0;
}
int eloop_cancel_timeout(eloop_timeout_handler handler, void *eloop_data, void *user_data)
{
    (void) handler; (void) eloop_data; (void) user_data;
    timer = NULL; return 1;
}
int fire_timer(void)
{
    eloop_timeout_handler h = timer;
    if (!h) return -1;
    timer = NULL;
    h(timer_ctx, NULL);
    return 0;
}

int ev_n, ev_type[64], ev_value[64];
u8 ev_ies[64][64];
int in_call;            /* set while a driver operation runs */
int ev_in_call;         /* events sent from inside an operation */
void wpa_supplicant_event(void *ctx, enum wpa_event_type event, union wpa_event_data *data)
{
    (void) ctx;
    if (in_call) ev_in_call++;
    if (ev_n >= 64) return;
    ev_type[ev_n] = event;
    ev_value[ev_n] = -1;
    if (data && event == EVENT_ASSOC) {
        ev_value[ev_n] = (int) data->assoc_info.req_ies_len;
        if (data->assoc_info.req_ies && data->assoc_info.req_ies_len <= 64)
            memcpy(ev_ies[ev_n], data->assoc_info.req_ies, data->assoc_info.req_ies_len);
    } else if (data && event == EVENT_ASSOC_REJECT) {
        ev_value[ev_n] = data->assoc_reject.status_code;
    } else if (data && event == EVENT_SCAN_RESULTS) {
        ev_value[ev_n] = data->scan_info.aborted;
    } else if (data && event == EVENT_MICHAEL_MIC_FAILURE) {
        ev_value[ev_n] = data->michael_mic_failure.unicast;
    }
    ev_n++;
}
void wpa_printf(int level, const char *fmt, ...) { (void) level; (void) fmt; }

void * os_zalloc(size_t size) { return calloc(1, size); }
size_t os_strlcpy(char *dest, const char *src, size_t siz)
{
    size_t n = strlen(src);
    if (siz) { size_t c = n < siz - 1 ? n : siz - 1; memcpy(dest, src, c); dest[c] = 0; }
    return n;
}
void wpa_scan_results_free(struct wpa_scan_results *res)
{
    size_t i;
    if (!res) return;
    for (i = 0; i < res->num; i++) free(res->res[i]);
    free(res->res);
    free(res);
}
const u8 * get_ie(const u8 *ies, size_t len, u8 eid)
{
    const u8 *end = ies + len;
    while (ies && end - ies >= 2 && ies[1] <= end - ies - 2) {
        if (ies[0] == eid) return ies;
        ies += 2 + ies[1];
    }
    return NULL;
}
const u8 * get_vendor_ie(const u8 *ies, size_t len, u32 vendor_type)
{
    const u8 *end = ies + len;
    while (ies && end - ies >= 2 && ies[1] <= end - ies - 2) {
        if (ies[0] == 221 && ies[1] >= 4 && WPA_GET_BE32(ies + 2) == vendor_type) return ies;
        ies += 2 + ies[1];
    }
    return NULL;
}

void * t_init(void)
{
    void *p; in_call = 1; p = wpa_driver_mv8688_ops.init(NULL, "en6"); in_call = 0; return p;
}
void t_deinit(void *priv) { in_call = 1; wpa_driver_mv8688_ops.deinit(priv); in_call = 0; }
int t_scan(void *priv, const u8 *ssid, size_t len)
{
    struct wpa_driver_scan_params p;
    int rc;
    memset(&p, 0, sizeof(p));
    p.num_ssids = 1;
    p.ssids[0].ssid = ssid;
    p.ssids[0].ssid_len = len;
    if (len) {      /* as the supplicant asks: the scan_ssid network, then the wildcard */
        p.num_ssids = 2;
    } else if (ssid && ssid[0]) {   /* "wildcard first" form, for the test */
        p.num_ssids = 2;
        p.ssids[1].ssid = ssid;
        p.ssids[1].ssid_len = strlen((const char *) ssid);
    }
    in_call = 1; rc = wpa_driver_mv8688_ops.scan2(priv, &p); in_call = 0;
    return rc;
}
struct t_res { u8 bssid[6]; int freq, level, caps, beacon_int; size_t ie_len; u8 ie[512]; };
int t_results(void *priv, struct t_res *out, int max)
{
    struct wpa_scan_results *r;
    int i;
    in_call = 1; r = wpa_driver_mv8688_ops.get_scan_results2(priv); in_call = 0;
    if (!r) return -1;
    for (i = 0; i < (int) r->num && i < max; i++) {
        memcpy(out[i].bssid, r->res[i]->bssid, 6);
        out[i].freq = r->res[i]->freq;
        out[i].level = r->res[i]->level;
        out[i].caps = r->res[i]->caps;
        out[i].beacon_int = r->res[i]->beacon_int;
        out[i].ie_len = r->res[i]->ie_len;
        memcpy(out[i].ie, r->res[i] + 1, r->res[i]->ie_len);
    }
    i = (int) r->num;
    wpa_scan_results_free(r);
    return i;
}
int t_associate(void *priv, const u8 *bssid, const u8 *ssid, size_t ssid_len,
                const u8 *ie, size_t ie_len, int key_mgmt, int pairwise, int group,
                int auth_alg, int mode)
{
    struct wpa_driver_associate_params p;
    int rc;
    memset(&p, 0, sizeof(p));
    p.bssid = bssid; p.ssid = ssid; p.ssid_len = ssid_len;
    p.wpa_ie = ie; p.wpa_ie_len = ie_len;
    p.key_mgmt_suite = key_mgmt; p.pairwise_suite = pairwise; p.group_suite = group;
    p.auth_alg = auth_alg; p.mode = mode; p.freq.freq = 2412;
    in_call = 1; rc = wpa_driver_mv8688_ops.associate(priv, &p); in_call = 0;
    return rc;
}
int t_set_key(void *priv, int alg, int idx, int key_flag, const u8 *key, size_t len)
{
    struct wpa_driver_set_key_params p;
    int rc;
    memset(&p, 0, sizeof(p));
    p.ifname = "en6"; p.alg = alg; p.key_idx = idx; p.key_flag = key_flag;
    p.key = key; p.key_len = len;
    in_call = 1; rc = wpa_driver_mv8688_ops.set_key(priv, &p); in_call = 0;
    return rc;
}
int t_deauth(void *priv)
{
    int rc;
    in_call = 1; rc = wpa_driver_mv8688_ops.deauthenticate(priv, NULL, 3); in_call = 0;
    return rc;
}
int t_bssid(void *priv, u8 *out) { return wpa_driver_mv8688_ops.get_bssid(priv, out); }
int t_ssid(void *priv, u8 *out) { return wpa_driver_mv8688_ops.get_ssid(priv, out); }
int t_capa(void *priv, unsigned *key_mgmt, unsigned *enc, unsigned *auth, int *ssids)
{
    struct wpa_driver_capa c;
    int rc = wpa_driver_mv8688_ops.get_capa(priv, &c);
    *key_mgmt = c.key_mgmt; *enc = c.enc; *auth = c.auth; *ssids = c.max_scan_ssids;
    return rc;
}
const u8 * t_mac(void *priv) { return wpa_driver_mv8688_ops.get_mac_addr(priv); }
int ev_code(const char *name)
{
    if (!strcmp(name, "assoc")) return EVENT_ASSOC;
    if (!strcmp(name, "disassoc")) return EVENT_DISASSOC;
    if (!strcmp(name, "scan")) return EVENT_SCAN_RESULTS;
    if (!strcmp(name, "reject")) return EVENT_ASSOC_REJECT;
    if (!strcmp(name, "mic")) return EVENT_MICHAEL_MIC_FAILURE;
    return -1;
}
int consts(const char *name)
{
    if (!strcmp(name, "PSK2")) return WPA_KEY_MGMT_PSK;
    if (!strcmp(name, "NONE")) return WPA_KEY_MGMT_NONE;
    if (!strcmp(name, "CCMP")) return WPA_CIPHER_CCMP;
    if (!strcmp(name, "CIPHER_NONE")) return WPA_CIPHER_NONE;
    if (!strcmp(name, "WEP40")) return WPA_CIPHER_WEP40;
    if (!strcmp(name, "OPEN")) return WPA_AUTH_ALG_OPEN;
    if (!strcmp(name, "SHARED")) return WPA_AUTH_ALG_SHARED;
    if (!strcmp(name, "INFRA")) return IEEE80211_MODE_INFRA;
    if (!strcmp(name, "IBSS")) return IEEE80211_MODE_IBSS;
    if (!strcmp(name, "ALG_NONE")) return WPA_ALG_NONE;
    if (!strcmp(name, "ALG_CCMP")) return WPA_ALG_CCMP;
    if (!strcmp(name, "ALG_TKIP")) return WPA_ALG_TKIP;
    if (!strcmp(name, "ALG_GCMP")) return WPA_ALG_GCMP;
    if (!strcmp(name, "PAIRWISE")) return KEY_FLAG_PAIRWISE_RX_TX;
    if (!strcmp(name, "GROUP")) return KEY_FLAG_GROUP_RX;
    return -1;
}
'''

DEVCTL_H = r'''
#ifndef FAKE_DEVCTL_H
#define FAKE_DEVCTL_H
#include <stddef.h>
#define EOK 0
#define _DCMD_NET 0
#define __DIOF(c, n, t) (0x100 + (n))
#define __DIOTF(c, n, t) (0x100 + (n))
int fake_devctl(int fd, int dcmd, void *data, size_t size, int *info);
#define devctl fake_devctl
#endif
'''


class Status(C.Structure):
    _fields_ = [('version', C.c_uint32), ('state', C.c_uint32), ('requested', C.c_uint32),
                ('done', C.c_uint32), ('last_rc', C.c_int32), ('last_step', C.c_char * 32),
                ('mac', C.c_uint8 * 6), ('bssid', C.c_uint8 * 6), ('channel', C.c_int32),
                ('rssi', C.c_uint32), ('ssid_len', C.c_uint32), ('ssid', C.c_uint8 * 32),
                ('assoc_status', C.c_uint16), ('aid', C.c_uint16)]
    _fields_ += [(name, C.c_uint32) for name in (
        'fw_release', 'connects', 'link_losses', 'link_cause', 'tx_frames', 'tx_bytes',
        'tx_errors', 'tx_drops', 'rx_frames', 'rx_bytes', 'rx_bad', 'rx_drops',
        'rx_up_pending', 'events', 'stray_responses', 'aborts', 'transport_errors', 'polls',
        'rsn', 'scans_done', 'results', 'key_sets', 'mic_ucast', 'mic_mcast')]


class ScanReq(C.Structure):
    _fields_ = [('ssid_len', C.c_uint32), ('ssid', C.c_uint8 * 32), ('request', C.c_uint32)]


class Bss(C.Structure):
    _fields_ = [('index', C.c_uint32), ('scan', C.c_uint32), ('count', C.c_uint32),
                ('bssid', C.c_uint8 * 6), ('capability', C.c_uint16), ('interval', C.c_uint16),
                ('rssi', C.c_uint8), ('ie_cut', C.c_uint8), ('channel', C.c_int32),
                ('ie_len', C.c_uint32), ('ie', C.c_uint8 * 512)]


class Join(C.Structure):
    _fields_ = [('bssid', C.c_uint8 * 6), ('pad', C.c_uint8 * 2), ('ssid_len', C.c_uint32),
                ('ssid', C.c_uint8 * 32), ('ie_len', C.c_uint32), ('ie', C.c_uint8 * 130),
                ('pad2', C.c_uint8 * 2), ('request', C.c_uint32)]


class Key(C.Structure):
    _fields_ = [('cipher', C.c_uint32), ('pairwise', C.c_uint32), ('key_len', C.c_uint32),
                ('key', C.c_uint8 * 32), ('request', C.c_uint32)]


class Request(C.Structure):
    _fields_ = [('request', C.c_uint32)]


class Res(C.Structure):
    _fields_ = [('bssid', C.c_uint8 * 6), ('freq', C.c_int), ('level', C.c_int),
                ('caps', C.c_int), ('beacon_int', C.c_int), ('ie_len', C.c_size_t),
                ('ie', C.c_uint8 * 512)]


DEVCTL_CB = C.CFUNCTYPE(C.c_int, C.c_int, C.c_void_p, C.c_size_t)
KINDS = {SCAN: ScanReq, JOIN: Join, SET_KEY: Key, DISCONNECT: Request}


class FakeDriver:
    """The control interface of devn-mv8688sta.so, one request at a time."""

    def __init__(self):
        self.status = Status(version=2, state=IDLE, fw_release=0x19260a26)
        self.status.mac[:] = list(MAC)
        self.requests = []          # (dcmd, copy of the request struct)
        self.pending = None
        self.auto = {DISCONNECT: 0, SET_KEY: 0}     # finish at once with this rc
        self.results = []           # Bss entries
        self.scan_number = 0
        self.replace_after = None   # replace the results after this many reads
        self.reads = 0

    def finish(self, rc=0, state=None, assoc_status=0):
        self.status.done = self.status.requested
        self.status.last_rc = rc
        self.status.last_step = b'' if not rc else b'step'
        if state is not None:
            self.status.state = state
        self.status.assoc_status = assoc_status
        dcmd, _ = self.pending
        self.pending = None
        if dcmd == SCAN:
            self.scan_number += 1
            self.status.scans_done = self.scan_number
        if dcmd == JOIN and state == ASSOCIATED:
            self.status.connects += 1

    def __call__(self, dcmd, data, size):
        if dcmd == STATUS:
            C.memmove(data, C.byref(self.status), C.sizeof(Status))
            return EOK
        if dcmd == SCAN_RESULT:
            bss = Bss.from_address(data)
            self.reads += 1
            if self.replace_after is not None and self.reads > self.replace_after:
                self.scan_number += 1
                self.replace_after = None
            index = bss.index
            C.memset(data, 0, C.sizeof(Bss))
            bss.index, bss.scan, bss.count = index, self.scan_number, len(self.results)
            if index >= len(self.results):
                return ENOENT
            src = self.results[index]
            C.memmove(data + 12, C.addressof(src) + 12, C.sizeof(Bss) - 12)
            return EOK
        if self.pending:
            return EBUSY
        kind = KINDS[dcmd]
        copy = kind.from_buffer_copy(C.string_at(data, C.sizeof(kind)))
        self.status.requested += 1
        kind.from_address(data).request = self.status.requested
        self.requests.append((dcmd, copy))
        self.pending = (dcmd, copy)
        if dcmd in self.auto:
            state = IDLE if dcmd == DISCONNECT else None
            self.finish(self.auto[dcmd], state=state)
        return EOK


def bss_entry(bssid=BSSID, channel=1, rssi=40, ies=IES, capability=0x0411):
    b = Bss()
    b.bssid[:] = list(bssid)
    b.channel, b.rssi, b.capability, b.interval = channel, rssi, capability, 100
    b.ie_len = len(ies)
    b.ie[:len(ies)] = list(ies)
    return b


@unittest.skipUnless(ARCHIVE.exists(), 'private wpa_supplicant 2.11 archive not present')
class WpaDriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('cc')
        if not compiler:
            raise RuntimeError('C compiler required for the backend tests')
        if hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() != ARCHIVE_SHA256:
            raise RuntimeError('wpa_supplicant archive checksum mismatch')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        tmp = Path(cls.tmp.name)
        with tarfile.open(ARCHIVE) as archive:
            members = [m for m in archive.getmembers()
                       if m.name.startswith('wpa_supplicant-2.11/src/')]
            archive.extractall(tmp, members=members, filter='data')
        src = tmp / 'wpa_supplicant-2.11' / 'src'
        (tmp / 'inc').mkdir()
        (tmp / 'inc' / 'devctl.h').write_text(DEVCTL_H)
        (tmp / 'shim.c').write_text(SHIM)
        so = tmp / 'drv.so'
        subprocess.run([compiler, '-std=gnu99', '-Wall', '-Wextra', '-Wno-unused-parameter',
                        '-Werror', '-shared', '-fPIC', '-I', str(tmp / 'inc'),
                        '-I', str(src), '-I', str(src / 'utils'), '-I', str(src / 'drivers'),
                        '-I', str(ROOT / 'wifi-sta'),
                        '-Dopen=fake_open', '-Dclose=fake_close', '-Dusleep=fake_usleep',
                        str(ROOT / 'wifi-sta' / 'wpa' / 'driver_mv8688.c'), str(tmp / 'shim.c'),
                        '-o', str(so)], check=True, capture_output=True)
        lib = cls.lib = C.CDLL(str(so))
        lib.t_init.restype = C.c_void_p
        for name in ('t_deinit', 't_deauth'):
            getattr(lib, name).argtypes = [C.c_void_p]
        lib.t_deinit.restype = None
        lib.t_scan.argtypes = [C.c_void_p, C.c_char_p, C.c_size_t]
        lib.t_results.argtypes = [C.c_void_p, C.POINTER(Res), C.c_int]
        lib.t_associate.argtypes = [C.c_void_p, C.c_char_p, C.c_char_p, C.c_size_t, C.c_char_p,
                                    C.c_size_t] + [C.c_int] * 5
        lib.t_set_key.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_char_p, C.c_size_t]
        lib.t_bssid.argtypes = lib.t_ssid.argtypes = [C.c_void_p, C.c_char_p]
        lib.t_capa.argtypes = [C.c_void_p] + [C.POINTER(C.c_uint)] * 3 + [C.POINTER(C.c_int)]
        lib.t_mac.argtypes = [C.c_void_p]
        lib.t_mac.restype = C.POINTER(C.c_uint8 * 6)
        lib.ev_code.argtypes = lib.consts.argtypes = [C.c_char_p]
        cls.k = {name: lib.consts(name.encode()) for name in (
            'PSK2', 'NONE', 'CCMP', 'CIPHER_NONE', 'WEP40', 'OPEN', 'SHARED', 'INFRA', 'IBSS',
            'ALG_NONE', 'ALG_CCMP', 'ALG_TKIP', 'ALG_GCMP', 'PAIRWISE', 'GROUP')}
        cls.ev = {name: lib.ev_code(name.encode())
                  for name in ('assoc', 'disassoc', 'scan', 'reject', 'mic')}

    def setUp(self):
        for name in ('ev_n', 'ev_in_call', 'in_call'):
            C.c_int.in_dll(self.lib, name).value = 0
        self.fake = FakeDriver()
        self.cb = DEVCTL_CB(self.fake)
        C.c_void_p.in_dll(self.lib, 'fake_devctl_cb').value = C.cast(self.cb, C.c_void_p).value
        self.drv = self.lib.t_init()
        self.assertTrue(self.drv)
        self.addCleanup(self.lib.t_deinit, self.drv)

    def events(self):
        n = C.c_int.in_dll(self.lib, 'ev_n').value
        types = (C.c_int * 64).in_dll(self.lib, 'ev_type')
        values = (C.c_int * 64).in_dll(self.lib, 'ev_value')
        names = {v: k for k, v in self.ev.items()}
        return [(names.get(types[i], types[i]), values[i]) for i in range(n)]

    def tick(self):
        self.assertEqual(self.lib.fire_timer(), 0)

    def associate(self, ie=EXT_CAPAB + RSN, key_mgmt='PSK2', pairwise='CCMP', group='CCMP',
                  auth='OPEN', mode='INFRA', bssid=BSSID, ssid=b'bench'):
        return self.lib.t_associate(self.drv, bssid, ssid, len(ssid), ie, len(ie),
                                    self.k[key_mgmt], self.k[pairwise], self.k[group],
                                    self.k[auth], self.k[mode])

    def associated(self):
        self.assertEqual(self.associate(), 0)
        self.fake.finish(state=ASSOCIATED)
        self.tick()
        self.assertEqual(self.events()[-1][0], 'assoc')

    def test_init_reads_the_mac_and_capabilities(self):
        self.assertEqual(bytes(self.lib.t_mac(self.drv).contents), MAC)
        key_mgmt, enc, auth, ssids = C.c_uint(), C.c_uint(), C.c_uint(), C.c_int()
        self.lib.t_capa(self.drv, key_mgmt, enc, auth, ssids)
        self.assertEqual((key_mgmt.value, enc.value, auth.value, ssids.value),
                         (0x1 | 0x2 | 0x4 | 0x8, 0x4 | 0x8, 0x1, 2))

    def test_init_refuses_another_version_or_a_failed_driver(self):
        for version, state in ((1, IDLE), (2, FAILED), (2, DOWN)):
            self.fake.status.version, self.fake.status.state = version, state
            self.assertFalse(self.lib.t_init(), (version, state))

    def test_a_scan_is_queued_and_reported_by_the_poll(self):
        self.assertEqual(self.lib.t_scan(self.drv, b'bench', 5), 0)
        dcmd, req = self.fake.requests[-1]
        self.assertEqual((dcmd, req.ssid_len, bytes(req.ssid)[:5]), (SCAN, 5, b'bench'))
        self.tick()
        self.assertEqual(self.events(), [])
        self.fake.results = [bss_entry(), bss_entry(bytes.fromhex('020000000002'), 11, 70)]
        self.fake.finish()
        self.tick()
        self.assertEqual(self.events(), [('scan', 0)])
        out = (Res * 4)()
        self.assertEqual(self.lib.t_results(self.drv, out, 4), 2)
        self.assertEqual((bytes(out[0].bssid), out[0].freq, out[0].level, out[0].caps,
                          out[0].beacon_int), (BSSID, 2412, -37, 0x0411, 100))
        self.assertEqual(bytes(out[0].ie)[:out[0].ie_len], IES)
        self.assertEqual((out[1].freq, out[1].level), (2462, -67))

    def test_the_named_ssid_is_scanned_for_wherever_it_is_in_the_request(self):
        self.assertEqual(self.lib.t_scan(self.drv, b'bench', 0), 0)
        _, req = self.fake.requests[-1]
        self.assertEqual((req.ssid_len, bytes(req.ssid)[:5]), (5, b'bench'))

    def test_a_wildcard_scan_is_passive_and_a_failed_one_aborted(self):
        self.assertEqual(self.lib.t_scan(self.drv, None, 0), 0)
        self.assertEqual(self.fake.requests[-1][1].ssid_len, 0)
        self.fake.finish(rc=-35)
        self.tick()
        self.assertEqual(self.events(), [('scan', 1)])

    def test_results_replaced_while_read_are_read_again(self):
        self.fake.results = [bss_entry(), bss_entry(bytes.fromhex('020000000002'))]
        self.fake.replace_after = 1
        out = (Res * 4)()
        self.assertEqual(self.lib.t_results(self.drv, out, 4), 2)
        self.assertEqual(self.fake.reads, 2 + 3)

    def test_associate_sends_only_the_rsn_element_and_reports_it(self):
        self.assertEqual(self.associate(), 0)
        dcmd, join = self.fake.requests[-1]
        self.assertEqual((dcmd, bytes(join.bssid), join.ssid_len), (JOIN, BSSID, 5))
        self.assertEqual(bytes(join.ie)[:join.ie_len], RSN)
        self.fake.finish(state=ASSOCIATED)
        self.tick()
        self.assertEqual(self.events(), [('assoc', len(RSN))])
        ies = (C.c_uint8 * 64 * 64).in_dll(self.lib, 'ev_ies')
        self.assertEqual(bytes(ies[0])[:len(RSN)], RSN)
        bssid, ssid = C.create_string_buffer(6), C.create_string_buffer(32)
        self.lib.t_bssid(self.drv, bssid)
        self.assertEqual((bssid.raw, self.lib.t_ssid(self.drv, ssid)), (BSSID, 5))

    def test_an_open_association_carries_no_element(self):
        self.assertEqual(self.associate(ie=EXT_CAPAB, key_mgmt='NONE', pairwise='CIPHER_NONE',
                                        group='CIPHER_NONE'), 0)
        self.assertEqual(self.fake.requests[-1][1].ie_len, 0)

    def test_requests_the_driver_cannot_serve_are_refused_unsent(self):
        cases = [dict(auth='SHARED'), dict(mode='IBSS'), dict(ie=EXT_CAPAB),
                 dict(ie=b'', key_mgmt='NONE', pairwise='WEP40', group='WEP40'),
                 dict(ssid=b'')]
        for case in cases:
            self.assertEqual(self.associate(**case), -1, case)
        self.assertEqual(self.fake.requests, [])

    def test_a_refused_association_is_reported_with_its_status(self):
        self.assertEqual(self.associate(), 0)
        self.fake.finish(rc=-42, assoc_status=17)
        self.tick()
        self.assertEqual(self.events(), [('reject', 17)])
        self.assertEqual(self.associate(), 0)
        self.fake.finish(rc=-46, assoc_status=0xffff)
        self.tick()
        self.assertEqual(self.events()[-1], ('reject', 1))

    def set_key(self, alg='ALG_CCMP', idx=0, flag='PAIRWISE', key=bytes(range(16))):
        return self.lib.t_set_key(self.drv, self.k[alg], idx, self.k[flag], key, len(key))

    def test_keys_wait_for_their_result(self):
        self.associated()
        self.assertEqual(self.set_key(), 0)
        dcmd, key = self.fake.requests[-1]
        self.assertEqual((dcmd, key.cipher, key.pairwise, key.key_len), (SET_KEY, 2, 1, 16))
        self.assertEqual(bytes(key.key)[:16], bytes(range(16)))
        self.assertEqual(self.set_key(alg='ALG_TKIP', idx=1, flag='GROUP', key=bytes(32)), 0)
        self.assertEqual(self.fake.requests[-1][1].cipher, 1)
        self.assertEqual(self.fake.requests[-1][1].pairwise, 0)
        self.fake.auto[SET_KEY] = -35
        self.assertEqual(self.set_key(), -1)

    def test_clears_while_idle_and_unknown_ciphers_send_nothing(self):
        self.assertEqual(self.set_key(alg='ALG_NONE', key=b''), 0)
        self.assertEqual(self.set_key(alg='ALG_GCMP'), -1)
        self.assertEqual(self.fake.requests, [])
        self.associated()
        self.assertEqual(self.set_key(alg='ALG_NONE', idx=4, flag='GROUP', key=b''), 0)
        self.assertEqual(self.set_key(alg='ALG_NONE', idx=1, flag='GROUP', key=b''), 0)
        self.assertEqual([(d, r.cipher) for d, r in self.fake.requests[1:]], [(SET_KEY, 0)])

    def test_the_end_of_the_link_is_reported_once(self):
        self.associated()
        self.fake.status.link_losses += 1
        self.fake.status.state = IDLE
        self.tick()
        self.tick()
        self.assertEqual([e for e, _ in self.events()], ['assoc', 'disassoc'])

    def test_a_new_association_under_the_same_state_is_also_an_end(self):
        self.associated()
        self.fake.status.connects += 1
        self.tick()
        self.assertEqual(self.events()[-1][0], 'disassoc')

    def test_mic_failures_are_reported_one_by_one(self):
        self.associated()
        self.fake.status.mic_ucast += 1
        self.fake.status.mic_mcast += 2
        self.tick()
        self.assertEqual(self.events()[1:], [('mic', 1), ('mic', 0), ('mic', 0)])

    def test_a_request_behind_a_scan_never_sends_events_from_inside_the_call(self):
        self.assertEqual(self.lib.t_scan(self.drv, b'bench', 5), 0)
        self.fake.finish()          # done, but not yet polled
        self.assertEqual(self.lib.t_deauth(self.drv), 0)
        self.assertEqual(C.c_int.in_dll(self.lib, 'ev_in_call').value, 0)
        self.assertEqual(self.events(), [])
        self.tick()
        self.assertEqual(self.events(), [('scan', 0)])

    def test_a_join_finished_behind_a_deauthentication_is_not_reported(self):
        self.assertEqual(self.associate(), 0)
        self.fake.finish(state=ASSOCIATED)
        self.assertEqual(self.lib.t_deauth(self.drv), 0)
        self.tick()
        self.assertEqual(self.events(), [])
        bssid = C.create_string_buffer(6)
        self.lib.t_bssid(self.drv, bssid)
        self.assertEqual(bssid.raw, bytes(6))

    def test_a_new_association_supersedes_an_unreported_one(self):
        other = bytes.fromhex('020000000002')
        self.assertEqual(self.associate(), 0)
        self.fake.finish(state=ASSOCIATED)          # done, not yet polled
        self.assertEqual(self.associate(bssid=other), 0)
        self.assertEqual(C.c_int.in_dll(self.lib, 'ev_in_call').value, 0)
        self.assertEqual(bytes(self.fake.requests[-1][1].bssid), other)
        self.tick()
        self.assertEqual(self.events(), [])         # neither A nor, yet, B
        self.fake.finish(state=ASSOCIATED)
        self.tick()
        self.assertEqual([e for e, _ in self.events()], ['assoc'])
        bssid = C.create_string_buffer(6)
        self.lib.t_bssid(self.drv, bssid)
        self.assertEqual(bssid.raw, other)

    def test_a_deauthentication_that_cannot_wait_still_cancels_the_join(self):
        self.assertEqual(self.associate(), 0)
        # The join never finishes while the deauthentication waits for it.
        self.assertEqual(self.lib.t_deauth(self.drv), -1)
        self.assertEqual([d for d, _ in self.fake.requests], [JOIN])
        self.fake.finish(state=ASSOCIATED)
        self.tick()
        self.assertEqual(self.events(), [])
        # It associated anyway, so the poll leaves.
        self.assertEqual([d for d, _ in self.fake.requests], [JOIN, DISCONNECT])
        bssid = C.create_string_buffer(6)
        self.lib.t_bssid(self.drv, bssid)
        self.assertEqual(bssid.raw, bytes(6))

    def test_a_cancelled_join_that_failed_needs_no_leave(self):
        self.assertEqual(self.associate(), 0)
        self.assertEqual(self.lib.t_deauth(self.drv), -1)
        self.fake.finish(rc=-42, assoc_status=17)
        self.tick()
        self.tick()
        self.assertEqual(self.events(), [])
        self.assertEqual([d for d, _ in self.fake.requests], [JOIN])

    def test_no_scan_while_associated(self):
        self.associated()
        self.assertEqual(self.lib.t_scan(self.drv, b'bench', 5), -1)
        self.assertEqual(len(self.fake.requests), 1)



@unittest.skipUnless(ARCHIVE.exists(), 'private wpa_supplicant 2.11 archive not present')
class WpaPatchTests(unittest.TestCase):
    """qnx632.patch applies cleanly and keeps secrets out of a -d log."""

    @classmethod
    def setUpClass(cls):
        if hashlib.sha256(ARCHIVE.read_bytes()).hexdigest() != ARCHIVE_SHA256:
            raise RuntimeError('wpa_supplicant archive checksum mismatch')
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        with tarfile.open(ARCHIVE) as archive:
            archive.extractall(cls.tmp.name, filter='data')
        cls.tree = Path(cls.tmp.name) / 'wpa_supplicant-2.11'
        patch = (ROOT / 'wifi-sta' / 'wpa' / 'qnx632.patch').read_bytes()
        cls.result = subprocess.run(['patch', '-p1', '--forward'], input=patch,
                                    cwd=cls.tree, capture_output=True)

    def test_the_patch_applies_cleanly(self):
        self.assertEqual(self.result.returncode, 0, self.result.stdout)
        self.assertNotIn(b'offset', self.result.stdout)
        self.assertNotIn(b'fuzz', self.result.stdout)

    def test_decrypted_key_data_is_dumped_only_as_key_material(self):
        text = (self.tree / 'src' / 'rsn_supp' / 'wpa.c').read_text()
        self.assertNotIn('wpa_hexdump(MSG_DEBUG, "WPA: IE KeyData"', text)
        self.assertEqual(text.count('wpa_hexdump_key(MSG_DEBUG, "WPA: IE KeyData"'), 2)

    def test_configuration_errors_do_not_echo_keys(self):
        text = (self.tree / 'wpa_supplicant' / 'config.c').read_text()
        lines = text.splitlines()
        for message in ('Invalid PSK', 'Invalid passphrase "', 'Invalid WEP key',
                        'Too long WEP key', 'Invalid psk_list"', 'Invalid psk_list PSK'):
            found = [i for i, line in enumerate(lines) if message in line]
            self.assertTrue(found, message)
            for i in found:     # the message with its continuation lines
                statement = ' '.join(lines[i:i + 3]).split(');')[0]
                self.assertNotIn("'%s'", statement, message)


if __name__ == '__main__':
    unittest.main()
