/*
 * 88W8688 command and event layer; see mv8688cmd.h. Where the factory uAP
 * driver and libertas differ, the factory decides how the FPGA host is
 * driven (block-mode CMD53, abort and retry) and libertas decides what the
 * station firmware is told (mask, ready condition, command layouts).
 */
#include <string.h>
#include "mv8688cmd.h"
#include "mv8688fw.h"

#define MV_IO_READY_MS      1000    /* libertas: 1 s, 1 ms apart */
#define MV_WRITE_TRIES      3       /* factory htoc: three attempts */

static int fail(struct mv8688_cmd *c, const char *step, int rc, int io_rc)
{
    c->step = step;
    c->io_rc = io_rc;
    return rc;
}

static void put16(uint8_t *p, unsigned v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}

static unsigned get16(const uint8_t *p)
{
    return p[0] | (unsigned)p[1] << 8;
}

static uint32_t get32(const uint8_t *p)
{
    return get16(p) | (uint32_t)get16(p + 2) << 16;
}

static void delay_ms(struct mv8688_cmd *c, unsigned ms)
{
    struct hbsdc *h = c->card->host;
    h->io->delay_ms(h->ctx, ms);
}

static unsigned now_ms(struct mv8688_cmd *c)
{
    struct hbsdc *h = c->card->host;
    return h->io->now_ms(h->ctx);
}

/* libertas if_sdio_wait_status(IO_RDY), before every transfer. Bounded by
 * the clock, which includes the time each status read takes, and by the
 * read count in case the clock does not move. */
static int wait_io_ready(struct mv8688_cmd *c)
{
    uint8_t status;
    unsigned reads, t0 = now_ms(c);
    int rc;
    for (reads = 1; ; reads++) {
        if ((rc = sdio_cmd52(c->card, 1, MV_REG_STATUS, 0, 0, &status)))
            return fail(c, "io_ready_read", rc, rc);
        c->ready_reads = reads;
        c->ready_status = status;
        if (status & MV_STATUS_IO_READY)
            return 0;
        if (reads >= MV_IO_READY_MS || now_ms(c) - t0 >= MV_IO_READY_MS)
            return fail(c, "io_ready", MV_ECMDREADY, 0);
        delay_ms(c, 1);
    }
}

int mv8688_cmd_start(struct mv8688_cmd *c)
{
    int rc;
    c->int_seen = 0;
    c->dnld_acks = c->packets = c->events = c->data_packets = c->stray_responses = 0;
    c->event_n = c->disconnects = 0;
    c->disconnect_cause = 0;
    c->data_bytes = c->data_bad = c->data_n = 0;
    c->tx_frames = c->tx_bytes = 0;
    if ((rc = sdio_cmd52(c->card, 1, MV_REG_HOST_INT_MASK, 1, MV_INT_MASK_ALL, NULL)))
        return fail(c, "int_mask", rc, rc);
    return 0;
}

int mv8688_cmd_stop(struct mv8688_cmd *c)
{
    int rc;
    if ((rc = sdio_cmd52(c->card, 1, MV_REG_HOST_INT_MASK, 1, 0, NULL)))
        return fail(c, "int_unmask", rc, rc);
    return 0;
}

/* Factory htoc: block mode at the I/O port; a failed write is terminated
 * with function-1 register 3 and tried again, three attempts in all. buf
 * holds whole blocks. Commands and data frames go the same way, as in
 * libertas if_sdio_host_to_card. */
static int write_packet(struct mv8688_cmd *c, uint8_t *buf, unsigned total, const char *step)
{
    unsigned blocks = (total + MV_BLKSZ - 1) / MV_BLKSZ;
    uint8_t config;
    int rc = 0;
    for (c->write_tries = 1; c->write_tries <= MV_WRITE_TRIES; c->write_tries++) {
        if ((rc = wait_io_ready(c)))
            return rc;
        rc = sdio_cmd53(c->card, 1, c->ioport, 0, 1, buf, blocks, MV_BLKSZ);
        if (rc == 0)
            return 0;
        c->aborts++;
        if (sdio_cmd52(c->card, 1, MV_REG_CONFIG, 1, MV_CONFIG_ABORT, &config))
            c->abort_failures++;
    }
    c->write_tries = MV_WRITE_TRIES;
    return fail(c, step, MV_ECMDWRITE, rc);
}

/* Factory ctoh: the length is function-1 0x42 shifted by the RX unit, read
 * in whole blocks from the I/O port; the packet's own header follows. */
static int read_packet(struct mv8688_cmd *c, unsigned *type)
{
    uint8_t len;
    unsigned blocks;
    int rc;
    if ((rc = sdio_cmd52(c->card, 1, MV_REG_RX_LEN, 0, 0, &len)))
        return fail(c, "rx_len_read", rc, rc);
    c->rx_len_raw = len;
    c->rx_size = (unsigned)len << c->rx_unit;
    if (c->rx_size < 4 || c->rx_size > MV_RX_MAX)
        return fail(c, "rx_len", MV_ECMDRX, 0);
    if ((rc = wait_io_ready(c)))
        return rc;
    blocks = (c->rx_size + MV_BLKSZ - 1) / MV_BLKSZ;
    if ((rc = sdio_cmd53(c->card, 1, c->ioport, 0, 0, c->in, blocks, MV_BLKSZ)))
        return fail(c, "rx_read", MV_ECMDRX, rc);
    c->rx_chunk = get16(c->in);
    *type = get16(c->in + 2);
    if (c->rx_chunk < 4 || c->rx_chunk > c->rx_size)
        return fail(c, "rx_header", MV_ECMDRX, 0);
    c->packets++;
    return 0;
}

static int check_response(struct mv8688_cmd *c, uint16_t command)
{
    c->resp = c->in + 4;
    c->resp_len = c->rx_chunk - 4;
    if (c->resp_len < MV_CMD_HEADER)
        return fail(c, "resp_short", MV_ECMDRESP, 0);
    c->resp_cmd = (uint16_t)get16(c->resp);
    c->resp_size = (uint16_t)get16(c->resp + 2);
    c->resp_seq = (uint16_t)get16(c->resp + 4);
    c->resp_result = (uint16_t)get16(c->resp + 6);
    /* libertas lbs_process_command_response accepts 0x8012 as well; here
     * only for ASSOCIATE, the one command it is known to answer. */
    if (c->resp_cmd != (command | MV_CMD_RESPONSE) &&
        !(command == MV_CMD_ASSOCIATE && c->resp_cmd == MV_CMD_RET_ASSOCIATE)) {
        c->stray_responses++;
        return fail(c, "resp_command", MV_ECMDRESP, 0);
    }
    if (c->resp_size < MV_CMD_HEADER || c->resp_size > c->resp_len)
        return fail(c, "resp_size", MV_ECMDRESP, 0);
    if (c->resp_seq != c->seq)
        return fail(c, "resp_seq", MV_ECMDRESP, 0);
    if (c->resp_result)
        return fail(c, "resp_result", MV_ECMDRESULT, 0);
    return 0;
}

static void note_event(struct mv8688_cmd *c)
{
    uint32_t cause = c->rx_chunk >= 8 ? get32(c->in + 4) : 0xffffffffu;
    c->events++;
    if (c->event_n < MV_EVENTS)
        c->event[c->event_n] = cause;
    c->event_n++;
    switch (cause & 0xff) {
    case MV_EVENT_LINK_LOST_SCAN:
    case MV_EVENT_LINK_LOST:
    case MV_EVENT_DEAUTHENTICATED:
    case MV_EVENT_DISASSOCIATED:
        c->disconnects++;
        c->disconnect_cause = cause;
        break;
    case MV_EVENT_MIC_ERR_UCAST:
        c->mic_ucast++;
        break;
    case MV_EVENT_MIC_ERR_MCAST:
        c->mic_mcast++;
    }
}

static void note_data(struct mv8688_cmd *c)
{
    struct mv8688_rx rx;
    uint8_t *f;
    c->data_packets++;
    if (mv8688_parse_rxpd(c->in + 4, c->rx_chunk - 4, &rx)) {
        c->data_bad++;
        return;
    }
    c->data_bytes += rx.frame_len;
    if (c->data_n < MV_DATA_KEPT)
        c->data[c->data_n] = rx;
    c->data_n++;
    if (!c->on_frame)
        return;
    f = c->in + 4 + rx.pkt_ptr;
    if (!rx.ethertype) {
        c->on_frame(c->frame_ctx, &rx, f, rx.frame_len);
        return;
    }
    /* libertas rx.c: the addresses move up against the SNAP type, which
     * leaves an Ethernet II frame 8 bytes further on. */
    memmove(f + 8, f, 12);
    c->on_frame(c->frame_ctx, &rx, f + 8, rx.frame_len - 8);
}

/* One look at the interrupt status, reading the packet it announces.
 * *type is that packet's type, or -1 when none was read. Events and data
 * frames are recorded here; a command response is left to the caller. */
static int poll_once(struct mv8688_cmd *c, int *type)
{
    uint8_t status;
    unsigned got;
    int rc;
    *type = -1;
    if ((rc = sdio_cmd52(c->card, 1, MV_REG_HOST_INT_STATUS, 0, 0, &status)))
        return fail(c, "int_status", rc, rc);
    c->int_seen |= status;
    if (!status)
        return 0;
    /* libertas: write the complement; set bits are cleared by 0. */
    if ((rc = sdio_cmd52(c->card, 1, MV_REG_HOST_INT_STATUS, 1, (uint8_t)~status, NULL)))
        return fail(c, "int_clear", rc, rc);
    if (status & MV_INT_DNLD)
        c->dnld_acks++;
    if (!(status & MV_INT_UPLD))
        return 0;
    if ((rc = read_packet(c, &got)))
        return rc;
    if (got == MV_TYPE_EVENT)
        note_event(c);
    else if (got == MV_TYPE_DATA)
        note_data(c);
    else if (got != MV_TYPE_CMD)
        return fail(c, "rx_type", MV_ECMDRX, 0);
    *type = (int)got;
    return 0;
}

int mv8688_command(struct mv8688_cmd *c, uint16_t command, const void *body,
                   unsigned body_len, unsigned timeout_ms)
{
    unsigned total = 4 + MV_CMD_HEADER + body_len, t0;
    int rc, type;

    c->step = NULL;
    c->io_rc = 0;
    c->resp = NULL;
    c->resp_len = 0;
    c->resp_cmd = c->resp_size = c->resp_seq = c->resp_result = 0;
    c->ready_reads = c->write_tries = c->polls = 0;
    c->rx_len_raw = c->rx_size = c->rx_chunk = 0;
    if (total > MV_CMD_FRAME_MAX)
        return fail(c, "cmd_size", HBSDC_EINVAL, 0);
    c->seq++;
    memset(c->out, 0, sizeof(c->out));
    put16(c->out, total);
    put16(c->out + 2, MV_TYPE_CMD);
    put16(c->out + 4, command);
    put16(c->out + 6, MV_CMD_HEADER + body_len);
    put16(c->out + 8, c->seq);
    if (body_len)
        memcpy(c->out + 4 + MV_CMD_HEADER, body, body_len);
    if ((rc = write_packet(c, c->out, total, "cmd_write")))
        return rc;

    /* The clock bounds the wait, whatever each status read, packet read or
     * IO_RDY wait costs; the poll count stands in if the clock stops. */
    t0 = now_ms(c);
    for (c->polls = 1; ; c->polls++) {
        if ((rc = poll_once(c, &type)))
            return rc;
        if (type == MV_TYPE_CMD)
            return check_response(c, command);
        if (now_ms(c) - t0 >= timeout_ms || c->polls >= timeout_ms)
            return fail(c, "resp_timeout", MV_ECMDTIMEOUT, 0);
        /* After an event or a frame, look again at once. */
        if (type < 0)
            delay_ms(c, 1);
    }
}

int mv8688_wait(struct mv8688_cmd *c, unsigned timeout_ms)
{
    unsigned polls, t0 = now_ms(c);
    int rc, type;
    c->step = NULL;
    c->io_rc = 0;
    c->wait_done = 0;
    for (polls = 1; ; polls++) {
        if ((rc = poll_once(c, &type)))
            return rc;
        if (type == MV_TYPE_CMD)
            c->stray_responses++;
        if (c->wait_done || now_ms(c) - t0 >= timeout_ms || polls >= timeout_ms)
            return 0;
        if (type < 0)
            delay_ms(c, 1);
    }
}

/* struct txpd: status, control, packet offset, length, destination, then
 * priority, power, delay and a reserved byte. libertas fills in only the
 * offset, the length and the destination for a station. */
int mv8688_send_data(struct mv8688_cmd *c, const uint8_t *frame, unsigned len,
                     unsigned timeout_ms)
{
    unsigned total = 4 + MV_TXPD_SIZE + len, blocks, acks, t0;
    uint8_t *p = c->tx;
    int rc, type;

    c->step = NULL;
    c->io_rc = 0;
    c->tx_blocks = c->tx_polls = 0;
    c->ready_reads = c->write_tries = 0;
    if (len < MV_ETH_MIN || len > MV_ETH_MAX)
        return fail(c, "tx_size", HBSDC_EINVAL, 0);
    blocks = (total + MV_BLKSZ - 1) / MV_BLKSZ;
    memset(p, 0, blocks * MV_BLKSZ);
    put16(p, total);
    put16(p + 2, MV_TYPE_DATA);
    put16(p + 12, MV_TXPD_SIZE);        /* location, from the txpd */
    put16(p + 16, len);
    memcpy(p + 18, frame, 6);           /* destination */
    memcpy(p + 4 + MV_TXPD_SIZE, frame, len);
    acks = c->dnld_acks;
    if ((rc = write_packet(c, p, total, "tx_write")))
        return rc;
    c->tx_blocks = blocks;
    c->tx_frames++;
    c->tx_bytes += len;

    /* libertas sends nothing more until the card's DNLD (dnld_sent). */
    t0 = now_ms(c);
    for (c->tx_polls = 1; ; c->tx_polls++) {
        if ((rc = poll_once(c, &type)))
            return rc;
        if (type == MV_TYPE_CMD)
            c->stray_responses++;
        if (c->dnld_acks != acks)
            return 0;
        if (now_ms(c) - t0 >= timeout_ms || c->tx_polls >= timeout_ms)
            return fail(c, "tx_done", MV_ETXDONE, 0);
        if (type < 0)
            delay_ms(c, 1);
    }
}

/* struct rxpd: status, SNR, control, length, noise floor, rate, then the
 * offset of the 802.3 header, the next pointer and the priority. */
int mv8688_parse_rxpd(const uint8_t *p, unsigned len, struct mv8688_rx *rx)
{
    static const uint8_t snap[6] = { 0xaa, 0xaa, 0x03, 0x00, 0x00, 0x00 };
    const uint8_t *f;
    memset(rx, 0, sizeof(*rx));
    if (len < MV_RXPD_SIZE)
        return MV_ECMDRX;
    rx->status = (uint16_t)get16(p);
    rx->snr = p[2];
    rx->pkt_len = (uint16_t)get16(p + 4);
    rx->nf = p[6];
    rx->rate = p[7];
    rx->pkt_ptr = get32(p + 8);
    rx->priority = p[16];
    if (rx->pkt_ptr < MV_RXPD_SIZE || rx->pkt_ptr > len || len - rx->pkt_ptr < 14)
        return MV_ECMDRX;
    rx->frame_len = len - rx->pkt_ptr;
    f = p + rx->pkt_ptr;
    memcpy(rx->dst, f, 6);
    memcpy(rx->src, f + 6, 6);
    /* The firmware passes frames up as 802.3 with an LLC/SNAP header. */
    if (rx->frame_len >= 22 && !memcmp(f + 14, snap, sizeof(snap)))
        rx->ethertype = (uint16_t)(f[20] << 8 | f[21]);
    return 0;
}

int mv8688_parse_hw_spec(const struct mv8688_cmd *c, struct mv8688_hw_spec *spec)
{
    const uint8_t *p = c->resp;
    if (!p || c->resp_size < MV_HW_SPEC_SIZE || c->resp_len < MV_HW_SPEC_SIZE)
        return MV_ECMDRESP;
    spec->hwifversion = (uint16_t)get16(p + 8);
    spec->version = (uint16_t)get16(p + 10);
    spec->nr_txpd = (uint16_t)get16(p + 12);
    spec->nr_mcast_adr = (uint16_t)get16(p + 14);
    memcpy(spec->mac, p + 16, 6);
    spec->regioncode = (uint16_t)get16(p + 22);
    spec->nr_antenna = (uint16_t)get16(p + 24);
    spec->fwrelease = get32(p + 26);
    spec->wcb_base = get32(p + 30);
    spec->rxpd_rdptr = get32(p + 34);
    spec->rxpd_wrptr = get32(p + 38);
    spec->fwcapinfo = get32(p + 42);
    return 0;
}

static int hex_digit(char ch)
{
    if (ch >= '0' && ch <= '9')
        return ch - '0';
    if (ch >= 'a' && ch <= 'f')
        return ch - 'a' + 10;
    if (ch >= 'A' && ch <= 'F')
        return ch - 'A' + 10;
    return -1;
}

int mv8688_parse_mac(const char *text, uint8_t mac[6])
{
    static const uint8_t placeholder[6] = { 0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff };
    static const uint8_t zero[6];
    unsigned i;
    int hi, lo;
    for (i = 0; i < 6; i++) {
        hi = hex_digit(text[3 * i]);
        lo = hi < 0 ? -1 : hex_digit(text[3 * i + 1]);
        if (lo < 0 || text[3 * i + 2] != (i < 5 ? ':' : '\0'))
            return -1;
        mac[i] = (uint8_t)(hi << 4 | lo);
    }
    if ((mac[0] & 1) || !memcmp(mac, zero, 6) || !memcmp(mac, placeholder, 6))
        return -2;
    return 0;
}

void mv8688_mac_body(uint8_t body[8], unsigned action, const uint8_t mac[6])
{
    put16(body, action);
    memcpy(body + 2, mac, 6);
}

int mv8688_resp_mac(const struct mv8688_cmd *c, uint8_t mac[6])
{
    if (!c->resp || c->resp_size < MV_MAC_ADDRESS_SIZE || c->resp_len < MV_MAC_ADDRESS_SIZE)
        return MV_ECMDRESP;
    memcpy(mac, c->resp + MV_CMD_HEADER + 2, 6);
    return 0;
}

/* libertas lbs_rates: 1, 2, 5.5 and 11 Mb/s marked basic, then OFDM. */
static const uint8_t scan_rates[] = {
    0x82, 0x84, 0x8b, 0x96, 0x0c, 0x12, 0x18, 0x24, 0x30, 0x48, 0x60, 0x6c
};

unsigned mv8688_scan_body(uint8_t *body, const uint8_t *channels, unsigned n,
                          unsigned dwell_ms, const uint8_t *ssid, unsigned ssid_len)
{
    uint8_t *p = body;
    unsigned i;
    if (n < 1 || n > MV_SCAN_CHANNELS || (ssid && (ssid_len < 1 || ssid_len > 32)))
        return 0;
    *p++ = MV_BSS_TYPE_ANY;
    memset(p, 0, 6);                    /* any BSSID */
    p += 6;
    if (ssid) {
        put16(p, MV_TLV_SSID);
        put16(p + 2, ssid_len);
        memcpy(p + 4, ssid, ssid_len);
        p += 4 + ssid_len;
    }
    put16(p, MV_TLV_CHANLIST);
    put16(p + 2, 7 * n);
    p += 4;
    for (i = 0; i < n; i++) {
        p[0] = 0;                       /* radio type B/G */
        p[1] = channels[i];
        p[2] = ssid ? 0 : MV_SCAN_PASSIVE;  /* libertas: active with an SSID */
        put16(p + 3, 0);                /* minimum dwell */
        put16(p + 5, dwell_ms);
        p += 7;
    }
    put16(p, MV_TLV_RATES);
    put16(p + 2, sizeof(scan_rates));
    memcpy(p + 4, scan_rates, sizeof(scan_rates));
    p += 4 + sizeof(scan_rates);
    return (unsigned)(p - body);
}

static void keep_rates(struct mv8688_bss *b, const uint8_t *ie)
{
    unsigned i;
    for (i = 0; i < ie[1] && b->rates_n < MV_RATES_MAX; i++)
        b->rates[b->rates_n++] = ie[2 + i];
}

/* Response: header, descriptor bytes (le16), set count (u8), the
 * descriptors, then a TSF TLV of 8 bytes per set. Each descriptor is a
 * length (le16), BSSID, RSSI, timestamp, beacon interval, capabilities and
 * the beacon's information elements. */
int mv8688_parse_scan(const struct mv8688_cmd *c, struct mv8688_bss *bss, unsigned max,
                      unsigned *sets)
{
    static const uint8_t wpa_oui[4] = { 0x00, 0x50, 0xf2, 0x01 };
    const uint8_t *p, *end, *ie, *ie_end, *ext_ie = NULL;
    unsigned bss_size, nr, i, len, supp, ext;
    struct mv8688_bss *b;
    *sets = 0;
    if (!c->resp || c->resp_size < MV_CMD_HEADER + 3 || c->resp_len < c->resp_size)
        return MV_ECMDRESP;
    bss_size = get16(c->resp + MV_CMD_HEADER);
    nr = c->resp[MV_CMD_HEADER + 2];
    *sets = nr;
    if (!nr)
        return 0;
    p = c->resp + MV_CMD_HEADER + 3;
    end = p + bss_size;
    if (MV_CMD_HEADER + 3 + bss_size + 4 + 8 * nr > c->resp_size)
        return MV_ECMDRESP;
    if (get16(end) != MV_TLV_TSF || get16(end + 2) / 8 != nr)
        return MV_ECMDRESP;
    for (i = 0; i < nr; i++) {
        if (end - p < 2)
            return MV_ECMDRESP;
        len = get16(p);
        p += 2;
        if (len < 19 || len > (unsigned)(end - p))
            return MV_ECMDRESP;
        b = i < max ? &bss[i] : NULL;
        if (b) {
            memset(b, 0, sizeof(*b));
            memcpy(b->bssid, p, 6);
            b->rssi = p[6];
            b->interval = (uint16_t)get16(p + 15);
            b->capability = (uint16_t)get16(p + 17);
            b->channel = b->ssid_len = -1;
        }
        supp = ext = 0;
        ie_end = p + len;
        for (ie = p + 19; ie_end - ie >= 2; ie += 2 + ie[1]) {
            if (ie[1] > ie_end - ie - 2)
                return MV_ECMDRESP;
            if (!b)
                continue;
            if (!b->ie_cut && 2u + ie[1] <= MV_BSS_IE_MAX - b->ie_len) {
                memcpy(b->ie + b->ie_len, ie, 2u + ie[1]);
                b->ie_len += 2u + ie[1];
            } else {
                b->ie_cut = 1;
            }
            if (ie[0] == MV_EID_DS_PARAMS && ie[1] >= 1)
                b->channel = ie[2];
            if (ie[0] == MV_EID_SSID && b->ssid_len < 0) {
                b->ssid_len = ie[1];
                memcpy(b->ssid, ie + 2, ie[1] < 32 ? ie[1] : 32);
            }
            /* libertas skips a rates element longer than MAX_RATES. */
            if (ie[0] == MV_EID_RATES && !supp++ && ie[1] <= MV_RATES_MAX)
                keep_rates(b, ie);
            if (ie[0] == MV_EID_EXT_RATES && !ext++ && ie[1] <= MV_RATES_MAX)
                ext_ie = ie;
            if (ie[0] == MV_EID_RSN ||
                (ie[0] == MV_EID_VENDOR && ie[1] >= 4 && !memcmp(ie + 2, wpa_oui, 4)))
                b->rsn = 1;
        }
        /* Supported rates first, whatever order the elements came in. */
        if (b) {
            b->rates_supp = b->rates_n;
            if (ext_ie)
                keep_rates(b, ext_ie);
        }
        ext_ie = NULL;
        p = ie_end;
    }
    return 0;
}

void mv8688_action_body(uint8_t body[4], unsigned action, unsigned value)
{
    put16(body, action);
    put16(body + 2, value);
}

void mv8688_wep_remove_body(uint8_t body[MV_WEP_BODY_SIZE])
{
    memset(body, 0, MV_WEP_BODY_SIZE);
    put16(body, MV_ACT_REMOVE);         /* key index 0 follows */
}

void mv8688_key_clear_body(uint8_t body[MV_KEY_BODY_SIZE], unsigned key_info)
{
    memset(body, 0, MV_KEY_BODY_SIZE);
    put16(body, MV_ACT_SET);
    put16(body + 2, MV_TLV_KEY_MATERIAL);
    put16(body + 4, MV_KEY_BODY_SIZE - 6);
    put16(body + 6, 0);                 /* key type WEP: libertas "doesn't matter" */
    put16(body + 8, key_info);
    put16(body + 10, 0);                /* no key */
}

int mv8688_key_body(uint8_t body[MV_KEY_BODY_SIZE], unsigned key_type, int pairwise,
                    const uint8_t *key, unsigned key_len)
{
    if (!key || !(key_type == MV_KEY_TYPE_AES ? key_len == 16
                  : key_type == MV_KEY_TYPE_TKIP && key_len == 32))
        return -1;
    mv8688_key_clear_body(body, MV_KEY_INFO_ENABLED |
                          (pairwise ? MV_KEY_INFO_UNICAST : MV_KEY_INFO_MCAST));
    put16(body + 6, key_type);
    put16(body + 10, key_len);
    memcpy(body + 12, key, key_len);
    return 0;
}

int mv8688_wpa_ie_check(const uint8_t *ie, unsigned len)
{
    static const uint8_t wpa_oui[4] = { 0x00, 0x50, 0xf2, 0x01 };
    if (!ie || len < 2 || len > MV_WPA_IE_MAX || ie[1] + 2u != len)
        return -1;
    if (ie[0] == MV_EID_RSN)
        return 0;
    return ie[0] == MV_EID_VENDOR && ie[1] >= 4 && !memcmp(ie + 2, wpa_oui, 4) ? 0 : -1;
}

void mv8688_auth_body(uint8_t body[MV_AUTH_BODY_SIZE], const uint8_t bssid[6])
{
    memset(body, 0, MV_AUTH_BODY_SIZE);
    memcpy(body, bssid, 6);
    body[6] = MV_AUTH_OPEN;
}

/* libertas lbs_rates as 500 kb/s units: 1-11 Mb/s, then 6-54 Mb/s. */
static const uint8_t hw_rates[] = { 2, 4, 11, 22, 12, 18, 24, 36, 48, 72, 96, 108 };

/* libertas add_ie_rates: the BSS's rate bytes (basic bit kept) in the
 * hardware's order. */
static unsigned common_rates(uint8_t *out, const uint8_t *rates, unsigned n)
{
    unsigned hw, ap, count = 0;
    for (hw = 0; hw < sizeof(hw_rates); hw++)
        for (ap = 0; ap < n; ap++)
            if ((rates[ap] & 0x7f) == hw_rates[hw])
                out[count++] = rates[ap];
    return count;
}

unsigned mv8688_assoc_body(uint8_t *body, const struct mv8688_bss *b)
{
    static const uint8_t fallback[] = { 0x82, 0x84, 0x8b, 0x96 };
    uint8_t *p = body;
    unsigned n;
    if (b->ssid_len < 1 || b->ssid_len > 32 || b->channel < 1 || b->channel > 14 ||
        b->rates_supp > b->rates_n || b->rates_n > MV_RATES_MAX)
        return 0;
    memcpy(p, b->bssid, 6);
    put16(p + 6, b->capability);
    put16(p + 8, MV_LISTEN_INTERVAL);
    put16(p + 10, 0);                   /* beacon period and DTIM: zero, */
    p[12] = 0;                          /* as libertas leaves them */
    p += 13;
    put16(p, MV_TLV_SSID);
    put16(p + 2, (unsigned)b->ssid_len);
    memcpy(p + 4, b->ssid, (unsigned)b->ssid_len);
    p += 4 + b->ssid_len;
    put16(p, MV_TLV_PHY_DS);
    put16(p + 2, 1);
    p[4] = (uint8_t)b->channel;
    p += 5;
    put16(p, MV_TLV_CF);
    put16(p + 2, 6);
    memset(p + 4, 0, 6);
    p += 10;
    put16(p, MV_TLV_RATES);
    if (b->rates_supp) {
        n = common_rates(p + 4, b->rates, b->rates_supp);
        n += common_rates(p + 4 + n, b->rates + b->rates_supp, b->rates_n - b->rates_supp);
    } else {
        /* No supported-rates element: libertas falls back to 802.11b. */
        memcpy(p + 4, fallback, sizeof(fallback));
        n = sizeof(fallback);
    }
    put16(p + 2, n);
    p += 4 + n;
    /* Firmware v9 and later; this one is 10.38. */
    put16(p, MV_TLV_AUTH_TYPE);
    put16(p + 2, 2);
    put16(p + 4, MV_AUTH_OPEN);
    p += 6;
    return (unsigned)(p - body);
}

unsigned mv8688_assoc_wpa_body(uint8_t *body, const struct mv8688_bss *b,
                               const uint8_t *wpa_ie, unsigned wpa_ie_len)
{
    unsigned len;
    if (wpa_ie_len && mv8688_wpa_ie_check(wpa_ie, wpa_ie_len))
        return 0;
    if (!(len = mv8688_assoc_body(body, b)) || !wpa_ie_len)
        return len;
    put16(body + len, wpa_ie[0]);
    put16(body + len + 2, wpa_ie[1]);
    memcpy(body + len + 4, wpa_ie + 2, wpa_ie[1]);
    return len + 4 + wpa_ie[1];
}

int mv8688_parse_assoc(const struct mv8688_cmd *c, uint16_t *capability, uint16_t *status,
                       uint16_t *aid)
{
    if (!c->resp || c->resp_size < MV_ASSOC_RESP_SIZE || c->resp_len < MV_ASSOC_RESP_SIZE)
        return MV_ECMDRESP;
    *capability = (uint16_t)get16(c->resp + 8);
    *status = (uint16_t)get16(c->resp + 10);
    *aid = (uint16_t)get16(c->resp + 12);
    return 0;
}

void mv8688_deauth_body(uint8_t body[MV_DEAUTH_BODY_SIZE], const uint8_t bssid[6],
                        unsigned reason)
{
    memcpy(body, bssid, 6);
    put16(body + 6, reason);
}
