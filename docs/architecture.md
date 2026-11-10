# Station architecture and code map

Read [platform](platform.md) for target constraints and [status](status.md)
for validation limits. The current design uses one radio in station mode;
the factory AP and station cannot run together.

## Runtime flow

1. `hbsdc.c` drives the Harman FPGA SDIO host; `sdio.c` supplies card I/O.
   `mv8688fw.c` downloads the helper and libertas station firmware. The
   factory uAP driver uses different commands and TX descriptors; swapping
   its firmware cannot implement this design.
2. `devn-mv8688sta.c` exposes an io-net driver through factory `devnp-shim`
   inside the existing `io-pkt`. `mv8688sta.c` handles link/station state;
   `mv8688cmd.c` implements firmware commands, events and data framing.
   The resulting interface is `en6`.
3. `wpa/driver_mv8688.c` uses the control ABI in `mv8688sta-ctl.h` for
   association and keys. `wpa/l2_packet_qnx.c` and `bpfl2.c` move EAPOL via
   BPF. `wpa/os_random_egd.c` gets nonces from loopback EGD on TCP 708;
   there is no usable random device fallback.
4. `sta-mode.sh` validates prerequisites, detaches `uap0`, loads the
   station, starts supplicant and factory `dhcp.client`, then maintains
   them with a keeper. Factory `dhcp-up` and the LAN flag supply the lease
   route/resolver behavior. The script manages station PF rules and a
   lease-bound SSH listener when the OpenSSH installation is running.
5. `gem/WifiClient.esd` invokes action links to `gem/gem.sh` for status,
   start and startup choice. `sta-install.sh hook` adds a background block
   to factory `lsd.sh`; its flash launcher waits for the HDD and calls
   `sta-boot.sh`. Boot checks markers, wired `en5`, optional OpenSSH
   readiness and the factory AP before starting the mode switch.

Persistent payload lives in `/mnt/persistence/wifi-sta`, GEM actions in
`/mnt/efs-persist/wifi-sta/gem`, and runtime state/logs in
`/fs/tmpfs/mmi-sta`. Boot logs also live beside the HDD payload and in
`/dev/shmem/wifi-sta-boot.log`. See [operations](operations.md) for markers.

## Integration contract

The OpenSSH submodule is pinned at
`602b29795454f6d10625e1e4124cb4e3fafa84c5` in station revision
`8226f4a9473838d1b0178e55e8a6786ff45045c4`. It provides build/ELF tools
and `jitter-egd`; that pin does not prove a hardware-tested source pair.
Run `git submodule update --init` to obtain the recorded dependency.

EGD on `127.0.0.1:708` is required. A running OpenSSH installation supplies
it; otherwise the station starts its bundled daemon, which stays until
restart. OpenSSH access itself is optional. With it running, station SSH
uses `/mnt/ssh` binaries, host identity and authorized keys, binding to the
current lease on port 2222. Without it there is no station SSH. The latter
mode is implemented but has not been exercised on the documented unit.

Wired helpers in this repository patch a verified copy of the factory ASIX
driver and bring up `en5`. Their installer appends a separate line to
OpenSSH's installed `start.sh`. That is distinct from the current station
`lsd.sh` hook and the older phase-7.6 station hook in OpenSSH's script.
Never replace a whole shared startup file without reconciling these edits.

## Where to change and check

All source names below are under `wifi-sta/` unless shown otherwise.

| Task | Implementation | Host tests under `tests/` |
| --- | --- | --- |
| Host/card access and firmware | `hbsdc.c`, `sdio.c`, `mv8688fw.c`, `sdio-probe.c` | `test_wifi_sta_host.py`, `test_wifi_sta_firmware.py` |
| Firmware protocol and station state | `mv8688cmd.c`, `mv8688sta.c`, `devn-mv8688sta.c` | `test_wifi_sta_cmd.py`, `test_wifi_sta_station.py` |
| WPA/BPF/randomness | `wpa/`, `bpfl2.c` | `test_wifi_sta_wpa_driver.py`, `test_wifi_sta_wpa_random.py`, `test_wifi_sta_bpf.py` |
| Mode switch, leases and keeper | `sta-mode.sh`, `lan-mode.sh` | `test_sta_mode.py`, `test_lan_mode.py` |
| Install, GEM and boot | `sta-install.sh`, `gem-install.sh`, `sta-boot.sh`, `boot-launch.sh`, `gem/` | `test_wifi_gem.py` |
| Wired management | `patch_asix_772b.py`, `install-en5-persistent.sh`, `en5-persistent-start.sh` | `test_patch_asix_772b.py`; no dedicated persistent-en5 shell test |
| Bench/probe recovery | `radio-reattach.sh`, `bench-open-ap.sh`, `benchnet.c` | `test_radio_reattach.py`, `test_bench_open_ap.py`, `test_wifi_sta_bench.py` |

`tools/ghidra/` scripts analyze hash-checked factory inputs; they are offline
research tools. Probes/bench scripts can change the radio and are not
routine status commands. Native host tests and mocked shell utilities
cannot establish radio, firmware, pdksh or factory DHCP behavior.
