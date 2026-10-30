/*
 * FPGA SDIO host layer; see hbsdc.h. Command completion mirrors the factory
 * ISR: read FLAG, mask the fired bits in IEN, clear them, then decode.
 */
#include <string.h>
#include "hbsdc.h"

#define RD(h, off)          ((h)->io->rd32((h)->ctx, (off)))
#define WR(h, off, v)       ((h)->io->wr32((h)->ctx, (off), (v)))
#define RD8(h, off)         ((h)->io->rd8((h)->ctx, (off)))
#define WR8(h, off, v)      ((h)->io->wr8((h)->ctx, (off), (v)))

#define HBSDC_WAIT_MS       1000    /* factory sleepon timeout */
#define HBSDC_SLICE_MS      10      /* re-read FLAG this often */
#define HBSDC_SPURIOUS_MAX  8
#define HBSDC_DT_SPIN_MAX   10000   /* factory: 10001 x 1 us at most */

uint32_t hbsdc_cmd_word(uint8_t opcode, uint32_t arg)
{
    /* Bytes on the wire after the start bits: index, then arg MSB first. */
    return (uint32_t)(opcode | 0x40) | ((arg >> 24) & 0xff) << 8 |
           ((arg >> 16) & 0xff) << 16 | ((arg >> 8) & 0xff) << 24;
}

uint32_t hbsdc_cmdty(uint8_t resp, uint16_t xfer)
{
    uint32_t type = 0;
    if (resp & HBSDC_RSP_PRESENT) {
        if (resp & HBSDC_RSP_CRC)
            type |= 0x4;
        if (resp & HBSDC_RSP_BUSY)
            type |= 0xa;
        else if (resp & HBSDC_RSP_136)
            type = 0x3;     /* the factory drops the CRC bit here */
        else
            type |= 0x2;
    }
    if (xfer & HBSDC_XFER_DATA) {
        type |= (xfer & HBSDC_XFER_READ) ? 0x20 : 0x30;
        if (xfer & HBSDC_XFER_MULTI)
            type |= 0x80;
    }
    return type;
}

uint32_t hbsdc_response(uint32_t w0, uint32_t w1)
{
    return ((w0 >> 8) & 0xff) << 24 | ((w0 >> 16) & 0xff) << 16 |
           (w0 >> 24) << 8 | (w1 & 0xff);
}

static uint8_t hbsdc_index(struct hbsdc *h, uint8_t index)
{
    WR8(h, HBSDC_INDEX, index);
    return RD8(h, HBSDC_INDEX);
}

int hbsdc_identify(struct hbsdc *h)
{
    unsigned raw;
    if (hbsdc_index(h, 0) != HBSDC_MODULE_ID)
        return HBSDC_EINVAL;
    h->major = hbsdc_index(h, 1);
    h->irq_index = hbsdc_index(h, 0xf);
    h->irq = h->irq_index + (h->major < 5 && h->irq_index == 0xc);
    if (h->major < 3) {
        h->change = hbsdc_index(h, 3);
        if (h->change < 5)
            return HBSDC_EINVAL;
        h->clock_hz = 66000000;
    } else {
        raw = (unsigned)hbsdc_index(h, 9) << 8;
        raw |= hbsdc_index(h, 8);
        h->clock_hz = raw == 0xffff ? 99000000 : raw * 10000;
    }
    h->caps = RD(h, HBSDC_CAPS);
    h->fifo_bytes = 256u << ((h->caps >> 8) & 3);
    return (h->caps & 2) ? HBSDC_OK : HBSDC_EINVAL;
}

void hbsdc_powerdown(struct hbsdc *h)
{
    /* The reset line (system register 8) is deliberately never driven. */
    WR(h, HBSDC_POWER, 0);
}

void hbsdc_quiesce(struct hbsdc *h)
{
    hbsdc_powerdown(h);
    WR(h, HBSDC_IEN, 0);
    WR8(h, HBSDC_CLKDIV, 0xa5);
    WR8(h, 0x21, 0xff);
    WR8(h, 0x24, 0xff);
    WR8(h, 0x25, 0xff);
}

int hbsdc_detect(struct hbsdc *h, uint32_t *raw)
{
    uint32_t power = RD(h, HBSDC_POWER);
    if (raw)
        *raw = power;
    if (power & HBSDC_POWER_CHANGE) {
        /* As the factory: acknowledge the change and fail this detect. */
        WR(h, HBSDC_POWER, power);
        h->changes++;
        return HBSDC_ECHANGE;
    }
    return (power & HBSDC_POWER_NO_CARD) ? HBSDC_ECARD : HBSDC_OK;
}

int hbsdc_powerup(struct hbsdc *h)
{
    if (RD(h, HBSDC_POWER) & HBSDC_POWER_NO_CARD)
        return HBSDC_ECARD;
    WR(h, HBSDC_CTRL, HBSDC_CTRL_DEV_RESET);
    h->io->delay_ms(h->ctx, 1);
    WR(h, HBSDC_CTRL, 0);
    WR(h, HBSDC_CTRL, HBSDC_CTRL_ENABLE);
    WR(h, HBSDC_CTRL, RD(h, HBSDC_CTRL) | HBSDC_CTRL_SDIO_EN);
    WR(h, HBSDC_CTRL, RD(h, HBSDC_CTRL) | HBSDC_CTRL_CR_CLR);
    WR(h, HBSDC_CTRL, RD(h, HBSDC_CTRL) | HBSDC_CTRL_DATA_CLR);
    WR8(h, 0x21, 0xff);
    WR8(h, 0x24, 0xff);
    WR8(h, 0x25, 0xff);
    WR(h, HBSDC_CTRL, RD(h, HBSDC_CTRL) | HBSDC_CTRL_CLK_EN);
    WR(h, HBSDC_POWER, RD(h, HBSDC_POWER) | HBSDC_POWER_ON);
    WR(h, HBSDC_IEN, 0);
    return HBSDC_OK;
}

int hbsdc_set_clock(struct hbsdc *h, unsigned hz)
{
    unsigned div;
    if (!hz || !h->clock_hz)
        return HBSDC_EINVAL;
    WR(h, HBSDC_CTRL, RD(h, HBSDC_CTRL) & ~(HBSDC_CTRL_HS | HBSDC_CTRL_FAST_CLK));
    div = (h->clock_hz + hz - 1) / hz >> 1;
    if (div < 2 && h->clock_hz > 66000000) {
        WR8(h, HBSDC_CLKDIV, div);
        WR(h, HBSDC_CTRL, RD(h, HBSDC_CTRL) | HBSDC_CTRL_HS);
        h->bus_hz = 49500000;
    } else if (div < 3) {
        WR8(h, HBSDC_CLKDIV, div);
        WR(h, HBSDC_CTRL, RD(h, HBSDC_CTRL) | HBSDC_CTRL_FAST_CLK);
        h->bus_hz = 25000000;
    } else {
        /* Divided clock; the divider value is inferred, not traced. */
        if (h->clock_hz < hz || hz < h->clock_hz / 0xff)
            return HBSDC_EINVAL;
        if (div > 0xff)
            div = 0xff;
        WR8(h, HBSDC_CLKDIV, div);
        h->bus_hz = h->clock_hz / (div * 2);
    }
    return HBSDC_OK;
}

int hbsdc_set_width(struct hbsdc *h, unsigned bits)
{
    if (bits == 4)
        WR(h, HBSDC_CTRL, RD(h, HBSDC_CTRL) | HBSDC_CTRL_MODE4);
    else if (bits == 1)
        WR(h, HBSDC_CTRL, RD(h, HBSDC_CTRL) & ~HBSDC_CTRL_MODE4);
    else
        return HBSDC_EINVAL;
    return HBSDC_OK;
}

int hbsdc_set_blksz(struct hbsdc *h, unsigned size)
{
    if (size < 1 || size > 512)
        return HBSDC_EINVAL;
    WR(h, HBSDC_BLKSZ, size);
    return HBSDC_OK;
}

static int hbsdc_wait(struct hbsdc *h)
{
    unsigned spurious = 0, waited = 0;
    uint32_t power, flags;
    int fired;
    h->flags = 0;
    h->by_irq = 0;
    for (;;) {
        /* Short slices, so a latched FLAG without an interrupt completes
         * in about 10 ms, not after the whole 1 s wait. */
        fired = h->io->wait_irq(h->ctx, HBSDC_SLICE_MS) == 0;
        power = RD(h, HBSDC_POWER);
        if (power & HBSDC_POWER_CHANGE) {
            WR(h, HBSDC_POWER, power);
            h->changes++;
        }
        flags = RD(h, HBSDC_FLAG) & 0xffff;
        if (flags) {
            WR(h, HBSDC_IEN, RD(h, HBSDC_IEN) & ~flags);
            WR(h, HBSDC_FLAG, flags);
        }
        if (fired) {
            h->interrupts++;
            h->io->irq_done(h->ctx);
        }
        h->flags |= flags;
        if (h->flags & (HBSDC_FLAG_CMD_RDY | HBSDC_FLAG_ERRORS)) {
            h->by_irq = fired;
            if (!fired)
                h->polls++;
            return HBSDC_OK;
        }
        if (fired ? ++spurious > HBSDC_SPURIOUS_MAX
                  : (waited += HBSDC_SLICE_MS) >= HBSDC_WAIT_MS)
            return HBSDC_ETIMEDOUT;
    }
}

static struct hbsdc_stats *timing(struct hbsdc *h)
{
    return h->io->ticks ? h->stats : NULL;
}

static uint32_t ticks(struct hbsdc *h)
{
    return h->io->ticks(h->ctx);
}

static int issue(struct hbsdc *h, struct hbsdc_cmd *cmd)
{
    uint32_t ctrl, w0, w1;
    int rc;
    if (cmd->resp & HBSDC_RSP_136)
        return HBSDC_EINVAL;    /* SDIO never needs R2 */
    if (RD(h, HBSDC_STAT) & (HBSDC_STAT_BUSY | HBSDC_STAT_DT_RUN))
        return HBSDC_EBUSY;
    ctrl = RD(h, HBSDC_CTRL);
    WR(h, HBSDC_FLAG, 0xfeff);
    WR(h, HBSDC_CTRL, ctrl | HBSDC_CTRL_CR_CLR);
    WR(h, HBSDC_CMD, hbsdc_cmd_word(cmd->opcode, cmd->arg));
    WR(h, HBSDC_CMD, cmd->arg & 0xff);
    WR(h, HBSDC_CMDTY, hbsdc_cmdty(cmd->resp, cmd->xfer));
    WR(h, HBSDC_CTRL, ctrl | HBSDC_CTRL_NEXT_CMD);
    WR(h, HBSDC_CTRL, ctrl | HBSDC_CTRL_START);
    WR(h, HBSDC_IEN, HBSDC_IEN_COMMAND);
    rc = hbsdc_wait(h);
    if (rc)
        return rc;
    if ((h->flags & HBSDC_FLAG_CMD_RDY) && (cmd->resp & HBSDC_RSP_PRESENT)) {
        w0 = RD(h, HBSDC_RESP);
        w1 = RD(h, HBSDC_RESP);
        cmd->response = hbsdc_response(w0, w1);
        h->resp_index = w0 & 0xff;
        WR(h, HBSDC_CTRL, RD(h, HBSDC_CTRL) | HBSDC_CTRL_NEXT_RESP);
    }
    return (h->flags & HBSDC_FLAG_ERRORS) ? HBSDC_ECARD : HBSDC_OK;
}

int hbsdc_command(struct hbsdc *h, struct hbsdc_cmd *cmd)
{
    struct hbsdc_stats *s = timing(h);
    uint32_t t0 = s ? ticks(h) : 0, t;
    int rc = issue(h, cmd);
    if (s) {
        t = ticks(h) - t0;
        if (rc) {
            s->failures++;
        } else {
            s->cmd_n++;
            s->cmd_ticks += t;
            if (t > s->cmd_max)
                s->cmd_max = t;
        }
    }
    return rc;
}

static void record(struct hbsdc_stats *s, int read, unsigned blocks, unsigned blksz,
                   uint32_t setup, uint32_t data, uint32_t drain)
{
    struct hbsdc_phase *p;
    p = &s->xfer[read][(blocks < HBSDC_BUCKETS ? blocks : HBSDC_BUCKETS) - 1];
    p->n++;
    p->bytes += blocks * blksz;
    p->setup += setup;
    p->data += data;
    p->drain += drain;
    if (data > p->data_max)
        p->data_max = data;
    if (setup + data + drain > p->total_max)
        p->total_max = setup + data + drain;
}

static int transfer(struct hbsdc *h, struct hbsdc_cmd *cmd, void *buf,
                    unsigned blocks, unsigned blksz, struct hbsdc_stats *s)
{
    unsigned char *p = buf;
    unsigned words, spin;
    uint32_t word, flags, t0 = 0, t1 = 0, t2 = 0;
    int rc, read = (cmd->xfer & HBSDC_XFER_READ) != 0;
    if (s)
        t0 = ticks(h);
    WR(h, HBSDC_CTRL, RD(h, HBSDC_CTRL) | HBSDC_CTRL_DATA_CLR);
    if (read)
        WR(h, HBSDC_CTRL, RD(h, HBSDC_CTRL) | HBSDC_CTRL_READ);
    else
        WR(h, HBSDC_CTRL, RD(h, HBSDC_CTRL) & ~HBSDC_CTRL_READ);
    WR(h, HBSDC_BLKSZ, blksz);
    WR(h, HBSDC_BLKCNT, blocks);
    cmd->xfer |= HBSDC_XFER_DATA;
    if (blocks > 1)
        cmd->xfer |= HBSDC_XFER_MULTI;
    rc = issue(h, cmd);
    if (rc)
        return rc;
    if (s)
        t1 = ticks(h);
    for (words = blocks * blksz / 4; words; words--, p += 4) {
        if (read) {
            word = RD(h, HBSDC_DATA);
            memcpy(p, &word, 4);
        } else {
            memcpy(&word, p, 4);
            WR(h, HBSDC_DATA, word);
        }
    }
    if (s)
        t2 = ticks(h);
    WR(h, HBSDC_CMDTY, RD(h, HBSDC_CMDTY) & ~0x80u);
    for (spin = 0; RD(h, HBSDC_STAT) & HBSDC_STAT_DT_RUN; spin++) {
        if (spin > HBSDC_DT_SPIN_MAX) {
            WR(h, HBSDC_FLAG, HBSDC_FLAG_DT_RDY);
            return HBSDC_EDATA;
        }
        h->io->spin_us(h->ctx, 1);
    }
    WR(h, HBSDC_FLAG, HBSDC_FLAG_DT_RDY);
    /* A data-phase error can latch after the command completed. */
    flags = RD(h, HBSDC_FLAG) & HBSDC_FLAG_DATA_ERRORS;
    if (flags) {
        WR(h, HBSDC_FLAG, flags);
        h->flags |= flags;
        return HBSDC_EDATA;
    }
    if (s)
        record(s, read, blocks, blksz, t1 - t0, t2 - t1, ticks(h) - t2);
    return HBSDC_OK;
}

int hbsdc_transfer(struct hbsdc *h, struct hbsdc_cmd *cmd, void *buf,
                   unsigned blocks, unsigned blksz)
{
    struct hbsdc_stats *s;
    int rc;
    if (!blocks || blocks > 511 || blksz < 4 || blksz > 512 || (blksz & 3))
        return HBSDC_EINVAL;
    s = timing(h);
    rc = transfer(h, cmd, buf, blocks, blksz, s);
    if (rc && s)
        s->failures++;
    return rc;
}

void hbsdc_shutdown(struct hbsdc *h)
{
    WR(h, HBSDC_IEN, 0);
    WR(h, HBSDC_CTRL, HBSDC_CTRL_DEV_RESET);
    h->io->delay_ms(h->ctx, 1);
    hbsdc_powerdown(h);
}
