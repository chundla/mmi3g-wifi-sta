/*
 * Station control; see mv8688sta.h. Each step logs one line, as the probe
 * did on the unit, so a driver run can be read against the 5.0 and 5.1
 * results.
 */
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include "mv8688sta.h"

static void say(struct mv8688_sta *s, const char *fmt, ...)
{
    char line[200];
    va_list ap;
    if (!s->log)
        return;
    va_start(ap, fmt);
    vsnprintf(line, sizeof(line), fmt, ap);
    va_end(ap);
    s->log(s->log_ctx, line);
}

static int fail(struct mv8688_sta *s, const char *step, int rc)
{
    s->step = step;
    s->rc = rc;
    return rc;
}

static const char *mac_text(const uint8_t *m, char *text)
{
    snprintf(text, 18, "%02x:%02x:%02x:%02x:%02x:%02x", m[0], m[1], m[2], m[3], m[4], m[5]);
    return text;
}

const char *mv8688_sta_event_name(uint32_t cause)
{
    switch (cause & 0xff) {
    case MV_EVENT_LINK_LOST_SCAN: return "link lost (scan)";
    case MV_EVENT_LINK_LOST: return "link lost";
    case MV_EVENT_DEAUTHENTICATED: return "deauthenticated";
    case MV_EVENT_DISASSOCIATED: return "disassociated";
    }
    return "other";
}

/* A command whose failure is the caller's; the line names the command. */
static int run(struct mv8688_sta *s, const char *name, uint16_t command, const void *body,
               unsigned len)
{
    struct mv8688_cmd *c = &s->cmd;
    int rc = mv8688_command(c, command, body, len, MV_CMD_TIMEOUT_MS);
    if (rc)
        say(s, "cmd: %s FAILED rc=%d at %s io_rc=%d result=0x%04x", name, rc,
            c->step ? c->step : "?", c->io_rc, c->resp_result);
    else
        say(s, "cmd: %s ok size=%u polls=%u tries=%u", name, c->resp_size, c->polls,
            c->write_tries);
    return rc ? fail(s, name, rc) : 0;
}

/* One that libertas would not notice being refused on a fresh firmware. */
static int run_tolerated(struct mv8688_sta *s, const char *name, uint16_t command,
                         const void *body, unsigned len)
{
    int rc = run(s, name, command, body, len);
    if (rc == MV_ECMDRESULT) {
        say(s, "join: %s refused; nothing was set to clear", name);
        s->step = NULL;
        s->rc = 0;
        return 0;
    }
    return rc;
}

/* The link ends with the first link-lost, deauthenticated or
 * disassociated event after the association. */
static void check_link(struct mv8688_sta *s)
{
    struct mv8688_cmd *c = &s->cmd;
    if (s->state != MV_STA_ASSOCIATED || c->disconnects == s->link_mark)
        return;
    s->link_cause = c->disconnect_cause;
    s->state = MV_STA_IDLE;
    s->link_losses++;
    say(s, "link: ended by event 0x%08x (%s)", (unsigned)s->link_cause,
        mv8688_sta_event_name(s->link_cause));
}

static int set_mac(struct mv8688_sta *s, const uint8_t *mac)
{
    static const uint8_t none[6];
    uint8_t body[8], got[6];
    char text[18];
    int rc;
    mv8688_mac_body(body, MV_ACT_SET, mac);
    if ((rc = run(s, "MAC_ADDRESS set", MV_CMD_MAC_ADDRESS, body, sizeof(body))))
        return rc;
    mv8688_mac_body(body, MV_ACT_GET, none);
    rc = run(s, "MAC_ADDRESS get", MV_CMD_MAC_ADDRESS, body, sizeof(body));
    if (rc == MV_ECMDRESULT) {
        say(s, "mac: read-back refused by the firmware; the set result stands");
        s->step = NULL;
        s->rc = 0;
        return 0;
    }
    if (rc)
        return rc;
    if (mv8688_resp_mac(&s->cmd, got))
        return fail(s, "mac_readback", MV_ECMDRESP);
    if (memcmp(got, mac, 6)) {
        say(s, "mac: read-back %s does NOT match", mac_text(got, text));
        return fail(s, "mac_readback", MV_STA_EMAC);
    }
    say(s, "mac: %s set and read back", mac_text(got, text));
    return 0;
}

int mv8688_sta_start(struct mv8688_sta *s, const uint8_t mac[6])
{
    struct mv8688_cmd *c = &s->cmd;
    uint8_t hw_body[MV_HW_SPEC_SIZE - MV_CMD_HEADER], control[4];
    uint32_t r;
    int rc;

    s->state = MV_STA_DOWN;
    s->func_init = 0;
    s->step = NULL;
    s->rc = 0;
    s->connects = s->link_losses = 0;
    memcpy(s->mac, mac, 6);
    if ((rc = mv8688_cmd_start(c))) {
        say(s, "cmd: host interrupt mask FAILED rc=%d", rc);
        return fail(s, "int_mask", rc);
    }
    if ((rc = run(s, "FUNC_INIT", MV_CMD_FUNC_INIT, NULL, 0)))
        return rc;
    s->func_init = 1;
    /* As libertas: the MAC field carries the broadcast address. */
    memset(hw_body, 0, sizeof(hw_body));
    memset(hw_body + 8, 0xff, 6);
    if ((rc = run(s, "GET_HW_SPEC", MV_CMD_GET_HW_SPEC, hw_body, sizeof(hw_body))))
        return rc;
    if (mv8688_parse_hw_spec(c, &s->spec))
        return fail(s, "hw_spec", MV_ECMDRESP);
    /* libertas moves the top byte (the patch level) to the bottom. */
    r = s->spec.fwrelease << 8 | (s->spec.fwrelease >> 24 & 0xff);
    say(s, "hw_spec: fw_release=%u.%u.%up%u capinfo=0x%08x region=0x%04x", r >> 24,
        r >> 16 & 0xff, r >> 8 & 0xff, r & 0xff, (unsigned)s->spec.fwcapinfo,
        s->spec.regioncode);
    if ((rc = set_mac(s, mac)))
        return rc;
    control[0] = MV_MAC_RX_ON | MV_MAC_TX_ON;
    control[1] = control[2] = control[3] = 0;
    if ((rc = run(s, "MAC_CONTROL", MV_CMD_MAC_CONTROL, control, sizeof(control))))
        return rc;
    s->state = MV_STA_IDLE;
    return 0;
}

static int ssid_is(const struct mv8688_bss *b, const uint8_t *ssid, unsigned len)
{
    return b->ssid_len == (int)len && !memcmp(b->ssid, ssid, len);
}

/* Keeps one entry per BSSID, the strongest report (the lower rssi). */
static void keep_result(struct mv8688_sta *s, const struct mv8688_bss *b)
{
    unsigned i;
    for (i = 0; i < s->result_n; i++) {
        if (!memcmp(s->result[i].bssid, b->bssid, 6)) {
            if (b->rssi < s->result[i].rssi)
                s->result[i] = *b;
            return;
        }
    }
    if (s->result_n < MV_STA_BSS_MAX)
        s->result[s->result_n++] = *b;
}

/* Channels 1-11 in libertas' groups of four: active for the SSID when one
 * is given, passive otherwise. result[] is refilled. A refused group is
 * skipped; a transport failure ends the scan. */
static int scan(struct mv8688_sta *s, const uint8_t *ssid, unsigned len)
{
    static const uint8_t channels[MV_STA_CHANNELS] = { 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11 };
    uint8_t body[MV_SCAN_BODY_MAX];
    unsigned first, n, body_len, sets, i, groups = 0, refused = 0;
    unsigned dwell = len ? MV_STA_SCAN_ACTIVE_MS : MV_STA_SCAN_PASSIVE_MS;
    int rc;

    s->result_n = 0;
    for (first = 0; first < MV_STA_CHANNELS; first += n) {
        groups++;
        n = MV_STA_CHANNELS - first;
        if (n > MV_SCAN_CHANNELS)
            n = MV_SCAN_CHANNELS;
        body_len = mv8688_scan_body(body, channels + first, n, dwell, len ? ssid : NULL, len);
        if ((rc = run(s, "SCAN", MV_CMD_SCAN, body, body_len))) {
            if (rc != MV_ECMDRESULT)
                return rc;
            refused++;
            continue;
        }
        if (mv8688_parse_scan(&s->cmd, s->seen, MV_STA_BSS_MAX, &sets))
            return fail(s, "scan_parse", MV_ECMDRESP);
        for (i = 0; i < sets && i < MV_STA_BSS_MAX; i++)
            keep_result(s, &s->seen[i]);
        s->heard += sets;
    }
    /* Nothing was scanned at all: report the refusal, not an absent SSID. */
    if (refused == groups)
        return fail(s, "scan_refused", MV_ECMDRESULT);
    s->step = NULL;
    s->rc = 0;
    return 0;
}

/* lbs_cfg_connect in its order, for cipher 0 without an element and for a
 * WPA group cipher with one; MAC_CONTROL went at start, without WEP.
 * *asked is set once ASSOCIATE is about to be written. */
static int associate(struct mv8688_sta *s, int *asked, const uint8_t *wpa_ie,
                     unsigned wpa_ie_len)
{
    const struct mv8688_bss *b = &s->bss;
    uint8_t body[MV_ASSOC_BODY_MAX];
    unsigned len;
    int rc;

    mv8688_wep_remove_body(body);
    if ((rc = run_tolerated(s, "SET_WEP remove", MV_CMD_SET_WEP, body, MV_WEP_BODY_SIZE)))
        return rc;
    mv8688_key_clear_body(body, MV_KEY_INFO_UNICAST);
    if ((rc = run_tolerated(s, "KEY_MATERIAL clear unicast", MV_CMD_KEY_MATERIAL, body,
                            MV_KEY_BODY_SIZE)))
        return rc;
    mv8688_key_clear_body(body, MV_KEY_INFO_MCAST);
    if ((rc = run_tolerated(s, "KEY_MATERIAL clear group", MV_CMD_KEY_MATERIAL, body,
                            MV_KEY_BODY_SIZE)))
        return rc;
    mv8688_action_body(body, MV_ACT_SET, wpa_ie_len ? 1 : 0);
    if ((rc = run(s, wpa_ie_len ? "ENABLE_RSN on" : "ENABLE_RSN off", MV_CMD_ENABLE_RSN,
                  body, 4)))
        return rc;
    s->rsn = wpa_ie_len != 0;
    mv8688_auth_body(body, b->bssid);
    if ((rc = run(s, "AUTHENTICATE open", MV_CMD_AUTHENTICATE, body, MV_AUTH_BODY_SIZE)))
        return rc;
    /* Firmware v9 and later takes no preamble here: radio on only. */
    mv8688_action_body(body, MV_ACT_SET, 1);
    if ((rc = run(s, "RADIO_CONTROL on", MV_CMD_RADIO_CONTROL, body, 4)))
        return rc;
    if (!(len = mv8688_assoc_wpa_body(body, b, wpa_ie, wpa_ie_len)))
        return fail(s, "assoc_body", wpa_ie_len ? MV_STA_EINVAL : MV_STA_ENOTOPEN);
    s->link_mark = s->cmd.disconnects;
    *asked = 1;
    if ((rc = run(s, "ASSOCIATE", MV_CMD_ASSOCIATE, body, len)))
        return rc;
    if (mv8688_parse_assoc(&s->cmd, &s->assoc_capability, &s->assoc_status, &s->aid))
        return fail(s, "assoc_parse", MV_ECMDRESP);
    say(s, "join: association status=0x%04x aid=0x%04x capability=0x%04x",
        s->assoc_status, s->aid, s->assoc_capability);
    if (s->assoc_status)
        return fail(s, "assoc_status", MV_STA_EASSOC);
    return 0;
}

static void clear_join(struct mv8688_sta *s)
{
    memset(&s->bss, 0, sizeof(s->bss));
    s->assoc_status = 0xffff;
    s->aid = s->assoc_capability = 0;
    s->scans = s->heard = s->found = 0;
    s->rsn = 0;
    s->step = NULL;
    s->rc = 0;
}

/* Joins s->bss; after any ASSOCIATE attempt the firmware's state is
 * unknown, so a failure leaves, as the probe did. A refusal then is
 * expected and only logged. */
static int join_bss(struct mv8688_sta *s, const uint8_t *wpa_ie, unsigned wpa_ie_len)
{
    uint8_t body[MV_DEAUTH_BODY_SIZE];
    int rc, asked = 0;
    if (!(rc = associate(s, &asked, wpa_ie, wpa_ie_len))) {
        s->state = MV_STA_ASSOCIATED;
        s->connects++;
        return 0;
    }
    if (asked) {
        mv8688_deauth_body(body, s->bss.bssid, MV_REASON_LEAVING);
        if (mv8688_command(&s->cmd, MV_CMD_DEAUTHENTICATE, body, sizeof(body),
                           MV_CMD_TIMEOUT_MS))
            say(s, "join: DEAUTHENTICATE after the failed association was refused or lost");
    }
    return rc;
}

static int usable(const struct mv8688_bss *b)
{
    return (b->capability & MV_CAP_ESS) && b->channel >= 1 && b->channel <= MV_STA_CHANNELS;
}

int mv8688_sta_connect(struct mv8688_sta *s, const uint8_t *ssid, unsigned ssid_len)
{
    const struct mv8688_bss *b = &s->bss;
    char text[18];
    unsigned i;
    int rc;

    if (s->state != MV_STA_IDLE)
        return fail(s, "connect_state", MV_STA_ESTATE);
    if (!ssid_len || ssid_len > 32)
        return fail(s, "connect_ssid", MV_STA_EINVAL);
    clear_join(s);
    while (!s->found && s->scans < MV_STA_SCAN_TRIES) {
        s->scans++;
        if ((rc = scan(s, ssid, ssid_len)))
            return rc;
        for (i = 0; i < s->result_n; i++) {
            const struct mv8688_bss *r = &s->result[i];
            if (!ssid_is(r, ssid, ssid_len))
                continue;
            say(s, "scan: bss %s ch=%d rssi=%u (~%d dBm) cap=0x%04x rsn=%d",
                mac_text(r->bssid, text), r->channel, r->rssi, 3 - (int)r->rssi,
                r->capability, r->rsn);
            if (!s->found++ || r->rssi < s->bss.rssi)
                s->bss = *r;
        }
        say(s, "scan: %u: %u descriptors, %u with the SSID", s->scans, s->heard, s->found);
    }
    if (!s->found)
        return fail(s, "ssid_absent", MV_STA_ENOTFOUND);
    if (!usable(b) || (b->capability & MV_CAP_PRIVACY) || b->rsn) {
        say(s, "join: REFUSED the BSS is not an open infrastructure network on 1-11");
        return fail(s, "not_open", MV_STA_ENOTOPEN);
    }
    return join_bss(s, NULL, 0);
}

int mv8688_sta_scan(struct mv8688_sta *s, const uint8_t *ssid, unsigned ssid_len)
{
    int rc;
    if (s->state != MV_STA_IDLE)
        return fail(s, "scan_state", MV_STA_ESTATE);
    if (ssid_len > 32)
        return fail(s, "scan_ssid", MV_STA_EINVAL);
    s->heard = 0;
    s->step = NULL;
    s->rc = 0;
    rc = scan(s, ssid, ssid_len);
    s->scans_done++;
    say(s, "scan: %s, %u descriptors, %u BSSs kept, rc=%d", ssid_len ? "active" : "passive",
        s->heard, s->result_n, rc);
    return rc;
}

int mv8688_sta_join(struct mv8688_sta *s, const uint8_t bssid[6], const uint8_t *ssid,
                    unsigned ssid_len, const uint8_t *wpa_ie, unsigned wpa_ie_len)
{
    const struct mv8688_bss *b = &s->bss;
    char text[18];
    unsigned i;

    if (s->state != MV_STA_IDLE)
        return fail(s, "join_state", MV_STA_ESTATE);
    if (!ssid_len || ssid_len > 32 || (wpa_ie_len && mv8688_wpa_ie_check(wpa_ie, wpa_ie_len)))
        return fail(s, "join_args", MV_STA_EINVAL);
    clear_join(s);
    for (i = 0; i < s->result_n; i++)
        if (!memcmp(s->result[i].bssid, bssid, 6) && ssid_is(&s->result[i], ssid, ssid_len))
            break;
    if (i == s->result_n)
        return fail(s, "join_unscanned", MV_STA_ENOTSCANNED);
    s->bss = s->result[i];
    s->found = 1;
    say(s, "join: bss %s ch=%d rssi=%u (~%d dBm) cap=0x%04x rsn=%d element=%u bytes",
        mac_text(b->bssid, text), b->channel, b->rssi, 3 - (int)b->rssi, b->capability,
        b->rsn, wpa_ie_len);
    if (!usable(b) || (wpa_ie_len ? !(b->capability & MV_CAP_PRIVACY) || !b->rsn
                                  : (b->capability & MV_CAP_PRIVACY) || b->rsn)) {
        say(s, "join: REFUSED the BSS does not match the %s request on 1-11",
            wpa_ie_len ? "WPA" : "open");
        return fail(s, "bss_mismatch", MV_STA_ENOTOPEN);
    }
    return join_bss(s, wpa_ie, wpa_ie_len);
}

int mv8688_sta_set_key(struct mv8688_sta *s, unsigned key_type, int pairwise,
                       const uint8_t *key, unsigned key_len)
{
    uint8_t body[MV_KEY_BODY_SIZE];
    const char *name;
    int rc;
    if (s->state != MV_STA_IDLE && s->state != MV_STA_ASSOCIATED)
        return fail(s, "key_state", MV_STA_ESTATE);
    if (!key_len) {
        mv8688_key_clear_body(body, pairwise ? MV_KEY_INFO_UNICAST : MV_KEY_INFO_MCAST);
        name = pairwise ? "KEY_MATERIAL clear unicast" : "KEY_MATERIAL clear group";
    } else if (mv8688_key_body(body, key_type, pairwise, key, key_len)) {
        return fail(s, "key_args", MV_STA_EINVAL);
    } else {
        name = pairwise ? "KEY_MATERIAL set unicast" : "KEY_MATERIAL set group";
    }
    rc = run(s, name, MV_CMD_KEY_MATERIAL, body, sizeof(body));
    if (!rc)
        s->key_sets++;
    return rc;
}

int mv8688_sta_disconnect(struct mv8688_sta *s)
{
    uint8_t body[MV_DEAUTH_BODY_SIZE];
    if (s->state != MV_STA_ASSOCIATED)
        return 0;
    s->state = MV_STA_IDLE;
    mv8688_deauth_body(body, s->bss.bssid, MV_REASON_LEAVING);
    return run(s, "DEAUTHENTICATE", MV_CMD_DEAUTHENTICATE, body, sizeof(body));
}

int mv8688_sta_poll(struct mv8688_sta *s, unsigned timeout_ms)
{
    int rc = mv8688_wait(&s->cmd, timeout_ms);
    check_link(s);
    return rc ? fail(s, s->cmd.step ? s->cmd.step : "wait", rc) : 0;
}

int mv8688_sta_send(struct mv8688_sta *s, const uint8_t *frame, unsigned len)
{
    int rc;
    if (s->state != MV_STA_ASSOCIATED)
        return MV_STA_ESTATE;
    rc = mv8688_send_data(&s->cmd, frame, len, MV_TX_DONE_MS);
    check_link(s);
    return rc ? fail(s, s->cmd.step ? s->cmd.step : "send", rc) : 0;
}

int mv8688_sta_stop(struct mv8688_sta *s)
{
    int rc = 0, step_rc;
    if (s->state == MV_STA_ASSOCIATED)
        rc = mv8688_sta_disconnect(s);
    if (s->func_init) {
        s->func_init = 0;
        step_rc = run(s, "FUNC_SHUTDOWN", MV_CMD_FUNC_SHUTDOWN, NULL, 0);
        rc = rc ? rc : step_rc;
    }
    step_rc = mv8688_cmd_stop(&s->cmd);
    if (step_rc)
        say(s, "cmd: host interrupt mask-off FAILED rc=%d", step_rc);
    s->state = MV_STA_DOWN;
    return rc ? rc : step_rc ? fail(s, "int_unmask", step_rc) : 0;
}
