# Credits

## Software this builds on

| Work | Author | Licence | How it is used |
| --- | --- | --- | --- |
| Linux [libertas](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/drivers/net/wireless/marvell/libertas) driver (`if_sdio.c`, `cmd.c`, `host.h`, `rx.c`) | Marvell and the Linux kernel contributors | GPL-2.0 | Reference for the SD8688 station protocol: command codes, `txpd`/`rxpd` layouts, scan, association and key TLVs, the firmware-ready register. Read, not copied; source comments name each libertas function followed. |
| [wpa_supplicant](https://w1.fi/wpa_supplicant/) 2.11 | Jouni Malinen and contributors | BSD-3-Clause | Built from the signature-checked release (SHA-256 `912ea06f…c0a`, key `EC4A A0A9 91A5 F246 4582 D52D 2B6E F432 EFC8 95FA`) with `wifi-sta/wpa/qnx632.patch` and this repository's backend. Not included. |
| [linux-firmware](https://git.kernel.org/pub/scm/linux/kernel/git/firmware/linux-firmware.git) `mrvl/sd8688.bin`, `mrvl/sd8688_helper.bin` | Marvell | `LICENCE.Marvell` (listed as redistributable in `WHENCE`) | The station firmware the driver loads, and its helper (byte-identical to the factory helper). Not included. |
| [qnx632-openssh](https://github.com/chundla/qnx632-openssh) | chundla | BSD-2-Clause | `jitter-egd` (the supplicant's random source), `sshd`, the start-up hook this hooks into, and `verify_elf.py` for the build checks. Submodule `openssh/`. |
| [Jitterentropy](https://github.com/smuellerDD/jitterentropy-library) | Stephan Müller | BSD-3-Clause / GPL-2.0 | Inside `jitter-egd`. |
| [Ghidra](https://github.com/NationalSecurityAgency/ghidra) 12.1.4 | National Security Agency | Apache-2.0 | Decompilation of the factory drivers through the scripts in `tools/ghidra/`. |
| [hostapd](https://w1.fi/hostapd/) on a Raspberry Pi | Jouni Malinen and contributors | BSD-3-Clause | The bench access point (`wifi-sta/bench-open-ap.sh`). |
| QNX Momentics 6.3.2 SDK | QNX Software Systems | Commercial | Compiler, headers and the io-net DDK. Not included. |

## MMI 3G research

- [DrGER2/MMI3G-Info](https://github.com/DrGER2/MMI3G-Info): the MMI 3G+
  research and scripts in whose fork this work began, and the access used
  to reach the unit at first.
- [DrGER2/MMI3GP-LAN-Setup](https://github.com/DrGER2/MMI3GP-LAN-Setup):
  reviewed, not used. It confirmed `en5` as the USB Ethernet interface
  name, and it already sets the factory `DLinkReplacesPPP` flag whose
  routing this work decoded and relies on.
- [dspl1236/MMI3G-Toolkit](https://github.com/dspl1236/MMI3G-Toolkit):
  its `DATA_CONNECTIVITY_OPTIONS.md` and `CUSTOM_DRIVER_INJECTION.md` set
  out the Wi-Fi client idea and the firmware-swap hypothesis this work
  tested and replaced; its GEM, GEMMI and platform research was consulted
  throughout, and its GEMMI release was compared byte for byte with this
  unit's.
- The Audizine, A5OC, VWVortex and ClubTouareg communities, for the
  MMI 3G knowledge the projects above collect.

The factory files cited in the notes (drivers, configuration, J9 classes)
were read from the author's own unit and its backups. None of them is
included here.
