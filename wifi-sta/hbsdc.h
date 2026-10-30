/*
 * Harman-Becker FPGA SDIO host ("HBFPGA SDC", module ID 0x53) as used for
 * the 88W8688 on the 9411 unit. Register layout and sequences follow the
 * factory devnp-mv8688uap.so (SHA-256 311b947c...53d269f7) as recorded in
 * docs/wifi-client/wifi-client-mode.md; no factory code is copied.
 *
 * All register access goes through struct hbsdc_io so the same code runs
 * against the mapped FPGA on the unit and against a fake in host tests.
 */
#ifndef HBSDC_H
#define HBSDC_H

#include <stdint.h>

#define HBSDC_DATA      0x00    /* PIO data FIFO, one word per access */
#define HBSDC_CMD       0x04    /* command FIFO, two words per command */
#define HBSDC_RESP      0x08    /* response FIFO */
#define HBSDC_CMDTY     0x0c
#define HBSDC_CTRL      0x10
#define HBSDC_STAT      0x14
#define HBSDC_IEN       0x18
#define HBSDC_FLAG      0x1c    /* write 1 to clear */
#define HBSDC_CLKDIV    0x20    /* byte */
#define HBSDC_POWER     0x28    /* card power and status (inferred) */
#define HBSDC_BLKCNT    0x2c
#define HBSDC_BLKSZ     0x30
#define HBSDC_INDEX     0x3c    /* byte: write an index, read its value */
#define HBSDC_CAPS      0x5c

#define HBSDC_CTRL_ENABLE       0x0001
#define HBSDC_CTRL_MODE4        0x0002
#define HBSDC_CTRL_READ         0x0004
#define HBSDC_CTRL_DATA_CLR     0x0008
#define HBSDC_CTRL_NEXT_CMD     0x0010
#define HBSDC_CTRL_NEXT_RESP    0x0020
#define HBSDC_CTRL_CR_CLR       0x0040
#define HBSDC_CTRL_DEV_RESET    0x0080
#define HBSDC_CTRL_START        0x0100
#define HBSDC_CTRL_HS           0x0800
#define HBSDC_CTRL_FAST_CLK     0x1000
#define HBSDC_CTRL_CLK_EN       0x2000
#define HBSDC_CTRL_SDIO_EN      0x4000

#define HBSDC_STAT_BUSY         0x0001
#define HBSDC_STAT_DT_RUN       0x8000

#define HBSDC_FLAG_CMD_RDY      0x0001
#define HBSDC_FLAG_SDIO_INT     0x0100
#define HBSDC_FLAG_DT_RDY       0x8000
/* Bits the factory completion path reports as errors. */
#define HBSDC_FLAG_ERRORS       0x6638
#define HBSDC_FLAG_DATA_ERRORS  0x4418  /* data timeout, CRC, frame, CRC token */
#define HBSDC_IEN_COMMAND       0x1843

#define HBSDC_POWER_ON          0x0003
#define HBSDC_POWER_CHANGE      0x0080
#define HBSDC_POWER_NO_CARD     0x2000

#define HBSDC_MODULE_ID         0x53

/* Response descriptor, same bit meaning as the factory command record. */
#define HBSDC_RSP_PRESENT       0x01
#define HBSDC_RSP_136           0x02
#define HBSDC_RSP_CRC           0x04
#define HBSDC_RSP_BUSY          0x08
#define HBSDC_RSP_OPCODE        0x10
#define HBSDC_RSP_R1            (HBSDC_RSP_PRESENT | HBSDC_RSP_CRC | HBSDC_RSP_OPCODE)
#define HBSDC_RSP_R4            HBSDC_RSP_PRESENT

#define HBSDC_XFER_DATA         0x0010
#define HBSDC_XFER_MULTI        0x0020
#define HBSDC_XFER_READ         0x0040

#define HBSDC_OK                0
#define HBSDC_EBUSY             (-1)
#define HBSDC_ETIMEDOUT         (-2)
#define HBSDC_ECARD             (-3)    /* flagged CRC, frame or timeout */
#define HBSDC_EINVAL            (-4)
#define HBSDC_EDATA             (-5)
#define HBSDC_ECHANGE           (-6)    /* card change flagged, now acknowledged */

struct hbsdc_io {
    uint32_t (*rd32)(void *ctx, unsigned off);
    void (*wr32)(void *ctx, unsigned off, uint32_t value);
    uint8_t (*rd8)(void *ctx, unsigned off);
    void (*wr8)(void *ctx, unsigned off, uint8_t value);
    /* Wait for the module interrupt: 0 when it fired, -1 on timeout. */
    int (*wait_irq)(void *ctx, unsigned timeout_ms);
    /* Re-arm the interrupt after its source was cleared. */
    void (*irq_done)(void *ctx);
    void (*delay_ms)(void *ctx, unsigned ms);
    void (*spin_us)(void *ctx, unsigned us);
    /* Monotonic milliseconds; wraps, so compare differences only. */
    unsigned (*now_ms)(void *ctx);
    /* Free-running counter for transfer timing, or NULL for none. Wraps,
     * so compare differences only; the caller knows its rate. */
    uint32_t (*ticks)(void *ctx);
};

/* Timing of successful transfers in counter ticks. setup is the register
 * setup and the command up to its completion, interrupt included; data the
 * PIO word loop; drain the wait for DT_RUN to clear and the checks after. */
struct hbsdc_phase {
    unsigned n, bytes;
    uint64_t setup, data, drain;
    uint32_t data_max, total_max;
};

#define HBSDC_BUCKETS       9   /* 1..8 blocks, then 9 and more */

struct hbsdc_stats {
    struct hbsdc_phase xfer[2][HBSDC_BUCKETS];     /* [0] writes, [1] reads */
    unsigned cmd_n;             /* successful commands without data */
    uint64_t cmd_ticks;
    uint32_t cmd_max;
    unsigned failures;          /* timed commands and transfers that failed */
};

struct hbsdc {
    const struct hbsdc_io *io;
    void *ctx;
    uint8_t major, change;
    uint8_t irq_index;          /* index 0xf; 0xff means no auto-select */
    unsigned irq;
    uint32_t clock_hz;          /* FPGA reference clock */
    uint32_t caps;              /* register 0x5c */
    unsigned fifo_bytes;
    unsigned bus_hz;
    /* Diagnostics for the most recent command. */
    uint32_t flags;             /* FLAG bits accumulated */
    uint8_t resp_index;         /* first response byte */
    int by_irq;                 /* completed by an interrupt */
    unsigned interrupts, polls; /* totals */
    unsigned changes;           /* card change events acknowledged */
    /* Filled while set and io->ticks is not NULL. */
    struct hbsdc_stats *stats;
};

struct hbsdc_cmd {
    uint8_t opcode;
    uint8_t resp;               /* HBSDC_RSP_* */
    uint16_t xfer;              /* HBSDC_XFER_* */
    uint32_t arg;
    uint32_t response;          /* bits 39..8 of a 48-bit response */
};

uint32_t hbsdc_cmd_word(uint8_t opcode, uint32_t arg);
uint32_t hbsdc_cmdty(uint8_t resp, uint16_t xfer);
uint32_t hbsdc_response(uint32_t w0, uint32_t w1);

/* Reads the module index registers; refuses anything but module 0x53. */
int hbsdc_identify(struct hbsdc *h);
/* Factory hbsdc_init tail: power down, interrupts off, slow clock. */
void hbsdc_quiesce(struct hbsdc *h);
/* 0 when a card is present and no change is pending. */
int hbsdc_detect(struct hbsdc *h, uint32_t *raw);
int hbsdc_powerup(struct hbsdc *h);
void hbsdc_powerdown(struct hbsdc *h);
int hbsdc_set_clock(struct hbsdc *h, unsigned hz);
int hbsdc_set_width(struct hbsdc *h, unsigned bits);
int hbsdc_set_blksz(struct hbsdc *h, unsigned size);
int hbsdc_command(struct hbsdc *h, struct hbsdc_cmd *cmd);
/* Block-mode PIO transfer: set up, command, then move the data. */
int hbsdc_transfer(struct hbsdc *h, struct hbsdc_cmd *cmd, void *buf,
                   unsigned blocks, unsigned blksz);
/* Factory host shutdown: interrupts off, device reset, power down. */
void hbsdc_shutdown(struct hbsdc *h);

#endif
