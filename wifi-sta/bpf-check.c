/*
 * Phase 6.0 BPF check: does BPF on an io-net interface behind devnp-shim
 * carry frames both ways, as wpa_supplicant's EAPOL path will need on
 * en6? Run on en5 (devn-asix through the same shim), it sends one ARP
 * request through BPF, the way the supplicant's l2_packet sends, and reads
 * ARP frames through BPF until the target answers or the wait ends. No
 * radio is involved, and nothing is configured on the interface.
 *
 * Listen mode (-l, 6.1 broadcast count on en6) sends nothing: for the
 * given time it reads IPv4 frames through BPF and logs each numbered UDP
 * broadcast from the bench ("mmi-bench-bcast <n>" to the given port), so
 * the numbers missing from the log are the broadcasts the station lost.
 *
 * Connection mode (-q, phase 7) also sends nothing: for the given time it
 * logs the unit's own DNS queries and TCP connection attempts on the
 * interface, and the replies to them (connlog.h), headers only. Its
 * filter keeps the unit's own frames, which the other modes drop. With
 * -d it also logs, in hex, the payload of every TCP segment between the
 * unit and that address and port, for reading a plain-HTTP exchange.
 *
 * usage: bpf-check -i if -m mac -s source-ip -t target-ip [-w seconds]
 *        bpf-check -i if -m mac -l port [-w seconds]
 *        bpf-check -i if -m mac -q [-d ip:port] [-w seconds]
 *   -m  the interface's own MAC (from ifconfig); frames from it are filtered
 *       (connection mode: frames from or to it are the ones logged)
 * exit: 0 the reply came through BPF (listen mode: a numbered broadcast
 *       came; connection mode: an event was logged), 1 usage or BPF set-up
 *       failure, 2 none in time
 */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <sys/ioctl.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <net/if.h>
#include <net/bpf.h>
#include "bpfl2.h"
#include "connlog.h"

#define ETH_ARP     0x0806
#define ETH_IPV4    0x0800
#define FRAMES_SHOWN 10
#define BCAST_TAG   "mmi-bench-bcast "

static unsigned now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (unsigned)ts.tv_sec * 1000 + (unsigned)(ts.tv_nsec / 1000000);
}

static int parse_mac(const char *text, uint8_t mac[6])
{
    unsigned v[6], i;
    char end;
    if (sscanf(text, "%2x:%2x:%2x:%2x:%2x:%2x%c", &v[0], &v[1], &v[2], &v[3], &v[4], &v[5],
               &end) != 6)
        return -1;
    for (i = 0; i < 6; i++)
        mac[i] = (uint8_t)v[i];
    return 0;
}

static void usage(void)
{
    fprintf(stderr, "usage: bpf-check -i if -m mac -s source-ip -t target-ip [-w seconds]\n"
                    "       bpf-check -i if -m mac -l port [-w seconds]\n"
                    "       bpf-check -i if -m mac -q [-d ip:port] [-w seconds]\n");
    exit(1);
}

/* The bench broadcast's number, or -1 when the frame is not one: an
 * Ethernet broadcast carrying IPv4/UDP to port with the tag. */
static long bcast_number(const uint8_t *f, unsigned caplen, unsigned port)
{
    static const uint8_t all[6] = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff };
    const uint8_t *ip = f + 14, *udp, *data;
    unsigned ihl, len, i;
    long n = 0;
    if (caplen < 14 + 20 || memcmp(f, all, 6) || f[12] != 0x08 || f[13] != 0x00 ||
        ip[0] >> 4 != 4 || ip[9] != 17)
        return -1;
    ihl = (ip[0] & 0x0f) * 4;
    if (ihl < 20 || caplen < 14 + ihl + 8)
        return -1;
    udp = ip + ihl;
    if (((unsigned)udp[2] << 8 | udp[3]) != port)
        return -1;
    data = udp + 8;
    len = caplen - (unsigned)(data - f);
    if (len <= sizeof(BCAST_TAG) - 1 || memcmp(data, BCAST_TAG, sizeof(BCAST_TAG) - 1))
        return -1;
    for (i = sizeof(BCAST_TAG) - 1; i < len && i < sizeof(BCAST_TAG) + 5; i++) {
        if (data[i] < '0' || data[i] > '9')
            break;
        n = n * 10 + (data[i] - '0');
    }
    return i == sizeof(BCAST_TAG) - 1 ? -1 : n;
}

/* Listen mode: log each numbered broadcast for the wait; send nothing. */
static int listen_bcast(int fd, uint8_t *buf, unsigned buflen, unsigned port, long wait)
{
    struct bpf_stat stats;
    struct timeval tv;
    fd_set rd;
    unsigned caplen, t0 = now_ms(), elapsed, frames = 0, bcasts = 0, numbered = 0;
    long n, high = -1;
    const uint8_t *f;
    size_t off;
    ssize_t got;
    int rc;

    while ((elapsed = now_ms() - t0) < (unsigned)wait * 1000) {
        unsigned left = (unsigned)wait * 1000 - elapsed;
        FD_ZERO(&rd);
        FD_SET(fd, &rd);
        tv.tv_sec = left / 1000;
        tv.tv_usec = (left % 1000) * 1000;
        if ((rc = select(fd + 1, &rd, NULL, NULL, &tv)) == -1 && errno != EINTR) {
            printf("bpf: select FAILED: %s\n", strerror(errno));
            break;
        }
        if (rc <= 0)
            continue;
        if ((got = read(fd, buf, buflen)) == -1) {
            printf("bpf: read FAILED: %s\n", strerror(errno));
            break;
        }
        for (off = 0; (rc = bpfl2_next(buf, (size_t)got, &off, &f, &caplen)) == 1; ) {
            frames++;
            if (caplen >= 6 && f[0] == 0xff && f[1] == 0xff)
                bcasts++;
            if ((n = bcast_number(f, caplen, port)) < 0)
                continue;
            numbered++;
            if (n > high)
                high = n;
            printf("bcast: %ld at %u ms\n", n, now_ms() - t0);
        }
        if (rc == -1)
            printf("bpf: a record did not fit the %d bytes read\n", (int)got);
        fflush(stdout);
    }
    memset(&stats, 0, sizeof(stats));
    if (ioctl(fd, BIOCGSTATS, &stats) == -1)
        printf("bpf: BIOCGSTATS FAILED: %s\n", strerror(errno));
    printf("result: ipv4_frames=%u broadcasts=%u numbered=%u highest=%ld bpf_recv=%u "
           "bpf_drop=%u elapsed=%u ms\n", frames, bcasts, numbered, high, stats.bs_recv,
           stats.bs_drop, now_ms() - t0);
    return numbered ? 0 : 2;
}

/* Connection mode: log the unit's DNS and TCP events for the wait, and the
 * payload to and from peer:port when port is not 0. */
static int listen_conn(int fd, uint8_t *buf, unsigned buflen, const uint8_t own[6], long wait,
                       const uint8_t peer[4], unsigned port)
{
    static char data[CONNLOG_DATA_MAX];
    struct bpf_stat stats;
    struct timeval tv;
    fd_set rd;
    unsigned caplen, t0 = now_ms(), elapsed, frames = 0, events = 0;
    const uint8_t *f;
    char line[CONNLOG_LINE_MAX];
    size_t off;
    ssize_t got;
    int rc;

    while ((elapsed = now_ms() - t0) < (unsigned)wait * 1000) {
        unsigned left = (unsigned)wait * 1000 - elapsed;
        FD_ZERO(&rd);
        FD_SET(fd, &rd);
        tv.tv_sec = left / 1000;
        tv.tv_usec = (left % 1000) * 1000;
        if ((rc = select(fd + 1, &rd, NULL, NULL, &tv)) == -1 && errno != EINTR) {
            printf("bpf: select FAILED: %s\n", strerror(errno));
            break;
        }
        if (rc <= 0)
            continue;
        if ((got = read(fd, buf, buflen)) == -1) {
            printf("bpf: read FAILED: %s\n", strerror(errno));
            break;
        }
        for (off = 0; (rc = bpfl2_next(buf, (size_t)got, &off, &f, &caplen)) == 1; ) {
            frames++;
            if (port && connlog_payload(f, caplen, own, peer, port, data))
                printf("conn: %u ms %s\n", now_ms() - t0, data);
            if (!connlog_event(f, caplen, own, line))
                continue;
            events++;
            printf("conn: %u ms %s\n", now_ms() - t0, line);
        }
        if (rc == -1)
            printf("bpf: a record did not fit the %d bytes read\n", (int)got);
        fflush(stdout);
    }
    memset(&stats, 0, sizeof(stats));
    if (ioctl(fd, BIOCGSTATS, &stats) == -1)
        printf("bpf: BIOCGSTATS FAILED: %s\n", strerror(errno));
    printf("result: ipv4_frames=%u events=%u bpf_recv=%u bpf_drop=%u elapsed=%u ms\n", frames,
           events, stats.bs_recv, stats.bs_drop, now_ms() - t0);
    return events ? 0 : 2;
}

static void ip_text(const uint8_t *p, char *out)
{
    snprintf(out, 16, "%u.%u.%u.%u", p[0], p[1], p[2], p[3]);
}

int main(int argc, char **argv)
{
    const char *ifname = NULL, *step;
    static const uint8_t nobody[6];
    uint8_t mac[6], frame[60], *buf;
    struct in_addr src, dst, peer;
    struct bpf_stat stats;
    struct timeval tv;
    fd_set rd;
    unsigned buflen, caplen, t0, elapsed, frames = 0, replies = 0;
    const uint8_t *f;
    size_t off;
    ssize_t got;
    long wait = 5, port = 0, peer_port = 0;
    char *colon;
    int option, fd, have_mac = 0, have_src = 0, have_dst = 0, conn = 0, rc;
    char a[16], b[16];

    peer.s_addr = 0;
    while ((option = getopt(argc, argv, "i:m:s:t:w:l:qd:")) != -1) {
        switch (option) {
        case 'i': ifname = optarg; break;
        case 'm': have_mac = parse_mac(optarg, mac) == 0; break;
        case 's': have_src = inet_aton(optarg, &src); break;
        case 't': have_dst = inet_aton(optarg, &dst); break;
        case 'w': wait = strtol(optarg, NULL, 10); break;
        case 'l': port = strtol(optarg, NULL, 10); break;
        case 'q': conn = 1; break;
        case 'd':
            if (!(colon = strchr(optarg, ':')))
                usage();
            *colon = '\0';
            if (!inet_aton(optarg, &peer))
                usage();
            peer_port = strtol(colon + 1, NULL, 10);
            if (peer_port < 1 || peer_port > 65535)
                usage();
            break;
        default: usage();
        }
    }
    if (!ifname || !have_mac || optind != argc || strlen(ifname) >= IFNAMSIZ ||
        (peer_port && !conn))
        usage();
    if (conn ? port || have_src || have_dst || wait < 1 || wait > 3600
             : port ? have_src || have_dst || port > 65535 || wait < 1 || wait > 900
             : !have_src || !have_dst || wait < 1 || wait > 60)
        usage();
    /* No frame comes from the all-zero address, so that filter keeps the
     * unit's own frames as well. */
    if ((fd = bpfl2_open(ifname, port || conn ? ETH_IPV4 : ETH_ARP, conn ? nobody : mac,
                         &buflen, &step)) == -1) {
        printf("bpf: set-up FAILED at %s: %s\n", step, strerror(errno));
        return 1;
    }
    if (!(buf = malloc(buflen))) {
        printf("bpf: no memory for %u bytes\n", buflen);
        return 1;
    }
    printf("bpf: /dev/bpf on %s, buffer %u bytes, %s %s %02x:%02x:%02x:%02x:%02x:%02x\n",
           ifname, buflen, port || conn ? "IPv4" : "ARP", conn ? "from or to" : "not from",
           mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    if (conn) {
        printf("bpf: logging DNS and TCP connection attempts for %lds\n", wait);
        if (peer_port)
            printf("bpf: and TCP payload to and from %s:%ld\n", inet_ntoa(peer), peer_port);
        fflush(stdout);
        rc = listen_conn(fd, buf, buflen, mac, wait, (const uint8_t *)&peer,
                         (unsigned)peer_port);
        close(fd);
        return rc;
    }
    if (port) {
        printf("bpf: listening %lds for numbered broadcasts to UDP port %ld\n", wait, port);
        fflush(stdout);
        rc = listen_bcast(fd, buf, buflen, (unsigned)port, wait);
        close(fd);
        return rc;
    }

    /* Who has target? Tell source; padded to the 60-byte minimum. */
    memset(frame, 0, sizeof(frame));
    memset(frame, 0xff, 6);
    memcpy(frame + 6, mac, 6);
    frame[12] = 0x08, frame[13] = 0x06;
    memcpy(frame + 14, "\x00\x01\x08\x00\x06\x04\x00\x01", 8);
    memcpy(frame + 22, mac, 6);
    memcpy(frame + 28, &src, 4);
    memcpy(frame + 38, &dst, 4);
    t0 = now_ms();
    got = write(fd, frame, sizeof(frame));
    printf("bpf: write of the %u-byte ARP request returned %d%s%s\n", (unsigned)sizeof(frame),
           (int)got, got == -1 ? " errno " : "", got == -1 ? strerror(errno) : "");

    while ((elapsed = now_ms() - t0) < (unsigned)wait * 1000 && !replies) {
        unsigned left = (unsigned)wait * 1000 - elapsed;
        FD_ZERO(&rd);
        FD_SET(fd, &rd);
        tv.tv_sec = left / 1000;
        tv.tv_usec = (left % 1000) * 1000;
        if ((rc = select(fd + 1, &rd, NULL, NULL, &tv)) == -1 && errno != EINTR) {
            printf("bpf: select FAILED: %s\n", strerror(errno));
            break;
        }
        if (rc <= 0)
            continue;
        if ((got = read(fd, buf, buflen)) == -1) {
            printf("bpf: read FAILED: %s\n", strerror(errno));
            break;
        }
        for (off = 0; (rc = bpfl2_next(buf, (size_t)got, &off, &f, &caplen)) == 1; ) {
            unsigned op = caplen >= 42 ? (unsigned)f[20] << 8 | f[21] : 0;
            frames++;
            if (caplen >= 42) {
                ip_text(f + 28, a);
                ip_text(f + 38, b);
            } else {
                strcpy(a, "?");
                strcpy(b, "?");
            }
            if (frames <= FRAMES_SHOWN)
                printf("bpf: frame %u at %u ms: %u bytes from %02x:%02x:%02x:%02x:%02x:%02x "
                       "op=%u sender=%s target=%s\n", frames, now_ms() - t0, caplen, f[6],
                       f[7], f[8], f[9], f[10], f[11], op, a, b);
            if (op == 2 && !memcmp(f + 28, &dst, 4) && !memcmp(f + 38, &src, 4) &&
                !memcmp(f, mac, 6))
                replies++;
        }
        if (rc == -1)
            printf("bpf: a record did not fit the %d bytes read\n", (int)got);
    }
    memset(&stats, 0, sizeof(stats));
    if (ioctl(fd, BIOCGSTATS, &stats) == -1)
        printf("bpf: BIOCGSTATS FAILED: %s\n", strerror(errno));
    printf("result: frames=%u replies=%u bpf_recv=%u bpf_drop=%u elapsed=%u ms\n", frames,
           replies, stats.bs_recv, stats.bs_drop, now_ms() - t0);
    close(fd);
    return replies ? 0 : 2;
}
