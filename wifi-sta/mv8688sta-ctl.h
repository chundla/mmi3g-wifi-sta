/*
 * Control interface of the 88W8688 station driver (devn-mv8688sta.so).
 *
 * devnp-shim attaches /dev/io-net/en<lan> for each io-net interface and
 * hands every devctl on it to the driver's devctl unchanged, with the
 * caller's credentials (devnp-shim.so raw_io_devctl and shim_ioctl
 * 0x801c040f; see docs/wifi-client/wifi-client-mode.md). Anyone may read
 * STATUS and SCAN_RESULT; the requests need root. A request (CONNECT,
 * DISCONNECT, and from version 2 SCAN, JOIN and SET_KEY) is only queued
 * for the driver's thread, one at a time (EBUSY while another is queued
 * or running); STATUS shows when it finished (done equal to the number
 * the request gave back) and how (last_rc), together with the state it
 * left. Frames the stack queued before a
 * request go out before it runs, so an EAPOL frame is not sent under a
 * key installed after it.
 *
 * Version 2 (phase 6) serves a host supplicant: SCAN fills the result
 * list SCAN_RESULT reads one entry at a time, JOIN associates with one
 * listed BSS carrying the supplicant's RSN or WPA element, and SET_KEY
 * installs the keys the 4-way and group handshakes derive.
 */
#ifndef MV8688STA_CTL_H
#define MV8688STA_CTL_H

#include <stdint.h>
#include <devctl.h>

#define MV8688STA_CTL_VERSION   2
#define MV8688STA_CTL_BSS_MAX   32
#define MV8688STA_CTL_IE_MAX    512     /* elements kept per BSS */
#define MV8688STA_CTL_WPA_IE_MAX 130    /* one RSN or WPA element, header included */

enum mv8688sta_ctl_state {
    MV8688STA_CTL_DOWN,         /* firmware not set up, or stopped */
    MV8688STA_CTL_IDLE,         /* not associated */
    MV8688STA_CTL_CONNECTING,   /* scanning or joining */
    MV8688STA_CTL_ASSOCIATED,
    MV8688STA_CTL_FAILED        /* the card stopped answering; destroy the interface */
};

struct mv8688sta_ctl_connect {
    uint32_t ssid_len;          /* 1-32 */
    uint8_t ssid[32];
    uint32_t request;           /* out: the request number */
};

struct mv8688sta_ctl_request {
    uint32_t request;           /* out: the request number */
};

/* Channels 1-11: active for the SSID when one is given, else passive. */
struct mv8688sta_ctl_scan {
    uint32_t ssid_len;          /* 0-32 */
    uint8_t ssid[32];
    uint32_t request;           /* out */
};

/* Entry index of the last finished scan's results. */
struct mv8688sta_ctl_bss {
    uint32_t index;             /* in */
    uint32_t scan;              /* out: scans_done when these results were kept */
    uint32_t count;             /* out: entries in the list */
    uint8_t bssid[6];
    uint16_t capability;
    uint16_t interval;          /* beacon interval, TU */
    uint8_t rssi;               /* as reported; about 3 - rssi dBm */
    uint8_t ie_cut;             /* more elements were heard than ie[] holds */
    int32_t channel;            /* DS parameter set, or -1 */
    uint32_t ie_len;
    uint8_t ie[MV8688STA_CTL_IE_MAX];   /* the elements, from the SSID on */
};

/* Associates with a BSS from the last scan's results; ie_len 0 for an
 * open network, else the supplicant's RSN or WPA element. */
struct mv8688sta_ctl_join {
    uint8_t bssid[6];
    uint8_t pad[2];
    uint32_t ssid_len;          /* 1-32 */
    uint8_t ssid[32];
    uint32_t ie_len;
    uint8_t ie[MV8688STA_CTL_WPA_IE_MAX];
    uint8_t pad2[2];
    uint32_t request;           /* out */
};

enum mv8688sta_ctl_cipher {
    MV8688STA_CTL_KEY_CLEAR,    /* key_len 0 */
    MV8688STA_CTL_KEY_TKIP,     /* 32 bytes: key, then the MIC keys */
    MV8688STA_CTL_KEY_CCMP      /* 16 bytes */
};

struct mv8688sta_ctl_key {
    uint32_t cipher;            /* enum mv8688sta_ctl_cipher */
    uint32_t pairwise;          /* 1: the pairwise key; 0: the group key */
    uint32_t key_len;
    uint8_t key[32];
    uint32_t request;           /* out */
};

struct mv8688sta_ctl_status {
    uint32_t version;           /* MV8688STA_CTL_VERSION */
    uint32_t state;             /* enum mv8688sta_ctl_state */
    uint32_t requested, done;   /* request numbers */
    int32_t last_rc;            /* of the last request: 0 or a layer code */
    char last_step[32];         /* where it failed */
    uint8_t mac[6];
    uint8_t bssid[6];           /* joined, or last chosen */
    int32_t channel;
    uint32_t rssi;              /* as reported; about 3 - rssi dBm */
    uint32_t ssid_len;
    uint8_t ssid[32];
    uint16_t assoc_status, aid;
    uint32_t fw_release;        /* GET_HW_SPEC, as reported */
    uint32_t connects, link_losses, link_cause;
    uint32_t tx_frames, tx_bytes, tx_errors, tx_drops;
    uint32_t rx_frames, rx_bytes, rx_bad, rx_drops, rx_up_pending;
    uint32_t events, stray_responses, aborts, transport_errors;
    uint32_t polls;
    /* Version 2. */
    uint32_t rsn;               /* the last join carried an RSN or WPA element */
    uint32_t scans_done, results;   /* finished scans; entries the last one kept */
    uint32_t key_sets;          /* keys installed or cleared */
    uint32_t mic_ucast, mic_mcast;  /* TKIP MIC failures the firmware reported */
};

#define DCMD_MV8688STA_STATUS     __DIOF(_DCMD_NET, 0xe0, struct mv8688sta_ctl_status)
#define DCMD_MV8688STA_CONNECT    __DIOTF(_DCMD_NET, 0xe1, struct mv8688sta_ctl_connect)
#define DCMD_MV8688STA_DISCONNECT __DIOF(_DCMD_NET, 0xe2, struct mv8688sta_ctl_request)
#define DCMD_MV8688STA_SCAN       __DIOTF(_DCMD_NET, 0xe3, struct mv8688sta_ctl_scan)
#define DCMD_MV8688STA_SCAN_RESULT __DIOTF(_DCMD_NET, 0xe4, struct mv8688sta_ctl_bss)
#define DCMD_MV8688STA_JOIN       __DIOTF(_DCMD_NET, 0xe5, struct mv8688sta_ctl_join)
#define DCMD_MV8688STA_SET_KEY    __DIOTF(_DCMD_NET, 0xe6, struct mv8688sta_ctl_key)

#endif
