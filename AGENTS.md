# Contributor and agent guidance

## Start here

Read [README](README.md), [platform rules](docs/platform.md),
[architecture](docs/architecture.md), [status](docs/status.md), then
[operations](docs/operations.md) for installation or runtime work.
The [investigation log](docs/wifi-client/wifi-client-mode.md) preserves
historical plans and results; it is not the current operating guide.

## Ownership and commands

This repository owns the station driver, SDIO/firmware layers, supplicant
port, mode switch, GEM, startup, wired helpers and Wi-Fi probes/traces.
`openssh/` is the pinned qnx632-openssh submodule: make entropy/SSH changes
in that project, not in a copied implementation here. Do not advance the
submodule merely to obtain newer docs.

From the root, run `python3 tests/parallel.py` before committing and
`python3 tests/parallel.py test_sta_mode` (or another module/class prefix)
while iterating. Serial equivalent: `python3 -m unittest discover -s tests`.
Host tests require Python 3, GCC and the host shell/utilities; private-input
checks can skip. Report skips. Mocked shell tests do not prove QNX behavior.

Build commands: `sh wifi-sta/build-driver.sh`,
`sh wifi-sta/build-probe.sh`, `sh wifi-sta/wpa/build-wpa.sh`.
Read operations for SDK, saved-library, archive and submodule prerequisites.
Builds do not deploy. Keep generated/private inputs under ignored `work/`.

## Editing rules

Preserve owner changes and private evidence. Follow the local platform
rules for every unit-side command, generated shell and native build.
Installer checksums pin scripts as well as binaries: coordinate affected
pins and tests when implementation changes, recording new validation;
never bypass a mismatch to make installation succeed.

Use multiline ESD blocks with a defined parent and factory examples;
confirm rendering on target. An ESD binding is not a shell reader or proof
of an available service. Keep historical log commands/checksums intact,
adding attributed corrections when needed. Record source checks separately
from installed artifacts and hardware trials.

Have independent access before detaching `uap0`; stop does not restore the
AP. Preserve disabled markers, rollback and other operations' locks.
The current removal scripts call unavailable `rmdir`; see status. Do not
silence this defect or claim the existing tests enforce the full inventory.
