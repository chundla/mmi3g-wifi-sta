/*
 * SDIO card layer over the FPGA host: identification, CMD52/CMD53, CCCR,
 * FBR and CIS. The attach sequence follows the factory sdio_attach_device
 * on this unit; register names follow the SDIO Simplified Specification.
 */
#ifndef SDIO_H
#define SDIO_H

#include <stdint.h>
#include "hbsdc.h"

#define SDIO_MAX_FUNC       7

#define SDIO_CCCR_IOEX      0x02
#define SDIO_CCCR_IORX      0x03
#define SDIO_CCCR_IENX      0x04
#define SDIO_CCCR_ABORT     0x06    /* write a function number: I/O abort */
#define SDIO_CCCR_BUS_IF    0x07
#define SDIO_CCCR_CAPS      0x08
#define SDIO_CCCR_CIS       0x09
#define SDIO_CCCR_SPEED     0x13

#define SDIO_ER5            (-10)   /* R5 flags reported an error */
#define SDIO_ENOTREADY      (-11)
#define SDIO_EID            (-12)   /* CIS identity differs */
#define SDIO_EVERIFY        (-13)   /* register read-back differs */

struct sdio_card {
    struct hbsdc *host;
    uint32_t power_raw;         /* host power register at detect */
    uint32_t ocr;
    unsigned nfunc;
    uint16_t rca;
    uint8_t cccr[0x14];
    unsigned width;
    uint32_t cis[SDIO_MAX_FUNC + 1];
    uint8_t fn_code[SDIO_MAX_FUNC + 1];
    uint16_t blksz[SDIO_MAX_FUNC + 1];
    uint16_t vendor, device;
    char manf[64];
    unsigned ready_ms;          /* time until IORx matched IOEx */
    uint32_t r5;                /* last R5 status */
    const char *step;           /* step that failed */
};

int sdio_cmd52(struct sdio_card *card, unsigned fn, unsigned addr, int write,
               uint8_t value, uint8_t *out);
int sdio_attach(struct sdio_card *card, uint16_t vendor, uint16_t device);
int sdio_set_blksz(struct sdio_card *card, unsigned fn, unsigned size);
int sdio_cmd53(struct sdio_card *card, unsigned fn, unsigned addr, int incr,
               int write, void *buf, unsigned blocks, unsigned blksz);

#endif
