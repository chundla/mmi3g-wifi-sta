/*
 * SDIO card layer; see sdio.h. Retry counts and delays follow the factory
 * driver where it has them; failures stop early instead of waiting out the
 * factory's 100 one-second command timeouts.
 */
#include <string.h>
#include "sdio.h"

#define SDIO_CMD52_TRIES    5
#define SDIO_CMD5_TRIES     100
#define SDIO_CMD5_TIMEOUTS  3
#define SDIO_OCR_GROUPS     5
#define SDIO_READY_MS       100
#define SDIO_CIS_SPAN       512
#define SDIO_CIS_TUPLES     64

#define SDIO_R5_ERRORS      0xcb00  /* CRC, illegal command, error, range */
#define SDIO_R5_STATE       0x3000

static int fail(struct sdio_card *card, const char *step, int rc)
{
    card->step = step;
    return rc;
}

static int sdio_check_r5(struct sdio_card *card, uint32_t r5)
{
    card->r5 = r5;
    if ((r5 & SDIO_R5_ERRORS) || !(r5 & SDIO_R5_STATE))
        return SDIO_ER5;
    return 0;
}

int sdio_cmd52(struct sdio_card *card, unsigned fn, unsigned addr, int write,
               uint8_t value, uint8_t *out)
{
    struct hbsdc_cmd cmd;
    int rc = HBSDC_EINVAL, tries;
    if (fn > SDIO_MAX_FUNC || addr > 0x1ffff)
        return HBSDC_EINVAL;
    for (tries = 0; tries < SDIO_CMD52_TRIES; tries++) {
        memset(&cmd, 0, sizeof(cmd));
        cmd.opcode = 52;
        cmd.resp = HBSDC_RSP_R1;
        cmd.arg = (uint32_t)fn << 28 | (uint32_t)addr << 9;
        if (write)
            cmd.arg |= 0x80000000u | value;
        rc = hbsdc_command(card->host, &cmd);
        if (rc == 0)
            break;
    }
    if (rc)
        return rc;
    rc = sdio_check_r5(card, cmd.response);
    if (rc == 0 && out)
        *out = cmd.response & 0xff;
    return rc;
}

static int sdio_cmd5(struct sdio_card *card, uint32_t arg, uint32_t *ocr)
{
    struct hbsdc_cmd cmd;
    unsigned tries, timeouts = 0;
    int rc = SDIO_ENOTREADY;
    for (tries = 0; tries < SDIO_CMD5_TRIES; tries++) {
        memset(&cmd, 0, sizeof(cmd));
        cmd.opcode = 5;
        cmd.resp = HBSDC_RSP_R4;
        cmd.arg = arg;
        rc = hbsdc_command(card->host, &cmd);
        if (rc == 0 && (cmd.response & 0x80000000u)) {
            *ocr = cmd.response;
            return 0;
        }
        if (rc == HBSDC_ETIMEDOUT && ++timeouts >= SDIO_CMD5_TIMEOUTS)
            return rc;
        if (rc)
            card->host->io->delay_ms(card->host->ctx, 1);
    }
    return rc ? rc : SDIO_ENOTREADY;
}

static int sdio_r1(struct sdio_card *card, uint8_t opcode, uint32_t arg,
                   uint32_t *response)
{
    struct hbsdc_cmd cmd;
    int rc;
    memset(&cmd, 0, sizeof(cmd));
    cmd.opcode = opcode;
    cmd.resp = HBSDC_RSP_R1;
    cmd.arg = arg;
    rc = hbsdc_command(card->host, &cmd);
    if (rc == 0)
        *response = cmd.response;
    return rc;
}

static int sdio_read24(struct sdio_card *card, unsigned addr, uint32_t *value)
{
    uint8_t b[3];
    int i, rc;
    for (i = 0; i < 3; i++) {
        rc = sdio_cmd52(card, 0, addr + i, 0, 0, &b[i]);
        if (rc)
            return rc;
    }
    *value = b[0] | (uint32_t)b[1] << 8 | (uint32_t)b[2] << 16;
    return 0;
}

static void sdio_append(char *dst, unsigned size, unsigned *used, char c)
{
    if (*used + 1 < size) {
        dst[(*used)++] = c;
        dst[*used] = 0;
    }
}

/* Walks the common CIS for MANFID (0x20) and VERS_1 (0x15). */
static int sdio_read_cis(struct sdio_card *card)
{
    unsigned p = card->cis[0], end, n, i, used = 0;
    uint8_t code, link, b[4];
    int rc, have_id = 0;
    if (p == 0 || p > 0x1ffff - SDIO_CIS_SPAN)
        return SDIO_EID;
    end = p + SDIO_CIS_SPAN;
    card->manf[0] = 0;
    for (n = 0; n < SDIO_CIS_TUPLES && p < end; n++) {
        if ((rc = sdio_cmd52(card, 0, p, 0, 0, &code)))
            return rc;
        if (code == 0xff)
            break;
        if (code == 0x00) {
            p++;
            continue;
        }
        if ((rc = sdio_cmd52(card, 0, p + 1, 0, 0, &link)))
            return rc;
        if (link == 0xff)
            break;
        if (code == 0x20 && link >= 4) {
            for (i = 0; i < 4; i++)
                if ((rc = sdio_cmd52(card, 0, p + 2 + i, 0, 0, &b[i])))
                    return rc;
            card->vendor = b[0] | b[1] << 8;
            card->device = b[2] | b[3] << 8;
            have_id = 1;
        } else if (code == 0x15 && link > 2) {
            /* Version bytes first, then NUL-separated strings, 0xff ends. */
            for (i = 2; i < link; i++) {
                if ((rc = sdio_cmd52(card, 0, p + 2 + i, 0, 0, &b[0])))
                    return rc;
                if (b[0] == 0xff)
                    break;
                if (b[0] == 0)
                    sdio_append(card->manf, sizeof(card->manf), &used, '/');
                else if (b[0] >= 0x20 && b[0] < 0x7f)
                    sdio_append(card->manf, sizeof(card->manf), &used, b[0]);
            }
            while (used && card->manf[used - 1] == '/')
                card->manf[--used] = 0;
        }
        p += 2 + link;
    }
    return have_id ? 0 : SDIO_EID;
}

int sdio_attach(struct sdio_card *card, uint16_t vendor, uint16_t device)
{
    struct hbsdc *h = card->host;
    uint32_t response, ocr;
    uint8_t mask, value;
    unsigned fn, waited, group;
    int rc;
    static const uint8_t cccr_regs[] = { 0x00, 0x01, 0x03, 0x08, 0x0d, 0x0e,
                                         0x0f, 0x13 };

    /* A pending change event fails the factory detect. Once it is
     * acknowledged, a second read reflects the card as it is now. */
    rc = hbsdc_detect(h, &card->power_raw);
    if (rc == HBSDC_ECHANGE)
        rc = hbsdc_detect(h, NULL);
    if (rc)
        return fail(card, "detect", rc);
    hbsdc_powerdown(h);
    h->io->delay_ms(h->ctx, 10);
    if ((rc = hbsdc_powerup(h)))
        return fail(card, "powerup", rc);
    h->io->delay_ms(h->ctx, 10);

    /* The factory rereads the OCR up to five times while it shows no
     * voltage window, then accepts it; this refuses it below. */
    for (group = 0; ; group++) {
        if ((rc = sdio_cmd5(card, 0, &ocr)))
            return fail(card, "read_ocr", rc);
        if ((ocr & 0xffff00) || group + 1 >= SDIO_OCR_GROUPS)
            break;
    }
    card->ocr = ocr;
    card->nfunc = (ocr >> 28) & 7;
    if (!(ocr & 0xffff00) || card->nfunc == 0)
        return fail(card, "read_ocr", SDIO_EID);
    if ((rc = sdio_cmd5(card, ocr & 0xffffff, &ocr)))
        return fail(card, "write_ocr", rc);

    /* The factory reissues CMD3 once after a failure that left no RCA.
     * This reissues it after a failure or a zero RCA, and needs one. */
    response = 0;
    rc = sdio_r1(card, 3, 0, &response);
    if (rc || (response >> 16) == 0) {
        response = 0;
        rc = sdio_r1(card, 3, 0, &response);
    }
    if (rc)
        return fail(card, "read_rca", rc);
    card->rca = response >> 16;
    if (card->rca == 0)
        return fail(card, "read_rca", SDIO_EID);
    if ((rc = sdio_r1(card, 7, (uint32_t)card->rca << 16, &response)))
        return fail(card, "select_card", rc);

    mask = (uint8_t)(((1u << (card->nfunc + 1)) - 2) & 0xfe);
    if ((rc = sdio_cmd52(card, 0, SDIO_CCCR_IOEX, 1, mask, NULL)) ||
        (rc = sdio_cmd52(card, 0, SDIO_CCCR_IOEX, 0, 0, &value)))
        return fail(card, "enable_all", rc);
    if ((rc = sdio_cmd52(card, 0, SDIO_CCCR_IENX, 1, mask | 1, NULL)) ||
        (rc = sdio_cmd52(card, 0, SDIO_CCCR_IENX, 0, 0, &value)))
        return fail(card, "enable_int", rc);
    card->cccr[SDIO_CCCR_IENX] = value;
    for (fn = 0; fn < sizeof(cccr_regs); fn++)
        if ((rc = sdio_cmd52(card, 0, cccr_regs[fn], 0, 0,
                             &card->cccr[cccr_regs[fn]])))
            return fail(card, "read_cccr", rc);
    /* The factory reads IORx once; record how long readiness took. */
    for (waited = 0; (card->cccr[SDIO_CCCR_IORX] & mask) != mask; waited++) {
        if (waited >= SDIO_READY_MS)
            break;
        h->io->delay_ms(h->ctx, 1);
        if ((rc = sdio_cmd52(card, 0, SDIO_CCCR_IORX, 0, 0,
                             &card->cccr[SDIO_CCCR_IORX])))
            return fail(card, "read_ior", rc);
    }
    card->ready_ms = waited;

    card->width = (h->caps & 1) ? 4 : 1;
    if ((card->cccr[SDIO_CCCR_CAPS] & 0x40) && !(card->cccr[SDIO_CCCR_CAPS] & 0x80))
        card->width = 1;
    if ((rc = sdio_cmd52(card, 0, SDIO_CCCR_BUS_IF, 1,
                         (uint8_t)(card->width >> 1 | 0x80), NULL)) ||
        (rc = sdio_cmd52(card, 0, SDIO_CCCR_BUS_IF, 0, 0,
                         &card->cccr[SDIO_CCCR_BUS_IF])))
        return fail(card, "bus_width", rc);

    for (fn = 0; fn <= card->nfunc; fn++) {
        if ((rc = sdio_read24(card, fn * 0x100 + SDIO_CCCR_CIS, &card->cis[fn])))
            return fail(card, "read_fbr", rc);
        if (fn && (rc = sdio_cmd52(card, 0, fn * 0x100, 0, 0, &card->fn_code[fn])))
            return fail(card, "read_fbr", rc);
    }
    if ((rc = sdio_read_cis(card)))
        return fail(card, "read_cis", rc);
    if ((rc = hbsdc_set_width(h, card->width)))
        return fail(card, "host_width", rc);
    if (card->vendor != vendor || card->device != device)
        return fail(card, "identity", SDIO_EID);
    return 0;
}

int sdio_set_blksz(struct sdio_card *card, unsigned fn, unsigned size)
{
    uint8_t value;
    unsigned base = fn * 0x100 + 0x10, i;
    int rc;
    if (fn > card->nfunc || size < 1 || size > 512)
        return HBSDC_EINVAL;
    for (i = 0; i < 2; i++) {
        uint8_t want = (uint8_t)(size >> (8 * i));
        if ((rc = sdio_cmd52(card, 0, base + i, 1, want, NULL)) ||
            (rc = sdio_cmd52(card, 0, base + i, 0, 0, &value)))
            return rc;
        if (value != want)
            return SDIO_EVERIFY;
    }
    card->blksz[fn] = (uint16_t)size;
    return hbsdc_set_blksz(card->host, size);
}

int sdio_cmd53(struct sdio_card *card, unsigned fn, unsigned addr, int incr,
               int write, void *buf, unsigned blocks, unsigned blksz)
{
    struct hbsdc_cmd cmd;
    int rc;
    if (fn > card->nfunc || addr > 0x1ffff || !blocks || blocks > 511)
        return HBSDC_EINVAL;
    if (card->blksz[fn] != blksz && (rc = sdio_set_blksz(card, fn, blksz)))
        return rc;
    memset(&cmd, 0, sizeof(cmd));
    cmd.opcode = 53;
    cmd.resp = HBSDC_RSP_R1;
    cmd.xfer = write ? 0 : HBSDC_XFER_READ;
    cmd.arg = (uint32_t)fn << 28 | 1u << 27 | (uint32_t)(incr != 0) << 26 |
              (uint32_t)addr << 9 | (blocks & 0x1ff);
    if (write)
        cmd.arg |= 0x80000000u;
    rc = hbsdc_transfer(card->host, &cmd, buf, blocks, blksz);
    if (rc)
        return rc;
    return sdio_check_r5(card, cmd.response);
}
