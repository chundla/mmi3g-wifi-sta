/*
 * wpa_supplicant driver interface for the 88W8688 station driver
 * (devn-mv8688sta.so, phase 6), over its devctl control interface
 * (wifi-sta/mv8688sta-ctl.h, version 2) on /dev/io-net/<ifname>.
 *
 * The firmware is FullMAC: it scans, authenticates and associates, and
 * encrypts once keys are installed (libertas' model, which cfg80211 drives
 * on Linux). This backend therefore offers what the libertas cfg80211
 * operations offer: a scan whose results carry the elements, an
 * association with the supplicant's RSN or WPA element, and key
 * installation. The 4-way and group handshakes run here, over the
 * l2_packet path (l2_packet_qnx.c); EAPOL frames are ordinary data
 * frames to the driver.
 *
 * The driver takes one request at a time and reports completions only
 * through its status. Scans and associations are therefore queued and
 * completed by a 100 ms status poll, which reports them as events, as a
 * Linux driver would; key and disconnect requests wait for their result.
 * When a request has to wait for a scan or join to finish, that result is
 * kept and its event still goes out from the poll, never from inside the
 * supplicant's call.
 * The same poll notices a link that ended (deauthentication,
 * disassociation or link loss reported by the firmware) and TKIP MIC
 * failures.
 *
 * Only open-system authentication and WPA/WPA2 with TKIP or CCMP are
 * offered. Each request is refused before it reaches the driver when it
 * asks for more.
 *
 * This file is distributed under the terms of the BSD license, as
 * wpa_supplicant is; see wpa_supplicant's README.
 */
#include "includes.h"

#include <devctl.h>
#include <fcntl.h>
#include <unistd.h>

#include "common.h"
#include "eloop.h"
#include "driver.h"
#include "common/ieee802_11_defs.h"
#include "common/ieee802_11_common.h"
#include "mv8688sta-ctl.h"

#define POLL_US         100000  /* status poll while running */
#define WAIT_STEP_US    20000   /* waiting for a request to finish */
#define SCAN_WAIT_MS    15000   /* passive: 11 channels x 200 ms, with margin */
#define JOIN_WAIT_MS    40000   /* six commands of up to 5 s each */
#define SHORT_WAIT_MS   6000    /* one command of up to 5 s */

enum pending { PEND_NONE, PEND_SCAN, PEND_JOIN, PEND_OTHER };

struct mv8688_drv {
    void *ctx;
    char ifname[IFNAMSIZ];
    int fd;
    u8 own_addr[ETH_ALEN];
    struct mv8688sta_ctl_status st;
    /* The one request the driver may hold. */
    u32 outstanding;            /* its number, 0 when none */
    enum pending kind;
    /* The outstanding join was cancelled by a deauthentication that could
     * not wait for it: it is not reported, and is left once it finishes. */
    int join_cancelled;
    int leave_after_cancel;     /* it associated anyway: disconnect from the poll */
    /* A finished scan or join whose event the poll has still to send. */
    enum pending done_kind;
    s32 done_rc;
    u16 done_assoc_status;
    char done_step[32];
    /* An association, as the supplicant last asked for and was told. */
    int associated;
    u8 bssid[ETH_ALEN];
    u8 ssid[SSID_MAX_LEN];
    size_t ssid_len;
    u8 wpa_ie[MV8688STA_CTL_WPA_IE_MAX];
    size_t wpa_ie_len;
    unsigned freq;
    u32 link_losses, connects;  /* the driver's counters when it came up */
    u32 mic_ucast, mic_mcast;
};

static int get_status(struct mv8688_drv *drv)
{
    int rc = devctl(drv->fd, DCMD_MV8688STA_STATUS, &drv->st, sizeof(drv->st), NULL);
    if (rc != EOK) {
        wpa_printf(MSG_ERROR, "mv8688: status: %s", strerror(rc));
        return -1;
    }
    if (drv->st.version != MV8688STA_CTL_VERSION) {
        wpa_printf(MSG_ERROR, "mv8688: driver control version %u, expected %u",
                   (unsigned) drv->st.version, MV8688STA_CTL_VERSION);
        return -1;
    }
    return 0;
}

static int finished(const struct mv8688_drv *drv)
{
    return (s32) (drv->st.done - drv->outstanding) >= 0;
}

/* Polls until the outstanding request has finished, at most wait_ms.
 * Returns 1 when it has (drv->st then holds its result), 0 on timeout and
 * -1 when the status cannot be read. */
static int wait_done(struct mv8688_drv *drv, unsigned wait_ms)
{
    unsigned waited = 0;
    for (;;) {
        if (get_status(drv))
            return -1;
        if (!drv->outstanding || finished(drv))
            return 1;
        if (waited >= wait_ms)
            return 0;
        usleep(WAIT_STEP_US);
        waited += WAIT_STEP_US / 1000;
    }
}

/* Takes the result of the scan or join that just finished from drv->st,
 * for the poll to report. A join that succeeded counts as associated from
 * here on. */
static void take_result(struct mv8688_drv *drv)
{
    enum pending kind = drv->kind;
    drv->outstanding = 0;
    drv->kind = PEND_NONE;
    if (kind != PEND_SCAN && kind != PEND_JOIN)
        return;
    if (kind == PEND_JOIN && drv->join_cancelled) {
        drv->join_cancelled = 0;
        if (drv->st.state == MV8688STA_CTL_ASSOCIATED)
            drv->leave_after_cancel = 1;
        return;
    }
    drv->done_kind = kind;
    drv->done_rc = drv->st.last_rc;
    drv->done_assoc_status = drv->st.assoc_status;
    os_memcpy(drv->done_step, drv->st.last_step, sizeof(drv->done_step));
    drv->done_step[sizeof(drv->done_step) - 1] = 0;
    if (kind == PEND_JOIN && !drv->done_rc && drv->st.state == MV8688STA_CTL_ASSOCIATED) {
        drv->associated = 1;
        drv->link_losses = drv->st.link_losses;
        drv->connects = drv->st.connects;
    }
}

/* Queues one request; any request still outstanding is waited for first
 * and, if it was a scan or a join, completed (its event goes out now). */
static int queue(struct mv8688_drv *drv, int dcmd, void *data, size_t size, u32 *number,
                 enum pending kind)
{
    int rc;
    if (drv->outstanding) {
        if (wait_done(drv, drv->kind == PEND_JOIN ? JOIN_WAIT_MS : SCAN_WAIT_MS) != 1) {
            wpa_printf(MSG_ERROR, "mv8688: request %u did not finish",
                       (unsigned) drv->outstanding);
            return -1;
        }
        take_result(drv);
    }
    rc = devctl(drv->fd, dcmd, data, size, NULL);
    if (rc != EOK) {
        wpa_printf(MSG_ERROR, "mv8688: request refused: %s", strerror(rc));
        return -1;
    }
    drv->outstanding = *number;
    drv->kind = kind;
    return 0;
}

/* Queues a request and waits for its result: 0 when it succeeded. */
static int run(struct mv8688_drv *drv, int dcmd, void *data, size_t size, u32 *number,
               const char *what)
{
    int rc;
    if (queue(drv, dcmd, data, size, number, PEND_OTHER))
        return -1;
    rc = wait_done(drv, SHORT_WAIT_MS);
    if (rc != 1) {
        wpa_printf(MSG_ERROR, "mv8688: %s did not finish", what);
        return -1;
    }
    drv->outstanding = 0;
    drv->kind = PEND_NONE;
    if (drv->st.last_rc) {
        wpa_printf(MSG_ERROR, "mv8688: %s failed rc=%d at %s", what, (int) drv->st.last_rc,
                   drv->st.last_step);
        return -1;
    }
    return 0;
}

static unsigned channel_freq(int channel)
{
    if (channel == 14)
        return 2484;
    return channel >= 1 && channel <= 13 ? 2407 + 5 * channel : 0;
}

/* Sends the event for a finished scan or join. */
static void report_result(struct mv8688_drv *drv)
{
    union wpa_event_data data;
    enum pending kind = drv->done_kind;

    drv->done_kind = PEND_NONE;
    os_memset(&data, 0, sizeof(data));
    if (kind == PEND_SCAN) {
        wpa_printf(MSG_DEBUG, "mv8688: scan done rc=%d", (int) drv->done_rc);
        if (drv->done_rc)
            data.scan_info.aborted = 1;
        wpa_supplicant_event(drv->ctx, EVENT_SCAN_RESULTS, &data);
        return;
    }
    if (drv->associated) {
        wpa_printf(MSG_DEBUG, "mv8688: associated with " MACSTR, MAC2STR(drv->bssid));
        data.assoc_info.req_ies = drv->wpa_ie_len ? drv->wpa_ie : NULL;
        data.assoc_info.req_ies_len = drv->wpa_ie_len;
        data.assoc_info.freq = drv->freq;
        wpa_supplicant_event(drv->ctx, EVENT_ASSOC, &data);
        return;
    }
    wpa_printf(MSG_INFO, "mv8688: association with " MACSTR " failed rc=%d at %s "
               "status=0x%04x", MAC2STR(drv->bssid), (int) drv->done_rc, drv->done_step,
               drv->done_assoc_status);
    data.assoc_reject.bssid = drv->bssid;
    data.assoc_reject.status_code =
        drv->done_assoc_status && drv->done_assoc_status != 0xffff ?
        drv->done_assoc_status : WLAN_STATUS_UNSPECIFIED_FAILURE;
    wpa_supplicant_event(drv->ctx, EVENT_ASSOC_REJECT, &data);
}

static void poll_status(void *eloop_ctx, void *timeout_ctx)
{
    struct mv8688_drv *drv = eloop_ctx;
    union wpa_event_data data;
    (void) timeout_ctx;

    if (get_status(drv) == 0) {
        if (drv->outstanding && finished(drv))
            take_result(drv);
        if (drv->done_kind != PEND_NONE)
            report_result(drv);
        if (drv->leave_after_cancel && !drv->outstanding) {
            struct mv8688sta_ctl_request req;
            os_memset(&req, 0, sizeof(req));
            wpa_printf(MSG_INFO, "mv8688: a cancelled join associated; leaving");
            if (queue(drv, DCMD_MV8688STA_DISCONNECT, &req, sizeof(req), &req.request,
                      PEND_OTHER) == 0)
                drv->leave_after_cancel = 0;
        }
        if (drv->associated && (drv->st.state != MV8688STA_CTL_ASSOCIATED ||
                                drv->st.link_losses != drv->link_losses ||
                                drv->st.connects != drv->connects)) {
            drv->associated = 0;
            wpa_printf(MSG_INFO, "mv8688: link ended: state=%u cause=0x%08x",
                       (unsigned) drv->st.state, (unsigned) drv->st.link_cause);
            os_memset(&data, 0, sizeof(data));
            data.disassoc_info.addr = drv->bssid;
            wpa_supplicant_event(drv->ctx, EVENT_DISASSOC, &data);
        }
        while (drv->st.mic_ucast != drv->mic_ucast || drv->st.mic_mcast != drv->mic_mcast) {
            os_memset(&data, 0, sizeof(data));
            data.michael_mic_failure.unicast = drv->st.mic_ucast != drv->mic_ucast;
            data.michael_mic_failure.src = drv->bssid;
            if (data.michael_mic_failure.unicast)
                drv->mic_ucast++;
            else
                drv->mic_mcast++;
            wpa_supplicant_event(drv->ctx, EVENT_MICHAEL_MIC_FAILURE, &data);
        }
    }
    eloop_register_timeout(0, POLL_US, poll_status, drv, NULL);
}

static int mv8688_get_bssid(void *priv, u8 *bssid)
{
    struct mv8688_drv *drv = priv;
    if (drv->associated)
        os_memcpy(bssid, drv->bssid, ETH_ALEN);
    else
        os_memset(bssid, 0, ETH_ALEN);
    return 0;
}

static int mv8688_get_ssid(void *priv, u8 *ssid)
{
    struct mv8688_drv *drv = priv;
    if (!drv->associated)
        return 0;
    os_memcpy(ssid, drv->ssid, drv->ssid_len);
    return drv->ssid_len;
}

static int mv8688_scan2(void *priv, struct wpa_driver_scan_params *params)
{
    struct mv8688_drv *drv = priv;
    struct mv8688sta_ctl_scan scan;

    size_t i;

    os_memset(&scan, 0, sizeof(scan));
    /* The driver scans for one SSID, actively, or passively for all. Two
     * are offered (max_scan_ssids) so that the supplicant puts a scan_ssid
     * network and the wildcard into one request rather than alternating
     * them; the named SSID is scanned for, as the 5.0-5.3 joins were. */
    for (i = 0; i < params->num_ssids && !scan.ssid_len; i++) {
        if (params->ssids[i].ssid_len > sizeof(scan.ssid))
            return -1;
        scan.ssid_len = params->ssids[i].ssid_len;
        if (scan.ssid_len)
            os_memcpy(scan.ssid, params->ssids[i].ssid, scan.ssid_len);
    }
    if (drv->associated) {
        /* The driver scans only while idle. */
        wpa_printf(MSG_DEBUG, "mv8688: no scan while associated");
        return -1;
    }
    wpa_printf(MSG_DEBUG, "mv8688: %s scan", scan.ssid_len ? "active" : "passive");
    return queue(drv, DCMD_MV8688STA_SCAN, &scan, sizeof(scan), &scan.request, PEND_SCAN);
}

static int add_result(struct wpa_scan_results *res, struct wpa_scan_res *r)
{
    struct wpa_scan_res **tmp = os_realloc_array(res->res, res->num + 1, sizeof(*tmp));
    if (!tmp)
        return -1;
    tmp[res->num++] = r;
    res->res = tmp;
    return 0;
}

static struct wpa_scan_results * mv8688_get_scan_results2(void *priv)
{
    struct mv8688_drv *drv = priv;
    struct mv8688sta_ctl_bss bss;
    struct wpa_scan_results *res;
    struct wpa_scan_res *r;
    u32 i, scan = 0, count = 0;
    int rc, tries;

    res = os_zalloc(sizeof(*res));
    if (!res)
        return NULL;
    /* Read again if another scan replaced the list while it was read. */
    for (tries = 0; tries < 2; tries++) {
        for (i = 0; ; i++) {
            os_memset(&bss, 0, sizeof(bss));
            bss.index = i;
            rc = devctl(drv->fd, DCMD_MV8688STA_SCAN_RESULT, &bss, sizeof(bss), NULL);
            if (rc == ENOENT && i == count)
                return res;
            if (rc != EOK || (i && bss.scan != scan))
                break;
            scan = bss.scan;
            count = bss.count;
            if (bss.ie_len > sizeof(bss.ie) || bss.ie_len == 0)
                continue;
            r = os_zalloc(sizeof(*r) + bss.ie_len);
            if (!r)
                break;
            os_memcpy(r->bssid, bss.bssid, ETH_ALEN);
            r->flags = WPA_SCAN_QUAL_INVALID | WPA_SCAN_NOISE_INVALID | WPA_SCAN_LEVEL_DBM;
            r->freq = channel_freq(bss.channel);
            r->beacon_int = bss.interval;
            r->caps = bss.capability;
            r->level = 3 - (int) bss.rssi;      /* libertas LBS_SCAN_RSSI_TO_MBM */
            r->ie_len = bss.ie_len;
            os_memcpy(r + 1, bss.ie, bss.ie_len);
            if (add_result(res, r)) {
                os_free(r);
                break;
            }
        }
        wpa_printf(MSG_DEBUG, "mv8688: scan results changed or failed while read (rc=%d)",
                   rc);
        wpa_scan_results_free(res);
        res = os_zalloc(sizeof(*res));
        if (!res)
            return NULL;
        count = 0;
    }
    return res;
}

static int mv8688_associate(void *priv, struct wpa_driver_associate_params *params)
{
    struct mv8688_drv *drv = priv;
    struct mv8688sta_ctl_join join;
    const u8 *ie = NULL;

    if (params->mode != IEEE80211_MODE_INFRA || !params->bssid || !params->ssid ||
        !params->ssid_len || params->ssid_len > sizeof(join.ssid) ||
        !(params->auth_alg & WPA_AUTH_ALG_OPEN)) {
        wpa_printf(MSG_ERROR, "mv8688: only an infrastructure BSS with a BSSID, an SSID "
                   "and open-system authentication");
        return -1;
    }
    if (params->key_mgmt_suite != WPA_KEY_MGMT_NONE) {
        /* As lbs_add_wpa_tlv: the first RSN element, else the WPA one,
         * of what the supplicant built for the request. */
        ie = get_ie(params->wpa_ie, params->wpa_ie_len, WLAN_EID_RSN);
        if (!ie)
            ie = get_vendor_ie(params->wpa_ie, params->wpa_ie_len, WPA_IE_VENDOR_TYPE);
        if (!ie || 2 + ie[1] > MV8688STA_CTL_WPA_IE_MAX) {
            wpa_printf(MSG_ERROR, "mv8688: no usable RSN or WPA element for key management "
                       "0x%x", params->key_mgmt_suite);
            return -1;
        }
    } else if (params->pairwise_suite != WPA_CIPHER_NONE ||
               params->group_suite != WPA_CIPHER_NONE) {
        wpa_printf(MSG_ERROR, "mv8688: WEP is not offered");
        return -1;
    }
    /* Settle an older request first, so its result cannot be read as this
     * one's; an older join not yet reported is superseded. */
    if (drv->outstanding) {
        if (wait_done(drv, drv->kind == PEND_JOIN ? JOIN_WAIT_MS : SCAN_WAIT_MS) != 1) {
            wpa_printf(MSG_ERROR, "mv8688: request %u did not finish",
                       (unsigned) drv->outstanding);
            return -1;
        }
        take_result(drv);
    }
    if (drv->done_kind == PEND_JOIN)
        drv->done_kind = PEND_NONE;
    drv->leave_after_cancel = 0;    /* the join disconnects first */
    os_memset(&join, 0, sizeof(join));
    os_memcpy(join.bssid, params->bssid, ETH_ALEN);
    join.ssid_len = params->ssid_len;
    os_memcpy(join.ssid, params->ssid, params->ssid_len);
    if (ie) {
        join.ie_len = 2 + ie[1];
        os_memcpy(join.ie, ie, join.ie_len);
    }
    drv->associated = 0;
    os_memcpy(drv->bssid, params->bssid, ETH_ALEN);
    os_memcpy(drv->ssid, params->ssid, params->ssid_len);
    drv->ssid_len = params->ssid_len;
    os_memcpy(drv->wpa_ie, join.ie, join.ie_len);
    drv->wpa_ie_len = join.ie_len;
    drv->freq = params->freq.freq;
    wpa_printf(MSG_DEBUG, "mv8688: join " MACSTR " with a %u-byte element",
               MAC2STR(params->bssid), (unsigned) join.ie_len);
    return queue(drv, DCMD_MV8688STA_JOIN, &join, sizeof(join), &join.request, PEND_JOIN);
}

static int mv8688_deauthenticate(void *priv, const u8 *addr, u16 reason_code)
{
    struct mv8688_drv *drv = priv;
    struct mv8688sta_ctl_request req;
    int rc;
    (void) addr;
    wpa_printf(MSG_DEBUG, "mv8688: deauthenticate reason %u", reason_code);
    /* A join still outstanding is cancelled now, even if the disconnect
     * below cannot wait for it; one already finished is not reported. */
    if (drv->outstanding && drv->kind == PEND_JOIN)
        drv->join_cancelled = 1;
    if (drv->done_kind == PEND_JOIN)
        drv->done_kind = PEND_NONE;
    drv->associated = 0;
    os_memset(&req, 0, sizeof(req));
    rc = run(drv, DCMD_MV8688STA_DISCONNECT, &req, sizeof(req), &req.request, "disconnect");
    if (!rc)
        drv->leave_after_cancel = 0;
    drv->associated = 0;
    return rc;
}

static int mv8688_set_key(void *priv, struct wpa_driver_set_key_params *params)
{
    struct mv8688_drv *drv = priv;
    struct mv8688sta_ctl_key key;
    int pairwise = (params->key_flag & KEY_FLAG_PAIRWISE) != 0;

    os_memset(&key, 0, sizeof(key));
    key.pairwise = pairwise;
    switch (params->alg) {
    case WPA_ALG_NONE:
        /* A join clears both keys first (lbs_cfg_connect), so only a
         * clear while associated needs the firmware. */
        if (!drv->associated || params->key_idx > 3)
            return 0;
        key.cipher = MV8688STA_CTL_KEY_CLEAR;
        break;
    case WPA_ALG_CCMP:
        key.cipher = MV8688STA_CTL_KEY_CCMP;
        break;
    case WPA_ALG_TKIP:
        key.cipher = MV8688STA_CTL_KEY_TKIP;
        break;
    default:
        wpa_printf(MSG_ERROR, "mv8688: key algorithm %d is not offered", params->alg);
        return -1;
    }
    if (key.cipher != MV8688STA_CTL_KEY_CLEAR) {
        if (params->key_len > sizeof(key.key))
            return -1;
        key.key_len = params->key_len;
        os_memcpy(key.key, params->key, params->key_len);
    }
    wpa_printf(MSG_DEBUG, "mv8688: %s %s key, index %d", key.cipher ? "set" : "clear",
               pairwise ? "pairwise" : "group", params->key_idx);
    return run(drv, DCMD_MV8688STA_SET_KEY, &key, sizeof(key), &key.request, "key");
}

static int mv8688_get_capa(void *priv, struct wpa_driver_capa *capa)
{
    (void) priv;
    os_memset(capa, 0, sizeof(*capa));
    capa->key_mgmt = WPA_DRIVER_CAPA_KEY_MGMT_WPA | WPA_DRIVER_CAPA_KEY_MGMT_WPA2 |
        WPA_DRIVER_CAPA_KEY_MGMT_WPA_PSK | WPA_DRIVER_CAPA_KEY_MGMT_WPA2_PSK;
    capa->enc = WPA_DRIVER_CAPA_ENC_TKIP | WPA_DRIVER_CAPA_ENC_CCMP;
    capa->auth = WPA_DRIVER_AUTH_OPEN;
    capa->max_scan_ssids = 2;      /* see mv8688_scan2 */
    return 0;
}

static const u8 * mv8688_get_mac_addr(void *priv)
{
    struct mv8688_drv *drv = priv;
    return drv->own_addr;
}

static void * mv8688_init(void *ctx, const char *ifname)
{
    struct mv8688_drv *drv;
    char path[64];

    if (os_strlen(ifname) >= IFNAMSIZ || os_strchr(ifname, '/'))
        return NULL;
    drv = os_zalloc(sizeof(*drv));
    if (!drv)
        return NULL;
    drv->ctx = ctx;
    os_strlcpy(drv->ifname, ifname, sizeof(drv->ifname));
    os_snprintf(path, sizeof(path), "/dev/io-net/%s", ifname);
    drv->fd = open(path, O_RDWR);
    if (drv->fd < 0) {
        wpa_printf(MSG_ERROR, "mv8688: %s: %s", path, strerror(errno));
        os_free(drv);
        return NULL;
    }
    if (get_status(drv) || drv->st.state == MV8688STA_CTL_DOWN ||
        drv->st.state == MV8688STA_CTL_FAILED) {
        wpa_printf(MSG_ERROR, "mv8688: %s is not a running station driver", ifname);
        close(drv->fd);
        os_free(drv);
        return NULL;
    }
    os_memcpy(drv->own_addr, drv->st.mac, ETH_ALEN);
    drv->mic_ucast = drv->st.mic_ucast;
    drv->mic_mcast = drv->st.mic_mcast;
    if (drv->st.state == MV8688STA_CTL_ASSOCIATED)
        wpa_printf(MSG_INFO, "mv8688: %s is already associated; the supplicant will "
                   "start a new association", ifname);
    wpa_printf(MSG_DEBUG, "mv8688: %s " MACSTR " fw_release=0x%08x", ifname,
               MAC2STR(drv->own_addr), (unsigned) drv->st.fw_release);
    eloop_register_timeout(0, POLL_US, poll_status, drv, NULL);
    return drv;
}

static void mv8688_deinit(void *priv)
{
    struct mv8688_drv *drv = priv;
    eloop_cancel_timeout(poll_status, drv, NULL);
    if (drv->outstanding)
        wait_done(drv, drv->kind == PEND_JOIN ? JOIN_WAIT_MS : SCAN_WAIT_MS);
    close(drv->fd);
    os_free(drv);
}

const struct wpa_driver_ops wpa_driver_mv8688_ops = {
    .name = "mv8688",
    .desc = "Marvell 88W8688 station driver (devn-mv8688sta.so) on QNX io-pkt",
    .get_bssid = mv8688_get_bssid,
    .get_ssid = mv8688_get_ssid,
    .set_key = mv8688_set_key,
    .init = mv8688_init,
    .deinit = mv8688_deinit,
    .deauthenticate = mv8688_deauthenticate,
    .associate = mv8688_associate,
    .get_capa = mv8688_get_capa,
    .get_mac_addr = mv8688_get_mac_addr,
    .scan2 = mv8688_scan2,
    .get_scan_results2 = mv8688_get_scan_results2,
};
