/*
 * Phase 5.2 station driver for the 88W8688: an io-net driver hosted by
 * devnp-shim inside the running io-pkt-v4-hc, giving an Ethernet interface
 * (en<lan>) on an open network, and from phase 6 on a WPA network whose
 * handshakes a host supplicant runs over this interface.
 *
 *   mount -T io-pkt -o lan=6,mac=<uap0 MAC>,helper=<file>,firmware=<file> \
 *       /fs/tmpfs/devn-mv8688sta.so
 *
 * How devnp-shim drives an io-net driver was read from the saved HN+R
 * devnp-shim.so (SHA-256 00af614e...cfbb5d8b) and devn-asix.so
 * (085f2658...a3df241a); see docs/wifi-client/wifi-client-mode.md. What
 * this driver relies on:
 *  - init runs on the stack thread with a throwaway dispatch handle; reg
 *    needs top type "en" and _REG_PRODUCER_UP, and calls back into the
 *    devctl for DCMD_IO_NET_GET_CONFIG to take the MAC, so the MAC is
 *    known before reg;
 *  - the stack's frames arrive one npkt per call of rx_down, from the
 *    stack thread; tx_done may be called from any thread;
 *  - frames go up as one contiguous iov of at most 2,048 bytes, and come
 *    back to tx_done once copied (shimrxcopy, the default);
 *  - ifconfig <if> destroy calls shutdown1 then shutdown2, then unloads the
 *    object: no thread of this driver may outlive shutdown2;
 *  - every devctl on /dev/io-net/en<lan> reaches the devctl here with the
 *    caller's credentials (mv8688sta-ctl.h).
 *
 * One thread owns the card: it brings it up as the 4.1-5.1 probe did, then
 * polls the card's interrupt status 1 ms apart (as the probe did; the FPGA
 * card-interrupt path is untested), sends queued frames, and runs the
 * control requests. Nothing is joined until a CONNECT or JOIN request.
 * The reset line (system register 8) is never written.
 */
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/neutrino.h>
#include <sys/slog.h>
#include <sys/slogcodes.h>
#include <sys/stat.h>
#include <sys/io-net.h>
#include <sys/dcmd_io-net.h>
#include <hw/nicinfo.h>
#include <hw/pci.h>
#include <net/if.h>
#include <net/if_dl.h>
#include <net/if_types.h>
#include "hbsdc.h"
#include "sdio.h"
#include "mv8688fw.h"
#include "mv8688cmd.h"
#include "mv8688sta.h"
#include "mv8688sta-ctl.h"

#define NAME            "devn-mv8688sta"
#define FPGA_VENDOR     0x10ee
#define FPGA_DEVICE     0x9411
#define MODULE_OFFSET   0x4a0000
#define MODULE_SIZE     0x10000
#define SYSREG_SIZE     0x800
#define SYSREG_WLAN     0x08    /* bit 2: WlanEnable, set holds the card off */
#define MV_VENDOR       0x02df
#define MV_DEVICE       0x9103
#define MV_CHIP_ID      0x3130
#define BUS_HZ          25000000
#define FW_READY_TRIES  100     /* factory: 100 reads, 10 ms apart */
#define IMAGE_MAX       0x100000
#define TXQ_MAX         32
#define POLL_MS         1
/* A card that fails this many transfers in a row is given up: the thread
 * stops touching it and the interface reports FAILED until destroyed. */
#define FAIL_LIMIT      20

struct image {
    uint8_t *data;
    uint32_t size, sum;
};

/* The control limits are the station core's. */
typedef char ctl_ie_max_matches[MV8688STA_CTL_IE_MAX == MV_BSS_IE_MAX ? 1 : -1];
typedef char ctl_wpa_ie_max_matches[MV8688STA_CTL_WPA_IE_MAX == MV_WPA_IE_MAX ? 1 : -1];
typedef char ctl_bss_max_matches[MV8688STA_CTL_BSS_MAX == MV_STA_BSS_MAX ? 1 : -1];

/* A queued request's arguments, copied from the devctl. */
union request_args {
    struct mv8688sta_ctl_connect connect;
    struct mv8688sta_ctl_scan scan;
    struct mv8688sta_ctl_join join;
    struct mv8688sta_ctl_key key;
};

struct dev {
    io_net_self_t *ion;
    void *dll_hdl;
    int reg_hdl;
    uint16_t cell, endpoint;
    nic_config_t cfg;
    nic_stats_t stats;
    uint8_t mac[6];
    struct image helper, firmware;
    /* Hardware, mapped by init and released after the thread is gone. */
    int pci_handle;
    void *pci_dev;
    volatile uint8_t *module, *sysregs;
    int irq_id;
    unsigned irq_vector;
    /* Owned by the thread. */
    struct hbsdc host;
    struct sdio_card card;
    struct mv8688_dl dl;
    struct mv8688_sta sta;
    uint8_t tx_frame[MV_ETH_MAX];
    unsigned fail_run;
    /* Shared, under lk. */
    pthread_mutex_t lk;
    pthread_cond_t cv;
    pthread_t thread;
    int thread_started, bringup_done, bringup_rc;
    int registered, stopping, stop;
    npkt_t *txq_head, *txq_tail;
    unsigned txq_n;
    int pending;                /* 0, or the request queued (enum request) */
    int running;                /* a request the thread has taken and not finished;
                                 * none is queued meanwhile, so it is number requested */
    unsigned req_frames;        /* frames queued before it, still to send */
    union request_args req;
    struct mv8688sta_ctl_status status;
    /* The last finished scan's results, for SCAN_RESULT. */
    unsigned results_n;
    uint32_t results_scan;
    struct mv8688_bss results[MV_STA_BSS_MAX];
};

enum request { REQ_NONE, REQ_CONNECT, REQ_DISCONNECT, REQ_SCAN, REQ_JOIN, REQ_KEY };

static struct dev *the_dev;

static void say(const char *fmt, ...)
{
    va_list ap;
    char line[240];
    va_start(ap, fmt);
    vsnprintf(line, sizeof(line), fmt, ap);
    va_end(ap);
    slogf(_SLOGC_NETWORK, _SLOG_INFO, NAME ": %s", line);
}

static void sta_log(void *ctx, const char *line)
{
    (void)ctx;
    say("%s", line);
}

/* --- register access for the host layer --------------------------------- */

static uint32_t q_rd32(void *ctx, unsigned off)
{
    return *(volatile uint32_t *)(((struct dev *)ctx)->module + off);
}

static void q_wr32(void *ctx, unsigned off, uint32_t value)
{
    *(volatile uint32_t *)(((struct dev *)ctx)->module + off) = value;
}

static uint8_t q_rd8(void *ctx, unsigned off)
{
    return ((struct dev *)ctx)->module[off];
}

static void q_wr8(void *ctx, unsigned off, uint8_t value)
{
    ((struct dev *)ctx)->module[off] = value;
}

/* Only the thread that attached the interrupt may wait for it. */
static int q_wait_irq(void *ctx, unsigned timeout_ms)
{
    uint64_t ns = (uint64_t)timeout_ms * 1000000;
    (void)ctx;
    TimerTimeout(CLOCK_REALTIME, _NTO_TIMEOUT_INTR, NULL, &ns, NULL);
    return InterruptWait(0, NULL) == -1 ? -1 : 0;
}

static void q_irq_done(void *ctx)
{
    struct dev *d = ctx;
    if (d->irq_id != -1)
        InterruptUnmask(d->irq_vector, d->irq_id);
}

static void q_delay_ms(void *ctx, unsigned ms)
{
    (void)ctx;
    delay(ms);
}

static void q_spin_us(void *ctx, unsigned us)
{
    (void)ctx;
    nanospin_ns(us * 1000);
}

static unsigned now_ms(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) == -1)
        return 0;
    return (unsigned)ts.tv_sec * 1000 + (unsigned)(ts.tv_nsec / 1000000);
}

static unsigned q_now_ms(void *ctx)
{
    (void)ctx;
    return now_ms();
}

static const struct hbsdc_io qnx_io = {
    q_rd32, q_wr32, q_rd8, q_wr8, q_wait_irq, q_irq_done, q_delay_ms, q_spin_us, q_now_ms,
    NULL
};

/* --- status, under lk ---------------------------------------------------- */

static void copy_step(struct dev *d, const char *step)
{
    strncpy(d->status.last_step, step ? step : "", sizeof(d->status.last_step) - 1);
    d->status.last_step[sizeof(d->status.last_step) - 1] = 0;
}

/* Called by the thread only, which owns sta, with lk held. */
static void publish_locked(struct dev *d, int ctl_state)
{
    const struct mv8688_sta *s = &d->sta;
    const struct mv8688_cmd *c = &s->cmd;
    struct mv8688sta_ctl_status *st = &d->status;
    if (st->state != MV8688STA_CTL_FAILED)
        st->state = ctl_state;
    memcpy(st->bssid, s->bss.bssid, 6);
    st->channel = s->bss.channel;
    st->rssi = s->bss.rssi;
    st->ssid_len = s->bss.ssid_len > 0 ? (uint32_t)s->bss.ssid_len : 0;
    memcpy(st->ssid, s->bss.ssid, sizeof(st->ssid));
    st->assoc_status = s->assoc_status;
    st->aid = s->aid;
    st->fw_release = s->spec.fwrelease;
    st->connects = s->connects;
    st->link_losses = s->link_losses;
    st->link_cause = s->link_cause;
    st->events = c->events;
    st->stray_responses = c->stray_responses;
    st->aborts = c->aborts;
    st->rsn = s->rsn;
    st->scans_done = s->scans_done;
    st->results = s->result_n;
    st->key_sets = s->key_sets;
    st->mic_ucast = c->mic_ucast;
    st->mic_mcast = c->mic_mcast;
    if (st->state == MV8688STA_CTL_ASSOCIATED)
        d->cfg.flags &= ~NIC_FLAG_LINK_DOWN;
    else
        d->cfg.flags |= NIC_FLAG_LINK_DOWN;
}

static void publish(struct dev *d, int ctl_state)
{
    pthread_mutex_lock(&d->lk);
    publish_locked(d, ctl_state);
    pthread_mutex_unlock(&d->lk);
}

static int sta_ctl_state(const struct mv8688_sta *s)
{
    return s->state == MV_STA_ASSOCIATED ? MV8688STA_CTL_ASSOCIATED
         : s->state == MV_STA_IDLE ? MV8688STA_CTL_IDLE : MV8688STA_CTL_DOWN;
}

/* A transport failure counts toward FAIL_LIMIT; a success resets the run. */
static void note_transfer(struct dev *d, int rc)
{
    if (rc == 0 || rc == MV_STA_ESTATE) {
        d->fail_run = 0;
        return;
    }
    pthread_mutex_lock(&d->lk);
    d->status.transport_errors++;
    d->status.last_rc = rc;
    copy_step(d, d->sta.step);
    if (++d->fail_run == FAIL_LIMIT) {
        d->status.state = MV8688STA_CTL_FAILED;
        d->cfg.flags |= NIC_FLAG_LINK_DOWN;
        say("card FAILED: %u transfers in a row failed, last rc=%d at %s; "
            "destroy the interface", FAIL_LIMIT, rc, d->sta.step ? d->sta.step : "?");
    }
    pthread_mutex_unlock(&d->lk);
}

/* --- frames up -------------------------------------------------------------- */

/* Called from the card thread for each frame that decoded. */
static void on_frame(void *ctx, const struct mv8688_rx *rx, const uint8_t *frame,
                     unsigned len)
{
    struct dev *d = ctx;
    io_net_self_t *ion = d->ion;
    npkt_t *npkt;
    net_buf_t *nb;
    net_iov_t *iov;
    uint8_t *buf;
    void *mem;
    int up;

    (void)rx;
    pthread_mutex_lock(&d->lk);
    up = d->registered && !d->stopping && len >= MV_ETH_MIN && len <= MV_ETH_MAX;
    if (!up)
        d->status.rx_drops++;
    else
        d->status.rx_up_pending++;      /* before tx_up: tx_done may run first */
    pthread_mutex_unlock(&d->lk);
    if (!up)
        return;
    npkt = ion->alloc_up_npkt(sizeof(*nb) + sizeof(*iov) + len, &mem);
    if (!npkt) {
        pthread_mutex_lock(&d->lk);
        d->status.rx_up_pending--;
        d->status.rx_drops++;
        d->stats.rx_failed_allocs++;
        pthread_mutex_unlock(&d->lk);
        return;
    }
    nb = mem;
    iov = (net_iov_t *)(nb + 1);
    buf = (uint8_t *)(iov + 1);
    memcpy(buf, frame, len);
    nb->niov = 1;
    nb->net_iov = iov;
    iov->iov_base = buf;
    iov->iov_phys = ion->mphys(buf);
    iov->iov_len = len;
    TAILQ_INSERT_HEAD(&npkt->buffers, nb, ptrs);
    npkt->framelen = len;
    npkt->tot_iov = 1;
    npkt->next = NULL;
    pthread_mutex_lock(&d->lk);
    d->status.rx_frames++;
    d->status.rx_bytes += len;
    d->stats.rxed_ok++;
    d->stats.octets_rxed_ok += len;
    pthread_mutex_unlock(&d->lk);
    /* The packets returned are those whose done record could not be set. */
    if (ion->tx_up_start(d->reg_hdl, npkt, 0, 0, d->cell, d->endpoint, 0, d)) {
        ion->free(npkt);
        pthread_mutex_lock(&d->lk);
        d->status.rx_up_pending--;
        d->status.rx_drops++;
        pthread_mutex_unlock(&d->lk);
    }
}

/* devnp-shim attaches the interface with no flags but its own; it ORs in the
 * iflags of a DL_ADVERT message sent up after registration (and clears
 * IFF_RUNNING itself), and reads nothing else from it but the two capability
 * words. Without IFF_BROADCAST the stack refuses IP sends to 255.255.255.255,
 * which is all dhcp.client sends (5.3 run). Laid out as the factory devn-asix
 * ax_advertise does; no IFF_MULTICAST, as MAC_CONTROL passes no multicast. */
static int advertise(struct dev *d)
{
    io_net_self_t *ion = d->ion;
    io_net_msg_dl_advert_t *ad;
    npkt_t *npkt;
    net_buf_t *nb;
    net_iov_t *iov;
    char name[8];
    void *mem;
    unsigned nlen;

    npkt = ion->alloc_up_npkt(sizeof(*nb) + sizeof(*iov) + sizeof(*ad), &mem);
    if (!npkt)
        return -1;
    nb = mem;
    iov = (net_iov_t *)(nb + 1);
    ad = (io_net_msg_dl_advert_t *)(iov + 1);
    memset(ad, 0, sizeof(*ad));
    ad->type = _IO_NET_MSG_DL_ADVERT;
    ad->iflags = IFF_BROADCAST | IFF_SIMPLEX | IFF_RUNNING;
    ad->mtu_max = ad->mtu_preferred = d->cfg.mtu;
    snprintf(name, sizeof(name), "en%d", (int)d->cfg.lan);
    nlen = strlen(name);
    strcpy(ad->up_type, name);
    ad->dl.sdl_len = sizeof(ad->dl);
    ad->dl.sdl_family = AF_LINK;
    ad->dl.sdl_index = d->cfg.lan;
    ad->dl.sdl_type = IFT_ETHER;
    ad->dl.sdl_nlen = nlen;
    ad->dl.sdl_alen = 6;
    memcpy(ad->dl.sdl_data, name, nlen);
    memcpy(ad->dl.sdl_data + nlen, d->mac, 6);
    nb->niov = 1;
    nb->net_iov = iov;
    iov->iov_base = ad;
    iov->iov_phys = ion->mphys(ad);
    iov->iov_len = sizeof(*ad);
    TAILQ_INSERT_HEAD(&npkt->buffers, nb, ptrs);
    npkt->framelen = sizeof(*ad);
    npkt->tot_iov = 1;
    npkt->next = NULL;
    npkt->flags |= _NPKT_MSG;
    pthread_mutex_lock(&d->lk);
    d->status.rx_up_pending++;          /* mv_tx_done takes it back */
    pthread_mutex_unlock(&d->lk);
    if (ion->tx_up_start(d->reg_hdl, npkt, 0, 0, d->cell, d->endpoint, 0, d)) {
        ion->free(npkt);
        pthread_mutex_lock(&d->lk);
        d->status.rx_up_pending--;
        pthread_mutex_unlock(&d->lk);
        return -1;
    }
    return 0;
}

/* Our up packet has been copied or dropped by the stack. */
static int mv_tx_done(npkt_t *npkt, void *done_hdl, void *func_hdl)
{
    struct dev *d = done_hdl;
    (void)func_hdl;
    d->ion->free(npkt);
    pthread_mutex_lock(&d->lk);
    d->status.rx_up_pending--;
    pthread_mutex_unlock(&d->lk);
    return 0;
}

/* --- frames down ------------------------------------------------------------ */

static void give_back(struct dev *d, npkt_t *npkt, int sent)
{
    if (!sent)
        npkt->flags |= _NPKT_NOT_TXED;
    d->ion->tx_done(d->reg_hdl, npkt);
}

/* From the stack thread: queue for the card thread, or drop at once. */
static int mv_rx_down(npkt_t *npkt, void *func_hdl)
{
    struct dev *d = the_dev;
    int take, full;
    (void)func_hdl;
    if (!d)
        return TX_DOWN_FAILED;
    if (npkt->flags & _NPKT_MSG) {
        give_back(d, npkt, 1);
        return TX_DOWN_OK;
    }
    pthread_mutex_lock(&d->lk);
    full = d->txq_n >= TXQ_MAX;
    take = !d->stopping && d->status.state == MV8688STA_CTL_ASSOCIATED && !full;
    if (take) {
        npkt->next = NULL;
        if (d->txq_tail)
            d->txq_tail->next = npkt;
        else
            d->txq_head = npkt;
        d->txq_tail = npkt;
        d->txq_n++;
        pthread_cond_signal(&d->cv);
    } else {
        d->status.tx_drops++;
    }
    pthread_mutex_unlock(&d->lk);
    if (take)
        return TX_DOWN_OK;
    give_back(d, npkt, 0);
    errno = full ? ENOBUFS : ENETDOWN;
    return TX_DOWN_FAILED;
}

/* Gathers the frame into one buffer; 0 when it does not fit an Ethernet
 * frame. */
static unsigned gather(struct dev *d, const npkt_t *npkt)
{
    const net_buf_t *nb;
    unsigned len = 0;
    int i;
    for (nb = TAILQ_FIRST(&npkt->buffers); nb; nb = TAILQ_NEXT(nb, ptrs)) {
        for (i = 0; i < nb->niov; i++) {
            if (nb->net_iov[i].iov_len > sizeof(d->tx_frame) - len)
                return 0;
            memcpy(d->tx_frame + len, nb->net_iov[i].iov_base, nb->net_iov[i].iov_len);
            len += nb->net_iov[i].iov_len;
        }
    }
    return len >= MV_ETH_MIN && len == npkt->framelen ? len : 0;
}

static void send_one(struct dev *d, npkt_t *npkt)
{
    unsigned len = gather(d, npkt);
    int rc = len ? mv8688_sta_send(&d->sta, d->tx_frame, len) : HBSDC_EINVAL;
    if (rc != HBSDC_EINVAL)
        note_transfer(d, rc);
    pthread_mutex_lock(&d->lk);
    if (!rc) {
        d->status.tx_frames++;
        d->status.tx_bytes += len;
        d->stats.txed_ok++;
        d->stats.octets_txed_ok += len;
    } else {
        d->status.tx_errors++;
    }
    pthread_mutex_unlock(&d->lk);
    give_back(d, npkt, !rc);
}

/* --- the card thread --------------------------------------------------------- */

static int bring_up(struct dev *d)
{
    struct sigevent event;
    struct mv8688_dl *dl = &d->dl;
    uint8_t b[2];
    uint16_t fw = 0;
    uint32_t port;
    unsigned t0;
    int rc;

    if (ThreadCtl(_NTO_TCTL_IO, 0) == -1) {
        say("ThreadCtl(_NTO_TCTL_IO) failed errno=%d", errno);
        return -1;
    }
    memset(&d->host, 0, sizeof(d->host));
    d->host.io = &qnx_io;
    d->host.ctx = d;
    if (hbsdc_identify(&d->host) || d->host.irq_index == 0xff) {
        say("module index not as expected (major=%u caps=0x%08x irq_index=0x%02x)",
            d->host.major, (unsigned)d->host.caps, d->host.irq_index);
        return -1;
    }
    hbsdc_quiesce(&d->host);
    d->irq_vector = d->host.irq;
    SIGEV_INTR_INIT(&event);
    d->irq_id = InterruptAttachEvent(d->irq_vector, &event, _NTO_INTR_FLAGS_TRK_MSK);
    if (d->irq_id == -1) {
        say("InterruptAttachEvent(%u) failed errno=%d", d->irq_vector, errno);
        return -1;
    }
    memset(&d->card, 0, sizeof(d->card));
    d->card.host = &d->host;
    if ((rc = sdio_attach(&d->card, MV_VENDOR, MV_DEVICE))) {
        say("card attach FAILED at %s rc=%d r5=0x%08x", d->card.step ? d->card.step : "?", rc,
            (unsigned)d->card.r5);
        return -1;
    }
    if ((rc = hbsdc_set_clock(&d->host, BUS_HZ)) || (rc = sdio_set_blksz(&d->card, 1, MV_BLKSZ))) {
        say("clock or block size FAILED rc=%d", rc);
        return -1;
    }
    if ((rc = sdio_cmd52(&d->card, 1, MV_REG_CHIP_ID, 0, 0, &b[0])) ||
        (rc = sdio_cmd52(&d->card, 1, MV_REG_CHIP_ID + 1, 0, 0, &b[1])) ||
        ((unsigned)b[0] << 8 | b[1]) != MV_CHIP_ID) {
        say("chip id check FAILED rc=%d id=0x%02x%02x", rc, b[0], b[1]);
        return -1;
    }
    if ((rc = mv8688_ioport(&d->card, &port)) || (rc = mv8688_fw_status(&d->card, &fw))) {
        say("I/O port or firmware status read FAILED rc=%d", rc);
        return -1;
    }
    if (fw == MV_FW_READY) {
        say("refused: firmware already running (ready magic before the helper)");
        return -1;
    }
    memset(dl, 0, sizeof(*dl));
    dl->card = &d->card;
    dl->ioport = port;
    t0 = now_ms();
    if ((rc = mv8688_prog_helper(dl, d->helper.data, d->helper.size)) ||
        (rc = mv8688_prog_fw(dl, d->firmware.data, d->firmware.size)) ||
        (rc = mv8688_wait_fw(dl, FW_READY_TRIES))) {
        say("download FAILED rc=%d at %s sent=%u requests=%u", rc, dl->step ? dl->step : "?",
            (unsigned)dl->sent, dl->requests);
        return -1;
    }
    say("download: %u ms, ioport=0x%05x rx_unit=%u requests=%u crc_retries=%u", now_ms() - t0,
        (unsigned)port, dl->rx_unit, dl->requests, dl->crc_retries);
    memset(&d->sta, 0, sizeof(d->sta));
    d->sta.cmd.card = &d->card;
    d->sta.cmd.ioport = port;
    d->sta.cmd.rx_unit = dl->rx_unit;
    d->sta.cmd.on_frame = on_frame;
    d->sta.cmd.frame_ctx = d;
    d->sta.log = sta_log;
    if ((rc = mv8688_sta_start(&d->sta, d->mac))) {
        say("station start FAILED rc=%d at %s", rc, d->sta.step ? d->sta.step : "?");
        mv8688_sta_stop(&d->sta);
        return -1;
    }
    return 0;
}

static void power_down(struct dev *d)
{
    hbsdc_shutdown(&d->host);
    if (d->irq_id != -1) {
        InterruptDetach(d->irq_id);
        d->irq_id = -1;
    }
}

static const char *const request_names[] = {
    "none", "connect", "disconnect", "scan", "join", "key"
};

/* The request's own refusals and the firmware's are not transport
 * failures; anything else counts toward FAIL_LIMIT. */
static int refusal(int rc)
{
    return rc == MV_STA_ENOTFOUND || rc == MV_STA_ENOTOPEN || rc == MV_STA_EASSOC ||
           rc == MV_STA_EINVAL || rc == MV_STA_ENOTSCANNED || rc == MV_ECMDRESULT;
}

static void run_request(struct dev *d, int request, const void *args)
{
    const struct mv8688sta_ctl_connect *conn = args;
    const struct mv8688sta_ctl_scan *scan = args;
    const struct mv8688sta_ctl_join *join = args;
    const struct mv8688sta_ctl_key *key = args;
    struct mv8688_sta *s = &d->sta;
    int rc;

    switch (request) {
    case REQ_CONNECT:
    case REQ_JOIN:
        publish(d, MV8688STA_CTL_CONNECTING);
        if (s->state == MV_STA_ASSOCIATED)
            mv8688_sta_disconnect(s);
        if (request == REQ_CONNECT)
            rc = mv8688_sta_connect(s, conn->ssid, conn->ssid_len);
        else
            rc = mv8688_sta_join(s, join->bssid, join->ssid, join->ssid_len, join->ie,
                                 join->ie_len);
        break;
    case REQ_SCAN:
        publish(d, MV8688STA_CTL_CONNECTING);
        rc = mv8688_sta_scan(s, scan->ssid, scan->ssid_len);
        pthread_mutex_lock(&d->lk);
        d->results_n = s->result_n;
        d->results_scan = s->scans_done;
        memcpy(d->results, s->result, s->result_n * sizeof(s->result[0]));
        pthread_mutex_unlock(&d->lk);
        break;
    case REQ_KEY:
        rc = mv8688_sta_set_key(s, key->cipher == MV8688STA_CTL_KEY_TKIP ? MV_KEY_TYPE_TKIP
                                   : MV_KEY_TYPE_AES, key->pairwise != 0, key->key,
                                key->cipher == MV8688STA_CTL_KEY_CLEAR ? 0 : key->key_len);
        break;
    default:
        rc = mv8688_sta_disconnect(s);
    }
    say("%s: rc=%d%s%s", request_names[request], rc, rc ? " at " : "",
        rc ? (s->step ? s->step : "?") : "");
    if (!refusal(rc))
        note_transfer(d, rc);
    /* The result, the state it left and the completion together: a
     * reader that sees done also sees what the request did. */
    pthread_mutex_lock(&d->lk);
    d->status.last_rc = rc;
    copy_step(d, rc ? s->step : NULL);
    publish_locked(d, sta_ctl_state(s));
    d->status.done = d->status.requested;
    d->running = 0;
    pthread_mutex_unlock(&d->lk);
}

static void *card_thread(void *arg)
{
    struct dev *d = arg;
    struct timespec until;
    union request_args args;
    unsigned packets, events;
    npkt_t *npkt;
    int request, rc, failed, before;

    rc = bring_up(d);
    if (rc)
        power_down(d);
    else
        publish(d, MV8688STA_CTL_IDLE);
    pthread_mutex_lock(&d->lk);
    d->bringup_done = 1;
    d->bringup_rc = rc;
    pthread_cond_broadcast(&d->cv);
    pthread_mutex_unlock(&d->lk);
    if (rc)
        return NULL;

    pthread_mutex_lock(&d->lk);
    while (!d->stop) {
        failed = d->status.state == MV8688STA_CTL_FAILED;
        request = d->pending;
        /* Frames queued before the request go first; a pairwise key must
         * not encrypt the EAPOL frame that preceded it. */
        if (request && d->req_frames && d->txq_head && !failed) {
            d->req_frames--;
            request = REQ_NONE;
        } else if (request) {
            d->pending = REQ_NONE;
            d->running = 1;
            d->req_frames = 0;
            args = d->req;
        }
        npkt = request || failed ? NULL : d->txq_head;
        if (npkt) {
            d->txq_head = npkt->next;
            if (!d->txq_head)
                d->txq_tail = NULL;
            d->txq_n--;
        }
        pthread_mutex_unlock(&d->lk);

        if (failed) {
            /* Leave the card alone; the requests still complete. */
            if (request) {
                pthread_mutex_lock(&d->lk);
                d->status.last_rc = MV_STA_ESTATE;
                copy_step(d, "card_failed");
                d->status.done = d->status.requested;
                d->running = 0;
            } else {
                clock_gettime(CLOCK_MONOTONIC, &until);
                until.tv_sec += 1;
                pthread_mutex_lock(&d->lk);
                if (!d->stop && !d->pending)
                    pthread_cond_timedwait(&d->cv, &d->lk, &until);
            }
            continue;
        }
        if (request) {
            run_request(d, request, &args);
        } else if (npkt) {
            send_one(d, npkt);
            publish(d, sta_ctl_state(&d->sta));
        } else {
            packets = d->sta.cmd.packets;
            events = d->sta.cmd.events;
            before = d->sta.state;
            rc = mv8688_sta_poll(&d->sta, POLL_MS);
            note_transfer(d, rc);
            /* An event can change counters without the state (a TKIP MIC
             * failure); the supplicant reads them from the status. */
            if (d->sta.state != before || rc || d->sta.cmd.events != events)
                publish(d, sta_ctl_state(&d->sta));
            pthread_mutex_lock(&d->lk);
            d->status.polls++;
            /* Nothing came: sleep a tick unless a frame or request arrives. */
            if (d->sta.cmd.packets == packets && !d->stop && !d->pending && !d->txq_head) {
                clock_gettime(CLOCK_MONOTONIC, &until);
                until.tv_nsec += POLL_MS * 1000000L;
                if (until.tv_nsec >= 1000000000L) {
                    until.tv_sec++;
                    until.tv_nsec -= 1000000000L;
                }
                pthread_cond_timedwait(&d->cv, &d->lk, &until);
            }
            continue;
        }
        pthread_mutex_lock(&d->lk);
    }
    pthread_mutex_unlock(&d->lk);

    if (d->status.state != MV8688STA_CTL_FAILED) {
        rc = mv8688_sta_stop(&d->sta);
        say("stop: rc=%d%s%s", rc, rc ? " at " : "", rc ? (d->sta.step ? d->sta.step : "?") : "");
    }
    power_down(d);
    publish(d, MV8688STA_CTL_DOWN);
    say("card powered down");
    return NULL;
}

/* --- io-net entry points ------------------------------------------------------- */

static int mv_shutdown1(int registrant_hdl, void *func_hdl)
{
    struct dev *d = the_dev;
    (void)registrant_hdl;
    (void)func_hdl;
    if (d) {
        pthread_mutex_lock(&d->lk);
        d->stopping = 1;
        pthread_mutex_unlock(&d->lk);
    }
    return 0;
}

static void stop_thread(struct dev *d)
{
    if (!d->thread_started)
        return;
    pthread_mutex_lock(&d->lk);
    d->stopping = d->stop = 1;
    pthread_cond_broadcast(&d->cv);
    pthread_mutex_unlock(&d->lk);
    pthread_join(d->thread, NULL);
    d->thread_started = 0;
}

static void release(struct dev *d)
{
    if (d->module && d->module != MAP_FAILED)
        munmap_device_memory((void *)d->module, MODULE_SIZE);
    if (d->sysregs && d->sysregs != MAP_FAILED)
        munmap_device_memory((void *)d->sysregs, SYSREG_SIZE);
    if (d->pci_dev)
        pci_detach_device(d->pci_dev);
    if (d->pci_handle != -1)
        pci_detach(d->pci_handle);
    free(d->helper.data);
    free(d->firmware.data);
    d->helper.data = d->firmware.data = NULL;
}

static int mv_shutdown2(int registrant_hdl, void *func_hdl)
{
    struct dev *d = the_dev;
    npkt_t *npkt;
    unsigned pending;
    (void)registrant_hdl;
    (void)func_hdl;
    if (!d)
        return 0;
    stop_thread(d);
    pthread_mutex_lock(&d->lk);
    npkt = d->txq_head;
    d->txq_head = d->txq_tail = NULL;
    d->txq_n = 0;
    pending = d->status.rx_up_pending;
    pthread_mutex_unlock(&d->lk);
    while (npkt) {
        npkt_t *next = npkt->next;
        give_back(d, npkt, 0);
        npkt = next;
    }
    release(d);
    the_dev = NULL;
    /* An up packet still queued would come back to this object after it is
     * unloaded; keep the state it would touch rather than free it. */
    if (pending)
        say("shutdown: %u frames still with the stack; state kept", pending);
    else
        free(d);
    say("shutdown complete");
    return 0;
}

static int mv_advert(int registrant_hdl, void *func_hdl)
{
    (void)registrant_hdl;
    (void)func_hdl;
    return 0;
}

static int mv_flush(int registrant_hdl, void *func_hdl)
{
    (void)registrant_hdl;
    (void)func_hdl;
    return 0;
}

/* args (size bytes, at most the union) is copied for the thread. */
static int queue_request(struct dev *d, int request, const void *args, size_t size,
                         uint32_t *number)
{
    int rc = EOK;
    pthread_mutex_lock(&d->lk);
    if (d->stopping || d->pending || d->running)
        rc = EBUSY;
    else if (d->status.state == MV8688STA_CTL_FAILED || d->status.state == MV8688STA_CTL_DOWN)
        rc = ENETDOWN;
    else {
        d->pending = request;
        d->req_frames = d->txq_n;
        memset(&d->req, 0, sizeof(d->req));
        if (args)
            memcpy(&d->req, args, size);
        *number = ++d->status.requested;
        pthread_cond_signal(&d->cv);
    }
    pthread_mutex_unlock(&d->lk);
    return rc;
}

static int read_result(struct dev *d, struct mv8688sta_ctl_bss *out)
{
    const struct mv8688_bss *b;
    uint32_t index = out->index;
    int rc = EOK;
    pthread_mutex_lock(&d->lk);
    memset(out, 0, sizeof(*out));
    out->index = index;
    out->scan = d->results_scan;
    out->count = d->results_n;
    if (index >= d->results_n) {
        rc = ENOENT;
    } else {
        b = &d->results[index];
        memcpy(out->bssid, b->bssid, 6);
        out->capability = b->capability;
        out->interval = b->interval;
        out->rssi = b->rssi;
        out->ie_cut = b->ie_cut != 0;
        out->channel = b->channel;
        out->ie_len = b->ie_len;
        memcpy(out->ie, b->ie, b->ie_len);
    }
    pthread_mutex_unlock(&d->lk);
    return rc;
}

static int mv_devctl(void *func_hdl, int dcmd, void *data, size_t size,
                     union _io_net_dcmd_ret_cred *ret)
{
    struct dev *d = func_hdl;
    struct mv8688sta_ctl_connect *conn = data;
    struct mv8688sta_ctl_request *req = data;
    struct mv8688sta_ctl_scan *scan = data;
    struct mv8688sta_ctl_join *join = data;
    struct mv8688sta_ctl_key *key = data;
    int root = !ret || !ret->ret_cred.cred || ret->ret_cred.cred->euid == 0;

    switch (dcmd) {
    case DCMD_IO_NET_GET_CONFIG:
        if (size < sizeof(nic_config_t))
            return EINVAL;
        pthread_mutex_lock(&d->lk);
        memcpy(data, &d->cfg, sizeof(nic_config_t));
        pthread_mutex_unlock(&d->lk);
        return EOK;
    case DCMD_IO_NET_GET_STATS:
        if (size < sizeof(nic_stats_t))
            return EINVAL;
        pthread_mutex_lock(&d->lk);
        memcpy(data, &d->stats, sizeof(nic_stats_t));
        pthread_mutex_unlock(&d->lk);
        return EOK;
    case DCMD_IO_NET_TX_FLUSH:
        return EOK;
    case DCMD_MV8688STA_STATUS:
        if (size < sizeof(struct mv8688sta_ctl_status))
            return EINVAL;
        pthread_mutex_lock(&d->lk);
        memcpy(data, &d->status, sizeof(d->status));
        pthread_mutex_unlock(&d->lk);
        return EOK;
    case DCMD_MV8688STA_CONNECT:
        if (!root)
            return EPERM;
        if (size < sizeof(*conn) || conn->ssid_len < 1 || conn->ssid_len > 32)
            return EINVAL;
        return queue_request(d, REQ_CONNECT, conn, sizeof(*conn), &conn->request);
    case DCMD_MV8688STA_DISCONNECT:
        if (!root)
            return EPERM;
        if (size < sizeof(*req))
            return EINVAL;
        return queue_request(d, REQ_DISCONNECT, NULL, 0, &req->request);
    case DCMD_MV8688STA_SCAN:
        if (!root)
            return EPERM;
        if (size < sizeof(*scan) || scan->ssid_len > 32)
            return EINVAL;
        return queue_request(d, REQ_SCAN, scan, sizeof(*scan), &scan->request);
    case DCMD_MV8688STA_SCAN_RESULT:
        if (size < sizeof(struct mv8688sta_ctl_bss))
            return EINVAL;
        return read_result(d, data);
    case DCMD_MV8688STA_JOIN:
        if (!root)
            return EPERM;
        if (size < sizeof(*join) || join->ssid_len < 1 || join->ssid_len > 32 ||
            join->ie_len > MV8688STA_CTL_WPA_IE_MAX ||
            (join->ie_len && mv8688_wpa_ie_check(join->ie, join->ie_len)))
            return EINVAL;
        return queue_request(d, REQ_JOIN, join, sizeof(*join), &join->request);
    case DCMD_MV8688STA_SET_KEY:
        if (!root)
            return EPERM;
        if (size < sizeof(*key) || key->cipher > MV8688STA_CTL_KEY_CCMP ||
            key->key_len > sizeof(key->key) ||
            (key->cipher == MV8688STA_CTL_KEY_CLEAR) != (key->key_len == 0))
            return EINVAL;
        return queue_request(d, REQ_KEY, key, sizeof(*key), &key->request);
    }
    /* Promiscuous and multicast filters are not offered (MAC_CONTROL has
     * RX and TX only), nor anything else. */
    return ENOTSUP;
}

static io_net_registrant_funcs_t mv_funcs = {
    8, NULL, mv_rx_down, mv_tx_done, mv_shutdown1, mv_shutdown2, mv_advert, mv_devctl,
    mv_flush, NULL, NULL
};

static int load_image(struct image *img, const char *path, int helper)
{
    const struct mv8688_pinned *pin;
    struct stat st;
    ssize_t got = 0;
    uint32_t done = 0;
    int fd = open(path, O_RDONLY);
    if (fd == -1 || fstat(fd, &st) == -1 || !S_ISREG(st.st_mode) || st.st_size <= 0 ||
        st.st_size > IMAGE_MAX) {
        say("image %s: cannot open or not a regular file of 1..%u bytes", path, IMAGE_MAX);
        if (fd != -1)
            close(fd);
        return -1;
    }
    img->size = (uint32_t)st.st_size;
    if (!(img->data = malloc(img->size))) {
        close(fd);
        return -1;
    }
    while (done < img->size && (got = read(fd, img->data + done, img->size - done)) > 0)
        done += (uint32_t)got;
    close(fd);
    if (done != img->size) {
        say("image %s: read %u of %u bytes", path, (unsigned)done, (unsigned)img->size);
        return -1;
    }
    img->sum = mv8688_cksum(img->data, img->size);
    pin = mv8688_pinned_image(img->size, img->sum, helper);
    say("image %s size=%u cksum=%u (%s)", path, (unsigned)img->size, (unsigned)img->sum,
        pin ? pin->name : "NOT a pinned image");
    if (!pin || (!helper && !pin->station)) {
        say("refused: %s is not the pinned %s image", path, helper ? "helper" : "station");
        return -1;
    }
    return 0;
}

/* lan, mac, helper, firmware and verbose. Anything else is refused. */
static int parse_options(struct dev *d, char *options, const char **helper,
                         const char **firmware)
{
    static char *const names[] = { "lan", "mac", "helper", "firmware", "verbose", NULL };
    char *value;
    int have_mac = 0;
    while (options && *options) {
        switch (getsubopt(&options, names, &value)) {
        case 0:
            if (!value)
                return -1;
            d->cfg.lan = strtol(value, NULL, 0);
            break;
        case 1:
            if (!value || mv8688_parse_mac(value, d->mac))
                return -1;
            have_mac = 1;
            break;
        case 2:
            *helper = value;
            break;
        case 3:
            *firmware = value;
            break;
        case 4:
            d->cfg.verbose = value ? strtoul(value, NULL, 0) : 1;
            break;
        default:
            say("unknown option \"%s\"", value ? value : "");
            return -1;
        }
    }
    return have_mac && *helper && *firmware ? 0 : -1;
}

static void init_config(struct dev *d)
{
    nic_config_t *cfg = &d->cfg;
    cfg->revision = NIC_CONFIG_REVISION;
    cfg->flags = NIC_FLAG_WIRELESS | NIC_FLAG_BROADCAST | NIC_FLAG_LINK_DOWN;
    cfg->mtu = cfg->mru = MV_ETH_MAX;
    memcpy(cfg->permanent_address, d->mac, 6);
    memcpy(cfg->current_address, d->mac, 6);
    cfg->mac_length = 6;
    cfg->connector = NIC_CONNECTOR_UNKNOWN;
    cfg->phy_addr = -1;
    cfg->media = NIC_MEDIA_802_11;
    cfg->media_rate = -1;
    cfg->duplex = -1;
    cfg->vendor_id = MV_VENDOR;
    cfg->device_id = MV_DEVICE;
    cfg->num_irqs = 1;
    strcpy((char *)cfg->device_description, "Marvell 88W8688 station");
    strcpy((char *)cfg->uptype, "en");
    cfg->iftype = IFT_ETHER;
    d->stats.revision = NIC_STATS_REVISION;
    d->stats.media = NIC_MEDIA_802_11;
    d->status.version = MV8688STA_CTL_VERSION;
    memcpy(d->status.mac, d->mac, 6);
}

/* Maps the FPGA and refuses a module some driver still holds. */
static int map_hardware(struct dev *d)
{
    struct pci_dev_info info;
    uint64_t bar;
    uint32_t ctrl, power, wlan;

    if ((d->pci_handle = pci_attach(0)) == -1) {
        say("pci_attach failed errno=%d", errno);
        return -1;
    }
    memset(&info, 0, sizeof(info));
    info.VendorId = FPGA_VENDOR;
    info.DeviceId = FPGA_DEVICE;
    if (!(d->pci_dev = pci_attach_device(NULL, PCI_SHARE, 0, &info))) {
        say("pci_attach_device(%04x:%04x) failed errno=%d", FPGA_VENDOR, FPGA_DEVICE, errno);
        return -1;
    }
    if (info.BaseAddressSize[0] < MODULE_OFFSET + MODULE_SIZE) {
        say("BAR0 too small for the SDIO module");
        return -1;
    }
    bar = info.CpuBaseAddress[0] & ~(uint64_t)0xf;
    d->sysregs = mmap_device_memory(NULL, SYSREG_SIZE, PROT_READ | PROT_NOCACHE, MAP_SHARED, bar);
    d->module = mmap_device_memory(NULL, MODULE_SIZE, PROT_READ | PROT_WRITE | PROT_NOCACHE,
                                   MAP_SHARED, bar + MODULE_OFFSET);
    if (d->sysregs == MAP_FAILED || d->module == MAP_FAILED) {
        say("mmap_device_memory failed errno=%d", errno);
        return -1;
    }
    ctrl = q_rd32(d, HBSDC_CTRL);
    power = q_rd32(d, HBSDC_POWER);
    wlan = *(volatile uint32_t *)(d->sysregs + SYSREG_WLAN);
    say("guard: ctrl=0x%08x power=0x%08x sysreg8=0x%08x", (unsigned)ctrl, (unsigned)power,
        (unsigned)wlan);
    if ((ctrl & (HBSDC_CTRL_ENABLE | HBSDC_CTRL_CLK_EN | HBSDC_CTRL_SDIO_EN)) ||
        (power & HBSDC_POWER_ON)) {
        say("refused: the SDIO module is in use (detach uap0 first)");
        return -1;
    }
    if (wlan & 4) {
        say("refused: WlanEnable is set; this driver never drives the reset line");
        return -1;
    }
    return 0;
}

static int mv_init(void *dll_hdl, dispatch_t *dpp, io_net_self_t *ion, char *options)
{
    io_net_registrant_t reg;
    const char *helper = NULL, *firmware = NULL;
    struct dev *d;
    int rc;

    (void)dpp;
    if (the_dev) {
        say("refused: one instance only");
        errno = EBUSY;
        return -1;
    }
    if (!(d = calloc(1, sizeof(*d)))) {
        errno = ENOMEM;
        return -1;
    }
    d->ion = ion;
    d->dll_hdl = dll_hdl;
    d->pci_handle = -1;
    d->irq_id = -1;
    pthread_mutex_init(&d->lk, NULL);
    pthread_cond_init(&d->cv, NULL);
    /* getsubopt writes into the string; the shim's copy is ours to change. */
    if (parse_options(d, options, &helper, &firmware)) {
        say("usage: lan=N,mac=xx:xx:xx:xx:xx:xx,helper=FILE,firmware=FILE[,verbose]");
        rc = EINVAL;
        goto out;
    }
    init_config(d);
    rc = ENODEV;
    if (load_image(&d->helper, helper, 1) || load_image(&d->firmware, firmware, 0) ||
        map_hardware(d))
        goto out;
    if (pthread_create(&d->thread, NULL, card_thread, d)) {
        say("pthread_create failed");
        goto out;
    }
    d->thread_started = 1;
    /* The bring-up is bounded inside (download and command timeouts); the
     * stack thread waits for it, as it does for the factory attach. */
    pthread_mutex_lock(&d->lk);
    while (!d->bringup_done)
        pthread_cond_wait(&d->cv, &d->lk);
    pthread_mutex_unlock(&d->lk);
    if (d->bringup_rc) {
        pthread_join(d->thread, NULL);
        d->thread_started = 0;
        goto out;
    }
    d->cfg.irq[0] = d->irq_vector;
    free(d->helper.data);
    free(d->firmware.data);
    d->helper.data = d->firmware.data = NULL;

    the_dev = d;
    memset(&reg, 0, sizeof(reg));
    reg.flags = _REG_PRODUCER_UP;
    reg.name = NAME;
    reg.top_type = "en";
    reg.func_hdl = d;
    reg.funcs = &mv_funcs;
    if (ion->reg(dll_hdl, &reg, &d->reg_hdl, &d->cell, &d->endpoint) == -1) {
        rc = errno;
        say("io-net registration failed errno=%d", rc);
        the_dev = NULL;
        stop_thread(d);
        goto out;
    }
    pthread_mutex_lock(&d->lk);
    d->registered = 1;
    pthread_mutex_unlock(&d->lk);
    if (advertise(d))
        say("the DL_ADVERT could not be sent; en%d will lack IFF_BROADCAST", (int)d->cfg.lan);
    say("attached: lan=%d irq=%u; idle until a connect request", (int)d->cfg.lan,
        d->irq_vector);
    return 0;

out:
    release(d);
    pthread_cond_destroy(&d->cv);
    pthread_mutex_destroy(&d->lk);
    free(d);
    errno = rc;
    return -1;
}

io_net_dll_entry_t io_net_dll_entry = { 2, mv_init, NULL };
