/*
 * 88W8688 command and event layer over the SDIO card layer, for the station
 * firmware (linux-firmware sd8688.bin). Framing and the card-to-host read
 * follow the factory uap_dnld_cmd, mv8688uap_sdio_htoc, mv8688uap_sdio_ctoh
 * and mv8688uap_get_int_status in devnp-mv8688uap.so (SHA-256
 * 311b947c...53d269f7); the ready condition, interrupt mask and command
 * layouts, the transmit and receive descriptors follow Linux libertas
 * if_sdio.c, host.h, tx.c and rx.c (v6.6), the driver this firmware was
 * written for. See docs/wifi-client/wifi-client-mode.md;
 * no factory or libertas code is copied.
 */
#ifndef MV8688CMD_H
#define MV8688CMD_H

#include <stdint.h>
#include "sdio.h"

#define MV_REG_HOST_INT_MASK    0x04
#define MV_REG_HOST_INT_STATUS  0x05
#define MV_REG_RX_LEN           0x42

#define MV_INT_UPLD         0x01    /* the card has a packet for the host */
#define MV_INT_DNLD         0x02    /* the card took the last host packet */
#define MV_INT_MASK_ALL     0x0f    /* libertas: UPLD, DNLD, UFLOW, OFLOW */
#define MV_STATUS_IO_READY  0x08

#define MV_TYPE_DATA        0
#define MV_TYPE_CMD         1
#define MV_TYPE_EVENT       3

#define MV_CMD_GET_HW_SPEC      0x0003
#define MV_CMD_SCAN             0x0006
#define MV_CMD_AUTHENTICATE     0x0011  /* stores the auth type; sends nothing */
#define MV_CMD_SET_WEP          0x0013
#define MV_CMD_RADIO_CONTROL    0x001c
#define MV_CMD_DEAUTHENTICATE   0x0024
#define MV_CMD_MAC_CONTROL      0x0028
#define MV_CMD_ENABLE_RSN       0x002f
#define MV_CMD_MAC_ADDRESS      0x004d
#define MV_CMD_ASSOCIATE        0x0050
#define MV_CMD_KEY_MATERIAL     0x005e
#define MV_CMD_FUNC_INIT        0x00a9
#define MV_CMD_FUNC_SHUTDOWN    0x00aa
#define MV_CMD_RESPONSE         0x8000
/* The firmware answers 802_11_ASSOCIATE with this code, not 0x8050
 * (libertas CMD_RET_802_11_ASSOCIATE; seen on the unit in 5.0). */
#define MV_CMD_RET_ASSOCIATE    0x8012

#define MV_MAC_RX_ON        0x0001
#define MV_MAC_TX_ON        0x0002

#define MV_ACT_GET          0x0000
#define MV_ACT_SET          0x0001
#define MV_ACT_REMOVE       0x0004
#define MV_MAC_ADDRESS_SIZE 16      /* struct cmd_ds_802_11_mac_address */

#define MV_BSS_TYPE_ANY     0x03
#define MV_TLV_RATES        0x0001
#define MV_TLV_CHANLIST     0x0101
#define MV_TLV_TSF          0x0113
#define MV_SCAN_PASSIVE     0x01    /* chanscanmode bit 0 */
#define MV_SCAN_CHANNELS    4       /* libertas LBS_SCAN_BEFORE_NAP */
#define MV_SCAN_BODY_MAX    100     /* bsstype, BSSID, SSID, channel list, rates */
#define MV_EID_SSID         0
#define MV_EID_RATES        1
#define MV_EID_DS_PARAMS    3
#define MV_EID_RSN          48
#define MV_EID_EXT_RATES    50
#define MV_EID_VENDOR       221
#define MV_RATES_MAX        14      /* libertas MAX_RATES */
/* Elements kept per BSS for the supplicant (cfg80211_inform_bss gets them
 * all in libertas); a longer set is cut at an element boundary. */
#define MV_BSS_IE_MAX       512

/* Association, as libertas lbs_cfg_connect does it. */
#define MV_TLV_SSID         0x0000
#define MV_TLV_PHY_DS       0x0003
#define MV_TLV_CF           0x0004
#define MV_TLV_KEY_MATERIAL 0x0100
#define MV_TLV_AUTH_TYPE    0x011f
#define MV_CAP_ESS          0x0001
#define MV_CAP_PRIVACY      0x0010
#define MV_AUTH_OPEN        0
#define MV_LISTEN_INTERVAL  10      /* libertas MRVDRV_DEFAULT_LISTEN_INTERVAL */
#define MV_KEY_INFO_MCAST   0x01
#define MV_KEY_INFO_UNICAST 0x02
#define MV_KEY_INFO_ENABLED 0x04
#define MV_KEY_TYPE_TKIP    1       /* libertas KEY_TYPE_ID_* */
#define MV_KEY_TYPE_AES     2
#define MV_KEY_MAX          32      /* TKIP: key and both MIC keys */
/* The RSN or WPA element of an association, header included; libertas
 * lbs_add_wpa_tlv takes at most 128 data bytes. */
#define MV_WPA_IE_MAX       130
#define MV_REASON_LEAVING   3       /* 802.11 deauthentication reason */
#define MV_ASSOC_BODY_MAX   228     /* fixed fields, five TLVs and a WPA TLV */
#define MV_ASSOC_RESP_SIZE  14      /* header, capability, status, AID */
#define MV_AUTH_BODY_SIZE   17      /* struct cmd_ds_802_11_authenticate */
#define MV_DEAUTH_BODY_SIZE 8
#define MV_WEP_BODY_SIZE    72      /* struct cmd_ds_802_11_set_wep */
#define MV_KEY_BODY_SIZE    44      /* action and the key parameter TLV */

/* Events: the low byte of the cause (libertas if_sdio_handle_event). */
#define MV_EVENT_LINK_LOST_SCAN 0x02
#define MV_EVENT_LINK_LOST      0x03
#define MV_EVENT_DEAUTHENTICATED 0x08
#define MV_EVENT_DISASSOCIATED  0x09
#define MV_EVENT_MIC_ERR_MCAST  0x0d    /* libertas MACREG_INT_CODE_MIC_ERR_* */
#define MV_EVENT_MIC_ERR_UCAST  0x0e

#define MV_RXPD_SIZE        20      /* struct rxpd */
#define MV_DATA_KEPT        8       /* data frames recorded */
#define MV_TXPD_SIZE        24      /* struct txpd */
#define MV_ETH_MIN          14      /* an Ethernet II header */
#define MV_ETH_MAX          1514    /* libertas: ETH_FRAME_LEN */
/* Host-to-card data buffer: whole blocks, up to the module's 2,048-byte
 * FIFO. The largest frame, with its SDIO header and txpd, is 7 blocks. */
#define MV_TX_MAX           2048
#define MV_TX_DONE_MS       100     /* DNLD after a data write */

#define MV_CMD_HEADER       8       /* command, size, seqnum, result */
#define MV_HW_SPEC_SIZE     46      /* struct cmd_ds_get_hw_spec */
#define MV_CMD_MAX          256     /* one block, SDIO header included */
/* Largest frame accepted: the factory htoc sends (length + 259) / 256
 * blocks, which is one block only up to 252 bytes. */
#define MV_CMD_FRAME_MAX    252
#define MV_RX_MAX           4096    /* largest card-to-host packet accepted */
#define MV_CMD_TIMEOUT_MS   5000    /* factory: 5 s for a command response */
#define MV_EVENTS           8       /* event causes recorded */

#define MV_ECMDREADY        (-30)   /* IO_RDY never set before a write */
#define MV_ECMDWRITE        (-31)   /* the command write failed three times */
#define MV_ECMDTIMEOUT      (-32)   /* no response in time */
#define MV_ECMDRX           (-33)   /* card-to-host length or header invalid */
#define MV_ECMDRESP         (-34)   /* response for another command, or malformed */
#define MV_ECMDRESULT       (-35)   /* the firmware reported a non-zero result */
#define MV_ETXDONE          (-36)   /* no DNLD after a data write */

/* A received data frame, from its rxpd and the headers it points to. */
struct mv8688_rx {
    uint16_t status, pkt_len;   /* pkt_len as reported; libertas ignores it */
    uint8_t snr, nf, rate, priority;
    uint32_t pkt_ptr;           /* offset of the 802.3 header from the rxpd */
    unsigned frame_len;         /* packet bytes from pkt_ptr on */
    uint8_t dst[6], src[6];
    uint16_t ethertype;         /* from the LLC/SNAP header; 0 without one */
};

struct mv8688_cmd {
    struct sdio_card *card;
    uint32_t ioport;
    uint8_t rx_unit;            /* function-1 0x43, read after the download */
    uint16_t seq;               /* last sequence number sent */
    const char *step;           /* step that failed */
    int io_rc;                  /* card-layer rc behind the failure, if any */
    uint8_t out[MV_CMD_MAX];
    uint8_t in[MV_RX_MAX];
    /* Response to the last command: its 8-byte header and the body. */
    uint16_t resp_cmd, resp_size, resp_seq, resp_result;
    unsigned resp_len;          /* bytes after the SDIO header */
    const uint8_t *resp;        /* points into in[] */
    /* Diagnostics for the last command. */
    unsigned ready_reads;       /* status reads until IO_RDY */
    uint8_t ready_status;       /* status byte that satisfied it */
    unsigned write_tries;
    unsigned aborts, abort_failures;
    unsigned polls;             /* interrupt-status reads until the response */
    unsigned rx_len_raw;        /* function-1 0x42 for the response */
    unsigned rx_size;           /* rx_len_raw << rx_unit */
    unsigned rx_chunk;          /* length in the packet's own header */
    /* Since mv8688_cmd_start. */
    uint8_t int_seen;           /* OR of every interrupt status read */
    unsigned dnld_acks;         /* statuses with DNLD set */
    unsigned packets, events, data_packets, stray_responses;
    unsigned event_n;
    uint32_t event[MV_EVENTS];
    unsigned disconnects;       /* link-lost, deauthenticated, disassociated */
    uint32_t disconnect_cause;  /* the last of them; event[] keeps the first */
    unsigned mic_ucast, mic_mcast;  /* TKIP MIC failures the firmware reported */
    /* Data frames since mv8688_cmd_start; data_packets counts them all. */
    unsigned data_bytes;        /* 802.3 bytes in the frames that decoded */
    unsigned data_bad;          /* frames whose descriptor did not fit */
    unsigned data_n;
    struct mv8688_rx data[MV_DATA_KEPT];
    /* Called for each data frame that decoded, with the frame as Ethernet
     * II when it carried LLC/SNAP (libertas rx.c), else as the 802.3 frame
     * the firmware passed up. It may set wait_done to end mv8688_wait. */
    void (*on_frame)(void *ctx, const struct mv8688_rx *rx, const uint8_t *frame,
                     unsigned len);
    void *frame_ctx;
    int wait_done;
    /* Data frames sent since mv8688_cmd_start, and the last one's write. */
    unsigned tx_frames, tx_bytes;
    unsigned tx_blocks;         /* blocks written */
    unsigned tx_polls;          /* status reads until its DNLD */
    uint8_t tx[MV_TX_MAX];
};

struct mv8688_bss {
    uint8_t bssid[6];
    uint8_t rssi;               /* as reported; libertas: 3 - rssi dBm */
    uint16_t interval, capability;
    int channel;                /* DS parameter set, or -1 */
    int ssid_len;               /* SSID element length, or -1 */
    uint8_t ssid[32];           /* the first 32 bytes of it */
    unsigned rates_n;           /* supported, then extended, rates kept */
    unsigned rates_supp;        /* how many of them are supported rates */
    uint8_t rates[MV_RATES_MAX];
    int rsn;                    /* an RSN or WPA element is present */
    unsigned ie_len;            /* elements kept, from the SSID element on */
    int ie_cut;                 /* more elements came than ie[] holds */
    uint8_t ie[MV_BSS_IE_MAX];
};

struct mv8688_hw_spec {
    uint16_t hwifversion, version, nr_txpd, nr_mcast_adr;
    uint8_t mac[6];
    uint16_t regioncode, nr_antenna;
    uint32_t fwrelease, wcb_base, rxpd_rdptr, rxpd_wrptr, fwcapinfo;
};

/* Unmasks the card's host interrupts (libertas 0x0f). The status register
 * is polled. The attach left CCCR IENx at master | function 1, as the
 * factory's does, but the FPGA host's card interrupt enable stays off. */
int mv8688_cmd_start(struct mv8688_cmd *c);
/* Masks them again; best effort before the power-down. */
int mv8688_cmd_stop(struct mv8688_cmd *c);
/* Sends one command (body after the 8-byte header) and waits up to
 * timeout_ms after the write for its response, recording any event or data
 * packet that arrives first. */
int mv8688_command(struct mv8688_cmd *c, uint16_t command, const void *body,
                   unsigned body_len, unsigned timeout_ms);
/* Sends nothing: reads every packet the card offers for timeout_ms, or
 * until on_frame sets wait_done, recording events and data frames as
 * mv8688_command does. A command response here counts as stray. Returns 0,
 * or the transport failure. */
int mv8688_wait(struct mv8688_cmd *c, unsigned timeout_ms);
/* Sends one Ethernet II frame (MV_ETH_MIN..MV_ETH_MAX bytes) behind a
 * txpd, as libertas lbs_hard_start_xmit and if_sdio_host_to_card do, then
 * waits up to timeout_ms for the card's DNLD before another write, reading
 * any packet it announces meanwhile. HBSDC_EINVAL for a bad length;
 * MV_ETXDONE when no DNLD came. */
int mv8688_send_data(struct mv8688_cmd *c, const uint8_t *frame, unsigned len,
                     unsigned timeout_ms);
/* Decodes the receive descriptor of a data packet (after the SDIO header)
 * and the 802.3 and LLC/SNAP headers it points to. MV_ECMDRX when the
 * frame it describes does not fit the packet. */
int mv8688_parse_rxpd(const uint8_t *p, unsigned len, struct mv8688_rx *rx);
/* Decodes a GET_HW_SPEC response; MV_ECMDRESP when it is too short. */
int mv8688_parse_hw_spec(const struct mv8688_cmd *c, struct mv8688_hw_spec *spec);

/* Parses "xx:xx:xx:xx:xx:xx". Returns -1 for any other text and -2 for an
 * address no station may use: all zero, a group address, or the station
 * firmware's own placeholder aa:bb:cc:dd:ee:ff. */
int mv8688_parse_mac(const char *text, uint8_t mac[6]);
/* Body of 802_11_MAC_ADDRESS: action, then the address (8 bytes). */
void mv8688_mac_body(uint8_t body[8], unsigned action, const uint8_t mac[6]);
/* Address in an 802_11_MAC_ADDRESS response; MV_ECMDRESP when too short. */
int mv8688_resp_mac(const struct mv8688_cmd *c, uint8_t mac[6]);
/* Body of an 802_11_SCAN of 1..MV_SCAN_CHANNELS channels, as libertas'
 * lbs_scan_worker builds one: any BSS type, any BSSID, then, with an SSID
 * (1-32 bytes), an SSID TLV and active channels, or without one passive
 * channels; then the channel list TLV and its rates TLV. Returns the
 * length, or 0 for a bad channel list or SSID. */
unsigned mv8688_scan_body(uint8_t *body, const uint8_t *channels, unsigned n,
                          unsigned dwell_ms, const uint8_t *ssid, unsigned ssid_len);
/* Decodes a scan response as libertas' lbs_ret_scan does, keeping up to
 * max descriptors; *sets is the count the firmware reported.
 * MV_ECMDRESP when the descriptors, their elements or the TSF TLV do not
 * fit the response. */
int mv8688_parse_scan(const struct mv8688_cmd *c, struct mv8688_bss *bss, unsigned max,
                      unsigned *sets);

/* Body of a two-word command: action, then a value (ENABLE_RSN,
 * RADIO_CONTROL). */
void mv8688_action_body(uint8_t body[4], unsigned action, unsigned value);
/* SET_WEP removing key 0, as libertas lbs_remove_wep_keys. */
void mv8688_wep_remove_body(uint8_t body[MV_WEP_BODY_SIZE]);
/* KEY_MATERIAL setting an empty key: libertas clears the unicast and the
 * group key this way before an open or WPA association. */
void mv8688_key_clear_body(uint8_t body[MV_KEY_BODY_SIZE], unsigned key_info);
/* KEY_MATERIAL installing a pairwise (unicast) or group key, as libertas
 * lbs_cfg_add_key: AES takes 16 bytes, TKIP 32. Returns 0, or -1 for
 * another type or length. */
int mv8688_key_body(uint8_t body[MV_KEY_BODY_SIZE], unsigned key_type, int pairwise,
                    const uint8_t *key, unsigned key_len);
/* Checks the RSN or WPA element a supplicant asks to associate with: one
 * whole element (RSN, or vendor 00:50:f2 type 1) of at most
 * MV_WPA_IE_MAX bytes. Returns 0 or -1. */
int mv8688_wpa_ie_check(const uint8_t *ie, unsigned len);
/* 802_11_AUTHENTICATE for open-system authentication with this BSS. */
void mv8688_auth_body(uint8_t body[MV_AUTH_BODY_SIZE], const uint8_t bssid[6]);
/* 802_11_ASSOCIATE for an open BSS from a scan, as libertas lbs_associate
 * builds it: BSSID, capability, listen interval, then SSID, DS, empty CF,
 * common rates and auth-type TLVs, and no WPA TLV. Returns the length, or 0
 * when the BSS has no usable SSID or channel. */
unsigned mv8688_assoc_body(uint8_t *body, const struct mv8688_bss *b);
/* The same, followed by the supplicant's RSN or WPA element as a TLV of
 * the element's own type (lbs_add_wpa_tlv). Without an element it is
 * mv8688_assoc_body; 0 also for an element mv8688_wpa_ie_check refuses. */
unsigned mv8688_assoc_wpa_body(uint8_t *body, const struct mv8688_bss *b,
                               const uint8_t *wpa_ie, unsigned wpa_ie_len);
/* Fields of an ASSOCIATE response; MV_ECMDRESP when it is too short. */
int mv8688_parse_assoc(const struct mv8688_cmd *c, uint16_t *capability, uint16_t *status,
                       uint16_t *aid);
/* 802_11_DEAUTHENTICATE: the BSSID and an 802.11 reason code. */
void mv8688_deauth_body(uint8_t body[MV_DEAUTH_BODY_SIZE], const uint8_t bssid[6],
                        unsigned reason);

#endif
