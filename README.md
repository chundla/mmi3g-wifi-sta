# Wi-Fi station mode for the Audi MMI 3G+

The MMI 3G+ head unit's Marvell 88W8688 normally runs as a 2.4 GHz access
point (`uap0`, 192.168.1.1). This repository turns it into a Wi-Fi
**client** that joins a phone hotspot with WPA2-PSK. The factory "LAN
replaces PPP" mode then routes the unit's online services through it.

It takes a new driver. Swapping the firmware under the factory driver
cannot work: `devnp-mv8688uap.so` is Marvell's uAP (micro access point)
driver, sends uAP-only commands, and frames packets in the uAP TxPD layout.
The libertas station firmware expects a different command set and TX
layout. What is here:

```text
phone hotspot (2.4 GHz, WPA2-PSK/CCMP)
        │ 802.11b/g
88W8688 running sd8688.bin (libertas station firmware, linux-firmware)
        │ SDIO, Harman FPGA module 53
devn-mv8688sta.so   io-net driver, hosted by the factory devnp-shim in io-pkt
   ├─ hbsdc.c       FPGA SDIO host (behaviour recovered from the factory driver)
   ├─ sdio.c        SDIO card layer, PIO
   ├─ mv8688fw.c    helper + firmware download
   ├─ mv8688cmd.c   libertas command/event and txpd/rxpd layer
   └─ mv8688sta.c   scan, join, keys, link state; control ioctl for the supplicant
wpa_supplicant 2.11 with driver_mv8688.c, BPF l2_packet, nonces from jitter-egd
dhcp.client (factory)    pf rules for en6 (block inbound; SSH from the hotspot only)
sta-mode.sh              start / status / stop, with a keeper that rejoins
GEM screen               WifiClient.esd: status, start, start-up choice, back to AP
```

## Status

Tested on one unit only: **9411, HN+R_US_AU_K0942_3, MU 1038** (2012 Audi
A6, North America, QNX 6.3.2). Do not assume it works on another train,
region, image selector or MU variant.

Passed on that unit, 2026-10-23 to 2026-10-30 (details and evidence in
[the station notes](docs/wifi-client/wifi-client-mode.md)):

- the station firmware boots, answers commands, scans and associates;
- `en6` attaches inside the running `io-pkt` and carries traffic;
- WPA2-PSK with CCMP, group and pairwise rekeys, and reconnects after a
  deauthentication or a network restart;
- an iPhone hotspot: lease, internet, about **1.6 Mbit/s** (10 MB in 52 s);
- the factory LAN mode makes the lease the unit's default route and
  resolver. Google Earth uses it; the Audi connect info services and
  online POI search do not try the network ("No SIM card");
- SSH from the phone that serves the hotspot;
- a GEM button that starts the station, and a start-up choice that joins
  the hotspot by itself about 3.5 minutes after power-on (with the 7.6
  hook; the 7.7 `lsd.sh` hook and the optional OpenSSH have not run on the
  unit yet);
- a restart brings the factory access point back.

Not yet seen: a cold start in the car away from home, a start-up with the
hotspot off, behaviour over a long drive, and why one start-up ended
before the station started. Broadcast frames are lost (about 40 % at
times) while the car's Bluetooth streams audio; unicast is unaffected.

**Station mode removes the car's access point** until the next restart:
the chip holds one firmware at a time. Have a way back in that does not
need Wi-Fi before trying it (the notes use USB Ethernet, `en5`).

## Layout

| Path | Contents |
| --- | --- |
| `wifi-sta/` | Driver sources, control tool, probes, on-unit scripts, build scripts |
| `wifi-sta/wpa/` | The `wpa_supplicant` 2.11 port: backend, BPF `l2_packet`, EGD random source, patch, configuration, `build-wpa.sh` |
| `wifi-sta/gem/` | The engineering-screen definition `WifiClient.esd` and its button script `gem.sh` |
| `docs/wifi-client/wifi-client-mode.md` | The whole investigation: evidence, design, every phase's plan and hardware result, hazards, open questions |
| `tools/ghidra/` | Ghidra scripts that traced the factory Wi-Fi, ASIX and io-pkt/io-net drivers (each checks the input's hash; no factory code is included) |
| `tools/gem/esd_grammar.py` | A model of the GEM definition grammar, used by the tests |
| `tests/` | Host tests: `python3 tests/parallel.py`, or `python3 -m unittest discover -s tests` |
| `openssh/` | Submodule: [qnx632-openssh](https://github.com/chundla/qnx632-openssh) |

The on-unit scripts are kept byte-for-byte as they ran, because
`sta-install.sh` pins their checksums. Their comments refer to the notes
by section name.

## Dependencies on the unit

- **`jitter-egd`** from [qnx632-openssh](https://github.com/chundla/qnx632-openssh)
  on `127.0.0.1:708`. The unit has no `/dev/urandom`, and `wpa_supplicant`
  takes its nonces from it; without it the handshake fails by design.
  `sta-mode.sh` uses the OpenSSH installation's when it runs, and
  otherwise starts the copy installed beside the station files.
- **Optional: the OpenSSH installation** from qnx632-openssh. While it
  runs, `sta-mode.sh` also starts an `sshd` from `/mnt/ssh` on the
  station's address, and `sta-boot.sh` waits for it at start-up. Without
  it the station has no SSH.
- The factory `devnp-shim.so`, `dhcp.client`, `/dev/bpf` and `pf`.
- For the wired management path: an AX88772B/C adapter, the
  `patch_asix_772b.py` device-table patch of the unit's own
  `devn-asix.so`, and an AMI cable coded for USB. See
  "USB Ethernet driver matching" and "Phase 2 results" in the notes.

## Building

You need the QNX 6.3.2 SDK (`sh-unknown-nto-qnx6.3.0-gcc` 3.3.5 with the
io-net DDK headers). It is commercial and not included; the
[OpenSSH build notes](https://github.com/chundla/qnx632-openssh/blob/main/docs/build.md)
record the media used. You also need `libc.so.2` and `libsocket.so.2`
copied from your unit, for the import check.

```sh
git clone --recurse-submodules https://github.com/chundla/mmi3g-wifi-sta.git
cd mmi3g-wifi-sta
# work/sdk                                   the extracted SDK (or set QNX_SDK_ROOT)
# work/unit-files/proc/boot/libc.so.2        from the unit (or set SAVED_FILES)
# work/unit-files/mnt/ifs-root/lib/libsocket.so.2
# work/build/downloads/wpa_supplicant-2.11.tar.gz   signature-checked release
sh wifi-sta/build-driver.sh      # devn-mv8688sta.so, mv8688-ctl, bpf-check, netcheck
sh wifi-sta/build-probe.sh       # mv8688-probe
sh wifi-sta/wpa/build-wpa.sh     # wpa_supplicant, wpa_cli
```

Firmware is not in this repository. Take `mrvl/sd8688.bin` and
`mrvl/sd8688_helper.bin` from
[linux-firmware](https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git/tree/mrvl)
(licence `LICENCE.Marvell`). From this tree, the builds are byte-identical
to the ones `sta-install.sh` pins and that ran on the test unit:

| File | POSIX `cksum` |
| --- | --- |
| `devn-mv8688sta.so` | `3938534867 64931` |
| `mv8688-ctl` | `3277080864 11450` |
| `mv8688-probe` | `2747790827 77195` |
| `wpa_supplicant` | `2366746706 491332` |
| `sd8688.bin` | `251454034 259172` (SHA-256 `f563f36b…9e216e`) |
| `sd8688_helper.bin` | `1538620194 2616` (SHA-256 `1c7ae3d4…1204d6f6`) |

## Installing

The [MMI3G-Toolkit](https://github.com/dspl1236/MMI3G-Toolkit) `wifi-sta`
module packages these files and runs the steps below from an SD card.

By hand, the steps are described in the notes: "The start button and
persistent files (7.4b, 7.5)", "Station mode at start-up (7.6)" and
"Start-up from `lsd.sh`, OpenSSH optional (7.7)". In short, as root on the
unit:

1. `lan-mode.sh enable`, then a restart, if the factory LAN-mode flag
   `/HBpersistence/DLinkReplacesPPP` is not already set.
2. `sta-install.sh install <dir>` puts the binaries, the firmware,
   `jitter-egd`, `sta-mode.sh`, `sta-boot.sh` and `boot-launch.sh` in
   `/mnt/persistence/wifi-sta`, and checks each against its pinned sum.
3. `sta-install.sh conf < wpa-hotspot.conf` stores the hotspot's
   configuration (mode 600). Generate it with `wpa_passphrase`, so the
   unit holds the derived key and not the passphrase.
4. `gem-install.sh install <dir>` adds the `WifiClient` screen to the GEM
   and its button script to `/mnt/efs-persist/wifi-sta/gem`.
5. `sta-install.sh hook` adds the start-up block to the factory `lsd.sh`,
   with its rollback.

Every step has a `status` and a `remove`/`unhook`. To upgrade the 7.6
install, whose hook is a line in OpenSSH's `start.sh`, run `unhook`,
`install` and `hook`.

## Licence

- The station driver and everything else in this repository, unless
  listed below: **GPL-2.0-only** ([LICENSE](LICENSE)). The driver was
  written with Linux libertas as the reference for the station protocol.
  No libertas code is copied, but it is released under libertas' licence
  to be safe.
- `wifi-sta/wpa/`, `wifi-sta/bpfl2.c`, `wifi-sta/bpfl2.h` and
  `wifi-sta/mv8688sta-ctl.h` (everything compiled into `wpa_supplicant`):
  **BSD-3-Clause** ([LICENSES/BSD-3-Clause.txt](LICENSES/BSD-3-Clause.txt)),
  the licence of `wpa_supplicant` itself.

See [CREDITS.md](CREDITS.md) for the work this builds on.
