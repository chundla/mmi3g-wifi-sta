/*
 * Phase 4.1-4.5 host bring-up, firmware download, command, scan and PIO
 * timing probe for the 88W8688 on the FPGA SDIO host, the phase 5.0
 * open-network association and the phase 5.1 data frames.
 *
 * Runs as an ordinary root process, never inside io-pkt: a fault here ends
 * this process, not MOST IP or the en5 management link. It brings the card
 * up as far as the factory probe does before firmware -- CMD5/3/7, CCCR,
 * CIS, 25 MHz, function 1 at 256-byte blocks -- reads the function-1
 * identity, I/O port and firmware status, and powers the card down again.
 * With -H and -F it also downloads the helper and the firmware image, waits
 * for the ready magic, powers down, and brings the card up a second time to
 * check the firmware is gone, so the factory attach will load its own. The
 * images must match a pinned size and cksum. With -C, and only with the
 * station image, it then sends the station firmware CMD_FUNC_INIT,
 * GET_HW_SPEC, MAC_CONTROL and CMD_FUNC_SHUTDOWN before that power-down.
 * -M sets the station's MAC after GET_HW_SPEC and reads it back; -L then
 * runs a passive scan of channels 1-11 and lists what it heard. -A then
 * joins the strongest open BSS with that SSID the way libertas connects to
 * an open network, stays associated for ASSOC_HOLD_MS while recording the
 * events and data frames the firmware passes up, and deauthenticates. It
 * sends no data frame. -I and -E replace that hold with data frames from
 * the station address -I to the peer -E: ARP requests until the peer
 * answers, then ICMP echoes whose writes take 1 to 7 blocks, twice each,
 * each reply checked byte for byte; ARP requests for -I are answered.
 * Without -A nothing is joined, and no command asks the radio to
 * transmit. -T times every transfer by phase (command, PIO
 * words, drain) and,
 * once the firmware is ready, reads its scratch register in 1-8 block CMD53
 * reads and checks every byte; it writes nothing the other options do not.
 * -c does only the firmware check, for a harness whose download
 * run did not finish. The reset line (system register 8) is never
 * written; system registers are mapped read-only.
 *
 * It refuses to touch the module while any driver holds it: the factory
 * running state has CTRL ENABLE/CLK_EN and power bits 0-1 set, and its
 * detach clears them. Detach uap0 first (radio-reattach.sh -P does).
 *
 * usage: mv8688-probe [-n | -c] [-p]
 *                     [-H helper -F firmware [-C [-M mac [-L [-A ssid [-I ip -E peer]]]] [-T]]]
 *   -n  guard check only: map read-only, print the registers, write nothing
 *   -c  firmware check only: bring the card up, read its status, power down
 *   -p  poll FLAG instead of attaching the module interrupt
 *   -H, -F  download these images (phase 4.2); both or neither
 *   -C  after the download, the phase 4.3 commands (station image only)
 *   -M  with -C: set this MAC (xx:xx:xx:xx:xx:xx) after GET_HW_SPEC
 *   -L  with -M: the phase 4.4 passive scan, after MAC_CONTROL
 *   -A  with -L: the phase 5.0 association with this open network (1-32 bytes)
 *   -I, -E  with -A: phase 5.1, this station IPv4 address and the peer's
 *   -T  with -C: the phase 4.5 transfer timing and read benchmark
 * exit: 0 OK (or guard passes with -n; with -c, no ready magic),
 *       1 refused before any write,
 *       2 failed after bring-up started (card powered down again),
 *       3 deadline or signal (card powered down again),
 *       4 firmware still answers after the power-down, or a download ran and
 *         its absence could not be read: do not let the factory attach run
 */
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/neutrino.h>
#include <sys/slog.h>
#include <sys/slogcodes.h>
#include <sys/syspage.h>
#include <hw/inout.h>
#include <hw/pci.h>
#include "hbsdc.h"
#include "sdio.h"
#include "mv8688fw.h"
#include "mv8688cmd.h"
#include "benchnet.h"

#define FPGA_VENDOR     0x10ee
#define FPGA_DEVICE     0x9411
#define MODULE_OFFSET   0x4a0000
#define MODULE_SIZE     0x10000
#define SYSREG_SIZE     0x800
#define SYSREG_BT       0x00    /* bit 2: BtReset */
#define SYSREG_WLAN     0x08    /* bit 2: WlanEnable, set holds the card off */
#define MV_VENDOR       0x02df
#define MV_DEVICE       0x9103
#define MV_CHIP_ID      0x3130  /* factory check on function-1 0x801c/0x801d */
#define PROBE_HZ        25000000
#define PROBE_DEADLINE  60
#define DOWNLOAD_DEADLINE 90    /* radio-reattach.sh bounds the run at 110 s */
/* A command starts only with this much of the deadline left: one command
 * whose transfers all stall just short of their bounds, then the
 * power-down and the second bring-up (under a second in 4.2). */
#define CMD_RESERVE_MS  30000
#define EXIT_FIRMWARE   4
#define FW_READY_TRIES  100     /* factory: 100 reads, 10 ms apart */
/* Two beacon intervals of 102.4 ms; libertas dwells 100 ms passively. */
#define SCAN_DWELL_MS   200
/* libertas LBS_DWELL_ACTIVE, for its SSID scan before a connect. */
#define SCAN_ACTIVE_MS  40
#define SCAN_BSS_MAX    32      /* descriptors listed per scan command */
#define SCAN_TRIES      3       /* SSID scans before an absent SSID fails */
#define ASSOC_HOLD_MS   15000   /* associated time, read in slices */
#define ASSOC_SLICE_MS  3000
#define ARP_TRIES       3
#define REPLY_MS        1000    /* for an ARP or echo reply */
#define ECHO_ROUNDS     2
#define AFTER_MS        3000    /* watched after the echoes */
/* Two counter samples across delays this long must agree to within 2%. */
#define CAL_MS          200
#define BENCH_ROUNDS    64      /* CMD53 reads per transfer size */
#define BENCH_CMD52     256
#define BENCH_MAX_BLOCKS 8      /* 2,048 bytes: the module's FIFO */
#define IMAGE_MAX       0x100000

struct image {
    const char *path;
    uint8_t *data;
    uint32_t size, sum;
    const struct mv8688_pinned *pin;
};

static volatile unsigned char *module;
static volatile unsigned char *sysregs;
static volatile sig_atomic_t writes_allowed;
static unsigned deadline_at;    /* now_ms() when the alarm fires */
static int polling, irq_id = -1;
static unsigned irq_vector;
static uint32_t ticks_hz;       /* calibrated counter rate; 0 without -T */

static void say(const char *fmt, ...)
{
    va_list ap;
    char line[256];
    va_start(ap, fmt);
    vsnprintf(line, sizeof(line), fmt, ap);
    va_end(ap);
    printf("%s\n", line);
    fflush(stdout);
    slogf(_SLOGC_NETWORK, _SLOG_INFO, "mv8688-probe: %s", line);
}

static uint32_t q_rd32(void *ctx, unsigned off)
{
    (void)ctx;
    return *(volatile uint32_t *)(module + off);
}

static void q_wr32(void *ctx, unsigned off, uint32_t value)
{
    (void)ctx;
    *(volatile uint32_t *)(module + off) = value;
}

static uint8_t q_rd8(void *ctx, unsigned off)
{
    (void)ctx;
    return module[off];
}

static void q_wr8(void *ctx, unsigned off, uint8_t value)
{
    (void)ctx;
    module[off] = value;
}

static int q_wait_irq(void *ctx, unsigned timeout_ms)
{
    uint64_t ns = (uint64_t)timeout_ms * 1000000;
    unsigned spins;
    (void)ctx;
    if (polling) {
        /* FLAG is read again by the caller; this only waits for a bit. */
        for (spins = 0; spins < timeout_ms * 100; spins++) {
            if (q_rd32(0, HBSDC_FLAG) & 0xffff & ~HBSDC_FLAG_SDIO_INT)
                break;
            nanospin_ns(10000);
        }
        return -1;
    }
    TimerTimeout(CLOCK_REALTIME, _NTO_TIMEOUT_INTR, NULL, &ns, NULL);
    return InterruptWait(0, NULL) == -1 ? -1 : 0;
}

static void q_irq_done(void *ctx)
{
    (void)ctx;
    if (irq_id != -1)
        InterruptUnmask(irq_vector, irq_id);
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

/* The SH ClockCycles() inline reads TMU1 through the timer page that the
 * factory's dev-ipc, qdbc and MMI3GMisc read the same way. */
static uint32_t q_ticks(void *ctx)
{
    (void)ctx;
#ifdef CLOCKCYCLES_INCR_BIT
    return (uint32_t)(ClockCycles() >> CLOCKCYCLES_INCR_BIT);
#else
    return (uint32_t)ClockCycles();
#endif
}

static const struct hbsdc_io qnx_io = {
    q_rd32, q_wr32, q_rd8, q_wr8, q_wait_irq, q_irq_done, q_delay_ms, q_spin_us, q_now_ms,
    NULL
};
static const struct hbsdc_io qnx_io_timed = {
    q_rd32, q_wr32, q_rd8, q_wr8, q_wait_irq, q_irq_done, q_delay_ms, q_spin_us, q_now_ms,
    q_ticks
};

/* Same steps as hbsdc_shutdown, safe to run from a signal handler. */
static void emergency(int sig)
{
    static const char msg[] = "verdict=FAIL deadline or signal; card powered down\n";
    if (writes_allowed && module) {
        *(volatile uint32_t *)(module + HBSDC_IEN) = 0;
        *(volatile uint32_t *)(module + HBSDC_CTRL) = HBSDC_CTRL_DEV_RESET;
        nanospin_ns(1000000);
        *(volatile uint32_t *)(module + HBSDC_POWER) = 0;
    }
    (void)sig;
    write(1, msg, sizeof(msg) - 1);
    _exit(3);
}

static int read_fn1(struct sdio_card *card, unsigned addr, uint8_t *value)
{
    return sdio_cmd52(card, 1, addr, 0, 0, value);
}

/* Rate of q_ticks against the monotonic clock. 0 (no timing) unless TMU1
 * runs over the full 32 bits, so differences survive a wrap, and two
 * samples agree to within 2%. */
static uint32_t calibrate_ticks(void)
{
    uint64_t cps = SYSPAGE_ENTRY(qtime)->cycles_per_sec;
    uint32_t rate[2], t0, tcor = 0;
    unsigned i, m0, ms[2];
#ifdef __SH_VM_TIMERPAGE_ADDR
    tcor = *(volatile uint32_t *)(__SH_VM_TIMERPAGE_ADDR + (__SH_MMR_TMU_TCOR1 -
                                                            __SH_MMR_TMU_TOCR));
#endif
    for (i = 0; i < 2; i++) {
        m0 = now_ms();
        t0 = q_ticks(0);
        delay(CAL_MS);
        ms[i] = now_ms() - m0;
        rate[i] = ms[i] ? (uint32_t)((uint64_t)(q_ticks(0) - t0) * 1000 / ms[i]) : 0;
    }
    say("timing: tcor1=0x%08x rates=%u,%u Hz over %u,%u ms syspage_cps=0x%08x%08x", tcor,
        rate[0], rate[1], ms[0], ms[1], (unsigned)(cps >> 32), (unsigned)cps);
    if (tcor != 0xffffffffu || rate[0] < 1000000 || rate[1] < 1000000 ||
        (rate[0] > rate[1] ? rate[0] - rate[1] : rate[1] - rate[0]) > rate[0] / 50) {
        say("timing: counter unusable; transfers are timed in milliseconds only");
        return 0;
    }
    return rate[0] / 2 + rate[1] / 2;
}

static unsigned avg_ns(uint64_t ticks, unsigned n)
{
    return n ? (unsigned)(ticks * 1000000000u / ticks_hz / n) : 0;
}

static unsigned max_us(uint32_t ticks)
{
    return (unsigned)((uint64_t)ticks * 1000000u / ticks_hz);
}

static unsigned rate_kbps(unsigned bytes, uint64_t ticks)
{
    return ticks ? (unsigned)((uint64_t)bytes * ticks_hz / ticks / 1000) : 0;
}

/* One line per direction and transfer size seen, then the data-less
 * commands (CMD52 almost entirely). kBps is 1000 bytes a second. */
static void report_stats(const char *tag, const struct hbsdc_stats *s)
{
    static const char *const dir[2] = { "write", "read" };
    const struct hbsdc_phase *p;
    unsigned d, b;
    if (!ticks_hz)
        return;
    for (d = 0; d < 2; d++)
        for (b = 0; b < HBSDC_BUCKETS; b++) {
            p = &s->xfer[d][b];
            if (!p->n)
                continue;
            say("timing: %s %s blocks=%s%u n=%u bytes=%u setup_ns=%u data_ns=%u drain_ns=%u "
                "max_us=%u/%u data_kBps=%u kBps=%u", tag, dir[d],
                b + 1 == HBSDC_BUCKETS ? ">=" : "", b + 1, p->n, p->bytes,
                avg_ns(p->setup, p->n), avg_ns(p->data, p->n), avg_ns(p->drain, p->n),
                max_us(p->data_max), max_us(p->total_max), rate_kbps(p->bytes, p->data),
                rate_kbps(p->bytes, p->setup + p->data + p->drain));
        }
    say("timing: %s cmd n=%u avg_ns=%u max_us=%u failures=%u", tag, s->cmd_n,
        avg_ns(s->cmd_ticks, s->cmd_n), max_us(s->cmd_max), s->failures);
}

/* Phase 4.5: CMD52 and fixed-address CMD53 reads of the scratch register,
 * which the running firmware holds at its ready magic. The download polls
 * the same register; nothing is written. A failed CMD53 is ended with the
 * CCCR I/O abort for function 1. */
static int bench(struct sdio_card *card)
{
    static const unsigned sizes[] = { 1, 2, 4, BENCH_MAX_BLOCKS };
    static uint8_t buf[BENCH_MAX_BLOCKS * MV_BLKSZ];
    uint8_t expect, value;
    uint16_t fw = 0;
    unsigned i, r, k, n, bad, bad_all = 0, t0;
    int rc, abort_rc, left = (int)(deadline_at - now_ms());

    if (left < CMD_RESERVE_MS) {
        say("bench: NOT RUN: %d ms left before the deadline, %u needed", left, CMD_RESERVE_MS);
        return -1;
    }
    if ((rc = read_fn1(card, MV_REG_SCRATCH, &expect))) {
        say("bench: scratch read FAILED rc=%d r5=0x%08x", rc, card->r5);
        return -1;
    }
    t0 = now_ms();
    for (r = bad = 0; r < BENCH_CMD52; r++) {
        if ((rc = read_fn1(card, MV_REG_SCRATCH, &value))) {
            say("bench: CMD52 read %u FAILED rc=%d r5=0x%08x", r, rc, card->r5);
            return -1;
        }
        bad += value != expect;
    }
    say("bench: %u CMD52 reads of fn1 0x%02x in %u ms, %u differ from 0x%02x", BENCH_CMD52,
        MV_REG_SCRATCH, now_ms() - t0, bad, expect);
    bad_all += bad;
    for (i = 0; i < sizeof(sizes) / sizeof(sizes[0]); i++) {
        n = sizes[i] * MV_BLKSZ;
        t0 = now_ms();
        for (r = bad = 0; r < BENCH_ROUNDS; r++) {
            /* The complement, so a word the PIO loop did not fill shows. */
            memset(buf, (uint8_t)~expect, n);
            rc = sdio_cmd53(card, 1, MV_REG_SCRATCH, 0, 0, buf, sizes[i], MV_BLKSZ);
            if (rc) {
                abort_rc = sdio_cmd52(card, 0, SDIO_CCCR_ABORT, 1, 1, NULL);
                say("bench: CMD53 read of %u blocks FAILED at round %u rc=%d r5=0x%08x "
                    "flags=0x%04x; abort rc=%d", sizes[i], r, rc, card->r5,
                    card->host->flags, abort_rc);
                return -1;
            }
            for (k = 0; k < n; k++)
                bad += buf[k] != expect;
        }
        say("bench: %u CMD53 reads of %u blocks (%u bytes) in %u ms, %u bytes differ",
            BENCH_ROUNDS, sizes[i], n, now_ms() - t0, bad);
        bad_all += bad;
    }
    if ((rc = mv8688_fw_status(card, &fw)) || fw != MV_FW_READY) {
        say("bench: firmware status afterwards rc=%d fw_status=0x%04x (ready magic %s)", rc,
            fw, fw == MV_FW_READY ? "present" : "LOST");
        return -1;
    }
    say("bench: %s; firmware status still 0x%04x", bad_all ? "DATA DIFFERED" : "all reads matched",
        fw);
    return bad_all ? -1 : 0;
}

/* Reads the whole file before anything is written to the module. */
static int load_image(struct image *img, int helper)
{
    struct stat st;
    ssize_t got = 0;
    uint32_t done = 0;
    int fd = open(img->path, O_RDONLY), err;
    if (fd == -1 || fstat(fd, &st) == -1) {
        err = errno;
        say("verdict=REFUSED image %s: %s failed errno=%d", img->path,
            fd == -1 ? "open" : "fstat", err);
        if (fd != -1)
            close(fd);
        return -1;
    }
    if (!S_ISREG(st.st_mode) || st.st_size <= 0 || st.st_size > IMAGE_MAX) {
        say("verdict=REFUSED image %s: not a regular file of 1..%u bytes", img->path,
            IMAGE_MAX);
        close(fd);
        return -1;
    }
    img->size = (uint32_t)st.st_size;
    img->data = malloc(img->size);
    if (!img->data) {
        say("verdict=REFUSED image %s: cannot allocate %u bytes", img->path,
            (unsigned)img->size);
        close(fd);
        return -1;
    }
    while (done < img->size) {
        got = read(fd, img->data + done, img->size - done);
        if (got <= 0)
            break;
        done += (uint32_t)got;
    }
    err = got < 0 ? errno : 0;
    close(fd);
    if (done != img->size) {
        if (err)
            say("verdict=REFUSED image %s: read failed after %u of %u bytes errno=%d",
                img->path, (unsigned)done, (unsigned)img->size, err);
        else
            say("verdict=REFUSED image %s: end of file after %u of %u bytes", img->path,
                (unsigned)done, (unsigned)img->size);
        return -1;
    }
    img->sum = mv8688_cksum(img->data, img->size);
    img->pin = mv8688_pinned_image(img->size, img->sum, helper);
    say("image: %s path=%s size=%u cksum=%u (%s)", helper ? "helper" : "firmware",
        img->path, (unsigned)img->size, (unsigned)img->sum,
        img->pin ? img->pin->name : "NOT a pinned image");
    if (!img->pin) {
        say("verdict=REFUSED image %s is not a pinned %s image", img->path,
            helper ? "helper" : "firmware");
        return -1;
    }
    return 0;
}

static int report_function1(struct sdio_card *card, uint32_t *port, uint16_t *fw,
                            const char *tag)
{
    uint8_t b[2], status, rx_unit;
    unsigned chip;
    int rc, ok = 1;

    if ((rc = read_fn1(card, MV_REG_CHIP_ID, &b[0])) ||
        (rc = read_fn1(card, MV_REG_CHIP_ID + 1, &b[1]))) {
        say("%sfn1: chip id read failed rc=%d r5=0x%08x", tag, rc, card->r5);
        return 0;
    }
    chip = (unsigned)b[0] << 8 | b[1];
    say("%sfn1: chip_id=0x%04x (factory expects 0x%04x)", tag, chip, MV_CHIP_ID);
    if (chip != MV_CHIP_ID)
        ok = 0;
    if ((rc = mv8688_ioport(card, port))) {
        say("%sfn1: I/O port read failed rc=%d r5=0x%08x", tag, rc, card->r5);
        return 0;
    }
    say("%sfn1: ioport=0x%05x", tag, (unsigned)*port);
    if ((rc = mv8688_fw_status(card, fw))) {
        say("%sfn1: firmware status read failed rc=%d", tag, rc);
        return 0;
    }
    say("%sfn1: fw_status=0x%04x (%s)", tag, *fw,
        *fw == MV_FW_READY ? "ready magic" : "ready magic absent");
    if ((rc = read_fn1(card, MV_REG_STATUS, &status)) ||
        (rc = read_fn1(card, MV_REG_RX_UNIT, &rx_unit))) {
        say("%sfn1: status read failed rc=%d", tag, rc);
        return 0;
    }
    say("%sfn1: card_status=0x%02x io_ready=%d dl_ready=%d rx_unit=%u", tag, status,
        (status & 0x08) != 0, (status & 0x01) != 0, rx_unit);
    return ok;
}

/* Attach, 25 MHz, function 1 at 256-byte blocks: the factory probe's
 * steps up to uap_init_fw. Returns 0 when function 1 answered as expected. */
static int bring_up(struct hbsdc *host, struct sdio_card *card, uint32_t *port,
                    uint16_t *fw, const char *tag)
{
    int rc;
    memset(card, 0, sizeof(*card));
    card->host = host;
    rc = sdio_attach(card, MV_VENDOR, MV_DEVICE);
    say("%scard: power_raw=0x%08x ocr=0x%08x nfunc=%u rca=0x%04x", tag, card->power_raw,
        card->ocr, card->nfunc, card->rca);
    say("%scccr: rev=0x%02x sd=0x%02x ior=0x%02x caps=0x%02x bus_if=0x%02x speed=0x%02x "
        "ready_ms=%u width=%u", tag, card->cccr[0], card->cccr[1], card->cccr[3],
        card->cccr[8], card->cccr[7], card->cccr[0x13], card->ready_ms, card->width);
    say("%scis: vendor=0x%04x device=0x%04x cis0=0x%05x cis1=0x%05x fn1_code=0x%02x "
        "manf=\"%s\"", tag, card->vendor, card->device, (unsigned)card->cis[0],
        (unsigned)card->cis[1], card->fn_code[1], card->manf);
    if (rc) {
        say("%sattach: FAILED at %s rc=%d r5=0x%08x flags=0x%04x resp_index=0x%02x", tag,
            card->step ? card->step : "?", rc, card->r5, host->flags, host->resp_index);
        return -1;
    }
    if ((rc = hbsdc_set_clock(host, PROBE_HZ))) {
        say("%sclock: FAILED rc=%d", tag, rc);
        return -1;
    }
    say("%sclock: bus=%u ctrl=0x%08x", tag, host->bus_hz, q_rd32(0, HBSDC_CTRL));
    if ((rc = sdio_set_blksz(card, 1, MV_BLKSZ))) {
        say("%sfn1: block size %u FAILED rc=%d", tag, MV_BLKSZ, rc);
        return -1;
    }
    return report_function1(card, port, fw, tag) ? 0 : -1;
}

/* The request register before the first chunk, as poll:value changes. */
static void report_trace(const struct mv8688_dl *dl)
{
    char line[160];
    unsigned i, n = dl->trace_n < MV_TRACE ? dl->trace_n : MV_TRACE;
    int len = 0;

    line[0] = 0;
    for (i = 0; i < n && len >= 0 && len < (int)sizeof(line); i++)
        len += snprintf(line + len, sizeof(line) - len, " %u:0x%04x",
                        dl->trace_poll[i], dl->trace_value[i]);
    say("request_trace: changes=%u%s%s", dl->trace_n, line,
        dl->trace_n > MV_TRACE ? " (more not kept)" : "");
}

static int download(struct sdio_card *card, uint32_t port, const struct image *helper,
                    const struct image *fw, uint8_t *rx_unit)
{
    static struct mv8688_dl dl;
    unsigned t0, t1, t2, t3;
    int rc;

    memset(&dl, 0, sizeof(dl));
    dl.card = card;
    dl.ioport = port;
    t0 = now_ms();
    rc = mv8688_prog_helper(&dl, helper->data, helper->size);
    t1 = now_ms();
    say("helper: rc=%d chunks=%u ready_reads_max=%u rd_base_pre=0x%04x rd_base_pre_rc=%d "
        "ms=%u%s%s", rc, dl.helper_chunks, dl.ready_reads_max, dl.rd_base_pre,
        dl.rd_base_pre_rc, t1 - t0, rc ? " FAILED at " : "", rc ? dl.step : "");
    if (rc)
        return -1;
    rc = mv8688_prog_fw(&dl, fw->data, fw->size);
    t2 = now_ms();
    report_trace(&dl);
    say("firmware: rc=%d sent=%u/%u requests=%u req_min=%u req_max=%u crc_retries=%u "
        "aborts=%u abort_failures=%u first_polls=%u early_odd=%u zero_polls_max=%u "
        "ready_reads_max=%u last_request=0x%04x ms=%u%s%s",
        rc, (unsigned)dl.sent, (unsigned)fw->size, dl.requests, dl.req_min, dl.req_max,
        dl.crc_retries, dl.aborts, dl.abort_failures, dl.first_polls, dl.early_odd,
        dl.zero_polls_max, dl.ready_reads_max, dl.last_request, t2 - t1,
        rc ? " FAILED at " : "", rc ? dl.step : "");
    if (rc)
        return -1;
    rc = mv8688_wait_fw(&dl, FW_READY_TRIES);
    t3 = now_ms();
    say("ready: rc=%d fw_status=0x%04x polls=%u rx_unit=%u rx_unit_rc=%d ms=%u%s", rc,
        dl.fw_status, dl.fw_polls, dl.rx_unit, dl.rx_unit_rc, t3 - t2,
        rc ? " (ready magic never appeared)" : "");
    say("download: total_ms=%u (helper start to ready magic; the factory uAP window "
        "measured 412-479 ms)", t3 - t0);
    *rx_unit = dl.rx_unit;
    return rc ? -1 : 0;
}

static void report_bytes(const char *tag, const uint8_t *p, unsigned n)
{
    char line[160];
    unsigned i, shown = n < 64 ? n : 64;
    for (i = 0; i < shown; i++)
        snprintf(line + 2 * i, 3, "%02x", p[i]);
    line[2 * shown] = 0;
    say("%s len=%u %s%s", tag, n, line, n > shown ? " (truncated)" : "");
}

static int run_command(struct mv8688_cmd *c, const char *name, uint16_t command,
                       const void *body, unsigned len)
{
    unsigned t0 = now_ms();
    int rc, left = (int)(deadline_at - t0);
    if (left < CMD_RESERVE_MS) {
        say("cmd: %s 0x%04x NOT SENT: %d ms left before the deadline, %u needed",
            name, command, left, CMD_RESERVE_MS);
        return -1;
    }
    rc = mv8688_command(c, command, body, len, MV_CMD_TIMEOUT_MS);
    say("cmd: %s 0x%04x rc=%d seq=%u resp=0x%04x size=%u resp_seq=%u result=0x%04x "
        "write_tries=%u aborts=%u ready_reads=%u io_status=0x%02x polls=%u rx_len=%u "
        "rx_size=%u chunk=%u ms=%u%s%s", name, command, rc, c->seq, c->resp_cmd,
        c->resp_size, c->resp_seq, c->resp_result, c->write_tries, c->aborts,
        c->ready_reads, c->ready_status, c->polls, c->rx_len_raw, c->rx_size, c->rx_chunk,
        now_ms() - t0, rc ? " FAILED at " : "", rc ? c->step : "");
    if (rc && c->io_rc)
        say("cmd: %s card-layer rc=%d r5=0x%08x", name, c->io_rc, c->card->r5);
    if (c->resp)
        report_bytes(rc ? "cmd: response (rejected)" : "cmd: response", c->resp,
                     c->resp_len);
    return rc;
}

static const char *mac_text(const uint8_t *m)
{
    static char text[18];
    snprintf(text, sizeof(text), "%02x:%02x:%02x:%02x:%02x:%02x", m[0], m[1], m[2], m[3],
             m[4], m[5]);
    return text;
}

/* libertas lbs_start_iface: the address goes in before anything else is
 * configured. The read-back is not something libertas does; a firmware
 * that rejects it (a non-zero result) is reported, not failed. */
static int set_mac(struct mv8688_cmd *c, const uint8_t *mac)
{
    static const uint8_t none[6];
    uint8_t body[8], got[6];
    int rc;
    mv8688_mac_body(body, MV_ACT_SET, mac);
    if ((rc = run_command(c, "MAC_ADDRESS set", MV_CMD_MAC_ADDRESS, body, sizeof(body))))
        return rc;
    if (mv8688_resp_mac(c, got))
        say("mac: set response too short to echo the address (size=%u)", c->resp_size);
    else
        say("mac: set response echoes %s (%s)", mac_text(got),
            memcmp(got, mac, 6) ? "DIFFERENT" : "as sent");
    mv8688_mac_body(body, MV_ACT_GET, none);
    rc = run_command(c, "MAC_ADDRESS get", MV_CMD_MAC_ADDRESS, body, sizeof(body));
    if (rc == MV_ECMDRESULT) {
        say("mac: read-back refused by the firmware (result=0x%04x); set result stands",
            c->resp_result);
        return 0;
    }
    if (rc)
        return rc;
    if (mv8688_resp_mac(c, got)) {
        say("mac: read-back response too short (size=%u)", c->resp_size);
        return -1;
    }
    if (memcmp(got, mac, 6)) {
        say("mac: read-back %s does NOT match the address set", mac_text(got));
        return -1;
    }
    say("mac: read-back %s matches", mac_text(got));
    return 0;
}

/* SSIDs are printed as text with anything outside printable ASCII, and
 * the backslash, as \xNN. */
static const char *ssid_text(const struct mv8688_bss *b)
{
    static char text[4 * 32 + 1];
    unsigned i, n = b->ssid_len < 0 ? 0 : b->ssid_len < 32 ? (unsigned)b->ssid_len : 32;
    int len = 0;
    for (i = 0; i < n; i++) {
        if (b->ssid[i] >= 0x20 && b->ssid[i] < 0x7f && b->ssid[i] != '\\')
            text[len++] = (char)b->ssid[i];
        else
            len += snprintf(text + len, sizeof(text) - len, "\\x%02x", b->ssid[i]);
    }
    text[len] = 0;
    return text;
}

static int ssid_is(const struct mv8688_bss *b, const char *ssid)
{
    size_t n = strlen(ssid);
    return b->ssid_len == (int)n && !memcmp(b->ssid, ssid, n);
}

/* Phase 4.4: a passive scan of channels 1-11 in libertas' groups of four.
 * A group that fails is reported and the next one still runs. With an
 * SSID, the strongest BSS carrying it (lowest RSSI byte) is kept in *best
 * and *found counts them. */
static int scan(struct mv8688_cmd *c, unsigned *heard, const char *ssid,
                struct mv8688_bss *best, unsigned *found)
{
    static const uint8_t channels[] = { 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11 };
    static struct mv8688_bss bss[SCAN_BSS_MAX];
    uint8_t body[MV_SCAN_BODY_MAX];
    char name[24];
    unsigned first, n, len, sets, i;
    int rc, failed = 0;
    for (first = 0; first < sizeof(channels); first += n) {
        n = sizeof(channels) - first;
        if (n > MV_SCAN_CHANNELS)
            n = MV_SCAN_CHANNELS;
        /* With an SSID, libertas' connect scan: active, for that SSID only.
         * 5.0's second run missed a -71 dBm BSS in two passive scans. */
        len = ssid ? mv8688_scan_body(body, channels + first, n, SCAN_ACTIVE_MS,
                                      (const uint8_t *)ssid, (unsigned)strlen(ssid))
                   : mv8688_scan_body(body, channels + first, n, SCAN_DWELL_MS, NULL, 0);
        snprintf(name, sizeof(name), "SCAN ch%u-%u", channels[first], channels[first + n - 1]);
        if ((rc = run_command(c, name, MV_CMD_SCAN, body, len))) {
            failed = 1;
            if (rc == MV_ECMDRESULT)
                continue;
            break;          /* the transport failed; later groups would too */
        }
        if (mv8688_parse_scan(c, bss, SCAN_BSS_MAX, &sets)) {
            say("scan: %s response malformed (size=%u, sets=%u)", name, c->resp_size, sets);
            failed = 1;
            continue;
        }
        say("scan: %s sets=%u descriptor_bytes=%u", name, sets,
            c->resp_size >= MV_CMD_HEADER + 2 ? c->resp[8] | c->resp[9] << 8 : 0);
        for (i = 0; i < sets && i < SCAN_BSS_MAX; i++)
            say("scan: bss bssid=%s ch=%d rssi=%u (~%d dBm) interval=%u cap=0x%04x%s "
                "ssid_len=%d ssid=\"%s\"", mac_text(bss[i].bssid), bss[i].channel,
                bss[i].rssi, 3 - (int)bss[i].rssi, bss[i].interval, bss[i].capability,
                bss[i].capability & 0x10 ? " privacy" : "", bss[i].ssid_len,
                ssid_text(&bss[i]));
        if (sets > SCAN_BSS_MAX)
            say("scan: %u more descriptors not listed", sets - SCAN_BSS_MAX);
        for (i = 0; ssid && i < sets && i < SCAN_BSS_MAX; i++)
            if (ssid_is(&bss[i], ssid) && (!(*found)++ || bss[i].rssi < best->rssi))
                *best = bss[i];
        *heard += sets;
    }
    return failed ? -1 : 0;
}

/* Supported rates, then "|" and the extended ones. */
static const char *rates_text(const struct mv8688_bss *b)
{
    static char text[3 * MV_RATES_MAX + 1];
    unsigned i, len = 0;
    text[0] = 0;
    for (i = 0; i < b->rates_n && i < MV_RATES_MAX; i++)
        len += snprintf(text + len, sizeof(text) - len, "%s%02x",
                        i ? (i == b->rates_supp ? "|" : ",") : "", b->rates[i]);
    return text;
}

/* A command whose refusal libertas would not notice on a fresh firmware:
 * reported, not failed. Transport failures still fail. */
static int run_tolerated(struct mv8688_cmd *c, const char *name, uint16_t command,
                         const void *body, unsigned len)
{
    int rc = run_command(c, name, command, body, len);
    if (rc == MV_ECMDRESULT) {
        say("join: %s refused (result=0x%04x); nothing was set to clear", name,
            c->resp_result);
        return 0;
    }
    return rc;
}

static const char *event_name(uint32_t cause)
{
    switch (cause & 0xff) {
    case MV_EVENT_LINK_LOST_SCAN: return "link lost (scan)";
    case MV_EVENT_LINK_LOST: return "link lost";
    case MV_EVENT_DEAUTHENTICATED: return "deauthenticated";
    case MV_EVENT_DISASSOCIATED: return "disassociated";
    default: return "other";
    }
}

/* Phase 5.1. Echo payloads chosen so that each write, 70 bytes of SDIO
 * header, txpd and Ethernet, IPv4 and ICMP headers plus the payload, takes
 * one more block: 1 to 7. 4.5 timed writes of 2 blocks at most. */
static const unsigned echo_sizes[] = { 56, 248, 504, 760, 1016, 1272, BENCH_PAYLOAD_MAX };
#define ECHO_SIZES (sizeof(echo_sizes) / sizeof(echo_sizes[0]))

struct link {
    struct mv8688_cmd *c;
    struct bench *b;
};

static const char *kind_name(enum bench_kind kind)
{
    switch (kind) {
    case BENCH_ARP_REPLY: return "arp_reply";
    case BENCH_ARP_ASKED: return "arp_request_for_us";
    case BENCH_ECHO_REPLY: return "echo_reply";
    case BENCH_ECHO_BAD: return "echo_BAD";
    case BENCH_ECHO_LATE: return "echo_late";
    case BENCH_IPV4_OTHER: return "ipv4_other";
    default: return "other";
    }
}

/* mv8688cmd hands every decoded frame here, as Ethernet II. Anything the
 * bench cares about ends the current wait so the caller can look. */
static void link_frame(void *ctx, const struct mv8688_rx *rx, const uint8_t *frame,
                       unsigned len)
{
    struct link *l = ctx;
    enum bench_kind kind = bench_rx(l->b, frame, len);
    if (kind == BENCH_OTHER || kind == BENCH_IPV4_OTHER) {
        say("rx: %s len=%u type=0x%02x%02x snr=%u", kind_name(kind), len,
            len >= 14 ? frame[12] : 0, len >= 14 ? frame[13] : 0, rx->snr);
        return;
    }
    say("rx: %s len=%u snr=%u nf=%u rate=%u%s%s", kind_name(kind), len, rx->snr, rx->nf,
        rx->rate, kind == BENCH_ECHO_BAD ? " failed at " : "",
        kind == BENCH_ECHO_BAD ? l->b->bad : "");
    l->c->wait_done = 1;
}

static int send_frame(struct mv8688_cmd *c, const char *name, const uint8_t *frame,
                      unsigned len)
{
    unsigned t0 = now_ms();
    int rc, left = (int)(deadline_at - t0);
    if (left < CMD_RESERVE_MS) {
        say("tx: %s NOT SENT: %d ms left before the deadline, %u needed", name, left,
            CMD_RESERVE_MS);
        return -1;
    }
    report_bytes("tx: frame", frame, len);
    rc = mv8688_send_data(c, frame, len, MV_TX_DONE_MS);
    say("tx: %s len=%u blocks=%u rc=%d write_tries=%u aborts=%u ready_reads=%u "
        "io_status=0x%02x dnld_polls=%u ms=%u%s%s", name, len, c->tx_blocks, rc,
        c->write_tries, c->aborts, c->ready_reads, c->ready_status, c->tx_polls,
        now_ms() - t0, rc ? " FAILED at " : "", rc ? c->step : "");
    if (rc && c->io_rc)
        say("tx: %s card-layer rc=%d r5=0x%08x", name, c->io_rc, c->card->r5);
    return rc;
}

static int arp_resolved(const struct bench *b)
{
    return b->peer_known;
}

static int echo_answered(const struct bench *b)
{
    return b->echo_done;
}

static int never(const struct bench *b)
{
    (void)b;
    return 0;
}

/* Reads what the card passes up for up to ms, until done() holds,
 * answering ARP requests for the station's address on the way. */
static int await(struct mv8688_cmd *c, struct bench *b, unsigned ms,
                 int (*done)(const struct bench *))
{
    uint8_t frame[BENCH_ARP_LEN];
    unsigned t0 = now_ms(), spent;
    int rc;
    for (;;) {
        if (b->arp_due) {
            b->arp_due = 0;
            if ((rc = send_frame(c, "ARP reply", frame, bench_arp_reply(b, frame))))
                return rc;
        }
        if (done(b))
            return 0;
        spent = now_ms() - t0;
        if (spent >= ms)
            return 0;
        if ((rc = mv8688_wait(c, ms - spent)))
            return rc;
    }
}

static const char *ip_text(const uint8_t *ip)
{
    static char text[16];
    snprintf(text, sizeof(text), "%u.%u.%u.%u", ip[0], ip[1], ip[2], ip[3]);
    return text;
}

/* Phase 5.1, once associated: resolve the peer, then echo every size
 * ECHO_ROUNDS times. Passes when the peer answered ARP, every size was
 * answered at least once, no reply failed a check and the link held. A
 * lost frame is reported; the air is not the subject here. */
static int exchange(struct mv8688_cmd *c, struct bench *b, unsigned ends_before)
{
    static uint8_t frame[MV_ETH_MAX];
    unsigned tries, round, i, seq, len, t0, blocks, answered[ECHO_SIZES], sizes_ok = 0;
    char name[32];
    int rc = 0;

    memset(answered, 0, sizeof(answered));
    for (tries = 0; tries < ARP_TRIES && !rc && !b->peer_known; tries++) {
        snprintf(name, sizeof(name), "ARP request %u", tries + 1);
        len = bench_arp_request(b, frame);
        t0 = now_ms();
        if (!(rc = send_frame(c, name, frame, len)))
            rc = await(c, b, REPLY_MS, arp_resolved);
        say("arp: try %u rc=%d resolved=%d ms=%u", tries + 1, rc, b->peer_known,
            now_ms() - t0);
        if (c->disconnects != ends_before)
            rc = -1;
    }
    if (!rc && !b->peer_known) {
        say("arp: FAILED %s did not answer %u requests", ip_text(b->peer_ip), ARP_TRIES);
        rc = -1;
    }
    if (!rc)
        say("arp: %s is at %s", ip_text(b->peer_ip), mac_text(b->peer_mac));
    for (round = 0; round < ECHO_ROUNDS && !rc; round++) {
        for (i = 0; i < ECHO_SIZES && !rc; i++) {
            seq = round * ECHO_SIZES + i + 1;
            len = bench_echo_request(b, frame, (uint16_t)seq, echo_sizes[i]);
            snprintf(name, sizeof(name), "echo %u", seq);
            t0 = now_ms();
            rc = send_frame(c, name, frame, len);
            /* Before the wait, which may send an ARP reply. */
            blocks = c->tx_blocks;
            if (!rc)
                rc = await(c, b, REPLY_MS, echo_answered);
            say("echo: seq=%u payload=%u frame=%u blocks=%u rc=%d reply=%s ms=%u", seq,
                echo_sizes[i], len, blocks, rc, b->echo_done ? "ok" : "none",
                now_ms() - t0);
            answered[i] += b->echo_done;
            if (c->disconnects != ends_before) {
                say("echo: the association ENDED");
                rc = -1;
            }
        }
    }
    /* Late replies and the link, for a few seconds more. */
    if (!rc)
        rc = await(c, b, AFTER_MS, never);
    for (i = 0; i < ECHO_SIZES; i++)
        sizes_ok += answered[i] > 0;
    say("bench: frames=%u arp_replies=%u arp_asked=%u echo_replies=%u echo_bad=%u "
        "echo_late=%u ipv4_other=%u other=%u tx_frames=%u tx_bytes=%u sizes_answered=%u/%u "
        "link_ends=%u", b->frames, b->arp_replies, b->arp_asked, b->echo_replies,
        b->echo_bad, b->echo_late, b->ipv4_other, b->other, c->tx_frames, c->tx_bytes,
        sizes_ok, (unsigned)ECHO_SIZES, c->disconnects - ends_before);
    if (!rc && (sizes_ok != ECHO_SIZES || b->echo_bad || c->disconnects != ends_before)) {
        say("bench: FAILED %s", b->echo_bad ? "a reply failed its checks"
                               : sizes_ok != ECHO_SIZES ? "a size was never answered"
                               : "the association ended");
        rc = -1;
    }
    return rc;
}

/* Phase 5.0: lbs_cfg_connect for cipher 0, in its order. MAC_CONTROL (RX
 * and TX, no WEP) already went before the scan. The WEP and key clears
 * find nothing to clear on a firmware this probe just booted, so their
 * refusal is tolerated. */
static int join(struct mv8688_cmd *c, const struct mv8688_bss *b, struct bench *bench)
{
    /* The largest body here; SET_WEP's 72 bytes also fit. */
    uint8_t body[MV_ASSOC_BODY_MAX];
    uint16_t capability = 0, status = 0xffff, aid = 0;
    unsigned len, slice, held, ends, ends_before, t0;
    int rc, assoc_rc, deauth_rc, parsed;

    say("join: target bssid=%s ch=%d rssi=%u (~%d dBm) cap=0x%04x rates=%s rsn=%d",
        mac_text(b->bssid), b->channel, b->rssi, 3 - (int)b->rssi, b->capability,
        rates_text(b), b->rsn);
    if (!(b->capability & MV_CAP_ESS) || (b->capability & MV_CAP_PRIVACY) || b->rsn) {
        say("join: REFUSED the BSS is not an open infrastructure network");
        return -1;
    }
    if (b->channel < 1 || b->channel > 11) {
        say("join: REFUSED channel %d is outside the scanned 1-11", b->channel);
        return -1;
    }
    mv8688_wep_remove_body(body);
    if ((rc = run_tolerated(c, "SET_WEP remove", MV_CMD_SET_WEP, body, MV_WEP_BODY_SIZE)))
        return rc;
    mv8688_key_clear_body(body, MV_KEY_INFO_UNICAST);
    if ((rc = run_tolerated(c, "KEY_MATERIAL clear unicast", MV_CMD_KEY_MATERIAL, body,
                            MV_KEY_BODY_SIZE)))
        return rc;
    mv8688_key_clear_body(body, MV_KEY_INFO_MCAST);
    if ((rc = run_tolerated(c, "KEY_MATERIAL clear group", MV_CMD_KEY_MATERIAL, body,
                            MV_KEY_BODY_SIZE)))
        return rc;
    mv8688_action_body(body, MV_ACT_SET, 0);
    if ((rc = run_command(c, "ENABLE_RSN off", MV_CMD_ENABLE_RSN, body, 4)))
        return rc;
    mv8688_auth_body(body, b->bssid);
    if ((rc = run_command(c, "AUTHENTICATE open", MV_CMD_AUTHENTICATE, body,
                          MV_AUTH_BODY_SIZE)))
        return rc;
    /* Firmware v9 and later takes no preamble here: radio on only. */
    mv8688_action_body(body, MV_ACT_SET, 1);
    if ((rc = run_command(c, "RADIO_CONTROL on", MV_CMD_RADIO_CONTROL, body, 4)))
        return rc;
    if (!(len = mv8688_assoc_body(body, b))) {
        say("join: REFUSED no usable SSID or channel for the association body");
        return -1;
    }
    report_bytes("join: associate body", body, len);
    ends_before = c->disconnects;
    assoc_rc = run_command(c, "ASSOCIATE", MV_CMD_ASSOCIATE, body, len);
    parsed = c->resp && !mv8688_parse_assoc(c, &capability, &status, &aid);
    if (parsed)
        say("join: association status=0x%04x aid=0x%04x capability=0x%04x%s", status, aid,
            capability, status ? " (not associated)" : "");
    rc = assoc_rc ? assoc_rc : !parsed || status ? -1 : 0;
    if (!rc && bench)
        rc = exchange(c, bench, ends_before);
    else if (!rc) {
        /* Held in slices, so the log shows the link was watched throughout. */
        for (held = 0; held < ASSOC_HOLD_MS && !rc; held += slice) {
            slice = ASSOC_HOLD_MS - held < ASSOC_SLICE_MS ? ASSOC_HOLD_MS - held
                                                          : ASSOC_SLICE_MS;
            if ((int)(deadline_at - now_ms()) < CMD_RESERVE_MS + (int)slice) {
                say("join: hold cut short at %u ms: the deadline is near", held);
                break;
            }
            t0 = now_ms();
            rc = mv8688_wait(c, slice);
            ends = c->disconnects - ends_before;
            say("join: held %u ms rc=%d events=%u data=%u data_bytes=%u data_bad=%u "
                "stray=%u link_ends=%u%s%s", held + now_ms() - t0, rc, c->events,
                c->data_packets, c->data_bytes, c->data_bad, c->stray_responses, ends,
                rc ? " FAILED at " : "", rc ? c->step : "");
            if (ends) {
                say("join: the association ENDED during the hold");
                rc = -1;
            }
        }
    }
    /* libertas lbs_disconnect. Sent after any association attempt, as the
     * firmware's state is unknown when the response was lost; a refusal of
     * a deauthentication that has nothing to end is reported only. */
    mv8688_deauth_body(body, b->bssid, MV_REASON_LEAVING);
    deauth_rc = run_command(c, "DEAUTHENTICATE", MV_CMD_DEAUTHENTICATE, body,
                            MV_DEAUTH_BODY_SIZE);
    if (deauth_rc == MV_ECMDRESULT && (assoc_rc || status)) {
        say("join: deauthentication refused after a failed association; not an error");
        deauth_rc = 0;
    }
    return rc ? rc : deauth_rc;
}

static void report_data(const struct mv8688_cmd *c)
{
    const struct mv8688_rx *r;
    unsigned i;
    for (i = 0; i < c->data_n && i < MV_DATA_KEPT; i++) {
        r = &c->data[i];
        say("data: %u frame_len=%u pkt_len=%u pkt_ptr=%u status=0x%04x snr=%u nf=%u "
            "rate=%u prio=%u dst=%s%s ethertype=0x%04x", i, r->frame_len, r->pkt_len,
            (unsigned)r->pkt_ptr, r->status, r->snr, r->nf, r->rate, r->priority,
            mac_text(r->dst), r->dst[0] & 1 ? " (group)" : "", r->ethertype);
        say("data: %u src=%s", i, mac_text(r->src));
    }
    if (c->data_n > MV_DATA_KEPT)
        say("data: %u more frames not listed", c->data_n - MV_DATA_KEPT);
}

/* Phase 4.3: what libertas sends a freshly booted SD8688 before it scans.
 * Leaves out its TX power read and 802.11d enable. With a MAC, phase 4.4:
 * set it, and with do_scan, scan passively before the shutdown. */
static int commands(struct sdio_card *card, uint32_t port, uint8_t rx_unit,
                    const uint8_t *mac, int do_scan, const char *ssid, struct bench *bench)
{
    static struct mv8688_cmd c;
    struct link link;
    struct mv8688_hw_spec spec;
    struct mv8688_bss target;
    uint8_t hw_body[MV_HW_SPEC_SIZE - MV_CMD_HEADER], mac_body[4];
    uint32_t r;
    unsigned i, tries, heard = 0, found = 0;
    int rc, init_rc, shutdown_rc = 0, stop_rc, scan_rc = 0, join_rc = 0;

    memset(&c, 0, sizeof(c));
    c.card = card;
    c.ioport = port;
    c.rx_unit = rx_unit;
    if (bench) {
        link.c = &c;
        link.b = bench;
        c.on_frame = link_frame;
        c.frame_ctx = &link;
    }
    if ((rc = mv8688_cmd_start(&c))) {
        say("cmd: host interrupt mask FAILED rc=%d r5=0x%08x", rc, card->r5);
        /* The write may still have reached the card. */
        say("cmd: mask_off_rc=%d", mv8688_cmd_stop(&c));
        return -1;
    }
    /* The attach enabled the function's CCCR interrupt, as the factory's
     * does; the host's card interrupt enable (IEN 0x100) stays off, and
     * the status register is polled. */
    say("cmd: host interrupt mask 0x%02x; cccr_ien=0x%02x host_ien=0x%08x; status polled; "
        "rx_unit=%u", MV_INT_MASK_ALL, card->cccr[SDIO_CCCR_IENX],
        (unsigned)card->host->io->rd32(card->host->ctx, HBSDC_IEN), rx_unit);
    rc = init_rc = run_command(&c, "FUNC_INIT", MV_CMD_FUNC_INIT, NULL, 0);
    if (!rc) {
        /* As libertas: the MAC field carries the broadcast address. */
        memset(hw_body, 0, sizeof(hw_body));
        memset(hw_body + 8, 0xff, 6);
        rc = run_command(&c, "GET_HW_SPEC", MV_CMD_GET_HW_SPEC, hw_body, sizeof(hw_body));
    }
    if (!rc) {
        if (mv8688_parse_hw_spec(&c, &spec)) {
            say("hw_spec: response too short (size=%u len=%u, need %u)", c.resp_size,
                c.resp_len, MV_HW_SPEC_SIZE);
            rc = -1;
        } else {
            /* libertas moves the top byte (the patch level) to the bottom. */
            r = spec.fwrelease << 8 | (spec.fwrelease >> 24 & 0xff);
            say("hw_spec: fw_release=%u.%u.%up%u raw=0x%08x capinfo=0x%08x hwif=0x%04x "
                "version=0x%04x", r >> 24, r >> 16 & 0xff, r >> 8 & 0xff, r & 0xff,
                (unsigned)spec.fwrelease, (unsigned)spec.fwcapinfo, spec.hwifversion,
                spec.version);
            say("hw_spec: region=0x%04x antennas=%u nr_txpd=%u nr_mcast=%u wcb_base=0x%08x "
                "rxpd_rdptr=0x%08x rxpd_wrptr=0x%08x", spec.regioncode, spec.nr_antenna,
                spec.nr_txpd, spec.nr_mcast_adr, (unsigned)spec.wcb_base,
                (unsigned)spec.rxpd_rdptr, (unsigned)spec.rxpd_wrptr);
            say("hw_spec: mac=%02x:%02x:%02x:%02x:%02x:%02x", spec.mac[0], spec.mac[1],
                spec.mac[2], spec.mac[3], spec.mac[4], spec.mac[5]);
        }
    }
    if (!rc && mac)
        rc = set_mac(&c, mac);
    if (!rc) {
        mac_body[0] = MV_MAC_RX_ON | MV_MAC_TX_ON;
        mac_body[1] = mac_body[2] = mac_body[3] = 0;
        rc = run_command(&c, "MAC_CONTROL", MV_CMD_MAC_CONTROL, mac_body, sizeof(mac_body));
    }
    /* A probe response can be lost too, so an SSID that was not heard is
     * asked for again, up to SCAN_TRIES scans. */
    for (tries = 0; !rc && do_scan && tries < SCAN_TRIES && !scan_rc && !found; tries++) {
        scan_rc = scan(&c, &heard, ssid, &target, &found);
        say("scan: %s, %u descriptors in all", scan_rc ? "FAILED" : "complete", heard);
        if (!ssid)
            break;
        say("join: %u BSS%s with the SSID after scan %u", found, found == 1 ? "" : "s",
            tries + 1);
    }
    if (!rc && ssid && !scan_rc) {
        if (found)
            join_rc = join(&c, &target, bench);
        else {
            say("join: FAILED the SSID was not heard in %u scans", SCAN_TRIES);
            join_rc = -1;
        }
    }
    /* libertas sends this before removal; the power-down follows anyway. */
    if (!init_rc)
        shutdown_rc = run_command(&c, "FUNC_SHUTDOWN", MV_CMD_FUNC_SHUTDOWN, NULL, 0);
    stop_rc = mv8688_cmd_stop(&c);
    say("cmd: summary int_seen=0x%02x dnld_acks=%u packets=%u events=%u data=%u "
        "data_bad=%u tx_frames=%u stray=%u aborts=%u abort_failures=%u mask_off_rc=%d",
        c.int_seen, c.dnld_acks, c.packets, c.events, c.data_packets, c.data_bad,
        c.tx_frames, c.stray_responses, c.aborts, c.abort_failures, stop_rc);
    for (i = 0; i < c.event_n && i < MV_EVENTS; i++)
        say("cmd: event %u cause=0x%08x (%s)", i, (unsigned)c.event[i],
            event_name(c.event[i]));
    report_data(&c);
    return rc || scan_rc || join_rc || shutdown_rc || stop_rc ? -1 : 0;
}

int main(int argc, char **argv)
{
    struct pci_dev_info info;
    struct hbsdc host;
    struct sdio_card card;
    struct sigevent event;
    struct image helper, firmware;
    void *pci_dev = NULL;
    int pci_handle;
    uint64_t bar;
    uint32_t ctrl, power, sys_wlan, sys_bt, port = 0;
    uint16_t fw_status = 0;
    int option, guard_only = 0, check_only = 0, verdict = 0, downloading, started = 0;
    int run_commands = 0, set_mac_option = 0, do_scan = 0, timed = 0;
    const char *ssid = NULL, *station_ip = NULL, *peer_ip = NULL;
    static struct bench net;
    uint8_t ip[4], peer[4];
    static struct hbsdc_stats stats[3];     /* download, bench, commands */
    uint8_t rx_unit = 0, mac[6];
    static const int signals[] = { SIGALRM, SIGHUP, SIGINT, SIGTERM, SIGQUIT,
                                   SIGPIPE, SIGSEGV, SIGBUS, SIGILL, SIGFPE };
    unsigned i;

    memset(&helper, 0, sizeof(helper));
    memset(&firmware, 0, sizeof(firmware));
    while ((option = getopt(argc, argv, "ncpH:F:CM:LA:I:E:T")) != -1) {
        switch (option) {
        case 'n': guard_only = 1; break;
        case 'c': check_only = 1; break;
        case 'p': polling = 1; break;
        case 'H': helper.path = optarg; break;
        case 'F': firmware.path = optarg; break;
        case 'C': run_commands = 1; break;
        case 'M':
            set_mac_option = 1;
            if (mv8688_parse_mac(optarg, mac)) {
                fprintf(stderr, "%s: -M %s is not a usable station address\n", argv[0],
                        optarg);
                return 1;
            }
            break;
        case 'L': do_scan = 1; break;
        case 'A':
            ssid = optarg;
            if (!*ssid || strlen(ssid) > 32) {
                fprintf(stderr, "%s: -A needs an SSID of 1-32 bytes\n", argv[0]);
                return 1;
            }
            break;
        case 'I':
        case 'E':
            if (bench_parse_ip(optarg, option == 'I' ? ip : peer)) {
                fprintf(stderr, "%s: -%c %s is not a usable unicast IPv4 address\n",
                        argv[0], option, optarg);
                return 1;
            }
            if (option == 'I')
                station_ip = optarg;
            else
                peer_ip = optarg;
            break;
        case 'T': timed = 1; break;
        default:
            fprintf(stderr, "usage: %s [-n | -c] [-p] [-H helper -F firmware "
                    "[-C [-M mac [-L [-A ssid [-I ip -E peer]]]] [-T]]]\n", argv[0]);
            return 1;
        }
    }
    if (!helper.path != !firmware.path || optind != argc ||
        (check_only && (guard_only || helper.path)) ||
        (run_commands && (!firmware.path || guard_only)) ||
        (set_mac_option && !run_commands) || (do_scan && !set_mac_option) ||
        (ssid && !do_scan) || (timed && !run_commands) || !station_ip != !peer_ip ||
        (station_ip && (!ssid || !memcmp(ip, peer, 4)))) {
        fprintf(stderr, "usage: %s [-n | -c] [-p] [-H helper -F firmware "
                "[-C [-M mac [-L [-A ssid [-I ip -E peer]]]] [-T]]]\n", argv[0]);
        return 1;
    }
    if (station_ip)
        bench_init(&net, mac, ip, peer, (uint16_t)getpid());
    downloading = helper.path != NULL && !guard_only;
    for (i = 0; i < sizeof(signals) / sizeof(signals[0]); i++)
        signal(signals[i], emergency);
    alarm(downloading ? DOWNLOAD_DEADLINE : PROBE_DEADLINE);
    deadline_at = now_ms() + (downloading ? DOWNLOAD_DEADLINE : PROBE_DEADLINE) * 1000u;
    if (downloading && (load_image(&helper, 1) || load_image(&firmware, 0)))
        return 1;
    if (run_commands && !firmware.pin->station) {
        say("verdict=REFUSED -C needs the station image; %s is not it", firmware.path);
        return 1;
    }
    if (timed)
        ticks_hz = calibrate_ticks();

    if (ThreadCtl(_NTO_TCTL_IO, 0) == -1) {
        say("verdict=REFUSED ThreadCtl(_NTO_TCTL_IO) failed errno=%d; run as root", errno);
        return 1;
    }
    pci_handle = pci_attach(0);
    if (pci_handle == -1) {
        say("verdict=REFUSED pci_attach failed errno=%d", errno);
        return 1;
    }
    /* The factory call: PCI_SHARE, vendor/device filled in, no search flag. */
    memset(&info, 0, sizeof(info));
    info.VendorId = FPGA_VENDOR;
    info.DeviceId = FPGA_DEVICE;
    pci_dev = pci_attach_device(NULL, PCI_SHARE, 0, &info);
    if (!pci_dev) {
        say("verdict=REFUSED pci_attach_device(%04x:%04x) failed errno=%d",
            FPGA_VENDOR, FPGA_DEVICE, errno);
        return 1;
    }
    bar = info.CpuBaseAddress[0] & ~(uint64_t)0xf;
    say("pci: bar0=0x%08x size=0x%08x module=0x%08x", (unsigned)bar,
        (unsigned)info.BaseAddressSize[0], (unsigned)(bar + MODULE_OFFSET));
    if (info.BaseAddressSize[0] < MODULE_OFFSET + MODULE_SIZE) {
        say("verdict=REFUSED BAR0 is too small for the SDIO module");
        return 1;
    }
    sysregs = mmap_device_memory(NULL, SYSREG_SIZE, PROT_READ | PROT_NOCACHE,
                                 MAP_SHARED, bar);
    module = mmap_device_memory(NULL, MODULE_SIZE, PROT_READ | PROT_NOCACHE |
                                (guard_only ? 0 : PROT_WRITE), MAP_SHARED,
                                bar + MODULE_OFFSET);
    if (sysregs == MAP_FAILED || module == MAP_FAILED) {
        say("verdict=REFUSED mmap_device_memory failed errno=%d", errno);
        return 1;
    }

    ctrl = q_rd32(0, HBSDC_CTRL);
    power = q_rd32(0, HBSDC_POWER);
    sys_wlan = *(volatile uint32_t *)(sysregs + SYSREG_WLAN);
    sys_bt = *(volatile uint32_t *)(sysregs + SYSREG_BT);
    say("guard: ctrl=0x%08x stat=0x%08x flag=0x%08x ien=0x%08x power=0x%08x caps=0x%08x",
        ctrl, q_rd32(0, HBSDC_STAT), q_rd32(0, HBSDC_FLAG), q_rd32(0, HBSDC_IEN),
        power, q_rd32(0, HBSDC_CAPS));
    say("guard: sysreg8=0x%08x wlan_enable=%u sysreg0=0x%08x bt_reset=%u",
        sys_wlan, (sys_wlan >> 2) & 1, sys_bt, (sys_bt >> 2) & 1);
    if ((ctrl & (HBSDC_CTRL_ENABLE | HBSDC_CTRL_CLK_EN | HBSDC_CTRL_SDIO_EN)) ||
        (power & HBSDC_POWER_ON)) {
        say("verdict=REFUSED the module is in use (a driver is attached)");
        return 1;
    }
    if (sys_wlan & 4) {
        say("verdict=REFUSED WlanEnable is set: the card is held off and this "
            "probe never drives the reset line");
        return 1;
    }
    if (guard_only) {
        say("verdict=GUARD-PASS module idle; nothing written");
        return 0;
    }

    memset(&host, 0, sizeof(host));
    host.io = ticks_hz ? &qnx_io_timed : &qnx_io;
    if (hbsdc_identify(&host)) {
        say("verdict=REFUSED module index: id/major/change/caps not as expected "
            "(major=%u change=%u caps=0x%08x); only the index selector was written",
            host.major, host.change, host.caps);
        return 1;
    }
    writes_allowed = 1;
    say("module: id=0x53 major=%u clock=%u irq=%u (index 0x%02x) caps=0x%08x fifo=%u",
        host.major, (unsigned)host.clock_hz, host.irq, host.irq_index,
        (unsigned)host.caps, host.fifo_bytes);
    hbsdc_quiesce(&host);
    if (!polling) {
        if (host.irq_index == 0xff) {
            hbsdc_shutdown(&host);
            say("verdict=FAIL no auto-selected interrupt; rerun with -p");
            return 2;
        }
        irq_vector = host.irq;
        SIGEV_INTR_INIT(&event);
        irq_id = InterruptAttachEvent(irq_vector, &event, _NTO_INTR_FLAGS_TRK_MSK);
        if (irq_id == -1) {
            hbsdc_shutdown(&host);
            say("verdict=FAIL InterruptAttachEvent(%u) errno=%d; rerun with -p",
                irq_vector, errno);
            return 2;
        }
    }

    if (bring_up(&host, &card, &port, &fw_status, check_only ? "check: " : "")) {
        verdict = 2;
        if (check_only)
            say("check: FAILED to bring the card up; firmware state unknown");
    } else if (check_only) {
        if (fw_status == MV_FW_READY) {
            say("check: firmware still answers after the power-down");
            verdict = EXIT_FIRMWARE;
        } else {
            say("check: ready magic absent (fw_status=0x%04x)", fw_status);
        }
    } else if (downloading && fw_status == MV_FW_READY) {
        say("download: refused; firmware is already running (ready magic before the helper)");
        verdict = 2;
    } else if (downloading) {
        started = 1;
        host.stats = &stats[0];
        if (download(&card, port, &helper, &firmware, &rx_unit)) {
            verdict = 2;
            report_stats("download", &stats[0]);
        } else {
            report_stats("download", &stats[0]);
            /* The commands still run after a failed bench: they show
             * whether the firmware survived it. */
            host.stats = &stats[1];
            if (timed && bench(&card))
                verdict = 2;
            if (timed)
                report_stats("bench", &stats[1]);
            host.stats = &stats[2];
            if (run_commands && commands(&card, port, rx_unit,
                                         set_mac_option ? mac : NULL, do_scan, ssid,
                                         station_ip ? &net : NULL))
                verdict = 2;
            report_stats("commands", &stats[2]);
        }
        host.stats = NULL;
    }
    hbsdc_shutdown(&host);

    /* The factory attach skips its own download when it finds the ready
     * magic. Bring the card up again, as it would, and check it is gone. */
    if (started) {
        delay(10);
        hbsdc_quiesce(&host);
        if (bring_up(&host, &card, &port, &fw_status, "post_teardown: ")) {
            say("post_teardown: FAILED to bring the card up again; firmware state unknown");
            verdict = EXIT_FIRMWARE;
        } else if (fw_status == MV_FW_READY) {
            say("post_teardown: firmware SURVIVED the power-down; a factory attach would "
                "skip its download and drive this image as if it were its own");
            verdict = EXIT_FIRMWARE;
        } else {
            say("post_teardown: ready magic absent after the power-down (fw_status=0x%04x); "
                "a factory attach will download its own image", fw_status);
        }
        hbsdc_shutdown(&host);
    }
    writes_allowed = 0;
    if (irq_id != -1)
        InterruptDetach(irq_id);
    say("teardown: ctrl=0x%08x power=0x%08x ien=0x%08x", q_rd32(0, HBSDC_CTRL),
        q_rd32(0, HBSDC_POWER), q_rd32(0, HBSDC_IEN));
    say("irq: mode=%s interrupts=%u polled=%u changes=%u last_resp_index=0x%02x",
        polling ? "poll" : "interrupt", host.interrupts, host.polls, host.changes,
        host.resp_index);
    munmap_device_memory((void *)module, MODULE_SIZE);
    munmap_device_memory((void *)sysregs, SYSREG_SIZE);
    pci_detach_device(pci_dev);
    pci_detach(pci_handle);
    say("verdict=%s", verdict == EXIT_FIRMWARE ? "FIRMWARE" : verdict ? "FAIL" : "OK");
    return verdict;
}
