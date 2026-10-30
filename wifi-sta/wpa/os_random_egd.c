/*
 * os_get_random for wpa_supplicant on the MMI: the unit has neither
 * /dev/random nor /dev/urandom (docs/access/openssh-build.md), so random
 * bytes come from the local EGD service that already serves OpenSSH,
 * jitter-egd on 127.0.0.1:708 (docs/access/openssh-randomness.md). Each
 * call asks for exactly the bytes it needs with EGD command 2 (a blocking
 * read of up to 255 bytes) on a fresh connection, each within one 30 s
 * deadline. Nothing is kept between calls, and there is no weaker
 * fallback: when the service does not answer in time, the call fails, and
 * so does whatever needed the bytes (a 4-way handshake's SNonce).
 *
 * Built in place of os_unix.c's /dev/urandom reader (CONFIG_OS_RANDOM_EGD).
 *
 * This file is distributed under the terms of the BSD license, as
 * wpa_supplicant is; see wpa_supplicant's README.
 */
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <netinet/in.h>
#include <arpa/inet.h>

#ifndef EGD_PORT
#define EGD_PORT        708     /* OpenSSH's --with-prngd-port on the unit */
#endif
#define EGD_READ_MAX    255     /* one command 2 request */
#ifndef EGD_DEADLINE_MS
#define EGD_DEADLINE_MS 30000   /* per request; jitter-egd takes about 1 s per 40 bytes */
#endif

int os_get_random(unsigned char *buf, size_t len);

static long now_ms(void)
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
        return -1;
    return (long) ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* Waits until fd is readable (or writable), at most until the deadline.
 * Returns 0 when it is, -1 on timeout or error. */
static int wait_fd(int fd, int for_write, long deadline)
{
    struct timeval tv;
    fd_set set;
    long left, now;
    int rc;
    for (;;) {
        if ((now = now_ms()) < 0 || (left = deadline - now) <= 0)
            return -1;
        FD_ZERO(&set);
        FD_SET(fd, &set);
        tv.tv_sec = left / 1000;
        tv.tv_usec = (left % 1000) * 1000;
        rc = select(fd + 1, for_write ? NULL : &set, for_write ? &set : NULL, NULL, &tv);
        if (rc > 0)
            return 0;
        if (rc < 0 && errno != EINTR)
            return -1;
    }
}

/* One command 2 request on a fresh connection, all of it (connect, the
 * request and every read) within one deadline. */
static int egd_read(unsigned char *out, size_t n)
{
    struct sockaddr_in sin;
    unsigned char request[2];
    socklen_t elen = sizeof(int);
    size_t got = 0;
    ssize_t r;
    long deadline = now_ms();
    int fd, flags, err = 0, rc = -1;

    if (deadline < 0)
        return -1;
    deadline += EGD_DEADLINE_MS;
    if ((fd = socket(AF_INET, SOCK_STREAM, 0)) < 0)
        return -1;
    if ((flags = fcntl(fd, F_GETFL)) < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0)
        goto out;
    memset(&sin, 0, sizeof(sin));
    sin.sin_family = AF_INET;
    sin.sin_port = htons(EGD_PORT);
    sin.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (connect(fd, (struct sockaddr *) &sin, sizeof(sin)) < 0) {
        if (errno != EINPROGRESS || wait_fd(fd, 1, deadline) ||
            getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &elen) < 0 || err)
            goto out;
    }
    request[0] = 2;
    request[1] = (unsigned char) n;
    if (wait_fd(fd, 1, deadline) || write(fd, request, 2) != 2)
        goto out;
    while (got < n) {
        if (wait_fd(fd, 0, deadline))
            goto out;
        r = read(fd, out + got, n - got);
        if (r < 0 && (errno == EAGAIN || errno == EINTR))
            continue;
        if (r <= 0)
            goto out;
        got += (size_t) r;
    }
    rc = 0;
out:
    close(fd);
    return rc;
}

int os_get_random(unsigned char *buf, size_t len)
{
    size_t done = 0, n;
    while (done < len) {
        n = len - done < EGD_READ_MAX ? len - done : EGD_READ_MAX;
        if (egd_read(buf + done, n)) {
            memset(buf, 0, len);
            return -1;
        }
        done += n;
    }
    return 0;
}
