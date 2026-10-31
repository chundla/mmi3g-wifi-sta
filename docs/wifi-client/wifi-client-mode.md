# Wi-Fi client (station) mode

> **About this copy.** These notes were written day by day in the author's
> private MMI 3G research repository and copied here on 2026-10-30 with
> the code. Revisions such as `11f5da6` refer to that repository. Paths
> under `<private>/` are evidence the author keeps privately (logs,
> captures, photos); links to unpublished notes were turned into plain
> references; `work/` is this repository's ignored build directory. The
> OpenSSH notes now live in
> [qnx632-openssh](https://github.com/chundla/qnx632-openssh).

Offline analysis, 2026-10-23, for **9411, HN+R_US_AU_K0942_3, MU 1038**.
Repository revision `11f5da6`, with existing owner changes. Upstream
reference: dspl1236/MMI3G-Toolkit `e9ca346`,
[CUSTOM_DRIVER_INJECTION.md](https://github.com/dspl1236/MMI3G-Toolkit/blob/e9ca3469835e3e1c78dabfb5cbe5676d2b752500/research/CUSTOM_DRIVER_INJECTION.md)
and
[DATA_CONNECTIVITY_OPTIONS.md](https://github.com/dspl1236/MMI3G-Toolkit/blob/e9ca3469835e3e1c78dabfb5cbe5676d2b752500/research/DATA_CONNECTIVITY_OPTIONS.md).
[Phase 0 results](#phase-0-results) were added the same day by the same
offline method. [Phase 1 results](#phase-1-results) are a read-only live
capture made the same day; it ran diagnostics only. [Phase 2
results](#phase-2-results) are local hardware results from 2026-10-25 to
2026-10-27 that established the wired management path. [Detach and firmware
observability](#detach-and-firmware-observability) is offline work from
2026-10-27 that prepares phase 3, and [phase 3a
results](#phase-3a-results) are a read-only hardware observation of the HMI
Wi-Fi switch on 2026-10-28, and [phase 3b and 3c
results](#phase-3b-and-3c-results) are the detach and firmware probes run on
the unit the same night. [Phase 3d, 4.0 and 4.1
results](#phase-3d-40-and-41-results) are hardware results from the
following afternoon: a power-off start-up, the stub load and the host
bring-up probe. The [4.2 firmware download
probe](#firmware-download-probe-42-prepared-offline) was prepared offline
after them, and [its results](#phase-42-results) are from the unit the same
evening: the station image downloaded and reached its ready magic. The
[4.3](#phase-43-results), [4.4](#phase-44-results) and
[4.5](#phase-45-results) results followed that night: the firmware answers
commands, takes the access point's MAC, hears the owner's hotspot in a
passive scan, and moves data by PIO at the rates measured there. The
[phase 5 plan](#phase-5-plan) and its first increment, an open-network
association, are offline work from 2026-10-29, and the [5.0
results](#phase-50-results) are from the unit that night: the station
associated with an open bench network and held the link. The [phase 6
plan](#phase-6-plan), WPA2-PSK through a `wpa_supplicant` port, is offline
work from the same day. The [6.0](#phase-60-results) and
[6.1](#phase-61-results) results are from the unit that afternoon: BPF
works once its filter is sent in the form `io-pkt` reads, and the station
joined the bench network with WPA2-PSK and CCMP. The
[6.2](#phase-62-results) results are from that evening: rekeys and
reconnects, and broadcast loss traced to the car's Bluetooth audio. The
[6.3](#phase-63-results) result is the owner's phone hotspot. The
[phase 7 plan](#phase-7-plan) is offline work from the same evening: it
records the owner's integration decisions and the factory "LAN replaces
PPP" mode that routes the online services through a lease. The
[7.1 results](#phase-71-results) are from the unit that evening: that
mode works over `en5`, Google Earth uses it and stalls, and the Audi
connect services never try the network.

## Conclusion

The goal is for the head unit to join a phone hotspot as a Wi-Fi client. That
needs a new station driver for the Marvell 88W8688. Swapping firmware under
the factory access-point driver cannot work.

- The factory `devnp-mv8688uap.so` is a QNX port of Marvell's uAP (micro
  access point) driver. It issues uAP-only commands during start-up.
- Its data path frames packets in the uAP TxPD layout. The station firmware
  expects the libertas `txpd` layout.
- It contains its own driver for the Harman FPGA SDIO host controller. A
  station driver has to drive the same controller.

The rest of the stack is closer than upstream suggests. The DHCP client,
BPF, an 802.11-aware `io-pkt` and the networking service's client role and
DHCP-client hooks are already on the unit. The work splits into:

1. A station-mode driver for the 88W8688 behind the FPGA SDIO host.
2. A `wpa_supplicant` build with a backend for that driver.
3. A mode switch that keeps a management path and a rollback that does not
   depend on Wi-Fi.

The station firmware is available:
linux-firmware `mrvl/sd8688.bin` is the libertas SD8688 firmware.

## Evidence

### Driver identity

Saved root IFS, `ifs-root/lib/dll/devnp-mv8688uap.so`, 93,538 bytes,
SHA-256 `311b947c…53d269f7`. It is an SH-4 little-endian ELF shared object,
stripped, with section headers removed, and needs only `libc.so.2`. It is a
native `io-pkt` driver (`iopkt_drvr_entry`, `ether_ifattach`,
`if_link_state_change`, `bpf_callout`), not an io-net driver.

Embedded source paths identify its origin:
`C:/dev/core_networking/sys/dev_qnx/mv8688uap-pb/` `uap_cmd.c`, `uap_main.c`,
`uap_sdio.c`, `upevent.c`. Function names (`sbi_prog_helper`,
`sbi_prog_fw_w_helper`, `request_firmware`, `uap_init_fw`, `uap_start_bss`)
match Marvell's Linux uAP driver.

The same object contains an SDIO host-controller driver, `hbsdc_*`. It
reaches an FPGA SDIO module through `pci_attach_device` and
`mmap_device_memory`, plus a generic SDIO card layer (`sdio_attach_device`,
`sdio_read_cis`, `sdio_set_blksz`, CMD52/CMD53 I/O). QNX 6.3.2 has no
separate SDIO stack on this unit; a replacement driver must reimplement or
reuse this layer.

Decompilation was done with Ghidra 12.1.4 using
[`tools/ghidra/Mv8688UapTrace.java`](../../tools/ghidra/Mv8688UapTrace.java),
which checks the hash. The image base is `0x10000`, so text file offset equals
the address minus `0x10000` (data: minus `0x11000`). The script promotes
callbacks that are only stored as pointers (SDIO host operations, the autoconf
detach entry, io-pkt hooks) to functions before decompiling. Unnamed PLT
stubs were matched to imports through the ELF `JMPREL` table. Callee identities
below were checked against strings, constants and those tables.

| Item | Finding |
| --- | --- |
| Control ioctls | `0x801c697b` `SIOCSDRVSPEC` and `0xc01c697b` `SIOCGDRVSPEC` (`struct ifdrv`). Both copy in a raw firmware command and send it through the command path, then copy the response out. This is a generic host-command passthrough. |
| Response hooks | Response `0x80b1` (uAP BSS_START) sets link up. `0x80b2` (BSS_STOP) sets link down. `0x80b0` (SYS_CONFIGURE) with TLV `0x012b` updates the MAC address. |
| Other ioctls | `0xc018697b` returns driver config/stats blocks (sizes `0x2a0`/`0x4c0`). `0xc0286936` and `0xc0906935` return 0. Everything else goes to `ether_ioctl`. |
| uAP commands at start-up | Attach (probe, firmware load, DMA set-up) sends no firmware command. `uap_get_mac_address` (`0x00b0`, TLV `0x012b`) has no direct caller in the trace. Bringing the interface up (`if_init`) sends `uap_set_mac_address`, also `0x00b0`, when the interface address differs from the cached one; a failure makes `if_init` return -1. The error-recovery path `sdio_pb_restart` sends it too. |
| TX framing | `mv8688uap_pb_htoc` writes a 4-byte SDIO header and then zeroes a 20-byte TxPD. It sets byte 0 = 1 (BSS type uAP), bytes 2–3 = length and byte 4 = `0x14` (packet offset 20). The data header totals 24 bytes. |
| Firmware files | `uap_init_fw` builds `<fwpath>/FwImage/helper_sd.bin` and `<fwpath>/FwImage/sd8688_ap.bin`. The usage text gives `fwpath` a default of `/tmp`. The factory mount passes `fwpath=/lib/dll`, so it reads the IFS copies and never `/tmp/FwImage`. |
| Already-loaded firmware | Before downloading, `sbi_check_fw_status` reads function-1 registers `0x40`/`0x41` once. If they read `0xfedc`, the driver logs `UAP FW already running! Skip FW download` and skips both files. libertas uses the same register and value (`IF_SDIO_FW_STATUS`, `IF_SDIO_FIRMWARE_OK`). |

The libertas SD8688 station firmware uses a different 24-byte `struct txpd`:
`tx_status`, `tx_control`, then `tx_packet_location` at byte 8 and length at
byte 12. The firmware types match (both use the same SDIO header and the
byte-identical helper), but the TX layouts and command sets do not.

### Factory mount and runtime

From the factory `efs-system` image, selector 41, in the author's private
copy of the factory update package (image SHA-256
`7691e7cb…4e2520e3`). Files were extracted with upstream's scan-based
`extract_f3s_efs.py`, which flattens paths, and only what is cited here was
checked. This is factory content; it is not a capture of the unit's current
writable EFS.

- `nws.cfg` (`df7e6850…18c2d6f0`) has `NetworkingService_0_ap.mountdir =
  /lib/dll/devnp-mv8688uap.so`, `mounttype = io-pkt` and `mountparam =
  irq=13,dma=0x104,fwpath=/lib/dll,clock=25M,resetmask=0`. It also sets
  `uap0` to static 192.168.1.1/24, dhcpd range .100–.110, and
  `configpath = /etc/ppp/hostapd.conf`.
- `hostapd.conf` (`eeb040f7…086c52`) is parsed by the networking service;
  hostapd itself does not run. It sets `hw_mode=b`, `channel=5`,
  `country_code=DE`. The access point is 2.4 GHz. Marvell describes the
  88W8688 as an 802.11b/g part, but that is product information, not
  verified here.
- `NWSProcess` (`3f73aa10…3aeaf2`) loads `libnetworkingservice.so`
  (`5142af92…932c65a6`). Its only Wi-Fi backend is
  `wlan/uapmv8688/CUapMv8688Api.cpp`, created by `CWlanDriverFactory`. It
  checks role ranges `NWS::ROLE_AP` … `NWS::ROLE_ON`. It contains
  `dhcp.client -b %s -i %s`, `setDHCPClient`/`dhcpc_on`, `wpa_passphrase`,
  `rsn_pairwise` and an embedded libpcap.
- `ifs-root/usr/apps/MMI3GMisc` (`9fe36299…75fcc6e8bc`) has
  `SPHWLAN::ROLE_AP`, `ROLE_CLIENT` and `ROLE_ADHOC`. It also handles
  `NWS_IEEE80211_EV_wlanCrash` with up to 4 retries before `SUS_FAILED`.

A client role is named at both levels. No station implementation was found
behind it: the only factory driver and API class are uAP.

Live evidence (October 17 and 20 captures, read-only):

- One `io-pkt-v4-hc -S -ptcpip stacksize=8192,mclbytes=4096 -ppf-v4`
  instance carries `uap0`, `mam0` (MOST, 10.0.0.100) and `mhp0`.
  `dhcpd -cf /mnt/phonedb/dhcpd.uap0.conf … uap0` runs separately.
- Each captured boot (October 17, three on October 20) shows one Wi-Fi
  attach: `hbsdc_init()` with `MemBase[0]=c04a0000`, `MemBase[1]=c0000000`,
  `Reset bit offset=8 mask=00000000`, `Auto-Select interrupt=56`,
  `detected FPGA clock=99000000`, `SDIO fast clock mode *spd=25000000
  HBSDC_CTRL=00007003`, then `mv8688uap_pb_init()/568: selected DMA=0x104`.
  (An earlier revision of this note said `hbsdc_init` ran twice per boot. That
  came from grepping several logs together and was wrong.)
- `sdc_init: FPGA Mod:53 Date:08445A Ver:2.1.6` belongs to the two
  `devb-sdc-hbfpga` instances for the SD-card slots (`-A0x420000 -I52` and
  `-A0x440000 -I54`), not to Wi-Fi. The slots use the same FPGA module type
  (ID `0x53`) as the Wi-Fi host at offset `0x4a0000`.
- `/dev/bpf` and `/dev/bpf0` exist.
- `pf` blocks unsolicited inbound traffic on `uap0` and passes `en5` in full.
  (Read from the saved factory `pf.conf`.)

Platform:

- `io-pkt-v4-hc` contains net80211 (`ieee80211_ifattach`,
  `ieee80211_crypto_*`), and `ifconfig` has `SIOCS80211*`.
- `/usr/sbin/dhcp.client` (`270a970b…f1abee90`) supports `-i`, `-u`, `-T`,
  `-I`, `-t`, `-n`, `-m` and `-a`. It runs `/etc/dhcp/dhcp-up` and manages
  `/etc/resolv.conf` and the default route.
- `devnp-shim.so` is present, and `devn-asix.so` is an io-net driver that it
  hosts.

Local SDK (private, not redistributable): QNX 6.3.0 headers with SH
gcc 3.3.5, the same toolchain as the [OpenSSH build](https://github.com/chundla/qnx632-openssh/blob/main/docs/build.md).
It includes the io-net DDK (`sys/io-net.h`, `drvr/nicsupport.h`,
`drvr/mdi.h`) but no native `io-pkt` driver headers.

### Firmware

| File | Bytes | SHA-256 | Source |
| --- | ---: | --- | --- |
| `ifs-root/lib/dll/FwImage/sd8688_ap.bin` | 307,396 | `43689e02…fdc35aca6e` | Factory uAP firmware |
| `ifs-root/lib/dll/FwImage/helper_sd.bin` | 2,616 | `1c7ae3d4…1204d6f6` | Factory helper |
| linux-firmware `mrvl/sd8688_helper.bin` | 2,616 | `1c7ae3d42277bf26925d661c44eea33ed19dd60776ad4ea9e1fd72111204d6f6` | Byte-identical to the factory helper |
| linux-firmware `mrvl/sd8688.bin` | 259,172 | `f563f36bfd08192264945c4181c907607bb5234afbff377ca0d18d8db99e216e` | libertas station firmware |

The linux-firmware files are from `HEAD 412eb786`, fetched 2026-10-23.
`libertas/sd8688*.bin` are links to the `mrvl/` files. `WHENCE` lists them
as "Redistributable. See LICENCE.Marvell". The licence text was not
retrieved in this pass. Keep firmware out of tracked files; a card
generator should fetch it and check the pinned hash.

The helper match means the factory helper-download protocol also loads the
station image. The station firmware's command set and its support for
embedded or host-side WPA have not been checked against this file.

## Phase 0 results

Offline, 2026-10-23. Same driver hash and method as above. Nothing was
executed. Register names come from the driver's own bit-name tables (data from
`0x25cd0`, printed by `_hbsdc_test`) and its usage text. Meanings
marked *inferred* follow from how the code uses a register. None of this
has been observed on hardware.

### FPGA resources

| Resource | Location on this unit | Evidence |
| --- | --- | --- |
| FPGA PCI device | Xilinx `10ee:9411` | `hbsdc_init` default; `devb-sdc-hbfpga -V10ee -D9411` |
| System registers | BAR0, `c0000000`, 2 KiB mapped | `MemBase[1]` log |
| Wi-Fi SDIO module, ID `0x53` | BAR0 + `0x4a0000`, 64 KiB mapped | `MemBase[0]` log; usage default `0xc04A0000` |
| PicoBlaze packet engine, ID `0x49` | BAR0 + `0x4c0000`, 4 KiB mapped | `mv8688uap_pb_init`; usage default `pbmem` |
| Interrupt | 56, auto-selected from module register `0x3c` index `0xf` | log. The mount also passes `irq=13`; phase 1 found vector 56 attached and nothing at 13. |
| PicoBlaze DMA channel | `0x104` | log; `dma=` option |

SDIO module registers (32-bit, offsets from the module base):

| Offset | Name | Use |
| --- | --- | --- |
| `0x00` | data FIFO | PIO data for CMD53, one word per access |
| `0x04` | command FIFO | command index \| `0x40` and argument, written as two words |
| `0x08` | response FIFO | read after `CMD_RDY` |
| `0x0c` | `CMDTY` | response type and data-transfer flags |
| `0x10` | `HBSDC_CTRL` | `ENABLE` 0x1, `MODE` 0x2 (4-bit), `READ` 0x4, `DATA_BUF_CLR` 0x8, `NEXT_CMD` 0x10, `NEXT_RESP` 0x20, `CR_BUF_CLR` 0x40, `DEV_RESET` 0x80, `START` 0x100, `CANCEL` 0x200, `HS_MODE` 0x800, `FAST_CLK` 0x1000, `CLK_EN` 0x2000, `SDIO_EN` 0x4000 |
| `0x14` | `HBSDC_STAT` | `BUSY` 0x1 … `DT_RUN` 0x8000 |
| `0x18` | `HBSDC_IEN` | `CMD_RDY` 0x1, `ERROR` 0x2, `SDIO` 0x100, `TIMEOUT` 0x800, `CRC` 0x1000, `DT_RDY` 0x8000 and FIFO levels |
| `0x1c` | `HBSDC_FLAG` | write 1 to clear; `CMD_RDY` 0x1, CRC and timeout errors, `SDIO_INT` 0x100, `DT_RDY` 0x8000 |
| `0x20` | clock divider (byte) | set by `_hbsdc_bus_speed` |
| `0x28` | card power and status (*inferred*) | bit 7: change flag, write 1 to clear. Bit 13 must be clear for detect and power-up. Power-up sets bits 0–1; power-down writes 0. |
| `0x2c`, `0x30` | block count, block size | CMD53 set-up |
| `0x3c` | index/data byte | index 0: module ID; 1: major version; 3: change number; 8–9: clock in 10 kHz units; `0xf`: interrupt |
| `0x5c` | capabilities | bit 1 required, bit 0 selects a 4-bit bus, bits 8–9 give FIFO size (256 << n) |

The logged `HBSDC_CTRL=00007003` decodes as `SDIO_EN | CLK_EN | FAST_CLK |
MODE | ENABLE`: 4-bit bus, 25 MHz.

### Power, reset and attach

Every attach and every `sdio_reset` runs the same steps:

1. `hbsdc_init` maps both windows. It checks module ID `0x53` and the
   version (major ≥ 3, or change number ≥ 5), reads the clock, installs the
   host operations, powers down and clears `IEN`.
2. `sdio_attach_device`: detect (register `0x28`), power-down, 10 ms,
   power-up, 10 ms. Then CMD5 to read the OCR (up to 100 tries), CMD5 with
   that OCR, CMD3 for the RCA and CMD7 to select the card. It sets up the CCCR
   (enable all, interrupt enable, bus width), reads the CIS and requires
   vendor/device `02df:9103`.
3. Power-down (`_hbsdc_powerdown`): if `resetmask` is non-zero, set the mask
   bits in system register `resetoffset`, or clear them with
   `resetnegated`. Then write module register `0x28` = 0.
4. Power-up (`_hbsdc_powerup`) runs only if register `0x28` bit 13 is clear.
   It releases the reset bits when the mask is non-zero, then pulses
   `CTRL=DEV_RESET` for 1 ms. It sets `ENABLE`, `SDIO_EN`, both buffer
   clears and `CLK_EN`, sets register `0x28` bits 0–1 and clears `IEN`.
5. `mv8688uap_probe`: 25 MHz, function 1 with 256-byte blocks, I/O port from
   function-1 registers 0–2, `uap_init_fw`, then the card's host-interrupt
   mask (function-1 register 4) = 1.

What this means for rollback:

- With the factory `resetmask=0`, the system reset line is never touched.
  Only module register `0x28` and `DEV_RESET` toggle. Whether either removes
  power from the 88W8688 is not established. The firmware-status check
  suggests the authors expected firmware to survive a re-attach.
- The usage text says "Default values are specified as MMI3g+/NTG5":
  `resetoffset` `0x8`/`0x0`, `resetmask` `0x4`/`0x0`, and `resetnegated` 0
  (inverted logic is "0=reset, 1=run"). On MMI 3G+ the built-in default
  would drive bit 2 of system register 8. The factory `nws.cfg` overrides
  this with `resetmask=0`; the reason is unknown. That bit is named
  `WlanEnable`, and Bluetooth is on a separate chip; see
  [Radio enable line and Bluetooth](#radio-enable-line-and-bluetooth).

### Radio enable line and Bluetooth

Offline, 2026-10-23, from the saved IFS (October 20 backup) and the factory
`efs-system` image, selector 41. Not observed on hardware.

- `/etc/sysregs-mmi3g-9411.cfg` (`07dad253…6b300b5d9`) is the
  `dev-sysregs` table for "MMI3G D1 9411". `dev-sysregs` is started by
  `mmi3g-srv-starter.cfg` with `-r -A 0x00001000,2k -c
  /etc/sysregs-mmi3g-9411.cfg`. The table names system register `0x08`
  bit 2 `WlanEnable` (read-modify-write, default 0, initialised at start-up).
  That is the register and bit the driver's MMI 3G+ default drives
  (`resetoffset=8`, `resetmask=4`). The table and the driver are assumed to
  address the same register window; this was not checked against the
  `dev-sysregs` code.
- Polarity: the driver sets the bit on power-down and clears it on power-up
  when `resetnegated` is 0. `dev-sysregs` initialises it to 0 and the access
  point runs, so on this unit a set bit appears to hold the radio off,
  whatever the name suggests. *Inferred.*
- No factory file other than this table references the `WlanEnable` sysreg
  device. The `WlanEnabled` strings in `MMI3GMisc`, `MMI3GApplication` and
  `lsd.jxe` are the HMI's Wi-Fi on/off attribute.
- Bluetooth is a separate chip. Factory `pss.cfg` (`916e2886…c289207c`)
  sets `Bluetooth.UART_DEV = /dev/serfpga1` and `Bluetooth.UART_RESET_DEV =
  /dev/sysregs/BtReset`. `BtReset` is system register `0x00` bit 2.
  `libpal_bt.so` (`b5185c04…bd881c42083`) uses CSR BlueCore
  `PSKEY_*` settings and `CSR_ROM`. Toggling `WlanEnable` therefore should
  not reset Bluetooth. Phase 1 checks that `PSSBSSProcess` holds
  `/dev/serfpga1` open on the running unit.
- The saved October 20 boot log has the `hbsdc_init` lines and the driver's
  `MICRO_AP_EV_*` events (slog codes 1007, 10 and 14), but none of its
  firmware messages (`UAP FW already running! Skip FW download`,
  `Downloading helper image`, `UAP FW is active`). Those are either gated by
  `drvdbg` or written to another stream. Phase 3 needs some other way to
  tell whether firmware was downloaded unless phase 1 finds where they go.

### Detach

The autoconf entry `mv8688uap_ca` (data `0x25f80`) gives device size
`0x8c0`, attach `0x13c20` and detach `0x14e80`. Detach:

1. Marks the instance dying and calls `shutdownhook_disestablish`.
2. Stops the interface: disables interrupts, `InterruptDetach` and unmaps
   the PicoBlaze window.
3. Calls `ether_ifdetach` and `if_detach`.
4. Waits for pending firmware commands (up to 64 quarter-second sleeps) and
   stack callbacks (up to 10), then `InterruptDetach` and `pci_detach`.
5. `sdio_stop` calls the host shutdown: `IEN=0`, `CTRL=DEV_RESET`, 1 ms,
   power-down, unmap and `pci_detach_device`.

The io-pkt shutdown hook runs the same cleanup. No firmware command is sent
on detach. libertas sends `CMD_FUNC_SHUTDOWN` (`0x00aa`) when its SD8688
module is removed; this driver does not. The radio firmware keeps running
unless power-down removes power. `ifconfig uap0 destroy` reaches this routine
on this build; see [Detach and firmware
observability](#detach-and-firmware-observability). It has not been tried
here, and `NWSProcess`'s reaction is unknown.

### Firmware download and command framing

The SDIO-level protocol matches libertas `if_sdio.c` for SD8688 (Linux
v6.6, `if_sdio.c` SHA-256 `80f4183e…e09f9e22`):

| Step | Factory driver | libertas |
| --- | --- | --- |
| Firmware status | function-1 `0x40`/`0x41` = `0xfedc` | `IF_SDIO_FW_STATUS`, `IF_SDIO_FIRMWARE_OK` |
| I/O port | function-1 `0x00`–`0x02` | `IF_SDIO_IOPORT` |
| Card ready | function-1 `0x20` bits `0x09` | `IF_SDIO_STATUS`, `IO_RDY \| DL_RDY` |
| Helper | 508-byte chunks after a 4-byte length, sent as 2 × 256-byte blocks; a zero block ends it | 60-byte chunks after a 4-byte length in 64-byte writes; a zero block ends it |
| Firmware | requested length from function-1 `0x10`/`0x11`; bit 0 means CRC error (up to 2 retries); a failed write is aborted by writing 4 to function-1 `0x03` | `IF_SDIO_RD_BASE`, same CRC rule |
| RX unit | function-1 `0x43` | `IF_SDIO_RX_UNIT` |
| Host interrupts | status `0x05`, mask `0x04` set to 1 (upload only) | `IF_SDIO_H_INT_STATUS`/`MASK`, mask `0x0f` |
| Command frame | 4-byte header {length + 4, type 1}, then the command | `MVMS_CMD` = 1, `struct cmd_header` |

CMD53 goes through the module FIFO by PIO. The host's DMA operations are
not installed; packets use the separate PicoBlaze engine. A station driver
can start with PIO only. PIO throughput at 25 MHz on a 4-bit bus has not been
measured.

The driver's `log()` messages, including `Skip FW download`, are enabled by
the initial debug mask (`drvdbg` = `0x40000007`). None appear in the
captured `sloginfo`, so where io-pkt sends them is not established.

### Station command map

The commands the station driver needs, from libertas `host.h` (v6.6,
SHA-256 `54db1d7c…2390573b`):

| Purpose | Commands |
| --- | --- |
| Start | `CMD_FUNC_INIT` `0x00a9` (SD8688), `GET_HW_SPEC` `0x0003` (firmware release, MAC, capabilities), `MAC_CONTROL` `0x0028` |
| Scan | `802_11_SCAN` `0x0006` |
| Join and leave | `802_11_ASSOCIATE` `0x0050` (SSID, rates, channel and RSN IE TLVs), `802_11_DEAUTHENTICATE` `0x0024` |
| WPA2 | `802_11_ENABLE_RSN` `0x002f`, `802_11_KEY_MATERIAL` `0x005e` for the PTK and GTK; EAPOL frames travel as data |
| Address and filters | `802_11_MAC_ADDRESS` `0x004d`, `MAC_MULTICAST_ADR` `0x0010`, `802_11_SNMP_MIB` `0x0016` |
| Status | `802_11_RSSI` `0x001f` |
| Teardown | `CMD_FUNC_SHUTDOWN` `0x00aa` |
| Events | link lost 2/3, link sensed 4, deauthenticated 8, disassociated 9, MIC error 13/14 |
| Data | `struct txpd` (24 bytes) and `struct rxpd`; SDIO type 0 for data, 3 for events |

The station firmware's actual responses (its `GET_HW_SPEC` release and
supported TLVs) can only be read on hardware, in phase 4. Both firmware
images contain the string `2.3.0.0`; that was not taken to be a firmware
release.

### Build check

[`wifi-sta/stub.c`](../../wifi-sta/stub.c) is a hardware-free io-net
driver. Its `io_net_dll_entry` init logs its options and returns `ENODEV`.
[`wifi-sta/build-stub.sh`](../../wifi-sta/build-stub.sh) builds it with the
SDK's gcc 3.3.5 (`-ml -m4 -fPIC -shared`). The output goes to the ignored
`work/build/`.

- Result: 7,027 bytes, SHA-256 `4a7527bd…55ada443`, the same on a rebuild.
  It needs `libc.so.2` and has 9 imports. All are exported by the unit's
  saved `proc/boot/libc.so.2` (`57060bfc…17f5cf99`):
  `openssh/verify_elf.py --shared` passes.
- ELF flags are `0x8` (sh3e). GNU as records the lowest SH ISA the
  instructions need, and sh3e is a subset of SH-4. The unit's own SH-4
  binaries carry `0x9`. The script rejects anything outside SH-1 to SH-4.

This proves linking only. The stub has not been loaded by `devnp-shim`.

## Phase 1 results

Live, read-only, 2026-10-23 14:52–14:59 UTC (host clock). Unit: 9411,
HN+R_US_AU_K0942_3, MU 1038 (`MainUnit-version.txt`), QNX 6.3.2
`HB_SH7785`. The unit clock read `00:09:14` at the start (epoch-based
uptime; no calendar time). Collector: the author's read-only capture tool
(`collect_live_vehicle_reads.py --stage wifi`, not published), on revision
`11f5da6` of the private research repository with uncommitted changes (collector
SHA-256 `5843b052…` for the main run, `65f15406…` for the retry). Private
output: `<private>/live-20261023-wifi/` (status `partial`) and
`live-20261023-wifi-retry/`. Nothing was written on the unit.

Access note: the Pi held both `192.168.1.109` and `192.168.1.110` on the
car's Wi-Fi and routed from `.110`, which the MMI's `mmi_openssh_pi` rule
correctly refuses. The first attempt failed on every read
(`live-20261023-wifi-failed-src110/`). The run used `MMI_JUMP_SOURCE=192.168.1.109`
(see [OpenSSH access](https://github.com/chundla/qnx632-openssh/blob/main/docs/persistent.md#access-and-recovery)).

| Question | Result |
| --- | --- |
| Driver loaded | `io-pkt-v4-hc` (pid 40981, same arguments as before) has `devnp-mlb.so` and `devnp-mv8688uap.so` loaded. `devnp-shim.so` is not loaded. `/dev/io-net` holds only `mhp0`; `uap0` is a native io-pkt interface. |
| Interrupt | io-pkt has vectors `0xb`, `0xc`, `0x22`, `0x38` and `0x200c` attached. `0x38` is 56, the auto-selected Wi-Fi interrupt. Nothing is attached at 13 (`0xd`), so `irq=13` from `nws.cfg` is not used. The other vectors were not attributed. |
| Driver `log()` output | io-pkt's fds 0–2 are `/dev/ser1` (the `devc-sersci` console); fds 3, 5 and 6 are `/dev/slog`. Messages missing from slog, such as the firmware messages, most likely go to the serial console. That was not read. |
| Radio lines | `/dev/sysregs/WlanEnable` and `/dev/sysregs/BtReset` exist and both read `0x00000000` with the access point and Bluetooth running. This supports, but does not prove, the polarity inferred above. |
| Bluetooth | `/dev/serfpga1` exists, and Bluetooth was active (AVRCP media metadata in slog). `pidin -p PSSBSSProcess fds` hung twice (the unit-side watchdog fired), so the open-file check was not obtained. |
| Binaries | `cksum` and size match the saved IFS for `devnp-mv8688uap.so`, `devnp-shim.so`, `devn-asix.so`, `FwImage/helper_sd.bin`, `FwImage/sd8688_ap.bin` and `dhcp.client`. All are links into `/mnt/ifs-root`. `/tmp/FwImage` does not exist. |
| Configuration | `/usr/data/nws.cfg` → `/mnt/efs-system/pss/nws/usr/bin/nws.cfg` (3,753 bytes) matches the factory copy cited above, apart from 3 trailing bytes in the factory extract. `/etc/ppp` → `/mnt/efs-system/pss/nws/usr/bin`. `hostapd.conf` (`2223404110`, 32,476 bytes) is byte-identical to the factory copy. `pf.conf` (2,757 bytes) matches the factory copy that the extractor named `pf_0x1196a4c.conf`. `dhcp-up` (6,292 bytes) appears in order in the factory extract, which has flash metadata spliced in at line 51. `dhcpd.uap0.conf` is generated (range .100–.110). `/etc/resolv.conf` and `/HBpersistence/DLinkReplacesPPP` do not exist. |
| Firewall | Loaded rules are the factory `uap0` set plus the `mmi_openssh_pi` rule: `block drop in on uap0 all`, with passes for DHCP, broadcast, SSDP, 8100, multicast, high UDP ports and ICMP echo. `en5`, `mam0`, `mep0` and `mhp0` pass in full. |
| Routes | `10.0.0/24` on `mam0`, `192.168.1/24` on `uap0`, SSDP multicast via `uap0`. No default route. |
| Boot log | Not obtained. By 9 minutes of uptime the slog buffer started at `00:02:37`, overwritten mostly by `io-fs-media -d mediabt,verbose=5`; the Wi-Fi attach is at about `00:00:54`. The October 20 captures hold it. A boot-time capture needs a read within about 2 minutes of start-up. There is no Wi-Fi file among the persisted `dev-irc` logs. |
| Link speed | About 2 KB/s through the Pi relay (32 KB in 17 s), so the full slog cannot finish inside the collector's 12-second watchdog. The stage reads a filtered log instead. |

## USB Ethernet driver matching

Offline, 2026-10-24. Saved IFS `devn-asix.so` (`085f2658…a3df241a`),
decompiled with `tools/ghidra/AsixUsbTrace.java` (the Wi-Fi trace script,
pinned to this hash). Nothing was run on the unit.

- `ax_insertion` and `ax_attach` both call `ax_verify_device_supported`.
  It compares the vendor and device from the USB stack's device instance
  (the adapter's descriptor) with the table at data `0x21b74`. An unlisted
  pair returns `0x13` (`ENODEV`) and logs "Device not supported by this
  driver". On a match it copies the chip code to instance `+0x76c` and the
  flags to `+0x824`.
- The driver's own suboptions are `path`, `wait`, `busnum`, `devnum`,
  `receive`, `transmit`, `phy_88772` and `pnp`. `vid`, `did`, `lan`,
  `speed`, `duplex` and `mac` are generic `nic_parse_options` settings
  stored in the configuration block at `ax_ctrl+0x40`. Matching never reads
  them, so no mount option makes `0b95:772b` attach.
- The AX88772B code is present: `ax_configuration` calls
  `ax_enable_88772B` for chip `0x88772b`, and `ax_enable` sends the extra
  88772B vendor commands. The flags word is written to the GPIO register
  (vendor command `0x1e`). The DUB-E100 C1 entry uses `0x80`; the AX88772
  entries use `0xb0`.
- `vdev-medialaunch` mounts network drivers into `io-pkt` with
  `busnum=%d,devnum=%d,%s` and then runs `ifconfig`. Its interface path
  format is `/dev/io-net/en%d`.

A RAM-only workaround is prepared but not run.
`wifi-sta/patch_asix_772b.py` checks the input hash and the original 16
bytes at file offset `0x10b94`. It then rewrites that entry's vendor and
device from `2001:1a02` to `0b95:772b`, keeping chip `0x88772b` and flags
`0x80`. Exactly 4 bytes change. The output
`work/build/devn-asix-772b.so` (SHA-256 `9de65625…c99866965`,
`cksum` `2558431275 75540`) passes `verify_elf.py --shared` against the
saved libc. The copy no longer recognises a DUB-E100 C1.

Proposed live steps (not run; each needs the owner's go-ahead):

1. Copy the file to `/tmp/devn-asix-772b.so` over SSH and check `cksum`.
   `/tmp` is RAM; nothing is written to flash.
2. Read slog to check that no other driver (for example the
   `io-fs-media` catch-all PFS entry) has claimed the adapter.
3. `mount -T io-pkt -o lan=5 /tmp/devn-asix-772b.so`, then
   `ifconfig en5 172.16.250.248 netmask 255.255.0.0` (the factory address).
   Speed is left to autonegotiation.
4. From the Pi: `172.16.250.1/16` on `eth0`, ping, then SSH on port 2222.

First live attempt, 2026-10-24, at about 00:24 uptime, with the owner's
go-ahead. Private log: `<private>/live-20261024-en5/`.

- The copy was uploaded to `/tmp` (a link to `/dev/shmem`), and `cksum`
  matched.
- `mount -T io-pkt -o lan=5,verbose /tmp/devn-asix-772b.so` returned 0.
  slog then showed `shim: unable to init dll …: No such device`, and io-pkt
  unloaded it; its object list and `uap0`/`mam0` were unchanged.
- No `ax_insertion` line was logged, so the USB stack reported no matching
  device.
- The SDK's QNX 6.3.0 `usb -v` (run from `/tmp` with its own
  `libusbdi.so.2`; the unit has neither) listed only the internal Telit
  modem (`1bc7:1003`, host controller 2). The adapter was not enumerated at
  all, so the patched entry was not exercised.
- `/tmp/devn-asix-772b.so`, `/tmp/usb` and `/tmp/libusbdi.so.2` were left in
  RAM for a retry; a restart removes them.

Risk: the driver runs inside the shared `io-pkt-v4-hc`. A crash would drop
`uap0` (all current access) and MOST IP until the unit restarts. Rollback is
`umount` of the interface or a restart; nothing persists. The 88772B code path
is factory code, but this vendor/device pair and the flags value are untested
with this board.

## Phase 2 results

### AMI cable coding gates USB entirely

Local hardware result, 2026-10-25 to 2026-10-27. Private logs:
`<private>/live-20261025-en5/`, `<private>/live-20261027-en5/`,
`<private>/live-20261027b-en5/`.

The 2026-10-24 failure was not a driver-matching problem. No USB device
enumerated at all, including a plain flash drive, because the AMI cable in
use identified as an analogue AUX cable and the unit does not power the USB
data path for that cable type.

An identification resistor across AMI pins 21/22 selects the cable type the
IOC reports; USB data is on pins 17/18 with power and ground on 25/28. The
engineering `System` screen shows the decoded result as "cable code info
(hex)". `MMI3GMedia` holds the code-to-name table at VA `0x082096dc`:

| Code | Name | Code | Name |
| --- | --- | --- | --- |
| `0x00` | `NO_AMI` | `0x08` | `CEA2017` |
| `0x01` | `IPOD_WITH_USB` | `0x09` | `BLUETOOTH` |
| `0x02` | `AUX_3_5` | `0x0a` | `AUX_AV` |
| `0x03` | `IPOD_OLD` | `0x0b` | `IPOD_VW` |
| `0x04` | `USB` | `0x0c` | `IPOD` |
| `0x06` | `MTP_WITH_USB` | `0xfe` | `UNKNOWN` |
| `0x07` | `ZUNE` | `0xff` | `NO_MITSUMI` |

Anything else renders as "UNKNOWN".

- The first cable read `02` (`AUX_3_5`). Nothing enumerated.
- Substituting an 18.7k resistor taken from an AMI-to-iPod cable changed the
  reported code to `0C` (`IPOD`). A phone connected to the cable charged, so
  5 V was present, but still nothing enumerated. Changing the resistor alone
  did not restore the data path on this cable.
- A genuine USB AMI cable, which the unit identified as a USB cable, worked
  immediately: a flash drive mounted and played, and the Ethernet adapter
  enumerated.

Check the reported cable code before suspecting the adapter or the driver.

The engineering `/usb` screen (`USB.esd`, `cksum 1354437587 1326`) exposes
"Hub + iPod/USB cable support" (`per 1 0x00180008`) and a polled "Num of
attached devices" (`per 1 0x00180000`). Leaving the checkbox clear did not
prevent enumeration; it was not the gate.

#### USB topology

One `/sbin/io-usb -c -d ehci -d ohci pindex=0,dis_sf,pindex=1,dis_sf`
provides USB 0 (EHCI), USB 1 (OHCI) and USB 2 (OHCI). The internal Telit
modem (`1bc7:1003`) sits on controller 2 at Full speed.

The AX88772C enumerates on controller 0 at **High** speed, drawing 200 mA.
It would not enumerate through a USB A extension cable that passed a flash
drive without trouble; connected directly to the AMI cable it enumerated at
once. Treat an extension that works for slower devices as unproven for this
adapter.

The unit has neither `usb` nor `libusbdi.so.2`. The SDK copies
(`<QNX SDK>/target/qnx6/shle/`) run from `/tmp` with
`LD_LIBRARY_PATH` pointing there.

### en5 established

Local hardware result, 2026-10-27, with the owner's go-ahead.

`mount -T io-pkt -o lan=5 /tmp/devn-asix-772b.so` returned 0 and the patched
entry attached. slog recorded `devn-ax: MII transceiver found at address 16.`,
the `io-pkt shim` and `Asix` lines, `devn-ax: link up (100 BaseT Full
Duplex)` and `ifwatchd watching interface en5`. `en5` came up at 100baseTX
full duplex. `uap0` and `mam0` were unaffected and `io-pkt-v4-hc` kept the
same pid throughout, so the 88772B path in the factory driver works with
this board.

Two findings change how the interface should be used:

- **The factory stack configures `en5` by itself.** Once the interface
  appears, `ifwatchd -D /etc/dhcp/dhcp-down en5` and `dhcp.client -b -i en5`
  start without intervention and take a lease. The 172.16.250.248/16 static
  named in the earlier proposal is only reached when no DHCP server answers,
  so step 3 of those proposed steps is unnecessary.
- **Factory `pf` already carries `pass in quick on en5 all keep state`.**
  The wired side is fully open inbound. Telnet on port 23 answers there, as
  do roughly fifteen Harman service ports. Scope `pf` before putting `en5`
  on a shared or untrusted network.

The installed `sshd` has `ListenAddress 192.168.1.1` and therefore does not
answer on `en5`. A second instance is needed; see below.

`ifconfig` on a driver hosted by `devnp-shim` rejects `SIOCSIFMEDIA`
("Inappropriate I/O control operation"), so speed and duplex cannot be
forced. Only the driver's own autonegotiation applies.

#### Diagnosing the wired link

A hand-terminated run initially linked at 100baseTX full duplex and passed
ICMP, but SSH hung at key exchange and timed out at `LoginGraceTime`. Loss
scaled with frame size:

| ICMP payload | Loss |
| --- | --- |
| 56 B | 20% |
| 972 B | 60% |
| 1472 B | 80% |

That pattern is a physical fault, not a software one: split pairs link and
pass small frames while corrupting large ones, and SSH fails as soon as it
needs a full-size frame. Reterminating both ends cleared it completely
(0% at 56, 500, 972 and 1472 B over 20 packets each). Note that the io-net
shim driver reported `Ierrs 0` throughout, so that counter does not detect
this.

Measured afterwards: 1 MB over SSH in 7.0 s (about 149 KB/s) with matching
checksums, against roughly 2–10 KB/s for the Wi-Fi relay.

#### Persistent installation

`wifi-sta/en5-persistent-start.sh` installs as
`/mnt/persistence/en5/start.sh` with the patched driver beside it.
`wifi-sta/install-en5-persistent.sh` stages and hooks it.

- The start script refuses to mount unless the driver's `cksum` is exactly
  `2558431275 75540`, because it is a fixed-offset patched binary.
- It tolerates repeated invocation: it will not mount a second copy into the
  shared `io-pkt` if `en5` exists, and will not start a second `sshd` if one
  already owns the pid file.
- It clears `LD_PRELOAD` for the same reason the OpenSSH startup does.
- It continues without carrier, so a cable connected later needs no re-run.
- The address is a static alias set at the top of the script. It is
  site-specific; DHCP is left running but not relied upon.
- The second `sshd` reuses the installed binary, host key and authorized
  keys, and differs only in `ListenAddress` and `PidFile`. Its config, log
  and pid file are RAM copies. The installed instance and its config are not
  modified.

The installer appends **one line** to `/mnt/persistence/openssh/start.sh`,
which already runs at boot through the OpenSSH hook, and deliberately does
not touch `lsd.sh` or `pf.conf`. Both are checksum-pinned and already
modified once by the OpenSSH installation; reusing the existing hook avoids
editing them again. The appended call is detached and its failure ignored,
so a fault in the `en5` path cannot stop the `sshd` that provides access.

Rollback and recovery, in order of independence:

1. `/mnt/efs-persist/en5.disabled` — the start script exits immediately if
   this exists. No payload code runs.
2. `/mnt/efs-persist/en5-rollback.sh` — restores
   `/mnt/persistence/openssh/start.sh` from `start.sh.pre-en5` and sets the
   kill switch. It lives outside the payload, so it works if the payload is
   broken.
3. `/mnt/persistence/en5/backup/` — holds `start.sh.original` and its
   recorded `cksum`.

Nothing in `efs-system` changes, so a firmware update that overwrites EFS
hooks removes the OpenSSH hook and this path with it.

Restart verified 2026-10-27. After an unattended restart the unit answered
on the wired address 53 seconds after it stopped responding, with
`/fs/tmpfs/en5-boot.log` recording:

```
en5: driver mounted.
en5: address 192.168.16.11 configured.
en5: second sshd ready on <address>:2222; PID 1232934.
```

`en5` was active at 100baseTX full duplex with 0% loss at 56 and 1472 B,
the resident driver still checksummed `2558431275 75540`, and `uap0`
(192.168.1.1) and MOST `mam0` (10.0.0.100) were both intact. No manual step
was involved.

## Detach and firmware observability

Offline, 2026-10-27, from the saved October 20 backup: `devnp-mv8688uap.so`
(`311b947c…53d269f7`, `cksum 1022510863 93538`, which is also what the live
unit reports), `sbin/io-pkt-v4-hc` (`8c185451…0f6ca3b7`), `proc/boot/libc.so.2`
(`57060bfc…17f5cf99`) and `bin/sloginfo`. Decompiled with
[`tools/ghidra/Mv8688UapTrace.java`](../../tools/ghidra/Mv8688UapTrace.java)
and the new
[`tools/ghidra/IoPktDetachTrace.java`](../../tools/ghidra/IoPktDetachTrace.java),
each pinned to its binary's SHA-256. Export and import tables were read with
[`openssh/verify_elf.py`](https://github.com/chundla/qnx632-openssh/blob/main/verify_elf.py). Nothing was run on the
unit. These are offline reconstructions; the behaviour is still unverified on
hardware.

### `ifconfig uap0 destroy` reaches the driver's detach

QNX patched the `SIOCIFDESTROY` path. In `ifioctl` (`0x08095c62`), ioctl
`0x80906979` does not go to the NetBSD cloner layer first:

```c
ifp = ifunit(name);
if (ifp != NULL && dev_detach_name(ifp->if_xname, 1) == 0)
    return 0;            /* QNX device path: the driver's own detach */
quiesce_all();
rc = if_clone_destroy(name);   /* NetBSD fallback, for pseudo-devices */
unquiesce_all();
```

`dev_detach_name` walks `alldevs`, matches the interface name and calls
`dev_detach(dev, 1)`, which calls the autoconf `ca_detach` entry — for this
driver `mv8688uap_ca.detach` at `0x14e80`, the routine described under
[Detach](#detach). So the mechanism exists and is the first thing tried.
`if_clone_destroy` by itself only searches the cloner list and returns
`EINVAL` for a hardware interface, so the QNX branch is what makes this work.

Two consequences matter for the experiment:

- **`dev_detach` quiesces the whole stack.** Unless the device sets flag
  `0x8000`, it calls `quiesce_all()` around the detach. The uAP detach then
  waits up to 64 quarter-second sleeps for pending firmware commands, so
  every interface in this `io-pkt` — `mam0`, `mhp0` and the `en5`
  management link — can stall for up to about 16 s. A stall of that length
  is expected, not a failure.
- **The DLL is unloaded.** On a clean detach `dev_detach` calls
  `nw_dlclose()` when no other device shares the handle, so the next mount
  re-reads the object from disk. A detach that returns 0 while another
  device still lists this one as its parent calls `panic()`; nothing in the
  factory configuration creates such a child.

Upstream QNX documentation agrees that `ifconfig <iface> destroy` is the
removal path for a driver loaded with `mount -T io-pkt`, and that `umount`
applies only to legacy io-net interfaces such as `/dev/io-net/en0`
([6.6.0](https://www.qnx.com/developers/docs/6.6.0.update/com.qnx.doc.core_networking/topic/drivers_Loading.html),
[7.0](https://www.qnx.com/developers/docs/7.0.0/com.qnx.doc.core_networking/topic/drivers_Loading.html)).
That distinction also applies to the `en5` path: `devn-asix.so` runs behind
`devnp-shim`, and the shim's interfaces are the io-net case.

### The driver's firmware messages cannot be read without the serial console

Phase 1 suspected the serial console. The link is now identified:

- Every firmware message (`UAP FW already running! Skip FW download`,
  `Downloading helper image (%d bytes)`, `Downloading FW image (%d bytes)`,
  `FW download over`, `UAP FW is active`) is emitted by `log()`, gated by
  bits of the driver's `drvdbg` word (initially `0x40000007`).
- `log` is an *imported* symbol. The driver's dynamic table has exactly one
  logging import, `log`, and `NEEDED` names only `libc.so.2`. The saved
  `libc.so.2` does not export `log`; `io-pkt-v4-hc` does (it is NetBSD's
  `log(int level, const char *fmt, ...)`, sharing the string-table suffix of
  `vlog`, which is why a plain string search misses it).
- That `io-pkt` build exposes no way to read what `log()` writes: it
  registers no `/dev/klog`, and its sysctl tables have no `msgbuf` node
  (`sysctl` is on the unit, but `kern.msgbuf` is not in the binary). Its
  stdout and stderr are `/dev/ser1`, as phase 1 found.
- Empirical confirmation: the October 17 boot capture is a full 2,709-line
  `sloginfo` that contains the `hbsdc_init` and `mv8688uap_pb_init` lines but
  no firmware line at all, even though a cold boot must download firmware.

The driver's *other* messages do reach slog, because `slogf`, `vslogf` and
`nic_slogf` are linked into the driver itself and write to `/dev/slog`. The
two major codes it uses are **1007** and **10**. One of them is the
discriminator this experiment needs: on a firmware failure
`mv8688uap_probe` calls

```c
slogf(0x3ef, 2, "%s()/%d: uap_init_fw() failed.", "mv8688uap_probe", 0x472);
```

so a failed firmware load is visible even though a successful one is not.

### A probe that answers "was the firmware reloaded?" from slog alone

`uap_init_fw` checks `sbi_check_fw_status` first. If firmware is already
running it returns success without reading a file. Otherwise it calls
`request_firmware` for `<fwpath>/FwImage/helper_sd.bin`, and a negative
result aborts the attach, which makes `mv8688uap_probe` log the line above.

`fwpath` is a mount option. Mounting with `fwpath` pointing at a directory
that holds no `FwImage` therefore turns the question into a pass/fail that
slog can answer:

| Outcome | Meaning |
| --- | --- |
| `uap0` attaches | The firmware was still running; nothing reloaded it. |
| Attach fails with `uap_init_fw() failed.` | The firmware was gone, so the preceding step cleared it. |

A failure also has to be told apart from one *before* the firmware step: if
`sdio_attach_device` cannot see the card at all (wrong reset polarity, or
power actually removed), the attach fails without that line.

`sloginfo` on this unit supports `-t` (millisecond timestamps) and `-m code`
(major-code filter), so the attach can also be timed: the interval between
`hbsdc_init()/833: MemBase[0]=…` and `mv8688uap_pb_init()/568: selected
DMA=0x104` brackets the SDIO attach and the firmware download. Second
resolution was too coarse to use (the cold-boot attach crosses one second
boundary); milliseconds should separate a 307,396-byte PIO download from a
skip. Do not pass `sloginfo -c`: it clears the buffer other work depends on.

### Restoring the driver must use `resetmask=4`, not the factory `resetmask=0`

`_hbsdc_powerup` releases the reset bits *only when the mask is non-zero*,
and the detach path's `sdio_stop` → power-down asserts them when it is.
So a probe that fails with `resetmask=4` leaves the reset line asserted, and
a later mount with the factory `resetmask=0` would neither release it nor
touch it: the card would stay held in reset until a power cycle. The restore
mount, and any watchdog, must use `resetmask=4` with a valid `fwpath` so the
power-up releases the line and downloads firmware again.

### A manually mounted `uap0` is not a working access point

`NWSProcess` programs the SSID, channel and WPA material through driver
ioctls after its own mount; `hostapd.conf` is parsed by the networking
service, and `hostapd` does not run. A manual `mount -T io-pkt` therefore
produces an attached radio with no network configured. Restoring a *usable*
access point without a restart depends on the networking service doing its
own mount — which is why switching Wi-Fi off and on in the HMI is the first
step of phase 3 rather than an afterthought. Do not read a present `uap0` as
restored service.

## Phase 3a results

Local hardware result, 2026-10-28 (unit uptime 00:02–00:13 of one start-up;
host clock 2026-10-27 23:54 to 2026-10-28 00:14 EDT). Read-only: the owner
switched Wi-Fi off and on in the HMI, and the capture only read. Nothing was
written on the unit, mounted, detached or staged. Access was the wired path,
`MMI_SSH_HOST=192.168.16.11 … --stage wifi-toggle` (four runs, all
`success`). Private logs: `<private>/live-20261027-toggle-before/`,
`live-20261027-toggle-off/` (the setting selected but not yet applied),
`live-20261028-toggle-off-applied/` and `live-20261028-toggle-after/`.

`sloginfo -t` gives millisecond stamps, so this start-up also provides the
reference for an attach that certainly downloaded firmware:

| Unit clock | Event |
| --- | --- |
| `00:00:56.180` | `hbsdc_init()/833: MemBase[0]=c04a0000` |
| `00:00:56.251` | `_hbsdc_bus_speed(): SDIO fast clock mode *spd=25000000` |
| `00:00:56.676` | `mv8688uap_pb_init()/568: selected DMA=0x104` |
| `00:01:12.953` | `MICRO_AP_EV_BSS_ACTIVE`, then `unknown event 81` at `.974` |
| `00:04:30.966` | `MICRO_AP_EV_BSS_IDLE` — Wi-Fi switched off |
| `00:12:53.599` | `MICRO_AP_EV_BSS_ACTIVE`, then `unknown event 81` at `.622` |

So a cold attach spends **425 ms** between the bus-speed line and
`pb_init` — the window that holds `sdio_attach_device` plus the 2,616-byte
helper and the 307,396-byte firmware download. That is the number a later
re-attach gets compared against.

The off/on cycle changed nothing else at all:

- **No re-attach and no firmware download.** No `hbsdc_init`, no
  `_hbsdc_bus_speed` and no `pb_init` line appeared between the two BSS
  events. Switching Wi-Fi off and on issues only the uAP BSS stop and start
  (host command `0x00b0`).
- **The driver is never unloaded.** `devnp-mv8688uap.so` stayed at
  `@78256000` in `io-pkt`'s object list in all four samples.
- **`uap0` never goes away.** It stayed `UP,BROADCAST,RUNNING` with
  192.168.1.1/24 and its `192.168.1/24` route throughout, and the factory
  `pf` rules were unchanged.
- **`dhcpd` is not restarted.** The same pid kept serving
  `dhcpd.uap0.conf` on `uap0` across the cycle.
- **The reset line is not used.** `/dev/sysregs/WlanEnable` read
  `0x00000000` in every sample, Wi-Fi off included, as did `BtReset`.

Two details are worth recording for anyone repeating this:

- The checkbox acts when it is selected, not when "apply settings" is
  pressed. `BSS_IDLE` arrived at `00:04:30.966`, before the owner pressed
  apply; the apply itself produced no event and changed nothing.
- `MICRO_AP_EV_BSS_ACTIVE`/`_IDLE` are read here as BSS start/stop rather
  than client association, on three grounds: each `ACTIVE` is followed about
  20–30 ms later by the same `unknown event 81`; the October 20 capture holds
  an `IDLE` at `00:08:45.504` with an `ACTIVE` 6.7 s later, which is the
  shape of an off/on cycle; and the `192.168.1.109`/`.110` entries that look
  like associated clients are `UHLS` — static ARP, not evidence of a station.
  This is inference from the pattern, not from Marvell's event
  documentation. No client association was tested: the Pi's `wlan0` was
  administratively down throughout, so whether the hotspot was beaconing was
  not confirmed from the air.

What this means for the remaining phases:

- **The HMI switch does not free the radio.** Phase 4 cannot ask the HMI to
  release the chip; it has to detach the interface itself.
- **The HMI switch is not a rollback.** It neither re-mounts nor
  reconfigures the driver, so after a manual detach it cannot restore a
  configured access point. A re-attach downloads fresh firmware, which holds
  no SSID or key, and only `NWSProcess`'s own mount path programs those.
  **A restart is the rollback for phases 3b and 3c**, and the 2026-10-27
  reboot test showed an unattended restart restores the access point and the
  wired path together.
- A BSS that is idle, with no client associated, is the least disruptive
  moment to attempt the phase 3b detach.

A calendar anchor came free: the start-up `BENCHMARK` line
`2026-10-27 23:51:45.791 … Activator#initWlanTrackers` carries a real date at
unit clock `00:00:53.026`, which puts that start-up at about 23:50:52.8 host
time. Treat it as a correlation from one log line, not as a time source.

## Phase 3b and 3c results

Local hardware result, 2026-10-28, with the owner's go-ahead (unit uptime
00:25–00:28 of the same start-up as [phase 3a](#phase-3a-results)). Private
log: `<private>/live-20261028-radio-probe/`.
[`wifi-sta/radio-reattach.sh`](../../wifi-sta/radio-reattach.sh) (`cksum`
`4114295377 16860` as run) was copied to `/fs/tmpfs`, verified by `cksum` and
run detached. Everything written stayed in RAM. Three detach cycles were
performed in total; the third was a hand-run follow-up, not part of the
script.

Two utilities the script had assumed are **not on this unit**: `nohup` and
`wc`. There is no `tail` or `head` either, and this `grep` rejects an empty
pattern while `grep -c .` skips slog's blank lines, so line counting uses
`sed -n '$='`. Detaching uses `ksh -c 'trap "" HUP; exec …' &`, since an
ignored signal disposition survives the `exec`. The `wc` call had been the
bound on each slog read, and without it the probes would have searched the
whole buffer and could have matched an *older* failure.

### `ifconfig uap0 destroy` reaches the driver, on hardware

`destroy` returned 0, `uap0` disappeared within a second, and
`devnp-mv8688uap.so` left `io-pkt`'s object list, so `nw_dlclose()` ran. The
driver's own detach is visible in slog:

```
00:25:06.304  mv8688uap_ioctl()/134: dying END(EIO)
00:25:06.306  mv8688uap_cmd_cleanup()/647: START  cmd_pending=0
00:25:06.308  mv8688uap_stk_callback_cleanup()/640: END  stk_callback_pending=0
```

This confirms the offline reconstruction: `ifioctl` → `dev_detach_name` →
the autoconf `ca_detach` entry. `NWSProcess` did **not** re-mount the driver
in the 20 s that followed, and never did so afterwards.

### The detach alone clears the firmware

Probe A re-attached with the factory `resetmask=0` and `fwpath` pointing at
an empty directory:

```
00:25:30.865  hbsdc_init()/833: MemBase[0]=c04a0000
00:25:30.865  hbsdc_init(): Reset bit offset=8 mask=00000000
00:25:30.916  _hbsdc_bus_speed(): SDIO fast clock mode *spd=25000000
00:25:30.918  mv8688uap_probe()/1138: uap_init_fw() failed.
```

The card answered SDIO but had no firmware. So a detach plus the module
register `0x28` power-down and the 1 ms `CTRL=DEV_RESET` pulse is enough to
clear it, and **the `WlanEnable` reset line is not needed to get firmware
reloaded**. That settles the question the original phase 3 gate asked, by
its second branch.

It also means probe B could prove nothing: with the firmware already gone it
would have failed whatever the reset line did. The script claimed otherwise
on this run; that message has since been corrected, and
`test_probe_b_claims_nothing_once_probe_a_found_no_firmware` pins the
behaviour.

### The reset line is real, and `resetmask=0` cannot undo it

Probe B's value turned out to be the register trace. With `resetmask=4` the
driver read-modify-writes system register 8:

```
00:25:33.121  hbsdc_init(): Reset bit offset=8 mask=00000004
00:25:33.122  _hbsdc_powerdown(): reset=00000306     <- bit 2 set
00:25:33.134  _hbsdc_powerup():   reset=00000302     <- bit 2 cleared
00:25:33.175  mv8688uap_probe()/1138: uap_init_fw() failed.
00:25:33.178  _hbsdc_powerdown(): reset=00000306     <- failure cleanup re-asserts it
00:25:33.178  mv8688uap_detect(): dev_attach() failed
00:25:33.178  Unable to init /lib/dll/devnp-mv8688uap.so: No such device
```

The third cycle then demonstrated the consequence predicted offline. A
`destroy` of that `resetmask=4` instance ran `_hbsdc_powerdown(): reset=
00000306`, leaving bit 2 asserted, and the factory `resetmask=0` mount could
not recover:

```
00:28:00.305  hbsdc_init(): Reset bit offset=8 mask=00000000
00:28:00.333  _hbsdc_iprocess(): Command Timeout      <- about 100 of these
              mount: Can't mount / (type io-pkt); Possible reason: No such device
```

The card does not answer SDIO at all while the bit is set, which confirms
both that system register 8 bit 2 gates the 88W8688 and that the inferred
polarity is right: **set holds the radio off**. A `resetmask=4` mount
recovered it immediately. Keep `resetmask=4` for any restore, as the script
and its watchdog do.

One consequence for the gate: the *factory* re-attach was not fairly tested
in this run, because the instance it followed was a `resetmask=4` one that
asserted the line on its way out. It was tested separately after the restart;
see below.

### Firmware download is measurable, and the rollback works

Every successful attach reloads the firmware, and the timing is consistent
with the 425 ms cold-boot reference from phase 3a:

| Attach | `_hbsdc_bus_speed` → `pb_init` |
| --- | --- |
| Start-up (phase 3a reference) | 425 ms |
| Restore after the probes | 458 ms |
| Recovery after the failed factory mount | 479 ms |
| The start-up that followed the restart | 421 ms |
| Factory-parameter re-attach (below) | 452 ms |
| The start-up after the second restart | 412 ms |

A failed attach is distinguishable at a glance: probe A failed 2 ms after the
bus-speed line.

### What the unit looks like afterwards

`mam0` (10.0.0.100) and `en5` (192.168.16.11) came through all three detach
cycles untouched, `io-pkt-v4-hc` kept pid 40981 throughout, and
`/dev/sysregs/BtReset` stayed `0x00000000`, so Bluetooth was never
disturbed. `WlanEnable` reads `0x00000000` again.

The access point, however, is **down until a restart**, and more plainly than
expected:

- `uap0` came back with **no hardware address** — `00:00:00:00:00:00`, where
  the factory-mounted interface had `00:1c:d7:xx:xx:xx`. The MAC is
  programmed after the mount, not read from the card by the attach.
- No `MICRO_AP_EV_*` event followed any of the three re-attaches, so the BSS
  was never started.
- `192.168.1.1/24` *was* back on the interface before the script set it,
  without any action of ours. Something in the factory stack re-applies the
  static address when an interface called `uap0` appears; which component was
  not established. It re-applies the address only — not the MAC, not the
  radio configuration.

So the earlier conclusion holds and is now observed rather than inferred: a
manual mount yields an attached radio, not a working access point, and a
restart is the rollback.

### The restart restored everything

Verified immediately afterwards, 2026-10-28 00:30 host time. The unit
answered ICMP on the wired address 00:30:13 and SSH on it at 00:30:20,
without intervention — a fourth unattended validation of the persistent
`en5` installation, whose boot log again read `driver mounted` / `address
192.168.16.11 configured` / `second sshd ready`. On the radio side:

- `uap0` has its hardware address back, `00:1c:d7:xx:xx:xx`, with
  192.168.1.1/24;
- the attach ran with the factory `mask=00000000` and took 421 ms, so
  firmware was downloaded as usual;
- `MICRO_AP_EV_BSS_ACTIVE` arrived 15.4 s after the attach, the same spacing
  as the previous start-up, so the networking service configured and started
  the BSS;
- `mam0` is back on 10.0.0.100.

A restart is therefore a complete rollback for this experiment, with no
manual step and no power-off. It was confirmed twice: the second restart,
after the factory re-attach check, restored the same things — wired path up
in about 40 s, `uap0` MAC back, a 412 ms attach and
`MICRO_AP_EV_BSS_ACTIVE` 15.6 s later — for five unattended validations of
the persistent `en5` installation in total. Whether a *power-off* cycle
differs in any way is still phase 3d.

Nothing from phases 3b and 3c persists. Everything the runs wrote lived in
`/fs/tmpfs`, and a restart removed all of it; the only persistent Wi-Fi-side
files on the unit remain the `en5` installation and its rollback script.

### The factory parameters are a sufficient re-attach

Run from that start-up's factory `resetmask=0` instance, 2026-10-28 00:36
host time, with `radio-reattach.sh -F` (`cksum 1844021547 19153`), which
skips the probes and tries the factory parameters first. Verdict `OK`.

The detach of a `resetmask=0` instance leaves the reset line alone — there is
no `_hbsdc_powerdown(): reset=` line in its window at all, unlike the
`resetmask=4` detach earlier. The factory mount then re-attached
unassisted:

```
00:06:07.009  mv8688uap_ioctl()/134: dying END(EIO)        <- detach, no reset= line
00:06:31.827  hbsdc_init(): Reset bit offset=8 mask=00000000
00:06:31.910  _hbsdc_bus_speed(): SDIO fast clock mode *spd=25000000
00:06:32.362  mv8688uap_pb_init()/568: selected DMA=0x104   <- 452 ms, a download
```

So **the factory mount parameters are enough on their own**: detach, mount,
firmware downloaded, interface back. The `resetmask=4` reset line is needed
only to recover a card that an earlier `resetmask=4` instance left asserted —
a situation nothing in the factory configuration creates. That completes
phase 3c.

`uap0` again came back with no hardware address and no BSS, and the static
address was again re-applied by something other than this script, so this
run also needed a restart to restore the access point.

## Upstream LAN setup compared

`DrGER2/MMI3GP-LAN-Setup` was reviewed and **not** used. It is not a
prerequisite for `en5` and loads no driver. It is a persistent EFS change
aimed at online services: it sets `usedhcp` and `DLinkReplacesPPP`, replaces
`nws.cfg`, the `dhcp-up`/`dhcp-down` scripts, `dnsrdr*.conf`, `pf.conf`
(with `nat on en5`), `resolv.conf`, browser and POI configuration and a
MyAudi jar, and replaces `/etc/shadow` with a root account that has no
password.

The shadow replacement and the wholesale `pf.conf`/NWS substitution conflict
with this repository's access rules, which require preserving backing
account files and scoping firewall changes to the intended client and
interface. `MMI3G-Toolkit`'s `modules/usb-net` was declined for the same
class of reason: its script opens an unauthenticated root shell on port
2323.

The useful confirmation from both is only that `en5` is the expected
interface name for a USB Ethernet adapter on this platform.

## Upstream proposal reviewed

| Upstream statement | Finding |
| --- | --- |
| Copy `sd8688.bin` to `/tmp/FwImage/sd8688_ap.bin`, `slay -f devnp-mv8688uap`, and the driver restarts with station firmware | The driver is a DLL inside `io-pkt-v4-hc`, not a process. Killing that `io-pkt` would also drop MOST IP (`mam0`), `pf` and our SSH. Without a chip reset the running uAP firmware stays loaded (`Skip FW download`; factory `resetmask=0`). Interface start-up sends uAP command `0x00b0`, and TX uses the uAP TxPD. Do not run this test. |
| `/tmp/FwImage` is checked before the IFS copy | Only when no `fwpath` is given. The factory mount passes `fwpath=/lib/dll`, so `/tmp/FwImage` is not read. |
| Start `io-pkt-v4 -d devnp-mv8688uap.so fwpath=…` | The unit runs `io-pkt-v4-hc`. A second stack instance taking over the same FPGA SDIO module is untested and unsafe. |
| "Reboot always restores clean IFS firmware" | Partly right, for the wrong reason. A detach does clear the firmware, so a re-attach downloads the IFS copy again ([phase 3c](#phase-3b-and-3c-results)). But firmware is not what makes the access point work: a re-attached `uap0` has no MAC and no BSS until the networking service mounts it itself. A full power cycle still has to be confirmed as the rollback. |
| wpa_supplicant is not in the QNX build | Correct. `NWSProcess` parses hostapd-style WPA settings for the access point only. |
| DHCP client on Wi-Fi is missing | `dhcp.client` is in the IFS, and `NWSProcess` already has a DHCP-client mode. |
| Driver injection via `LD_LIBRARY_PATH` | Not needed: `mount -T io-pkt <absolute path>` loads a driver. Setting `LD_LIBRARY_PATH` for all processes conflicts with this repository's service-environment rules. |

## Proposed architecture

```text
phone hotspot (2.4 GHz, WPA2-PSK/CCMP)
        │ 802.11b/g
88W8688 running sd8688.bin (libertas STA firmware)
        │ SDIO, FPGA module 53
devn-mv8688sta.so  (new; io-net driver hosted by devnp-shim in the existing io-pkt)
   ├─ hbsdc host + SDIO card layer   (behaviour recovered from the factory driver; PIO first)
   ├─ helper + firmware download      (same protocol; byte-identical helper)
   ├─ libertas command/event layer    (HW spec, MAC control, scan, associate, keys, deauth)
   ├─ libertas txpd/rxpd data path    (Ethernet frames up to io-net)
   └─ control ioctls                  (scan, results, connect with IEs, set key, status)
        │
wpa_supplicant 2.x  (driver_mv8688.c backend, BPF l2_packet, internal crypto)
dhcp.client -i <if> -u -T <s>       (factory binary)
pf rules for the new interface       (block inbound like uap0)
```

Design choices and their reasons:

- **io-net via `devnp-shim`.** The SDK has the io-net DDK. Native `io-pkt`
  drivers need headers that match Harman's `core_networking` build, which we
  don't have. `devn-asix.so` shows the shim works on this unit.
- **Ethernet interface with a custom control ioctl.** The firmware handles
  association itself (FullMAC). net80211 is present, but it assumes a
  driver-visible MAC layer and native headers. A custom `wpa_supplicant`
  backend, like libertas' approach with host-side WPA, is the smaller change.
- **Reference sources.** For the station protocol, use Linux
  `drivers/net/wireless/marvell/libertas` (`if_sdio.c`, `cmd.c`, `host.h`).
  Code derived from it is GPL-2.0 and must be released under it. For the FPGA
  host, use the factory driver's `hbsdc_*` behaviour. Do not copy factory
  code into tracked files.
- **PIO before DMA.** The factory SDIO layer already moves CMD53 data by
  PIO; only the packet path uses the PicoBlaze engine. PIO avoids
  reverse-engineering the PicoBlaze firmware interface. Measure throughput
  before deciding whether DMA is needed.
- **Quick prototype option.** The factory object exports `hbsdc_init`,
  `sdio_*` and `sbi_prog_*` dynamically. A prototype could call them from a
  separate loader instead of reimplementing the host first. Their signatures,
  global state and interaction with `io-pkt` are unverified, and the factory
  driver would need to be detached first.

Phone side: 2.4 GHz only. Some phones need a compatibility or 2.4 GHz option.
WPA3-only hotspots will not work. WPA2/WPA3 transition mode should accept a
WPA2 client; the firmware's PMF capability is unknown. Hotspot client
isolation determines whether another device on the hotspot can reach the MMI.

## Phase 4 plan

Offline planning, 2026-10-28, after phases 3a–3c. Nothing in this section has
run on the unit. Since then the host layer and the 4.1 probe have been built
and tested offline; see [Host bring-up probe](#host-bring-up-probe-41-prepared-offline).
The stub from [Build check](#build-check) has still never been loaded by
`devnp-shim`. Phase 3 is what makes the work approachable — the
radio can be taken with `ifconfig uap0 destroy` and handed back with the
factory mount parameters, and the worst case of a failed attempt is a
restart.

### Two options ruled out first

**Reusing the factory driver's code.** `devnp-mv8688uap.so` exports 109
symbols, and they cover the whole lower half of the driver: `hbsdc_init` and
`_hbsdc_test`; `sdio_init`, `sdio_attach_device`, `sdio_send_command`,
`sdio_read_ioreg`, `sdio_write_ioreg`, `sdio_read_iomem`,
`sdio_write_iomem_sg`, `sdio_set_blksz`, `sdio_bus_speed`, `sdio_bus_width`,
`sdio_enable_func`, `sdio_read_cis`, `sdio_intr_enable`, `sdio_start`,
`sdio_stop`, `sdio_reset`; `sbi_check_fw_status`, `sbi_prog_helper`,
`sbi_prog_fw_w_helper`, `request_firmware`; the `uap_*` command helpers; and
the `drvdbg` mask as a data export. The
[quick prototype option](#proposed-architecture) was to call these from a
separate loader instead of reimplementing the host.

It does not work, because those symbols exist only while the object is
loaded, and the object is loaded only while it is attached — that is, while
it is driving the chip with uAP firmware. A detach runs `nw_dlclose()`
([Detach and firmware observability](#detach-and-firmware-observability)), so
the symbols go with it. There is no resident-but-idle state to borrow from.
A failed attach appears to unload it too (`Unable to init
/lib/dll/devnp-mv8688uap.so: No such device` in phase 3c), though
`io-pkt`'s object list was only checked across a failed attach for
`devn-asix.so`, not for this driver. The export list stays useful as a map of
what the lower half has to provide.

**Running a station driver alongside the access point.** Not possible on this
hardware. The chip holds one firmware image at a time and the two roles are
two images (`sd8688_ap.bin` and libertas `sd8688.bin`), so the roles are
mutually exclusive; both drivers would also map the same FPGA window at
offset `0x4a0000` and attach interrupt 56. Station mode therefore removes the
car's access point while it is active, and `en5` is the management path that
makes that acceptable. A concurrent access point would need a second radio,
not this one.

### Increments

Each step ends with the radio handed back and, where the driver attached at
all, a restart to restore a configured access point. The slog oracles from
phase 3 apply throughout: major codes 1007 and 10, `sloginfo -t` for
millisecond stamps, and `_hbsdc_bus_speed` → `pb_init` as the firmware-download
window, against the five measurements of 412–479 ms recorded above. Remember
that anything the driver sends through `log()` goes to the serial console
only, so a new driver should use `slogf` for everything it wants to read
back.

4.0 **Load the stub through `devnp-shim`.** No new code. It logs its options
and returns `ENODEV`, so `io-pkt` should reject and unload it. Pass: the
stub's own slog line appears and `uap0`, `mam0` and `en5` are untouched. This
is the cheapest possible check that the shim accepts the toolchain's output
on the target, and it needs no detach and no restart.

4.1 **`hbsdc` host bring-up.** The first real code: map both FPGA windows,
check module ID `0x53` and the version, read the clock, install host
operations, power down and up, then CMD5 for the OCR, CMD3 for the RCA, CMD7
to select, set up the CCCR, read the CIS and require vendor/device
`02df:9103`. The register map and sequence are already recovered in
[Power, reset and attach](#power-reset-and-attach). Pass: the probe reports
the CIS, the function-1 I/O port and the `0x3130` chip check. No firmware is
involved, so this isolates the host layer. Needs the factory driver detached
first. Built as a standalone process, not a driver; see
[Host bring-up probe](#host-bring-up-probe-41-prepared-offline).

4.2 **Firmware download.** Helper first, then the station image, following
the protocol in
[Firmware download and command framing](#firmware-download-and-command-framing);
the factory helper is byte-identical to linux-firmware's, so that half is
known-good. Pass: function-1 `0x40`/`0x41` reads `0xfedc` and the download
window is a few hundred milliseconds, in line with the measured range.
Passed on the unit on 2026-10-28; see [Phase 4.2 results](#phase-42-results).

4.3 **Command and event layer.** `CMD_FUNC_INIT` (`0x00a9`), then
`GET_HW_SPEC` (`0x0003`) for the firmware release, the MAC and the
capability word, then `MAC_CONTROL` (`0x0028`). Pass: a well-formed
`GET_HW_SPEC` response. This is the first point at which the station
firmware's actual identity and capabilities can be read, which several
entries under [Not established](#not-established) are waiting on. Note that
the MAC arrives from the firmware here; the factory driver programs it
separately through `uap_set_mac_address`, which is why a manually mounted
`uap0` shows `00:00:00:00:00:00`. Built and tested offline as `mv8688-probe
-C`; see [Command layer probe](#command-layer-probe-43-prepared-offline).
Passed on the unit on 2026-10-28; see [Phase 4.3 results](#phase-43-results).
The firmware reported the placeholder MAC `aa:bb:cc:dd:ee:ff`, so the host
must set the MAC itself.

4.4 **Passive scan.** `802_11_SCAN` (`0x0006`), then read the results.
Pass: the owner's own hotspot appears in the result list. This is the gate
the plan's phase 4 names, and the first evidence that the radio works as a
station at all. The MAC is set first; the owner chose to reuse the access
point's factory address, read from `uap0` at run time. Built and tested
offline as `mv8688-probe -C -M <mac> -L`; see
[Passive scan probe](#passive-scan-probe-44-prepared-offline).
Passed on the unit on 2026-10-28; see [Phase 4.4 results](#phase-44-results).

4.5 **Throughput sanity check on PIO.** Measure what CMD53 by PIO actually
achieves before deciding whether the PicoBlaze DMA path is needed, which is
one of the open items under [Not established](#not-established). Built and
tested offline as `mv8688-probe -C -T`: every transfer is timed by phase,
and a read benchmark checks every byte. See
[PIO timing probe](#pio-timing-probe-45-prepared-offline). Run on the
unit on 2026-10-28. The timing was captured, but the register-read data
check failed. RX by PIO tops out at about 1.5–1.8 MB/s, so DMA stays an
open throughput item rather than a gate; see
[Phase 4.5 results](#phase-45-results).

### Host bring-up probe (4.1), prepared offline

Offline, 2026-10-28, from the saved decompilation of the same driver hash
(`<private>/offline-analysis/wifi-detach/uap-trace.txt`, written by
`Mv8688UapTrace.java`) and instruction checks on the saved object. Built and
tested on the workstation only. It was then run on the unit; see
[4.1 results](#host-bring-up-41-passed).

**Increments 4.1–4.4 run outside `io-pkt`.** Nothing in host bring-up,
firmware download, the command layer or a scan needs to live in the shared
stack. A root process with I/O privileges can make the same
`pci_attach_device` and `mmap_device_memory` calls the factory driver makes,
and attach the same interrupt (the factory uses an `InterruptAttach_r` ISR;
the probe uses `InterruptAttachEvent`). If it faults, only that process dies. `mam0` and
the `en5` management link are untouched, and QNX releases its mappings and
interrupt on exit. Only the data path (4.5 onwards) needs the `io-net`
wrapper. That removes the first risk listed under
[Risks specific to this phase](#risks-specific-to-this-phase) for every step
before it. The code is split so the wrapper can reuse it unchanged:

| File | Role |
| --- | --- |
| [`wifi-sta/hbsdc.c`](../../wifi-sta/hbsdc.c) | FPGA host: identification, power, clock, bus width, command issue and completion, block-mode PIO. All register access goes through a hook table. |
| [`wifi-sta/sdio.c`](../../wifi-sta/sdio.c) | Card layer: CMD5/3/7, CMD52 with R5 checks, CCCR, FBR, a bounded CIS walk, block size, CMD53. |
| [`wifi-sta/sdio-probe.c`](../../wifi-sta/sdio-probe.c) | The QNX process `mv8688-probe`: PCI, mappings, guard, interrupt, report. |
| [`wifi-sta/build-probe.sh`](../../wifi-sta/build-probe.sh) | Cross-build plus `verify_elf.py` against the saved `libc.so.2`. |

Host details recovered for this, beyond the
[register table](#fpga-resources):

- **PCI.** The factory makes exactly one call:
  `pci_attach_device(NULL, PCI_SHARE, 0, &info)`, with only `VendorId` and
  `DeviceId` filled in and no search flag (checked in the instructions at
  `0xe530`–`0xe538`, not just the decompiler), after `pci_attach(0)`. The
  module is BAR0 + `0x4a0000` (64 KiB). The system registers are BAR0, mapped
  with 2 KiB. The probe makes the same calls and maps the system registers
  read-only.
- **Command issue** (`FUN_0001de00`). If `STAT` has `BUSY` or `DT_RUN`, the
  host is busy. Otherwise: `FLAG = 0xfeff`, `CTRL = c | CR_CLR`; command FIFO
  word 1 = `(0x40|index) | arg[31:24]<<8 | arg[23:16]<<16 | arg[15:8]<<24`,
  word 2 = `arg[7:0]`; then `CMDTY`, `CTRL = c | NEXT_CMD`,
  `CTRL = c | START`, `IEN = 0x1843`, where `c` is `CTRL` as read before the
  sequence.
- **`CMDTY`.** `0x2` means a 48-bit response, `0x4` a CRC check, `0xa` busy;
  R1/R5/R6 give `0x6` and R4 (CMD5) gives `0x2`. Data adds `0x20`, writes
  `0x10` more and multi-block `0x80`. A 136-bit response encodes as `0x3`
  with the CRC bit dropped; SDIO never uses one.
- **Completion** (`FUN_0001d840` ISR, `FUN_0001d8e0` decode). Read `FLAG`
  (low 16 bits), clear the fired bits in `IEN`, write them back to `FLAG`.
  Errors are `0x200` command timeout, `0x20` response CRC, `0x2000` response
  frame, `0x400` data timeout, `0x8` data CRC, `0x4000` data frame and `0x10`
  CRC token. On `CMD_RDY` two response FIFO reads give
  `[index, status 31..24, 23..16, 15..8]` and `[status 7..0, CRC]`, then
  `CTRL |= NEXT_RESP`. A power register bit 7 change event is acknowledged
  first; the factory ISR reports it as a separate card event
  (`FUN_0001d840`), and a pending one makes the factory detect fail
  (`FUN_0001d760`).
- **PIO data** (`FUN_0001dca0`, `FUN_0001dd20`). Before the command:
  `CTRL |= DATA_CLR`, `READ` set or cleared, `BLKSZ`, `BLKCNT`. After it,
  move one word per FIFO access with no flow control, clear `CMDTY` bit 7,
  poll `STAT.DT_RUN` with at most 10,001 × 1 µs sleeps, and write
  `FLAG = DT_RDY`. The probe then also checks `FLAG` for a data timeout,
  CRC, frame or CRC-token error (`0x4418`) that latched after the command
  completed, and fails the transfer on one; the factory leaves those to its
  ISR.
- **Clock** (`FUN_0001dfc0`). Clear `HS|FAST_CLK`, then
  `div = ceil(clock/hz) >> 1`. Below 2 with a clock over 66 MHz is high speed
  (49.5 MHz); below 3 is fast clock (25 MHz, divider byte 2 at 99 MHz).
  Otherwise the divided clock is `clock/(2·div)`; the byte written in that
  branch is inferred. Identification starts at divider `0xa5`, about
  300 kHz.
- **Card enables.** `IOEx` is taken from a table indexed by the function
  count (`0x2470c`: `00 02 06 0e 1e 3e 7e fe`), and `IENx` is that value
  `| 1`. For this card that is `0x02` and `0x03`. `CCCR 7` gets
  `width>>1 | 0x80`. The host switches to 4-bit only after the CIS reads.
- **A check libertas lacks.** `mv8688uap_probe` reads function-1 registers
  `0x801c`/`0x801d` and requires `0x3130` (set in the attach at
  `param+0x668`) before reading the I/O port. The probe reads and reports it.

Where the probe departs from the factory, and why:

- **Completion.** It uses `InterruptAttachEvent`/`InterruptWait` in place of
  an ISR thread, in 10 ms slices up to the factory's 1 s per command. If the
  interrupt never fires but `FLAG` latched, the command completes after one
  slice and is counted as polled, so a dead interrupt line costs about
  10 ms a command, not 1 s (an attach is about 65 commands, against the
  probe's 60 s deadline). `-p` polls
  only, without attaching the interrupt. A change event during a command is
  acknowledged and counted (`changes=` in the report), and the command
  carries on, as the factory's decode does. A change pending at detect
  fails the factory attach; the probe acknowledges it, reads the power
  register again and reports it.
- **Retries.** The CMD5 limits are the factory's (100), but three waits with
  no interrupt and no flag stop it early, so a dead host fails in about 3 s,
  not 100 s. A card-reported timeout (`FLAG 0x200`) still gets the full 100
  tries, as in the factory, with the factory's 1 ms delay after a failed
  command only. A ready OCR with no voltage window is read again up to five
  times, as in the factory (`FUN_0001f060`); the factory then accepts it, the
  probe refuses it. The factory reissues CMD3 once after a failure that left
  no RCA (`sdio_attach_device`); the probe reissues it once after a failure
  or a zero RCA and requires a non-zero one.
- **Readiness.** It reads `CCCR 0`/`1` as well and records how long `IORx`
  takes to match `IOEx`; the factory reads it once. The CIS walk follows tuple
  links within 512 bytes and 64 tuples; the factory skips unknown tuples one
  byte at a time.
- **The reset line is never written.** The probe never drives
  `WlanEnable`/system register 8 and never touches anything the factory
  `resetmask=0` mount would not.

The guard comes first and writes nothing. It is an idle-state check on a
register snapshot, not proof that no process owns the module. If `CTRL` has
`ENABLE`/`CLK_EN`/`SDIO_EN`, or the power register has bits 0–1 set, the
probe refuses: that is the factory driver's running state (`CTRL=0x7003`), and
its detach clears both. It also refuses if `WlanEnable` is set. `-n` stops
after the guard with the module mapped read-only, so it is safe to run with
the access point up. On every exit after the first write, including its own
60 s deadline, `SIGPIPE` from a closed output reader and the fatal signals,
the probe runs the factory host shutdown:
`IEN=0`, `CTRL=DEV_RESET`, 1 ms, power register `0`. The phase 3 factory
re-attaches all started from that state. It reports `key=value` lines to
stdout and slog, ending with `verdict=OK|FAIL|REFUSED|GUARD-PASS`, and exits
0, 1 (refused: from the guard, nothing written; from identification, only
the index selector), 2 (failed, card powered down) or 3 (deadline or
signal). The firmware status is reported as the ready magic (`0xfedc`)
present or absent. A single read cannot tell absent firmware from firmware
that is not yet ready; the factory retries it with 10 ms delays.

Not carried over yet, and needed before this code is reused for 4.2
onwards:

- The factory keeps an SDIO interrupt enable (`IEN 0x100`) across commands
  and acknowledges that bit separately; `hbsdc_command` writes
  `IEN = 0x1843`. That is fine while the card interrupt is off.
- `sdio_attach` leaves the clean-up after a failure to its caller (the
  probe shuts the host down); the factory powers down on OCR, select and
  identity failures.
- Card-side high-speed negotiation (`sdio_bus_speed`, `FUN_0001fd40`) and the
  transport's abort-and-retry (function-1 register 3 = 4, `FUN_0001bba0`).
  The firmware download's own abort is now carried over; see
  [Firmware download probe](#firmware-download-probe-42-prepared-offline).
- Smaller differences: the probe reads three FBR CIS pointer bytes and the
  function codes before the CIS, and a failure there is fatal; it joins CIS
  strings with `/` and does not require `VERS_1`; it refuses 136-bit
  responses; it checks R5 after CMD53.

An adversarial review on 2026-10-28 (Codex, `gpt-6.1-sol`, read-only)
compared this code with `uap-trace.txt` and the saved driver binary. It
found no wrong register offset, access width, FIFO byte order or PIO
direction. The detect, OCR, RCA, PIO-count and wording differences above
are the ones it found and that were then fixed. A second, general pass the
same day found four more, also fixed: the watchdog could restore while the
probe was still running, `SIGPIPE` skipped the shutdown, a dead interrupt
line made each command wait the full 1 s (so an attach overran the 60 s
deadline), and a data-phase error after a completed CMD53 was not
reported. Like the rest of this
section, that is agreement with saved code, not hardware validation.

`radio-reattach.sh -P <probe>` runs it in phase 3's harness: the same
preflight, `en5` check, watchdog and detach. First it runs `probe -n` while
`uap0` is still attached and stops before detaching anything unless that
**refuses** with "module is in use", so the guard is checked against the real
running driver. After the detach it runs `probe -n` again (it must pass),
then the probe itself, and then re-attaches with the factory parameters first.
The probe's pid is kept in `probe.pid` while it runs. If the run dies or
stalls with the probe still alive, the watchdog sends it `SIGTERM` (its
shutdown path), waits up to 5 s, kills it, and only then mounts the driver,
so a live probe cannot reset the card under a freshly mounted driver. A
timed-out command gets `SIGTERM` 3 s before `SIGKILL` for the same reason.
A failed probe is `PARTIAL`, not `FAIL`; the restore still runs, and a
restart is still needed for a working access point.

Build (`wifi-sta/build-probe.sh`, SDK gcc 3.3.5, `-Wall -W -Werror`):
`mv8688-probe`, 23,440 bytes, SHA-256 `2d1b3975…eaa073b`, `cksum
1422762852 23440`, identical on a rebuild. `verify_elf.py` finds all 35
imports in the unit's saved `libc.so.2`. Source revision: branch
`wifi-sta-phase4-host-probe`, the review fixes on top of `1c21d2e`.
`radio-reattach.sh` with `-P` is `cksum 1017706870 23334`.

Tests: `test_wifi_sta_host` (26) compiles `hbsdc.c` and `sdio.c` on the
workstation and drives them through `ctypes` against a fake module and an
SD8688-like card. They cover the encodings above, the attach order and power
sequence, interrupt acknowledgement, the poll fallback, dead-card and
silent-host bounds and their time cost, R5 errors, identity and CIS bounds, clock selection,
CMD53 PIO in both directions, a late data error, and shutdown. The fake's register behaviour
comes from this analysis: strobe bits do not read back (runtime `CTRL` reads
`0x7003` although power-up wrote `CR_CLR` and `DATA_CLR`), `FLAG` latches
whatever `IEN` holds, power register bit 7 clears when written back, and
FIFO data is always ready. Those are assumptions
about the FPGA, not observations. `test_radio_reattach` gained six `-P`
cases, including a probe that outlives the run and must be stopped before
the watchdog mounts. The probe's own QNX code (`sdio-probe.c`: guard,
signals, mappings) has no workstation test.

To run it once the unit is on and `en5` is up (owner's go-ahead required;
the access point is down until a restart afterwards):

```sh
# copy and verify (scp over the wired path), then:
ssh -p 2222 root@<en5 address> \
  '/fs/tmpfs/mv8688-probe -n; echo exit=$?'        # expect REFUSED, exit 1
ssh -p 2222 root@<en5 address> \
  "/bin/ksh -c 'trap \"\" HUP; exec /fs/tmpfs/radio-reattach.sh -P /fs/tmpfs/mv8688-probe' \
   > /fs/tmpfs/radio-run.log 2>&1 &"
```

Pass for 4.1: `verdict=OK` with `vendor=0x02df device=0x9103`,
`chip_id=0x3130` and an I/O port, followed by a factory re-attach that takes
the usual 412–479 ms. The report also answers several open hardware
questions on its own: whether the module interrupt fires for a process that
attaches it, the CCCR capabilities, the readiness delay, the `fw_status`
after a detach, and whether the factory attach recovers from a module this
code powered down.

### Firmware download probe (4.2), prepared offline

Offline, 2026-10-28, after the 4.1 hardware run, from the same saved
decompilation (`uap_init_fw`, `sbi_prog_helper`, `sbi_prog_fw_w_helper`,
`sbi_check_fw_status`, `FUN_0001abe0`, `sd_get_rx_unit`) and the saved
driver object. Built and tested on the workstation only; **nothing here has
run on the unit**.

[`wifi-sta/mv8688fw.c`](../../wifi-sta/mv8688fw.c) adds the download to the
4.1 layers, and `mv8688-probe -H <helper> -F <firmware>` runs it after the
4.1 steps. The factory protocol, as recovered:

- **Order** (`uap_init_fw`). Read the firmware status once. If it is the
  ready magic `0xfedc`, skip the download. Otherwise load and send the
  helper, then the firmware, then poll the status up to 100 times, 10 ms
  apart, and read the RX unit (function-1 `0x43`).
- **Ready wait** (`FUN_0001abe0`). Before every write: up to 100 reads of
  function-1 `0x20` for `IO_RDY | DL_RDY` (`0x09`), 10 µs apart (the
  `nanospin` timespec at `0x23434` is `{0, 10000}`).
- **Helper** (`sbi_prog_helper`). Chunks of 508 bytes, each preceded by its
  length as a 32-bit little-endian word and sent as two 256-byte blocks,
  then one zeroed 256-byte block. Every write is a block-mode CMD53 with a
  fixed address: function 1, the I/O port. Only the length and the data are
  copied, so a short last chunk is padded with the previous chunk's bytes;
  the probe does the same.
- **Firmware** (`sbi_prog_fw_w_helper`). The helper asks for each chunk
  through function-1 `0x10`/`0x11`. Over `0x908` is an error, checked before
  bit 0 is looked at. An even length is the next chunk, clipped to what is
  left and sent in whole 256-byte blocks, padded as above. An odd length
  asks for the previous buffer again, unchanged; the third odd request in a
  row fails. A failed CMD53 is ended by writing 4 to function-1 register 3,
  and the offset advances whether or not that write succeeds. The image is
  finished when the offset reaches its size, checked after the ready wait
  and before the next request is read.

That much is instruction-checked. What the card does is not: the factory
*relies* on the card asking for a failed chunk again through the odd length,
and nothing in the binary shows that it does. If the abort itself fails, or
the failed chunk was the last one, no further request is read and only the
ready-magic check can catch it. The same holds for a CRC report on the last
chunk.

Where the probe departs from the factory:

- **A stall is a failure.** The factory polls a zero request length 100
  times with `usleep(10)` and then returns success from that stage, leaving
  the status check to decide. The probe polls up to 1,000 times 1 ms apart
  and then fails with `fw_request` and the byte count. The longer poll helps
  only after the first ready wait, whose bound is the factory's; the
  helper's start-up time is not known, and the factory's gap after the
  helper also includes opening the firmware file, which the probe has done
  already.
- **The first request is waited for, odd or zero.** Until a chunk has been
  sent, an odd or zero request means "not yet": nothing is written and the
  register is read again 1 ms later, within the same 1,000-read bound. Each
  change is kept (`request_trace:`, up to eight). The factory instead treats
  an odd first request as a resend and issues a block-mode CMD53 with a
  block count of 0, which block mode takes as open-ended. The first version
  of this probe failed on it; see [Phase 4.2 results](#phase-42-results).
  The probe also reads the request register once before the helper
  (`rd_base_pre`), as a diagnostic only.
- **Firmware already running is a refusal.** The factory skips its
  download; the probe stops, because it would not know which image is
  running.
- **The images are pinned.** The probe reads each file whole before it
  writes anything and refuses unless its size and POSIX `cksum` match one of
  three entries for its role: the helper (2,616 bytes, `1538620194`), the
  station `sd8688.bin` (259,172 bytes, `251454034`) or the factory
  `sd8688_ap.bin` (307,396 bytes, `3281661138`). The last allows a control
  run with the image the factory loads. The report prints each image's
  size and checksum as read.
- **A second bring-up checks the firmware is gone.** The factory re-attach
  skips its download when it finds the ready magic, and would then drive the
  station image as if it were its own uAP image. After the shutdown the
  probe brings the card up again as that attach would and reads the status
  once (`post_teardown:`). That is the same single read the factory attach
  acts on; it is weaker than showing the firmware was erased. Phase 3c and
  4.1 both found the firmware gone after the factory detach, which ends in
  the same power-down, so this is expected to pass. The check runs whenever
  the download started, but not after the deadline or a signal.
- The ready-magic poll matches the factory: a failed read is retried at
  once, a read without the magic waits 10 ms, and a failed RX-unit read is
  reported (`rx_unit_rc`) but does not fail the download.

Exit codes are those of 4.1 plus **4**: the ready magic was still there
after the power-down, or a download ran and the second bring-up could not
read the status. `mv8688-probe -c` runs that check on its own: guard, bring
the card up, read the status, power down; exit 0 when the magic is absent,
4 when it is present, 1 or 2 when it could not look. Its guard is the 4.1
one, so after a probe killed with `SIGKILL` it may refuse a module left
powered; that counts as not passing.

The report adds `image:`, `helper:` (chunks, worst ready wait, ms),
`firmware:` (bytes, requests, smallest and largest request, CRC resends,
aborts, longest zero-length wait, ms), `ready:` (status, polls, RX unit, ms),
`download: total_ms`, and the `post_teardown:` lines, ending with
`verdict=OK|FAIL|REFUSED|GUARD-PASS|FIRMWARE`. The deadline is 90 s with a download.
At the largest request a 259,172-byte image is about 112 chunks of about
four commands each; the request size this helper uses is not known.

`radio-reattach.sh -P <probe> -H <helper> -S <image>` passes the images to
the probe's real run only (the two `-n` guard runs are unchanged), logs both
checksums, and bounds the run at 110 s. That bound plus the 3 s `TERM`
grace stays under the watchdog's 120 s stall window, which the probe run
does not advance. Around it:

- **The factory restore is withheld when firmware may remain.** Exit 4
  withholds it. After any other exit except 0 and 1, the script runs
  `probe -c` and withholds unless that exits 0. Withheld, it mounts
  nothing, disarms the watchdog (which would otherwise mount once the run
  exits), ends `FAIL`, and says to power the unit off fully. A restart is
  not enough: its attach would find the same firmware. Whether a full
  power-off clears it is the assumption here, not an observation; the
  `resetmask=4` reset line is a further option, but it has not been shown
  to clear firmware.
- **One owner for the restore.** The run and its watchdog each create
  `restore.owner` (`mkdir` is atomic) before mounting, so the absolute cap
  can no longer race the run's own restore into `io-pkt`. The watchdog
  gives way only when the cap catches a run that holds the lock and is
  still making progress. After a stall the run is hung or gone; an exited
  run can linger as a zombie that `kill -0` still reports. The watchdog then
  takes over, stops the run and any probe, and only then mounts. After a
  download it also runs `probe -c` first and does not mount unless that
  passes. The run skips the probe once the watchdog holds the lock, and logs
  between its two restore mounts so a slow restore cannot look like a
  stall. This ownership rule is new for every mode, not only 4.2.
- **Options are strict.** `-1`, `-F` and `-P` exclude each other, stray
  operands and empty `-P`, `-H` or `-S` values are usage errors, and the
  images go only with `-P`.

Known limits:

- The data FIFO is 2,048 bytes on this unit and the largest request is ten
  blocks (2,560 bytes). The factory code issues the same ten-block PIO write
  for such a request, with no flow control, and its download host has no
  DMA. Whether this helper ever asks for ten blocks, and how the FPGA
  absorbs 2,560 bytes, has not been seen for either image.
- Whether the receiver ignores the padding after a chunk is not known; the
  probe sends what the factory sends.
- No command is sent to the firmware, so this does not show that the
  station image runs, only that it booted far enough to write the ready
  magic. Whether that image transmits anything before it is given a command
  is not known.
- The station image is linux-firmware `HEAD 412eb786` `mrvl/sd8688.bin`,
  fetched again on 2026-10-28 into the private
  `work/firmware/` with the factory helper beside it. Its
  SHA-256 matches the pinned value in [Firmware](#firmware).

An adversarial review on 2026-10-28 (Codex, `gpt-6.1-sol`, read-only)
checked this code against `uap-trace.txt` and the instructions of the saved
driver. It confirmed the chunking, header, block counts, CMD53 arguments,
request registers and byte order, the `0x908` limit, two resends, the
abort, the end condition, the ready-magic poll, the RX-unit register, the
pinned sizes and checksums, and that the factory download uses PIO with
the full transfer size. It found that a detected surviving firmware still
led to a factory restore, that the absolute cap could race the run's own
restore, that this section claimed more than the binary shows, loose option
handling and stale `errno` reports. All of these were then fixed as
described above; the padding and ready-poll differences it listed now
follow the factory. Like the rest of this section, that is agreement with
saved code, not hardware validation.

Build: `mv8688-probe`, 30,498 bytes, SHA-256 `7d8e5c80…58821e45`, `cksum
3331856334 30498`, identical on a rebuild; `verify_elf.py` finds all 39
imports in the saved `libc.so.2`. `radio-reattach.sh` is `cksum 3589660094
29228`. Source revision: branch `wifi-sta-phase4-host-probe` on top of
`69ad7a9`.

Tests: `test_wifi_sta_firmware` (17) compiles the three C layers and runs
them against a fake card that implements the boot ROM and helper sides as
described above: chunking, padding and the end block, the CMD53 argument,
the ready wait and its bound, requests clipped to whole blocks, the largest
request, odd-length resends and their limit, odd values before the first request waited out without a write (and one that never clears), an aborted write recovered by
the resend, an oversized request, a stall, the ready-magic poll and its
limit, a failed RX-unit read, and a power-down returning the card to its
boot ROM. `mv8688_cksum` is checked against the workstation `cksum`. The
request sizes and failure modes are chosen for the tests, and the fake
models neither FIFO pressure nor retained firmware. `test_radio_reattach`
gained ten cases: the image options and their validation, withholding after
exit 4 and after a failed separate check, the check after an unfinished
download, a watchdog that owns the restore, the cap stopping a live run
before a single mount, and the watchdog's own firmware check. The probe's
own QNX code (image loading, the second bring-up, `-c`) has no workstation
test.

To run it once the unit is on and `en5` is up (owner's go-ahead required;
the access point is down until a restart afterwards, or until a full
power-off if the restore is withheld):

```sh
# copy mv8688-probe, radio-reattach.sh, sd8688_helper.bin and sd8688.bin
# to /fs/tmpfs over the wired path and check each cksum on the unit, then:
ssh -p 2222 root@<en5 address> \
  "/bin/ksh -c 'trap \"\" HUP; exec /fs/tmpfs/radio-reattach.sh \
   -P /fs/tmpfs/mv8688-probe -H /fs/tmpfs/sd8688_helper.bin \
   -S /fs/tmpfs/sd8688.bin' > /fs/tmpfs/radio-run.log 2>&1 &"
```

Pass for 4.2: `verdict=OK`, `ready: rc=0 fw_status=0xfedc`, a
`download: total_ms` of a few hundred milliseconds, and `post_teardown:`
with the ready magic absent, followed by a factory re-attach that takes the
usual 412–479 ms. If the station image fails, a second run with `-S
/lib/dll/FwImage/sd8688_ap.bin` separates the download code from the
image.

### Command layer probe (4.3), prepared offline

Offline, 2026-10-28, after the 4.2 hardware run. Built and tested on the
workstation; the result on the unit is in
[Phase 4.3 results](#phase-43-results). Source revision: branch
`wifi-sta-phase4-host-probe` on top of `c50fa66`.

[`wifi-sta/mv8688cmd.c`](../../wifi-sta/mv8688cmd.c) adds the command layer,
and `mv8688-probe -H <helper> -F <firmware> -C` runs it straight after a
download that reached the ready magic. The two references divide the work.
The factory driver decides how the FPGA host is driven. libertas decides
what the station firmware is told, because this image was written for it.

From the factory (`uap_dnld_cmd`, `mv8688uap_sdio_htoc`,
`mv8688uap_sdio_ctoh`, `mv8688uap_get_int_status`, `uap_get_mac_address`,
`mv8688uap_probe` in the saved decompilation):

- **Framing.** A 4-byte SDIO header `{length + 4, type 1}`, then the
  command with the 8-byte `{command, size, seqnum, result}` header
  libertas also uses. `uap_get_mac_address` builds the same layout
  (`0x00b0`, size 20). It is written as a block-mode CMD53 at the fixed
  I/O port. Every command here fits one 256-byte block. `mv8688uap_sdio_htoc`
  is handed the framed length, SDIO header included, and sends
  `(length + 259) / 256` blocks: two from 253 bytes. The probe rounds to
  whole blocks, which agrees only up to 252 bytes, so it refuses any frame
  longer than that.
- **A failed write** is ended by writing 4 to function-1 register 3 and
  tried again: three attempts in all.
- **Card to host.** The host interrupt status is function-1 `0x05`; bit 0
  means a packet is waiting and bit 1 means the last host packet was taken.
  The length is function-1 `0x42` shifted left by the RX unit (5 after the
  4.2 download, so 32-byte units). It is read in whole 256-byte blocks from
  the I/O port and has its own `{length, type}` header: type 1 is a command
  response, 3 an event, 0 a data frame. A response must carry the command
  with bit 15 set, and the factory rejects anything else.
- **No PicoBlaze.** The factory sends commands through
  `mv8688uap_pb_command` only once its packet engine is running (the flag
  at `0x6e8`). Before that they go through `uap_dnld_cmd`: written by
  `mv8688uap_sdio_htoc`, answered through `get_int_status` and `ctoh`, on
  the same PIO path. It polls or sleeps on the interrupt depending on the
  flag at `0x659`. So PIO is a path the factory itself uses for commands.

From libertas (v6.6 `if_sdio.c` and `host.h`, the same SHA-256 values as
above, fetched again on 2026-10-28):

- **Interrupt mask `0x0f`** at function-1 `0x04`, as
  `if_sdio_finish_power_on` writes before `CMD_FUNC_INIT`; the factory sets
  bit 0 (`sdio_set_ioreg`) for its uAP image, after its firmware
  initialization. The card's CCCR interrupt enable is on for function 1:
  the attach writes the master bit and the function bits, as the factory's
  common attach does. The FPGA host's card interrupt enable (`IEN` bit
  `0x100`) stays off, so the probe polls `0x05`, waiting 1 ms after each
  read that finds no packet. The `cmd:` mask line records both enables. The
  interrupt status is cleared by writing its complement, as
  `if_sdio_interrupt` does; the factory writes `~status & 3`. Both treat it
  as cleared by a 0, so a bit that was not set in the read is written as 1
  and survives.
- **`IO_RDY` (`0x08`) before every transfer**, both ways, for up to one
  second with 1 ms between reads (`if_sdio_wait_status`). The factory instead
  waits for `0x09` (adding `DL_RDY`), and only while its last packet has not
  been acknowledged. Whether the station image keeps `DL_RDY` set once it is
  running is not known, so the probe does not depend on it. It records the
  status byte it saw.
- **Time bounds are measured on the monotonic clock**, so they include the
  time each SDIO transaction takes. The read count is a second bound in
  case the clock stops: 1,000 `IO_RDY` reads, and as many interrupt polls
  as the timeout has milliseconds. The response timeout is 5 s from the
  end of the write.
- **Sequence numbers** count from 1. A response with another sequence
  number, a size field beyond the packet, or a non-zero result is a failure.
  The factory checks only the command.
- **The commands**, in libertas' order: `CMD_FUNC_INIT` (`0x00a9`, header
  only), `GET_HW_SPEC` (`0x0003`, 46 bytes, the MAC field set to the
  broadcast address as `lbs_update_hw_spec` does), `MAC_CONTROL`
  (`0x0028`, action `RX_ON | TX_ON` = `0x0003`, the value
  `lbs_init_adapter` sets), then `CMD_FUNC_SHUTDOWN` (`0x00aa`), which libertas
  sends before removal. Left out: libertas' TX power read and its
  802.11d enable. `MAC_CONTROL` turns the receive and transmit paths on,
  but nothing here asks the firmware to scan, probe or join; whether the
  station image transmits anything unprompted while unassociated is not
  known.

The probe allows `-C` only with the pinned station image. With the uAP
image it refuses before writing anything (exit 1), because that firmware
has its own command set. Each command is reported in one line: return
code, sequence number, response command, size and result, write attempts,
`IO_RDY` reads and status byte, interrupt polls, `0x42` value, transfer
size, packet length and milliseconds. The response bytes follow, as hex,
up to 64 bytes. `GET_HW_SPEC` is decoded into `hw_spec:` lines. Its
`fw_release` is printed both raw and as libertas prints it, which moves
the top byte (the patch level) to the bottom. The other fields are the
capability word, interface and hardware versions, region code, antennas,
TX descriptor and multicast counts, the queue pointers and the MAC. A
`cmd: summary` line gives every interrupt status bit seen, `DNLD`
acknowledgements, packets, events, data frames and stray responses. The
first eight event causes follow, one per line; later events are counted
only. The MAC is printed in full to
the private run log and slog on the unit. Records in this repository give
only its OUI.

`FUNC_SHUTDOWN` is sent whenever `FUNC_INIT` succeeded, even after a later
failure. The mask is written back to 0 after the commands, and also after
a failed mask write, since that write may still have reached the card.
The verdict is `FAIL` (exit 2) if any command fails, including the
shutdown, or if the mask cannot be cleared. After the commands, the
power-down and the second bring-up (`post_teardown:`) run as in 4.2, so the
restore rules are unchanged: exit 2 leads `radio-reattach.sh` to its
separate `probe -c` before any factory mount.

A command starts only if at least 30 s of the 90 s deadline remain;
otherwise it is reported as `NOT SENT` and counts as a failure. That
reserve covers one command whose transfers all stall just short of their
1 s host bounds, then the power-down and the second bring-up, which took
under a second in 4.2. A deadline or signal during the commands still
takes the emergency path of 4.1 and 4.2: the card is reset and powered
down without `FUNC_SHUTDOWN`, the mask write or the second bring-up, and
the probe exits 3, which `radio-reattach.sh` answers with its separate
`probe -c`.

`radio-reattach.sh -P <probe> -H <helper> -S <image> -C` passes `-C` to the
real run only (the two `-n` guard runs are unchanged), logs "with the
firmware download and commands", and reports
`RESULT: the station firmware answered its commands` on exit 0. `-C`
without both images is a usage error.

Known limits:

- The fake firmware in the tests answers as libertas expects; the real
  image's response sizes, timing and any events it sends unprompted are not
  known.
- The probe has never read data back over CMD53 on the unit. 4.1 and 4.2
  only wrote; every read so far was CMD52. The factory reads the same way
  (`sdio_read_iomem`: `DATA_CLR` and `READ` in `CTRL`, block size and count,
  then the command, then one FIFO word per access, single-block reads
  without the multi-block flag), and `hbsdc_transfer` follows it, but
  this is the first hardware test of that path.
- The probe reads every byte of a whole-block transfer from the FIFO. What
  the FPGA returns when the card supplies fewer bytes than requested is
  not known; the fake answers zeros. The factory reads whole blocks the
  same way.
- An upload the probe rejects (invalid length or header) is acknowledged
  but not drained. It fails the command; the power-down that follows
  clears it.
- Polling instead of the card interrupt is a choice for the probe. A driver
  would set the FPGA host's card interrupt enable, and that path is
  untested.

Tests: `test_wifi_sta_cmd` (28) compiles the four C layers and runs them
against a fake station firmware behind the fake FPGA and card. The tests
cover:

- the frame bytes, the CMD53 arguments both ways, matching on command,
  response bit, sequence number, size and result, and sequence numbers
  counting up;
- the mask and the complement write that clears the status;
- `GET_HW_SPEC` encoding and decoding, and a response too short to decode;
- events and data frames before the response, read without a wait between
  packets, and a stream of events that still times out;
- polls 1 ms apart, the response timeout on the clock (also when every
  transaction waits out the host's 10 ms interrupt slice, and across a
  clock wrap), an unknown packet type, invalid
  and oversized RX lengths, and a packet header longer than its transfer;
- a two-block response, an RX unit of 0, the `IO_RDY` wait with its
  1,000-read bound and its 1 s clock bound, a failed write terminated and
  retried, three failed writes, and a frame the factory would send in two
  blocks.

A mutation check (the response bit, the clear, the sequence check, the
no-wait-after-a-packet rule, the retry count, the RX-unit shift, then
after the review each clock bound, the poll-count bound and the 252-byte
limit) failed at least one test each. `test_radio_reattach` gained three cases: `-C` only on
the real run, `-C` without images, and a failed command run followed by the
separate firmware check before the factory mount. The probe's QNX `main`
(option checks, the station-only refusal, the report) has no workstation
test.

An adversarial review on 2026-10-28 (Codex, `gpt-6.1-sol`, read-only)
checked this code against `uap-trace.txt`, the instructions of the saved
driver and the libertas sources. It confirmed the framing, the CMD53
arguments both ways, the abort and retries, the factory read path
(`sdio_read_iomem` flags, `READ` direction, block size and count
registers, one FIFO word per access, the `DT_RUN` wait), the RX length and
unit, the mask, the status clear, the `IO_RDY` register and bit, the four
command layouts, the `GET_HW_SPEC` field offsets and the `fw_release`
rotation. It found that the time bounds counted reads rather than
milliseconds, that a failed mask-off still gave exit 0 and a failed mask
write skipped the mask-off, that this section wrongly said the CCCR
interrupt enable was off, the factory's rounding at 253–256 bytes, and an
unsupported time allowance. All of these were then fixed as described
above. Still open: what the FPGA FIFO returns on a short card transfer,
whether `MAC_CONTROL` makes the firmware transmit, and how the combo card
recovers after `FUNC_SHUTDOWN`. As before, that is agreement with saved
code, not hardware validation.

Build: `mv8688-probe`, 38,194 bytes, SHA-256 `f8b970d8…54748b02`, `cksum
1213016136 38194`, identical on a rebuild; `verify_elf.py` finds all 40
imports in the saved `libc.so.2`. `radio-reattach.sh` is `cksum 3524676784
29944`.

To run it once the unit is on and `en5` is up (owner's go-ahead required;
as in 4.2, a restart is needed afterwards for a configured access point):

```sh
# copy mv8688-probe, radio-reattach.sh, sd8688_helper.bin and sd8688.bin
# to /fs/tmpfs over the wired path and check each cksum on the unit, then:
ssh -p 2222 root@<en5 address> \
  "/bin/ksh -c 'trap \"\" HUP; exec /fs/tmpfs/radio-reattach.sh \
   -P /fs/tmpfs/mv8688-probe -H /fs/tmpfs/sd8688_helper.bin \
   -S /fs/tmpfs/sd8688.bin -C' > /fs/tmpfs/radio-run.log 2>&1 &"
```

Pass for 4.3: `verdict=OK`, `cmd: GET_HW_SPEC … rc=0 … resp=0x8003` with a
decoded `hw_spec: fw_release=…`, `FUNC_INIT`, `MAC_CONTROL` and
`FUNC_SHUTDOWN` with `rc=0`, and `post_teardown:` with the ready magic
absent, followed by the usual factory re-attach. A `FUNC_INIT` timeout
with `int_seen=0x00` would point first at the polling choice (a status
the card raises only as an interrupt, with the FPGA host's enable off)
before the command framing.

### Passive scan probe (4.4), prepared offline

Offline, 2026-10-28, after the 4.3 hardware run. Built and tested on the
workstation; the results on the unit are in
[Phase 4.4 results](#phase-44-results). Source revision: branch
`wifi-sta-phase4-host-probe` on top of `d64bad0`.

**The station's MAC.** The station firmware has no address of its own
([4.3](#phase-43-results) read the placeholder `aa:bb:cc:dd:ee:ff`). The
owner decided on 2026-10-28 that the station reuses the access point's
factory MAC: the address the factory stack programs into `uap0`, OUI
`00:1c:d7`. What follows from that:

- Only one firmware runs on the radio at a time, so the access point and
  the station never use the address at the same moment.
- A phone that has seen the car's access point sees the same address
  when the car joins it as a client.
- The repository does not hold the address. `radio-reattach.sh -L` reads
  it from `ifconfig uap0` before the detach and passes it to the probe as
  `-M`; it appears in full only in the private run log and slog on the
  unit.
- Where the factory stack takes it from is not established (see
  [Not established](#not-established)). Only the factory mount programs
  it: a `uap0` mounted by hand shows all zeros ([phase
  3b/3c](#what-the-unit-looks-like-afterwards)). The script therefore
  refuses, before anything is detached, a missing address, all zeros, a
  group address or the firmware's placeholder, and says to restart the
  unit first. The probe parses the address again and refuses the same set
  (exit 1, nothing written).

**The commands**, in [`wifi-sta/mv8688cmd.c`](../../wifi-sta/mv8688cmd.c)
and `mv8688-probe -H … -F … -C -M <mac> -L`:

- After `GET_HW_SPEC`, `802_11_MAC_ADDRESS` (`0x004d`, 16 bytes: action
  `SET` = 1, then the address), which libertas' `lbs_start_iface` sends
  before anything else is configured. A second `802_11_MAC_ADDRESS` with
  action `GET` (0) reads it back; libertas does not do this. A non-zero
  result to the read-back is reported, not failed; a read-back that
  differs from the address set fails the run. The echo in the `SET`
  response is reported only.
- `MAC_CONTROL` as in 4.3.
- `802_11_SCAN` (`0x0006`) for channels 1–11, in three commands of 4, 4
  and 3 channels (libertas' `LBS_SCAN_BEFORE_NAP`). Each body is what
  libertas' `lbs_scan_worker` builds when no SSID is given: BSS type any
  (3), a zero BSSID, a channel-list TLV (`0x0101`) of 7-byte entries
  `{radio type 0 (B/G), channel, mode 1 (passive), minimum 0, maximum
  dwell}`, and the rates TLV (`0x0001`) with libertas' twelve rates. Four
  channels make a 55-byte body and a 67-byte frame. Without an SSID TLV
  libertas scans passively, so no probe request is asked for.
- The dwell is **200 ms** per channel, against libertas' 100 ms: two
  beacon intervals at the usual 102.4 ms, so that one run is enough to
  hear the hotspot. About 2.2 s in all.
- Channels 12 and 13 are left out. This is a US unit, and the firmware's
  region code `0x73` is not one libertas knows; how the firmware treats
  channels 1–11 under it is part of what this run shows.

The response is decoded as libertas' `lbs_ret_scan` does: descriptor
bytes (le16) and set count (u8), the descriptors `{length, BSSID, RSSI,
8-byte timestamp, beacon interval, capabilities, information elements}`,
then a TSF TLV (`0x0113`) of 8 bytes per set. The probe checks the TLV
type and count, that each descriptor has at least its 19 fixed bytes and
fits the descriptor bytes, and that each element fits its descriptor; a
malformed response fails its group. Up to 32 descriptors per group are
listed, one `scan: bss` line each: BSSID, channel from the DS parameter
element, the raw RSSI byte with libertas' `3 − RSSI` dBm estimate, beacon
interval, capabilities (marked `privacy` when bit 4 is set) and the SSID,
with bytes outside printable ASCII written as `\xNN`. A group answered
with a non-zero result is reported and the next group still runs; a
transport failure ends the scan. Any scan failure fails the run (exit 2).
`FUNC_SHUTDOWN`, the power-down and the second bring-up follow as in 4.3,
so the restore rules are unchanged.

`radio-reattach.sh -L` needs `-C` (otherwise a usage error), reads the
address during the preflight (so `-n` checks it too), passes `-C -M
<mac> -L` to the real run only, and reports `RESULT: the station took the
MAC and completed a passive scan` on exit 0.

Pass for 4.4: `verdict=OK`; `MAC_ADDRESS set` with `rc=0` and a matching
(or refused) read-back; three `SCAN` lines with `rc=0`; and the owner's
hotspot among the `scan: bss` lines on its channel. The probe cannot know
the hotspot's name, so the operator reads the private log. The names and
BSSIDs of neighbouring networks stay in that log and in slog on the unit;
records here give counts only.

Known limits:

- Whether the firmware answers `802_11_MAC_ADDRESS` `GET`, accepts a
  200 ms dwell, or restricts channels 1–11 under region `0x73` is not
  known.
- A response longer than the probe's 4,096-byte receive buffer fails as an
  invalid length and ends the scan. How large the firmware lets a scan
  response grow was not checked.
- A long SSID full of escaped bytes can be cut short by the 256-character
  report line.
- Whether the firmware transmits anything during a passive scan is not
  observed from the air.

Tests: `test_wifi_sta_cmd` gained 12 cases (40 in all): address parsing
(both cases, eight malformed forms, and zero, placeholder, broadcast and
group addresses refused), the `802_11_MAC_ADDRESS` frame bytes and
response decoding, a short response, the scan body bytes for four
channels and its framed length, no channels and five channels refused,
two decoded descriptors (one with no DS element), an empty scan, a 40-byte
SSID cut to 32 with its length kept, descriptors beyond the listing limit
still checked, and seven malformed responses. `test_radio_reattach`
gained three: the address read from `uap0` and passed only to the real
run, five unusable addresses refused before any detach or probe run, and
`-L` without `-C`. The probe's `set_mac` and `scan` steps in its QNX `main`
have no workstation test. No adversarial review has been run on this
increment yet.

Build: `mv8688-probe`, 42,525 bytes, SHA-256 `241ada01…739c877e`, `cksum
3071007465 42525`, identical on a rebuild; `verify_elf.py` finds all 41
imports in the saved `libc.so.2`. It also carries the `cccr_ien` report
fix from [Phase 4.3 results](#phase-43-results), which the 4.4 runs
confirmed (`cccr_ien=0x03`). `radio-reattach.sh` is `cksum 2610472158 31676`.

To run it from a fresh start-up (the factory mount must have programmed
`uap0`'s address) with `en5` up; owner's go-ahead required, and a restart
is needed afterwards for a configured access point:

```sh
# copy mv8688-probe, radio-reattach.sh, sd8688_helper.bin and sd8688.bin
# to /fs/tmpfs over the wired path and check each cksum on the unit, then:
ssh -p 2222 root@<en5 address> \
  "/bin/ksh -c 'trap \"\" HUP; exec /fs/tmpfs/radio-reattach.sh \
   -P /fs/tmpfs/mv8688-probe -H /fs/tmpfs/sd8688_helper.bin \
   -S /fs/tmpfs/sd8688.bin -C -L' > /fs/tmpfs/radio-run.log 2>&1 &"
```

### PIO timing probe (4.5), prepared offline

Offline, 2026-10-28, after the 4.4 hardware run. Built and tested on the
workstation; nothing in this section has run on the unit. Source revision:
branch `wifi-sta-phase4-host-probe` on top of `442aeeb`.

**What is already known.** Every transfer so far has been PIO. The 4.2
download moved 259,172 bytes in 357 ms, about 726 kB/s, but that figure
includes the firmware's own pace: 983 requests, each needing status and
length reads before its CMD53. It does not say how long the CMD53 itself
takes, how that splits between the command and the data words, or how
fast reads are. Reads matter most, because received frames arrive that
way.

**What the probe measures.** With `-T`:

- **Every transfer is timed in three phases**, in
  [`wifi-sta/hbsdc.c`](../../wifi-sta/hbsdc.c). *Setup* covers the
  register setup and the command up to its completion, including the
  interrupt. *Data* is the PIO word loop alone. *Drain* is the wait for
  `DT_RUN` to clear and the error checks after it. Results are kept by
  direction and by size (1 to 8 blocks, and 9 or more). Data-less commands,
  which are almost all CMD52, are timed separately. Failures are counted,
  not timed. Timing is off unless both a counter and a statistics block are
  set, so the earlier runs' code path is unchanged.
- **Counter.** This is the SH `ClockCycles()` inline. It reads TMU1 through
  the timer page at `0x7bff8000`, which the factory's `dev-ipc`, `qdbc`,
  `MMI3GMisc` and `showtimes` read the same way (offline: 13 ELF files in
  the saved IFS carry both TMU1 register addresses). The probe also reads
  `TCOR1` there. Before the module is touched, it calibrates the counter
  against `CLOCK_MONOTONIC` over two 200 ms delays. It uses the counter
  only when `TCOR1` is `0xffffffff`, so a 32-bit difference survives a
  wrap, the rate is at least 1 MHz, and the two samples agree to within
  2%. Otherwise it reports `counter unusable` and the benchmark is timed in
  milliseconds only. The syspage `cycles_per_sec` is printed for
  comparison, not used.
- **Writes** are taken from the firmware download, timed separately from
  the rest of the run. These are real transfers of 1 and 2 blocks; the
  station image never asked for more in 4.2. No other write was added.
- **Reads come from a benchmark run once the ready magic is up and before
  `FUNC_INIT`.** First, 256 CMD52 reads of function-1 register `0x40`, the
  scratch register that holds the ready magic. Then 64 CMD53 reads each of
  1, 2, 4 and 8 blocks (8 blocks is the module's 2,048-byte FIFO) from the
  same register at a *fixed* address: block mode, op code 0, 256-byte
  blocks. That makes 960 blocks, 245,760 bytes. The buffer is filled with
  the complement of the expected byte before each read, and every byte is
  compared afterwards. A fixed-address register read writes nothing, and
  the download polls the same register by CMD52. A failed CMD53 is ended
  with the standard CCCR I/O abort (`0x06` ← 1). The scratch register is
  read again at the end and must still hold `0xfedc`.
- **The 4.3 commands still follow** (`FUNC_INIT`, `GET_HW_SPEC`,
  `MAC_CONTROL`, `FUNC_SHUTDOWN`), and they also run after a failed
  benchmark. Their answers show whether the firmware came through the
  reads intact. Their transfers are timed in a third block.

Report lines, in this order: `timing: tcor1=… rates=…` (the calibration);
`timing: download write blocks=N …`; the `bench:` lines, each with its
millisecond total and the count of bytes that differed; `timing: bench
read blocks=N …`; then `timing: commands …`. Each `timing:` line gives the
average setup, data and drain time in nanoseconds, the worst data and total
time in microseconds, the data-phase rate and the whole-transfer rate in
kB/s (1,000 bytes a second), and a `cmd` line with the average and worst
data-less command.

What to do with the numbers, after the run. A received full frame (1,514
bytes plus the 4-byte SDIO header and the RX descriptor) is 7 blocks, and
so is a transmitted one. With the measured costs:

- **TX per frame** ≈ one CMD52 (`IO_RDY`) + a 7-block write.
- **RX per frame** ≈ four CMD52s (status read, status clear, length,
  `IO_RDY`) + a 7-block read + the card interrupt's latency.

The comparison is the air link: 802.11g to a phone hotspot rarely carries
more than about 2–3 MB/s of TCP. If the projected PIO rate for full frames
is well above that, and the data phase (busy CPU time in the caller's
thread) is an acceptable share, DMA is not needed for phases 5 and 6.

`radio-reattach.sh -T` needs `-C` (otherwise a usage error). It passes `-T`
after the other options, to the real run only, and reports `RESULT: the PIO
timing and read benchmark completed and the firmware still answered` on
exit 0. The probe refuses `-T` without `-C`. A difference in any byte, a
failed read or a lost ready magic fails the run (exit 2); the power-down,
the second bring-up and the restore rules are as in 4.3.

Pass for 4.5: `verdict=OK`; a usable counter; `bench: all reads matched;
firmware status still 0xfedc`; the four commands answered afterwards; and a
`timing:` line for each read size and for the download's writes.

Known limits:

- Whether the SD8688 accepts a fixed-address multi-block CMD53 to a
  register, not the I/O port, is not known. Libertas reads registers only
  by CMD52. A refusal fails the benchmark and is ended by the CCCR abort;
  the commands that follow show whether the card recovered.
- A register read is not an I/O-port read. The card may answer from a
  different internal path than it uses for queued frames, so the read rate
  is the host's and the bus's, not necessarily the firmware's.
- The interrupt the probe waits on is the FPGA host's command completion,
  not the card's own interrupt (`IEN` `0x100`), which stays off. How fast a
  card interrupt reaches a driver, and so the RX latency per frame, is not
  measured.
- Only 1- and 2-block writes are timed. Larger writes are extrapolated
  from the per-block cost.
- The probe is an ordinary process at its default priority. Inside
  `io-pkt` a driver competes with other threads differently, and nothing
  here measures contention with the rest of the system.
- Nothing is transmitted or received over the air; this is the host side
  only.

Tests: `test_wifi_sta_host` gained five cases (31 in all). They cover a
fixed-address CMD53 read (argument op-code bit clear, one register
repeated) and transfers timed by direction and size, including the 9-block
bucket. Each register access advances the fake counter by one, so the
data phase is exactly one tick per word. Data-less commands are timed apart
from transfers. A failed transfer counts as a failure, and a CMD52 retry as
a failure plus a success. Nothing is timed without a counter or without
statistics. `test_radio_reattach` gained two: `-T` passed after `-C` and
after `-L`, and `-T` without `-C`. The calibration and the benchmark in the
probe's QNX `main` have no workstation test. No adversarial review has been
run on this increment yet.

Build: `mv8688-probe`, 59,871 bytes, SHA-256 `dfe68899…588e0ae7`, `cksum
2695180020 59871`, identical on a rebuild; `verify_elf.py` finds all 41
imports in the saved `libc.so.2`. The growth over 4.4 is mostly libgcc's
64-bit division, linked in statically. `radio-reattach.sh` is `cksum
3007782088 32450`.

To run it from a fresh start-up with `en5` up; owner's go-ahead required,
and a restart is needed afterwards for a configured access point. `-L` is
not needed, so the run does not depend on `uap0`'s address:

```sh
# copy mv8688-probe, radio-reattach.sh, sd8688_helper.bin and sd8688.bin
# to /fs/tmpfs over the wired path and check each cksum on the unit, then:
ssh -p 2222 root@<en5 address> \
  "/bin/ksh -c 'trap \"\" HUP; exec /fs/tmpfs/radio-reattach.sh \
   -P /fs/tmpfs/mv8688-probe -H /fs/tmpfs/sd8688_helper.bin \
   -S /fs/tmpfs/sd8688.bin -C -T' > /fs/tmpfs/radio-run.log 2>&1 &"
```

### Handling the station firmware

`sd8688.bin` (SHA-256 `f563f36bfd08192264945c4181c907607bb5234afbff377ca0d18d8db99e216e`)
is fetched from linux-firmware and must stay out of tracked files and SD
payloads, as [Firmware](#firmware) already requires. The private copy for
4.2 is in `work/firmware/`. Put it in RAM on the
unit and point the driver's `fwpath` at that directory, checking the hash
before use. The factory driver's hardcoded `<fwpath>/FwImage/sd8688_ap.bin`
name belongs to the uAP image; a station driver should use its own name so
the two can never be confused. Code derived from Linux `libertas` is
GPL-2.0 and anything released has to say so.

### Build and verification

`wifi-sta/build-stub.sh` already has the toolchain path: the SDK's SH
gcc 3.3.5, `-ml -m4 -fPIC -shared`, output to the private workspace, and
`openssh/verify_elf.py --shared` checking every import against the unit's
saved `libc.so.2`. Keep that check in the loop for every build; it is what
catches an import the unit cannot resolve before the object ever reaches the
car. The SDK has the io-net DDK (`sys/io-net.h`, `drvr/nicsupport.h`,
`drvr/mdi.h`) but no native `io-pkt` driver headers, which is why the driver
is an io-net one behind `devnp-shim`.

### Risks specific to this phase

- The driver runs inside the shared `io-pkt-v4-hc`. A crash takes MOST IP
  (`mam0`) and the `en5` management link with it until a restart. Phase 3
  measured that restart at about 40 seconds, fully restorative, which is what
  makes this an acceptable iteration loop.
- Do not drive the `WlanEnable` reset line unless something needs it. Phase 3c
  showed a detach clears the firmware on its own and the factory parameters
  re-attach unassisted, while a `resetmask=4` instance that fails leaves the
  line asserted and only another `resetmask=4` mount can clear it.
- Leave the FPGA SDIO module in a state the factory driver can recover from.
  Its attach does a full power-down and power-up, which is why phase 3's
  restores worked, but a new driver that stops halfway has not been tested
  against that assumption.
- `dev-irc` filters the Wi-Fi failure messages. Phase 3c produced two of them
  with no visible effect; a phase that produces many more may find out what
  the filter is for.

## Phase 3d, 4.0 and 4.1 results

Local hardware results, 2026-10-28 (afternoon), on the documented unit.
Source revision: branch `wifi-sta-phase4-host-probe` at `268bccf`; the
binaries are the builds recorded above (`mv8688-probe` `cksum 1422762852
23440`, stub `cksum 365366910 7027`, `radio-reattach.sh` `cksum 1017706870
23334`), each checked again on the unit after the copy. Everything ran from
`/fs/tmpfs` over the wired `en5` path and nothing persists. Evidence is in
the private `<private>/live-20261028-phase4/`.

### Full power-off cycle (3d)

The car had been off overnight, so this start-up followed a full power-off,
not a QNX restart. On the radio side it restored the factory state with no
other recovery step: `uap0` had its hardware address `00:1c:d7:xx:xx:xx` and
192.168.1.1, the owner's Pi was associated through it, and `mam0` was on
10.0.0.100. The slog had wrapped before it could be read, so that start-up's
attach time and `MICRO_AP_EV_BSS_ACTIVE` were not captured. Bluetooth and
phone calls were not checked in this session, so that half of 3d is still
open.

SSH did not come back after this power-off, although the radio did. The
OpenSSH start-up reported `ready` about a minute after boot, then
`jitter-egd` died without writing anything. The `en5` second `sshd` was
running its config test against it at that moment and logged `Couldn't read
from PRNGD socket: Connection reset by peer`. The main `sshd` logged
`Couldn't connect to PRNGD port 708: Connection refused`, and it had exited
too. Neither port 2222 answered for more than 15 minutes. See
[the OpenSSH note](https://github.com/chundla/qnx632-openssh/blob/main/docs/persistent.md#cold-boot-entropy-failure).
Access was recovered through the SD-card Telnet path, reaching the login
over `en5`, which the factory `pf` passes in full. Re-running the installed
`/mnt/persistence/openssh/start.sh` by hand then brought up both `sshd`s and
`jitter-egd` within 3 s.

### Stub load (4.0): passed

`mount -T io-pkt -o lan=9,probe_only=1 /fs/tmpfs/devn-mv8688sta-stub.so`
with the access point up:

```
devn-mv8688sta-stub: build check, options="lan=9,probe_only=1"; not attaching
shim: unable to init dll /fs/tmpfs/devn-mv8688sta-stub.so: No such device
Unable to init /fs/tmpfs/devn-mv8688sta-stub.so: Unknown error
```

`devnp-shim` accepts the SDK toolchain's output, calls `io_net_dll_entry`,
passes the mount options through, and unloads the object on `ENODEV`: `pidin
mem` on `io-pkt` afterwards listed only `devnp-mv8688uap.so` and
`devn-asix-772b.so`. `io-pkt` kept its PID (40981), and the interface list,
`uap0`, `mam0` and `en5` were unchanged. **`mount` exited 0 although the init
failed**, so a script cannot use its exit status to tell whether a driver
attached; check for the interface instead.

### Probe guard with the access point up: passed

`mv8688-probe -n` with the factory driver attached exited 1 with `verdict=REFUSED
the module is in use (a driver is attached)`. It read `CTRL=0x7007`,
`STAT=0x414`, `FLAG=0x00340000`, `IEN=0x1843`, power register `0x3`,
capabilities `0x4347`, system register 8 `0x302` (`WlanEnable` clear) and
`BtReset` clear. The running `CTRL` differs from the attach-time `0x7003` only
in `READ` (`0x4`), the direction of the last data transfer.

### Host bring-up (4.1): passed

`radio-reattach.sh -P /fs/tmpfs/mv8688-probe`, verdict `OK`. The script's
own `-n` guard check refused before the detach, as it must; the driver
detached and unloaded, `NWSProcess` did not re-mount it within 20 s, the
guard then passed, and the probe ran:

```
module: id=0x53 major=4 clock=99000000 irq=56 (index 0x38) caps=0x00004347 fifo=2048
card: power_raw=0x00000000 ocr=0x90ff8000 nfunc=1 rca=0x0001
cccr: rev=0x32 sd=0x02 ior=0x02 caps=0x03 bus_if=0x82 speed=0x01 ready_ms=0 width=4
cis: vendor=0x02df device=0x9103 cis0=0x08000 cis1=0x08080 fn1_code=0x00 manf="Marvell/802.11 SDIO ID: 10"
clock: bus=25000000 ctrl=0x00007003
fn1: chip_id=0x3130 (factory expects 0x3130)
fn1: ioport=0x10000
fn1: fw_status=0x0000 (ready magic absent)
fn1: card_status=0x0d io_ready=1 dl_ready=1 rx_unit=0
teardown: ctrl=0x00000000 power=0x00000000 ien=0x00000000
irq: mode=interrupt interrupts=80 polled=0 changes=0 last_resp_index=0x34
```

What this settles:

- **The module interrupt reaches a separate process.** Every command
  completed by interrupt 56 through `InterruptAttachEvent`; none fell back
  to polling. Interrupts alone are no reason to put the station driver
  inside `io-pkt` before the data path.
- **The recovered host layer is right on hardware**: identification, the
  CMD5/3/7 sequence, CCCR set-up, the 4-bit switch, the 25 MHz fast clock
  (the same `CTRL=0x7003` the factory driver logs), the CIS walk and CMD52
  through function 1 all worked first time.
- **The card is the expected part.** `02df:9103`, chip ID `0x3130` and I/O
  port `0x10000`. The OCR `0x90ff8000` is ready with a 2.7–3.6 V window. The
  CCCR reports SDIO revision 2.00 (`0x32`), `caps=0x03` (CMD52 during data
  and multi-block), and `speed=0x01` (high speed supported, not enabled).
- **The detach leaves no firmware running**, now read directly:
  `fw_status=0x0000`, with the card reporting `io_ready` and `dl_ready`,
  i.e. waiting for a helper download. This agrees with phase 3c.
- **The factory driver recovers from a module this code powered down.** The
  re-attach with the factory parameters downloaded firmware in **446 ms**
  (`_hbsdc_bus_speed` 00:24:22.862 to `pb_init` 00:24:23.308), within the
  412–479 ms range. As in phase 3c, the manually mounted `uap0` then had
  `00:00:00:00:00:00` and no BSS, and a restart was needed.

The owner's forced reboot then restored everything unaided: `uap0` with its
MAC and 192.168.1.1, a 413 ms attach, `MICRO_AP_EV_BSS_ACTIVE` about 30 s
later, `mam0` on 10.0.0.100, and both `sshd`s and `jitter-egd` running. This
time SSH started normally.

This is one run. The probe's interrupt path, its signal handling and its
deadline were not exercised by a failure, and 4.2 onwards still needs the
items listed under "Not carried over yet" in the section above.

## Phase 4.2 results

Local hardware results, 2026-10-28 (evening), on the documented unit. Both
runs used `radio-reattach.sh -P /fs/tmpfs/mv8688-probe -H
/fs/tmpfs/sd8688_helper.bin -S /fs/tmpfs/sd8688.bin` from `/fs/tmpfs` over
the wired `en5` path, each after a forced reboot, with every file's `cksum`
checked on the unit after the copy (`radio-reattach.sh` `cksum 3589660094
29228`, helper `1538620194 2616`, station image `251454034 259172`). Nothing
persists. Evidence is in the private `<private>/live-20261028-phase42/`.

### First run: the first request was odd

Probe `cksum 3331856334 30498` (source `3c0a158`). The detach, the guard and
the helper went as in 4.1: six helper chunks in 4 ms, at most 13 status
reads per ready wait. The first read of the request register then returned
a value with bit 0 set, at once (`zero_polls_max=0`). The probe treated it
as a resend request with nothing to resend and stopped:

```
host-probe: firmware: rc=-21 sent=0/259172 requests=1 req_min=0 req_max=0 crc_retries=1 ... FAILED at fw_crc
host-probe: post_teardown: fn1: fw_status=0x0000 (ready magic absent)
host-probe: verdict=FAIL
radio: the separate firmware check found no ready magic; the factory attach may run.
```

No firmware byte had been written, so the image was not involved. The
failure paths behaved as designed: the second bring-up and the separate
`probe -c` both found no ready magic, the factory restore ran and reached
`pb_init`, and the run ended `PARTIAL`. The raw value had not been logged.

The factory reads its first request only after `request_firmware` has
opened and read its 300 KB image, while the probe read it within
microseconds of the helper's end block. The probe now waits out odd and
zero values before the first chunk and records them; see the changed
bullet in
[Firmware download probe](#firmware-download-probe-42-prepared-offline).

### Second run: passed

Probe `cksum 1701426994 31485`, SHA-256 `b6ad584b…70d12fa7`, identical on a
rebuild; `verify_elf.py` passed against the saved `libc.so.2`. Verdict `OK`:

```
host-probe: helper: rc=0 chunks=6 ready_reads_max=13 rd_base_pre=0x0000 rd_base_pre_rc=0 ms=4
host-probe: request_trace: changes=2 0:0x0001 1:0x0010
host-probe: firmware: rc=0 sent=259172/259172 requests=983 req_min=16 req_max=512 crc_retries=0 aborts=0 abort_failures=0 first_polls=1 early_odd=1 zero_polls_max=0 ready_reads_max=13 last_request=0x0010 ms=357
host-probe: ready: rc=0 fw_status=0xfedc polls=5 rx_unit=5 rx_unit_rc=0 ms=52
host-probe: download: total_ms=413 (helper start to ready magic; the factory uAP window measured 412-479 ms)
host-probe: post_teardown: fn1: fw_status=0x0000 (ready magic absent)
host-probe: irq: mode=interrupt interrupts=6790 polled=0 changes=0 last_resp_index=0x34
host-probe: verdict=OK
radio: RESULT: the firmware reached its ready magic and was cleared again (exit 0).
```

What this settles:

- **The station image downloads and boots to its ready magic** through the
  recovered protocol, on this unit's FPGA host, by PIO. 413 ms from the
  helper's start to `0xfedc` is within the factory uAP image's 412–479 ms.
- **Right after the helper, the request register reads `0x0001`.** It
  read `0x0000` under the boot ROM, `0x0001` on the first read after the
  helper's end block, and `0x0010`, an even 16-byte request, 1 ms later.
  This was one observation, so it shows only that the helper presents an odd
  value briefly. It does not show what that value means or how long it lasts.
- **Requests were 16 to 512 bytes, two blocks at most.** The 2,048-byte
  FIFO limit listed under known limits was not reached by this image. The
  mean was 264 bytes over 983 requests.
- **No CRC resend and no aborted write** occurred in the 259,172 bytes.
- **The RX unit reads 5** once the station firmware runs; it was 0 before.
- **Power-down clears the station firmware too**: the second bring-up read
  `fw_status=0x0000` with `io_ready` and `dl_ready` set, so the factory
  attach downloaded its own image. It reached `pb_init` within the second
  of the probe's verdict (slog 00:03:39; millisecond stamps not captured).
- The interrupt path carried all 6,790 commands; none were polled.

Not shown: that the firmware does anything beyond writing the ready magic.
No command was sent, so its version, MAC and capabilities are still
unread (4.3). As before, the manually mounted `uap0` had no SSID, and a
restart was needed afterwards. That is two runs, one of each probe build.

## Phase 4.3 results

Run on 2026-10-28 evening, the car on, the unit freshly started, over
`en5`. Probe `cksum 1213016136 38194` (source `fd44919`),
`radio-reattach.sh` `cksum 3524676784 29944`, helper and station image as
in 4.2; every cksum was checked on the unit before the run. Verdict `OK`,
exit 0, in one run:

```
host-probe: download: total_ms=401 (helper start to ready magic; the factory uAP window measured 412-479 ms)
host-probe: cmd: host interrupt mask 0x0f; cccr_ien=0x00 host_ien=0x00001842; status polled; rx_unit=5
host-probe: cmd: FUNC_INIT 0x00a9 rc=0 seq=1 resp=0x80a9 size=8 resp_seq=1 result=0x0000 write_tries=1 aborts=0 ready_reads=1 io_status=0x0b polls=10 rx_len=1 rx_size=32 chunk=12 ms=28
host-probe: cmd: GET_HW_SPEC 0x0003 rc=0 seq=2 resp=0x8003 size=46 resp_seq=2 result=0x0000 write_tries=1 aborts=0 ready_reads=1 io_status=0x0b polls=2 rx_len=2 rx_size=64 chunk=50 ms=4
host-probe: cmd: response len=46 03802e00020000000200210008004000aabbccddeeff7300010001260a1900000000000000000000000003030000
host-probe: hw_spec: fw_release=10.38.1p25 raw=0x190a2601 capinfo=0x00000303 hwif=0x0002 version=0x0021
host-probe: hw_spec: region=0x0073 antennas=1 nr_txpd=8 nr_mcast=64 wcb_base=0x00000000 rxpd_rdptr=0x00000000 rxpd_wrptr=0x00000000
host-probe: hw_spec: mac=aa:bb:cc:dd:ee:ff
host-probe: cmd: MAC_CONTROL 0x0028 rc=0 seq=3 resp=0x8028 size=12 resp_seq=3 result=0x0000 write_tries=1 aborts=0 ready_reads=1 io_status=0x0b polls=2 rx_len=1 rx_size=32 chunk=16 ms=4
host-probe: cmd: FUNC_SHUTDOWN 0x00aa rc=0 seq=4 resp=0x80aa size=8 resp_seq=4 result=0x0000 write_tries=1 aborts=0 ready_reads=1 io_status=0x0b polls=2 rx_len=1 rx_size=32 chunk=12 ms=3
host-probe: cmd: summary int_seen=0x03 dnld_acks=4 packets=4 events=0 data=0 stray=0 aborts=0 abort_failures=0 mask_off_rc=0
host-probe: post_teardown: ready magic absent after the power-down (fw_status=0x0000); a factory attach will download its own image
host-probe: irq: mode=interrupt interrupts=6842 polled=0 changes=0 last_resp_index=0x34
host-probe: verdict=OK
radio: RESULT: the station firmware answered its commands and was cleared again (exit 0).
radio: restore: attach reached mv8688uap_pb_init.
```

The rest of the bring-up and the download matched 4.2 (983 requests of
16–512 bytes, no CRC resend or abort, the odd `0x0001` request once after
the helper). The factory attach reached `pb_init` within a second of the
probe's verdict. As before, the manually mounted `uap0` had no MAC or SSID
afterwards, and a restart is needed.

What this settles:

- **The station firmware runs and answers commands** on this unit's FPGA
  host, by PIO. All four commands were answered on the first write with
  matching command, sequence number and a zero result, in 3 to 28 ms.
- **CMD53 reads work.** These were the first on the unit: one- and
  two-unit packets (32 and 64 bytes at RX unit 5), each read as one
  256-byte block, with lengths and headers consistent.
- **Polling the status register works.** `0x05` showed `UPLD` and `DNLD`
  (`int_seen=0x03`) with the FPGA host's card interrupt enable off
  (`host_ien` `0x1842`, bit `0x100` clear). `IO_RDY` was always set at the
  first read; the status byte was `0x0b`, so `DL_RDY` stays set while the
  firmware runs.
- **Firmware identity:** release 10.38.1.p25 in libertas' notation (raw
  `0x190a2601`), interface version 2, hardware version `0x21`, capability
  word `0x303`, one antenna, 8 TX descriptors, 64 multicast entries. It is
  the linux-firmware image pinned in [Firmware](#firmware), as expected.
- **The card has no MAC of its own.** `GET_HW_SPEC` returned
  `aa:bb:cc:dd:ee:ff`, a placeholder. The factory driver programs the MAC
  itself (`uap_set_mac_address`), so a station driver has to set one
  before it scans or joins.
- **The region code is `0x73`**, which is not one of the six libertas
  knows (`0x10`, `0x20`, `0x30`, `0x31`, `0x32`, `0x40`); libertas would
  fall back to `0x10` (US). Which channels this firmware scans with that
  code is open for 4.4.
- **No events or data frames arrived**, including after `MAC_CONTROL`
  turned receive and transmit on. Whether anything was transmitted was not
  observed from the air.
- **Power-down still clears the firmware** after `FUNC_SHUTDOWN`
  (`fw_status=0x0000` at the second bring-up), and the factory restore
  downloaded its own image.

One report field was wrong: `cccr_ien=0x00`. The attach writes and reads
back CCCR `0x04`, but that register was not in the set copied into the
card record, so the probe printed an entry that was never filled. The
attach now keeps its read-back; the test asserts it. Probe after the fix:
38,194 bytes, SHA-256 `f1fe0d9c…a70901e8`, `cksum 2110216681 38194`,
identical on a rebuild, 40 imports resolved. It was not run itself; the
4.4 build carries the same fix and read `cccr_ien=0x03` on the unit.

Not shown: that the firmware can scan. That is 4.4, which also needs the
MAC to be set first. Evidence: private run log, slog and interface state
under `<private>/live-20261028-phase43/`.

## Phase 4.4 results

Two runs on 2026-10-28 evening, the car on, each from a fresh start-up,
over `en5`. Probe `cksum 3071007465 42525`, `radio-reattach.sh` `cksum
2610472158 31676`, helper and station image as in 4.2; every cksum was
checked on the unit, and `-n` passed (the address read from `uap0`)
before each run. Both runs: verdict `OK`, exit 0.

The first run (unit uptime 17 min) had the owner's hotspot switched off,
which only became clear afterwards. The second (uptime 3 min, after a
forced restart) had it on. The second run, with the address shown here
by its OUI only:

```
host-probe: download: total_ms=397 (helper start to ready magic; the factory uAP window measured 412-479 ms)
host-probe: cmd: MAC_ADDRESS set 0x004d rc=0 seq=3 resp=0x804d size=16 resp_seq=3 result=0x0000 write_tries=1 aborts=0 ready_reads=1 io_status=0x0b polls=2 rx_len=1 rx_size=32 chunk=20 ms=3
host-probe: mac: set response echoes 00:1c:d7:… (as sent)
host-probe: cmd: MAC_ADDRESS get 0x004d rc=0 seq=4 resp=0x804d size=16 resp_seq=4 result=0x0000 write_tries=1 aborts=0 ready_reads=1 io_status=0x0b polls=2 rx_len=1 rx_size=32 chunk=20 ms=3
host-probe: mac: read-back 00:1c:d7:… matches
host-probe: cmd: SCAN ch1-4 0x0006 rc=0 seq=6 resp=0x8006 size=881 … rx_len=28 rx_size=896 chunk=885 ms=812
host-probe: scan: SCAN ch1-4 sets=3 descriptor_bytes=832
host-probe: cmd: SCAN ch5-8 0x0006 rc=0 seq=7 resp=0x8006 size=1106 … rx_len=35 rx_size=1120 chunk=1110 ms=813
host-probe: scan: SCAN ch5-8 sets=4 descriptor_bytes=1047
host-probe: cmd: SCAN ch9-11 0x0006 rc=0 seq=8 resp=0x8006 size=559 … rx_len=18 rx_size=576 chunk=563 ms=617
host-probe: scan: SCAN ch9-11 sets=2 descriptor_bytes=520
host-probe: scan: complete, 9 descriptors in all
host-probe: cmd: summary int_seen=0x03 dnld_acks=9 packets=9 events=0 data=0 stray=0 aborts=0 abort_failures=0 mask_off_rc=0
host-probe: post_teardown: ready magic absent after the power-down (fw_status=0x0000); a factory attach will download its own image
host-probe: verdict=OK
radio: RESULT: the station took the MAC and completed a passive scan (exit 0); read the scan: lines for the hotspot.
radio: restore: attach reached mv8688uap_pb_init.
```

`FUNC_INIT`, `GET_HW_SPEC` (the same release, capabilities, region and
placeholder MAC as in 4.3), `MAC_CONTROL` and `FUNC_SHUTDOWN` answered as
in 4.3, every command on its first write. The factory attach reached
`pb_init` afterwards, `mam0` and `en5` were untouched, and `WlanEnable` and
`BtReset` stayed `0x00000000`. As before, the manually mounted `uap0` had
no MAC or SSID, and a restart was needed.

What this settles:

- **The station can scan, and it hears the owner's hotspot.** The
  hotspot was the strongest network heard: channel 6, raw RSSI 39
  (about −36 dBm by libertas' estimate), privacy set. It was identified
  by its name. Gate 4.4 is met.
- **The station takes the access point's factory address.**
  `802_11_MAC_ADDRESS` `SET` was accepted and echoed. The firmware also
  answers `GET`, and the read-back matched, in both runs.
- **The scan command works as libertas builds it.** All three groups
  answered with result 0 in both runs. The groups took 812–839 ms for four
  channels and 616–617 ms for three, which fits the requested 200 ms
  dwell plus some overhead per channel. So the firmware accepts that
  dwell and does not refuse channels 1–11 under region `0x73`.
- **Every response decoded cleanly**: TSF TLV type and count, descriptor
  lengths and elements. The largest was 1,110 bytes (1,120 at RX unit 5),
  well inside the probe's 4,096-byte buffer.
- **Nine networks in the second run**: three on channel 1, four on
  channel 6, two on channel 9, from −36 to −87 dBm. Five carry an SSID
  and four are hidden. Interval 100 TU except for one at 31 TU. Their
  names and BSSIDs stay in the private log.
- **Still no events or data frames** after `MAC_CONTROL` or during the
  scans (`events=0 data=0`).
- **The first run heard only channel 1**: the same three networks as the
  second, at the same strength. Nothing on channels 5–11, including
  networks the second run heard at about −73 dBm. Why is not established
  (see [Not established](#not-established)).

Not shown: anything about transmitting. The scan was passive, nothing was
joined, and nothing was observed from the air. Evidence: private run
logs, reports and slog under `<private>/live-20261028-phase44/`
(hotspot off) and `<private>/live-20261028-phase44b/` (hotspot on).

## Phase 4.5 results

One run on 2026-10-28 evening (host clock about 00:40 UTC on 2026-10-29),
the car on, from a fresh start-up (unit uptime 15 min, `uap0` holding its
factory address), over `en5`. Probe `cksum 2695180020 59871`,
`radio-reattach.sh` `cksum 3007782088 32450`, helper and station image as
in 4.2; every cksum was checked on the unit. Command
`radio-reattach.sh -P … -H … -S … -C -T`. The hotspot was not needed and
nothing was sent over the air. Verdict `FAIL` (probe exit 2), script
verdict `PARTIAL`, for one reason only: the benchmark's register reads
returned the wrong bytes (below). The timing was captured in full.

```
host-probe: timing: tcor1=0xffffffff rates=11011438,10975405 Hz over 203,202 ms syspage_cps=0x00a7d8c000000000
host-probe: download: total_ms=400 (helper start to ready magic; the factory uAP window measured 412-479 ms)
host-probe: timing: download write blocks=1 n=493 bytes=126208 setup_ns=31969 data_ns=49139 drain_ns=7335 max_us=1711/1745 data_kBps=5209 kBps=2894
host-probe: timing: download write blocks=2 n=497 bytes=254464 setup_ns=30609 data_ns=109222 drain_ns=8604 max_us=6319/6354 data_kBps=4687 kBps=3449
host-probe: timing: download cmd n=5472 avg_ns=32864 max_us=11701 failures=0
host-probe: bench: 256 CMD52 reads of fn1 0x40 in 7 ms, 0 differ from 0xdc
host-probe: bench: 64 CMD53 reads of 1 blocks (256 bytes) in 7 ms, 16384 bytes differ
host-probe: bench: 64 CMD53 reads of 2 blocks (512 bytes) in 15 ms, 32768 bytes differ
host-probe: bench: 64 CMD53 reads of 4 blocks (1024 bytes) in 30 ms, 65536 bytes differ
host-probe: bench: 64 CMD53 reads of 8 blocks (2048 bytes) in 66 ms, 131072 bytes differ
host-probe: bench: DATA DIFFERED; firmware status still 0xfedc
host-probe: timing: bench read blocks=1 n=64 bytes=16384 setup_ns=28782 data_ns=74378 drain_ns=5045 max_us=509/541 data_kBps=3441 kBps=2365
host-probe: timing: bench read blocks=2 n=64 bytes=32768 setup_ns=39908 data_ns=171211 drain_ns=5015 max_us=1832/1866 data_kBps=2990 kBps=2368
host-probe: timing: bench read blocks=4 n=64 bytes=65536 setup_ns=29995 data_ns=428841 drain_ns=6145 max_us=5748/5780 data_kBps=2387 kBps=2202
host-probe: timing: bench read blocks=8 n=64 bytes=131072 setup_ns=176322 data_ns=794732 drain_ns=5135 max_us=9438/9472 data_kBps=2576 kBps=2097
host-probe: timing: bench cmd n=259 avg_ns=25237 max_us=167 failures=0
host-probe: timing: commands write blocks=1 n=4 bytes=1024 setup_ns=57534 data_ns=41251 drain_ns=37067 max_us=42/198 data_kBps=6205 kBps=1884
host-probe: timing: commands read blocks=1 n=4 bytes=1024 setup_ns=61286 data_ns=67745 drain_ns=5503 max_us=68/234 data_kBps=3778 kBps=1902
host-probe: irq: mode=interrupt interrupts=7172 polled=0 changes=0 last_resp_index=0x34
host-probe: verdict=FAIL
```

`FUNC_INIT`, `GET_HW_SPEC` (the same release, capabilities, region and
placeholder MAC as in 4.3), `MAC_CONTROL` and `FUNC_SHUTDOWN` all answered
after the benchmark, each on its first write. The power-down cleared the
ready magic, the factory attach reached `pb_init`, and `mam0` and `en5`
were untouched. `WlanEnable` and `BtReset` stayed `0x00000000`. As before,
the manually mounted `uap0` had no MAC or SSID, and a restart was needed.

What this settles:

- **The counter works.** `TCOR1` read `0xffffffff`, and the two samples
  gave 11.01 and 10.98 MHz, matching the syspage's 11,000,000 cycles a
  second. One tick is about 91 ns.
- **Writes cost about 48–55 µs per 256-byte block in the data phase**, or
  0.77–0.85 µs per 32-bit word, plus about 31 µs of setup (command and
  interrupt) and 7–9 µs of drain per transfer. A 2-block write took
  148 µs on average, 3.4 MB/s.
- **Reads are slower: about 67–86 µs per block**, 1.2–1.7 µs per word,
  with the cost per block rising from 1 to 4 blocks. An 8-block read
  (2,048 bytes, a full FIFO) took 976 µs on average, 2.1 MB/s, or 841 µs
  without the single worst one. The read-to-write gap fits PCI reads
  waiting for each word while writes are posted. That explanation is
  inferred and was not measured.
- **The register benchmark represents real port reads.** The 4.3 command
  responses, read from the I/O port, spent 67.7 µs per block in the data
  phase. The benchmark's 1-block register reads spent 67.5 µs (both
  figures without the worst sample).
- **CMD52 costs 25–33 µs** on average, nearly all of it the command and
  its interrupt.
- **Worst cases reach milliseconds**: up to 9.4 ms for one 8-block read
  and 11.7 ms for one CMD52 during the download. They are almost all in
  the data phase or the command wait. The probe ran at default priority,
  so preemption is the likely cause; it was not identified.
- **Fixed-address CMD53 to a register completes but returns other data.**
  Every byte of all 960 blocks differed from `0xdc`, yet no transfer
  failed, no data error was flagged, no abort was needed, and the scratch
  register still held `0xfedc` afterwards. What the card returned was not
  recorded. This only affected the benchmark's data check.

Per-frame projection from these figures, for a full 1,514-byte frame (7
blocks on the bus):

| | Cost per frame | Rate | CPU busy in the PIO loop |
|---|---|---|---|
| TX: 1 CMD52 + 7-block write | about 400–450 µs | about 3.4–3.7 MB/s | about 340–380 µs |
| RX: 4 CMD52 + 7-block read | about 840–990 µs | about 1.5–1.8 MB/s | about 500–600 µs |

The RX figure leaves out the card interrupt's latency, which was not
measured, so the real ceiling is lower still.

**Outcome for the DMA question.** TX by PIO is comfortably above an
802.11g hotspot link. RX by PIO is not. Its ceiling of about 1.5–1.8 MB/s
is below the 2–3 MB/s such a link can carry. At that rate the PIO loop
alone would keep the CPU busy for over half the time, and the loop sits in
the networking path. By the rule set before the run, DMA therefore stays
open. PIO is still fast enough to make and test a working station:
association, DHCP, WPA2 and ordinary traffic. Phases 5 and 6 can proceed
on PIO, and DMA becomes a throughput improvement measured against real
traffic in phase 6, not a gate before it.

Not shown: card interrupt latency, contention inside `io-pkt`, writes
larger than 2 blocks (the 7-block figure is extrapolated), and what a
fixed-address CMD53 to the scratch register returns. Evidence: private run
log under `<private>/live-20261028-phase45/`.

## Phase 5 plan

Offline planning, 2026-10-29, after the 4.5 run. Nothing in this section
has run on the unit. Source revision: branch `wifi-sta-phase4-host-probe`
on top of `64489ec`.

Phase 5 is "join an open network and get an address". It still needs no
`wpa_supplicant`, but it is the first phase in which the radio transmits:
authentication, association and, later, data frames, all from the access
point's factory MAC. It is split the way phase 4 was. Nothing goes into
`io-pkt` until the association and the frame formats have worked from the
standalone probe.

**The bench access point** (owner decision, 2026-10-29): the Pi, not the
phone. An iPhone hotspot always requires a password, and the Pi gives an
access-point-side record that the phone cannot:
[`wifi-sta/bench-open-ap.sh`](../../wifi-sta/bench-open-ap.sh), run on the
Pi as root, starts `hostapd` with an open 802.11g network (`mmi-bench`,
channel 1 by default, country `US`, no WMM), `dnsmasq` serving
`10.42.88.50–99/24` with **no router and no DNS** (options 3 and 6
empty, so a later `dhcp.client` cannot install a default route or rewrite
`resolv.conf` from the bench), and `tcpdump` on the interface. `hostapd`'s
log records the station's authentication, association and
deauthentication; the capture shows data frames only. `stop` ends all
three, moves the logs, leases and capture to `/var/tmp/mmi-bench-ap-<UTC
time>`, clears the interface address and hands the interface back to
whichever manager `start` took it from. For NetworkManager that is
`nmcli device set … managed no/yes`. For netplan it is a runtime
`/run/systemd/network/05-mmi-bench-ap.network` with `Unmanaged=yes`
(world-readable, since `systemd-networkd` does not run as root), a
`networkctl reload`, and stopping `netplan-wpa-<interface>.service`; `stop`
reverses all three. A system-wide `dhcpcd` manager, if one is running, is
told `dhcpcd -x <interface>` first and `dhcpcd -n <interface>` after `stop`
(see [the 5.0 results](#phase-50-results) for why). Any other
`wpa_supplicant` on the interface makes `start` refuse rather than guess
how to restore it.
Taking the interface ends the Pi's own client link, which is no loss
during a run: the car's access point is down then anyway, and the
management path is `en5`. The subnet avoids `uap0` (192.168.1/24), `mam0`
(10.0.0/24), the `en5` LAN (192.168.16/20) and the AX88772 default
(172.16/16).

**Bench check on the Pi, 2026-10-29** (local hardware, no head unit
involved): Raspberry Pi 4 Model B Rev 1.4, Ubuntu 24.04.5, kernel
`6.8.0-1065-raspi`, `brcmfmac`. It is reached over `eth0`; `wlan0` is its
netplan client link to the car's access point. `hostapd` 2.10 and
`dnsmasq-base` 2.91 were installed from Ubuntu for this; `hostapd.service`
stays masked, and `dnsmasq-base` has no service. AdGuard Home holds port
53 there, which `--port=0` leaves alone. Script `cksum 1597476200 9465`
(as used for 5.0; the `dhcpcd` handover came after).
Two `start`/`status`/`stop` cycles: `hostapd` reached `AP-ENABLED`, `iw`
showed `wlan0` as type AP with `mmi-bench` on channel 1 (2,412 MHz),
`dnsmasq` listened on `wlan0:67`, and `tcpdump` ran. The first cycle
showed the override file unread by `networkd` (`configuring`, created mode
600); after the fix the second showed `unmanaged` with
`netplan-wpa-wlan0` stopped. After each `stop`, the override and the state
directory were gone, `netplan-wpa-wlan0` was active again, `wlan0` was back
in managed mode (no carrier, as before: the car was off), and the logs and
capture were in `/var/tmp`. No station joined, so this shows the network
comes up and goes away cleanly, not that a station can associate or get a
lease. That `networkd` re-reaches `configured` once the car's network is
in range again was not seen.

### Increments

5.0 **Open association, from the probe.** Scan for the bench SSID, join
it the way libertas joins an open network, stay associated for 15 s
while recording what the firmware passes up, deauthenticate. No data
frame is sent. Pass: `ASSOCIATE` status 0 with an AID, no link-lost,
deauthentication or disassociation event during the hold, and `hostapd`
logging the association and the deauthentication for the factory MAC. See
[Open association probe](#open-association-probe-50-prepared-offline).
Passed on the unit on 2026-10-29 at the third run; see
[Phase 5.0 results](#phase-50-results).

5.1 **Data frames, from the probe.** After association, send frames by
the libertas `txpd` path and read replies by `rxpd`: an ARP request for
the Pi's address and ICMP echoes up to 1,472 bytes of payload, which is
also the first 7-block write (4.5 timed 2 at most). Pass: the Pi's
capture shows the frames with the right source and checksums, and the
replies decode. Built and tested offline; see
[Data frame probe](#data-frame-probe-51-prepared-offline). Passed on the
unit on 2026-10-29 at the first run; see
[Phase 5.1 results](#phase-51-results).

5.2 **The io-net driver.** `devn-mv8688sta.so` behind `devnp-shim`,
reusing `hbsdc.c`, `sdio.c`, `mv8688fw.c` and `mv8688cmd.c`, with
association driven by a control ioctl from a small tool. This is the
first code inside the shared `io-pkt-v4-hc`, so a fault there takes `mam0`
and `en5` with it until a restart. `pf` blocks for the new interface go in
before it comes up. Pass: a static address on the bench subnet answers
ping from the Pi, `mam0` and `en5` are untouched, and a detach of the new
interface followed by a restart restores the access point. Built and
tested offline; see
[Station driver](#station-driver-52-prepared-offline).

5.3 **DHCP and routes.** The factory `dhcp.client -b -T` on the new
interface, started with `&` because it never daemonizes, against the
bench `dnsmasq`. Check the lease, that no default route and no
`resolv.conf` change came from the bench, and that `mam0` routes survive.
Then restore. Built and tested offline; see
[DHCP on the station](#dhcp-on-the-station-53-prepared-offline). Passed
on the unit at the fifth run; see [Phase 5.3 results](#phase-53-results).

### Open association probe (5.0), prepared offline

Built and tested on the workstation, 2026-10-29. In
[`wifi-sta/mv8688cmd.c`](../../wifi-sta/mv8688cmd.c) and `mv8688-probe -H …
-F … -C -M <mac> -L -A <ssid>`.

After the first two hardware runs the probe changed twice, as recorded in
[Phase 5.0 results](#phase-50-results): `ASSOCIATE` also accepts the
`0x8012` response code, and with `-A` the scans are libertas' active SSID
scan (40 ms per channel, up to three). The description below is the
version first prepared; the build figures at its end are updated.

**Which BSS.** The 4.4 scan runs as before, and every descriptor whose SSID
equals the `-A` argument byte for byte is counted. The one with the
lowest RSSI byte (the strongest) is kept. If none was heard, one more full
scan runs, since 4.4's first run showed that a single 200 ms dwell can miss
a network. The scan parser now also keeps the supported-rates and
extended-rates elements (supported first, whatever order they came in;
an element over libertas' 14 rates is skipped, as libertas does) and
notes any RSN element or WPA vendor element (`00:50:f2` type 1). The probe
refuses to join a BSS without the ESS capability bit, with the privacy
bit, with an RSN or WPA element, or on a channel outside 1–11.

**The sequence** is libertas' `lbs_cfg_connect` for cipher 0, in its
order, with `MAC_CONTROL` (RX and TX on, WEP off) already sent before the
scan:

| Command | Body | Notes |
|---|---|---|
| `802_11_SET_WEP` (`0x0013`) | action `REMOVE` (4), key 0; 72 bytes | `lbs_remove_wep_keys`. A non-zero result is reported, not failed: nothing was ever set |
| `802_11_KEY_MATERIAL` (`0x005e`) ×2 | action `SET`, key TLV `0x0100` of 38 bytes: type WEP, info unicast (2), then group (1), length 0 | Clears both keys. Refusal tolerated as above |
| `802_11_ENABLE_RSN` (`0x002f`) | `SET`, 0 | Required |
| `802_11_AUTHENTICATE` (`0x0011`) | BSSID, auth type 0 (open), 10 reserved | Stores the auth type; libertas notes it sends nothing over the air |
| `802_11_RADIO_CONTROL` (`0x001c`) | `SET`, 1 (radio on; no preamble on firmware v9 or later) | Required |
| `802_11_ASSOCIATE` (`0x0050`) | BSSID, the scan's capability, listen interval 10, beacon period and DTIM 0, then TLVs: SSID (`0x0000`), DS (`0x0003`, the channel), empty CF (`0x0004`, 6 bytes), rates (`0x0001`), auth type (`0x011f`, 2 bytes, 0) | Required; 5 s timeout, as libertas |
| `802_11_DEAUTHENTICATE` (`0x0024`) | BSSID, reason 3 (leaving) | Sent after any association attempt, as the firmware's state is unknown when a response is lost; refused after a failed association, not an error |

The rates TLV is libertas' `add_ie_rates`: for each of its twelve rates in
table order (1, 2, 5.5, 11, then 6–54 Mb/s), every byte of the BSS's
element with that rate, basic bit kept, first for the supported-rates
element and then for the extended one. A BSS without a supported-rates
element gets the 802.11b fallback `82 84 8b 96`. The largest body, a
32-byte SSID with 14 rates, is 88 bytes, a 100-byte frame: one block.

The response is decoded as `{capability, status, AID}` after the header.
Libertas treats a firmware v9+ status as the 802.11 code; the probe only
reports it, and passes on status 0. During the hold, `mv8688_wait` reads
every packet the card announces without sending a command, in five 3 s
slices, each logged with the running counts. A command response arriving
there counts as stray. Event causes are the low byte, as libertas'
`if_sdio_handle_event`; link lost (2, 3), deauthenticated (8) and
disassociated (9) are counted as `disconnects`, and any of them during the
hold fails the run. Data frames are decoded as libertas' `rxpd`: status,
SNR, length, noise floor, rate and the offset of the 802.3 header, then
destination, source and the LLC/SNAP ethertype. The first eight are listed
(`data:` lines, private log only). Libertas ignores the descriptor's own
length field, so the probe takes the frame length from the packet and
reports both; a descriptor whose offset or 802.3 header does not fit the
packet is counted as `data_bad`. The hold is cut short if less than the
30 s command reserve plus a slice would remain before the 90 s deadline;
the whole run, including two scans, should take about 30 s.

`radio-reattach.sh -A <ssid>` needs `-L` (the MAC is read from `uap0`
as in 4.4), accepts only letters, digits, `.`, `_` and `-` up to 32
characters, so the SSID reaches the probe as one plain word, and reports
`RESULT: the station associated with '<ssid>' …` on exit 0.

Pass for 5.0: `verdict=OK`; `ASSOCIATE` `rc=0` with `status=0x0000` and a
non-zero AID; five `join: held` lines with `link_ends=0`; `DEAUTHENTICATE`
`rc=0`; and, in the Pi's `hostapd.log`, the station (by its factory MAC)
authenticated, associated and then deauthenticated, at times that match.

Known limits:

- Whether this firmware accepts each of these commands is not known
  until the run. The bodies follow libertas v6.6, which was written for
  this image family; that this exact image (10.38.1.p25) takes them is
  unverified. A refused `SET_WEP` or `KEY_MATERIAL` is tolerated.
- Region code `0x73` is still unknown. Channel 1 is used because it is
  legal in the US and 4.4 heard networks there; what the firmware's
  transmit power is under that code was not read.
- The AID's top two bits, which 802.11 sets in the frame, may or may not
  be stripped by the firmware; the probe only requires it non-zero.
- What data the firmware passes up while associated, and whether the
  `rxpd` length matches the packet, is what this run will show. Nothing
  from it is used yet.
- The probe's `join` step in its QNX `main` has no workstation test; the
  command bodies, response and descriptor decoders and the wait loop do.

Tests: `test_wifi_sta_cmd` gained 15 cases (55 in all): rates kept in
order with the RSN and WPA elements noted and another vendor element not,
an over-long rates element skipped, the `ASSOCIATE` body bytes against
libertas' layout, rate ordering and an unknown rate dropped, the 11b
fallback, no body without an SSID or channel, the largest body framed in
one block, response decoding with a success and a refusal, a short
response, the bytes of the other five bodies, `rxpd` decoding with and
without LLC/SNAP and at another offset, four descriptors that do not fit,
the wait recording events, disconnects, frames, a bad descriptor and a
stray response without writing anything, the wait bounded by the clock,
and a card failure during the wait. `test_radio_reattach` gained two:
the SSID passed after the MAC and scan, and six refused forms (spaces, a
semicolon, a glob, 33 characters, empty, no `-L`). `test_bench_open_ap`
has three. No adversarial review has been run on this increment yet.

Build as first prepared: `mv8688-probe`, 67,473 bytes, SHA-256
`3a58489f…7ed280`, `cksum 4016092601 67473`. As it passed in run 3: SHA-256
`54dba037…a29ed471`,
`cksum 2141926964 67697`, identical on a rebuild; `verify_elf.py` finds
all 42 imports in the saved `libc.so.2` for both. 331 tests. `radio-reattach.sh` is `cksum 4148981172
33602`; `bench-open-ap.sh` is `cksum 1597476200 9465`.

To run it: on the Pi, start the bench network and watch its log; then on
the unit, from a fresh start-up with `en5` up (owner's go-ahead required;
a restart is needed afterwards for a configured access point):

```sh
# on the Pi (root@<pi>; the script is in /root/mmi-bench)
./bench-open-ap.sh start                      # mmi-bench, channel 1
# copy mv8688-probe, radio-reattach.sh, sd8688_helper.bin and sd8688.bin
# to /fs/tmpfs over the wired path and check each cksum on the unit, then:
ssh -p 2222 root@<en5 address> \
  "/bin/ksh -c 'trap \"\" HUP; exec /fs/tmpfs/radio-reattach.sh \
   -P /fs/tmpfs/mv8688-probe -H /fs/tmpfs/sd8688_helper.bin \
   -S /fs/tmpfs/sd8688.bin -C -L -A mmi-bench' > /fs/tmpfs/radio-run.log 2>&1 &"
# on the Pi, during and after the run
./bench-open-ap.sh status
./bench-open-ap.sh stop
```

### Data frame probe (5.1), prepared offline

Built and tested on the workstation, 2026-10-29. Nothing here has run on
the unit. Source revision: branch `wifi-sta-phase4-host-probe` on top of
`4f4b13c`. `mv8688-probe -H … -F … -C -M <mac> -L -A <ssid> -I <station
address> -E <peer address>`; `radio-reattach.sh … -C -L -A mmi-bench -I
10.42.88.10 -E 10.42.88.1`. The station address is outside the bench
`dnsmasq` pool (`.50–.99`); the peer is the Pi's bench address.

Everything before the association is 5.0 unchanged. With `-I` and `-E`,
the 15 s silent hold is replaced by data frames; without them the probe
behaves as in 5.0.

**Transmit**, in [`wifi-sta/mv8688cmd.c`](../../wifi-sta/mv8688cmd.c)
`mv8688_send_data`, follows libertas' `lbs_hard_start_xmit` and
`if_sdio_host_to_card` for a station: the SDIO header `{length, type 0}`,
then a 24-byte `txpd` in which only the packet offset (24), the packet
length and the destination address are filled in (status, control,
priority, power and delay stay zero, as libertas leaves them), then the
Ethernet II frame as the network stack would hand it over, 14–1,514
bytes. The firmware does the 802.11 and LLC/SNAP conversion. The write
goes the same way as a command (`IO_RDY` wait, block-mode CMD53 to the
I/O port, abort and retry three times). Libertas sends nothing more after
a data write until the card's `DNLD` interrupt (`dnld_sent`); the probe
polls the status for it, up to 100 ms, reading whatever the card
announces with it, and fails the frame with `tx_done` if it does not come.
Every 5.0 command was followed by one `DNLD` (`dnld_acks` equalled the
command count in each run), but that a data write raises one too is
libertas' assumption, not yet seen here.

**Receive.** Each data frame that decodes (as in 5.0, by `pkt_ptr`) is
handed to a callback. With LLC/SNAP, the two addresses move up against the
SNAP ethertype, leaving an Ethernet II frame 8 bytes further on, as
libertas' `lbs_process_rxed_packet` does; without it, the 802.3 frame is
passed on unchanged, as libertas does too. The same callback is what the
io-net driver (5.2) will use; it can end a wait early.

**The bench traffic** is probe-only, in
[`wifi-sta/benchnet.c`](../../wifi-sta/benchnet.c); in 5.2 `io-pkt` does
this. Addresses are parsed strictly (four decimal parts, no leading zeros,
not `0/8`, loopback, multicast or broadcast; station and peer must
differ). The sequence:

1. An ARP request, broadcast, "who has the peer, tell the station", from
   the factory MAC; up to three, 1 s apart, until a reply from the peer's
   address to the station's MAC and address arrives (a group sender address
   is refused).
2. ICMP echo requests to the peer's MAC, with payloads of 56, 248, 504,
   760, 1,016, 1,272 and 1,472 bytes. With 70 bytes of SDIO header, `txpd`
   and Ethernet, IPv4 and ICMP headers, each write takes one block more: 1
   to 7 blocks, 126 to 1,542 bytes. The IPv4 header is 20 bytes, TTL 64,
   don't-fragment, identification equal to the sequence; ICMP identifier
   is the probe's process ID, sequence 1–14, payload byte *i* =
   (sequence + *i*) mod 256. All seven sizes run twice, each waiting up
   to 1 s for its reply.
3. A reply counts only if every check passes: the station's MAC as
   destination and the peer's learned MAC as source, IPv4 header checksum,
   no fragmentation, peer to station addresses, ICMP type 0 code 0,
   ICMP checksum, the awaited sequence, the payload length and every
   payload byte. IP options and trailing padding are allowed. A sound
   reply to an earlier sequence, or a duplicate, counts as late, not bad.
   Anything else with the probe's identifier is `echo_bad`, with the check
   it failed.
4. ARP requests for the station's address are answered at any point.
5. Then 3 s more of receiving, for late replies and link events, and
   `DEAUTHENTICATE` as in 5.0.

Every frame sent is logged (`tx:` with its first 64 bytes, block count,
write tries, `IO_RDY` reads and `DNLD` polls); every frame received is
logged (`rx:` with its kind, length, SNR, noise floor and rate); each echo
has an `echo:` line, and a `bench:` line sums up.

Pass for 5.1: `verdict=OK`; an `arp: … is at` line with the Pi's MAC
(OUI `dc:a6:32`); `sizes_answered=7/7`, `echo_bad=0`, `link_ends=0`;
`DEAUTHENTICATE` `rc=0`; and in the Pi's capture (`tcpdump -r … -n -e
-vv 'arp or icmp'`), from the factory MAC, the ARP request, the Pi's reply,
and the echo requests with no `bad cksum`, Ethernet lengths 98 to 1,514,
each followed by the Pi's reply. A lost frame (one of the two rounds of a
size unanswered) is reported but does not fail the run: the subject is the
frame format, not the air.

Known limits:

- The `txpd` with zero control and priority is what libertas sends; what
  rate this firmware then picks was not read, and the `rxpd` rate index is
  reported only.
- The first data writes of more than 2 blocks, and the first reads of the
  I/O port of more than 5 blocks, happen in this run.
- `MAC_CONTROL` is still RX and TX only; multicast is not enabled, which
  ARP and unicast ICMP do not need. IPv6 neighbour discovery from the Pi
  will not be passed up.
- The probe's exchange in its QNX `main` has no workstation test; the
  transmit path, the receive callback and the bench frames do.
- No throughput is measured; 4.5's projection stands.

Tests: `test_wifi_sta_cmd` gained 8 (66 in all): the `txpd` and framing
bytes against libertas' layout, the 1–7 block writes for the seven echo
frames, lengths outside 14–1,514 refused unsent, a missing `DNLD`
failing with `tx_done`, the `DNLD` wait reading what arrives with it and
leaving the rest, a failed data write retried and then failed as
`tx_write`, SNAP frames handed on as Ethernet II and others unchanged, and
a callback ending a wait. The new `test_wifi_sta_bench` has 14, against
frames built independently in Python from RFC 826, 791 and 792: address
parsing, the RFC 1071 checksum example, the ARP request and reply, ARP
traffic for others ignored, echo requests at 0–1,472 bytes, the awaited
reply accepted with options or padding, late and duplicate replies, eight
corrupted replies each failing its own check, and other traffic left
alone. `test_radio_reattach` gained two: both addresses passed after the
SSID, and eight refused forms (one address only, two words, three or five
parts, a command substitution, empty, no `-A`). 355 tests.

Build: `mv8688-probe` 76,187 bytes, SHA-256 `0f952111…0f52c565`,
`cksum 3411235905 76187`, identical on a rebuild; `verify_elf.py` finds all
44 imports in the saved `libc.so.2`. `radio-reattach.sh` is
`cksum 4264391060 35072`; `bench-open-ap.sh` is unchanged
(`cksum 2187135882 10336`), and the Pi's copy matches it. Read-only check
on the Pi, 2026-10-29: `INPUT` policy accept with only Docker's rules,
`icmp_echo_ignore_all` 0, `arp_ignore` 0, `rp_filter` 2 (loose).
No adversarial review has been run on this increment yet.

To run it, as for 5.0 (owner's go-ahead required; a fresh start-up before,
a restart after):

```sh
# on the Pi (root@<pi>; the script is in /root/mmi-bench)
./bench-open-ap.sh start                      # mmi-bench, channel 1
# copy mv8688-probe, radio-reattach.sh, sd8688_helper.bin and sd8688.bin
# to /fs/tmpfs over the wired path and check each cksum on the unit, then:
ssh -p 2222 root@<en5 address> \
  "/bin/ksh -c 'trap \"\" HUP; exec /fs/tmpfs/radio-reattach.sh \
   -P /fs/tmpfs/mv8688-probe -H /fs/tmpfs/sd8688_helper.bin \
   -S /fs/tmpfs/sd8688.bin -C -L -A mmi-bench -I 10.42.88.10 -E 10.42.88.1' \
   > /fs/tmpfs/radio-run.log 2>&1 &"
# on the Pi, after the run
./bench-open-ap.sh stop
tcpdump -r /var/tmp/mmi-bench-ap-<time>/capture.pcap -n -e -vv 'arp or icmp'
```

### Station driver (5.2), prepared offline

Built and tested on the workstation, 2026-10-29. Source revision: branch
`wifi-sta-phase4-host-probe` on top of `666b5fd`, uncommitted at the time
of writing. This subsection was written before the hardware run; for what
happened on the unit, see [Phase 5.2 results](#phase-52-results).

#### How `devnp-shim` hosts an io-net driver

Offline analysis of the saved HN+R IFS objects, decompiled with
[`tools/ghidra/IoNetTrace.java`](../../tools/ghidra/IoNetTrace.java)
(pinned to both hashes): `devnp-shim.so` (SHA-256 `00af614e…cfbb5d8b`,
36,962 bytes) and, as a working example, `devn-asix.so` (`085f2658…a3df241a`).
The traces stay private under `<private>/offline-analysis/io-net/`. Struct
offsets used below were checked against the SDK's `sys/io-net.h` and
`hw/nicinfo.h` with compile-time asserts in the SDK compiler. Nothing in
this subsection was observed on hardware.

- **Loading.** io-pkt routes a `devn-*.so` mount to the shim, which strips
  its own option (`shimrxcopy`, default 1) and calls the driver's
  `io_net_dll_entry` `init` on the stack thread (or on a "shim_entry"
  thread it then waits for). The `dispatch_t` passed in is created for the
  call and destroyed after it, so a driver cannot attach a resource
  manager to it. A non-zero return makes the shim unload the object, as
  4.0 saw.
- **Registration.** `ex_reg` accepts only top type `en` and registrant
  type `_REG_PRODUCER_UP`; it ignores the cell, endpoint and
  `_REG_ENDPOINT_ARG`. Inside the call, `shim_attach` asks the driver's
  `devctl` for `DCMD_IO_NET_GET_CONFIG` (0x2a0 bytes) and takes the
  current address, or the permanent one if that is zero, for
  `ether_ifattach`. The driver must know its MAC before it registers. The
  shim's own `nic_parse_options` also reads the mount options; its result
  is overwritten by that devctl.
- **Callbacks the shim makes.** `rx_down` once per outgoing frame, from
  `if_start` on the stack thread, only while `IFF_RUNNING`; `tx_done` for
  the driver's own up packets; `devctl`; `flush` on `ifconfig down`;
  `shutdown1` and `shutdown2` on detach and at io-pkt shutdown (with
  inconsistent arguments in the shutdown hook, so a driver should not
  trust them). `dl_advert` is never called. `ifconfig up` sends the driver
  nothing except a `DCMD_IO_NET_PROMISCUOUS` when that flag changes.
- **Frames up.** `tx_up` drops data packets while the interface is not
  running. With `shimrxcopy` the stack copies only the first iov of the
  first buffer, up to the frame length and the 2,048-byte cluster, then
  calls the driver's `tx_done` at once; an up packet must be one
  contiguous buffer. `alloc_up_npkt(size)` returns a packet with an empty
  buffer list and `size` bytes behind it, freed with `free`.
  `devn-asix.so` does the same: one iov, chained packets, `tx_up_start`.
- **Frames down.** Each npkt carries the mbuf chain as iovs (up to 44). A
  `tx_done` from a thread the stack does not track is queued and freed
  later by the shim, so a driver thread may hand packets back. If
  `rx_down` returns −1, the shim counts an output error and frees nothing;
  the driver must have handed the packet back itself (`devn-asix.so` sets
  `_NPKT_NOT_TXED` and does).
- **Link state.** Only `SIOCGIFMEDIA` asks for it, through
  `GET_CONFIG`'s `NIC_FLAG_LINK_DOWN`; nothing is pushed to the stack.
- **Control path.** The shim attaches `/dev/io-net/en<N>` (mode 0666). A
  `devctl` on it is passed to the driver's `devctl` unchanged, with the
  caller's `_cred_info` in the `ret_cred` argument; for the shim's own
  calls that pointer is to a zeroed buffer.
- **Removal.** `ifconfig en<N> destroy` (`SIOCIFDESTROY`) reaches
  `shim_detach`, which needs root and `shimrxcopy` set. It runs the
  interface stop, `shutdown1`, `shutdown2`, `if_detach` and then unloads
  the object. No driver thread may outlive `shutdown2`. An up packet still
  queued for the stack at that moment would later be handed back to an
  unloaded object.

#### The driver

[`wifi-sta/devn-mv8688sta.c`](../../wifi-sta/devn-mv8688sta.c), mounted as

```sh
mount -T io-pkt -o lan=6,mac=<uap0 MAC>,helper=<file>,firmware=<file> devn-mv8688sta.so
```

Any other option is refused. `init` reads both images and refuses
anything but the pinned helper and station image (the pin table moved from
the probe to [`mv8688fw.c`](../../wifi-sta/mv8688fw.c)). It maps the FPGA
as the probe does and makes the same checks: no driver holds the SDIO
module, and `WlanEnable` is clear. The reset line is never written.
Then it starts one card thread and waits for its bring-up. That thread
does the 4.1–4.3 steps unchanged: interrupt 56, card attach, 25 MHz,
256-byte blocks, chip ID `0x3130`, no ready magic yet, then the helper and
firmware download. The station set-up follows, in
[`wifi-sta/mv8688sta.c`](../../wifi-sta/mv8688sta.c): `FUNC_INIT`,
`GET_HW_SPEC`, `MAC_ADDRESS` set and read back, and `MAC_CONTROL` with RX
and TX. Only then does `init` register `en6`. The stack thread is blocked
for this, about half a second judging by 4.2, as it is during the factory
attach. Nothing is joined at mount.

The card thread owns every card access. Each turn it runs a pending
connect or disconnect request, sends one queued frame (at most 32 queued,
accepted only while associated), or reads the card's interrupt status
once. When nothing came it sleeps 1 ms unless a frame or request arrives.
This is the probe's polling; the FPGA card-interrupt path is still
untested. Received frames go up as Ethernet II, as in 5.1, each in its own
single-iov packet. A link-lost, deauthenticated or disassociated event
moves it back to idle; the command layer now keeps the last such cause,
since its event list holds only the first eight. After 20 failed transfers
in a row the thread stops touching the card and the interface reports
`FAILED` until it is destroyed.

Connect is the 5.0 sequence: up to three active SSID scans of channels
1–11, the strongest BSS with the SSID, and refusal of anything but an
open infrastructure BSS on 1–11. Then come `SET_WEP` and both
`KEY_MATERIAL` clears (refusals tolerated), `ENABLE_RSN` off,
`AUTHENTICATE` open, `RADIO_CONTROL` on and `ASSOCIATE`. A failed
association is followed by `DEAUTHENTICATE`.
On `shutdown2` the thread deauthenticates, sends `FUNC_SHUTDOWN`, masks
the card's interrupts, powers the card down and exits. Then queued frames
go back to the stack, the FPGA is unmapped, and the state is freed. If up
packets are still with the stack, the state is kept and a warning logged
instead.

The device's `GET_CONFIG` reports the MAC as both addresses, 802.11 media,
an unknown rate and `NIC_FLAG_LINK_DOWN` unless associated. `GET_STATS`
counts the frames. Promiscuous mode and multicast filters are refused
(`ENOTSUP`); `MAC_CONTROL` has neither.

**Control.** [`wifi-sta/mv8688sta-ctl.h`](../../wifi-sta/mv8688sta-ctl.h)
defines three devctls on `/dev/io-net/en6`: status, connect and
disconnect. Connect and disconnect need root, as the node itself is open
to anyone, and they only queue the request and return its number; status
shows when it is done. [`wifi-sta/mv8688-ctl.c`](../../wifi-sta/mv8688-ctl.c)
is the tool: `mv8688-ctl [-i en6] [-w s] status | connect <ssid> |
disconnect`. It waits for the request and exits 0 only when it succeeded
(associated, for a connect).

#### The run

`radio-reattach.sh -D <driver> -K <tool> -P <probe> -H … -S … -A <ssid>
-I <address> -E <peer> [-W <hold>]`. The probe only checks the module here.

1. **Preflight.** It touches nothing: `uap0`'s factory MAC is read and
   checked as for 4.4, `en6` must not exist, and every file's cksum is
   logged. The probe guard must refuse while the factory driver is
   attached. `-n` stops here.
2. **Detach.** `uap0` is detached, the re-mount watch runs, and then the
   probe guard must pass on the idle module.
3. **Filter rules.** Two rules labelled `mmi_sta_en6` go in before
   `pass out`, filter rules only, as the persistent SSH rule went in.
   They allow echo requests from the peer to the station address in on
   `en6`, and block everything else in on it. Without them the
   interface would fall through to `pf`'s default pass (see
   [Hazards](#hazards-specific-to-this-unit)). The run checks that both
   rules loaded, and restores the previous set otherwise; without the
   rules, the driver is not mounted.
4. **Station run.** The driver is mounted and `en6` gets the station
   address `/24`. `mv8688-ctl connect` follows, then three pings of the
   peer each at 56 and 1,472 bytes. Next is a hold of `-W` seconds
   (default 60, at most 600), logged every 10 s, in which the Pi pings the
   station; the link must still be associated, with no link loss. Then:
   disconnect, a 2 s pause so that no received frame is still with the
   stack, and `ifconfig en6 destroy`.
5. **Checks.** The probe guard must pass again, the firmware check must
   find no ready magic, and the rules come out. The factory driver is
   then re-attached as after 4.x. A restart is still needed for a
   configured access point.

If `en6` will not go, the factory attach is withheld, the rules stay, and
the report says to restart: io-pkt starts afresh then. If firmware still
answers, the attach is withheld and the report says to power off, as in
host mode. The watchdog first disconnects and destroys `en6`. If `en6`
stays, it does not mount the factory driver. Otherwise it removes the
rules, runs the firmware check, and mounts.

Pass for 5.2: the run's `RESULT:` line. In the Pi's capture, echo requests
to `10.42.88.10` sent during the hold are answered by the factory MAC
(OUI `00:1c:d7`), and `hostapd` logs one association and one
disassociation. `mam0` and `en5` stay up throughout, and no `mmi_sta_en6`
rule remains afterwards. A restart then restores the access point.

Known limits and risks:

- **This is the first code of ours inside the shared io-pkt.** A fault in
  it takes `mam0` and `en5` down with io-pkt until a forced restart. SSH
  and the Telnet card both need `en5`, so the owner's restart is then the
  only recovery. The probe ran as its own process for exactly this
  reason. Its fault paths have no unit test here: the QNX glue (threads,
  io-net calls, interrupt attach in io-pkt) builds and links, but no
  workstation test runs it.
- Teardown still has a window. A frame that reaches the stack in the last
  moments before `destroy` would come back to the unloaded object; the
  disconnect and pause are meant to close it, and nothing proves they do.
- Polling costs one `CMD52` (25–33 µs in 4.5) per millisecond while idle,
  about 3 % of the CPU. The thread runs at the stack thread's priority. RX
  throughput is still bounded as 4.5 projected.
- Whether the networking service, `ifwatchd` or anything else reacts to a
  new `en6` (for instance by starting `dhcp.client`, as happens on `en5`)
  was not established. `libnetworkingservice.so` holds `dhcp.client -b %s
  -i %s`. The bench `dnsmasq` gives no router or DNS, so a lease from it
  cannot change routes or `resolv.conf`.
- Broadcast frames reached the probe in 5.0 and 5.1. That the Pi's
  broadcast ARP for the station is passed up is assumed. The station's own
  pings make the Pi learn its MAC first anyway.

Tests:

- `test_wifi_sta_station`, new, has 26 against the fake module and
  firmware. They cover:
  - set-up order and the `MAC_CONTROL` bits;
  - a refused MAC read-back tolerated, a different one failed, and stop
    still shutting down;
  - the connect scan, active for the SSID, choosing the strongest BSS,
    retried up to three times; refusals per group and of every group;
  - protected and off-channel BSSs refused before authentication;
  - a refused association followed by deauthentication, and none after a
    failure before `ASSOCIATE`;
  - link ends from events, also after the event record is full;
  - frames only while associated;
  - disconnect and stop.
- `test_radio_reattach` gained 13 for `-D`. They cover:
  - the whole order, and the inserted rules and their placement;
  - rules that will not load keeping the driver out;
  - a failed connect, unanswered pings, a lost link and a driver that does
    not attach, each still restoring;
  - a stuck `en6`, and firmware left behind, each withholding the factory
    attach;
  - option checks, preflight refusals and the dry run;
  - the watchdog destroying `en6` and removing the rules before its
    firmware check and mount, and staying out when `en6` will not go.
  
  Its fake `sleep` now scales with the argument in these tests, so the
  watchdog's ticks keep their real ratio to the run's 1 s waits.
- 394 tests in all.

Build ([`wifi-sta/build-driver.sh`](../../wifi-sta/build-driver.sh)):

| File | SHA-256 | `cksum` | Imports |
|---|---|---|---|
| `devn-mv8688sta.so` | `97cec234…2fd0eeef` | `4051650589 58772` | 48 |
| `mv8688-ctl` | `4da98e67…e51c09ec` | `3782954773 9157` | 19 |
| `mv8688-probe`, rebuilt for the pin table | `27e23094…2cf330b2` | `3930888157 76287` | |
| `radio-reattach.sh` | | `883843081 47629` | |

`verify_elf.py` finds every import of the driver and the tool in the saved
`libc.so.2`, the driver's `io_net_dll_entry` is exported, and both are
SH-4 subsets. The probe rebuilt cleanly. No adversarial review has been
run on this increment yet.

To run it (owner's go-ahead required; a fresh start-up before, a restart
after):

```sh
# on the Pi (root@<pi>; the script is in /root/mmi-bench)
./bench-open-ap.sh start                      # mmi-bench, channel 1
# copy devn-mv8688sta.so, mv8688-ctl, mv8688-probe, radio-reattach.sh,
# sd8688_helper.bin and sd8688.bin to /fs/tmpfs over the wired path and
# check each cksum on the unit, then:
ssh -p 2222 root@<en5 address> \
  "/bin/ksh -c 'trap \"\" HUP; exec /fs/tmpfs/radio-reattach.sh \
   -D /fs/tmpfs/devn-mv8688sta.so -K /fs/tmpfs/mv8688-ctl \
   -P /fs/tmpfs/mv8688-probe -H /fs/tmpfs/sd8688_helper.bin \
   -S /fs/tmpfs/sd8688.bin -A mmi-bench -I 10.42.88.10 -E 10.42.88.1' \
   > /fs/tmpfs/radio-run.log 2>&1 &"
# on the Pi, once the log says "hold: 60s associated"
ping -c 20 10.42.88.10; ping -c 10 -s 1472 10.42.88.10
# on the Pi, after the run
./bench-open-ap.sh stop
tcpdump -r /var/tmp/mmi-bench-ap-<time>/capture.pcap -n -e -vv 'arp or icmp'
```

### DHCP on the station (5.3), prepared offline

Offline work, 2026-10-29, after the 5.2 run. Nothing in this section has
run on the unit. Source revision: branch `wifi-sta-phase4-host-probe` on
top of `ea575fe`. The driver, control tool and probe are the 5.2 builds,
unchanged.

5.3 is the 5.2 run with the station address taken by the factory DHCP
client instead of set by hand: `radio-reattach.sh -D … -A <ssid> -d -E
<peer>`, where `-d` replaces `-I`.

#### What the factory DHCP path does

Read from saved files only. `dhcp.client` is the saved IFS copy of
2026-10-20 (`cksum 263280565 57048`, SHA-256 `270a970b…f1abee90`, the
same as the live October 23 capture). `/etc/dhcp/dhcp-up` is the live
copy read on 2026-10-23 (`cksum 3046797907 6292`, the size `ls` gave).

- **`dhcp.client`** (QNX 6.3.2 `632-1624`, 2009) has no BPF code. It
  sends and receives over a UDP socket bound to port 68 with
  `SO_BINDTODEVICE`, `SO_BROADCAST` and `SO_REUSEPORT`, so `pf` sees its
  traffic. Its usage text says:
  - `-u` keeps it in the foreground until the interface is configured,
    and then it goes to the background as a daemon;
  - `-T <s>` ends it with exit 3 if no server ACKs in time, each time
    it goes back to `DISCOVER`;
  - `-b` asks the server to broadcast its replies;
  - `-n` leaves out the server's name servers.

  The factory runs `dhcp.client -b -i en5`, and `libnetworkingservice.so`
  has `dhcp.client -b %s -i %s`. There is no foreground-only mode. The
  binary holds strings for deleting the default route, unconfiguring the
  interface and restoring the saved resolver data. When it does each of
  these was not decoded.
- **After every lease it runs `/etc/dhcp/dhcp-up`.** The path is fixed in
  the binary, and the script is written for `en5`. On this unit
  `/HBpersistence/DLinkReplacesPPP` and `/mnt/efs-system/RSE` were absent
  (live capture of 2026-10-23), so it takes the MU branch without D-Link:
  1. It waits up to 6 s for `/dev/shmem/networkscript.semaphore`, and
     removes it.
  2. `route add -net $IPADDRESS/16 $GATEWAY`, and
     `route add 10.184.26.1/24 $GATEWAY`. These two produce the
     `192.168/16` and `10.184.26/24` routes via the `en5` gateway that
     every capture since `en5` came up shows.
  3. It starts `ifwatchd -D /etc/dhcp/dhcp-down en5` unless
     `/tmp/ifwatchd_dhcp_down` exists.
  4. `route delete default`.
  5. It re-adds a `ppp0` default route if `/tmp/ppp0_defaultroute`
     exists.
  6. It replaces a present `/etc/resolv.conf` with an empty file. The
     line is `touch rm /etc/resolv.conf`, so it also creates a file
     named `rm` in its working directory.
  7. It touches the semaphore again.
- **For the bench.** The bench lease has no router and no DNS, so
  `$GATEWAY` is empty and both `route add` lines should fail. The unit has
  no default route to delete: none of the seven saved routing tables from
  2026-10-23 to 2026-10-28 shows one, consistent with `en5`'s own lease
  running the same script. `/etc/resolv.conf` was absent in the
  2026-10-27 capture. What QNX `route` does with an empty gateway was not
  checked; the run records it.
- **For phase 6 this is a finding, not a bench detail.** A phone hotspot's
  lease carries a router, and `dhcp.client` would install it as the
  default route. The factory `dhcp-up` would then delete it and empty
  `resolv.conf`. A working hotspot client needs its own after-lease
  handling, or the route and DNS put back after `dhcp-up`. Nothing is
  changed for that here.

#### The run

The 5.2 run, with these differences:

1. **Preflight.** Driver mode with `-d` takes no `-I`; `-d` outside
   driver mode is a usage error. `dhcp.client` and `dhcp-up` must match
   the cksums above. The run refuses if `DLinkReplacesPPP` or `RSE`
   exists, because `dhcp-up` would then rewrite `pf` anchors or point the
   default route at `mam0`, and those branches were not reviewed. It
   logs whether `/tmp/ppp0_defaultroute` and `/tmp/ifwatchd_dhcp_down`
   exist. `pidin -f aA` must work and list no `dhcp.client … -i en6`.
   The `dhcp.client` lines it does list go into the report, so a `-n`
   dry run shows whether the format matches what the stop step parses.
   The resolver (`resolv.conf` and `getconf _CS_RESOLVE`) is recorded.
2. **Filter rules.** There are three rules instead of two: UDP from the
   peer's port 67 to port 68 (no state matches a reply to a request sent
   from `0.0.0.0` to the broadcast address), echo requests from the peer
   to any address (the address is not known yet), then the block.
3. **Lease.** `en6` is brought up without an address and joined as in
   5.2. Then routes and the resolver are recorded, and the run starts
   `dhcp.client -b -u -T 30 -i en6`, bounded at 60 s. `-b` is the
   factory's choice and is needed here: before `en6` holds an address, the
   stack would drop a unicast offer. After exit 0 the run waits 10 s for
   `dhcp-up` and its 6 s semaphore wait. The address `en6` then holds must
   be on the peer's `/24`; otherwise there are no pings and no hold.
   Routes are compared without counters and without cloned or ARP host
   entries (flags `c`, `L`). Every difference is logged. A default route
   that appears or disappears, or a `mam0` route that disappears, counts
   against the run, as does any resolver change.
4. **Pings and hold** are as in 5.2, to and from the leased address.
5. **Stop.** Before the disconnect, the run finds its client in
   `pidin -f aA` by arguments ending in `-i en6`, so the factory's
   `-b -i en5` never matches. It sends TERM, then KILL after 10 s. Then
   it records whether `en6` still holds the address, the route changes
   and the resolver. The disconnect, destroy and factory restore follow
   as in 5.2. At the end, the routes after the restore are compared with
   the baseline in the same way.

The watchdog stops the client the same way before it destroys `en6`.

Pass for 5.3: the run's `RESULT:` line, which needs the lease, no
default-route, `mam0` or resolver change, both pings, the hold and a
stopped client. On the Pi, `dnsmasq` should log `DISCOVER`, `OFFER`,
`REQUEST` and `ACK` for the factory MAC, the lease file should name the
address, and its pings during the hold should be answered. Whether
`dhcp.client` sends a `RELEASE` on TERM is unknown, so neither outcome
fails the run. `mam0` and `en5` must stay up, and no `mmi_sta_en6` rule
may remain.

Known limits and risks:

- Everything from 5.2 still applies: the driver runs inside the shared
  `io-pkt`.
- `dhcp-up` is global and runs `route` and `ifwatchd` as root. This run
  changes nothing in it and only records what it did. A route it adds
  stays until a restart, and a restart is needed afterwards anyway.
- `en5`'s own `dhcp.client` also binds port 68 with `SO_REUSEPORT`, so a
  broadcast offer on `en6` may reach it too. With `SO_BINDTODEVICE` it
  should not take that offer, and the transaction ID differs anyway.
  This is unproven; the run checks that `en5` stays up and keeps its
  routes.
- On the unit, the `pidin -f aA` format is known only from the October 27
  captures (`<pid> dhcp.client -b -i en5`). The dry run checks it again.
- The pass rule for 67→68 matches any destination on `en6`, but only from
  the peer's address. The bench is the owner's Pi.

Tests: `test_radio_reattach` gained 11 for `-d` (`StationDhcpMode`), run
against a fake `dhcp.client` that leaves a daemon behind. They cover:

- the order (join, lease, pings, stop, leave), and that `en6` gets no
  static address;
- the three rules;
- a default route brought by the lease, a lost `mam0` route and a
  resolver change, each partial, with the client still stopped;
- that cloned and ARP entries do not count as route changes;
- no lease (exit 3) and a lease off the peer's subnet, each skipping the
  pings and still restoring;
- a client that ignores TERM;
- refusals: unreviewed files, either `dhcp-up` branch marker, a client
  already on `en6`, and wrong option combinations;
- the dry run;
- the watchdog stopping the client before it destroys `en6`.

406 tests in all. `radio-reattach.sh` is now `cksum 2174008454 60923`.
No adversarial review has been run.

To run it (owner's go-ahead required; a fresh start-up before, a restart
after):

```sh
# on the Pi (root@<pi>; the script is in /root/mmi-bench)
./bench-open-ap.sh start                      # mmi-bench, channel 1
# copy devn-mv8688sta.so, mv8688-ctl, mv8688-probe, radio-reattach.sh,
# sd8688_helper.bin and sd8688.bin to /fs/tmpfs over the wired path and
# check each cksum on the unit; a dry run first (-n), then:
ssh -p 2222 root@<en5 address> \
  "/bin/ksh -c 'trap \"\" HUP; exec /fs/tmpfs/radio-reattach.sh \
   -D /fs/tmpfs/devn-mv8688sta.so -K /fs/tmpfs/mv8688-ctl \
   -P /fs/tmpfs/mv8688-probe -H /fs/tmpfs/sd8688_helper.bin \
   -S /fs/tmpfs/sd8688.bin -A mmi-bench -d -E 10.42.88.1' \
   > /fs/tmpfs/radio-run.log 2>&1 &"
# on the Pi, once the log says "hold: 60s associated; ping <address>"
./bench-open-ap.sh status                     # the lease
ping -c 20 <address>; ping -c 10 -s 1472 <address>
# on the Pi, after the run
./bench-open-ap.sh stop
```

## Phase 5.0 results

Three runs on 2026-10-29 between about 01:40 and 02:07 UTC (host clock),
the car on, each from a fresh start-up with `uap0` holding its factory
address, over `en5`. `radio-reattach.sh` `cksum 4148981172 33602`, helper
and station image as in 4.2; every cksum was checked on the unit, and each
run's `-n` preflight passed first. The bench access point was the Pi
(`bench-open-ap.sh` `cksum 1597476200 9465`), open, `mmi-bench`, channel 1.
Command `radio-reattach.sh -P … -H … -S … -C -L -A mmi-bench`. The Pi's
BSSID is named here by its OUI only (`dc:a6:32`), the station's as
`00:1c:d7`.

| Run | Unit uptime at start | Probe | Pi | What happened | Verdict |
|---|---|---|---|---|---|
| 1 | 3 min 26 s | `cksum 4016092601 67473` (as prepared) | in the house, heard at RSSI byte 74 (about −71 dBm) | Associated; the probe rejected the response code, skipped the hold and deauthenticated | `PARTIAL` (probe `FAIL`) |
| 2 | 7 min 6 s | `cksum 2886143953 67505` (`0x8012` accepted) | same place | Two passive scans did not hear the Pi; no association attempted | `PARTIAL` (probe `FAIL`) |
| 3 | 9 min 42 s | `cksum 2141926964 67697` (active SSID scan) | in the car, RSSI byte 46 (about −43 dBm) | Associated, held 15 s, deauthenticated | **`OK`** |

**Run 1: the association response comes back as `0x8012`.** The download
(helper, then the station image), `MAC_ADDRESS` set and read-back,
`MAC_CONTROL` and the passive scan worked as in 4.4; the scan heard the Pi
on channel 1, the weakest of four networks there. `SET_WEP` remove, both
`KEY_MATERIAL` clears, `ENABLE_RSN` off, `AUTHENTICATE` open and
`RADIO_CONTROL` on were each accepted on the first write with result 0 in
3–4 ms, so none of the tolerances was needed. `ASSOCIATE` (`0x0050`) was
answered in 12 ms with response code **`0x8012`**, result 0, sequence
matching, 51 bytes: capability `0x0401`, status 0, AID `0xc001` (the
802.11 top two bits set), then the Pi's supported-rates, extended-rates,
extended-capabilities and a vendor element (OUI `00:10:18`). The probe
required `0x8050` and failed the command. Libertas expects exactly this:
`host.h` defines `CMD_RET_802_11_ASSOCIATE` as `0x8012`, and
`lbs_process_command_response` accepts it. The offline preparation missed
it. Because the command failed, there was no hold; `DEAUTHENTICATE`
followed at once and was accepted. The Pi's `hostapd` logged the factory
MAC `associated`, then `disassociated` 2.7 ms later.

Fix: `ASSOCIATE`, and only `ASSOCIATE`, also accepts `0x8012`; every other
command still has to match its own response code (two tests).

**Run 2: a weak access point missed by passive scans.** Both scans (200 ms
passive dwell per channel) heard the three strong channel-1 networks at
about −45 dBm, but not the Pi; the first scan heard only 3 networks in
all, the second 12. `hostapd` was beaconing throughout and logged no
station. Nothing else differed. One 200 ms listen is not reliable for a
network at about −71 dBm on a busy channel; how often it misses was not
measured.

Fix: with `-A`, the probe now scans the way libertas does before it
connects to a named network (`lbs_scan_worker` with an SSID): an SSID TLV
(`0x0000`) after the BSSID, every channel active (mode 0) with libertas'
40 ms `LBS_DWELL_ACTIVE`, up to three scans. Without `-A` the passive scan
is unchanged. One test.

**Run 3: passed.** The owner moved the Pi into the car. The first active
scan returned one descriptor in all, the Pi on channel 1 at about −43 dBm
(channel groups took 174, 176 and 132 ms), so the firmware answers an
SSID scan with that SSID only. The download took 421 ms. The connect
commands were again accepted at once; `ASSOCIATE` took 15 ms and again
came back as `0x8012`, status 0, AID `0xc001`. The hold:

```
join: held 3000 ms rc=0 events=0 data=0 data_bytes=0 data_bad=0 stray=0 link_ends=0
join: held 6006 ms rc=0 events=0 data=0 data_bytes=0 data_bad=0 stray=0 link_ends=0
join: held 9000 ms rc=0 events=0 data=0 data_bytes=0 data_bad=0 stray=0 link_ends=0
join: held 12000 ms rc=0 events=0 data=0 data_bytes=0 data_bad=0 stray=0 link_ends=0
join: held 15000 ms rc=0 events=0 data=1 data_bytes=350 data_bad=0 stray=0 link_ends=0
```

`DEAUTHENTICATE` and `FUNC_SHUTDOWN` were accepted, the power-down cleared
the ready magic, the factory attach reached `pb_init`, `mam0` and `en5`
were untouched, `WlanEnable` and `BtReset` stayed `0x00000000`, and the
script reported `RESULT: the station associated with 'mmi-bench', held the
link and deauthenticated`. As before, the manually mounted `uap0` had no
SSID, and a restart was needed.

The Pi's side agrees. `hostapd` logged the station `associated` and
`AP-STA-CONNECTED` at …616.778, then `disassociated` and
`AP-STA-DISCONNECTED` at …631.797, 15.02 s later; the probe's
deauthentication appears there as a disassociation, logged twice.

**The first received frame.** The one data frame the firmware passed up
decoded as: `rxpd` status `0x0001`, SNR 50, noise floor 93, rate index 0,
**802.3 header at offset 38** (not straight after the 20-byte `rxpd`),
`pkt_len` 350 equal to the packet's own length from that offset,
destination broadcast, source the Pi, LLC/SNAP ethertype `0x0800`. The
Pi's capture has the same frame: the Pi's DHCP request broadcast at
…631.515, 342 bytes as Ethernet II, which is 350 less the 8-byte LLC/SNAP
header. Two multicast mDNS frames the Pi sent during the hold (…625.70 to
`224.0.0.251`, …625.84 to `ff02::fb`) were not passed up. That fits
`MAC_CONTROL` having been sent with RX and TX only, without libertas'
multicast enable (`0x0020`), but it was not tested.

What this settles:

- The station firmware accepts libertas' open-connect sequence unchanged,
  and associates and holds a link to an open 802.11g network with the
  access point's factory MAC. The radio transmits.
- `ASSOCIATE`'s response code is `0x8012` on this firmware.
- An SSID scan, active, finds the target where passive scans can miss it,
  and returns only that SSID.
- Received data frames carry an `rxpd` whose packet offset is not 20, so
  the data path must use `pkt_ptr`, as libertas does. The firmware passes
  broadcasts up already converted to 802.3 with LLC/SNAP.

**A bench problem it showed.** The Pi runs a system-wide `dhcpcd` (10.0.6,
`dhcpcd.service`, all interfaces) besides netplan's `networkd`; it is what
holds the Pi's second address on the car's network. `bench-open-ap.sh`
hands `wlan0` over from `networkd` and `netplan-wpa-wlan0` only, so
`dhcpcd` kept broadcasting DHCP requests from `wlan0` while it was the
access point. That frame is the one the probe received. It did no harm
here, but its journal shows worse: with no server answering, its lease
on `wlan0` expired after about 5 s of rebinding and it added an IPv4
link-local address (`169.254.241.99`) and a `169.254/16` route to the
access point's interface.

Fix, the same night: `start` sends `dhcpcd -x <interface>`, which removes
the interface from the running manager without releasing its lease
(`-k` would send a release); with the Pi's `persistent` option that
leaves its addresses, and `start`'s flush removes them. `stop` sends
`dhcpcd -n <interface>`, which adds it back. Checked on the Pi with the
car's access point down (no head unit run): `-x` logged `wlan0: removing
interface` and `-n` `wlan0: waiting for carrier`, with the manager and
`eth0` untouched. In a 40 s `start`/`stop` cycle `wlan0` carried only
`10.42.88.1/24` and its route, the capture held no BOOTP frame, `dhcpcd`
logged nothing for `wlan0` between the two commands, and afterwards
`netplan-wpa-wlan0` was active and `dhcpcd` waiting for carrier. Script
`cksum 2187135882 10336`. Not seen: `dhcpcd` regaining the Pi's lease on
the car's network once the access point is back.

Not shown: data transmitted by the station (5.0 sent none), whether the
firmware passes up unicast frames, reassociation, behaviour with the
access point at the range of run 2, and anything about WPA. Evidence:
private run logs, the Pi's `hostapd`, `dnsmasq` and `tcpdump` output under
`<private>/live-20261029-phase50{,b,c}/`.

## Phase 5.1 results

One run on 2026-10-29 at about 02:48 UTC (host clock), the car on, from a
fresh start-up (forced restart by the owner; unit uptime about 3 min at
the start, `uap0` holding its factory address), over `en5`. Every cksum was
checked on the unit: `mv8688-probe` `3411235905 76187`,
`radio-reattach.sh` `4264391060 35072`, helper and station image as in
4.2. The `-n` preflight passed first. The Pi was in the car, running
`bench-open-ap.sh` (`cksum 2187135882 10336`): open, `mmi-bench`,
channel 1, `10.42.88.1/24`. Command `radio-reattach.sh -P … -H … -S … -C
-L -A mmi-bench -I 10.42.88.10 -E 10.42.88.1`. As before, the Pi's BSSID is
named by its OUI only (`dc:a6:32`), the station's as `00:1c:d7`.

**Verdict `OK`, at the first run.** Download 373 ms; `MAC_ADDRESS` set and
read back; the first SSID scan found the Pi (one descriptor, RSSI byte 43,
about −40 dBm); the connect commands as in 5.0; `ASSOCIATE` answered as
`0x8012`, status 0, AID `0xc001`, in 13 ms. Then:

| What | Result |
|---|---|
| ARP | First request answered within 4 ms; the peer's MAC learned |
| Echoes | 14 of 14 answered and every check passed: 7 of 7 sizes, twice each |
| Writes | 15 data frames, 11,286 bytes, 1–7 blocks; each on the first try, `IO_RDY` on the first read, `DNLD` on the second status read |
| Per echo | 3–13 ms from the start of the write to the checked reply; 5 and 13 ms for the 7-block frames |
| Received | 15 data frames, none bad, none late, nothing else; SNR 45–49, noise floor 92–93, rate index 10–12 |
| Link | No event; `link_ends=0`; `DEAUTHENTICATE` and `FUNC_SHUTDOWN` accepted |
| Command layer | 32 packets, 32 `DNLD`s (17 commands, 15 data writes), no aborts, no stray responses |

`DNLD` does follow a data write, as libertas assumes: one per write, every
time. The replies to the 1,472-byte echoes, 1,514 bytes as Ethernet II,
were the first reads of the I/O port of 7 blocks; they decoded as the
others did. By libertas' `fw_data_rates` table, indices 10, 11 and 12 are
36, 48 and 54 Mb/s; the first replies came at 54 and the last at 36. That
this firmware uses the same table is assumed, not checked. Some `rx:` lines
come before the `tx:` line of the frame they answer: the reply arrived
during the `DNLD` wait, and the `tx:` line is written after the wait.

After the probe, the power-down cleared the ready magic, the factory
attach reached `pb_init`, `mam0` and `en5` were untouched, and `WlanEnable`
and `BtReset` stayed `0x00000000`. The script reported `RESULT: the station
associated with 'mmi-bench', exchanged ARP and ICMP echoes with 10.42.88.1
and deauthenticated`. As in earlier runs, the manually mounted `uap0` had
no SSID afterwards, and a restart was needed.

**The Pi's side agrees.** `hostapd` logged the factory MAC `associated` at
02:49:10.568 UTC and `disassociated` 3.10 s later, logged twice as in 5.0.
The capture, decoded with `tcpdump -n -e -vv 'arp or icmp'`, holds, from
02:49:10.574: the broadcast ARP request from the factory MAC asking for
`10.42.88.1` on behalf of `10.42.88.10`, the Pi's reply, then 14 echo
requests from the factory MAC, Ethernet lengths 98, 290, 546, 802, 1,058,
1,314 and 1,514 twice over, IP identification 1–14, ICMP identifier the
probe's process ID and sequence 1–14, each followed within 0.15 ms by the
Pi's reply. `tcpdump` reported no `bad cksum`. The last reply left the Pi
at 02:49:10.647, and the disassociation came 3.02 s later, at the end of
the probe's 3 s watch. Other frames in the capture are the Pi's own mDNS
and NetBIOS broadcasts, all from before the association, and three unicast
ARP requests from the Pi to the station 2.3–4.4 s after it left (Linux
re-checking a stale neighbour), which nobody answered.

What this settles:

- The station transmits and receives data frames through the libertas
  `txpd`/`rxpd` path on this firmware, at every write size up to a full
  Ethernet frame, with the factory MAC, and the frames are right on the
  air: the access point's stack accepted them and answered.
- A data write raises `DNLD` like a command, and the libertas rule of
  one transfer in flight is enough on its own; no write was retried.
- Received frames handed on as Ethernet II, the form the io-net driver
  will pass to `io-pkt`, carried what the Pi sent, byte for byte.

Not shown: throughput (each echo waited for its reply), behaviour under a
stream of frames or with the radio busy, multicast (still off in
`MAC_CONTROL`), anything at range or over minutes, and anything inside
`io-pkt`. Evidence: the private run log, preflight log, and the Pi's
`hostapd`, `dnsmasq` and `tcpdump` output with a decoded copy, under
`<private>/live-20261029-phase51/`.

## Phase 5.2 results

One run on 2026-10-29 at about 03:58 UTC (host clock), the car on, from a
fresh start-up (forced restart by the owner; unit uptime 3 min 21 s at the
start, `uap0` holding its factory address), over `en5`. Every cksum was
checked on the unit before the run, as built (see
[Station driver](#station-driver-52-prepared-offline)):
`devn-mv8688sta.so` `4051650589 58772`, `mv8688-ctl` `3782954773 9157`,
`mv8688-probe` `3930888157 76287`, `radio-reattach.sh` `883843081 47629`,
helper and station image as in 4.2. The `-n` preflight passed first. The Pi
was in the car, running `bench-open-ap.sh` (`cksum 2187135882 10336`) as
in 5.1. Command as in the run instructions above, `-W` left at 60 s. No
adversarial review had been run on the driver.

**Verdict `OK`, at the first run.** This was the first code of ours inside
the shared `io-pkt`, and the stack survived the whole run: `mam0` and
`en5` stayed up and the SSH session over `en5` was never lost. In order,
the run log shows:

| Step | Result |
|---|---|
| Detach | `uap0` destroyed, the factory object unloaded, no automatic re-mount within 20 s; the probe guard passed on the idle module |
| Filter rules | Both `mmi_sta_en6` rules loaded before the mount |
| Mount | `mount -T io-pkt -o lan=6,mac=…,helper=…,firmware=… devn-mv8688sta.so` returned and `en6` attached (download and station set-up inside `init`) |
| Connect | `mv8688-ctl connect mmi-bench` reported associated |
| Station pings | Peer answered at 56 and 1,472 bytes |
| Hold | 60 s, `state=associated` at every 10 s check; no link loss |
| Teardown | Disconnect, then `ifconfig en6 destroy`: `en6` gone, module idle, no ready magic |
| Restore | Rules out; factory attach with `resetmask=0` reached `pb_init`; `WlanEnable` and `BtReset` stayed `0x00000000`; watchdog disarmed |

Afterwards `ifconfig -l` listed `lo0 pflog0 mam0 mhp0 en5 uap0` and no
`mmi_sta_en6` rule remained. `sloginfo` held no line from the driver or the
shim by the time it was read, a few minutes later; the log wraps quickly
(see [Hazards](#hazards-specific-to-this-unit)), so this shows nothing
either way. As after every manual mount, `uap0` had no SSID, and a restart
is needed for the access point.

**The Pi's side agrees.** During the hold the Pi pinged the station 20
times at 56 bytes and 10 times at 1,472 bytes: 30 of 30 answered, 0 %
loss, round trip 5.0–11.1 ms (average 6.2 ms) and 5.3–8.8 ms (average
7.7 ms). This is the receive and send path through `io-pkt`: the
requests went up the stack and its replies came back down through
`rx_down`. `hostapd` logged the factory MAC `associated` at 03:58:57.400
UTC and `disassociated` at 04:00:12.656, 75.3 s later (logged twice, as
in 5.0). The capture holds 41 frames from the factory MAC and nothing
else from it:

- a gratuitous ARP for `10.42.88.10` 0.6 s after the association (the
  stack's, when `en6` took its address), then its ARP request for the Pi;
- 6 echo requests (3 at 64 and 3 at 1,480 IP bytes; the script's pings);
- 30 echo replies (20 at 64 and 10 at 1,480 IP bytes);
- 3 ARP replies, the last at 03:59:53.832 to a unicast ARP request from
  the Pi, i.e. ARP from the access point is passed up and answered.

`tcpdump -vv` found no bad checksum. The station sent no DHCP, IPv6 or
other frame, so nothing on the unit started `dhcp.client` or another
service on `en6` within these 75 s, or if it did, it sent nothing. The
other frames in the capture are the Pi's own mDNS and NetBIOS broadcasts.

What this settles:

- `devnp-shim` loads, registers and destroys the station driver as the
  offline analysis said: the MAC from `GET_CONFIG`, the firmware download
  inside `init` on the stack thread, and a clean `ifconfig destroy`
  through `shutdown1`/`shutdown2` and the unload, after which the
  firmware is gone and the factory driver attaches.
- The card thread's 1 ms polling carries frames both ways in `io-pkt`
  with the factory MAC, at full Ethernet size, and the devctl control
  path (`mv8688-ctl`) works.
- The disconnect, pause and destroy closed the teardown window for this
  run; that is one run, not a proof.

Not shown: throughput or a stream of frames (pings only, one per second),
the driver's fault paths (`FAILED` after 20 errors, a link loss during the
hold), behaviour beyond 75 s or at range, DHCP (5.3), and whether anything
on the unit reacts to `en6` over a longer time. Evidence: the private run
log, preflight log, post-run state, and the Pi's ping output, `hostapd`,
`dnsmasq` and `tcpdump` output with a decoded copy, under
`<private>/live-20261029-phase52/`.

## Phase 5.3 results

### First run: no IFF_BROADCAST on en6

One run on 2026-10-29, started at 04:31:57 UTC (host clock), the car on.
It came from a fresh start-up (unit uptime 28 min, `uap0` holding its
factory address) and ran over `en5`. The six files were copied to
`/fs/tmpfs`, and each cksum on the unit matched the build: the 5.2
driver, tool and probe, and `radio-reattach.sh` `2174008454 60923`. On
the unit, `dhcp.client` and `dhcp-up` matched the pinned values. Neither
`dhcp-up` branch marker existed. `/tmp/ifwatchd_dhcp_down` existed, and
`/tmp/ppp0_defaultroute` and `/etc/resolv.conf` did not. The `-n` dry run
passed first. It listed the factory client as `1081425 dhcp.client -b -i
en5 ` (with a trailing space; the stop's pattern allows it). It recorded
`_CS_RESOLVE` as `nocache_on`. The routing table had no default route.
The Pi ran `bench-open-ap.sh` (`cksum 2187135882 10336`) in the car.

**Verdict `PARTIAL`.** In order:

| Step | Result |
|---|---|
| Detach, re-mount watch, guard | As in 5.2 |
| Filter rules | All three `mmi_sta_en6` rules loaded |
| Mount, connect | `en6` attached; associated with the Pi on channel 1, about −46 dBm, AID `0xc001` |
| Lease | `dhcp.client -b -u -T 30 -i en6` exited 3 (its own timeout) with no output; no pings and no hold |
| Stop | No `dhcp.client` for `en6` was left running |
| Teardown, restore | As in 5.2: `en6` destroyed, module idle, no ready magic, rules out, factory attach with `resetmask=0`; watchdog disarmed |
| Routes | No default route came or went and every `mam0` route stayed. Only the `239.255.255.250/32` route on `uap0` was gone after the manual re-attach (expected, since the networking service adds it) |

The Pi saw nothing from the station but the 802.11 association and the
disassociation 40.1 s later. There was no DHCP frame, no lease, and no
frame from the factory MAC in the capture. The driver's counters agree:
`tx_frames=0` after the connect. Its status after the lease showed
`en6: flags=80000041<UP,RUNNING,SHIM>`. `en5` shows
`80008a43<UP,BROADCAST,RUNNING,ALLMULTI,SIMPLEX,MULTICAST,SHIM>`.

**Cause** (offline, from the saved `devnp-shim` and `devn-asix`
decompilations): the shim attaches the interface with `if_flags =
0x80000000` (its own `SHIM` bit) and nothing else. It ORs in further
flags only from an up packet marked `_NPKT_MSG` whose message type is
`0x100` (`_IO_NET_MSG_DL_ADVERT`). From that message it takes `iflags`
and the two capability words, then clears `IFF_RUNNING`. The factory
`devn-asix` sends one right after it registers (`ax_advertise`): 0x4c
bytes, `iflags 0x842` (`BROADCAST|RUNNING|SIMPLEX`, or `0x8842` with
`MULTICAST` when its configuration has `NIC_FLAG_MULTICAST`), the MTU,
`up_type` `en<lan>`, and a `sockaddr_dl` with the name and MAC. It goes
up through `tx_up`. The 5.2 driver sent none; its `NIC_FLAG_BROADCAST`
in `nic_config_t` does not reach `if_flags`. Without `IFF_BROADCAST`, a
NetBSD-derived `ip_output` refuses a send to 255.255.255.255, which is
the only kind of send `dhcp.client` makes before it has a lease. So the
requests never reached the driver. The 5.2 traffic was unicast IP and
ARP. ARP is built by the link layer and does not depend on that flag.
This explains why 5.2 passed and 5.3 did not.

**Change for the second run.**

- `devn-mv8688sta.c` now sends the same `DL_ADVERT` after `reg`, through
  `tx_up_start`. `rx_up_pending` counts it until `tx_done`. Its `iflags`
  are `IFF_BROADCAST|IFF_SIMPLEX|IFF_RUNNING`, without `MULTICAST`,
  because `MAC_CONTROL` passes no multicast.
- `radio-reattach.sh` logs `en6`'s flags after the mount. With `-d`, it
  skips the lease if `BROADCAST` is missing.
- `test_radio_reattach` gained one test for that (407 tests in all).
- Rebuilt:

| File | SHA-256 | `cksum` |
|---|---|---|
| `devn-mv8688sta.so` | `3f7ad38e…7668aad8` | `2454083246 59636` |
| `mv8688-ctl` (unchanged) | `4da98e67…e51c09ec` | `3782954773 9157` |
| `radio-reattach.sh` | | `1908183992 61559` |

`verify_elf.py` passes for both, and the probe is unchanged. That the
shim accepts this advert and sets `BROADCAST` on `en6` is shown only
offline. The second run's flag line checks it.

Evidence: the run log, the state directory, the post-run state and the
Pi's logs and capture, under `<private>/live-20261029-phase53/`.

### Second run: the lease works, `-u` never returns

2026-10-29, started at 04:46:16 UTC, after a forced restart by the owner.
It used the rebuilt driver (`2454083246 59636`) and `radio-reattach.sh`
`1908183992 61559`. Every cksum matched on the unit, the dry run passed,
and there was still no default route. The bench was as before.

**The advert works.** `en6` attached as `80000802<BROADCAST,SIMPLEX,
SHIM>`. After the `up`, the shim showed it as `80000843<UP,BROADCAST,
RUNNING,SIMPLEX,SHIM>`. The association matched the first run (about
−45 dBm).

**The DHCP exchange and the address both worked.** The Pi's capture and
`dnsmasq --log-dhcp` agree (times are the Pi's local clock):

| Time | Frame |
|---|---|
| 00:47:03.364 | `DHCPDISCOVER`, 0.0.0.0:68 → 255.255.255.255:67, from the factory MAC |
| 00:47:03–05 | `dnsmasq`'s three ARP probes for `10.42.88.50`, unanswered |
| 00:47:06.370 | `DHCPOFFER` `10.42.88.50`, broadcast (`-b`) |
| 00:47:06.442 | `DHCPREQUEST` |
| 00:47:06.444 | `DHCPACK` `10.42.88.50`, lease 10 min, no router or DNS option |
| 00:47:06.452 | The station's gratuitous ARP for `10.42.88.50`: the address is on `en6` |
| 00:47:06.452–.461 | An echo request from the Pi, the station's ARP for the Pi, and its echo reply |
| 00:47:11.846 | The station answers the Pi's unicast ARP |
| 00:48:03.582 | `DHCPRELEASE` `10.42.88.50`, unicast to the Pi |

The station asked for options 1, 3, 6, 12, 15 and 28. No request was
retransmitted. It sent 7 frames and received 9 by the driver's counters,
with no errors or drops.

**But `dhcp.client -u` never left the foreground.** The run's 60 s bound
expired, and the TERM it sent produced the `DHCPRELEASE`: the client
releases on TERM, and it also took the address off `en6`. The run
therefore reported "exit 1", which was the bound, not the client, and
skipped the pings and the hold. Teardown and restore were as in the
first run: no default-route or `mam0` change, and every rule out.
Afterwards no `dhcp.client` for `en6` and no `dhcp-up`, `route` or
`waitfor` process was running. `/dev/shmem/networkscript.semaphore`
still showed 00:04, the boot-time lease on `en5`. `dhcp-up` removes and
re-creates that file, so it did not complete for this lease, and
probably did not start. Why `-u` waits on `en6` is not known. The run
recorded nothing from the client itself (no `-d`).

**Change for the third run.** `radio-reattach.sh` now starts the client as
the factory does on `en5`, in the background from the start:
`dhcp.client -d -b -T 30 -i en6`, where `-d` sends its steps to slog. It
then polls `en6` for an address for up to 40 s. Before and after the
lease it records the `dhcp-up` semaphore and any `dhcp`, `ifwatchd`,
`waitfor` or `route` process, and it captures the client's slog lines.
`test_radio_reattach` has 72 tests (409 in all); script
`cksum 212763613 62997`.

Evidence: `<private>/live-20261029-phase53b/`.

### Third run: the client does not daemonize at all

2026-10-29, started at 04:58:37 UTC after a forced restart. It used
`radio-reattach.sh` `212763613 62997`, the same driver as run 2, and
matching cksums; the dry run passed. `en6` attached with `BROADCAST`
again.

`dhcp.client -d -b -T 30 -i en6`, without `-u`, also never returned. The
20 s bound on its start expired and its TERM released the lease. So the
run again had no pings and no hold, and the restore was clean as before.
The Pi's capture shows the following (Pi clock):

| Time | Frame |
|---|---|
| 00:59:27.687 | `DHCPDISCOVER` |
| 00:59:30.706 | `DHCPREQUEST`; `dnsmasq` ACKs `10.42.88.50` |
| 00:59:34.734 | A second `DHCPREQUEST`, ACKed again (why the first ACK was not taken is not known) |
| 00:59:34.745 | Gratuitous ARP for `10.42.88.50`: the address is on `en6` |
| 00:59:46.387–.390 | ARP for the Pi, then a unicast `DHCPRELEASE` (the bound's TERM) |

This time `dhcp-up` did run and finish. The semaphore went from
00:02 (before the lease) to 00:04 (unit clock). The run's route comparison
over the whole run shows no default-route or `mam0` change, and only the
`239.255.255.250/32` route on `uap0` was missing after the manual
re-attach, as in the earlier runs. No `slog` line came from `-d`; where
its debug output goes was not found.

**Conclusion.** This `dhcp.client` stays in the foreground for the life of
the lease. Its usage text gives the form as `dhcp.client [options...] &`,
and the factory starts it with `&`. The `-u` text ("do not move to the
background until …") did not describe what was seen in run 2.

**Change for the fourth run.** `radio-reattach.sh` starts the client with
`&`, keeps its pid, polls `en6` for an address for up to 40 s, and
reports the client's exit status if it ends first. Stopping it is still by
its arguments in `pidin` (TERM, then KILL), and the watchdog does the
same. The test double now stays in the foreground the same way.
409 tests; script `cksum 77964376 63272`.

Evidence: `<private>/live-20261029-phase53c/`.

### Fourth run: broadcast replies lost, and a polling bug

2026-10-29, started at 05:08:27 UTC after a forced restart. It used
`radio-reattach.sh` `77964376 63272` and the same driver; cksums
matched, and the dry run passed.

The client now ran in the background as intended (pid 3797076). It was
found and stopped by its arguments, and the restore was clean: no
default-route or `mam0` change, and every rule out. But the run took
`0.0.0.0` for a lease. The client holds `inet 0.0.0.0 netmask
0xff000000` on the interface while it negotiates. The poll stopped
there after 1 s, and the check 10 s later still found `0.0.0.0`, so
the run reported a lease off the peer's subnet and went to teardown.

The Pi's capture shows why there was no address yet after 11 s (Pi
clock):

| Time | Frame |
|---|---|
| 01:09:16.820 | `DHCPDISCOVER` |
| 01:09:16.8–18.9 | `dnsmasq`'s three ARP probes, broadcast |
| 01:09:19.826 | `DHCPOFFER`, broadcast: **not acted on** |
| 01:09:20.827 | `DHCPDISCOVER` again (the client's 4 s retry) |
| 01:09:20.829 | `DHCPOFFER`; `DHCPREQUEST` follows 10 ms later |
| 01:09:20.841 | `DHCPACK`: **not acted on** |
| 01:09:24.845 | `DHCPREQUEST` again (4 s retry); `DHCPACK` at .847 |
| (until the stop at about 01:09:30) | No gratuitous ARP and no further request |

The driver's counters after the run were 4 frames and 468 bytes
received, with `rx_bad=0` and `rx_drops=0`. The Pi sent 7 broadcasts in
that time: three 42-byte ARP probes and four 342-byte DHCP replies. 468
bytes is exactly three ARP probes plus one DHCP reply. So the station
was handed only one of the four DHCP replies, the second OFFER, and the
driver dropped nothing it was given. 802.11 broadcasts get no
acknowledgement and no retry. Whether the frames were lost in the air,
in the firmware, or in some broadcast-specific state (power save, DTIM)
is not established. Run 3's first ACK was likely missed the same way.
Run 2 lost none. The Pi was at about −45 dBm, in the car. What the
client would have done after 01:09:24 is not known, because the poll
had already given up.

**Change for the fifth run.** The poll ignores `0.0.0.0`, so the 40 s
window covers the client's retries. The test double now holds `0.0.0.0`
first, as the real client does. 409 tests; script
`cksum 852508176 63422`. Broadcast loss stays an open receive-path item
for phase 6, since WPA's group-key handshake and ARP also depend on
received broadcasts. It needs a counted test: for example, the Pi
sending N broadcasts while the station counts them.

Evidence: `<private>/live-20261029-phase53d/`.

### Fifth run: passed

2026-10-29, started at 05:20:31 UTC after a forced restart (unit clock
00:03:57 from boot). It used `radio-reattach.sh` `852508176 63422`,
driver `2454083246 59636` and the same `mv8688-ctl`, probe, helper and
firmware as run 4. All six cksums matched on the unit, `uap0` had its
factory MAC, and the dry run passed. The verdict was **OK**.

- **Interface.** `en6` attached as `80000802<BROADCAST,SIMPLEX,SHIM>`.
- **Lease.** `dhcp.client -d -b -T 30 -i en6`, started with `&` (pid
  3747923), put `10.42.88.50/24` on `en6` 3 s after it started.
  `dnsmasq` logged one DISCOVER, OFFER, REQUEST and ACK, with no retry.
  The station received both broadcast replies: its REQUEST went out
  11 ms after the OFFER, and its gratuitous ARP 9 ms after the ACK.
- **dhcp-up.** It ran: the semaphore went from 00:01 to 00:04 (unit
  clock).
- **Routes and resolver.** The lease added only `10.42.88/24 link#5
  en6`. No default route came or went, every `mam0` route stayed, and
  `resolv.conf` and `_CS_RESOLVE` were unchanged.
- **Pings.** The station pinged the Pi at 56 and 1472 bytes, 3 of 3
  each. During the 60 s hold, the Pi pinged `10.42.88.50`: 20 of 20 at
  56 bytes (average 6.3 ms) and 10 of 10 at 1472 bytes (8.0 ms). The
  capture has 37 echo requests and 37 replies. The 37th request is
  `dnsmasq`'s own check of the address after the ACK.
- **Stop and restore.** TERM stopped the client, which sent a unicast
  `DHCPRELEASE` that `dnsmasq` logged, and its `/24` route went with
  it. The rules came out, the access point was re-attached with the
  factory parameters, and `en5` stayed up. The whole-run route
  comparison shows no default-route or `mam0` change. As in every run,
  only the `239.255.255.250/32` route on `uap0` is missing after the
  manual re-attach.

**No broadcasts were lost.** The driver's RX count after the run was
66 frames and 25405 bytes, with no bad or dropped frames. The Pi's
capture between `hostapd`'s association and disassociation has 40
unicast frames to the station, 26 broadcasts and 2 mDNS multicasts.
The unicast frames and broadcasts come to exactly 66 frames and 25405
bytes. So the station got every unicast frame and all 26 broadcasts,
including 20 NetBIOS broadcasts that came in bursts of five within a
millisecond. It filtered the multicasts, as expected, since the driver
programs no multicast list.

Run 4's loss (3 of 4 DHCP replies missed) did not recur. Broadcast
receive is therefore intermittent, not broken. It stays open for phase
6 and still needs the counted test.

The client's `-d` output still did not appear: `sta-dhcp.txt` and the
run's `slog` capture are both empty.

5.3 passes: a lease, no default-route or `resolv.conf` change from the
bench, `mam0` routes kept, and a clean restore.

Evidence: `<private>/live-20261029-phase53e/`.

## Phase 6 plan

Offline planning and preparation, 2026-10-29, after the 5.3 run. Nothing
in this section has run on the unit. Source revision: branch
`wifi-sta-phase4-host-probe` on top of `d7194cc`.

Phase 6 is "join a WPA2-PSK network". As the
[architecture](#proposed-architecture) planned, the firmware does what it
did in phase 5 (scan, authenticate, associate) and, once given keys, the
CCMP encryption; the 4-way and group handshakes run on the host, in
`wpa_supplicant`, exactly as libertas leaves them to `wpa_supplicant`
through cfg80211 on Linux. Four pieces are new:

- **The driver serves a supplicant** (control interface version 2,
  [`mv8688sta-ctl.h`](../../wifi-sta/mv8688sta-ctl.h)). `SCAN` scans
  channels 1–11, actively for a named SSID or passively for all, and
  keeps every BSS heard, one entry per BSSID (the strongest report), with
  its elements: up to 512 bytes from the SSID element on, cut at an
  element boundary. `SCAN_RESULT` reads them one at a time. `JOIN`
  associates with one listed BSS, carrying the supplicant's RSN or WPA
  element as the TLV libertas' `lbs_add_wpa_tlv` builds (the element's own
  ID and length as 16-bit words) after `ENABLE_RSN` on; the BSS must
  advertise privacy and RSN or WPA. `SET_KEY` installs a pairwise or group
  key with `KEY_MATERIAL` as `lbs_cfg_add_key` does (AES 16 bytes, TKIP
  32; no key index, no receive counter), or clears one. One request is
  queued or running at a time, and its completion is published together
  with the state it left. Frames the stack
  queued before a request are sent before it runs, so the EAPOL 4/4 frame
  is not encrypted under the pairwise key installed right after it. The
  firmware's TKIP MIC-failure events (13 and 14) are counted. The open
  `CONNECT` of 5.2 is unchanged.
- **A `wpa_supplicant` backend**,
  [`wifi-sta/wpa/driver_mv8688.c`](../../wifi-sta/wpa/driver_mv8688.c).
  The driver takes one request at a time and reports only through its
  status, so scans and joins are queued and finished by a 100 ms status
  poll that sends the supplicant's events; keys and disconnects wait for
  their result. The poll also turns a link the firmware ended
  (deauthenticated, disassociated, link lost) into a disassociation event.
  An event is never sent from inside one of the supplicant's own calls.
  A new association first settles any earlier request, and an earlier join
  not yet reported is dropped; a deauthentication cancels an outstanding
  join even when it cannot wait for it, and if that join associates anyway
  the poll leaves again.
  It offers open authentication and WPA/WPA2 with TKIP or CCMP only, and
  passes only the first RSN (else WPA) element of what the supplicant
  builds, as `lbs_add_wpa_tlv` does. It declares two scan SSIDs so the
  supplicant puts a `scan_ssid=1` network and the wildcard into one
  request, and scans for the named one: with one, the supplicant
  alternates them, and every other scan would be a passive one, the kind
  that missed a weak access point in 5.0. The cost is that the wildcard
  and any second named SSID in a request are not scanned, so a
  configuration must hold one network; the run script enforces that.
- **EAPOL over BPF.** The unit's `io-pkt` has `/dev/bpf` (phase 1) but
  there is no libpcap, so
  [`l2_packet_qnx.c`](../../wifi-sta/wpa/l2_packet_qnx.c) sets BPF up as
  libpcap would on NetBSD, through
  [`wifi-sta/bpfl2.c`](../../wifi-sta/bpfl2.c): buffer size before the
  interface, `DLT_EN10MB` checked, immediate mode, complete headers on
  write, and a filter for type `0x888e` that drops the station's own
  frames. Whether BPF works on an interface behind `devnp-shim` is
  unknown: `dhcp.client` uses sockets, not BPF (5.3).
- **Random bytes from `jitter-egd`.** The unit has neither `/dev/random`
  nor `/dev/urandom` ([OpenSSH build notes](https://github.com/chundla/qnx632-openssh/blob/main/docs/build.md)),
  which is where `wpa_supplicant` takes the SNonce. With its random pool
  off, `random_get_bytes` is `os_get_random`, which
  [`os_random_egd.c`](../../wifi-sta/wpa/os_random_egd.c) replaces with
  EGD command 2 to the `jitter-egd` that serves OpenSSH on
  `127.0.0.1:708`, each request within one 30 s deadline across connect,
  send and every read. Nothing is cached and there is no weaker fallback:
  if `jitter-egd` is down or too slow, the handshake fails. It produced about 40 bytes a
  second on the unit ([randomness notes](https://github.com/chundla/qnx632-openssh/blob/main/docs/randomness.md)),
  so a 32-byte SNonce blocks the supplicant for about a second when the
  first 4-way message arrives. `hostapd` retransmits message 1 and accepts
  a message 2 for an earlier one, so this should cost time, not the
  handshake; it is measured in 6.1.

**The supplicant build.**
[`wifi-sta/wpa/build-wpa.sh`](../../wifi-sta/wpa/build-wpa.sh) builds
`wpa_supplicant` and `wpa_cli` 2.11 from the release archive (SHA-256
`912ea06f…c0a`), whose signature was checked when it was downloaded: good
signature from Jouni Malinen's key, fingerprint
`EC4A A0A9 91A5 F246 4582 D52D 2B6E F432 EFC8 95FA` (fetched from
`keyserver.ubuntu.com`; that establishes consistency with that key, not
an independent identity check). The archive stays private in
`work/build/downloads/`. [`qnx632.patch`](../../wifi-sta/wpa/qnx632.patch)
adds the QNX byte order, `strings.h`, `unsetenv` and `net/if_ether.h`
cases, registers the driver, links `libsocket` instead of `librt`, and
switches in the EGD reader and the BPF helper. It also keeps secrets out
of a `-d` log, which upstream does not: message 3's decrypted key data
(which carries the GTK) is dumped with `wpa_hexdump_key`, shown only with
`-K`, like upstream's other decrypted dumps, and configuration errors no
longer echo an invalid PSK, passphrase, WEP key or `psk_list` entry.
[`wpa_supplicant.config`](../../wifi-sta/wpa/wpa_supplicant.config)
builds WPA/WPA2-PSK only: no EAP, WPS, P2P, PMF or roaming; internal
crypto (with its own bignum code, as the unit has no libtommath), the
Unix control interface and a debug file. Both programs need only the
unit's `libc.so.2` and `libsocket.so.2`, with no missing symbols in
`openssh/verify_elf.py`'s check against the saved libraries (that is a
link check, not a run). Built: `wpa_supplicant` `cksum 2366746706
491332` (after the 6.0 filter fix below), `wpa_cli` `2199494007 85168`. The added files compile without
warnings at `-Wall -W`; the rest of `wpa_supplicant` has GCC 3.3.5's
"parameter has incomplete type" warnings from upstream headers.

**The bench.** [`bench-open-ap.sh`](../../wifi-sta/bench-open-ap.sh)
`start -w` makes the Pi's network WPA2-PSK with CCMP only (`wpa=2`,
`WPA-PSK`, `rsn_pairwise=CCMP`, `ieee80211w=0`) under a fresh random
256-bit PSK. The PSK goes to `/run/mmi-bench-ap/psk` (mode 600), is never
printed, and is replaced by `<not kept>` in the `hostapd.conf` that `stop`
keeps with the logs. `-g` and `-p` set the group and pairwise rekey
intervals (30–86,400 s) for 6.2; `status` also shows `hostapd`'s WPA and
EAPOL lines. Script `cksum 1103698690 12407`, not yet copied to the Pi.

### Increments

6.0 **The supplicant runs, and BPF works on a shim interface.** No radio.
On the unit: `wpa_supplicant -v` and `wpa_cli -v` start and print their
versions, and [`bpf-check`](../../wifi-sta/bpf-check.c) on `en5` (also a
`devnp-shim` interface, `devn-asix`) sends one ARP request through BPF
and reads the reply through BPF. Pass: both programs run, and the reply
arrives through BPF with the station's own request filtered out. This
answers the BPF question without a restart; `en5` carries the
management session, and the check sends one broadcast ARP request on it
and configures nothing.

6.1 **WPA2-PSK with the Pi.** `radio-reattach.sh -D … -A mmi-bench -d -E
10.42.88.1 -w wpa_supplicant -c <config>`: the 5.3 run with
`wpa_supplicant` joining instead of `mv8688-ctl connect`. Pass:
`CTRL-EVENT-CONNECTED`; `hostapd` logs the pairwise and group handshakes
complete for the factory MAC; the lease, the pings and the 60 s hold as
in 5.3, now over CCMP; `wpa_supplicant` stops on TERM, deauthenticating;
the restore is clean. The time from message 1 to message 2 is recorded
(the EGD wait).

6.2 **Rekeys, reconnects and broadcasts.** The same run with `-g 60 -p
120` on the bench, a deauthentication from `hostapd_cli`, and a restart
of the bench network during the hold. Pass: the link survives the group
and pairwise rekeys and traffic continues; after the deauthentication and
the restart the supplicant reconnects on its own. The counted broadcast
test left open in 5.3 goes here: a known number of broadcasts from the Pi
during the hold against the driver's receive count.

6.3 **The phone hotspot.** The owner's iPhone hotspot on 2.4 GHz
("Maximize Compatibility"), with its passphrase in a mode-600 file only.
Pass: association and handshakes, a lease, a ping of the phone, and a
throughput figure. The default route and DNS from the lease are where the
factory `dhcp-up` interferes (5.3: it deletes the default route after
every lease); 6.3 records what happens and changes nothing persistent,
and route and resolver handling stays a phase 7 decision.

### Supplicant and BPF check (6.0), prepared offline

Built and tested on the workstation, 2026-10-29. `bpf-check -i en5 -m
<en5 MAC> -s 192.168.16.11 -t 192.168.16.2 [-w 5]` opens `/dev/bpf` on
the interface with `bpfl2_open` (the code `l2_packet_qnx.c` uses, filtering
ARP instead of EAPOL), writes one 60-byte ARP request for the target, and
reads ARP frames until the target's reply comes or the wait ends. It
prints the buffer size, the write result, each frame read (up to ten),
the reply, and BPF's own receive and drop counts. Exit 0 means the reply
arrived through BPF. The source and target are the unit's static `en5`
alias and this workstation. Builds:
[`build-driver.sh`](../../wifi-sta/build-driver.sh) now builds
`bpf-check` too (`cksum 2139201750 11814` after the 6.0 fix, needs
`libc` and `libsocket`;
its layout asserts against the SDK's `struct bpf_hdr` and
`struct bpf_insn` compile), and
[`build-wpa.sh`](../../wifi-sta/wpa/build-wpa.sh) the supplicant.

```sh
# copy bpf-check, wpa_supplicant and wpa_cli to /fs/tmpfs and check their
# cksums on the unit, then:
/fs/tmpfs/wpa_supplicant -v; /fs/tmpfs/wpa_cli -v
/fs/tmpfs/bpf-check -i en5 -m <en5 MAC from ifconfig en5> \
    -s 192.168.16.11 -t 192.168.16.2
```

### WPA2-PSK run (6.1), prepared offline

Built and tested on the workstation, 2026-10-29. Driver
`devn-mv8688sta.so` `cksum 3938534867 64931`, control tool `mv8688-ctl`
`3277080864 11450` (adds `scan [ssid]` and `results`), probe
`2747790827 77195` (rebuilt: the command layer and its BSS record
changed; in this mode it only checks the module), `radio-reattach.sh`
`1973069813 70765`. The 5.3 driver was rebuilt from `d7194cc` and came
out byte-identical (`2454083246 59636`); it is kept privately as
`devn-mv8688sta-53.so`.

`radio-reattach.sh -D … -w <wpa_supplicant> -c <config>` adds to the 5.3
run:

- **Preflight.** The configuration must be a non-empty regular file
  owned by root with mode 600, holding exactly one network with exactly
  `ssid="<-A>"` and `key_mgmt=WPA-PSK`. It is read through pipes only;
  nothing from it is logged or copied. `jitter-egd` must be running (`pidin`, bounded);
  otherwise the run refuses before touching anything, and the remedy is
  rerunning `/mnt/persistence/openssh/start.sh` (the cold start of
  2026-10-28 lost it). The supplicant's `cksum` is logged.
- **Join.** After `en6` attaches and is up, `wpa_supplicant -i en6 -D
  mv8688 -c <config> -d -t -f <state>/wpa.log` starts in the background.
  With the patch, `-d` logs no key material (only `-K` would). The join
  counts once, within 60 s, the log has `CTRL-EVENT-CONNECTED` and a key
  negotiation with `PTK=CCMP GTK=CCMP`, and the driver reports the
  association with an RSN join (`rsn=1`); a connection short of that is a
  failure. Its state, WPA, EAPOL, driver and BPF lines go to the report.
- **Leaving.** Before the disconnect, TERM stops the supplicant, which
  deauthenticates through the driver; KILL after 10 s counts against the
  run. The watchdog does the same before it destroys `en6`, as the
  supplicant also holds `en6`'s control node.

The configuration is made on the workstation from the bench PSK and piped
to the unit, so the PSK is not displayed or written on the workstation:

```sh
# on the Pi (root@<pi>; the script is in /root/mmi-bench)
./bench-open-ap.sh start -w
# on the workstation: the unit's configuration, straight from the Pi's PSK
ssh root@<pi> cat /run/mmi-bench-ap/psk | {
    read -r psk
    printf 'network={\n\tssid="mmi-bench"\n\tscan_ssid=1\n\tkey_mgmt=WPA-PSK\n\tproto=RSN\n\tpairwise=CCMP\n\tgroup=CCMP\n\tieee80211w=0\n\tpsk=%s\n}\n' "$psk"
} | ssh -p 2222 root@<en5 address> \
    'umask 077; cat > /fs/tmpfs/wpa-bench.conf'
# copy devn-mv8688sta.so, mv8688-ctl, mv8688-probe, radio-reattach.sh,
# sd8688_helper.bin, sd8688.bin and wpa_supplicant to /fs/tmpfs and check
# each cksum on the unit; a dry run first (-n), then:
/bin/ksh -c 'trap "" HUP; exec /fs/tmpfs/radio-reattach.sh \
    -D /fs/tmpfs/devn-mv8688sta.so -K /fs/tmpfs/mv8688-ctl \
    -P /fs/tmpfs/mv8688-probe -H /fs/tmpfs/sd8688_helper.bin \
    -S /fs/tmpfs/sd8688.bin -A mmi-bench -d -E 10.42.88.1 \
    -w /fs/tmpfs/wpa_supplicant -c /fs/tmpfs/wpa-bench.conf' \
    > /fs/tmpfs/radio-run.log 2>&1 &
# on the Pi, once the log says "hold: 60s associated; ping <address>"
ping -c 20 <address>; ping -c 10 -s 1472 <address>
# on the Pi, after the run
./bench-open-ap.sh status; ./bench-open-ap.sh stop
```

**Tests** (workstation; 489 in all, from 408): the command layer keeps
the elements and builds the `KEY_MATERIAL` and WPA `ASSOCIATE` bodies
libertas builds, and the largest WPA association still fits one block;
the station core's scan, join and key calls send what `lbs_cfg_connect`
and `lbs_cfg_add_key` send, in order, and refuse mismatched or unscanned
BSSs before any command; the BPF filter, run by a small interpreter,
takes EAPOL from others only, and the record walk follows `bpf_hdr`; the
EGD reader frames and chunks requests and fails without partial bytes;
the backend, compiled against the pinned release's headers with a fake
driver, queues and reports scans and joins only from its poll, waits for
keys, reports a link end once, keeps a deauthentication from being
undone by a join it waited for or could not wait for, lets a new
association supersede an unreported one, and refuses what the driver
cannot do; the patch applies cleanly to the pinned release and leaves no
plain dump of decrypted key data and no configuration error that echoes a
key; the EGD reader gives up at its deadline on a silent or trickling
service;
the run script refuses a configuration that is not root's alone, holds
more than one network, names another one (a dot in `-A` is literal) or is
not WPA-PSK, refuses without `jitter-egd`, does not count a connection
without CCMP or an RSN join, keeps the PSK out of the
report, stops the supplicant before the station leaves, and its watchdog
stops it before destroying `en6`. The backend tests need the private
release archive and are skipped without it. None of this shows that the
firmware accepts the RSN TLV or installs keys as libertas expects, that
BPF works on `en6`, or how long the EGD wait is on the unit.

**Adversarial review.** Codex (`gpt-6.1-sol`, read-only) reviewed the
phase 6 change set on 2026-10-29 from an isolated copy: the changed
sources, tests and this document, with the pristine 2.11 release and
libertas v6.6 as references; no private files, factory decompilation or
SDK. The report is kept privately as
`<private>/codex-6-review.md`. Each finding was checked
against the code before acting on it. Fixed: a second request could be
queued while one was running, and the completion then carried the newer
number; a completion was visible before the state it left, so the
backend could report a successful join as rejected; `-d` logged message
3's decrypted key data and configuration errors echoed keys, so the
earlier claim here that `-d` logs no key material was wrong for upstream;
an older join's result could be reported under a newer association's
identity; a deauthentication that timed out could be undone by the join
it cancelled; TKIP MIC-failure counts reached the status only on a state
change; the configuration check took `-A` as a pattern, allowed more than
one network, and success did not check the cipher or the RSN join; and
the EGD reader had no overall deadline. Not changed: the up-packet
lifetime at destroy (an up frame's completion arriving after the shim
unloads the driver) is inherited from 5.2. The shim's own trace has it
copy and complete each up frame inside the call, and the run leaves and
waits before the destroy, but that was not observed on the unit. Two
findings are recorded as limits instead: the backend scans one named SSID
(above), and 6.2's deauthentication and restart tests will need a hold
gate that expects link losses, as the current one requires none.

**Risks specific to this phase.**

- The firmware (10.38.1.p25) might handle `ENABLE_RSN`, the RSN TLV or
  `KEY_MATERIAL` differently from the libertas firmware generations;
  libertas' WPA2 support for SD8688 is the only reference.
- Key indexes are not passed (libertas passes none). At a group rekey the
  firmware replaces the one group key, so broadcasts sent under the old
  index just before the change may be lost. Receive counters (RSC) are
  not set either, so replayed group frames are not rejected by the host.
- The supplicant blocks for the EGD read; if `jitter-egd` dies mid-run,
  the next handshake fails rather than falling back.
- `en6` is still not exposed: the `pf` rules of 5.2/5.3 stay in force for
  the run, admitting only the peer's echo requests and DHCP replies.

### Broadcast listener and reconnects (6.2), prepared after 6.1

Built and tested on the workstation, 2026-10-29, after the second 6.1
run left about 10 % of broadcasts lost with no way to tell which.
[`bpf-check`](../../wifi-sta/bpf-check.c) gains a listen mode, `-i en6
-m <factory MAC> -l 9 -w <seconds>` (`cksum 2889311792 13829`). It sends
nothing. For the given time it reads IPv4 frames from others through BPF
and prints each bench broadcast it gets, a UDP broadcast to port 9
carrying `mmi-bench-bcast <n>`, with its number and time, then BPF's own
counts. The Pi sends the numbered broadcasts and logs each send time on
its own clock, the clock `hostapd` stamps its rekeys with, so each
missing number can be placed against the rekeys. The listener's
descriptor sits beside the supplicant's EAPOL one on `en6`; BPF sees what
the driver hands up, before `pf`.

[`radio-reattach.sh`](../../wifi-sta/radio-reattach.sh) gains `-R`
(`cksum 3751876072 71559` as run; `3631679403 71635` after the `grep -o`
fix below), for 6.2's deauthentication and network restart. With `-w` only, the hold then passes when the station is
associated again at its end, and the report logs `connects` and
`link_losses`. Without `-R`, any link loss still fails the hold.

Tests (495 with the `grep -o` check): the listen mode's parser takes the number only from an
Ethernet broadcast of IPv4/UDP to the port with the tag, reads IP options
and stops at a short capture; `-R` passes a hold with link losses if the
station is associated at its end, the same losses fail it without `-R`,
and `-R` without `-w` is refused before any change.

## Phase 6.0 results

One sitting on 2026-10-29, finished by 15:56 UTC (host clock), the car
on, the unit freshly started (uptime 4 min at the first look, `uap0`
holding its factory address, `jitter-egd` running), over `en5`. Nothing
on the radio was touched.

**The supplicant runs.** `wpa_supplicant`, `wpa_cli` and `bpf-check` were
copied to `/fs/tmpfs` and each cksum on the unit matched the build.
`wpa_supplicant -v` and `wpa_cli -v` printed `v2.11` and exited 0.
`/dev/bpf` and `/dev/bpf0` exist.

**The first BPF check failed at the filter.** `bpf-check -i en5` opened
`/dev/bpf`, set the buffer size and attached to `en5`, then failed:
`set-up FAILED at BIOCSETF: Invalid argument`. The unit's `libc.so.2`
`ioctl()` (from the saved library, offline) special-cases terminal and
file commands and `SIOCGIFCONF`, but no BPF command, so `BIOCSETF`
goes to `io-pkt` as an 8-byte `struct bpf_program` whose `bf_insns`
points into the caller. A probe on the unit (kept privately, not part of
the tree) set the filter three ways on a fresh descriptor:

| How | Result |
|---|---|
| `ioctl(fd, BIOCSETF, &prog)` | `EINVAL`, for any program |
| `devctlv(fd, BIOCSETF, …)`, the program then the instructions | Accepted; a drop-all program read 0 bytes from `en5` in 2 s, an accept-all one 4,168 |
| `MsgSendv` of an `_IO_DEVCTL` with `nbytes` 8 and the instructions after the program | The same: accepted, 0 against 7,284 bytes |

So `io-pkt` reads the instructions from the message after the program,
the form a later `libc` builds for this command. `bpfl2_open` now sends
the filter with `devctlv` in that form; the rest of the set-up stays on
`ioctl()`. `bpf-check` and `wpa_supplicant` (which uses the same code for
EAPOL) were rebuilt; the other phase 6 builds are unchanged.

**The second BPF check passed.** With the rebuilt `bpf-check` (`cksum
2139201750 11814`), the ARP request for the workstation was written in
full (60 of 60 bytes), and the reply came back through BPF 3 ms later.
It was the only frame read: BPF counted 2 frames on the descriptor
(`bpf_recv=2 bpf_drop=0`), the station's own request and the reply, and
the filter passed only the reply. Exit 0.

What this does not show: BPF on `en6` (the station driver's interface,
also behind `devnp-shim`), and EAPOL rather than ARP. 6.1 is the first
use of both.

## Phase 6.1 results

One run on 2026-10-29, started at 15:59:18 UTC (host clock), the car on,
right after 6.0 and from the same start-up (unit uptime 14 min at the
start, `uap0` holding its factory address), over `en5`. The Pi ran
`bench-open-ap.sh start -w` (`cksum 1103698690 12407`; WPA2-PSK, CCMP,
default group rekey, no PTK rekey). The configuration was written to
`/fs/tmpfs/wpa-bench.conf` from the Pi's PSK through pipes, as planned
(root, mode 600, 183 bytes). The files in `/fs/tmpfs` matched the builds:
driver `3938534867 64931`, `mv8688-ctl` `3277080864 11450`, probe
`2747790827 77195`, `radio-reattach.sh` `1973069813 70765`,
`wpa_supplicant` `2366746706 491332` and both firmware images. The `-n`
dry run passed first.

**Verdict `OK`.** In order:

| Step | Result |
|---|---|
| Detach, re-mount watch, guard, filter rules | As in 5.3 |
| Mount | `en6` attached, `BROADCAST,SIMPLEX,SHIM` |
| Join | One active scan for `mmi-bench` (0.58 s), one result; JOIN with the 22-byte RSN element (CCMP/CCMP/PSK); associated, AID `0xc001`, `rsn=1`; signal about −80 dBm |
| Handshake | 4-way handshake complete, `PTK=CCMP GTK=CCMP`, `CTRL-EVENT-CONNECTED` 3 s after the supplicant started; `key_sets=2` (pairwise, then group with index 1) |
| Lease | `10.42.88.50` after 9 s; only `10.42.88/24` on `en6` added; no default route, `mam0` route or resolver change |
| Station's pings of the Pi | 56 and 1,472 bytes each answered, but 1 reply of 3 in each run |
| Hold | 60 s associated, `link_losses=0`, `mic_ucast=0 mic_mcast=0` |
| Leaving | `dhcp.client` stopped; `wpa_supplicant` stopped on TERM after 1 s, deauthenticating (reason 3) |
| Teardown, restore | As in 5.3: `en6` destroyed, module idle, no ready magic, rules out, factory attach with `resetmask=0`; watchdog disarmed |

**The handshake.** `hostapd` logged the factory MAC `associated` at
16:00:08.038 UTC (the Pi's clock), message 1 at .045, message 2 received
983 ms later, message 3 at 16:00:09.032, message 4 at .128, then
`pairwise key handshake completed (RSN)` and `EAPOL-4WAY-HS-COMPLETED`.
With WPA2 the group key comes in message 3, so there is no separate group
handshake. On the unit, message 1 arrived through BPF on `en6` 48 ms
before the driver reported the association; the supplicant held it, as
upstream does, and processed it once the association event came. From
message 1's processing to the new SNonce took 736 ms, which is the EGD
read of 32 bytes from `jitter-egd` (the 6.1 plan expected about 1 s).
The keys went to the driver after message 4 was sent, pairwise first and
then the group key, as libertas orders them. The firmware accepted the
RSN element, `ENABLE_RSN` and both `KEY_MATERIAL` commands, and traffic
flowed under CCMP afterwards. So BPF works on `en6` in both directions
for EAPOL, with the `devctlv` filter from 6.0.

**Secrets.** On the unit, none of the run's files (the report, the
`wpa_supplicant` log and the run log) contains the PSK. This was checked
by a count on the unit, without printing the PSK. The supplicant log has 13
key dumps reading `[REMOVED]`, among them `WPA: IE KeyData`, the dump that
upstream would have shown with the GTK at `-d`. The Pi kept
`wpa_psk=<not kept>` in the saved `hostapd.conf`.

**Frame loss towards the station.** The Pi pinged the station during the
hold: 19 of 20 at 56 bytes (7–80 ms) and 7 of 10 at 1,472 bytes (22–118
ms). The Pi's capture has every echo request the station sent and the
Pi's reply to each. The station's own pings saw 1 reply of 3 per run, so
4 of the 6 unicast replies the Pi sent never reached the driver. The DHCP
exchange needed a second discover. Between association and
disassociation the Pi sent 72 frames the station should receive (42
unicast to the factory MAC, 30 broadcast; one mDNS multicast left out),
and the driver counted 55 (`rx_drops=0`, `rx_bad=0`). Every frame the
station sent appears in the capture. 5.3's last run, with the Pi in the
car at about −46 dBm, lost none. Here the signal was about −80 dBm, so
range is the likely cause, not CCMP. The run does not separate the two,
and a run with the Pi back in the car is the check (6.2 needs it anyway
for the counted broadcast test).

### Second run: the Pi in the car, rekeys and counted broadcasts

The owner forced a restart and moved the Pi into the car. One run,
started at 16:29:38 UTC (host clock), from that start-up (unit uptime
3.5 min, `uap0` holding its factory address, `jitter-egd` running). The
files were recopied, and their cksums matched as before. The bench ran
`start -w -g 60 -p 120` (group key every 60 s, pairwise every 120 s).
The run used `-W 200` so that rekeys fell in the hold. `-n` passed
first. **Verdict `OK`.**

| Step | Result |
|---|---|
| Join | Connected 4 s after the supplicant started, `PTK=CCMP GTK=CCMP`, `rsn=1` |
| Lease | `10.42.88.50` after 3 s, one exchange; routes and resolver as in the first run |
| Hold | 200 s associated, `link_losses=0`, no MIC failures |
| Rekeys | `hostapd` completed 5 group key handshakes and 2 pairwise 4-way handshakes with the station; the supplicant installed each new key (`key_sets=9`: 2 at the join, 5 group, 2 pairwise; an unchanged group key is not reinstalled after a pairwise rekey, as upstream does) |
| Pi's pings during the hold | 150 of 150 at 56 bytes (2.6–11 ms) and 30 of 30 at 1,472 bytes (6–10 ms), from 30 s to 180 s after association, across the group rekeys at 78 s and 138 s and the pairwise rekey at 122 s; the other rekeys fell at 18 s, 198 s, 243 s and 258 s, and the station was still associated at each |
| Leaving, teardown, restore | As in the first run |

**Range explains the first run's loss.** With the Pi in the car, no
unicast frame was lost. All 205 unicast frames the Pi sent the station
between association and disassociation came through: every ping, the 12
EAPOL frames (the supplicant logged 12), every ARP and the dnsmasq echo,
each of which the station answered.

**Broadcasts are still lost.** During the hold the Pi sent 60 numbered
UDP broadcasts to `10.42.88.255:9` at 2.4 s intervals. Between
association and disassociation the Pi sent 276 frames the station should
receive, and the driver counted 265 (`rx_drops=0`, `rx_bad=0`). Four of
the missing frames are NetBIOS broadcasts sent 1 s after association,
before the station had the group key (the status after the join counted
only the 3 EAPOL frames). The other 7 frames are 614 bytes in all. The
only mix of the captured frames that both excludes the frames shown
received and matches the driver's byte count is 6 of the 60 numbered
broadcasts and one later NetBIOS broadcast. This assumes the driver's
byte count is the Ethernet length, as in 5.3's last run, where the counts
agreed exactly. That is about 10 % of broadcasts after keying and none of the unicast frames. 802.11
does not retry broadcasts, but this is close range. 5.3's fourth run lost
broadcasts without encryption, and its fifth lost none. The driver counts
frames, not which ones, so it is not known whether the losses cluster at
the group rekeys (where a single group key slot would lose frames sent
under the old key) or are spread out. A receiver on `en6` that logs the
numbered broadcasts through BPF would answer that.

**The first message 1 was dropped once.** Message 1 arrived through BPF
40 ms after the JOIN was sent and 88 ms before the driver reported the
association. The supplicant held it, but upstream processes a held frame
only when the association event comes within 200 ms of it, and logging
at `-d` took the supplicant 280 ms to get there. So it dropped the
frame, and `hostapd` resent message 1 after its 1 s timeout. The first
run made it in 110 ms. This costs a second, not the connection.

Evidence is kept privately in `<private>/live-20261029-phase61b/`.

What the two runs do not show: reconnection after a deauthentication or a
restart of the network, which broadcasts are lost and why, and the phone
hotspot. Evidence is kept privately in
`<private>/live-20261029-phase61/`: the unit's state directory, the
run log and the Pi's logs and capture.

## Phase 6.2 results

One run on 2026-10-29, started at 16:52:05 UTC (host clock), the car on,
the Pi in the car, from a fresh start-up (unit uptime 2 min, `uap0`
holding its factory address, `jitter-egd` running). The files matched
the builds as in 6.1, with `radio-reattach.sh` `3751876072 71559` and
`bpf-check` `2889311792 13829`. The bench ran `start -w -g 30 -p 120`.
The run was `-W 360 -R`, and `-n` passed first. When the hold began,
`bpf-check -l 9 -w 340` started on `en6`. On the Pi, a script then sent
640 numbered broadcasts 0.5 s apart, logging each send time, and pinged
the station once a second 320 times. At 150 s it deauthenticated the
station with `hostapd_cli deauthenticate`, and at 230 s it disabled the
network, then enabled it 5 s later (`hostapd_cli disable`/`enable`, the
same configuration and PSK).

**Verdict `OK`.** Join, lease, pings, leaving and restore went as in
6.1. The hold ended associated, with `connects=3 link_losses=2`, both
link ends with cause `0x8`. The script's line for those counts came out
empty: the unit's `grep` has no `-o`. It now uses `sed`, and a test keeps
`grep -o` out of the script.

**Reconnects.** Both were automatic:

| Event | What happened |
|---|---|
| Deauthentication | The driver reported the link end; the supplicant rescanned, joined and completed a new 4-way handshake 1.9 s later |
| Network down 5.6 s | Link end; the first rejoin, 1.3 s later, was refused while the network was down (`ASSOC-REJECT`); the supplicant kept scanning and was connected again 3.5 s after the network came back |

Two of the Pi's 320 pings were lost in the deauthentication and seven in
the restart (`Destination Host Unreachable` while the station was gone).
None was lost anywhere else, and the RTT stayed at 5–7 ms on average in
every 10 s window.

**Rekeys.** While the station was associated, `hostapd` completed 13
group-key handshakes and 2 scheduled pairwise rekeys with it, besides the
three joins. The supplicant installed every new key.

**Which broadcasts were lost.** The listener got 480 of the 640 numbered
broadcasts (BPF counted no drops). The Pi's capture has 629 of them; the
other 11 were sent while its network was disabled. The losses fall into
four kinds of period:

| Time (s from the first broadcast) | Lost | Notes |
|---|---|---|
| 0–70 and 100–205 | 138 of 351, about 39 % | In runs of 1 to 5, not tied to any event; through rekeys and the deauthentication |
| 70–100 and 205–230 | 2 of 109 | No change on either side is known |
| 230–238.5 | 18 of 18 | The network down and the rejoin |
| 238.5–320 | 2 of 162 | Each sent 80 ms after a group rekey began, before its handshake completed |

So the losses are not caused by the rekeys or the supplicant. The two
after the restart are the expected rekey window: `hostapd` switches its
broadcasts to the new group key when it starts the handshake, and no
station can decrypt them until it has installed that key. With a frame
every 0.5 s, only those two fell in such a window. The rest come and go
in periods of tens of seconds while unicast traffic is untouched. 802.11
does not retry broadcasts, so whatever corrupts or blocks a frame now and
then loses a broadcast but only delays a unicast one by a retry, which at
these RTTs would not show. Arrival times show no buffering. The received
broadcasts' delays drift by about 25 ms over the run, the two clocks'
difference, with no steps of a beacon interval. That points at the radio
channel or a radio sharing it rather than at the station's code. The
periods are long enough to suggest a 2.4 GHz neighbour switching on and
off: the car's own Bluetooth (a separate chip, with media playback seen
in the log earlier that day) or the Pi's combined Wi-Fi/Bluetooth chip.
That is not established. The loss stopped when the network restarted and
did not return in the last 80 s. A run with the car's Bluetooth idle
would test the first explanation. 5.3's open-network runs (one lost
broadcasts, one did not) fit the same picture.

Evidence is kept privately in `<private>/live-20261029-phase62/`: the
unit's state directory, the run log and the listener's log, and the Pi's
logs, capture, send log, ping log and the times of the deauthentication
and the restart.

The owner confirmed that a phone was connected to the car over Bluetooth
and playing audio throughout that run.

### Second run: Bluetooth off

The same test without the reconnects, with the phone's Bluetooth
switched off before a forced restart, so the car had no Bluetooth link.
One run on 2026-10-29, started at 17:13:38 UTC (host clock), from that
start-up (unit uptime 2 min, factory address, `jitter-egd` running), the
Pi in the same place. Files as above, with `radio-reattach.sh`
`3631679403 71635`. The bench ran `start -w -g 30 -p 120`. The run was
`-W 360` (no `-R`), and `-n` passed first. The listener and the Pi's 640
broadcasts and 320 pings started with the hold, as before. **Verdict
`OK`.** The link held the whole 360 s (`connects=1 link_losses=0`),
through 15 group-key handshakes and 3 scheduled pairwise rekeys.

**Nothing was lost.** The listener got all 640 numbered broadcasts, and
the Pi's pings were answered 320 of 320 (2.5–11 ms). Between association
and disassociation the Pi sent 1,031 frames addressed to the station or
broadcast, and the driver counted 1,031 (`rx_drops=0`, `rx_bad=0`). The
3 mDNS multicasts were left out, as in 5.3. No numbered broadcast fell in a group
rekey's window this time: the first after each of the 10 rekeys in the
test was sent 0.20–0.28 s after it began, after its handshake had
completed.

**Conclusion.** The broadcast loss in the first run and in 5.3's fourth
run came from the car's Bluetooth audio, not from the driver, the
firmware's key handling or the supplicant. With it off, the station
received every frame. Bluetooth is a separate chip (CSR BlueCore) in the
same 2.4 GHz band. Unicast frames are retried and
got through with no visible delay. Broadcasts are not retried and about
40 % were lost in the periods when audio was streaming. Only the
on/off comparison establishes this, not how the two radios interact:
whether the factory access point coordinates with Bluetooth in some way
the station driver does not is unknown. For 6.3 this matters, because the
phone giving the hotspot will usually also be the car's Bluetooth audio
source. A lost broadcast costs a DHCP or ARP retry, not the connection.

Evidence is kept privately in `<private>/live-20261029-phase62b/`.

## Phase 6.3 results

One run on 2026-10-29, started at 17:57:59 UTC (host clock), the car on,
from a fresh start-up (unit uptime 3 min, factory address, `jitter-egd`
running), over `en5`. The owner set a temporary random passphrase on
their iPhone's hotspot, with "Maximize Compatibility" on. The passphrase
was generated on the workstation into a private mode-600 file and is to
be changed after the tests. The configuration was written to the unit
from that file through pipes (root, mode 600, 147 bytes). It names the
hotspot as the 4.4 scan heard it, with `key_mgmt=WPA-PSK`, CCMP only and
no PMF. Files as in 6.2's second run, with `radio-reattach.sh`
`3631679403 71635`. The run was `-A <hotspot> -d -E 172.20.10.1 -W 120`,
and `-n` passed first. Whether the phone's Bluetooth was on was not
recorded; it had been switched off for 6.2's second run.

**Verdict `OK`.**

| Step | Result |
|---|---|
| Join | One active scan; the hotspot on channel 6 at about −48 dBm; connected 5 s after the supplicant started, `PTK=CCMP GTK=CCMP`, `rsn=1` |
| Handshake | The EGD read for the SNonce took 1.2 s; message 2 went out 1.27 s after message 1, and the phone's resend of message 1 arrived 12 ms later and was answered too; the handshake completed 1.7 s after the first message 1 |
| Lease | `172.20.10.13` from `172.20.10.1` after 2 s; routes `172.20.10.0/28` on `en6` and `172.20/16` via the phone added; no default route, as `dhcp-up` deletes it |
| Station's pings of the phone | 56 and 1,472 bytes answered; then 30 of 30 at 1,472 bytes (7–65 ms, average 19) and 30 of 30 at 56 bytes (4–66 ms, average 12) |
| Internet through the phone | With a host route for `1.1.1.1` via the phone, added by hand during the hold and removed after it: 5 of 5 echoes answered, 48–80 ms |
| Hold | 120 s associated, `link_losses=0`, no MIC failures; 77 frames received, 78 sent |
| Leaving, restore | As in 6.1; both `172.20` routes left with the lease; no default or `mam0` route came or went |

**The run left a file on the system flash.** `/etc` is a link to
`/mnt/efs-system/etc`, persistent flash (95 % full). `resolv.conf` was
absent before the run and while leased. After `dhcp.client` stopped, it
was an empty file, mode 666, created then. None of the bench runs did
this; the phone's lease is the first with DNS servers. The factory
`dhcp-down` replaces an existing `resolv.conf` with an empty one, but it
runs from `ifwatchd`, which watches `en5` only. Which program wrote the
file, and when it held the phone's servers, was not seen. The file was
removed by hand after the run, which restores the state before it
(0 bytes, with nothing to preserve). No stray `rm` file was left by the
`dhcp-up` line that would make one. `radio-reattach.sh` now does this
itself (`cksum 593401862 72459`). When the baseline had no `resolv.conf`
and one exists after the client stops or at the end of the run, it keeps
a copy in the state directory and removes the file. Two tests cover it:
the removal, and keeping a file that was there before.

**What this shows.** The station joins the owner's phone hotspot with
WPA2-PSK, takes a lease and reaches the internet through it. Without a
default route (the factory `dhcp-up` deletes it) and with no resolver,
only a host route added by hand gets there, as expected. Phase 7 has to
decide the route and DNS handling. No throughput figure was taken: the
unit has no transfer tool (`nc`, `wget`, `ftp` are absent). The pings put
the round trip to the phone at 4–66 ms. Evidence is kept privately in
`<private>/live-20261029-phase63/`.

## Phase 7 plan

Offline planning, 2026-10-29, after the 6.3 run. Nothing in this section
has run on the unit. Source revision: branch `wifi-sta-phase4-host-probe`
at `ba5a590`.

### Owner decisions

Made on 2026-10-29:

- **Purpose: the factory online services.** The hotspot connection is
  for the MMI's own online applications (online POI search, online
  traffic, the other Audi connect services), not only for this
  repository's tools.
- **Trigger: a button on an engineering (GEM) screen.** Station mode is
  started from the car's display, not automatically at start-up.
  Replaced on 2026-10-30: the station starts at start-up, and the GEM
  switches back to the access point
  ([7.6](#station-mode-at-start-up-76-prepared-offline)).
- **Access: SSH from the hotspot.** On the station interface, `pf` passes
  TCP 22 from the hotspot's own subnet and blocks every other inbound
  connection, including Telnet and the Harman service ports.

Going back to the access point needs a restart in every design (phase 3:
a manual re-mount has no SSID). So station mode lasts until the next
restart or power-off, and that restart is its rollback.

### The factory already has a "LAN replaces PPP" mode

Read from saved files only. `lsd.jxe` is the saved IFS copy (SHA-256
`45552250…`, ROM `794685a9…`), decoded with a bounded opcode walk over
the author's unpublished `read_saved_j9_rom.py`'s
method tables. That walk is not a full J9 decoder, so these are the
sequences around each call, not whole methods. `dhcp-up` is the live
copy from 5.3 (`cksum 3046797907 6292`).

- **One flag file switches it.** `DataConnectionUtils.<clinit>` sets a
  static from `new File("/HBpersistence/DLinkReplacesPPP").exists()`, and
  `isLocalNetMode()` returns it. The flag is read once, when the class
  loads, so it takes effect only from the next start-up. The name refers
  to a USB Ethernet (D-Link) adapter that replaces the PPP data link of
  the phone module. `OnlinetrafficDataStateMachine` and `SysApp` test the
  same file themselves.
- **The online applications treat LAN mode as connected.**
  `OnlineConnectionHandler.requestConnect` goes straight to "Connection
  already established" when `isConnectionEstablished()` *or*
  `isLocalNetMode()` is true. It then asks `isConnectionUsageAllowed`,
  which still checks the roaming and confirmation settings, and refreshes
  the map's connection state. `UmtsModelAccess.updateConnectionModeModel`
  pins the connection mode model in LAN mode. `UmtsApplication.requestConnect`
  simulates the connection confirmation "for LAN mode", but only on a unit
  that is not the front main unit, so not on this one. Other callers:
  `MapInterface.refreshDataConnectionState`, `MapRendererManager.updateActiveRenderer`
  (Google Earth), `AbstractStatusBarHandler`, `UmtsDefaultListener`,
  `ModelAccess` (phone) and `NaviSDSPOIOnlineSysCallHandler` (voice POI
  search).
- **`dhcp-up` routes through the lease in LAN mode.** In its MU branch,
  with the flag present, it keeps the default route `dhcp.client`
  installed instead of deleting it. It loads `pf` anchors `dnsRedirect1`
  to `dnsRedirect4`, which redirect DNS that MOST (`mam0`) and access-point
  clients send to the internal resolvers (`10.0.2.100`/`.101` and
  `192.168.2.100`/`.101`) to the lease's name servers. If the lease has
  none, it falls back to `134.60.1.111`. It clears `_CS_RESOLVE`, and
  writes both servers to `/etc/resolv.conf`, which is on the system
  flash. This branch runs after *every* lease, on any interface.
- **`MMI3GMisc` also knows the flag.** Its `IPPresCtrl` has the path next
  to messages that switch the DHCP client's start parameters between
  `-ub` and `-unb` (`-n` drops the server's name servers). Which one LAN
  mode picks was not decoded; 7.1 records the `en5` client's arguments.

So the route and DNS handling that phase 6.3 left open is already built.
A lease taken while the flag exists becomes the default route and the
resolver, and the HMI stops asking the phone module for a data call.

**`en5` alone can test it.** Every capture since 2026-10-25 shows the
factory `dhcp.client -b -i en5` holding a lease from the home router
(`192.168.16.1`). Its routes come from the same `dhcp-up`. With the flag
set, that lease would become the default route through the home
network, with no radio involved. That separates "do the factory online
services still work" from "does the station work", and the first question
is the one that can sink the purpose.

**What the servers say, seen from the workstation.** These are external
observations from 2026-10-29, not unit results. The configured hosts
(`poi.audi-online.com`, `poi.audi-online.de`, `car.audi-online.com`,
`menu.audi-online.de`, `services01.audi-online.de`) all still resolve.
`http://poi.audi-online.com/poi` answers 404. `car.audi-online.com:443`
completes TLS 1.0, 1.1 and 1.2 handshakes, with a certificate for that
name from `VW-CA-PROC-06` (VW PKI) that expired on 2014-10-04. None of
this shows that a request from this car is served. Audi's bulletin
2060681/2 retired the old Google Earth service and excludes MMI3G+
navigation from its replacement
(recorded in the author's head-unit findings),
so Google Earth is not expected to work.

### The engineering-screen button

Read from the saved `efs-system` backup of 2026-10-17. GEM has a factory
`script` element. `Scripts.esd` (SHA-256 `58536bc8…`) and
`OnlineTraffic.esd` use it as a multiline block:

```text
   script
	value		sys 1 0x0100 "/scripts/myScript.sh"
	label		"Start Script"
```

In `AppDevelopment.jar`, `ScriptModel.performAction` writes the string
to engineering key `sys 1 0x0100` through `EngineeringInfoService.storeString`.
`ScriptWidget` refuses a press while a script is still running.
`ScriptOutputReader` waits 2 s for a FIFO and then shows its lines on the
screen. An earlier version of this section said that the Java side never
executes anything and that a native service runs the script. That was
wrong: see [the GEM screen (7.4a)](#the-gem-screen-74a-prepared-offline),
which decodes the runner and the definition directory from the same jar.

### Increments

7.0 **Offline.** This section, plus the tools 7.1 needs. The unit has no
`nc`, `wget` or `ftp`, so a small static `netcheck` is needed. It
resolves a name through the unit's resolver, opens a TCP connection, and
can fetch one HTTP URL, timing it and counting bytes. It answers "is the
route and DNS right" without the HMI, and gives the throughput figure 6.3
lacked. The BPF listener of 6.2 gets a mode that logs DNS query names
and TCP connection attempts on one interface, headers only. That shows
which hosts the HMI tries while the owner uses the online menus.

7.1 **Factory LAN mode over `en5`, no radio.** This is a persistent change,
so it needs the owner's go-ahead. Confirm the flag is absent, create
the empty file `/HBpersistence/DLinkReplacesPPP`, and restart.
Afterwards, record:
- the default route and `/etc/resolv.conf`;
- the `dnsRedirect` anchors, `_CS_RESOLVE` and the `en5` client's
  arguments;
- `netcheck` against the Audi hosts;
- the HMI's online menus while the listener runs.

Then delete the file, restart, and check that the routes and resolver
are as before. `dhcp-up` will have written `/etc/resolv.conf` in LAN
mode. The baseline has none, so the rollback removes it, as
`radio-reattach.sh` does. Pass is the observed facts, either way: which
hosts the HMI contacts, what they answer, and what the HMI shows. A clean
"no service answers" ends the purpose for that service, not the phase.
While the flag is set, the unit routes to the internet through the home
network, and `en5` stays fully open inbound (the phase 2 accepted risk).

7.2 **LAN mode over the station.** Only if 7.1 finds something worth
reaching ([prepared after 7.1](#lan-mode-over-the-station-72-prepared-after-71)).
The flag is set, the unit freshly started, and the 6.3 run on
the phone hotspot. `radio-reattach.sh` currently refuses when the flag
exists (5.3), so this needs an explicit option. Pass: the phone's lease
becomes the default route and resolver through `dhcp-up`'s LAN branch,
`netcheck` reaches the hosts, and the HMI behaves as in 7.1. The run also
gives a throughput figure through the phone. If `en5` also has a lease,
two leases compete for the default route, so this run needs `en5`
without a DHCP server.

7.3 **The mode switch, run over SSH.** `sta-mode.sh start|status`, with
no restore watchdog: once started, the station stays up until the next
restart. Owner decisions after 7.2 added a keeper and a `stop`; see
[prepared after 7.2](#the-mode-switch-73-prepared-after-72) and
[the results](#phase-73-results). Steps:
- Refuse unless `uap0` still has its factory MAC, and keep that MAC.
- Install `pf` rules for `en6`: SSH from `(en6:network)`, the DHCP
  replies, and a block for everything else inbound, all under one
  label. These go in before the interface comes up.
- Detach `uap0`, then mount the station driver.
- Start `wpa_supplicant` in the background with the owner's
  configuration (mode 600, never logged), and start `dhcp.client` with
  `&`.
- Report `OK`, `PARTIAL` or `FAIL`.

The switch is safe to call repeatedly: a lock directory, and a second
`start` only reports status. A failure after the detach leaves no access
point and says so; the restart is the recovery. Reconnects are the
supplicant's job, as in 6.2. `jitter-egd` must be running, as the cold
start of 2026-10-28 showed it may not be, so `start` checks it.

7.4 **The GEM screen.** First, a status-only `script` button, to settle
where added `.esd` files are read from, that the screen renders, what
the runner's user and environment are, and how output reaches the
screen ([prepared as 7.4a](#the-gem-screen-74a-prepared-offline)).
Only then the start button, with `sta-mode.sh` detaching at once
so the GEM console is not held. "Back to the access point" is a restart.
Either the owner's restart key combination does it, or a button does, if
a safe restart command for this unit is found. The start button is
[prepared with 7.5](#the-start-button-and-persistent-files-74b-75-prepared-offline).

7.5 **Persistent installation.** Install the driver, firmware images,
`wpa_supplicant`, `sta-mode.sh` and the `.esd` on writable storage, under
the [persistent-change rules](https://github.com/chundla/qnx632-openssh/blob/main/docs/persistent.md): exact
originals and `cksum`s, a kill switch, and a rollback that does not
depend on Wi-Fi. The hotspot passphrase goes in a mode-600 file on the
HDD. Nothing starts station mode at start-up, so the boot path changes
only by the `.esd` file
([prepared with 7.4b](#the-start-button-and-persistent-files-74b-75-prepared-offline)).

**`en5` in the car (owner, 2026-10-29).** The USB Ethernet adapter goes
to the home router and is connected only when needed. In normal use, the
station is the unit's only lease and its only management path, so SSH
from the hotspot is the access, and a `jitter-egd` failure at a cold
start (2026-10-28) would leave no wired fallback. At home, with the flag
set and both connected, the `en5` and `en6` leases both run `dhcp-up`'s
LAN branch and compete for the default route and `resolv.conf`. 7.2 has
to record which one wins, or `en5` has to be unplugged once the station
is reachable over SSH.

**Upstream corroboration.** dspl1236/MMI3G-Toolkit `e9ca346` uses the same
flag: `modules/usb-net` "lte" mode and `modules/lte-setup` create
`/HBpersistence/DLinkReplacesPPP` for online services over a USB adapter.
That is an upstream report, not a result on this unit.

**Google Earth (upstream workaround), after 7.2.** Upstream's
`modules/google-earth` reports imagery working on MMI3G+ "K0942"
([research/GOOGLE_EARTH_RESTORATION.md](https://github.com/dspl1236/MMI3G-Toolkit/blob/e9ca3469835e3e1c78dabfb5cbe5676d2b752500/research/GOOGLE_EARTH_RESTORATION.md)).
Its parts:
- two fixed-offset patches to `libembeddedearth.so` (`0x343d20`,
  `0x3470a0`);
- a local proxy that serves its own `dbRoot` and authentication
  responses;
- an `/etc/hosts` entry pointing `kh.google.com` at that proxy, which
  forwards tile requests to Google.

It needs only a default route and DNS, which LAN mode over the station
gives it. Before anything is installed:
- match this unit's `libembeddedearth.so` to upstream's exact original
  bytes and checksum (train K0942_3 here; upstream does not name its
  train beyond K0942);
- note that `/etc/hosts` is on the system flash;
- inspect GEMMI's restart and cache behaviour first, because repeated
  exits can reformat its cache.

Only imagery shown on this unit's display counts as a result.

**The `en6` NAT gap.** The factory `pf.conf` translates
`<natRangeTable>` (`192.168.1.0/24` and `10.0.0.101`, a MOST device; the
MU is `10.0.0.100`) only on `ppp0` and `en5`. Traffic from that device
would leave `en6` untranslated and get no replies, so 7.2 needs the
matching `nat on en6` rule, RAM-only, beside the station's other rules.
Whether any online service runs on `10.0.0.101` rather than on the MU was
not established.

### Tools for 7.1, prepared offline

Built and tested on the workstation, 2026-10-29. Nothing here has run on
the unit.

- **[`netcheck`](../../wifi-sta/netcheck.c)** (`cksum 4028533788 14081`
  after the 7.1 display fix below):
  `resolve <name>`, `connect <host> <port>` (prints the local address
  the route chose) and `get http://host[:port]/path`. `get` sends one
  HTTP/1.0 request and prints the status line, header and body bytes,
  time to first byte, elapsed time and rate. The body is discarded,
  stopping at `-m` bytes (default 16 MB). One alarm (`-w`, default 10 s)
  ends the program wherever it is. It prints `_CS_RESOLVE` first,
  because QNX's resolver prefers that to `/etc/resolv.conf`. Exit 0
  done (for `get`, an HTTP status line came, even 404), 2 failed,
  3 deadline.
- **`bpf-check -q`** (`cksum 3506974913 17574`; the 6.2 build is kept as
  `wifi-sta-build/bpf-check-62`): for `-w` seconds, up to 3,600, it
  logs the unit's own DNS queries (name, type, server), their replies
  (rcode, first A record) and TCP connection attempts with their SYN-ACK
  or RST ([`connlog.c`](../../wifi-sta/connlog.c)), headers only. Its
  filter keeps the unit's own frames, which the other modes drop.
  `io-pkt`'s BPF does pass the unit's *sent* frames to a reader (7.1).
  NAT hides the originator: on `en5`, the MU and `10.0.0.101` look the
  same.
- **[`lan-mode.sh`](../../wifi-sta/lan-mode.sh)** (`cksum 222995021 8045`
  after the two 7.1 fixes below):
  - `status`: the flag, who set it, the default route and
    `resolv.conf`.
  - `snapshot <dir>`: bounded reads of the routes, `pfctl -sn`, the four
    `dnsRedirect` anchors, `_CS_RESOLVE`, `ifconfig -a` and the
    DHCP/`ifwatchd` command lines. A read that fails or times out makes
    it `PARTIAL`.
  - `enable`: refuses an unreviewed `dhcp-up` (`3046797907 6292`), an
    RSE marker, under 64 KB free, or a flag it did not set. It keeps a
    copy of any `resolv.conf` and records itself in
    `/mnt/efs-persist/lan-mode/owner` before creating the empty flag,
    then checks it.
  - `disable`: removes only a flag it set. It removes a `resolv.conf`
    that was absent before (keeping a copy) or puts the earlier one back.

  Tests run it under `dash` with fake utilities. That does not show
  that `/bin/ksh` on the unit behaves the same.

`build-driver.sh` builds `netcheck` and the new `bpf-check`. Both link
against only `libc.so.2` and `libsocket.so.2`, with no missing symbols;
that is a link check, not a run. The driver and `mv8688-ctl` rebuilt
byte-identical. 524 tests.

**The 7.1 run**, over `en5` at home, the unit freshly started:

```sh
# copy netcheck, bpf-check and lan-mode.sh to /fs/tmpfs; check each cksum
/fs/tmpfs/lan-mode.sh snapshot /fs/tmpfs/lan-before
/fs/tmpfs/netcheck resolve car.audi-online.com        # expected to fail: no resolver
/fs/tmpfs/netcheck connect 143.164.168.19 443         # expected to fail: no route
/fs/tmpfs/lan-mode.sh enable                          # owner's go-ahead; then restart

# after the restart (tmpfs is empty again: recopy and check)
/fs/tmpfs/lan-mode.sh status
/fs/tmpfs/lan-mode.sh snapshot /fs/tmpfs/lan-after
/fs/tmpfs/netcheck resolve car.audi-online.com
/fs/tmpfs/netcheck connect car.audi-online.com 443
/fs/tmpfs/netcheck get http://poi.audi-online.com/poi
/bin/ksh -c 'trap "" HUP; exec /fs/tmpfs/bpf-check -i en5 -m <en5 MAC> -q -w 900' \
    > /fs/tmpfs/lan-conn.log 2>&1 &
# the owner opens online POI search, online traffic and the other online
# menus, noting what each shows; then the log is collected

/fs/tmpfs/lan-mode.sh disable                         # then restart
/fs/tmpfs/lan-mode.sh snapshot /fs/tmpfs/lan-rollback # compare with lan-before
```

Snapshots and logs go to `<private>/live-<date>-phase71/`.

### LAN mode over the station (7.2), prepared after 7.1

Built and tested on the workstation, 2026-10-29. Nothing here has run on
the unit. Source revision: branch `wifi-sta-phase4-host-probe` at
`70193d5`. 7.1 found one online service that uses the route (Google
Earth), and the planned SSH access and the upstream Google Earth
workaround both need the station's lease to be the unit's route. So 7.2
goes ahead.

**Setup.** At home, with the flag set (`lan-mode.sh enable`, then a
restart) and `en5` cabled to the home router as the management path.
After the restart, `en5`'s lease runs `dhcp-up`'s LAN branch, as in 7.1:
a default route via `192.168.16.1`, `resolv.conf` naming the home DNS
server, and the four anchors pointing at it. A second default route
cannot be added beside the first, so the operator deletes `en5`'s by hand
(`route delete default`, RAM only) before the run. The run refuses to
start while any default route exists. `en5` keeps its connected
`192.168.16/20` route, which is all the workstation's SSH session
needs. A renewal of `en5`'s lease during the run might add its default
route back. The home router's lease time is not known, so the run checks
the route again at the end of the hold.

**[`radio-reattach.sh`](../../wifi-sta/radio-reattach.sh) `-l`**
(`cksum 4127338165 83120`), with `-d`:
- **Preflight.** Requires the flag as an empty file (lan-mode.sh's) and
  no RSE marker. Without `-l`, the flag is still refused, as before.
  Refuses an unreadable routing table or any default route, records the
  four `dnsRedirect` anchors, and keeps a copy of `resolv.conf`.
- **After the lease.** Instead of forbidding route and resolver changes,
  it requires them:
  - the only default route is `default <peer> en6`;
  - every `mam0` route is still there;
  - `resolv.conf` names a server other than `dhcp-up`'s fallback
    `134.60.1.111`;
  - `_CS_RESOLVE` names no server.

  It records the anchors again.
- **NAT on `en6`.** Then it inserts
  `nat on en6 from <natRangeTable> to any -> (en6) round-robin` after
  `en5`'s rule. It does this with `pfctl -N` (the main NAT set only,
  RAM). That load must leave the four anchors' rules and the table's
  entries as they were. Whether `pfctl -N` on this unit does that is not
  known, so it is checked. On any difference, the previous NAT set is
  loaded again and the run is `PARTIAL`. The rule is matched by its exact
  text, because NAT rules here carry no label. It comes out with the
  filter rules after the destroy, or in the watchdog.
- **The hold** (`-W`, up to 600 s) asks for the observations: "run
  netcheck and use the online services now". At its end, the default
  route must still be the lease's alone.
- **Stopping the client** may take the lease's default route with it;
  that alone is not a fault. Any other default route, or a lost `mam0`
  route, still is.
- **The resolver.** `resolver_undo` now also puts back a `resolv.conf`
  that existed before the run (here, `en5`'s), keeping the run's copy. A
  re-created file is mode 666, as `dhcp-up` creates it. It still removes
  one that did not exist before.
- **Left for the restart.** The anchors keep the phone's DNS server, and
  the flag stays set. The report says so; `lan-mode.sh disable` and the
  restart (needed anyway for the access point) roll both back.

Tests (537 in all, from 525): the whole LAN run in order. That is, the
NAT rule present only while the station is up, after `en5`'s; the home
`resolv.conf` back afterwards; and the filter and NAT sets as before.
Also covered:
- refusals before any change: a default route, an unreadable table, a
  missing or non-empty flag, the flag without `-l`, and `-l` without `-d`;
- a dry run that reads only;
- `PARTIAL` for a second default route, a lease without DNS servers, an
  uncleared `_CS_RESOLVE`, a NAT load that empties the anchors (undone)
  and a default route lost during the hold;
- a client stop that takes its default route, which still passes;
- the watchdog removing the NAT rule.

They run under `dash` with fake utilities. That does not show what
`pfctl -N`, `dhcp.client` or `/bin/ksh` do on the unit.

**Throughput.** `netcheck` has no TLS, so it needs a plain-HTTP file.
On 2026-10-29, from the workstation,
`http://ipv4.download.thinkbroadband.com/10MB.zip` answered 200 with
10,485,760 bytes. `speedtest.tele2.net` timed out, and `proof.ovh.net`
redirects to HTTPS. Check the URL again before the run. The download
costs about 10 MB of the phone's data.

**The 7.2 run**, at home, the car on, the iPhone hotspot on, `en5`
cabled; the passphrase configuration is piped to
`/fs/tmpfs/wpa-hotspot.conf` as in 6.3:

```sh
/fs/tmpfs/lan-mode.sh enable                 # owner's go-ahead; then restart
# after the restart: copy devn-mv8688sta.so, mv8688-ctl, mv8688-probe,
# radio-reattach.sh, sd8688_helper.bin, sd8688.bin, wpa_supplicant,
# netcheck, bpf-check and lan-mode.sh to /fs/tmpfs; check each cksum;
# write wpa-hotspot.conf; check jitter-egd is running
/fs/tmpfs/lan-mode.sh status
/fs/tmpfs/lan-mode.sh snapshot /fs/tmpfs/lan72-before
route delete default                         # en5's; RAM only
/bin/ksh -c 'trap "" HUP; exec /fs/tmpfs/radio-reattach.sh -t 1200 \
    -D /fs/tmpfs/devn-mv8688sta.so -K /fs/tmpfs/mv8688-ctl \
    -P /fs/tmpfs/mv8688-probe -H /fs/tmpfs/sd8688_helper.bin \
    -S /fs/tmpfs/sd8688.bin -A <hotspot> -d -l -E 172.20.10.1 -W 600 \
    -w /fs/tmpfs/wpa_supplicant -c /fs/tmpfs/wpa-hotspot.conf' \
    > /fs/tmpfs/radio-run.log 2>&1 &          # -n first
# once the log says "hold: 600s associated; run netcheck"
/bin/ksh -c 'trap "" HUP; exec /fs/tmpfs/bpf-check -i en6 -m <uap0 MAC> -q -w 480' \
    > /fs/tmpfs/lan72-conn.log 2>&1 &
/fs/tmpfs/netcheck resolve car.audi-online.com
/fs/tmpfs/netcheck connect car.audi-online.com 443    # local address: en6's lease
/fs/tmpfs/netcheck -w 120 get http://ipv4.download.thinkbroadband.com/10MB.zip
/fs/tmpfs/lan-mode.sh snapshot /fs/tmpfs/lan72-leased
# the owner opens Google Earth and the online menus, as in 7.1
# after the run's verdict
/fs/tmpfs/lan-mode.sh disable                # then restart
/fs/tmpfs/lan-mode.sh snapshot /fs/tmpfs/lan72-rollback   # compare with 7.1's baseline
```

Pass, as planned: the phone's lease becomes the only default route and
the resolver through `dhcp-up`'s LAN branch; `netcheck` resolves and
connects from `en6`'s address; the download gives a rate; and the HMI
does what it did in 7.1, with Google Earth's connections now leaving
through `en6`. Evidence goes to `<private>/live-<date>-phase72/`.

### The mode switch (7.3), prepared after 7.2

Built and tested on the workstation, 2026-10-29, then
[run](#phase-73-results) on 2026-10-30. Source revision: branch
`wifi-sta-phase4-host-probe` at `81cc6df`. The checksum below is the
prepared script; the run's fixes are in the results.

**Owner decisions, 2026-10-29:**
- **A keeper.** In the car, the station is the only way in, and its
  `sshd` has to listen on the phone's lease. So after the start, a
  keeper stays in the background until the restart. It follows a late
  hotspot and a changed address.
- **A `stop`**, so a session can end without a restart. It answers the
  7.2 wish to end a run early.
- **Refuse an existing default route** (`en5`'s, at home) rather than
  delete it, as in 7.2. In the car, `en5` is absent.

**[`sta-mode.sh`](../../wifi-sta/sta-mode.sh)** (`cksum 280443354 42385`),
`start [-n] [-r dir] [-c config] | status | stop`.
- **Files.** `-r` names the directory with `devn-mv8688sta.so`,
  `mv8688-ctl`, `mv8688-probe`, `sd8688_helper.bin`, `sd8688.bin` and
  `wpa_supplicant`. The default is the script's own directory. The
  configuration (`-c`, default `wpa-hotspot.conf` there) must be root's,
  mode 600, with one network, one `ssid=` and `key_mgmt=WPA-PSK`. It is
  read through pipes, and neither the key nor the SSID is logged. State
  and logs go to `/fs/tmpfs/mmi-sta`.
- **Preflight, before any change** (`-n` stops after it). The checks:
  - the factory driver, `dhcp.client` and `dhcp-up` match their pinned
    `cksum`s, and there is no RSE marker;
  - the LAN-mode flag is set and empty, from `lan-mode.sh`. Station
    mode exists for LAN mode, and only that `dhcp-up` branch was
    reviewed for a lease that should become the route;
  - no `en6` exists, and `uap0` has its factory MAC, which the station
    takes;
  - no default route exists;
  - `jitter-egd` is running, and no `dhcp.client` serves `en6`;
  - no rule of the switch's two labels is loaded. There must be one
    `pass out` rule and one `en5` NAT rule, and no `en6` NAT rule;
  - the probe guard refuses while the factory driver holds the module.

  A session arriving on `uap0` is warned, not refused. It drops at the
  detach, and the owner reconnects over the hotspot.
- **The start**, detached from the session (HUP ignored, as with
  `radio-reattach.sh`). The session follows the log until a verdict, at
  most 300 s. A `mkdir` lock admits one start, and a second `start` only
  prints the status. The steps:
  - keep a copy of `resolv.conf`;
  - load `en6`'s rules under label `mmi_sta_en6`: the DHCP server's
    replies in, everything else on `en6` blocked. If they do not load,
    nothing else is done and `uap0` stays;
  - destroy `uap0`. If it stays, the rules come out and the start fails
    with nothing else changed;
  - check that the guard passes, then mount the station driver with the
    factory MAC, check `BROADCAST` and bring `en6` up;
  - start `wpa_supplicant` with `-t -f` and without `-d`, so the log
    keeps the INFO events (connections, handshakes, rekeys) and grows
    slowly.

  A failure after the detach is `FAIL`. There is no access point then;
  `stop` or a restart is the recovery.
- **The keeper**, every 10 s, logging only on changes:
  - it restarts `wpa_supplicant` if it has exited, at most once a
    minute. Reconnects are the supplicant's own job, as in 6.2;
  - once the link is associated, it starts `dhcp.client -b -i en6`,
    without `-T`, and again if it exits, at most once a minute;
  - for each new lease, it replaces the SSH rule (label `mmi_sta_ssh`):
    TCP 2222 from the lease's subnet to its address, before `en6`'s
    rules. It then starts an `sshd` bound to that address, from the
    installed `sshd_config` with only `ListenAddress` and `PidFile`
    changed, as `en5`'s second `sshd` does;
  - it adds `en6`'s NAT rule (as in 7.2, checking the anchors and the
    table) and puts it back if it goes;
  - it logs the default routes whenever they change, so a competing
    lease shows, but does not fight it;
  - if `en6` disappears, it marks the phase failed and exits.

  It never touches `uap0`.
- **The start's verdict**, once. `OK` when all of these hold:
  - the link is associated, with RSN and a CCMP handshake;
  - there is a lease, and the only default route is `en6`'s;
  - `resolv.conf` names a server other than the `134.60.1.111`
    fallback, and `_CS_RESOLVE` is cleared;
  - the NAT rule is in, and `sshd` listens on the lease.

  Otherwise `PARTIAL` after 120 s, with the missing items listed; the
  keeper goes on. So a start with the hotspot off reports `PARTIAL` and
  completes by itself once the phone is on.
- **`stop`**, also detached, refused while a start is still bringing
  the station up. It stops the keeper, `sshd`, the `en6` `dhcp.client`
  (matched by its arguments, never `en5`'s) and the supplicant. Then it
  disconnects and destroys `en6`, takes out both labels' rules and the
  NAT rule, and puts back the `resolv.conf` from before the start
  (keeping the run's copy), or removes one that was absent. The factory
  driver then goes back only if the guard finds the module idle and
  `-c` finds no firmware: `resetmask=0` first, then `resetmask=4`, with
  `192.168.1.1` on `uap0`. That `uap0` has no SSID: the restart is still
  what brings the access point back. An `en6` that will not go keeps the
  factory driver out and the rules in. Leftover firmware also keeps it
  out, and the recovery is then a full power-off.
- **`status`.** Reports the phase, keeper, link, lease, supplicant,
  client, `sshd`, default routes, resolver, NAT and `jitter-egd`, and
  the last ten log lines. Then `OK`, `PARTIAL` (exit 3) or `FAIL`
  (exit 2), or "not started" (exit 0).

**The SSH port is 2222.** The phase 7 owner decision says TCP 22. Every
`sshd` on this unit listens on 2222, and so does the station's.

**`radio-reattach.sh` stop request** (`cksum 3920899936 83425`). A hold
now ends early, with every end-of-run step, once
`/fs/tmpfs/mmi-radio/stop` exists. Each run clears a stale one at the
start.

Tests (698 in all, from 668): 28 for `sta-mode.sh`. They cover the start
in order and the rules it leaves, the stop and a second start without a
restart, and the resolver both ways. Also covered:
- every refusal, with nothing changed;
- rules that will not load (`uap0` stays);
- a driver that does not attach, then a stop that brings the factory
  driver back;
- an `en6` that will not go, and firmware left after the stop;
- the keeper: a late hotspot, a new lease (moving `sshd` and the rule),
  a supplicant restart, a lost NAT rule and a competing default route;
- an `sshd` configuration that does not test;
- the mask and network helpers, and no utility the unit lacks.

The stop request has one test (with a stale request), which runs in the
static-address and supplicant driver classes. The tests run under `dash`
with fake utilities. They do not show what `/bin/ksh`, `pfctl`,
`dhcp.client`, `wpa_supplicant` or `sshd` do on the unit.

**Not known until the run:**
- whether this `wpa_supplicant` writes its INFO lines to the `-f` file
  without `-d`. The verdict needs the "Key negotiation completed" line;
- what `dhcp.client` does without `-T` when no server answers;
- whether `pfctl` accepts the SSH rule as written
  (`from 172.20.10.0/28 to 172.20.10.13 port = 2222`). The factory's own
  rules use the same forms;
- whether `sshd` binds to the leased address, and how long a TERM to the
  old `sshd` leaves sessions on the old address.

**The 7.3 run**, at home, the car on, the flag set, `en5` cabled. The
passphrase configuration is piped to `/fs/tmpfs/wpa-hotspot.conf` as in
6.3. A second client joins the iPhone hotspot (a laptop with the client
key, or the Pi) for the SSH check:

```sh
# copy devn-mv8688sta.so, mv8688-ctl, mv8688-probe, sd8688_helper.bin,
# sd8688.bin, wpa_supplicant and sta-mode.sh to /fs/tmpfs; check each
# cksum; write wpa-hotspot.conf
route delete default                         # en5's; RAM only
/fs/tmpfs/sta-mode.sh start -n
/fs/tmpfs/sta-mode.sh start                  # follows the log to a verdict
/fs/tmpfs/sta-mode.sh status
# from the hotspot client: ssh -p 2222 root@<lease address>
# the owner opens Google Earth; then turns the hotspot off for a minute
# and on again, and checks status and SSH again
/fs/tmpfs/sta-mode.sh stop
/fs/tmpfs/sta-mode.sh status                 # then restart the unit
```

Pass:
- the start reports `OK`, and SSH from the hotspot reaches the unit;
- Google Earth's traffic leaves through `en6`;
- after the hotspot comes back, the keeper has the link, the lease and
  `sshd` again, without anyone logging in;
- the stop reports `OK`, and the restart brings the access point back.

Evidence goes to `<private>/live-<date>-phase73/`.

### The GEM screen (7.4a), prepared offline

Prepared on 2026-10-29 after the 7.3 run, on branch
`wifi-sta-phase4-host-probe` at `99692da`. Nothing in this section has
run on the unit. It uses the saved `efs-system/lsd/AppDevelopment.jar`
(SHA-256 `ef2a4453…`, the copy in
the author's per-3 sweep notes, not published), decoded with
`javap -p -c`. These are ordinary Java classes, so the decode is complete.
It also uses the saved IFS `etc/system/efs_symlinks.cfg` and the live
listings of 2026-10-22.

**Where the GEM reads definitions.** `ESDFileParser.getEngineeringFiles`
lists `System.getProperty("de.audi.tghu.engineering.base_dir",
"/mnt/flash/efs1/engdefs")` for names ending `.esd`, and `lsd.sh` (line
231) sets `/HBpersistence/engdefs`. The IFS's `efs_symlinks.cfg` has the
line `/mnt/efs-system,/engdefs,/HBpersistence/engdefs`. Every live listing
of `/HBpersistence` taken on 2026-10-22
(`<private>/live-20261022-stage1/precheck*.txt`) shows
`engdefs -> /mnt/efs-system/engdefs`, timestamped within the boot. So the
GEM reads the factory directory itself, and an added screen is one new
file in `/mnt/efs-system/engdefs`. The 2026-10-17 backup has no
`efs-persist/engdefs` because a FAT32 copy cannot hold the link. The
per-3 sweep had concluded from that absence that the directory was
missing and the menu empty. That conclusion was wrong, and those notes
have been corrected.
The owner reading the factory `System` and `/onofftimer` screens on this
unit fits the corrected picture.

**When.** `ScreenRenderer.startParser` starts a "Parser thread" that runs
`ESDFileParser.init` once, when the HMI connects. A new or removed `.esd`
shows only after a restart.

**A bad file does not stop the menu, but a bad `screen` line misplaces.**
`ScreenParser.parseScreens` handles one token line per call, catches the
`ParseException` and logs it, and parsing goes on with the next line. A
failed element is lost. But if a `screen` line fails, the elements after
it attach to the screen parsed before it, and that may be in another
file: the parser's current screen carries across files, and the order of
`File.listFiles` is unspecified. So the file is checked against
[`tools/gem/esd_grammar.py`](../../tools/gem/esd_grammar.py) before it goes
near the unit. The model now covers `script`: `ScriptParser` wants
`value <source> <namespace> <key> "<string>"` (exactly five tokens) and
`label`, and allows `poll`.

**What a `script` press runs.** `SystemAccessor` handles `sys 1` keys:
1 and 2 are the JVM's free and total memory, 3 runs the garbage
collector, and `0x100` (`SYSTEM_SHELLEXEC`) runs a script. The JVM does
it itself; no native service is involved:

1. On QNX, `Runtime.exec("rm script.fifo")` and then
   `Runtime.exec("mkfifo script.fifo")`, each with working directory
   `/scripts`, each waited for. `/scripts` is a start-up link to
   `/mnt/efs-system/scripts`, so each press recreates the FIFO on the
   system flash. `mkfifo` is in the IFS `/bin`.
2. A thread (`SystemAccessor$1`) checks `new File(<string>).exists()`. If
   the file exists, it runs
   `Runtime.exec({"/bin/ksh", "-c", <string> + " >/scripts/script.fifo"})`
   and waits for it. Otherwise it runs
   `echo "Script not found!" >/scripts/script.fifo`.

Consequences:
- The script runs as the J9 process `lsd.sh` started, with that process's
  user and environment. Whether that environment includes `LD_PRELOAD`
  is what the runner check reads.
- The string is a whole path. A button cannot pass arguments: with an
  argument, `exists()` fails and the console shows "Script not found!".
  Hence one link per action.
- Only stdout reaches the FIFO. stderr goes wherever J9's goes.

**How output reaches the screen.** `ScreenRenderer` creates one
`ScriptOutputReader` on `/scripts/script.fifo`, with the console's
96-line buffer (`ConsoleScreen.getBufferSize`). The reader sleeps 2 s,
opens the FIFO and reads lines until end of file, that is, until every
writer has closed it. Until then `isScriptRunning()` is true, and
`ScriptWidget` ignores presses of any script button. Nothing has a time
limit. A script that hangs, or a process it leaves behind with the FIFO
open, holds the console until the next restart. The exit status is
stored but not shown.

Upstream's `research/ARCHITECTURE.md` reported the `Runtime.exec` path and
`/scripts/script.fifo`, and its `ESD_SCREEN_FORMAT.md` the
`/mnt/efs-system/engdefs` location. The saved jar and IFS here agree with
both.

**What 7.4a adds.** Status only; no button changes the radio.

- [`wifi-sta/gem/WifiClient.esd`](../../wifi-sta/gem/WifiClient.esd)
  (`cksum 2799610818 405`): screen `WifiClient` under `Main`, laid out like
  the factory `Scripts.esd`, with two `script` buttons:
  - "Station status" runs `/mnt/efs-persist/wifi-sta/gem/status`;
  - "Runner check" runs `/mnt/efs-persist/wifi-sta/gem/check`.

  The saved factory set has 183 files and 178 distinct screen names, and
  none of them is `wificlient`.
- [`wifi-sta/gem/gem.sh`](../../wifi-sta/gem/gem.sh) (`cksum 1675214043 5649`):
  both links point at it, and it takes the action from its own name. It
  sends stderr to stdout, records the inherited `LD_PRELOAD` and then
  unsets it, bounds every command that could hang, and appends each line
  to `gem.log` beside it. The log keeps one older generation at 32 KB.
  - `status` runs `sta-mode.sh status` (bounded to 90 s) from
    `/mnt/efs-persist/wifi-sta/` or `/fs/tmpfs/`. The verdict comes last,
    because the console shows about 12 rows. Without a `sta-mode.sh`, it
    reports whether `uap0` and `en6` are present.
  - `check` reports the runner: the user (the unit has no `id`, so it
    reads the owner of a new file), the parent's name, `LD_PRELOAD`,
    `PATH`, the working directory, the umask and the FIFO's type. It
    shows an 80-column ruler, and prints two lines 3 s apart, to see
    whether lines appear as they are written. Into the log it writes
    `pidin` for itself and its parent, the parent's `family`, its own
    open files and the exported environment. The open files tell whether
    J9's descriptors reach children. That matters for 7.4b, whose
    background processes must not hold the FIFO.
- [`wifi-sta/gem-install.sh`](../../wifi-sta/gem-install.sh)
  (`cksum 1528029822 10310`) `status | install <dir> | remove`. `install` checks the staged files
  against pinned sums and that `/HBpersistence/engdefs` leads to
  `/mnt/efs-system/engdefs`. It refuses if a definition already names
  `WifiClient`, and needs 64 KB free on both flash volumes. It installs
  `gem.sh` and its links first, and the `.esd` last. In between, it runs
  the installed `gem.sh` directly, as the GEM will: the SSH launcher on
  `efs-persist` has only ever been started through `/bin/ksh`, and a
  failed exec would reach only J9's stderr. It undoes a partial install. `remove` deletes only files with this build's sums, moves the
  logs to `/fs/tmpfs`, and removes the two directories if they are empty.

No factory file changes: the persistent changes are one new file on the
system flash and one new directory on `efs-persist`. Nothing runs at
start-up, and the scripts run only on a press. The rollback is
`gem-install.sh remove` over SSH (on `en5` or `uap0`), followed by a
restart. It needs neither the GEM nor Wi-Fi.

Host checks (`tests/test_wifi_gem.py`) cover:
- the definition's parse, layout, name and pins;
- the factory script screens, which parse under the extended model;
- the scripts' output, timeouts and log, run under `dash` with fake
  utilities;
- install, refusals, undo and removal.

They establish nothing about the unit.

**The 7.4a run.** Owner at the car, ignition on, SSH over `en5` or the
Pi:

```sh
# copy WifiClient.esd, gem.sh and gem-install.sh to /fs/tmpfs/gem; check
# each cksum
ksh /fs/tmpfs/gem/gem-install.sh status
ksh /fs/tmpfs/gem/gem-install.sh install /fs/tmpfs/gem
# restart the unit; open the GEM; Main > WifiClient
# press "Runner check", photograph the console; then "Station status"
ksh /fs/tmpfs/gem/gem-install.sh status      # recopy to /fs/tmpfs first
cat /mnt/efs-persist/wifi-sta/gem/gem.log
```

Pass:
- `WifiClient` appears under `Main`, the factory screens are all still
  there, and both buttons draw;
- "Runner check" fills the console, and its user, `LD_PRELOAD`, parent and
  open files are in `gem.log`;
- "Station status" says `sta-mode.sh` is not on the unit, and that
  `uap0` is present (the tmpfs copies are gone after the restart);
- after each press, the next press works.

A screen that does not appear, or factory screens that go missing, means
`remove` and a restart. Also record:
- whether "A" shows before "B";
- the ruler's last visible column;
- how many console rows show;
- whether the console scrolls.

Evidence goes to `<private>/live-<date>-phase74a/`.

**What 7.4b (the start button) will need, from the above.**
`sta-mode.sh start` already runs its worker with stdin from `/dev/null`
and output to `worker.out`, so the worker does not hold the FIFO. Its
foreground part follows the log for up to 300 s. On the GEM console,
that means live progress, with the buttons blocked until the verdict.
Any process the start leaves behind (the keeper, `wpa_supplicant`,
`dhcp.client`, `sshd`) must not inherit the FIFO, nor any J9 descriptor
the check finds. The station files and the passphrase must be on
persistent storage first (7.5's layout), since the GEM runs after a
restart, when `/fs/tmpfs` is empty.

### The start button and persistent files (7.4b, 7.5), prepared offline

Prepared on 2026-10-29 after [the 7.4a run](#phase-74a-results), on
branch `wifi-sta-phase4-host-probe` at `7f81352`. Nothing in this
section has run on the unit. 7.4b and 7.5 go together, because the
button needs the station files after a restart.

**Choices made here.** The owner can change any of them before the run:
- **The station files go on the HDD**, in `/mnt/persistence/wifi-sta`,
  beside the OpenSSH installation. They come to about 1 MB. The system
  flash had 1,816 KB free in 7.4a, and the HDD had about 158 MB free
  after OpenSSH ([persistent OpenSSH](https://github.com/chundla/qnx632-openssh/blob/main/docs/persistent.md)).
  The passphrase belongs on the HDD anyway (see the hazards). The GEM's
  own files stay where 7.4a put them, on `efs-persist`.
- **One press starts the station.** There is no arming step. The label
  says what it costs, and an accidental press costs the access point
  until the next restart.
- **A kill switch**, `/mnt/efs-persist/wifi-sta/gem/start.disabled`: while
  it exists, the button refuses. It is made over SSH.
- **No stop or restart button.** A restart is still the way back to
  the access point, and no safe restart command for this unit is known.

**[`sta-install.sh`](../../wifi-sta/sta-install.sh)**
(`cksum 3134082493 10816`), `status | install <dir> | conf | remove`:
- It pins seven files. The six binaries and images are the ones the 7.3
  run used. The local builds still match them, and a test checks that
  when they are present:

  | File | `cksum` | Mode |
  |---|---|---|
  | `devn-mv8688sta.so` | `3938534867 64931` | 700 |
  | `mv8688-ctl` | `3277080864 11450` | 700 |
  | `mv8688-probe` | `2747790827 77195` | 700 |
  | `sd8688_helper.bin` | `1538620194 2616` | 600 |
  | `sd8688.bin` | `251454034 259172` | 600 |
  | `wpa_supplicant` | `2366746706 491332` | 700 |
  | `sta-mode.sh` | `2913500418 43139` | 700 |

  `sta-mode.sh` is the version with the stop fixes from after the 7.3
  run, which has not yet run on the unit.
- `install` checks each staged file against its pin. It refuses:
  - while station mode is running (`sta-mode.sh`'s lock exists);
  - if anything is already there that is not absent or this build;
  - if the HDD or the OpenSSH installation (`/mnt/ssh/sbin/sshd`, whose
    `sshd` and `jitter-egd` the station needs) is missing;
  - if less than 2 MB is free.

  It makes the directory mode 700 and copies each file beside its target,
  checking it before and after the rename. It then has `/bin/ksh -n`
  parse `sta-mode.sh`. It writes `SUMS` (`<cksum> <bytes> <name>` per
  file) last, and undoes a partial install.
- `conf` reads the hotspot configuration from stdin, under umask 077,
  into a new file beside `wpa-hotspot.conf`. It checks the file as
  `sta-mode.sh` does: root's, mode 600, one `network={`, one `ssid=`,
  `key_mgmt=WPA-PSK`, and also one `psk=`, at most 4 KB. Only then does
  it rename it into place. Nothing of its contents is printed. A second
  `conf` replaces the first, for a changed passphrase.
- `status` reports each file's sum and mode, `SUMS`, whether the
  configuration would pass (never its contents), `sshd`, whether station
  mode is running, and the free space.
- `remove` deletes `SUMS` first, so the button refuses from then on. Then
  it deletes each file that matches its pin, the configuration and the
  directory. A file that does not match stays, and is reported.

**The start button.** `WifiClient.esd` (`cksum 3567683713 529`) gains a
third button, "Start station (access point off until restart)". It runs
`/mnt/efs-persist/wifi-sta/gem/start`, a third link to `gem.sh`
(`cksum 3629712146 10439`). Factory labels run to 57 characters; this
one has 46. `start` does the following, in order:
1. **Checks that the press is fresh.** Each press recreates the FIFO
   just before the script launches. If the console lost the factory
   race, the launch waits in its redirect until something opens the FIFO
   for reading, and only someone working by hand would do that, maybe
   much later. So `start` compares the FIFO's `ls -l` time with that of
   a new file in `/fs/tmpfs`. It refuses unless they are on the same day
   and at most 2 minutes apart. In 7.4a the FIFO's time was the press
   (00:01 for the first attempt, 00:06 for the press at 00:06:30), and
   stayed 00:01 after the hand-opened read. Whether a write changes it
   was not seen, so this check comes before any output.
2. **Checks the kill switch.**
3. **Checks the files.** Every file in `SUMS` must match its `cksum`
   (each bounded to 60 s). Otherwise it names the file and refuses.
4. **Starts the station.** It runs `/bin/ksh /mnt/persistence/wifi-sta/sta-mode.sh start`
   with stdin from `/dev/null` and its output to a file in `/fs/tmpfs`,
   never to the FIFO. It shows that file's new lines each second, without
   the `sta: ` prefix, until the start exits. Then it shows the exit
   status. After 420 s (the start's own checks take up to about a minute
   and its follower 300 s) it stops following, and says to press "Station
   status" later. The start goes on, and nothing it leaves running can
   hold the console.

`sta-mode.sh` itself is unchanged. Its preflight runs on every press,
so a start that is refused changes nothing:
- **at home** with `en5` cabled, `en5`'s lease is a default route, so the
  start refuses until that route is deleted over SSH;
- **in the car**, `en5` is absent.

`jitter-egd` has to be running. If the cold start of 2026-10-28 repeats,
the start refuses, and the recovery is a restart (the button cannot
restart `jitter-egd`). "Station status" now looks for `sta-mode.sh` in
`/mnt/persistence/wifi-sta` first, then in `/fs/tmpfs`.

**`gem-install.sh`** (`cksum 626865000 10973`) installs the three links.
It recognises the 7.4a build on the unit (`.esd` `2799610818 405`,
`gem.sh` `1675214043 5649`) as `older`. `install` then refuses, and
`remove` takes it out, so the 7.4a build is replaced by `remove`, then
`install`.

**Persistent changes and rollback.** Two kinds:
- **New files only:** the HDD directory and the replaced GEM files.
  Nothing factory changes and nothing runs at start-up.
- **The flag `/HBpersistence/DLinkReplacesPPP`** stays set, as since 7.2,
  because station mode needs LAN mode.

Rollback, over SSH on `en5` or `uap0`, needing neither the GEM nor
Wi-Fi:
- `sta-install.sh remove` and `gem-install.sh remove`, then a restart;
- the kill switch alone stops the button.

Host checks (46 in `tests/test_wifi_gem.py`, 746 in all, from 726)
cover, under `dash` with fake utilities:
- the three-button definition;
- `start`'s refusals: a stale press, one from another day, no FIFO, the
  kill switch, a changed file, no `SUMS`;
- its output and exit status, and that a start which outlives the limit
  leaves the console free;
- `sta-install.sh`'s install, configuration, refusals, undo and removal,
  and that its `SUMS` is what `start` checks;
- the older-build removal.

They establish nothing about the unit.

**Not known until the run:**
- whether opening the FIFO for writing changes its time on QNX. If it
  does, a press released late looks fresh, and the freshness check does
  not protect against it. The 7.4a listings point the other way: the
  time stayed 00:01 after the hand-opened read released the waiting
  script, but that listing may have come before the script wrote;
- whether the driver loads its images from the HDD paths as it did from
  `/fs/tmpfs` (the mount option string is longer);
- whether the console draws the start's lines as they come, or only at
  the end;
- whether a start launched from the GEM's J9 process behaves as one
  launched over SSH. It inherits J9's environment, which `sta-mode.sh`
  resets, and it has no terminal.

**The run.** Install, at home, over SSH on `en5`. The hotspot
configuration is piped from the workstation's private file, as in 6.3.
The passphrase from 6.3 was meant to be temporary, so if the owner
changes it on the phone, the new configuration is the one to pipe.

```sh
# copy the six station files, sta-mode.sh and sta-install.sh to
# /fs/tmpfs/sta, and WifiClient.esd, gem.sh and gem-install.sh to
# /fs/tmpfs/gem; check each cksum
ksh /fs/tmpfs/sta/sta-install.sh status
ksh /fs/tmpfs/sta/sta-install.sh install /fs/tmpfs/sta
ksh /fs/tmpfs/sta/sta-install.sh conf        # stdin: the configuration
ksh /fs/tmpfs/sta/sta-install.sh status
ksh /fs/tmpfs/gem/gem-install.sh remove      # the 7.4a build
ksh /fs/tmpfs/gem/gem-install.sh install /fs/tmpfs/gem
route delete default                         # en5's; RAM only
/mnt/persistence/wifi-sta/sta-mode.sh start -n   # the installed files
# restart the unit
```

Then the button, in two steps:
1. **At home**, after a restart and about 5 minutes' wait, with `en5`
   cabled:
   - press "Station status" ("not started"), then "Start station". The
     start must refuse because of `en5`'s default route, with nothing
     changed, and the next press must work;
   - delete that route over SSH, turn the hotspot on and press "Start
     station" again. Record the console's lines and verdict, SSH from the
     phone to the lease on port 2222, and "Station status";
   - restart; the access point must come back.
2. **In the car**, `en5` unplugged, from a cold start: wait a few minutes,
   press "Station status", then "Start station" with the hotspot on. Check
   SSH from the phone and the online menus; then restart.

Pass:
- the installed files pass `start -n`;
- the refusal at home changes nothing;
- each "Start station" reaches `OK` (or `PARTIAL`, then `OK` from the
  keeper) and SSH works over the hotspot;
- the console is free after each press;
- the access point comes back after each restart;
- `gem.log` holds each press.

Evidence goes to `<private>/live-<date>-phase75/`.

### Station mode at start-up (7.6), prepared offline

Prepared on 2026-10-30, after the [7.4b and 7.5 run](#phase-74b-and-75-results),
on branch `wifi-sta-phase4-host-probe`. Nothing in this section has run on
the unit.

**Owner decisions, 2026-10-30.** These replace the phase 7 trigger
decision ("a button on an engineering screen"):
- **The station starts at start-up**, joining the hotspot without a press.
- **The GEM switches back to the factory access point.** The switch holds
  for every start-up until it is switched again.
- **With `en5` plugged in at start-up** (at home), the automatic start is
  skipped and the access point stays.

The access point comes back only through a restart
([phase 3](#phase-3b-and-3c-results); a manual re-mount has no network
name). So the GEM chooses what the *next* start-up does. It cannot swap
the access point back on the spot.

**The start-up mode** is one marker file,
`/mnt/efs-persist/wifi-sta/gem/client-at-start-up`. When it is present,
the unit joins the hotspot. When it is absent, the car's access point
stays, which is also the default, so a lost or deleted marker means the
factory behaviour. Two new GEM buttons set it:
- "Start-up: join the hotspot" (`client`) writes the marker;
- "Start-up: car access point (restart to apply)" (`ap`) deletes it.

Both check the press time, as the start button does. "Station status"
now begins with the mode and the last start-up's lines from `boot.log`.
`gem-install.sh remove` deletes the marker. Without the screen nothing
could switch back, so removing the screen also ends the automatic start.

**[`sta-boot.sh`](../../wifi-sta/sta-boot.sh)** (`cksum 1916130123 3248`)
is installed on the HDD beside `sta-mode.sh` as an eighth pinned file. It
runs in the background from the OpenSSH start-up, after `jitter-egd` is
ready and the access point's `sshd` is up. It does nothing (the access
point stays) in any of these cases:
- no marker;
- the kill switch `start.disabled`;
- `en5` present after a 60 s settling wait;
- `uap0` without `192.168.1.1` for a further 300 s;
- the marker or kill switch changed during the wait;
- a file that does not match `SUMS`.

Then it runs `sta-mode.sh start`, whose own preflight also refuses before
the detach. Each start-up appends to `/mnt/persistence/wifi-sta/boot.log`,
which keeps one older generation at 32 KB. `sta-mode.sh`'s lines go in
indented.

**The hook.** `sta-install.sh hook` appends one line to
`/mnt/persistence/openssh/start.sh`, after the `en5` line:

```sh
[ ! -x /mnt/persistence/wifi-sta/sta-boot.sh ] || (/bin/ksh /mnt/persistence/wifi-sta/sta-boot.sh > /fs/tmpfs/wifi-sta-boot.out 2>&1 &) || :
```

Before the change, `hook` does the following:
- keeps a copy, `start.sh.pre-wifi-sta`, in the station directory;
- writes `/mnt/efs-persist/wifi-sta-rollback.sh` outside the HDD
  payload;
- has `/bin/ksh -n` parse the new file.

The rollback deletes the marker. It then takes out only that exact line,
and reports whether `start.sh` is back to its checksum from before the
hook. `sta-install.sh unhook` runs the rollback, and `remove` refuses
while the line is in. A second `hook` only reports that it is there.
`install` now completes a set of which some files are already this
build's, so the 7.5 install gains `sta-boot.sh` without a reinstall.

**What stays the same.** The hook is the only change to a file the unit
starts from. It runs only when the OpenSSH start-up reached its end, and
it starts nothing until the GEM's `client` button is pressed. Both the
hook and the marker start out inactive, which is the supervised first
launch the [persistent-change rules](https://github.com/chundla/qnx632-openssh/blob/main/docs/persistent.md)
ask for.

**Failure and recovery:**
- Every refusal before the detach leaves the access point. That includes
  the cold start of 2026-10-28, which lost `jitter-egd`: then OpenSSH
  stops before the hook line.
- A failure after the detach leaves no Wi-Fi until the restart, and the
  next start-up would try again. The recovery is the GEM's "Start-up: car
  access point", then a restart. It needs no network.
- Other ways back: SSH over `en5` (plugging it in at start-up already
  stops the automatic start), `/mnt/efs-persist/wifi-sta-rollback.sh`,
  and the SD-card Telnet.

**Files:**
- `WifiClient.esd` `563898311 762`, five buttons: status, the two
  start-up modes, "Start station now", "Runner check";
- `gem.sh` `201203542 12514`;
- `gem-install.sh` `744049621 11400`. Its older builds now include 7.4b
  (`3567683713 529`, `3629712146 10439`);
- `sta-install.sh` `3475110003 15427`.

The start button's first line now reads "files match SUMS; sta-mode.sh
start follows", because the 7.4b refusal showed that the earlier wording
("starting. The access point goes…") was wrong when the start then
refuses.

Host checks: 61 in `tests/test_wifi_gem.py`, 761 in all. They cover:
- the mode buttons and their held-up-press refusal, and the status's last
  start-up;
- `sta-boot.sh`'s refusals and start, and its log;
- hook, unhook, the rollback on its own (with a later change to
  `start.sh` kept), and the refusal of a `start.sh` that already names
  `sta-boot.sh`;
- completing an older set.

They establish nothing about the unit.

**Not known until the run:**
- whether 60 s after the OpenSSH start-up is late enough for the
  factory networking service. All earlier detaches came at least
  3 minutes after start-up. `NWSProcess` may count a missing `uap0`
  early in its life differently;
- whether the HMI shows a Wi-Fi fault at start-up after the detach;
- how often the iPhone hotspot is visible when the car starts. In 7.3 it
  was not broadcasting until its settings page was opened. The keeper
  waits for it indefinitely.

**The run.** First a restart, to see the access point come back after
7.4b's start. Then, at home over `en5`:

```sh
# copy sta-boot.sh and sta-install.sh to /fs/tmpfs/sta, and
# WifiClient.esd, gem.sh and gem-install.sh to /fs/tmpfs/gem; check each
# cksum
ksh /fs/tmpfs/sta/sta-install.sh install /fs/tmpfs/sta   # adds sta-boot.sh
ksh /fs/tmpfs/gem/gem-install.sh remove                  # the 7.4b build
ksh /fs/tmpfs/gem/gem-install.sh install /fs/tmpfs/gem
ksh /fs/tmpfs/sta/sta-install.sh hook
ksh /fs/tmpfs/sta/sta-install.sh status
# restart the unit
```

1. **At home, `en5` plugged in.** After the restart, `boot.log` must say
   "car access point". Press "Start-up: join the hotspot", then restart.
   `boot.log` must say "en5 is present", and the access point must stay.
2. **`en5` unplugged, hotspot on.** Restart. Within about 3 minutes the
   phone's SSH reaches `172.20.10.13:2222`. Over it, read `boot.log`,
   `sta-mode.sh status` and the start-up timing. "Station status" in the
   GEM shows the same.
3. **Back.** Press "Start-up: car access point" and restart. The access
   point comes back, and `boot.log` says "car access point".
4. **In the car, from a cold start**, hotspot on: as in step 2.

Pass:
- each step's `boot.log` line;
- an automatic join with SSH from the phone;
- the access point back after step 3;
- no HMI fault the owner notices.

Evidence goes to `<private>/live-<date>-phase76/`.

### Start-up from `lsd.sh`, OpenSSH optional (7.7), prepared offline

Prepared on 2026-10-30, after the [7.6 run](#phase-76-results), for a
packaged install from an MMI3G-Toolkit SD card. Nothing in this section has
run on the unit.

**Why.** Through 7.6 the station needed the
[OpenSSH installation](https://github.com/chundla/qnx632-openssh), for three
things: `jitter-egd`, the `sshd` on the lease, and the start-up line in
its `start.sh`. A unit without that installation could not run the
station at all. 7.7 makes all three optional:

- **`jitter-egd`.** `sta-mode.sh start` uses a `jitter-egd` that is
  already running, as before. If none is running, it starts the copy
  beside the station files (qnx632-openssh's build, `cksum 2336099037
  35856`, a ninth pinned file) on port 708. It waits up to 65 s for
  `JITTER_EGD_READY`, as the OpenSSH start-up does, and it does this
  before the `pf` rules and the detach. A failure is a `FAIL` with the
  access point kept. That copy runs until the restart, and a `stop`
  leaves it running for the next start.
- **`sshd`.** SSH is used only while the OpenSSH installation is
  running: `/mnt/ssh/sbin/sshd`, its configuration, and the `sshd`
  account its start-up adds to the RAM `/etc/passwd`. Otherwise the
  preflight says "the station will have no SSH", and the keeper loads no
  SSH rule, so `en6` admits nothing inbound. `OK` then needs no `sshd`,
  and the result line says "with no SSH".
- **The start-up hook** moves from OpenSSH's `start.sh` to a marked
  block in the factory `/mnt/efs-system/lsd/lsd.sh`, as the OpenSSH
  installation's own hook does:

```sh
# BEGIN MMI WIFI-STA
if [ -r /mnt/efs-persist/wifi-sta-boot.sh ]; then
    (trap '' 1; exec /bin/ksh /mnt/efs-persist/wifi-sta-boot.sh > /dev/shmem/wifi-sta-boot.log 2>&1) &
fi
# END MMI WIFI-STA
```

The block goes after OpenSSH's `# END MMI OPENSSH` line if there is one,
otherwise straight after the shebang. The launcher
[`boot-launch.sh`](../../wifi-sta/boot-launch.sh) is on `efs-persist`,
because the HDD may not be mounted yet when `lsd.sh` runs. It waits up to
180 s for `sta-boot.sh` on the HDD, then runs it.

**`sta-boot.sh` waits longer now.** It runs about 90 s earlier than in 7.6,
so it waits for these, in order:
1. `uap0` with `192.168.1.1`, up to 300 s;
2. if OpenSSH is installed (`INSTALL_READY`, not disabled): both of its
   services alive and its start lock gone, up to 300 s. OpenSSH's own
   launcher gives up within about 215 s;
3. the 60 s settle;
4. then the `en5` check, as before.

Step 2 matters for two reasons. Without it, the station could take port
708 before OpenSSH's `jitter-egd`, and OpenSSH's start-up would then fail.
Or the detach could come before OpenSSH has seen `uap0`, and it would skip
its start. With these waits on a unit with OpenSSH, the detach comes when
it did in 7.6: about a minute after OpenSSH is up. If OpenSSH never comes
up, as on the cold start of 2026-10-28, the station now starts anyway,
after 300 s, with its own `jitter-egd` and without SSH. In 7.6 it did not
start at all then.

**`sta-install.sh hook` changes `lsd.sh`.** It first checks that:
- `lsd.sh` begins with `#!/bin/ksh`;
- it parses;
- it names neither `wifi-sta` nor `sta-boot`;
- both flash volumes have at least 64 KB free.

Then it does the following, in order:
1. keeps a copy, `/mnt/efs-persist/wifi-sta-lsd.sh.pre`, and checks the
   copy's sum;
2. writes `/mnt/efs-persist/wifi-sta-rollback.sh`;
3. installs the launcher;
4. builds the new `lsd.sh` beside it, has `/bin/ksh -n` parse it, and
   renames it into place;
5. checks that `lsd.sh` without the block has the sum from before the
   hook.

The rollback first deletes the launcher and the marker. Without the
launcher, the block does nothing even if the edit after it fails. It then
deletes only the lines from `# BEGIN MMI WIFI-STA` to `# END MMI WIFI-STA`.
It reports whether `lsd.sh` is back to its earlier sum. It never copies a
whole file back, so a later change to `lsd.sh`, such as an OpenSSH
reinstall, survives. OpenSSH's own rollback restores the whole
`lsd.sh.pre-openssh`, so it also takes this block out.

**Upgrading the 7.6 install.** The rollback keeps its path,
`/mnt/efs-persist/wifi-sta-rollback.sh`, so `unhook` runs the 7.6
rollback. That takes the line out of `start.sh` (back to `702352968 4246`)
and deletes the marker. `hook` refuses while that line is in. `install`
recognises the first build's `sta-mode.sh` (`2913500418 43139`) and
`sta-boot.sh` (`1916130123 3248`) as `older`, and replaces them in place.
The configuration stays. The six binaries and the firmware are unchanged.
So the upgrade is `unhook`, `install`, `hook`, then the marker written
again if it was there before. The toolkit module's installer does those
steps.

**Files:**
- `sta-mode.sh` `3337595432 45937`;
- `sta-boot.sh` `4201762923 4507`;
- `boot-launch.sh` `898658748 853`;
- `sta-install.sh` `948402866 19745`;
- `gem-install.sh`, `WifiClient.esd`, `gem.sh` and the binaries are
  unchanged.

Host checks (`tests/test_sta_mode.py`, `tests/test_wifi_gem.py`) cover:
- the bundled `jitter-egd` started before the rules and the detach, and
  one that fails keeping `uap0`;
- the station without the OpenSSH installation: no `sshd`, no SSH rule,
  status `OK`;
- the launcher;
- the OpenSSH wait: installed and up, still starting, never up, disabled;
- the block after OpenSSH's, and after the shebang;
- the rollback on its own, with a later change kept, and one that cannot
  write `lsd.sh`;
- refusing an unrecognised `lsd.sh` or too little flash;
- upgrading the first build: its own `sta-install.sh`, taken from git,
  runs `install`, `conf` and `hook`, then this build's `install`,
  `unhook` and `hook` follow.

They establish nothing about the unit. The unit's `sed` and `grep` have
not yet run the `=` line number and `-i -e` forms that `hook` uses.

**Not known until the run:**
- whether the block in `lsd.sh` runs at every start-up, as OpenSSH's does;
- whether the unit's `sed` keeps `lsd.sh` byte for byte outside the
  block;
- the start-up timing with the wait for OpenSSH;
- the bundled `jitter-egd` on a unit without OpenSSH. On this unit, the
  OpenSSH one is always used.

The first three are answered in the [7.7 results](#phase-77-results); the
fourth is still open.

## Phase 7.1 results

One run on 2026-10-29, from 19:20 to 19:45 UTC (host clock; the unit's
clock read 1970), over `en5` to the home router, with the owner in the
car. The unit was freshly started, with the factory MAC on `uap0` and
`jitter-egd` running. Files: `netcheck`, `bpf-check` `3506974913 17574`,
and `lan-mode.sh`, each checked on the unit.

**Two fixes during the run.**
- QNX `df` refuses the symlink `/HBpersistence` ("Block device
  required"). Given `/HBpersistence/`, it prints the filesystem's line
  and then that error. So `lan-mode.sh` now takes the `/dev/` line;
  the flag lives on `/mnt/efs-persist` (24,872 KB free).
- `netcheck` printed the peer as the local address, because `inet_ntoa`
  returns one static buffer. Both fixes have tests. After the rollback,
  a third fix: QNX `date` has no `+%s`, so `disable` named its copy
  `resolv.lan-mode.s`. Copies are now numbered.

**Verdict: LAN mode works as decoded, and the rollback is clean. Only
Google Earth used the connection.**

| Step | Result |
|---|---|
| Baseline | No default route; no `resolv.conf`; `_CS_RESOLVE=nocache_on`; NAT on `ppp0` and `en5` only; the four `dnsRedirect` anchors empty; `en5` client `dhcp.client -b -i en5`. `netcheck`: name lookup fails (`h_errno 2`), connect gives "No route to host" |
| `enable`, restart | Flag set (0 bytes), ownership recorded, `resolv.conf` noted as absent |
| LAN mode | Default route via `192.168.16.1` on `en5`; `resolv.conf` (48 bytes, mode 666) names the home network's two DNS servers; `_CS_RESOLVE` empty; the anchors redirect DNS for `mam0` (`10.0.2.100`/`.101`) and `uap0` (`192.168.2.100`/`.101`) to those servers; `10.184.26/24` gone, and a `10.1/16` route via `10.0.96.1` on `en5` appeared, origin unknown. The `en5` client's arguments did not change |
| `netcheck` | `car.audi-online.com` resolved in 220 ms; TCP 443 connected in 136–150 ms from `en5`'s address. `GET http://poi.audi-online.com/poi` was reset by the server with no bytes; the workstation gets the same reset for the same request, so that is the server's behaviour |
| Online POI | The lists the owner opened were the on-board POI database (screens `navWizardPoiDbClassCatListEntries`, `navWizardPoiDbAreaToppoiPoilist`); online search was not reached |
| Audi connect | Info services: "The page could not be loaded" (`InfoServicesWaitingError`). The online status overview shows data service "No SIM card". **No DNS query or connection** was logged |
| Google Earth | Stays at "Please wait…" (`navMapOnlineSplash`). It looked up `kh.google.com` and connected to port 80 24 times, in pairs about every 6 s, each answered with a SYN-ACK. The HTTP exchange was not captured |
| `disable`, restart | Flag gone; the LAN-mode `resolv.conf` removed (copy kept). NAT, the four anchors and `_CS_RESOLVE` byte-identical to the baseline; `10.184.26/24` back; DHCP processes the same. The routing tables differ only in cached host entries |

**What this shows.** The factory LAN mode is real and complete at the
network level: one empty file gives the MMI a default route, a resolver
and DNS for MOST through whatever lease it has. The Audi connect info
services do not use it. They stop at the phone module's data status ("No
SIM card") and never reach the network, so a working route is not enough
for them. What would make them try was not found. Google Earth does use
the route. From the workstation, `http://kh.google.com/dbRoot.v5` still
answers (200, 16,930 bytes). Upstream reports that the old client's
authentication is what fails and answers it locally
([upstream notes](https://github.com/dspl1236/MMI3G-Toolkit/blob/e9ca3469835e3e1c78dabfb5cbe5676d2b752500/research/GOOGLE_EARTH_RESTORATION.md)).
The stall fits that report, but it was not shown here. The online status screen
displays the car phone's IMEI, which is not recorded here. Evidence is
kept privately in `<private>/live-20261029-phase71/`.

### Google Earth: upstream's binaries against this unit

Offline and read-only comparison, 2026-10-29, after 7.1. Upstream:
dspl1236/MMI3G-Toolkit `e9ca346` and its release assets as listed by the
GitHub API that day. Unit: the saved HDD copy of `/mnt/nav/gemmi` from
2026-10-17, and `cksum` of the live files the same evening. Nothing was
installed or run.

- **The live files are the saved ones.** `libembeddedearth.so`
  (`3288471911 20563703`), `gemmi_final`, `libmessaging.so`,
  `libthirdparty_icu_3_5.so`, `mapStylesWrite`, `run_gemmi.sh` and
  `drivers.ini` have the same `cksum` on the unit as in the backup.
  `/etc/hosts` has only the localhost entries.
- **Upstream's "GEMMI NAR Audi K0942" release (`v1.0-gemmi`) is this
  unit's stock GEMMI.** The SHA-256 digests the API lists for seven of
  its eight assets equal the saved files': `libembeddedearth.so`
  (`c1860dc6…`), `gemmi_final`, `libmessaging.so`,
  `libthirdparty_icu_3_5.so`, `mapStylesWrite`, `run_gemmi.sh` and
  `drivers.ini`. The eighth, `gemmi_models_res.zip` (`5f5dc92d…`), was
  downloaded and unpacked without running anything. Its 79 files (with
  `\` separators) are byte-identical to the unit's `models/` and `res/`,
  with no file missing on either side. The `google-earth` payload's
  `run_gemmi.sh` is the same stock file. So upstream's offsets refer to
  exactly this build. The P0824 VW donor library is a different file
  (20,563,840 bytes) and does not apply.
- **The two patches fit.** At `0x343d20` the unit's library has
  `86 2f 1f c7 96 2f`, and at `0x3470a0` `86 2f 2d c7`: upstream's
  "original" bytes in both places. Upstream's in-browser patcher
  (`tools/ge_patcher.js`) checks them before writing, and refuses a
  mismatch. `ge_deploy.sh` itself does not; it copies whichever
  library is on the card.
- **What the workaround adds is not stock.** `gemmi_proxy`
  (SHA-256 `18e6dbbc…`, 66,328 bytes) is a prebuilt SH QNX executable
  that `run_gemmi.sh` starts before GEMMI, to serve the custom `dbRoot`
  and the cached authentication answers on `127.0.0.1:80`. It needs only
  `libc.so.2`, and `verify_elf.py` finds no missing symbol against this
  unit's copy. That is a link check, not a run. The repository has no
  source for it; it has only a PC-side Python tile proxy. The other
  added files are `dbRoot_custom.bin`, `auth_resp1.bin`/`auth_resp2.bin`,
  `ge_settings.dat`, `gemmi_control.sh` and `gemmi_server.sh`.
- **What the deploy does to the unit:**
  - remounts `/mnt/nav` read-write;
  - force-kills `gemmi_final` and any proxy, which is the GEMMI-exit
    hazard the author's review of the toolkit warns about;
  - deletes and re-copies every GEMMI file, keeping one
    `libembeddedearth.so.orig`;
  - edits `run_gemmi.sh`;
  - strips old Google entries from `/etc/hosts`, which is on the system
    flash.

So the binary-identity gate is met for the library: patching this unit's
own copy at upstream's offsets is exactly what upstream did. The open
items before an install are the sourceless proxy running as root, and
GEMMI's restart and cache behaviour. The author's own loopback proxy and
installer (in the private research repository, not published here) handle
the restart and cache behaviour. On 2026-10-29 they showed imagery on the
unit over `en5` in LAN mode.

## Phase 7.2 results

One run on 2026-10-29, started at 20:25:15 UTC (host clock; the unit's
clock read 1970), at home, the car on, over `en5`. The flag was set with
`lan-mode.sh enable` after a fresh start-up, and the unit was restarted.
After that restart, `en5`'s lease had installed LAN mode as in 7.1: a
default route via `192.168.16.1`, and `resolv.conf` (48 bytes, mode 666)
naming the home DNS servers. `jitter-egd` was running and `uap0` had its
factory MAC. Files as in 6.3, with `radio-reattach.sh`
`4127338165 83120`, `netcheck`, `bpf-check` `3506974913 17574` and
`lan-mode.sh`; all ten were checked on the unit. The configuration was
piped as in 6.3 (root, mode 600, 147 bytes). `en5`'s default route was
deleted by hand, and `-n` passed. The run was
`-t 1200 -A <hotspot> -d -l -E 172.20.10.1 -W 600` with the supplicant.

**Verdict `OK`. The phone's lease became the unit's route and resolver
through the factory LAN branch, and everything was rolled back cleanly.
The HMI did what it did in 7.1: only Google Earth used the network.**

| Step | Result |
|---|---|
| Join | Connected 3 s after the supplicant started, `PTK=CCMP GTK=CCMP`, `rsn=1` |
| Lease | `172.20.10.13` after 2 s. The lease added `default 172.20.10.1 en6`, the only default route, plus `172.20/16` via the phone and `172.20.10.0/28`; every `mam0` route stayed |
| Resolver | `resolv.conf`: `nameserver 172.20.10.1` and `nameserver 134.60.1.111`. The phone's lease names one server, so `dhcp-up` filled the second with its fallback. `_CS_RESOLVE` empty |
| Anchors | `dnsRedirect1`/`2` redirect `mam0`'s and `uap0`'s first internal resolver to `172.20.10.1`; `3`/`4` the second to `134.60.1.111` |
| NAT on `en6` | `pfctl -N` inserted the rule after `en5`'s. The four anchors and `<natRangeTable>` (`10.0.0.101`, `192.168.1.0/24`) were unchanged by the load and by its removal. So on this unit, `pfctl -N` leaves anchor rules and the table alone |
| `netcheck` | `car.audi-online.com` resolved through the phone in 166 ms; TCP 443 connected in 173 ms from `172.20.10.13` |
| Throughput | `GET http://ipv4.download.thinkbroadband.com/10MB.zip`: 10,485,760 bytes in 52.4 s, **about 200 KB/s (1.6 Mbit/s)**, first byte after 158 ms |
| Hold | 600 s; `link_losses=0`, no MIC failures; the default route was still the lease's alone at the end |
| Stop and leave | Stopping `dhcp.client` took the lease's default route and `172.20.10.0/28` with it, and nothing else. `resolv.conf` was put back to `en5`'s (the run's copy kept). The supplicant stopped on TERM, `en6` was destroyed, the module was idle and no firmware was left; the filter and NAT rules came out, and the factory driver re-attached |
| End of the run | No default route came or went over the run; every `mam0` route stayed. The anchors kept the phone's servers until the restart, as expected |

**The HMI, with the connection log on `en6` for 480 s** (49 events, no
BPF drops):
- **Google Earth** stayed at "Please wait…". It looked up
  `kh.google.com` through the phone and connected to port 80 from
  `172.20.10.13`, ten times, in pairs about every 6 s. Each attempt got
  a SYN-ACK.
- **Audi connect info services:** "The page could not be loaded", with
  data service "No SIM card", as in 7.1.
- **Online POI search** (the owner searched "coffee" through Google)
  made no DNS query and no connection.
- Apart from `netcheck` and Google Earth, the log shows no other host.

**Rollback.** `lan-mode.sh disable` removed the flag and the LAN-mode
`resolv.conf` (copy in `/mnt/efs-persist/lan-mode/`), and the unit was
restarted. Afterwards, the flag and `resolv.conf` were absent. The NAT
rules, the four anchors, `_CS_RESOLVE` and the routes (without cloned
and ARP entries) were identical to the snapshot taken before the flag
was set. `uap0` was back at `192.168.1.1` with its factory MAC, and
`jitter-egd` was running.

**What this shows.** The station gives the unit a working internet
connection through the phone. It reaches the factory's route, resolver,
DNS redirects and NAT without any change to factory files beyond the
flag. The phone link is about 1.6 Mbit/s. The online services the owner
chose as the purpose are gated before the network: online POI search
and the info services stop at the phone module's state and never try.
That was so over `en5` in 7.1 and is so through the phone here. Google
Earth is the only service that uses the connection, and it stalls as
before. That stall fits upstream's report that its authentication
fails, which is what upstream's workaround answers. The run also shows
that `pfctl -N` on this unit leaves the anchors and the table alone. A
hold cannot be ended early except through the watchdog, which skips the
end-of-run checks, so 7.3 should accept a stop request. Evidence is kept
privately in `<private>/live-20261029-phase72/`. The SSID is redacted
there and in this document.

## Phase 7.3 results

One run, started on 2026-10-30 at 01:41:26 UTC (host clock; the unit's
clock read 1970), at home, the car on, over `en5`. The LAN-mode flag had
stayed set since 7.2. Files as in 7.2, with `sta-mode.sh`
`537073364 42395` (the prepared script with the fix below), all seven
checked on the unit. The configuration was piped as in 6.3 (root,
mode 600, 147 bytes). `en5`'s default route was deleted by hand, and
`start -n` passed.

**Before the run.** `en5` was missing: its boot log showed the driver
mounted and `192.168.16.11` configured at boot, but by then
`devn-asix-772b.so` was no longer loaded in `io-pkt`. When the adapter
drops off USB, nothing mounts the driver again. Running
`/mnt/persistence/en5/start.sh` again (detached, as the boot hook does)
brought it back. The carrier took about 30 s, and the second `sshd`,
still running from boot, answered once the address was back.

**First start: refused by the unit's shell.** `sta-mode.sh start` with
no options stopped at once with `@: parameter not set`: under `set -u`,
this ksh rejects an empty `"$@"`, which `dash` in the tests accepts.
`start -n` had worked because it passed an argument. Nothing was
changed. The dispatch now passes `${1+"$@"}`, with a test that checks
the text.

**Start: `PARTIAL`, then `OK` from the keeper.**

| Step | Result |
|---|---|
| Detach and mount | Rules loaded, `uap0` gone 1 s after the destroy, `en6` mounted with `BROADCAST`, supplicant started; 6 s in all |
| Verdict | `PARTIAL` after 120 s: not associated. The hotspot was not broadcasting; 230 directed scans found nothing (`results=0`) until the owner turned it on |
| Join | Within 13 s of the hotspot coming on: `rsn=1`, "Key negotiation completed … `[PTK=CCMP GTK=CCMP]`" in the `-f` log without `-d`; about −60 dBm on channel 6 |
| Lease | The keeper started `dhcp.client -b -i en6` on association; `172.20.10.13/28` 15 s later |
| SSH | Rule `pass in quick on en6 inet proto tcp from 172.20.10.0/28 to 172.20.10.13 port = 2222 flags S/SA keep state label "mmi_sta_ssh"` loaded as written; `sshd` listening on `172.20.10.13:2222` 5 s after the lease |
| Route, resolver, NAT | The only default route via `172.20.10.1` on `en6`; `resolv.conf` `172.20.10.1` and the `134.60.1.111` fallback; `_CS_RESOLVE` empty; `nat on en6 from <natRangeTable> to any -> (en6) round-robin` |
| `status` | `OK` |

The owner logged in with Termius on the iPhone that served the hotspot,
to `172.20.10.13:2222` with a second authorized key (see
[persistent OpenSSH](https://github.com/chundla/qnx632-openssh/blob/main/docs/persistent.md)). So the phone
hosting the hotspot reaches its own client, and no second device is
needed. The owner also reported Google Earth imagery working. The only
default route was `en6`'s; the traffic itself was not captured.

**Hotspot off for about a minute, then on.** Nobody logged in. The keeper
logged the link idle, connecting, then associated again (`connects=2`,
`link_losses=1`), 13 s after the hotspot returned. The phone came back
with a new BSSID, and the supplicant completed a new CCMP handshake.
`dhcp.client` kept its pid. A `status` taken right then reported no
lease, and about 45 s after the rejoin `172.20.10.13` was back, with
`sshd` (same pid) and its rule unchanged. The keeper's log has no lease
line for that gap. `status` was `OK` again, and the owner's Termius login
worked again.

**Stop: died partway, then `OK`.** The first `stop` stopped the keeper,
`sshd` and `dhcp.client`. The supplicant then took its TERM, sent a
deauthentication (`reason=3 locally_generated=1`) and logged
`CTRL-EVENT-TERMINATING`. The stop worker then vanished before logging
the supplicant stopped. It left nothing in `worker.out`, no verdict, and
the lock. The system log had wrapped, so the cause is not known: a
signal the shell could not report, nothing in our code. The follower
waited its full 300 s. The remaining steps were run again with
`ksh -x … __stop`, and that stop's verdict was `OK` in 12 s:
- `en6` gone, both labels' rules and the NAT rule out (21 rules, as
  before the start);
- `resolv.conf` put back byte for byte (`2947314810 48`);
- factory driver mounted with `resetmask=0`, `192.168.1.1` on `uap0`,
  and the guard refusing.

Since the run, the stop logs each process before stopping it and logs a
caught INT, QUIT, PIPE or TERM. The follower ends as soon as its worker
is gone without a verdict, instead of waiting out the limit
(`sta-mode.sh` `2913500418 43139`; not yet run on the unit). A test has
the fake supplicant TERM the stop worker (700 tests in all, from 698).

**Restart.** The owner restarted the unit. The access point came back
(the Pi rejoined the car's network and pinged the unit). `jitter-egd`,
both `sshd`s and `en5` came back, and the flag stayed set. At 3 min
uptime `en5` had no lease yet and no default route.

**Not known before the run, now:**
- the supplicant writes its INFO lines to the `-f` file without `-d`: yes;
- `pfctl` accepts the SSH rule as written: yes;
- `sshd` binds to the leased address: yes. How long a TERM to an old
  `sshd` leaves sessions was not exercised: the lease never changed;
- `dhcp.client` without `-T` when no server answers: not exercised. The
  keeper starts it only on association, and the phone answered each
  time.

Pass criteria: the start reached `OK` (through the keeper, after the
hotspot came on), SSH over the hotspot worked, the keeper recovered
from the outage unattended, and the restart brought the access point
back. The stop needed a second run. Evidence is kept privately in
`<private>/live-20261029-phase73/`. The SSID is redacted there in
the copied logs and in this document.

## Phase 7.4a results

First run on 2026-10-29 (local evening), over SSH on `en5`. Evidence is in
`<private>/live-20261029-phase74a/`, including the owner's photos. Unit
clock times count from start-up.

**Before.** The live unit matched the decode:
- `/HBpersistence/engdefs -> /mnt/efs-system/engdefs`, with
  183 definitions;
- `/scripts -> /mnt/efs-system/scripts`;
- a FIFO `/scripts/script.fifo` already present from an earlier factory
  script press;
- 1,816 KB free on `/mnt/efs-system`.

**Install.** `gem-install.sh install` went through: 184 definitions, the
pinned sums, and `gem.sh runs directly` from `/mnt/efs-persist`. Then the
owner restarted the unit.

**Screen.** `WifiClient` appeared and opened. The definition loads and
renders. About a minute after the start-up, the owner pressed a button.
The console showed the GEM's own error screen, `/wificlient/Console/Error
in ScreenRenderer`:

```text
ScreenRenderer().enterConsoleScreen()
java.lang.IllegalThreadStateException: Thread is already started
at java.lang.Thread.start ... ScreenRenderer.enterConsoleScreen
... ScreenRenderer$1.linkClicked ... ScriptWidget.processKey ...
```

A second press gave the same screen.

**Cause, from the jar and the unit.** The cause is a race in the factory
code, not in this repository's files:
1. `ScriptWidget.processKey` calls `linkClicked` (which runs
   `enterConsoleScreen`, starting the reader) before `performAction`
   (which runs the script).
2. The reader sleeps 2 s ("2 secs waited ... (for fifo creation)") and
   then opens `/scripts/script.fifo`.
3. Meanwhile `SystemAccessor.execScript` runs `rm script.fifo` and then
   `mkfifo script.fifo`, each as a process spawned from J9.
4. If the reader's open falls between the two, it throws
   `FileNotFoundException`. `run()` then catches the `IOException`
   without clearing the static instance or `scriptRunning`.
5. From then on, `enterConsoleScreen` gets the dead thread back from
   `getInstance` and calls `start()` on it again: the exception on the
   screen. `ScriptWidget` also sees `isScriptRunning()` as true, so later
   presses never reach `performAction`.
6. The script that was launched waits in its `>` redirect for a reader
   that never comes.

The unit showed exactly this:
- the FIFO had been recreated at 00:01;
- no line from the press was in `gem.log`;
- a FIFO opened by hand from SSH at 00:06:35 received the "Runner
  check" output at once.

`pidin -f aA` at 00:05 had not listed the waiting `ksh`; why it did not
was not established. A press soon after start-up, when spawns from J9
are slow, makes the gap likely. Whether a later press avoids it is not
yet known. Until the next restart, every script button (factory ones
included) stays broken, and the waiting script runs if anything opens
the FIFO for reading.

**What the check reported once unblocked** (`gem.log`):
- **User:** `root`. The script's parent is a `ksh` whose parent is the
  J9 process (pid 229418).
- **Inherited environment:** `LD_PRELOAD=/usr/lib/libmc.so`,
  `PATH=/j9/bin:/proc/boot:/bin:/sbin:/usr/sbin:/usr/bin:/j9/bin`,
  `LD_LIBRARY_PATH=.:/proc/boot:/lib:/lib/dll:/usr/lib:/usr/lib/dll:/j9/bin:/lsd`,
  working directory `/`, umask 022. The exported names are the boot
  environment's (`DSI_*`, `MALLOC_*`, `NV_*`, `EMP_PARAMS` and others);
  only the names were logged.
- **Descriptors:** stdin is a pipe from J9; stdout and stderr (after
  `gem.sh`'s `exec 2>&1`) are the FIFO; fd 10 is the script being read.
  No other J9 descriptor reaches the child.
- **Timing:** "B" followed "A" by 3 s, so `gem.sh` itself writes lines as
  it goes. Whether the console draws them as they arrive was not seen.

For 7.4b this means:
- background processes need `< /dev/null` as well as their output
  redirected (`sta-mode.sh start` already does both);
- `LD_PRELOAD` must be cleared (`sta-mode.sh` does);
- there are no stray J9 descriptors to close.

### Second attempt: a settled unit

After a restart, the owner waited about 5 minutes. Evidence:
`gem-log-2.txt` and photos 5 and 6.
- **Runner check** (pressed at 00:05:47) opened `/wificlient/Console`
  with the check's lines and finished with `check done` at 00:05:55.
- **Station status** (pressed at 00:06:30) worked after it: "sta-mode.sh is
  not on this unit", "uap0 present, en6 absent". A press after a finished
  script works.
- The script's facts matched the first attempt: `root`, `LD_PRELOAD`
  inherited, parent `ksh` under the J9 process (again pid 229418), and
  the same four descriptors.
- **Console:** the 80-column ruler fits on one line. About 11 lines show
  at once, and the up and down arrows scroll the 96-line buffer. Whether
  the console drew "A" before "B" was not observed.

So 7.4a passes. On a settled unit the factory race did not occur. Soon
after start-up it did once. A button that changes the radio has to live
with this, because a lost race leaves its script waiting until something
reads the FIFO. Two consequences:
- 7.4b's start must be harmless if it runs late or never;
- the owner should wait a few minutes after start-up before pressing.

The owner confirmed that all of the factory screens under `Main` are
still there.

## Phase 7.4b and 7.5 results

One run on 2026-10-30, at home, over SSH on `en5`. Evidence is in
`<private>/live-20261030-phase75/`, with the owner's photos. Unit clock
times count from start-up.

**Install (uptime 00:02–00:05).**
- The eleven staged files matched their sums on the unit.
- `sta-install.sh install` put the seven files on the HDD (162,414 KB free
  before, 161,457 after).
- `conf` accepted the owner's new configuration: root, mode 600,
  186 bytes. The owner made it on the workstation with `wpa_passphrase`,
  so the unit holds the derived key, not the passphrase.
- `gem-install.sh` found the 7.4a build `older`, removed it and installed
  the three-button build (184 definitions). The old `remove`'s `rmdir`
  failed although both logs had moved; what was still in the directory
  was not seen, and it did not affect the install.
- With `en5`'s default route deleted, `sta-mode.sh start -n` from
  `/mnt/persistence/wifi-sta` passed every check.

**The buttons, after a restart.**
- "Station status" at 00:02:57: "station mode is not started".
- "Start station" at 00:03:41, with `en5`'s lease present: the press time
  read "00:03, now 00:03". The `SUMS` check took 7 s. Then `sta-mode.sh`
  refused because of the default route, with `exit 2`. `uap0` stayed, no
  `en6` and no lock appeared, and no temporary file was left. The factory
  console race did not occur at 3 to 4 minutes after start-up.
- `en5`'s default route was deleted over SSH at 00:05:02, with the hotspot
  on. "Start station" at 00:05:36 went through these steps:

| Time | Step |
|---|---|
| 00:05:43 | `start -n` checks passed from the HDD files; worker started |
| 00:05:44 | `uap0` gone |
| 00:05:47 | `en6` mounted from the HDD firmware paths, `BROADCAST` |
| 00:06:03 | Associated (`rsn=1`, one scan) |
| 00:06:19 | Lease `172.20.10.13/28` |
| 00:06:24 | `sshd` listening on the lease, port 2222 |
| 00:06:28 | Verdict `OK`; console `exit 0` at 00:06:30 |

The console drew the lines as they came. Long lines are cut at the right
edge, not wrapped. The owner's SSH login from the phone over the hotspot
worked. "Station status" afterwards worked and showed `status OK` with
the recent log, so the console was free after the start.
`sta-mode.sh status` over `en5` agreed: the only default route was
`en6`'s, and `resolv.conf` named the phone's resolver and the fallback.

So 7.4b and 7.5 pass on the unit at home. Settled from the list above:
- the driver loads its images from the HDD paths;
- the console draws progress live;
- a start from the GEM's J9 process behaves as one over SSH.

Not yet seen:
- whether the restart brings the access point back after this run;
- a start in the car with `en5` unplugged;
- whether opening the FIFO for writing changes its time.

The owner then chose to have the station start at start-up; see
[7.6](#station-mode-at-start-up-76-prepared-offline).

## Phase 7.6 results

One run on 2026-10-30, at home. Evidence is in
`<private>/live-20261030-phase76/`, with the owner's photos and the
`boot.log` the owner copied over the hotspot. Unit clock times count from
start-up.

**Before.** The restart after 7.4b's start brought `uap0` back with its
factory MAC and `192.168.1.1`, and no `en6`, so the last open 7.4b item
passed. The access point's network name was not checked from the unit.

**Install, over `en5`.**
- Twelve staged files matched their sums.
- `sta-install.sh install` found the set `partial` and added
  `sta-boot.sh`; the configuration stayed.
- `gem-install.sh` replaced the 7.4b build (`older`) with the five-button
  one. Its `remove` again could not delete the empty-looking directory on
  `efs-persist`, so that has now happened twice. The install was not
  affected.
- `sta-install.sh hook` changed `start.sh` from `702352968 4246` to
  `3755721087 4387`, and the unit's ksh parses it. The copy on the HDD
  matched the original sum, and the rollback script is on `efs-persist`.
  A second `hook` only reported that the line was there.
- `sta-boot.sh` run by hand under the unit's ksh, with no marker, logged
  "car access point … nothing to do".

**The start-ups.** The OpenSSH start-up reached the hook line at 1:33 to
1:35 of uptime every time.

| Start-up | Marker | `en5` | `boot.log` |
|---|---|---|---|
| 1 | absent | plugged in | "car access point … nothing to do" at 00:01:34 |
| (GEM "Start-up: join the hotspot" at 00:03:13) | | | |
| 2, 3 | present | plugged in | 60 s wait, then "en5 is present …; keeping the access point" at 00:02:34 and 00:02:35 |
| 4 | present | unplugged | "starting the station" at 00:02:36, then nothing: this start-up ended before `sta-mode.sh start` returned. Why was not recorded |
| 5 | present | unplugged | Joined; see below |
| (GEM "Start-up: car access point" at 00:06:05) | | | |
| 6 | absent | plugged in | "car access point … nothing to do" at 00:01:34; `uap0` back with `192.168.1.1`, no `en6` |

Start-up 5, with the hotspot on:

| Time | Step |
|---|---|
| 00:01:35 | Hook ran; mode hotspot client, 60 s wait |
| 00:02:37 | `sta-mode.sh start`; preflight passed |
| 00:02:47 | `uap0` gone |
| 00:02:50 | `en6` mounted |
| 00:03:07 | Associated |
| 00:03:22 | Lease `172.20.10.13/28` |
| 00:03:28 | `sshd` on the lease |
| 00:03:33 | Verdict `OK` |

The owner's Termius login over the hotspot worked. "Station status"
showed:
- the mode;
- the start-up's own lines;
- `status OK`: one default route via `en6`, and `resolv.conf` naming
  the phone's resolver and the fallback.

The owner reported no fault on the HMI.

So 7.6 passes at home:
- the automatic join takes about 3.5 minutes from power-on to SSH over the
  hotspot;
- `en5` at start-up keeps the access point;
- the GEM switch back works through a restart.

The detach at 2:47 of uptime, about a minute earlier than any earlier
detach, caused nothing the owner noticed.

Not yet seen:
- the car test: a cold start, `en5` unplugged, away from home;
- why start-up 4 ended early;
- a start-up with the hotspot off, which should give `PARTIAL` and then
  join when the phone appears;
- whether the factory networking service reacts over a long drive.

## Phase 7.7 results

Runs on 2026-10-30 and 2026-10-31, at home, with the build installed by the
MMI3G-Toolkit `wifi-sta` module's SD card (module from this repository at
`11f7001`, then `0e27791`). Evidence is in `<private>/live-20261030-phase77/`:
the card's install log, the owner's photos, the system log and core dump
of the crash, and the start-up log. Unit clock times count from start-up.

**The card install (7.7 build).** The toolkit's installer ran on the
second insertion; the first left no log, so it never started. In order:
- the card's files and the factory driver, `dhcp.client` and `dhcp-up`
  matched;
- the LAN-mode flag was already set;
- `sta-install.sh status` found the 7.6 set `older` and its line in
  OpenSSH's `start.sh`, so `unhook` ran first: `start.sh` went back to
  `702352968 4246`;
- `install` upgraded the set in place and kept the configuration;
- the GEM screen was already this build's;
- `hook` changed `lsd.sh` from `3047168123 11873` to `2750904050 12069`,
  the block after line 6. A dry run here against the saved copy of this
  unit's `lsd.sh` gave the same bytes, so the unit's `sed` and `grep`
  forms behave as the host's for this edit;
- about 570 KB of the HDD and 1 KB of `efs-system` were used.

At every later start-up the `lsd.sh` block ran: `boot.log` has its
"start-up" line at 8 to 10 s of uptime, about 90 s earlier than OpenSSH's
hook ran it in 7.6.

**"Start station now" crashed twice.** Both presses came about 2.5 to
3 minutes after start-up. Each time the start's log stopped at "detaching
uap0", and the follower reported "the worker … ended without a verdict"
3 to 5 s later. `uap0` was gone and `en6` was never mounted, so the unit
had no Wi-Fi until a restart. For the second press, a watcher started over
`en5` saved the system log as soon as the worker ended. It shows:

```text
00:03:14  run fault pid 3752026 tid 1 signal 11 code 1 ip 0x7034e148 mnt/ifs-root/bin/ksh
00:03:15  core dumped for pid 3752026
```

The factory `dumper` keeps cores in `/HBpersistence/core/`. The core puts
the fault in `strcmp` in `libc.so.2` (offset `0x4e148`), called from
`ksh` at `0x080540e6`. That is PD KSH v5.2.14's `error_prefix`, which
compares `source->file` with the script name before printing a built-in's
error. `source->file` was `0x00050008`, not a pointer.

The cause, from the pdksh 5.2.14 source and one check on the unit: `eval`
runs its words through `shell()`, which leaves the shell's current input
source pointing at the eval's temporary record. Only the next top-level
command resets it. On the unit, a `kill -0` error after a function's
`eval` prints without the script's `[line]` prefix, which shows the
switch. A worker is one top-level command that runs until the restart,
and `load_lock` began it with an `eval`. Once that record's memory was
reused, the next built-in error read a freed pointer. Here that was the
`kill -0` in `bounded` when `ifconfig uap0 destroy` ended. Whether the
memory had been reused depends on what ran before, which is why the same
start passed over SSH (once, at 4:37 of uptime, with tracing). The 7.3
stop worker that vanished, and 7.6's start-up 4, which ended before
`sta-mode.sh start` returned, fit the same cause. Neither left evidence,
so this is not shown for them.

`0e27791` writes and reads the lock one variable at a time. There is now
no `eval` in `sta-mode.sh`, and a host test keeps it out of the scripts
that run on the unit. `sta-install.sh` pins the new `sta-mode.sh`
(`2285213847 47091`, installer `2674971188 19774`) and upgrades the 7.7
one as `older`. The toolkit card with that build upgraded the unit in
place. The same press, about 3 minutes after start-up, then gave verdict
`OK`: `uap0` gone in 2 s, associated in 15 s, lease and `sshd` 15 s
later. The system log had no fault and no new core.

**Start-up join, then back to the access point.** With the marker set,
`en5` unplugged and the hotspot on:

| Time | Step |
|---|---|
| 00:00:10 | `lsd.sh` block; mode hotspot client |
| 00:01:23 | "OpenSSH is up (waited 10s)", after `uap0` had its address; 60 s wait |
| 00:02:24 | `sta-mode.sh start` |
| 00:02:33 | `uap0` gone |
| 00:02:54 | Associated |
| 00:03:10 | Lease `172.20.10.13/28` |
| 00:03:20 | Verdict `OK`; "Station status" showed `status OK` |

That is about 13 s sooner than 7.6's start-up 5. "Start-up: car access
point" and a restart, with `en5` plugged in, brought `uap0` back with its
factory MAC and `192.168.1.1`, and `boot.log` logged "car access point …
nothing to do" at 00:00:13.

So 7.7 passes at home with OpenSSH installed:
- the toolkit card installs and upgrades;
- the `lsd.sh` block runs at every start-up;
- the start-up join takes about 3 minutes 20 s to the verdict;
- the GEM's start button works after the `eval` fix.

Not yet seen:
- the bundled `jitter-egd` and the station without OpenSSH, on a unit;
  this unit always uses OpenSSH's;
- the car test: a cold start away from home, a start-up with the hotspot
  off, and a long drive.

## Hazards specific to this unit

- **Loss of access.** SSH and Telnet both depend on `uap0`. The chip runs one
  firmware at a time, so station mode removes the access point. A management
  path that doesn't depend on Wi-Fi must work before any live experiment.
  USB Ethernet on `en5` is the candidate: `pf` passes `en5` fully. It has
  not been verified on this unit. In the saved IFS, `umass-enum_def.cfg`
  (`f4ece829…25bba399`) and `umass-enum_hub.cfg` have identical
  USB-Ethernet entries. `vdev-medialaunch` runs with `-f
  /etc/umass-enum.cfg`; the live copy of that file was not checked. The
  entries mount `devn-asix.so` for `0b95:1720`, `0b95:7720` (AX88772),
  `0846:1040`, `2001:3c05` and `2001:1a02`, with `speed=10 duplex=1` and
  `netif=5`, and assign the static address `172.16.250.248/16`. AX88772A/B
  (`0b95:772a`, `0b95:772b`) and AX88179 are not listed. There is no DHCP
  server on `en5`; the peer needs a static address in `172.16.0.0/16`.
  `devn-asix.so`'s own device table (data `0x10b74`, 16-byte entries:
  vendor, device, chip code, flags) has `2001:1a00`, `2001:3c05`,
  `2001:1a02` (chip `88772b`), `07d1:3c05`, `0b95:1720`, `0b95:7720`,
  `0b95:772a` (chip `88172a`), `1737:0039`, `0846:1040`, `13b1:0018` and
  `077b:2226`. A generic AX88772B (`0b95:772b`) is in neither list. On
  2026-10-24 one was plugged in, and the Pi's `eth0` showed no carrier.
  Mount options cannot add an ID; see
  [USB Ethernet driver matching](#usb-ethernet-driver-matching).
- **Rollback needs a restart, not a radio reset.** Measured in
  [phase 3c](#phase-3b-and-3c-results): a detach clears the firmware on its
  own, so every re-attach downloads it again, and the `resetmask=4` reset
  line is not needed for that. What a manual re-attach cannot restore is the
  MAC address and the BSS, which only the networking service's own mount path
  programs, so returning to a working access point means restarting the unit.
- **A `resetmask=4` detach leaves the radio held off.** Bit 2 of FPGA system
  register 8, which `dev-sysregs` names `WlanEnable`, is the driver's MMI 3G+
  default (`resetmask=4`); the factory mount overrides it with `resetmask=0`
  for a reason still unknown. With the bit set the card does not answer SDIO
  at all, and a `resetmask=0` mount can neither assert nor release it — so it
  cannot recover a card another instance left in reset. Any restore, and the
  watchdog, must use `resetmask=4`. See
  [Power, reset and attach](#power-reset-and-attach).
- **Bluetooth.** Factory configuration puts Bluetooth on a separate CSR chip
  with its own UART and reset line (offline evidence, see
  [Radio enable line and Bluetooth](#radio-enable-line-and-bluetooth)). Phase 3
  still checks that phone calls survive a Wi-Fi reset.
- **Shared stack.** Never restart `io-pkt-v4-hc`; it carries MOST IP and
  `pf`. Detach only the Wi-Fi interface. The driver has a full detach routine
  (see [Detach](#detach)), and `dev_detach_name` is the path that reaches it;
  that it does so on this unit is unverified. Note that `dev_detach`
  quiesces the entire stack while the detach runs, so `mam0` and the `en5`
  management link stall with it for up to about 16 s.
- **Networking-service contention.** `NWSProcess` mounts and monitors the
  access point and counts crashes. The HMI may retry or mark Wi-Fi failed.
  Switching Wi-Fi off in the HMI does *not* detach the driver — it stops the
  BSS and leaves everything mounted ([phase 3a](#phase-3a-results)) — so
  taking over the radio means detaching it under a service that still
  believes it owns the interface. `dev-irc` also filters Wi-Fi slog messages
  (`SGIrcSLogWLanFilter`, `Unable to init /lib/dll/devnp-mv8688uap.so`,
  `WLAN initialisation failed`). What it does on a match was not decoded.
- **Firewall exposure.** The factory rules name `uap0` only. A new interface
  name falls through to `pf`'s default pass, which would expose Telnet (23),
  8100, 2021, 4444 and other listeners to every hotspot client. Add explicit
  blocks before bringing the interface up.
- **Routing and DNS.** `dhcp.client` replaces the default route and
  `resolv.conf`. Factory `dhcp-up`, `/HBpersistence/DLinkReplacesPPP` and
  ppp/en5 routes interact with this; confirm `mam0` routes survive.
- **Secrets.** The hotspot passphrase belongs in a mode-600 file on the
  unit's persistent HDD. Keep it out of the repository, SD payloads and logs.

## Plan and gates

Each phase completes, with evidence recorded, before the next starts.
Phases 0–1 change nothing on the unit.

0. **Offline. Done 2026-10-23; see [Phase 0 results](#phase-0-results).**
   Recovered:
   - the `hbsdc` register map and attach, power and reset sequences;
   - the firmware-status check and the detach path;
   - the libertas SD8688 protocol match and the station command map.

   The hardware-free stub links against the unit's libc. The Bluetooth path
   was resolved offline later the same day (separate CSR chip), and phase 1
   answered the interrupt (56) and the `log()` streams. Still open offline:
   - what `dev-irc` and `NWSProcess` do on a Wi-Fi failure.

   The firmware's capability response needs hardware (phase 4).
1. **Read-only live capture. Done 2026-10-23; see
   [Phase 1 results](#phase-1-results).** Not obtained: the boot-time slog
   (buffer wrapped) and `PSSBSSProcess` open files (`pidin` hangs). To
   repeat:

   ```sh
   # the author's read-only capture tool; not published
   MMI_JUMP_SOURCE=192.168.1.109 python3 collect_live_vehicle_reads.py \
       --stage wifi <private>/live-<date>-wifi
   ```

   `--only <name>` repeats individual reads.

   The `wifi` stage uses the collector's bounded, read-only wrapper over
   [OpenSSH](https://github.com/chundla/qnx632-openssh/blob/main/docs/persistent.md). It records:
   - `io-pkt-v4-hc` arguments, loaded objects (`mem`), attached interrupts
     (`irqs`, which answers 13 versus 56) and open files (`fds`, where
     stdout and stderr go);
   - `NWSProcess` and `PSSBSSProcess` open files (Bluetooth on
     `/dev/serfpga1`);
   - `/dev/io-net`, `/dev/sysregs`, `/dev/serfpga*` and `/dev/bpf*` nodes,
     and the current `WlanEnable` and `BtReset` values read through
     `dev-sysregs`;
   - `ifconfig -a`, `netstat -rn`, `pfctl -s rules` and the Wi-Fi and
     networking lines of `sloginfo` (filtered on the unit);
   - listings and `cksum` of the driver, shim, `devn-asix.so`, both
     firmware files and `dhcp.client`;
   - the effective `nws.cfg`, `hostapd.conf`, `pf.conf`,
     `dhcpd.uap0.conf`, `dhcp-up` and `resolv.conf`. Lines naming a
     passphrase, PSK, key or secret are redacted on the unit before
     transfer; `cksum` covers the unredacted bytes.

   The unit's `cksum` matches POSIX `cksum` (checked on `lsd.jxe`), so
   results compare directly with the factory and saved copies cited above.
2. **Management path. Done 2026-10-27; see [en5 established](#en5-established).**
   SSH over USB Ethernet (`en5`) is up and independent of `uap0`, and the
   driver, interface and a second `sshd` are installed to start at boot. The
   blocker was the AMI cable's identification resistor, not the driver.

   The `pf` rule was **not** scoped: the owner chose to leave the factory
   `pass in quick on en5 all keep state` in place. Telnet and the Harman
   service ports therefore answer on the wired side whenever the unit is
   powered. That is an accepted risk on the owner's own network, not a
   general recommendation, and it is the one part of this gate that did not
   meet the original intent.
3. **Reversible radio control on the access-point firmware.** Prepared
   offline on 2026-10-27; see [Detach and firmware
   observability](#detach-and-firmware-observability). Nothing in this gate
   has been run on the unit yet. The original pass criterion — "the driver
   logs a download, not `Skip FW download`" — is not usable: those messages
   reach the serial console only. The gate was restructured into four steps,
   in this order, each with the owner's go-ahead:

   3a. **HMI Wi-Fi off, then on. Done 2026-10-28; see [Phase 3a
   results](#phase-3a-results).** The switch issues only the uAP BSS stop and
   start: the driver stays mounted, `uap0` keeps its address, `dhcpd` keeps
   running, `WlanEnable` is never touched and no firmware is reloaded. So the
   HMI can neither free the radio nor restore a configured access point, and
   **the rollback for 3b and 3c is a restart**. That start-up also provided
   the 425 ms reference for an attach that did download firmware.

   To repeat it: one sample before the toggle, one with Wi-Fi off and one
   after switching it back on. The capture has to use the wired path: the
   Wi-Fi one goes away with the access point.

   ```sh
   for phase in before off after; do
       MMI_SSH_HOST=<en5 address> python3 collect_live_vehicle_reads.py \
           --stage wifi-toggle <private>/live-<date>-toggle-$phase
   done
   ```

   `sysregs-radio` answered one question on its own: `WlanEnable` stays
   `0x00000000` with Wi-Fi off, so the factory software does not drive that
   reset line, and phase 3c still has to.

   3b and 3c. **Manual detach and the firmware probes. Done 2026-10-28; see
   [phase 3b and 3c results](#phase-3b-and-3c-results).** `destroy` reaches
   the driver's detach, `NWSProcess` does not re-mount, and the detach alone
   clears the firmware, so every re-attach downloads it again (458 and
   479 ms against the 425 ms reference) and the reset line is not needed for
   that. The line is nevertheless real: with bit 2 of system register 8 set
   the card does not answer SDIO, and a `resetmask=0` mount cannot clear it.
   `mam0`, `en5` and Bluetooth were unaffected.

   The factory parameters were then shown to be a sufficient re-attach on
   their own, from a fresh start-up's `resetmask=0` instance (`-F` mode,
   452 ms, firmware downloaded), which completes 3c.

   3d. **Full power-off cycle.** Confirm it restores the factory state with
   no other recovery step, and that Bluetooth and phone calls are unaffected
   by 3a–3c. **Radio side done 2026-10-28** (see
   [phase 3d results](#full-power-off-cycle-3d)); Bluetooth and calls not yet
   checked. That start-up lost SSH to an entropy-daemon failure. A restart
   is now known to be *required* after 3b/3c, because a manually mounted
   `uap0` has no MAC and no BSS.

   [`wifi-sta/radio-reattach.sh`](../../wifi-sta/radio-reattach.sh) performs
   3b and 3c as one RAM-only run: it pins the factory driver's `cksum`,
   refuses to start from a session arriving on `uap0`, requires `en5` with
   carrier, bounds every diagnostic, arms a dead-man watchdog that re-mounts
   with `resetmask=4` if the script dies, stops if the networking service
   re-mounts the driver first, and reports `OK`, `PARTIAL` or `FAIL`. `-n`
   runs the preflight only. Tests: `test_radio_reattach` (fake QNX
   utilities under dash; it does not show that `/bin/ksh` on the unit
   behaves the same, nor anything about the radio).
4. **Station firmware bring-up, no association.** Load `sd8688.bin` with
   the new driver. Read HW spec and firmware version, then run a passive
   scan. Detach and restore the access point (phase 3 path). Record any
   timeout or failure. Broken into increments 4.0–4.5, with the options
   ruled out and the risks, in the [phase 4 plan](#phase-4-plan). The 4.1
   host bring-up probe is built and tested offline as a standalone process
   (`mv8688-probe`, run through `radio-reattach.sh -P`); see
   [Host bring-up probe](#host-bring-up-probe-41-prepared-offline). 4.0, the
   guard and 4.1 passed on the unit on 2026-10-28; see
   [their results](#phase-3d-40-and-41-results). The 4.2 download is built
   and tested offline (`radio-reattach.sh -P … -H … -S …`); see
   [Firmware download probe](#firmware-download-probe-42-prepared-offline).
   4.2 passed on the unit with the station image on 2026-10-28; see
   [Phase 4.2 results](#phase-42-results). The 4.3 command layer
   (`CMD_FUNC_INIT`, `GET_HW_SPEC`, `MAC_CONTROL`, `CMD_FUNC_SHUTDOWN`) is
   built and tested offline (`radio-reattach.sh -P … -H … -S … -C`); see
   [Command layer probe](#command-layer-probe-43-prepared-offline). 4.3
   passed on the unit on 2026-10-28; see
   [Phase 4.3 results](#phase-43-results). 4.4 (the access point's factory
   MAC set on the station, then a passive scan of channels 1–11) is built
   and tested offline (`radio-reattach.sh -P … -H … -S … -C -L`); see
   [Passive scan probe](#passive-scan-probe-44-prepared-offline). 4.4
   passed on the unit on 2026-10-28: the station took the MAC and heard the
   owner's hotspot; see [Phase 4.4 results](#phase-44-results). 4.5, the
   PIO throughput check, is built and tested offline (`radio-reattach.sh
   -P … -H … -S … -C -T`); see
   [PIO timing probe](#pio-timing-probe-45-prepared-offline). It ran on
   the unit on 2026-10-28. TX by PIO projects to about 3.4–3.7 MB/s per
   full frame and RX to about 1.5–1.8 MB/s, below a hotspot's 2–3 MB/s.
   Phases 5 and 6 proceed on PIO, with DMA left as a throughput item; see
   [Phase 4.5 results](#phase-45-results).
5. **Open-network association and DHCP.** Join a bench open access point.
   Apply `pf` blocks first, then `dhcp.client -u -T`. Check routes, DNS and
   `mam0`, then restore. Split into 5.0–5.3 in the
   [phase 5 plan](#phase-5-plan); the bench access point is the Pi (owner
   decision, 2026-10-29). 5.0, the open association from the standalone
   probe (`radio-reattach.sh -P … -H … -S … -C -L -A <ssid>`), is built
   and tested offline; see
   [Open association probe](#open-association-probe-50-prepared-offline).
   5.0 passed on the unit on 2026-10-29: the station associated with the
   Pi, held the link for 15 s and deauthenticated, after fixes for the
   `0x8012` association response and a passive scan that missed a weak
   access point; see [Phase 5.0 results](#phase-50-results). 5.1, ARP and
   ICMP echoes from the probe (`… -A <ssid> -I <address> -E <peer>`), is
   built and tested offline; see
   [Data frame probe](#data-frame-probe-51-prepared-offline). 5.1 passed
   on the unit on 2026-10-29 at the first run: ARP and all 14 echoes
   (1–7 block writes) answered and checked, matching the Pi's capture; see
   [Phase 5.1 results](#phase-51-results). 5.2, the io-net driver
   `devn-mv8688sta.so` run through `radio-reattach.sh -D …`, is built and
   tested offline; see
   [Station driver](#station-driver-52-prepared-offline). 5.2 passed on
   the unit on 2026-10-29 at the first run: `en6` attached inside
   `io-pkt`, associated, answered 30 of 30 pings from the Pi during a
   60 s hold, and was destroyed with `mam0` and `en5` up throughout; see
   [Phase 5.2 results](#phase-52-results). 5.3, the factory
   `dhcp.client` on `en6` (`radio-reattach.sh -D … -d -E <peer>`), is
   built and tested offline; see
   [DHCP on the station](#dhcp-on-the-station-53-prepared-offline). Its
   review of the factory `dhcp-up` found that the script deletes the
   default route after every lease, which matters for phase 6. The first
   5.3 run on 2026-10-29 got no lease: `en6` lacked `IFF_BROADCAST`
   because the driver sent no io-net `DL_ADVERT`, so no request left the
   stack. The driver now sends one. The second run took a lease and set
   the address within a second, but `dhcp.client -u` never left the
   foreground. The third run showed it never daemonizes: it is meant to be
   started with `&`, which the fourth run does. The fourth run's poll
   mistook the client's interim `0.0.0.0` for the lease, and it showed
   that broadcast DHCP replies are sometimes not received. 5.3 passed at
   the fifth run on 2026-10-29. It leased `10.42.88.50` in 3 s with no
   default-route, `mam0` or resolver change, and answered 30 of 30 pings
   from the Pi during the hold. Its client stopped with a `DHCPRELEASE`,
   and the restore was clean. All 26 broadcasts were received that time.
   See [Phase 5.3 results](#phase-53-results).
6. **WPA2-PSK.** Port `wpa_supplicant`: minimal configuration, internal
   crypto, BPF l2_packet, `driver_mv8688`. Test with the phone hotspot.
   Measure throughput and reconnects. Split into 6.0–6.3 in the
   [phase 6 plan](#phase-6-plan): 6.0 runs the supplicant and checks BPF on
   `en5`, 6.1 joins the Pi's WPA2-PSK bench network, 6.2 covers rekeys,
   reconnects and the counted broadcast test, and 6.3 the phone hotspot.
   The port, the driver's control version 2, the BPF path and the
   `jitter-egd` random source are built and tested offline; the unit has
   no `/dev/urandom`, so the supplicant's nonces come from `jitter-egd`.
   [6.0](#phase-60-results) passed on the unit on 2026-10-29 after a fix
   to how the BPF filter is set, and [6.1](#phase-61-results) passed the
   same day: WPA2-PSK with CCMP, a lease and a 60 s hold. A second run
   at close range survived 5 group and 2 pairwise rekeys with no unicast
   loss. [6.2](#phase-62-results) followed: the supplicant reconnected
   on its own after a deauthentication and after a restart of the
   network. Broadcasts were lost (about 40 % in periods) while the car's
   Bluetooth streamed audio, and none were lost with Bluetooth off.
   [6.3](#phase-63-results) joined the owner's iPhone hotspot, leased an
   address and reached the internet through it with a host route; no
   throughput figure yet.
7. **Integration.** Write a mode-switch script that tolerates repeated
   calls. Coordinate with `NWSProcess`/HMI state. Make any persistent hook
   only after phases 3–6 pass, following the
   [persistent-change rules](https://github.com/chundla/qnx632-openssh/blob/main/docs/persistent.md). Planned in
   7.0–7.5 in the [phase 7 plan](#phase-7-plan). The owner chose the
   factory online services as the purpose, a GEM button as the trigger and
   SSH from the hotspot as the access. Since the access point comes back
   only with a restart, station mode lasts until the next one, with no
   timer reverting it. The factory's `DLinkReplacesPPP` flag already
   routes a lease's default route and DNS to the online applications, so
   7.1 tests those services over `en5` before any radio work.
   [7.1](#phase-71-results) ran on 2026-10-29: LAN mode gives the unit a
   route and DNS, and its rollback is clean. Google Earth uses the route
   and stalls; the Audi connect info services stop at "No SIM card"
   without any network traffic. 7.2 (LAN mode over the station) is
   [prepared](#lan-mode-over-the-station-72-prepared-after-71):
   `radio-reattach.sh -l` requires the phone's lease to become the route
   and resolver and adds NAT on `en6`. [7.2](#phase-72-results) ran on
   2026-10-29: the phone's lease became the route and resolver (about
   1.6 Mbit/s), the rollback was clean, and the HMI behaved as in 7.1.
   Google Earth uses the route and stalls; online POI search and the
   info services make no request. 7.3, the mode switch `sta-mode.sh`
   with a keeper and a `stop`, was
   [prepared](#the-mode-switch-73-prepared-after-72) and
   [ran](#phase-73-results) on 2026-10-30: the station came up from the
   switch, SSH reached it from the hotspot phone, the keeper recovered
   from a hotspot outage, and the restart brought the access point back.
   The first stop died partway; a second finished. 7.4a, a status-only
   GEM screen, is [prepared](#the-gem-screen-74a-prepared-offline): the
   saved jar shows that the GEM's own JVM runs a script button through
   `/bin/ksh` into a FIFO, and that the GEM reads the factory definition
   directory through a start-up link. [7.4a](#phase-74a-results) ran on
   2026-10-29: the screen renders and both buttons ran as `root` on a
   settled unit. A press a minute after start-up lost a race in the
   factory console code, which leaves every script button broken until
   the next restart. 7.4b (the start button) and 7.5 (the station files
   on the HDD) are
   [prepared together](#the-start-button-and-persistent-files-74b-75-prepared-offline)
   and [passed at home](#phase-74b-and-75-results) on 2026-10-30: the
   GEM's button started the station from the HDD files, and SSH worked
   from the phone. The owner then chose to start the station at start-up,
   with the GEM switching back to the access point;
   [7.6](#station-mode-at-start-up-76-prepared-offline) was prepared and
   [passed at home](#phase-76-results): the unit joins the hotspot by
   itself about 3.5 minutes after power-on, and the GEM switches back to
   the access point through a restart.
   [7.7](#start-up-from-lsdsh-openssh-optional-77-prepared-offline), the
   start-up block in `lsd.sh` with OpenSSH optional, installed from the
   MMI3G-Toolkit card and [passed at home](#phase-77-results) on
   2026-10-31, after a fix for a crash of the unit's `ksh` that broke the
   GEM's start button. The join now takes about 3 minutes 20 s. Next is
   the car test, from a cold start.

## Not established

- What `dev-irc` does with the Wi-Fi failure messages it filters. Phase 3c
  produced two of them (`Unable to init /lib/dll/devnp-mv8688uap.so`) with no
  visible effect, but its handling was not decoded. `NWSProcess` does not
  re-mount a detached driver, and which component re-applies
  `192.168.1.1/24` to a fresh `uap0` was not identified.
- Why the factory mount sets `resetmask=0`. Nothing observed needs the reset
  line: a detach clears the firmware on its own and the factory parameters
  re-attach unassisted, so the override costs nothing here. Phase 3c settled
  the rest, except whether bit 2 of system register 8 removes power or only
  asserts a reset — it holds the card off hard enough that SDIO commands
  time out, but the two were not distinguished.
- How the HMI and `NWSProcess` present a missing access point to the user
  over a longer period than the few minutes observed, and whether anything
  retries. The HMI switch itself only stops and starts the BSS.
- Whether the uAP firmware's `MICRO_AP_EV_BSS_ACTIVE`/`_IDLE` really are BSS
  start/stop. The reading is inferred from the event pattern; no client
  association was observed, and the hotspot's beaconing was never checked
  from the air.
- Whether `PSSBSSProcess` holds `/dev/serfpga1` open on the running unit
  (`pidin fds` hangs on that process).
- Which device owns the other interrupt vectors attached by io-pkt. The
  firmware messages are now traced to io-pkt's `log()`, whose only outlet on
  this build is io-pkt's stdout, that is `/dev/ser1`; nothing has read that
  console here.
- How fast a card interrupt (FPGA `IEN` `0x100`) reaches a driver, and so
  the RX latency per frame. Also the cost of PIO inside `io-pkt` under
  contention, and of writes larger than 2 blocks. 4.5 measured the host
  side from a standalone process; see [Phase 4.5 results](#phase-45-results).
- What a fixed-address block-mode CMD53 to function-1 register `0x40`
  returns. In 4.5 it completed without error, but no byte matched the
  `0xdc` that CMD52 reads there.
- Whether the PicoBlaze DMA channel (`0x104`) can carry station traffic,
  and what it would save. RX by PIO projects below a hotspot's air rate.
- What `NWSProcess` does with `ROLE_CLIENT`. No station backend was found.
- Whether the station firmware passes up multicast frames: unicast and
  broadcast frames all came up in 5.3's last run and 6.2's second, but no
  mDNS multicast ever did (`MAC_CONTROL` does not ask for them). Whether
  `rxpd` `pkt_len` always matches the packet (it did once).
- Station firmware embedded-supplicant support, PMF and WMM behaviour. Its
  version (10.38.1.p25) and capability word (`0x303`) were read in 4.3.
- How the car's Bluetooth audio costs the station broadcasts (6.2): radio
  interference, or a coexistence arrangement the factory access point
  uses and the station driver does not. Whether the factory access point
  loses frames the same way was not measured. A phone hotspot that is
  also the audio source (6.3) is the case that matters.
- What the station firmware's region code `0x73` means. Libertas does not
  recognise it. Channels 1–11 were all scanned without an error and
  networks were heard on 1, 6 and 9; channels 12–14 were not asked for.
- Why the first 4.4 run heard nothing on channels 5–11 while the second,
  about 15 minutes later, heard four networks on channel 6 and two on
  channel 9, three of them at about −73 dBm or stronger. One 200 ms
  passive dwell per channel may miss beacons; how often was not measured.
- Where the factory stack takes the access point's MAC from. The station
  reuses it (owner decision, 2026-10-28), and 4.4 reads it from a
  factory-mounted `uap0`. A mode switch that runs while `uap0` is not
  mounted will need it from that source, or saved before the switch.
- Long-term behaviour across many restarts, and whether a firmware
  update's removal of the OpenSSH EFS hook is the only way this path
  disappears. One power-off start-up lost SSH because `jitter-egd` died;
  the cause was not established.
- Whether the Telnet SD card runs when it is already inserted at start-up.
  After the 4.1 restart it was not mounted at all, so nothing was learned.
- Long-term stability of the AX88772C on this USB stack, and whether
  other adapter chips in the patched entry's position would attach.
- Whether any factory online service still serves this car. In 7.1 the
  Audi connect info services made no request at all ("No SIM card"), and
  what would make them try is unknown. Google Earth stalled
  until the author's local proxy showed imagery on 2026-10-29. See the
  [7.1 results](#phase-71-results). (The `10.1/16` route via `10.0.96.1`
  that appeared in LAN mode, listed here earlier as of unknown origin,
  comes from the factory `/mnt/efs-system/lsd/lsd.sh`, whose second line
  is `(sleep 120 && route add -net 10.1 10.0.96.1) &`; read from the
  saved original on 2026-10-30.)
- How long after start-up the factory GEM console race stops occurring,
  and why `pidin -f aA` did not list the script that was waiting on the
  FIFO ([7.4a results](#phase-74a-results)).
- Behaviour on other trains, regions, image selectors or MU variants.
