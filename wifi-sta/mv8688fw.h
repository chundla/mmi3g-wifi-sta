/*
 * 88W8688 helper and firmware download over the SDIO card layer. The
 * protocol follows the factory sbi_prog_helper, sbi_prog_fw_w_helper and
 * sbi_check_fw_status in devnp-mv8688uap.so (SHA-256 311b947c...53d269f7)
 * as recorded in docs/wifi-client/wifi-client-mode.md; no factory code is
 * copied. Function-1 register names follow Linux libertas if_sdio.h.
 */
#ifndef MV8688FW_H
#define MV8688FW_H

#include <stdint.h>
#include "sdio.h"

#define MV_REG_IOPORT       0x00    /* function 1, 0x00-0x02 */
#define MV_REG_CONFIG       0x03    /* write 4: host terminates the CMD53 */
#define MV_REG_RD_BASE      0x10    /* 0x10-0x11: requested length */
#define MV_REG_STATUS       0x20
#define MV_REG_SCRATCH      0x40    /* 0x40-0x41: firmware status */
#define MV_REG_RX_UNIT      0x43
#define MV_REG_CHIP_ID      0x801c  /* 0x801c-0x801d, big-endian */

#define MV_STATUS_DL_READY  0x09    /* IO_RDY | DL_RDY */
#define MV_CONFIG_ABORT     0x04
#define MV_FW_READY         0xfedc
#define MV_BLKSZ            256
#define MV_HELPER_CHUNK     508     /* 4-byte length + 508 = two blocks */
#define MV_FW_REQUEST_MAX   0x908
#define MV_BUF_BYTES        2560    /* MV_FW_REQUEST_MAX in whole blocks */
#define MV_TRACE            8       /* request-register changes recorded */

#define MV_EREQUEST         (-20)   /* requested length over 0x908 */
#define MV_ECRC             (-21)   /* CRC retries exhausted */
#define MV_ESTALL           (-22)   /* card stopped requesting early */
#define MV_ENOTREADY        (-23)   /* download-ready status never set */
#define MV_ENOFW            (-24)   /* ready magic never appeared */

struct mv8688_dl {
    struct sdio_card *card;
    uint32_t ioport;
    const char *step;           /* step that failed */
    uint8_t buf[MV_BUF_BYTES];
    unsigned buf_blocks;        /* blocks of the chunk in buf */
    /* Diagnostics. */
    unsigned helper_chunks;
    unsigned requests, req_min, req_max;
    unsigned crc_retries, aborts, abort_failures;
    unsigned ready_reads_max;   /* status reads until DL_READY, worst */
    unsigned zero_polls_max;    /* zero-length reads before a request */
    unsigned fw_polls;          /* scratch reads until the ready magic */
    uint32_t sent;              /* firmware bytes the card asked for */
    uint16_t fw_status;
    uint8_t rx_unit;
    int rx_unit_rc;             /* RX unit read; non-zero leaves rx_unit 0 */
    uint16_t rd_base_pre;       /* request register before the helper */
    int rd_base_pre_rc;
    uint16_t last_request;      /* last request value read, raw */
    unsigned first_polls;       /* reads 1 ms apart before the first chunk */
    unsigned early_odd;         /* odd values read before the first chunk */
    /* Request register before the first chunk: each change, by poll. */
    unsigned trace_n;
    uint16_t trace_poll[MV_TRACE];
    uint16_t trace_value[MV_TRACE];
};

/* The only images the probe and the station driver download. */
struct mv8688_pinned {
    uint32_t size, sum;         /* bytes and POSIX cksum */
    int helper, station;
    const char *name;
};

/* POSIX cksum (CRC-32, then the length), as the unit's cksum prints. */
uint32_t mv8688_cksum(const void *data, uint32_t size);
/* The pinned image of this size and cksum, or NULL. */
const struct mv8688_pinned *mv8688_pinned_image(uint32_t size, uint32_t sum, int helper);
int mv8688_fw_status(struct sdio_card *card, uint16_t *status);
int mv8688_ioport(struct sdio_card *card, uint32_t *port);
int mv8688_prog_helper(struct mv8688_dl *dl, const uint8_t *image, uint32_t size);
int mv8688_prog_fw(struct mv8688_dl *dl, const uint8_t *image, uint32_t size);
/* Polls the ready magic up to tries times, 10 ms apart, then reads RX unit. */
int mv8688_wait_fw(struct mv8688_dl *dl, unsigned tries);

#endif
