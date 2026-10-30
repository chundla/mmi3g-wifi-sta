/*
 * Phase 7 network check. The unit has no nc, wget or ftp, so this answers
 * "do the route and the resolver work" without the HMI, and measures a
 * download for the throughput figure 6.3 lacked:
 *
 *   resolve  the name through the unit's resolver (gethostbyname)
 *   connect  a TCP connection to the first address; shows the local
 *            address the route chose
 *   get      one plain-HTTP/1.0 GET; status, header and body bytes, time
 *            and rate. The body is counted and discarded.
 *
 * Everything runs under one deadline (-w, default 10 s); an alarm ends the
 * program there, whatever call it is in. The resolver configuration
 * (_CS_RESOLVE, which QNX prefers to /etc/resolv.conf) is printed first.
 *
 * usage: netcheck [-w seconds] resolve name
 *        netcheck [-w seconds] connect host port
 *        netcheck [-w seconds] [-m max-bytes] get http://host[:port][/path]
 * exit: 0 done (get: an HTTP status line came), 1 usage, 2 failed,
 *       3 deadline reached
 */
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/time.h>

#define HOST_MAX    128
#define MAX_BYTES   (16L << 20)
#define LINE_SHOWN  80

/* http://host[:port][/path]; host is letters, digits, '.', '-'. Returns 0,
 * or -1 when the URL is not of that form. */
int nc_parse_url(const char *url, char *host, unsigned *port, const char **path)
{
    const char *p = url, *end;
    size_t n;
    if (strncmp(p, "http://", 7))
        return -1;
    p += 7;
    for (end = p; (*end >= 'a' && *end <= 'z') || (*end >= 'A' && *end <= 'Z') ||
                  (*end >= '0' && *end <= '9') || *end == '.' || *end == '-'; end++)
        ;
    n = (size_t)(end - p);
    if (!n || n >= HOST_MAX)
        return -1;
    memcpy(host, p, n);
    host[n] = 0;
    *port = 80;
    if (*end == ':') {
        unsigned long v = 0;
        const char *d = ++end;
        while (*end >= '0' && *end <= '9' && end - d < 6)
            v = v * 10 + (unsigned long)(*end++ - '0');
        if (end == d || v < 1 || v > 65535)
            return -1;
        *port = (unsigned)v;
    }
    if (*end && *end != '/')
        return -1;
    *path = *end ? end : "/";
    for (p = *path; *p; p++)
        if (*p <= ' ' || *p > '~')
            return -1;
    return 0;
}

/* The status code of "HTTP/1.x NNN ...", or -1. */
int nc_status(const char *buf, size_t len)
{
    if (len < 12 || strncmp(buf, "HTTP/1.", 7) || buf[8] != ' ' ||
        buf[9] < '1' || buf[9] > '5' || buf[10] < '0' || buf[10] > '9' ||
        buf[11] < '0' || buf[11] > '9')
        return -1;
    return (buf[9] - '0') * 100 + (buf[10] - '0') * 10 + (buf[11] - '0');
}

/* The offset just past the blank line ending the headers, or -1. */
long nc_header_end(const char *buf, size_t len)
{
    size_t i;
    for (i = 0; i + 4 <= len; i++)
        if (!memcmp(buf + i, "\r\n\r\n", 4))
            return (long)(i + 4);
    return -1;
}

#ifndef NETCHECK_NO_MAIN

static unsigned t0;

static unsigned now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (unsigned)ts.tv_sec * 1000 + (unsigned)(ts.tv_nsec / 1000000);
}

static void deadline(int sig)
{
    static const char msg[] = "result: deadline reached\n";
    ssize_t n = write(1, msg, sizeof(msg) - 1);
    (void)sig;
    (void)n;
    _exit(3);
}

static void usage(void)
{
    fprintf(stderr, "usage: netcheck [-w seconds] resolve name\n"
                    "       netcheck [-w seconds] connect host port\n"
                    "       netcheck [-w seconds] [-m max-bytes] get http://host[:port][/path]\n");
    exit(1);
}

static void show_resolver(void)
{
#ifdef _CS_RESOLVE
    char buf[256], *p;
    size_t n = confstr(_CS_RESOLVE, buf, sizeof(buf));
    if (n == 0) {
        printf("resolver: _CS_RESOLVE unset\n");
        return;
    }
    for (p = buf; *p; p++)
        if (*p == '\n' || *p < ' ' || *p > '~')
            *p = ';';
    printf("resolver: _CS_RESOLVE=%s%s\n", buf, n > sizeof(buf) ? " (cut)" : "");
#else
    printf("resolver: no _CS_RESOLVE on this system\n");
#endif
}

static int resolve(const char *name, struct in_addr *first)
{
    struct hostent *h;
    unsigned start = now_ms(), i;
    if (!(h = gethostbyname(name))) {
        printf("resolve: %s FAILED after %u ms: h_errno %d\n", name, now_ms() - start,
               h_errno);
        return -1;
    }
    if (h->h_addrtype != AF_INET || h->h_length != 4 || !h->h_addr_list[0]) {
        printf("resolve: %s FAILED after %u ms: no IPv4 address\n", name, now_ms() - start);
        return -1;
    }
    for (i = 0; h->h_addr_list[i] && i < 8; i++) {
        struct in_addr a;
        memcpy(&a, h->h_addr_list[i], 4);
        printf("resolve: %s -> %s\n", name, inet_ntoa(a));
    }
    printf("resolve: %u address%s in %u ms\n", i, i == 1 ? "" : "es", now_ms() - start);
    memcpy(first, h->h_addr_list[0], 4);
    return 0;
}

static int connect_to(const char *host, unsigned port)
{
    struct sockaddr_in sa, local;
    socklen_t len = sizeof(local);
    struct in_addr addr;
    unsigned start;
    fd_set wr;
    int fd, err = 0, flags;
    if (resolve(host, &addr))
        return -1;
    if ((fd = socket(AF_INET, SOCK_STREAM, 0)) == -1) {
        printf("connect: socket FAILED: %s\n", strerror(errno));
        return -1;
    }
    memset(&sa, 0, sizeof(sa));
    sa.sin_family = AF_INET;
    sa.sin_port = htons((unsigned short)port);
    sa.sin_addr = addr;
    flags = fcntl(fd, F_GETFL, 0);
    fcntl(fd, F_SETFL, flags | O_NONBLOCK);
    start = now_ms();
    if (connect(fd, (struct sockaddr *)&sa, sizeof(sa)) == -1) {
        if (errno != EINPROGRESS) {
            err = errno;
        } else {
            socklen_t elen = sizeof(err);
            FD_ZERO(&wr);
            FD_SET(fd, &wr);
            /* The alarm bounds the wait. */
            if (select(fd + 1, NULL, &wr, NULL, NULL) != 1 ||
                getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &elen) == -1)
                err = errno ? errno : EIO;
        }
    }
    if (err) {
        printf("connect: %s:%u FAILED after %u ms: %s\n", inet_ntoa(addr), port,
               now_ms() - start, strerror(err));
        close(fd);
        return -1;
    }
    fcntl(fd, F_SETFL, flags);
    if (getsockname(fd, (struct sockaddr *)&local, &len) == 0) {
        /* inet_ntoa returns one static buffer: one call per printf. */
        char peer[16];
        snprintf(peer, sizeof(peer), "%s", inet_ntoa(addr));
        printf("connect: %s:%u in %u ms, local %s:%u\n", peer, port, now_ms() - start,
               inet_ntoa(local.sin_addr), ntohs(local.sin_port));
    } else
        printf("connect: %s:%u in %u ms\n", inet_ntoa(addr), port, now_ms() - start);
    return fd;
}

static int get(const char *url, long max_bytes)
{
    char host[HOST_MAX], req[512], buf[8192], head[1024];
    const char *path;
    unsigned port, start, first = 0;
    long total = 0, hdr_end = -1;
    size_t head_len = 0;
    ssize_t n = 0;
    int fd, status = -1, len;
    if (nc_parse_url(url, host, &port, &path)) {
        printf("get: not a plain http://host[:port][/path] URL\n");
        return 1;
    }
    if ((fd = connect_to(host, port)) == -1)
        return 2;
    len = snprintf(req, sizeof(req), "GET %s HTTP/1.0\r\nHost: %s\r\nUser-Agent: netcheck\r\n"
                   "Connection: close\r\n\r\n", path, host);
    if (len < 0 || len >= (int)sizeof(req) || write(fd, req, (size_t)len) != len) {
        printf("get: request FAILED: %s\n", len >= (int)sizeof(req) ? "too long" :
               strerror(errno));
        close(fd);
        return 2;
    }
    start = now_ms();
    while (total < max_bytes && (n = read(fd, buf, sizeof(buf))) > 0) {
        if (!total)
            first = now_ms() - start;
        if (hdr_end < 0 && head_len < sizeof(head)) {
            size_t take = (size_t)n < sizeof(head) - head_len ? (size_t)n
                                                                : sizeof(head) - head_len;
            memcpy(head + head_len, buf, take);
            head_len += take;
            if ((hdr_end = nc_header_end(head, head_len)) >= 0 || head_len == sizeof(head))
                status = nc_status(head, head_len);
        }
        total += n;
    }
    if (n == -1 && total < max_bytes)
        printf("get: read FAILED after %ld bytes: %s\n", total, strerror(errno));
    close(fd);
    if (status < 0 && head_len)
        status = nc_status(head, head_len);
    if (head_len) {
        size_t i, shown = 0;
        char line[LINE_SHOWN + 1];
        for (i = 0; i < head_len && head[i] != '\r' && head[i] != '\n' && shown < LINE_SHOWN;
             i++)
            line[shown++] = head[i] >= ' ' && head[i] <= '~' ? head[i] : '?';
        line[shown] = 0;
        printf("get: status line \"%s\"\n", line);
    }
    {
        unsigned ms = now_ms() - start;
        long body = hdr_end >= 0 ? total - hdr_end : 0;
        printf("result: status=%d header_bytes=%ld body_bytes=%ld total_bytes=%ld "
               "first_byte_ms=%u elapsed_ms=%u rate=%ld B/s%s\n", status,
               hdr_end >= 0 ? hdr_end : total, body, total, first, ms,
               ms ? (long)((double)total * 1000 / ms) : 0,
               total >= max_bytes ? " (stopped at -m)" : "");
    }
    return status < 0 ? 2 : 0;
}

int main(int argc, char **argv)
{
    struct in_addr addr;
    long wait = 10, max_bytes = MAX_BYTES, port;
    int option, rc = 1, fd;

    while ((option = getopt(argc, argv, "w:m:")) != -1) {
        switch (option) {
        case 'w': wait = strtol(optarg, NULL, 10); break;
        case 'm': max_bytes = strtol(optarg, NULL, 10); break;
        default: usage();
        }
    }
    if (optind >= argc || wait < 1 || wait > 600 || max_bytes < 1 || max_bytes > (1L << 30))
        usage();
    signal(SIGALRM, deadline);
    alarm((unsigned)wait);
    t0 = now_ms();
    setvbuf(stdout, NULL, _IOLBF, 0);
    show_resolver();
    if (!strcmp(argv[optind], "resolve") && argc - optind == 2) {
        rc = resolve(argv[optind + 1], &addr) ? 2 : 0;
    } else if (!strcmp(argv[optind], "connect") && argc - optind == 3) {
        port = strtol(argv[optind + 2], NULL, 10);
        if (port < 1 || port > 65535)
            usage();
        if ((fd = connect_to(argv[optind + 1], (unsigned)port)) == -1) {
            rc = 2;
        } else {
            close(fd);
            rc = 0;
        }
    } else if (!strcmp(argv[optind], "get") && argc - optind == 2) {
        rc = get(argv[optind + 1], max_bytes);
    } else {
        usage();
    }
    printf("netcheck: exit %d after %u ms\n", rc, now_ms() - t0);
    return rc;
}

#endif
