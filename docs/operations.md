# Build and operate the station

This guide describes the source at station revision
`8226f4a9473838d1b0178e55e8a6786ff45045c4`, documented on 2026-11-09.
Read [platform](platform.md) and [status](status.md) first. The procedures
are specific to the recorded unit and pinned payload, not a generic
installer for other firmware. Historical results remain in the
[investigation log](wifi-client/wifi-client-mode.md).

## Host preparation and builds

Clone with `--recurse-submodules`, or run `git submodule update --init`.
The pinned `openssh/` dependency contains SDK extraction, ELF verification
and entropy builds. Do not update its revision to set up a build.

Provide these private inputs; none is shipped here:

| Input | Default location / requirement |
| --- | --- |
| QNX 6.3.2 SDK with io-net DDK | `work/sdk`, or `QNX_SDK_ROOT` |
| Unit libc | `work/unit-files/proc/boot/libc.so.2` |
| Unit socket library | `work/unit-files/mnt/ifs-root/lib/libsocket.so.2` |
| Supplicant release | `work/build/downloads/wpa_supplicant-2.11.tar.gz`, verified release 2.11; build checks SHA-256 |
| Station firmware | Obtain `mrvl/sd8688.bin` and `mrvl/sd8688_helper.bin` from linux-firmware under its Marvell licence; verify installer pins |
| Jitterentropy source | Follow the pinned submodule's build script; default `openssh/work/work/jitterentropy-library-3.7.0` |

`SAVED_FILES` overrides the root containing the two library paths.
`OPENSSH_WORK` overrides the station build workspace despite its name;
`OUT_DIR` overrides driver/probe outputs, and `WPA_BUILD` overrides the
supplicant workspace. Use paths without spaces for the legacy toolchain.
Host GCC is for tests; these builds use the QNX cross-compiler. Some SDK
host tools require a 32-bit Linux runtime; SDK extraction instructions are
in the OpenSSH dependency's build documentation.

From this repository root, on the host:

```sh
sh wifi-sta/build-driver.sh
sh wifi-sta/build-probe.sh
sh wifi-sta/wpa/build-wpa.sh
(cd openssh && sh build-jitter.sh)
```

The last command assumes the submodule's SDK and Jitterentropy inputs
are prepared, or its `QNX_SDK_ROOT`/`OPENSSH_WORK` are set appropriately.
These commands do not connect to the unit.

| Build | Default outputs |
| --- | --- |
| Driver | `work/build/devn-mv8688sta.so`, `mv8688-ctl`, `bpf-check`, `netcheck`, ELF reports |
| Probe | `work/build/mv8688-probe`, ELF report |
| Supplicant | `work/build/wpa/wpa_supplicant`, `wpa_cli`, logs and ELF reports |
| Entropy | `openssh/work/jitter-build/jitter-egd` |

All ten files listed in `wifi-sta/sta-install.sh`'s `files` block must be
staged together under their listed names, including firmware, `jitter-egd`,
`sta-mode.sh`, `sta-boot.sh` and `boot-launch.sh`. Also stage
`WifiClient.esd` and `gem.sh` together for GEM installation. The installer
pins checksums of scripts and binaries; any development build mismatch
needs reviewed pins and validation, not a bypass. Keep firmware licence
material with any redistribution permitted by its licence.

## Before persistent installation

Have an authenticated root shell and independent recovery access that
survives losing `uap0`. On the documented unit this was wired `en5`;
provisioning keys, adapter and recovery access is a prerequisite, not an
automatic feature of these builds. Do not assume an SD Telnet payload is
included in this repository. Prepare recovery before changing networking.

Preserve current startup, GEM and routing configuration and checksums;
check HDD/flash space, write results and target identity. Stage files into
a private RAM directory or SD path and verify transferred bytes on target.
The examples below assume verified station files/scripts at `/fs/tmpfs/sta`,
GEM files/installer at `/fs/tmpfs/gem`, and `lan-mode.sh` at `/fs/tmpfs`.
These are chosen staging locations, not generated output directories.
Do not expose or log hotspot credentials.

On the unit, inspect first:

```sh
/bin/ksh /fs/tmpfs/lan-mode.sh status
/bin/ksh /fs/tmpfs/sta/sta-install.sh status
/bin/ksh /fs/tmpfs/gem/gem-install.sh status
```

If LAN mode is absent, `lan-mode.sh enable` creates the owned flag;
restart before relying on the HMI's routing mode. If the flag already
exists, inspect ownership/status rather than removing another owner's
flag. Recopy RAM staging after restarting.

## Install and configure

The station installer requires at least 2048 KiB free on the HDD; GEM
checks at least 64 KiB on each of its target partitions. These guards are
minimums, not substitutes for checking total staging and backup needs.
GEM installation requires writable EFS; the script does not remount it.
The startup hook remounts `efs-system` itself. Check each command's result
before moving to the next step.

```sh
/bin/ksh /fs/tmpfs/sta/sta-install.sh install /fs/tmpfs/sta
/bin/ksh /fs/tmpfs/sta/sta-install.sh conf < /fs/tmpfs/wpa-hotspot.conf
/bin/ksh /fs/tmpfs/gem/gem-install.sh install /fs/tmpfs/gem
/bin/ksh /fs/tmpfs/sta/sta-install.sh hook
```

Generate the configuration privately on the host with `wpa_passphrase`.
Remove its commented plaintext passphrase before transfer. Use exactly
one network block with the intended SSID, `key_mgmt=WPA-PSK`, CCMP and a
64-hex derived PSK; keep `ctrl_interface` empty as in the station tests.
A derived PSK is still a credential. The installer reads stdin, validates
the file, stores it root-owned mode 600, and does not print it. Keep staged
copies private and remove them after verified installation.

The station payload goes to `/mnt/persistence/wifi-sta`; `SUMS` is written
last. GEM files go to `/mnt/efs-system/engdefs/WifiClient.esd` and
`/mnt/efs-persist/wifi-sta/gem`. Restart to make GEM load the new screen
under Main > WifiClient. Inspect installer status again and verify installed
checksums before a supervised first launch.

## Manual operation and results

With installed prerequisites and the hotspot available:

```sh
/bin/ksh /mnt/persistence/wifi-sta/sta-mode.sh start -n
/bin/ksh /mnt/persistence/wifi-sta/sta-mode.sh start
/bin/ksh /mnt/persistence/wifi-sta/sta-mode.sh status
```

`start -n` checks without detaching the AP. The real start detaches `uap0`;
its session may drop while detached work continues. Reconnect through the
independent path to inspect status. Exit 0 means OK (status can also mean
not started), 1 usage, 2 refused/FAIL, 3 PARTIAL. Read the verdict and logs;
PARTIAL is not complete success. The keeper can join a hotspot that appears
later and follows lease changes, supplicant/DHCP exits and SSH binding.

`sta-mode.sh stop` stops station operation; it does **not** restore the
factory AP. Select car access point for startup and restart the unit to
restore it. Do not try to recover by killing the shared `io-pkt` process.

GEM's Runner check and Station status give bounded feedback; Start station
now starts the same mode switch. Startup choice affects the next boot.
Changing choice alone neither starts the station nor restores the AP.

## Automatic startup and diagnostics

`hook` places a marked background block in factory `lsd.sh` and installs
`/mnt/efs-persist/wifi-sta-boot.sh`. That launcher waits up to 180 seconds
for the HDD, then invokes `sta-boot.sh`. With the hotspot startup marker
set, boot checks include kill switch, `en5`, AP readiness and optional
OpenSSH readiness, followed by a settling wait. Presence of `en5` keeps
the AP; it is not a test for working wired carrier.

| Control / log | Purpose |
| --- | --- |
| `/mnt/efs-persist/wifi-sta/gem/client-at-start-up` | GEM creates for hotspot startup; AP choice removes it |
| `/mnt/efs-persist/wifi-sta/gem/start.disabled` | Blocks GEM/automatic starts; direct `sta-mode.sh start` does not consult it |
| `/mnt/persistence/wifi-sta/boot.log` | Boot decisions, rotated beside the payload |
| `/dev/shmem/wifi-sta-boot.log` | Launcher output until restart |
| `/fs/tmpfs/mmi-sta/log` | Mode-switch and keeper log until restart |
| `/mnt/efs-persist/wifi-sta/gem/gem.log` | GEM action log |

No hotspot address is fixed; inspect the current lease. SSH on that address
at port 2222 exists only with the working OpenSSH installation and its keys.
A running station without SSH still requires EGD; the bundled-daemon path
has host coverage but no recorded target trial.

## Upgrade, disable and rollback

Before upgrading, choose AP startup and restart, retaining independent
access. `sta-install.sh` refuses payload changes while its station lock
exists. Save configuration and installed bytes privately.

For an older phase-7.6 hook in OpenSSH's `start.sh`, use the installer’s
`unhook` before `install` and `hook` to migrate to the separate `lsd.sh`
block. Preserve the distinct wired helper line. An unchanged current
payload install is harmless; recognized older files upgrade in place and
configuration stays. Unknown modified files are refused. GEM older builds
require `remove`, then `install`; restart to reload definitions.

To suppress GEM/automatic starts, create `start.disabled` above or choose
AP in GEM. Neither prevents a direct manual `sta-mode.sh start`. For startup rollback independent of the HDD mode switch, run:

```sh
/bin/ksh /mnt/efs-persist/wifi-sta-rollback.sh
```

Inspect the rollback and saved `wifi-sta-lsd.sh.pre` against any later
startup edits first. `sta-install.sh unhook` invokes that rollback when
available. After disabling/unhooking, restart for factory AP restoration.
For full removal while station mode is not running, run `unhook` before
`sta-install.sh remove`, then `gem-install.sh remove`; use the verified
staged installers. Payload removal deletes the stored hotspot configuration.
GEM removal also clears its startup choice. LAN mode is separate: use
`lan-mode.sh disable` only for a flag that script owns, then restart.

**Known removal defect:** both installers call `rmdir`, absent from the
observed unit. Station removal can leave empty directories and misleading
nonempty messages; GEM removal can also leave empty directories while
returning success. Check remaining files as well as exit status. Do not
substitute recursive deletion or claim removal is fully validated; this
documentation change does not repair those calls.

## Wired management prerequisite

The wired route requires a suitable AX88772B/C adapter and USB-enabled AMI
cable coding. `patch_asix_772b.py` checks the original driver's full SHA-256
and table bytes before producing `work/build/devn-asix-772b.so`; it changes
the D-Link entry to `0b95:772b`. It is not a universal USB-ID patch.

For the documented persistent layout, verified driver plus
`en5-persistent-start.sh` renamed to `start.sh` are staged in
`/mnt/persistence/en5`. `install-en5-persistent.sh` appends the wired helper
to the existing OpenSSH startup, saves backups and writes an independent
`/mnt/efs-persist/en5-rollback.sh`; startup remains disabled by
`/mnt/efs-persist/en5.disabled` pending supervised validation. The helper
uses static `192.168.16.11/20` and can start a second SSH listener using
OpenSSH's identity. Inspect PF exposure: a wired path is not inherently
restricted to an intended client. Preserve this hook through OpenSSH
updates. Detailed adapter/coding observations are in the investigation's
[USB Ethernet section](wifi-client/wifi-client-mode.md#usb-ethernet-driver-matching).
