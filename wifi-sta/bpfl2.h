/*
 * Ethernet frames of one type over BPF, for the phase 6 supplicant's
 * EAPOL path (wpa_supplicant's l2_packet) and the BPF check on en5. The
 * unit has /dev/bpf in io-pkt but no libpcap, so this sets BPF up the way
 * libpcap would on NetBSD: buffer size before the interface, immediate
 * mode, complete headers on write, and a filter for the frame type that
 * also drops the station's own frames (BPF sees sent frames too).
 *
 * The filter and the record parser are platform-free, for the host tests;
 * opening needs QNX.
 */
#ifndef BPFL2_H
#define BPFL2_H

#include <stddef.h>
#include <stdint.h>

/* struct bpf_insn's layout. */
struct bpfl2_insn {
    uint16_t code;
    uint8_t jt, jf;
    uint32_t k;
};

#define BPFL2_FILTER_LEN    8
#define BPFL2_BUFLEN        32768   /* asked for; BPF may grant less */

/* Accepts whole frames of this Ethernet type whose source is not own. */
void bpfl2_filter(struct bpfl2_insn prog[BPFL2_FILTER_LEN], uint16_t ethertype,
                  const uint8_t own[6]);

/* Walks the records of one read: on 1, *frame and *caplen are the next
 * captured frame and *off has moved past it; 0 at the end; -1 when a
 * record header does not fit what was read. */
int bpfl2_next(const uint8_t *buf, size_t len, size_t *off, const uint8_t **frame,
               unsigned *caplen);

#ifdef __QNXNTO__
/* Opens /dev/bpf on the interface with the filter above. Returns the
 * descriptor and the buffer size reads must use, or -1 with errno set and
 * *step naming the call that failed. */
int bpfl2_open(const char *ifname, uint16_t ethertype, const uint8_t own[6],
               unsigned *buflen, const char **step);
#endif

#endif
