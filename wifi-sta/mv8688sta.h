/*
 * Station control for the 88W8688 io-net driver (phase 5.2): what follows
 * the firmware download, joining an open network, watching the link and
 * sending frames, over the command layer. The steps are the ones the phase
 * 4.3-5.1 probe ran on the unit, in its order, from libertas cmd.c and
 * cfg.c (v6.6): FUNC_INIT, GET_HW_SPEC, MAC_ADDRESS set and read back,
 * MAC_CONTROL; an active SSID scan of channels 1-11, then lbs_cfg_connect
 * for an open network; DEAUTHENTICATE to leave. No libertas code is copied.
 *
 * Phase 6 adds what a host supplicant needs, as libertas offers it to
 * cfg80211: a scan whose results keep each BSS's elements, a join of one
 * scanned BSS with the supplicant's RSN or WPA element (lbs_cfg_connect
 * for a TKIP or CCMP group cipher), and key installation
 * (lbs_cfg_add_key). The 4-way handshake is the supplicant's; its EAPOL
 * frames are ordinary data frames here.
 *
 * Single-threaded: one owner calls everything. Platform-free, so the host
 * tests drive it against the fake module and firmware.
 */
#ifndef MV8688STA_H
#define MV8688STA_H

#include <stdint.h>
#include "mv8688cmd.h"

#define MV_STA_SCAN_TRIES       3       /* SSID scans before an absent SSID fails */
#define MV_STA_SCAN_ACTIVE_MS   40      /* libertas LBS_DWELL_ACTIVE */
#define MV_STA_SCAN_PASSIVE_MS  200     /* as 4.4 ran it; libertas uses 100 */
#define MV_STA_BSS_MAX          32      /* descriptors kept per scan command */
#define MV_STA_CHANNELS         11

#define MV_STA_ENOTFOUND    (-40)   /* the SSID was not heard */
#define MV_STA_ENOTOPEN     (-41)   /* not an open infrastructure BSS on 1-11 */
#define MV_STA_EASSOC       (-42)   /* ASSOCIATE answered with a non-zero status */
#define MV_STA_ESTATE       (-43)   /* not in a state for this request */
#define MV_STA_EMAC         (-44)   /* the MAC read back differs */
#define MV_STA_EINVAL       (-45)   /* bad SSID length, address, element or key */
#define MV_STA_ENOTSCANNED  (-46)   /* the BSSID is not in the last scan's results */

enum mv8688_sta_state {
    MV_STA_DOWN,                /* not started, or stopped */
    MV_STA_IDLE,                /* firmware set up, not associated */
    MV_STA_ASSOCIATED
};

struct mv8688_sta {
    struct mv8688_cmd cmd;      /* first, so the tests can mirror it */
    int state;                  /* enum mv8688_sta_state */
    int func_init;              /* FUNC_INIT was answered: FUNC_SHUTDOWN is owed */
    uint8_t mac[6];
    struct mv8688_hw_spec spec;
    /* The last connect: the BSS chosen and how the association went. */
    struct mv8688_bss bss;
    uint16_t assoc_status, aid, assoc_capability;
    unsigned scans, heard, found;
    unsigned link_mark;         /* cmd.disconnects when the link came up */
    uint32_t link_cause;        /* the event that ended the last link */
    unsigned connects, link_losses;
    unsigned scans_done;        /* mv8688_sta_scan calls that finished */
    unsigned key_sets;          /* KEY_MATERIAL installs and clears that took */
    int rsn;                    /* the last join carried an RSN or WPA element */
    /* The last failure: the step and its code. */
    const char *step;
    int rc;
    /* One finished line per call; may be NULL. */
    void (*log)(void *ctx, const char *line);
    void *log_ctx;
    struct mv8688_bss seen[MV_STA_BSS_MAX];     /* one SCAN command's descriptors */
    /* Every BSS the last scan heard, one entry per BSSID (the strongest
     * report wins), in the order first heard. */
    unsigned result_n;
    struct mv8688_bss result[MV_STA_BSS_MAX];
};

/* After the download: unmask the card's interrupts, FUNC_INIT, GET_HW_SPEC,
 * set the MAC and read it back (a refused read-back is tolerated, a
 * different address fails), MAC_CONTROL with RX and TX on. cmd.card,
 * cmd.ioport and cmd.rx_unit must be set, and the log callback if wanted;
 * the rest is cleared here. IDLE on success. */
int mv8688_sta_start(struct mv8688_sta *s, const uint8_t mac[6]);
/* From IDLE: up to MV_STA_SCAN_TRIES active scans for the SSID (1-32
 * bytes), then the open-network connect to the strongest BSS carrying it.
 * ASSOCIATED on success. A failed association is followed by a
 * DEAUTHENTICATE, as the firmware's state is unknown then. */
int mv8688_sta_connect(struct mv8688_sta *s, const uint8_t *ssid, unsigned ssid_len);
/* From IDLE: one scan of channels 1-11, active for the SSID when one is
 * given (1-32 bytes), passive otherwise. result[] holds what it heard,
 * whatever the outcome. Nothing is joined. */
int mv8688_sta_scan(struct mv8688_sta *s, const uint8_t *ssid, unsigned ssid_len);
/* From IDLE: joins the BSS with this BSSID and SSID from the last scan's
 * results. With an RSN or WPA element (mv8688_wpa_ie_check) the BSS must
 * advertise privacy and RSN or WPA, and RSN is enabled; without one it
 * must be open, as for mv8688_sta_connect. ASSOCIATED on success; a failed
 * association is followed by a DEAUTHENTICATE. */
int mv8688_sta_join(struct mv8688_sta *s, const uint8_t bssid[6], const uint8_t *ssid,
                    unsigned ssid_len, const uint8_t *wpa_ie, unsigned wpa_ie_len);
/* KEY_MATERIAL, IDLE or ASSOCIATED: installs a pairwise or group key
 * (MV_KEY_TYPE_AES with 16 bytes, MV_KEY_TYPE_TKIP with 32), or with
 * key_len 0 clears it as libertas does before a join. */
int mv8688_sta_set_key(struct mv8688_sta *s, unsigned key_type, int pairwise,
                       const uint8_t *key, unsigned key_len);
/* DEAUTHENTICATE when associated; IDLE afterwards whatever it returned. */
int mv8688_sta_disconnect(struct mv8688_sta *s);
/* Reads what the card offers for up to timeout_ms (mv8688_wait). An event
 * that ends the link moves ASSOCIATED to IDLE. */
int mv8688_sta_poll(struct mv8688_sta *s, unsigned timeout_ms);
/* One Ethernet II frame, ASSOCIATED only (MV_STA_ESTATE otherwise). */
int mv8688_sta_send(struct mv8688_sta *s, const uint8_t *frame, unsigned len);
/* Disconnect, FUNC_SHUTDOWN if it is owed, mask the card's interrupts:
 * every step is tried, and the first failure is returned. DOWN after. */
int mv8688_sta_stop(struct mv8688_sta *s);
const char *mv8688_sta_event_name(uint32_t cause);

#endif
