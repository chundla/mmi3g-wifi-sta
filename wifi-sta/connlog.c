/*
 * Phase 7 connection log; see connlog.h.
 */
#include <stdio.h>
#include <string.h>
#include "connlog.h"

#define DNS_PORT    53
#define TCP_FIN     0x01
#define TCP_SYN     0x02
#define TCP_RST     0x04
#define TCP_ACK     0x10
#define DNS_HDR     12
#define DNS_ANSWERS_WALKED 16

static unsigned be16(const uint8_t *p)
{
    return (unsigned)p[0] << 8 | p[1];
}

static void ip_text(const uint8_t *p, char out[16])
{
    snprintf(out, 16, "%u.%u.%u.%u", p[0], p[1], p[2], p[3]);
}

/* Skips one encoded name at msg[*pos]: labels, or labels ending in a
 * compression pointer. Copies it (labels before any pointer) to name when
 * name is not NULL, "..." marking a pointer. Returns 0, or -1 when it does
 * not fit len or is malformed. */
static int dns_name(const uint8_t *msg, unsigned len, unsigned *pos, char *name)
{
    unsigned p = *pos, out = 0, labels = 0, i;
    for (;;) {
        unsigned n;
        if (p >= len || ++labels > 64)
            return -1;
        n = msg[p];
        if ((n & 0xc0) == 0xc0) {
            if (p + 2 > len)
                return -1;
            if (name && out + 3 < CONNLOG_NAME_MAX) {
                memcpy(name + out, "...", 3);
                out += 3;
            }
            p += 2;
            break;
        }
        if (n & 0xc0)
            return -1;
        p++;
        if (n == 0)
            break;
        if (p + n > len)
            return -1;
        if (name && out && out + 1 < CONNLOG_NAME_MAX)
            name[out++] = '.';
        for (i = 0; name && i < n && out + 1 < CONNLOG_NAME_MAX; i++) {
            uint8_t c = msg[p + i];
            name[out++] = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                          (c >= '0' && c <= '9') || c == '-' || c == '_' ? c : '?';
        }
        p += n;
    }
    if (name) {
        if (!out)
            name[out++] = '.';
        name[out] = 0;
    }
    *pos = p;
    return 0;
}

static int dns_event(const uint8_t *msg, unsigned len, int query, const uint8_t *peer,
                     char *out)
{
    char name[CONNLOG_NAME_MAX], server[16], a[16] = "-";
    unsigned pos = DNS_HDR, qd, an, i, type;
    if (len < DNS_HDR)
        return 0;
    qd = be16(msg + 4);
    an = be16(msg + 6);
    ip_text(peer, server);
    if (query) {
        if ((msg[2] & 0x80) || qd < 1 || dns_name(msg, len, &pos, name) || pos + 4 > len)
            return 0;
        snprintf(out, CONNLOG_LINE_MAX, "dns-query id=%u type=%u name=%s to=%s",
                 be16(msg), be16(msg + pos), name, server);
        return 1;
    }
    if (!(msg[2] & 0x80))
        return 0;
    for (i = 0; i < qd && i < 4; i++) {
        if (dns_name(msg, len, &pos, NULL) || pos + 4 > len)
            goto report;
        pos += 4;
    }
    if (i < qd)
        goto report;
    for (i = 0; i < an && i < DNS_ANSWERS_WALKED; i++) {
        unsigned rdlen;
        if (dns_name(msg, len, &pos, NULL) || pos + 10 > len)
            break;
        type = be16(msg + pos);
        rdlen = be16(msg + pos + 8);
        pos += 10;
        if (pos + rdlen > len)
            break;
        if (type == 1 && be16(msg + pos - 8) == 1 && rdlen == 4) {
            ip_text(msg + pos, a);
            break;
        }
        pos += rdlen;
    }
report:
    snprintf(out, CONNLOG_LINE_MAX, "dns-reply id=%u rcode=%u answers=%u a=%s from=%s",
             be16(msg), msg[3] & 0x0f, an, a, server);
    return 1;
}

/* The IPv4 first fragment the unit sent or was sent, with at least 8 bytes
 * of transport header within the capture. Returns 1 and sets *sent, *l4
 * and *l4len (cut to the capture), else 0. */
static int own_ipv4(const uint8_t *f, unsigned caplen, const uint8_t own[6], int *sent,
                    const uint8_t **l4, unsigned *l4len)
{
    const uint8_t *ip = f + 14;
    unsigned ihl, total;

    if (caplen < 14 + 20 || f[12] != 0x08 || f[13] != 0x00 || ip[0] >> 4 != 4)
        return 0;
    if (!memcmp(f + 6, own, 6))
        *sent = 1;
    else if (!memcmp(f, own, 6))
        *sent = 0;
    else
        return 0;
    ihl = (ip[0] & 0x0f) * 4;
    total = be16(ip + 2);
    if (ihl < 20 || total < ihl || (be16(ip + 6) & 0x1fff))
        return 0;
    if (total > caplen - 14)
        total = caplen - 14;
    if (total < ihl + 8)
        return 0;
    *l4 = ip + ihl;
    *l4len = total - ihl;
    return 1;
}

int connlog_payload(const uint8_t *f, unsigned caplen, const uint8_t own[6],
                    const uint8_t peer[4], unsigned port, char *out)
{
    const uint8_t *ip = f + 14, *l4, *data;
    unsigned l4len, doff, len, wire, sport, dport, i;
    unsigned long seq;
    int sent, n;
    char s[16], d[16];

    if (!own_ipv4(f, caplen, own, &sent, &l4, &l4len) || ip[9] != 6 || l4len < 20)
        return 0;
    sport = be16(l4);
    dport = be16(l4 + 2);
    if (sent ? memcmp(ip + 16, peer, 4) || dport != port
             : memcmp(ip + 12, peer, 4) || sport != port)
        return 0;
    doff = (l4[12] >> 4) * 4;
    if (doff < 20 || doff > l4len)
        return 0;
    /* The segment's length on the wire; the capture may hold less. */
    wire = be16(ip + 2) - (ip[0] & 0x0f) * 4;
    wire = wire > doff ? wire - doff : 0;
    len = l4len - doff;
    if (!wire)
        return 0;
    if (len > CONNLOG_DATA_BYTES)
        len = CONNLOG_DATA_BYTES;
    data = l4 + doff;
    seq = (unsigned long)be16(l4 + 4) << 16 | be16(l4 + 6);
    ip_text(ip + 12, s);
    ip_text(ip + 16, d);
    n = snprintf(out, CONNLOG_DATA_MAX, "tcp-data %s %s:%u > %s:%u seq=%lu len=%u got=%u hex=",
                 sent ? "out" : "in", s, sport, d, dport, seq, wire, len);
    for (i = 0; i < len; i++)
        n += snprintf(out + n, CONNLOG_DATA_MAX - n, "%02x", data[i]);
    return 1;
}

int connlog_event(const uint8_t *f, unsigned caplen, const uint8_t own[6], char *out)
{
    const uint8_t *ip = f + 14, *l4;
    unsigned l4len, sport, dport;
    int sent;
    char s[16], d[16];

    if (!own_ipv4(f, caplen, own, &sent, &l4, &l4len))
        return 0;
    sport = be16(l4);
    dport = be16(l4 + 2);
    ip_text(ip + 12, s);
    ip_text(ip + 16, d);

    if (ip[9] == 17) {
        if (sent && dport == DNS_PORT)
            return dns_event(l4 + 8, l4len - 8, 1, ip + 16, out);
        if (!sent && sport == DNS_PORT)
            return dns_event(l4 + 8, l4len - 8, 0, ip + 12, out);
        return 0;
    }
    if (ip[9] != 6 || l4len < 20)
        return 0;
    switch (l4[13] & (TCP_SYN | TCP_RST | TCP_ACK | TCP_FIN)) {
    case TCP_SYN:
        if (!sent)
            return 0;
        snprintf(out, CONNLOG_LINE_MAX, "tcp-syn to=%s:%u from=%s:%u", d, dport, s, sport);
        return 1;
    case TCP_SYN | TCP_ACK:
        if (sent)
            return 0;
        snprintf(out, CONNLOG_LINE_MAX, "tcp-synack from=%s:%u", s, sport);
        return 1;
    case TCP_RST:
    case TCP_RST | TCP_ACK:
        if (sent)
            return 0;
        snprintf(out, CONNLOG_LINE_MAX, "tcp-rst from=%s:%u", s, sport);
        return 1;
    }
    return 0;
}
