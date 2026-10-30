/*
 * wpa_supplicant layer-2 packet interface for QNX io-pkt over /dev/bpf,
 * without libpcap (the unit has none). It does what l2_packet_freebsd.c
 * does through libpcap: frames of one Ethernet type, not sent by this
 * station, read in immediate mode; frames written with their Ethernet
 * header complete. The BPF set-up and the record walk are bpfl2.c's
 * (wifi-sta/bpfl2.c), which the BPF check on en5 also uses.
 *
 * This file is distributed under the terms of the BSD license, as
 * wpa_supplicant is; see wpa_supplicant's README.
 */
#include "includes.h"

#include <sys/types.h>
#include <sys/socket.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <net/if_dl.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#include "common.h"
#include "eloop.h"
#include "l2_packet.h"
#include "bpfl2.h"

struct l2_packet_data {
    int fd;
    char ifname[IFNAMSIZ];
    u8 own_addr[ETH_ALEN];
    void (*rx_callback)(void *ctx, const u8 *src_addr, const u8 *buf, size_t len);
    void *rx_callback_ctx;
    int l2_hdr;                 /* hand frames on with their Ethernet header */
    u8 *buf;
    unsigned buflen;
};

int l2_packet_get_own_addr(struct l2_packet_data *l2, u8 *addr)
{
    os_memcpy(addr, l2->own_addr, ETH_ALEN);
    return 0;
}

int l2_packet_send(struct l2_packet_data *l2, const u8 *dst_addr, u16 proto, const u8 *buf,
                   size_t len)
{
    struct l2_ethhdr *eth;
    ssize_t sent;

    if (l2->l2_hdr)
        return write(l2->fd, buf, len) == (ssize_t) len ? 0 : -1;
    eth = os_malloc(sizeof(*eth) + len);
    if (!eth)
        return -1;
    os_memcpy(eth->h_dest, dst_addr, ETH_ALEN);
    os_memcpy(eth->h_source, l2->own_addr, ETH_ALEN);
    eth->h_proto = host_to_be16(proto);
    os_memcpy(eth + 1, buf, len);
    sent = write(l2->fd, eth, sizeof(*eth) + len);
    if (sent != (ssize_t) (sizeof(*eth) + len))
        wpa_printf(MSG_ERROR, "l2_packet_send: write to BPF on %s returned %d: %s",
                   l2->ifname, (int) sent, strerror(errno));
    os_free(eth);
    return sent == (ssize_t) (sizeof(*eth) + len) ? 0 : -1;
}

static void l2_packet_receive(int sock, void *eloop_ctx, void *sock_ctx)
{
    struct l2_packet_data *l2 = eloop_ctx;
    const struct l2_ethhdr *eth;
    const u8 *frame;
    unsigned caplen;
    size_t off = 0;
    ssize_t got;
    int rc;
    (void) sock_ctx;

    got = read(sock, l2->buf, l2->buflen);
    if (got < 0) {
        wpa_printf(MSG_ERROR, "l2_packet_receive: read from BPF on %s: %s", l2->ifname,
                   strerror(errno));
        return;
    }
    while ((rc = bpfl2_next(l2->buf, (size_t) got, &off, &frame, &caplen)) == 1) {
        if (caplen < sizeof(*eth) || !l2->rx_callback)
            continue;
        eth = (const struct l2_ethhdr *) frame;
        if (l2->l2_hdr)
            l2->rx_callback(l2->rx_callback_ctx, eth->h_source, frame, caplen);
        else
            l2->rx_callback(l2->rx_callback_ctx, eth->h_source, frame + sizeof(*eth),
                            caplen - sizeof(*eth));
    }
    if (rc < 0)
        wpa_printf(MSG_ERROR, "l2_packet_receive: a BPF record did not fit the %d bytes "
                   "read", (int) got);
}

/* The interface's link-level address, from its AF_LINK entry. */
static int own_link_addr(const char *ifname, u8 *addr)
{
    struct ifaddrs *ifap, *ifa;
    const struct sockaddr_dl *sdl;
    int rc = -1;

    if (getifaddrs(&ifap) != 0)
        return -1;
    for (ifa = ifap; ifa; ifa = ifa->ifa_next) {
        if (!ifa->ifa_addr || ifa->ifa_addr->sa_family != AF_LINK ||
            os_strcmp(ifa->ifa_name, ifname) != 0)
            continue;
        sdl = (const struct sockaddr_dl *) ifa->ifa_addr;
        if (sdl->sdl_alen == ETH_ALEN) {
            os_memcpy(addr, LLADDR(sdl), ETH_ALEN);
            rc = 0;
        }
        break;
    }
    freeifaddrs(ifap);
    return rc;
}

struct l2_packet_data * l2_packet_init(const char *ifname, const u8 *own_addr,
                                       unsigned short protocol,
                                       void (*rx_callback)(void *ctx, const u8 *src_addr,
                                                           const u8 *buf, size_t len),
                                       void *rx_callback_ctx, int l2_hdr)
{
    struct l2_packet_data *l2;
    const char *step;

    if (os_strlen(ifname) >= IFNAMSIZ)
        return NULL;
    l2 = os_zalloc(sizeof(*l2));
    if (!l2)
        return NULL;
    os_strlcpy(l2->ifname, ifname, sizeof(l2->ifname));
    l2->rx_callback = rx_callback;
    l2->rx_callback_ctx = rx_callback_ctx;
    l2->l2_hdr = l2_hdr;
    if (own_addr) {
        os_memcpy(l2->own_addr, own_addr, ETH_ALEN);
    } else if (own_link_addr(ifname, l2->own_addr)) {
        wpa_printf(MSG_ERROR, "l2_packet_init: no link-level address for %s", ifname);
        os_free(l2);
        return NULL;
    }
    l2->fd = bpfl2_open(ifname, protocol, l2->own_addr, &l2->buflen, &step);
    if (l2->fd < 0) {
        wpa_printf(MSG_ERROR, "l2_packet_init: BPF on %s failed at %s: %s", ifname, step,
                   strerror(errno));
        os_free(l2);
        return NULL;
    }
    l2->buf = os_malloc(l2->buflen);
    if (!l2->buf) {
        close(l2->fd);
        os_free(l2);
        return NULL;
    }
    wpa_printf(MSG_DEBUG, "l2_packet_init: BPF on %s for 0x%04x, buffer %u bytes", ifname,
               protocol, l2->buflen);
    eloop_register_read_sock(l2->fd, l2_packet_receive, l2, NULL);
    return l2;
}

struct l2_packet_data * l2_packet_init_bridge(
    const char *br_ifname, const char *ifname, const u8 *own_addr, unsigned short protocol,
    void (*rx_callback)(void *ctx, const u8 *src_addr, const u8 *buf, size_t len),
    void *rx_callback_ctx, int l2_hdr)
{
    (void) ifname;
    return l2_packet_init(br_ifname, own_addr, protocol, rx_callback, rx_callback_ctx,
                          l2_hdr);
}

void l2_packet_deinit(struct l2_packet_data *l2)
{
    if (!l2)
        return;
    eloop_unregister_read_sock(l2->fd);
    close(l2->fd);
    os_free(l2->buf);
    os_free(l2);
}

int l2_packet_get_ip_addr(struct l2_packet_data *l2, char *buf, size_t len)
{
    struct ifaddrs *ifap, *ifa;
    const struct sockaddr_in *sin;
    int rc = -1;

    if (getifaddrs(&ifap) != 0)
        return -1;
    for (ifa = ifap; ifa; ifa = ifa->ifa_next) {
        if (!ifa->ifa_addr || ifa->ifa_addr->sa_family != AF_INET ||
            os_strcmp(ifa->ifa_name, l2->ifname) != 0)
            continue;
        sin = (const struct sockaddr_in *) ifa->ifa_addr;
        if (sin->sin_addr.s_addr == INADDR_ANY)
            continue;
        os_strlcpy(buf, inet_ntoa(sin->sin_addr), len);
        rc = 0;
        break;
    }
    freeifaddrs(ifap);
    return rc;
}

void l2_packet_notify_auth_start(struct l2_packet_data *l2)
{
    (void) l2;
}

int l2_packet_set_packet_filter(struct l2_packet_data *l2, enum l2_packet_filter_type type)
{
    (void) l2;
    (void) type;
    return -1;
}
