/*
 * Bench traffic for the phase 5.1 probe: ARP and ICMP echo frames, built
 * and checked by hand, so the station's first data frames can be compared
 * with the bench access point's capture. Probe-only; the io-net driver
 * leaves this to io-pkt. Frames are Ethernet II, as libertas hands them to
 * the firmware and as mv8688cmd passes received ones on. Formats follow
 * RFC 826, RFC 791 and RFC 792; nothing here comes from the factory image.
 */
#ifndef BENCHNET_H
#define BENCHNET_H

#include <stdint.h>

#define BENCH_ARP_LEN       42      /* Ethernet II and an IPv4 ARP packet */
#define BENCH_ECHO_HDR      42      /* Ethernet, IPv4 and ICMP headers */
#define BENCH_PAYLOAD_MAX   1472    /* a 1,500-byte IP datagram */
#define BENCH_ETH_ARP       0x0806
#define BENCH_ETH_IPV4      0x0800

/* What the last frame given to bench_rx was. */
enum bench_kind {
    BENCH_OTHER,                /* not for this bench, or not ARP or IPv4 */
    BENCH_ARP_REPLY,            /* the peer's address, now known */
    BENCH_ARP_ASKED,            /* a request for our address; answer it */
    BENCH_ECHO_REPLY,           /* the awaited echo, every check passed */
    BENCH_ECHO_BAD,             /* our identifier, but a check failed */
    BENCH_ECHO_LATE,            /* a sound reply, but not the one awaited */
    BENCH_IPV4_OTHER            /* IPv4 that is none of the above */
};

struct bench {
    uint8_t mac[6], ip[4], peer_ip[4];
    uint8_t peer_mac[6];
    int peer_known;
    /* The echo awaited: identifier, sequence and payload length. */
    uint16_t id, seq;
    unsigned payload;
    int echo_done;
    /* A request for our address that still needs a reply. */
    int arp_due;
    uint8_t asker_mac[6], asker_ip[4];
    /* Counts since bench_init. */
    unsigned frames, arp_replies, arp_asked, echo_replies, echo_bad, echo_late, ipv4_other;
    unsigned other;
    const char *bad;            /* the check the last bad echo failed */
};

/* Parses a dotted quad. -1 for any other text, -2 for an address no
 * station should use or ping: 0.x.x.x, loopback, multicast, the limited
 * broadcast. */
int bench_parse_ip(const char *text, uint8_t ip[4]);
void bench_init(struct bench *b, const uint8_t mac[6], const uint8_t ip[4],
                const uint8_t peer_ip[4], uint16_t id);
/* RFC 1071 one's-complement sum, folded and complemented. */
uint16_t bench_checksum(const uint8_t *p, unsigned len);
/* Broadcast "who has peer_ip, tell ip". Returns BENCH_ARP_LEN. */
unsigned bench_arp_request(const struct bench *b, uint8_t *frame);
/* Answers the pending request in asker_mac/asker_ip. */
unsigned bench_arp_reply(const struct bench *b, uint8_t *frame);
/* An echo request to the peer's known address: identifier b->id, this
 * sequence, payload bytes (uint8_t)(seq + i). Sets the awaited echo.
 * Returns the frame length, or 0 for a payload over BENCH_PAYLOAD_MAX or
 * an unknown peer. */
unsigned bench_echo_request(struct bench *b, uint8_t *frame, uint16_t seq,
                            unsigned payload);
/* Classifies a received Ethernet II frame and updates the bench. */
enum bench_kind bench_rx(struct bench *b, const uint8_t *frame, unsigned len);

#endif
