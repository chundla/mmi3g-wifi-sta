/*
 * Bench ARP and ICMP echo frames; see benchnet.h.
 */
#include <string.h>
#include "benchnet.h"

#define IP_HDR      20
#define ICMP_HDR    8
#define IP_TTL      64
#define IP_ICMP     1
#define ICMP_ECHO_REPLY     0
#define ICMP_ECHO_REQUEST   8

static void put16(uint8_t *p, unsigned v)
{
    p[0] = (uint8_t)(v >> 8);
    p[1] = (uint8_t)v;
}

static unsigned get16(const uint8_t *p)
{
    return (unsigned)p[0] << 8 | p[1];
}

int bench_parse_ip(const char *text, uint8_t ip[4])
{
    unsigned i, value, digits;
    for (i = 0; i < 4; i++) {
        for (value = digits = 0; *text >= '0' && *text <= '9'; text++) {
            /* No leading zeros: inet_aton would read them as octal. */
            if (digits++ && !value)
                return -1;
            value = value * 10 + (unsigned)(*text - '0');
            if (value > 255)
                return -1;
        }
        if (!digits || *text != (i < 3 ? '.' : '\0'))
            return -1;
        text++;
        ip[i] = (uint8_t)value;
    }
    if (ip[0] == 0 || ip[0] == 127 || ip[0] >= 224)
        return -2;
    return 0;
}

void bench_init(struct bench *b, const uint8_t mac[6], const uint8_t ip[4],
                const uint8_t peer_ip[4], uint16_t id)
{
    memset(b, 0, sizeof(*b));
    memcpy(b->mac, mac, 6);
    memcpy(b->ip, ip, 4);
    memcpy(b->peer_ip, peer_ip, 4);
    b->id = id;
}

uint16_t bench_checksum(const uint8_t *p, unsigned len)
{
    uint32_t sum = 0;
    unsigned i;
    for (i = 0; i + 1 < len; i += 2)
        sum += get16(p + i);
    if (len & 1)
        sum += (uint32_t)p[len - 1] << 8;
    while (sum >> 16)
        sum = (sum & 0xffff) + (sum >> 16);
    return (uint16_t)~sum;
}

static unsigned arp(uint8_t *f, unsigned op, const uint8_t *dst, const uint8_t *src_mac,
                    const uint8_t *src_ip, const uint8_t *target_mac, const uint8_t *target_ip)
{
    memcpy(f, dst, 6);
    memcpy(f + 6, src_mac, 6);
    put16(f + 12, BENCH_ETH_ARP);
    put16(f + 14, 1);                   /* Ethernet */
    put16(f + 16, BENCH_ETH_IPV4);
    f[18] = 6;
    f[19] = 4;
    put16(f + 20, op);
    memcpy(f + 22, src_mac, 6);
    memcpy(f + 28, src_ip, 4);
    memcpy(f + 32, target_mac, 6);
    memcpy(f + 38, target_ip, 4);
    return BENCH_ARP_LEN;
}

unsigned bench_arp_request(const struct bench *b, uint8_t *frame)
{
    static const uint8_t broadcast[6] = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff };
    static const uint8_t unknown[6];
    return arp(frame, 1, broadcast, b->mac, b->ip, unknown, b->peer_ip);
}

unsigned bench_arp_reply(const struct bench *b, uint8_t *frame)
{
    return arp(frame, 2, b->asker_mac, b->mac, b->ip, b->asker_mac, b->asker_ip);
}

unsigned bench_echo_request(struct bench *b, uint8_t *frame, uint16_t seq,
                            unsigned payload)
{
    uint8_t *ip = frame + 14, *icmp = ip + IP_HDR;
    unsigned i;
    if (payload > BENCH_PAYLOAD_MAX || !b->peer_known)
        return 0;
    memcpy(frame, b->peer_mac, 6);
    memcpy(frame + 6, b->mac, 6);
    put16(frame + 12, BENCH_ETH_IPV4);
    ip[0] = 0x45;
    ip[1] = 0;
    put16(ip + 2, IP_HDR + ICMP_HDR + payload);
    put16(ip + 4, seq);                 /* identification */
    put16(ip + 6, 0x4000);              /* don't fragment */
    ip[8] = IP_TTL;
    ip[9] = IP_ICMP;
    put16(ip + 10, 0);
    memcpy(ip + 12, b->ip, 4);
    memcpy(ip + 16, b->peer_ip, 4);
    put16(ip + 10, bench_checksum(ip, IP_HDR));
    icmp[0] = ICMP_ECHO_REQUEST;
    icmp[1] = 0;
    put16(icmp + 2, 0);
    put16(icmp + 4, b->id);
    put16(icmp + 6, seq);
    for (i = 0; i < payload; i++)
        icmp[ICMP_HDR + i] = (uint8_t)(seq + i);
    put16(icmp + 2, bench_checksum(icmp, ICMP_HDR + payload));
    b->seq = seq;
    b->payload = payload;
    b->echo_done = 0;
    return BENCH_ECHO_HDR + payload;
}

static enum bench_kind rx_arp(struct bench *b, const uint8_t *f, unsigned len)
{
    const uint8_t *a = f + 14;
    if (len < BENCH_ARP_LEN || get16(a) != 1 || get16(a + 2) != BENCH_ETH_IPV4 ||
        a[4] != 6 || a[5] != 4)
        return BENCH_OTHER;
    if (get16(a + 6) == 2 && !memcmp(a + 14, b->peer_ip, 4) && !memcmp(f, b->mac, 6) &&
        !memcmp(a + 18, b->mac, 6) && !memcmp(a + 24, b->ip, 4) && !(a[8] & 1)) {
        memcpy(b->peer_mac, a + 8, 6);
        b->peer_known = 1;
        b->arp_replies++;
        return BENCH_ARP_REPLY;
    }
    if (get16(a + 6) == 1 && !memcmp(a + 24, b->ip, 4) && !(a[8] & 1)) {
        memcpy(b->asker_mac, a + 8, 6);
        memcpy(b->asker_ip, a + 14, 4);
        b->arp_due = 1;
        b->arp_asked++;
        return BENCH_ARP_ASKED;
    }
    return BENCH_OTHER;
}

static enum bench_kind echo_bad(struct bench *b, const char *why)
{
    b->bad = why;
    b->echo_bad++;
    return BENCH_ECHO_BAD;
}

static enum bench_kind rx_ipv4(struct bench *b, const uint8_t *f, unsigned len)
{
    const uint8_t *ip = f + 14, *icmp;
    unsigned ihl, total, i, n;
    if (len < 14 + IP_HDR || ip[0] >> 4 != 4 || (ihl = 4u * (ip[0] & 15)) < IP_HDR ||
        ip[9] != IP_ICMP || len < 14 + ihl + ICMP_HDR) {
        b->ipv4_other++;
        return BENCH_IPV4_OTHER;
    }
    icmp = ip + ihl;
    if (icmp[0] != ICMP_ECHO_REPLY || get16(icmp + 4) != b->id) {
        b->ipv4_other++;
        return BENCH_IPV4_OTHER;
    }
    /* From here on it claims to answer this bench: every check counts. */
    total = get16(ip + 2);
    if (total < ihl + ICMP_HDR || 14 + total > len)
        return echo_bad(b, "ip_length");
    if (bench_checksum(ip, ihl))
        return echo_bad(b, "ip_checksum");
    if (get16(ip + 6) & 0x3fff)
        return echo_bad(b, "fragment");
    if (memcmp(ip + 12, b->peer_ip, 4) || memcmp(ip + 16, b->ip, 4))
        return echo_bad(b, "ip_address");
    if (memcmp(f, b->mac, 6) || !b->peer_known || memcmp(f + 6, b->peer_mac, 6))
        return echo_bad(b, "mac_address");
    n = total - ihl;
    if (icmp[1] || bench_checksum(icmp, n))
        return echo_bad(b, "icmp_checksum");
    /* An earlier echo's reply after its wait ended, or a duplicate. */
    if (get16(icmp + 6) != b->seq || b->echo_done) {
        b->echo_late++;
        return BENCH_ECHO_LATE;
    }
    if (n - ICMP_HDR != b->payload)
        return echo_bad(b, "payload_length");
    for (i = 0; i < b->payload; i++)
        if (icmp[ICMP_HDR + i] != (uint8_t)(b->seq + i))
            return echo_bad(b, "payload_bytes");
    b->echo_done = 1;
    b->echo_replies++;
    return BENCH_ECHO_REPLY;
}

enum bench_kind bench_rx(struct bench *b, const uint8_t *frame, unsigned len)
{
    b->frames++;
    if (len >= 14 && get16(frame + 12) == BENCH_ETH_ARP) {
        enum bench_kind kind = rx_arp(b, frame, len);
        b->other += kind == BENCH_OTHER;
        return kind;
    }
    if (len >= 14 && get16(frame + 12) == BENCH_ETH_IPV4)
        return rx_ipv4(b, frame, len);
    b->other++;
    return BENCH_OTHER;
}
