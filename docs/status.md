# Status and validation limits

Documentation review: **2026-11-09**. Source reviewed:
`8226f4a9473838d1b0178e55e8a6786ff45045c4`; OpenSSH submodule
`602b29795454f6d10625e1e4124cb4e3fafa84c5`. New documentation is a
working-tree change, not a deployment. See [platform](platform.md) for
unit identity and reporting rules, and [operations](operations.md) for
current procedures.

## What the recorded evidence supports

| Evidence | Result and limit |
| --- | --- |
| Saved-image analysis and hardware trials, 2026-10-23–30 | SDIO, station firmware, scanning, association, `en6`, data, DHCP, WPA2/CCMP, rekeys and reconnects exercised on unit 9411 only |
| Phone hotspot, phase 6.3 | Lease and internet traffic; about 1.6 Mbit/s in the recorded transfer |
| Phase 7.7, 2026-10-30–31 | SD installation/upgrade, GEM start after the eval fix, separate `lsd.sh` hook and automatic hotspot join at home, with OpenSSH installed; about 3 min 20 s to OK in the recorded startup |
| Owner report, 2026-10-31 | Cold start away from home, hotspot initially absent and long drive reported working; no captured logs/timings from those runs |
| Source/host checks | Described separately below; not an installed or hardware-tested revision pair |

The [investigation log](wifi-client/wifi-client-mode.md#phase-77-results)
retains original source IDs (`11f7001`, `0e27791`), artifact checksums,
methods and chronology. Earlier IDs belong to the original research
history, not necessarily this repository. Do not assign later Git revisions
to hardware runs whose installed revision was not recorded.

## Known limits and follow-up

- AP and station cannot coexist. Detach leaves no factory AP; stop does not
  restore it. AP startup choice plus restart is the recorded recovery.
- Bluetooth audio caused substantial broadcast loss (about 40% at times);
  recorded unicast traffic was unaffected. This is not an all-environment
  performance guarantee.
- Station operation and bundled `jitter-egd` without OpenSSH are implemented
  but untested on this unit. No hardware result establishes that path.
- GEM and station removal still call unavailable `rmdir`; see operations
  for directory-cleanup limitations that a success exit can hide. This was found by source
  review against the local utility inventory, not a new target trial.
- Existing utility exclusion tests cover selected commands, not the full
  inventory; shell mocks run with host utilities. Inventory-based checking
  and constrained mock PATHs remain follow-up implementation work.
- LAN routing alone does not establish application feature availability.
  Historical Google Earth and Audi connect results describe those versions
  and dates; they are not a claim that external services work today.
- Cross-project startup, routing, PF, entropy and listeners need combined
  installed-artifact evidence after any change. The dependency pin and
  host success do not establish a fully tested software combination.

## Host validation

The documented command is `python3 tests/parallel.py` (or serial unittest
discovery). It uses host GCC and shell mocks; it does not need a QNX SDK for
the ordinary host checks. Some tests require the saved GEM set through
`MMI_SAVED_ENGDEFS`, the supplicant archive, local builds or historical Git
objects and may skip without them. Report skipped coverage explicitly.

On 2026-11-09, the first `python3 tests/parallel.py` run executed 473
host tests with 27 skips and one failure in
`test_sta_mode.StaMode.test_a_stop_that_is_killed_says_so_and_a_second_stop_finishes`:
the log lacked `stop: verdict OK` when asserted. The isolated test passed,
and a second full run passed (473 tests, 27 skipped). Source inspection
shows the stop worker writes its verdict file before logging that line,
consistent with a timing race; no runtime code or tests were changed.
The parallel runner reports the skip count, not individual skip reasons;
the conditional coverage categories above remain explicit.
No cross-compilation, installation, target execution, reboot, power-off
or new entropy assessment is part of this documentation review.
