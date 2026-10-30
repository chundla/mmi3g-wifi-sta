/*
 * Control tool for the 88W8688 station driver (phase 5.2): asks it to join
 * or leave an open network, or to scan (phase 6), and prints its status
 * and scan results. See mv8688sta-ctl.h.
 *
 * usage: mv8688-ctl [-i if] [-w seconds] status | connect ssid | disconnect
 *                   | scan [ssid] | results
 *   -i  interface (default en6): opens /dev/io-net/<if>
 *   -w  wait this long for a request to finish (default 30)
 * exit: 0 done (connect: associated), 1 usage or open failure,
 *       2 the request failed or was refused, 3 it did not finish in time
 */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "mv8688sta-ctl.h"

static const char *state_name(uint32_t state)
{
    switch (state) {
    case MV8688STA_CTL_DOWN: return "down";
    case MV8688STA_CTL_IDLE: return "idle";
    case MV8688STA_CTL_CONNECTING: return "connecting";
    case MV8688STA_CTL_ASSOCIATED: return "associated";
    case MV8688STA_CTL_FAILED: return "FAILED";
    }
    return "?";
}

static int get_status(int fd, struct mv8688sta_ctl_status *st)
{
    int rc;
    memset(st, 0, sizeof(*st));
    if ((rc = devctl(fd, DCMD_MV8688STA_STATUS, st, sizeof(*st), NULL)) != EOK) {
        fprintf(stderr, "mv8688-ctl: status: %s\n", strerror(rc));
        return -1;
    }
    if (st->version != MV8688STA_CTL_VERSION) {
        fprintf(stderr, "mv8688-ctl: driver status version %u, expected %u\n",
                (unsigned)st->version, MV8688STA_CTL_VERSION);
        return -1;
    }
    return 0;
}

static void print_status(const struct mv8688sta_ctl_status *st)
{
    const uint8_t *m = st->mac, *b = st->bssid;
    printf("state=%s requested=%u done=%u last_rc=%d last_step=%s\n", state_name(st->state),
           (unsigned)st->requested, (unsigned)st->done, (int)st->last_rc,
           st->last_step[0] ? st->last_step : "-");
    printf("mac=%02x:%02x:%02x:%02x:%02x:%02x bssid=%02x:%02x:%02x:%02x:%02x:%02x "
           "channel=%d rssi=%u (~%d dBm) ssid_len=%u assoc_status=0x%04x aid=0x%04x\n",
           m[0], m[1], m[2], m[3], m[4], m[5], b[0], b[1], b[2], b[3], b[4], b[5],
           (int)st->channel, (unsigned)st->rssi, 3 - (int)st->rssi, (unsigned)st->ssid_len,
           st->assoc_status, st->aid);
    printf("fw_release=0x%08x connects=%u link_losses=%u link_cause=0x%08x\n",
           (unsigned)st->fw_release, (unsigned)st->connects, (unsigned)st->link_losses,
           (unsigned)st->link_cause);
    printf("tx_frames=%u tx_bytes=%u tx_errors=%u tx_drops=%u\n", (unsigned)st->tx_frames,
           (unsigned)st->tx_bytes, (unsigned)st->tx_errors, (unsigned)st->tx_drops);
    printf("rx_frames=%u rx_bytes=%u rx_bad=%u rx_drops=%u rx_up_pending=%u\n",
           (unsigned)st->rx_frames, (unsigned)st->rx_bytes, (unsigned)st->rx_bad,
           (unsigned)st->rx_drops, (unsigned)st->rx_up_pending);
    printf("events=%u stray_responses=%u aborts=%u transport_errors=%u polls=%u\n",
           (unsigned)st->events, (unsigned)st->stray_responses, (unsigned)st->aborts,
           (unsigned)st->transport_errors, (unsigned)st->polls);
    printf("rsn=%u scans_done=%u results=%u key_sets=%u mic_ucast=%u mic_mcast=%u\n",
           (unsigned)st->rsn, (unsigned)st->scans_done, (unsigned)st->results,
           (unsigned)st->key_sets, (unsigned)st->mic_ucast, (unsigned)st->mic_mcast);
}

/* One line per entry: the SSID, then what a supplicant matches on: the
 * privacy bit, an RSN element and a WPA element. */
static int print_results(int fd)
{
    static const uint8_t wpa_oui[4] = { 0x00, 0x50, 0xf2, 0x01 };
    struct mv8688sta_ctl_bss bss;
    uint32_t i, scan = 0, count = 0;
    unsigned k;
    int rc;

    for (i = 0; ; i++) {
        const uint8_t *ie, *end, *ssid = NULL;
        unsigned ssid_len = 0, rsn = 0, wpa = 0;
        memset(&bss, 0, sizeof(bss));
        bss.index = i;
        /* An error reply carries no data: the list ends at ENOENT. */
        rc = devctl(fd, DCMD_MV8688STA_SCAN_RESULT, &bss, sizeof(bss), NULL);
        if (rc == ENOENT && i == count)
            break;
        if (rc != EOK) {
            fprintf(stderr, "mv8688-ctl: results: %s\n", strerror(rc));
            return -1;
        }
        count = bss.count;
        if (i && bss.scan != scan) {
            fprintf(stderr, "mv8688-ctl: results replaced by another scan while read\n");
            return -1;
        }
        scan = bss.scan;
        end = bss.ie + (bss.ie_len < sizeof(bss.ie) ? bss.ie_len : sizeof(bss.ie));
        for (ie = bss.ie; end - ie >= 2 && ie[1] <= end - ie - 2; ie += 2 + ie[1]) {
            if (ie[0] == 0 && !ssid) {
                ssid = ie + 2;
                ssid_len = ie[1];
            }
            rsn |= ie[0] == 48;
            wpa |= ie[0] == 221 && ie[1] >= 4 && !memcmp(ie + 2, wpa_oui, 4);
        }
        printf("bss %u: %02x:%02x:%02x:%02x:%02x:%02x ch=%d rssi=%u (~%d dBm) cap=0x%04x "
               "privacy=%d rsn=%u wpa=%u ies=%u%s ssid=\"", (unsigned)i, bss.bssid[0],
               bss.bssid[1], bss.bssid[2], bss.bssid[3], bss.bssid[4], bss.bssid[5],
               (int)bss.channel, bss.rssi, 3 - (int)bss.rssi, bss.capability,
               (bss.capability & 0x10) != 0, rsn, wpa, (unsigned)bss.ie_len,
               bss.ie_cut ? " (cut)" : "");
        for (k = 0; k < ssid_len; k++)
            putchar(ssid[k] >= 0x20 && ssid[k] < 0x7f && ssid[k] != '"' ? ssid[k] : '?');
        printf("\"\n");
    }
    printf("results=%u scan=%u\n", (unsigned)i, (unsigned)scan);
    return 0;
}

static void usage(void)
{
    fprintf(stderr, "usage: mv8688-ctl [-i if] [-w seconds] status | connect ssid | disconnect\n"
                    "                  | scan [ssid] | results\n");
    exit(1);
}

int main(int argc, char **argv)
{
    struct mv8688sta_ctl_status st;
    struct mv8688sta_ctl_connect conn;
    struct mv8688sta_ctl_request req;
    struct mv8688sta_ctl_scan scan;
    const char *ifname = "en6", *verb;
    char path[64];
    uint32_t number = 0;
    long wait = 30, waited;
    int option, fd, rc = EINVAL;

    while ((option = getopt(argc, argv, "i:w:")) != -1) {
        switch (option) {
        case 'i': ifname = optarg; break;
        case 'w': wait = strtol(optarg, NULL, 10); break;
        default: usage();
        }
    }
    if (optind >= argc || wait < 1 || strlen(ifname) > 16 || strchr(ifname, '/'))
        usage();
    verb = argv[optind++];
    snprintf(path, sizeof(path), "/dev/io-net/%s", ifname);
    if ((fd = open(path, O_RDWR)) == -1) {
        fprintf(stderr, "mv8688-ctl: %s: %s\n", path, strerror(errno));
        return 1;
    }
    if (!strcmp(verb, "status") && optind == argc) {
        if (get_status(fd, &st))
            return 2;
        print_status(&st);
        return 0;
    }
    if (!strcmp(verb, "results") && optind == argc)
        return print_results(fd) ? 2 : 0;
    if (!strcmp(verb, "connect") && optind + 1 == argc) {
        memset(&conn, 0, sizeof(conn));
        conn.ssid_len = (uint32_t)strlen(argv[optind]);
        if (conn.ssid_len < 1 || conn.ssid_len > 32)
            usage();
        memcpy(conn.ssid, argv[optind], conn.ssid_len);
        rc = devctl(fd, DCMD_MV8688STA_CONNECT, &conn, sizeof(conn), NULL);
        number = conn.request;
    } else if (!strcmp(verb, "scan") && optind + 1 >= argc) {
        memset(&scan, 0, sizeof(scan));
        if (optind < argc) {
            scan.ssid_len = (uint32_t)strlen(argv[optind]);
            if (scan.ssid_len < 1 || scan.ssid_len > 32)
                usage();
            memcpy(scan.ssid, argv[optind], scan.ssid_len);
        }
        rc = devctl(fd, DCMD_MV8688STA_SCAN, &scan, sizeof(scan), NULL);
        number = scan.request;
    } else if (!strcmp(verb, "disconnect") && optind == argc) {
        memset(&req, 0, sizeof(req));
        rc = devctl(fd, DCMD_MV8688STA_DISCONNECT, &req, sizeof(req), NULL);
        number = req.request;
    } else {
        usage();
    }
    if (rc != EOK) {
        fprintf(stderr, "mv8688-ctl: %s refused: %s\n", verb, strerror(rc));
        return 2;
    }
    printf("request=%u\n", (unsigned)number);
    for (waited = 0; ; waited++) {
        if (get_status(fd, &st))
            return 2;
        if ((int32_t)(st.done - number) >= 0)
            break;
        if (waited >= wait * 10) {
            print_status(&st);
            fprintf(stderr, "mv8688-ctl: %s did not finish in %ld s\n", verb, wait);
            return 3;
        }
        usleep(100000);
    }
    print_status(&st);
    if (!strcmp(verb, "scan") && print_results(fd))
        return 2;
    if (st.last_rc || (!strcmp(verb, "connect") && st.state != MV8688STA_CTL_ASSOCIATED))
        return 2;
    return 0;
}
