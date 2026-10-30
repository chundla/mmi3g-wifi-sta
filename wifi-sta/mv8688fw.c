/*
 * 88W8688 helper and firmware download; see mv8688fw.h. Bounds and delays
 * follow the factory where it has them. Where it does not, or where it
 * treats a stall as success, this fails instead and says which step.
 */
#include <string.h>
#include "mv8688fw.h"

#define MV_READY_READS      100     /* factory: 100 reads, 10 us apart */
#define MV_READY_SPIN_US    10
#define MV_ZERO_POLLS       1000    /* 1 ms apart; the factory gives up sooner */
#define MV_CRC_RETRIES      2
#define MV_FW_POLL_MS       10

static int fail(struct mv8688_dl *dl, const char *step, int rc)
{
    dl->step = step;
    return rc;
}

static uint32_t crc_byte(uint32_t crc, uint8_t b)
{
    int i;
    crc ^= (uint32_t)b << 24;
    for (i = 0; i < 8; i++)
        crc = (crc & 0x80000000u) ? crc << 1 ^ 0x04c11db7u : crc << 1;
    return crc;
}

uint32_t mv8688_cksum(const void *data, uint32_t size)
{
    const uint8_t *p = data;
    uint32_t crc = 0, i, n;
    for (i = 0; i < size; i++)
        crc = crc_byte(crc, p[i]);
    for (n = size; n; n >>= 8)
        crc = crc_byte(crc, (uint8_t)n);
    return ~crc;
}

static const struct mv8688_pinned pinned[] = {
    { 2616, 1538620194u, 1, 0, "helper: factory helper_sd.bin = linux-firmware sd8688_helper.bin" },
    { 259172, 251454034u, 0, 1, "station: linux-firmware mrvl/sd8688.bin" },
    { 307396, 3281661138u, 0, 0, "uAP: factory sd8688_ap.bin" },
};

const struct mv8688_pinned *mv8688_pinned_image(uint32_t size, uint32_t sum, int helper)
{
    unsigned i;
    for (i = 0; i < sizeof(pinned) / sizeof(pinned[0]); i++)
        if (pinned[i].size == size && pinned[i].sum == sum && pinned[i].helper == helper)
            return &pinned[i];
    return NULL;
}

static int read16(struct sdio_card *card, unsigned addr, uint16_t *value)
{
    uint8_t lo, hi;
    int rc;
    if ((rc = sdio_cmd52(card, 1, addr, 0, 0, &lo)) ||
        (rc = sdio_cmd52(card, 1, addr + 1, 0, 0, &hi)))
        return rc;
    *value = lo | (uint16_t)hi << 8;
    return 0;
}

int mv8688_fw_status(struct sdio_card *card, uint16_t *status)
{
    return read16(card, MV_REG_SCRATCH, status);
}

int mv8688_ioport(struct sdio_card *card, uint32_t *port)
{
    uint8_t b;
    unsigned i;
    int rc;
    *port = 0;
    for (i = 0; i < 3; i++) {
        if ((rc = sdio_cmd52(card, 1, MV_REG_IOPORT + i, 0, 0, &b)))
            return rc;
        *port |= (uint32_t)b << (8 * i);
    }
    return 0;
}

/* Factory FUN_0001abe0: up to 100 status reads for IO_RDY | DL_RDY. */
static int wait_dl_ready(struct mv8688_dl *dl)
{
    struct hbsdc *h = dl->card->host;
    uint8_t status;
    unsigned reads;
    int rc;
    for (reads = 1; reads <= MV_READY_READS; reads++) {
        if ((rc = sdio_cmd52(dl->card, 1, MV_REG_STATUS, 0, 0, &status)))
            return rc;
        if ((status & MV_STATUS_DL_READY) == MV_STATUS_DL_READY) {
            if (reads > dl->ready_reads_max)
                dl->ready_reads_max = reads;
            return 0;
        }
        h->io->spin_us(h->ctx, MV_READY_SPIN_US);
    }
    dl->ready_reads_max = MV_READY_READS;
    return MV_ENOTREADY;
}

static int write_blocks(struct mv8688_dl *dl, unsigned blocks)
{
    /* Block mode, fixed address: every block goes to the I/O port. */
    return sdio_cmd53(dl->card, 1, dl->ioport, 0, 1, dl->buf, blocks, MV_BLKSZ);
}

int mv8688_prog_helper(struct mv8688_dl *dl, const uint8_t *image, uint32_t size)
{
    uint32_t offset = 0, n;
    int rc;
    if (!size)
        return fail(dl, "helper_image", HBSDC_EINVAL);
    /* Diagnostic only: what the request register holds under the boot ROM. */
    dl->rd_base_pre_rc = read16(dl->card, MV_REG_RD_BASE, &dl->rd_base_pre);
    for (;;) {
        if ((rc = wait_dl_ready(dl)))
            return fail(dl, "helper_ready", rc);
        if (offset >= size)
            break;
        n = size - offset < MV_HELPER_CHUNK ? size - offset : MV_HELPER_CHUNK;
        /* As the factory: only the length and the data are written, so a
         * short last chunk is padded with the previous chunk's tail. */
        dl->buf[0] = (uint8_t)n;
        dl->buf[1] = (uint8_t)(n >> 8);
        memcpy(dl->buf + 4, image + offset, n);
        if ((rc = write_blocks(dl, 2)))
            return fail(dl, "helper_write", rc);
        dl->helper_chunks++;
        offset += n;
    }
    memset(dl->buf, 0, MV_BLKSZ);
    if ((rc = write_blocks(dl, 1)))
        return fail(dl, "helper_end", rc);
    return 0;
}

/* The helper asks for each chunk by length; bit 0 set asks for a resend. */
static int read_request(struct mv8688_dl *dl, uint16_t *len)
{
    struct hbsdc *h = dl->card->host;
    unsigned polls;
    int rc;
    for (polls = 0; polls < MV_ZERO_POLLS; polls++) {
        if ((rc = read16(dl->card, MV_REG_RD_BASE, len)))
            return rc;
        dl->last_request = *len;
        if (*len) {
            if (polls > dl->zero_polls_max)
                dl->zero_polls_max = polls;
            return 0;
        }
        h->io->delay_ms(h->ctx, 1);
    }
    dl->zero_polls_max = polls;
    return MV_ESTALL;
}

static void trace(struct mv8688_dl *dl, unsigned poll, uint16_t value)
{
    if (dl->trace_n && dl->trace_value[dl->trace_n - 1] == value)
        return;
    if (dl->trace_n < MV_TRACE) {
        dl->trace_poll[dl->trace_n] = (uint16_t)poll;
        dl->trace_value[dl->trace_n] = value;
    }
    dl->trace_n++;
}

/* Before the first chunk there is nothing to resend, and the helper may
 * still be starting: the factory reads the first request only after opening
 * the firmware file, and the first run on the unit (2026-10-28) read an odd
 * value at once. Until a chunk is asked for, zero and odd both mean "not
 * yet"; nothing is written and each change is recorded. */
static int read_first_request(struct mv8688_dl *dl, uint16_t *len)
{
    struct hbsdc *h = dl->card->host;
    unsigned polls;
    int rc;
    for (polls = 0; polls < MV_ZERO_POLLS; polls++) {
        if ((rc = read16(dl->card, MV_REG_RD_BASE, len)))
            return rc;
        dl->last_request = *len;
        trace(dl, polls, *len);
        if (*len && !(*len & 1)) {
            dl->first_polls = polls;
            return 0;
        }
        dl->early_odd += *len & 1;
        h->io->delay_ms(h->ctx, 1);
    }
    dl->first_polls = polls;
    return MV_ESTALL;
}

int mv8688_prog_fw(struct mv8688_dl *dl, const uint8_t *image, uint32_t size)
{
    uint32_t offset = 0, n;
    unsigned crc_tries = 0;
    uint16_t len;
    uint8_t config;
    int rc;
    if (!size)
        return fail(dl, "fw_image", HBSDC_EINVAL);
    dl->buf_blocks = 0;
    for (;;) {
        if ((rc = wait_dl_ready(dl)))
            return fail(dl, "fw_ready", rc);
        if (offset >= size)
            return 0;
        /* The factory takes a stall here for the end of the image and
         * leaves the ready-magic check to decide; this reports it. */
        if (!dl->buf_blocks) {
            if ((rc = read_first_request(dl, &len)))
                return fail(dl, "fw_first_request", rc);
        } else if ((rc = read_request(dl, &len))) {
            return fail(dl, "fw_request", rc);
        }
        if (len > MV_FW_REQUEST_MAX)
            return fail(dl, "fw_request", MV_EREQUEST);
        dl->requests++;
        if (len & 1) {
            /* Resend the buffer as it is; the offset already moved past it.
             * Never a block count of 0, which block mode takes as open-ended. */
            dl->crc_retries++;
            if (++crc_tries > MV_CRC_RETRIES || !dl->buf_blocks)
                return fail(dl, "fw_crc", MV_ECRC);
            n = 0;
        } else {
            crc_tries = 0;
            if (dl->req_min == 0 || len < dl->req_min)
                dl->req_min = len;
            if (len > dl->req_max)
                dl->req_max = len;
            n = size - offset < len ? size - offset : len;
            dl->buf_blocks = (n + MV_BLKSZ - 1) / MV_BLKSZ;
            /* Padding to whole blocks is whatever the buffer held, as in
             * the factory; the receiver's use of it is not known. */
            memcpy(dl->buf, image + offset, n);
        }
        if (write_blocks(dl, dl->buf_blocks)) {
            /* As the factory: terminate the transfer and carry on; the
             * card reports the lost chunk through the CRC bit. */
            dl->aborts++;
            if (sdio_cmd52(dl->card, 1, MV_REG_CONFIG, 1, MV_CONFIG_ABORT, &config))
                dl->abort_failures++;
        }
        offset += n;
        dl->sent = offset;
    }
}

int mv8688_wait_fw(struct mv8688_dl *dl, unsigned tries)
{
    struct hbsdc *h = dl->card->host;
    int rc;
    for (dl->fw_polls = 1; dl->fw_polls <= tries; dl->fw_polls++) {
        rc = mv8688_fw_status(dl->card, &dl->fw_status);
        if (rc == 0 && dl->fw_status == MV_FW_READY) {
            /* The factory ignores a failed RX unit read; so does this. */
            dl->rx_unit_rc = sdio_cmd52(dl->card, 1, MV_REG_RX_UNIT, 0, 0, &dl->rx_unit);
            return 0;
        }
        /* As the factory: a failed read is retried at once, a read that
         * worked but showed no magic waits 10 ms. */
        if (rc == 0 && dl->fw_polls < tries)
            h->io->delay_ms(h->ctx, MV_FW_POLL_MS);
    }
    dl->fw_polls = tries;
    return fail(dl, "fw_status", MV_ENOFW);
}
