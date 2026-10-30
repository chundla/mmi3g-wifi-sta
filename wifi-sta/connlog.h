/*
 * Phase 7 connection log: which hosts the unit itself tries to reach. One
 * Ethernet frame in, at most one line out, headers only:
 *
 *   dns-query id=<n> type=<n> name=<name> to=<server>
 *   dns-reply id=<n> rcode=<n> answers=<n> a=<first A or -> from=<server>
 *   tcp-syn to=<ip>:<port> from=<ip>:<port>
 *   tcp-synack from=<ip>:<port>
 *   tcp-rst from=<ip>:<port>
 *
 * Only frames the unit sent (source is own) or was sent (destination is
 * own) count; IPv4 first fragments only. Names are cut at
 * CONNLOG_NAME_MAX, any label byte outside [A-Za-z0-9_-] is shown as '?',
 * and "..." stands for a compression pointer.
 * Platform-free, for the host tests.
 */
#ifndef CONNLOG_H
#define CONNLOG_H

#include <stddef.h>
#include <stdint.h>

#define CONNLOG_NAME_MAX    128
#define CONNLOG_LINE_MAX    256
/* Payload bytes shown per segment, and the line that holds them in hex. */
#define CONNLOG_DATA_BYTES  1600
#define CONNLOG_DATA_MAX    (2 * CONNLOG_DATA_BYTES + 128)

/* Returns 1 and writes a line (no newline) to out when the frame is one of
 * the events above, else 0. out must hold CONNLOG_LINE_MAX bytes. */
int connlog_event(const uint8_t *frame, unsigned caplen, const uint8_t own[6], char *out);

/* The payload of a TCP segment between the unit and peer:port, for reading
 * a plain-text protocol (bpf-check -d). Returns 1 and writes
 *
 *   tcp-data out|in <src>:<port> > <dst>:<port> seq=<n> len=<on the wire>
 *            got=<captured> hex=<captured bytes>
 *
 * (one line, no newline) when the segment carries data, else 0. A
 * retransmission is logged again with the same seq. out must hold
 * CONNLOG_DATA_MAX bytes. */
int connlog_payload(const uint8_t *frame, unsigned caplen, const uint8_t own[6],
                    const uint8_t peer[4], unsigned port, char *out);

#endif
