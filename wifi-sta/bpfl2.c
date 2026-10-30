/*
 * Ethernet frames over BPF; see bpfl2.h.
 */
#include <string.h>
#include "bpfl2.h"

#define LD_W_ABS    0x20    /* BPF_LD | BPF_W | BPF_ABS */
#define LD_H_ABS    0x28    /* BPF_LD | BPF_H | BPF_ABS */
#define JEQ_K       0x15    /* BPF_JMP | BPF_JEQ | BPF_K */
#define RET_K       0x06    /* BPF_RET | BPF_K */

/* bpf_hdr on a 32-bit stack: a struct timeval, bh_caplen, bh_datalen,
 * bh_hdrlen; records start on a long boundary. */
#define HDR_CAPLEN  8
#define HDR_HDRLEN  16
#define HDR_MIN     18
#define WORDALIGN(x) (((x) + 3) & ~(size_t)3)

static void put(struct bpfl2_insn *i, uint16_t code, uint8_t jt, uint8_t jf, uint32_t k)
{
    i->code = code;
    i->jt = jt;
    i->jf = jf;
    i->k = k;
}

void bpfl2_filter(struct bpfl2_insn prog[BPFL2_FILTER_LEN], uint16_t ethertype,
                  const uint8_t own[6])
{
    uint32_t hi = (uint32_t)own[0] << 24 | (uint32_t)own[1] << 16 | own[2] << 8 | own[3];
    put(&prog[0], LD_H_ABS, 0, 0, 12);              /* type */
    put(&prog[1], JEQ_K, 0, 5, ethertype);          /* other type: drop */
    put(&prog[2], LD_W_ABS, 0, 0, 6);               /* source, first 4 bytes */
    put(&prog[3], JEQ_K, 0, 2, hi);                 /* not ours: accept */
    put(&prog[4], LD_H_ABS, 0, 0, 10);              /* source, last 2 bytes */
    put(&prog[5], JEQ_K, 1, 0, (uint32_t)own[4] << 8 | own[5]);    /* ours: drop */
    put(&prog[6], RET_K, 0, 0, 0xffffffffu);        /* accept the whole frame */
    put(&prog[7], RET_K, 0, 0, 0);                  /* drop */
}

static unsigned get32(const uint8_t *p)
{
    uint32_t v;
    memcpy(&v, p, 4);
    return v;
}

int bpfl2_next(const uint8_t *buf, size_t len, size_t *off, const uint8_t **frame,
               unsigned *caplen)
{
    const uint8_t *h;
    uint16_t hdrlen;
    unsigned cap;
    if (*off >= len)
        return 0;
    if (len - *off < HDR_MIN)
        return -1;
    h = buf + *off;
    cap = get32(h + HDR_CAPLEN);
    memcpy(&hdrlen, h + HDR_HDRLEN, 2);
    if (hdrlen < HDR_MIN || hdrlen > len - *off || cap > len - *off - hdrlen)
        return -1;
    *frame = h + hdrlen;
    *caplen = cap;
    *off += WORDALIGN((size_t)hdrlen + cap);
    return 1;
}

#ifdef __QNXNTO__
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <unistd.h>
#include <devctl.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <net/if.h>
#include <net/bpf.h>

typedef char insn_size_matches[sizeof(struct bpfl2_insn) == sizeof(struct bpf_insn) ? 1 : -1];
typedef char caplen_matches[offsetof(struct bpf_hdr, bh_caplen) == HDR_CAPLEN ? 1 : -1];
typedef char hdrlen_matches[offsetof(struct bpf_hdr, bh_hdrlen) == HDR_HDRLEN ? 1 : -1];

/* BIOCSETF with the instructions after the program in the message. The
 * unit's libc ioctl() sends only the struct, whose bf_insns points into
 * this process, and io-pkt refuses it (EINVAL); io-pkt reads the
 * instructions from the message instead (6.0 on the unit, 2026-10-29). */
static int set_filter(int fd, struct bpf_program *prog)
{
    iov_t send[2], reply[1];
    int rc;
    SETIOV(&send[0], prog, sizeof(*prog));
    SETIOV(&send[1], prog->bf_insns, prog->bf_len * sizeof(struct bpf_insn));
    SETIOV(&reply[0], prog, sizeof(*prog));
    if ((rc = devctlv(fd, BIOCSETF, 2, 1, send, reply, NULL)) != EOK) {
        errno = rc;
        return -1;
    }
    return 0;
}

int bpfl2_open(const char *ifname, uint16_t ethertype, const uint8_t own[6],
               unsigned *buflen, const char **step)
{
    struct bpfl2_insn insns[BPFL2_FILTER_LEN];
    struct bpf_program prog;
    struct ifreq ifr;
    u_int value;
    int fd, saved;

    *step = "open";
    if ((fd = open("/dev/bpf", O_RDWR)) == -1)
        return -1;
    /* Before the interface: BPF sizes its buffers when it attaches. A
     * refusal leaves the default, which BIOCGBLEN reports below. */
    value = BPFL2_BUFLEN;
    ioctl(fd, BIOCSBLEN, &value);
    memset(&ifr, 0, sizeof(ifr));
    strncpy(ifr.ifr_name, ifname, sizeof(ifr.ifr_name) - 1);
    *step = "BIOCSETIF";
    if (ioctl(fd, BIOCSETIF, &ifr) == -1)
        goto fail;
    *step = "BIOCGDLT";
    if (ioctl(fd, BIOCGDLT, &value) == -1)
        goto fail;
    if (value != DLT_EN10MB) {
        errno = EPROTONOSUPPORT;
        goto fail;
    }
    value = 1;
    *step = "BIOCIMMEDIATE";
    if (ioctl(fd, BIOCIMMEDIATE, &value) == -1)
        goto fail;
    value = 1;
    *step = "BIOCSHDRCMPLT";
    if (ioctl(fd, BIOCSHDRCMPLT, &value) == -1)
        goto fail;
    bpfl2_filter(insns, ethertype, own);
    prog.bf_len = BPFL2_FILTER_LEN;
    prog.bf_insns = (struct bpf_insn *)insns;
    *step = "BIOCSETF";
    if (set_filter(fd, &prog) == -1)
        goto fail;
    *step = "BIOCGBLEN";
    if (ioctl(fd, BIOCGBLEN, &value) == -1)
        goto fail;
    *buflen = value;
    *step = NULL;
    return fd;

fail:
    saved = errno;
    close(fd);
    errno = saved;
    return -1;
}
#endif
