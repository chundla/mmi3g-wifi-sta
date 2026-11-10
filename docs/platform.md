# Target platform and change rules

## Supported evidence

The documented unit is **9411, HN+R_US_AU_K0942_3, MU 1038**, a 2012 Audi
A6 using QNX 6.3.2 on Renesas SH7785 (SH-4 little-endian). Family ID,
firmware train, region, brand, MU and image selector are separate fields.
Saved selector-41 images describe those inputs, not every installed file.
No compatibility with another combination follows from similar filenames.

These rules were adapted on 2026-11-09 from the original integration
workspace's repository guidance at source revision
`df352bda0c8f7a5e2b906ecf311cc49284f741e6` and the dated unit observations
below. They are maintained locally; no research checkout is required.

## Native builds

Use the QNX 6.3.2 SDK's `sh-unknown-nto-qnx6.3.0-gcc` 3.3.5 with SH-4
little-endian flags (`-ml -m4`). The unit has `libc.so.2`; QNX 6.5 binaries
requiring `libc.so.3` are not substitutes. Check imports against libraries
from the intended unit. ELF checks establish compatibility with the supplied
libraries, not successful loading or behavior on hardware. Fixed-offset
patches require exact input hashes and original-byte checks.

SDKs, saved runtime libraries, firmware, credentials, keys, account hashes,
raw captures and deployment evidence remain in ignored private storage.
Do not add them to source, documentation or distributable payloads. Public
docs can record sanitized evidence IDs, artifact checksums and limitations.

## Unit-side shell

Target `/bin/ksh`, PD KSH 5.2.14. Host build scripts may use host utilities;
commands executed on the unit, including generated shell and SSH command
strings, may use only its builtins, their own functions, staged programs
and observed utilities in [the local PATH inventory](units/9411-path-utilities.txt).
The inventory is a dated observation, can include owner-installed files,
and does not prove that a listed file executes or that an option works.
Other launchers may supply a different PATH.

The observed unit lacks `dirname`, `basename`, `rmdir`, `printf`, `head`,
`tail`, `awk`, `cut`, `tr`, `sort`, `wc`, `find`, `expr`, `dd`, `od`, `tar`,
`sync` and `uptime`. Use parameter expansion (`${0%/*}`, `${0##*/}`),
`read` loops, `grep`, `sed`, and `print`/`echo` where their semantics fit.
Do not add partial command shims that silently lose formatting, sorting,
uniqueness or durable-write semantics. Avoid assuming `mkdir -p` works
across the unit's mount prefixes; create missing directories one at a time.
The station's historical option checks also found no `grep -o` or `date +%s`.

Before relying on an unobserved command or option, check it read-only on
the intended unit (`whence -v NAME`, `use NAME`) and record date, unit,
source and result locally. Never extend the inventory from an SDK or manual.
Host tests and mocked utilities cannot establish target availability or
pdksh behavior. No inventory-based shell checker is integrated here yet;
adding one and restricting mocked-test PATHs is follow-up work, not an
existing validation guarantee.

Clear inherited `LD_PRELOAD=/usr/lib/libmc.so` before launching SSH and
its helpers. This library creates a thread that prevents QNX `fork`.
With `set -e`, use `unset LD_PRELOAD || :`, as unset can fail when absent.
Preserve tested library search paths. In long-lived pdksh functions avoid
`eval`: the station investigation traced a shell crash to stale eval input
state. Host dash tests do not reproduce that failure.

## Persistent changes and trials

Before writes, preserve exact originals/checksums off the component being
changed, check space, prepare independent access and rollback, and verify
both installed bytes and effective behavior. Keep original passwd/group
files intact and verify RAM account mappings. Scope firewall changes to the
intended interface/client; avoid reloading unrelated NAT or flushing states.
Repeated startup must not create duplicate services or driver mounts.

Run a new script's harmless mode on the unit before its hardware path;
use only modes the script actually implements. Bound diagnostics that may
hang, record failures/timeouts, and compose cleanup traps. Keep another
operation's locks and reclamation holds intact. A hold is not a consistent
snapshot or database lock. Firmware updates can overwrite EFS hooks; SD
removal and checksums alone do not establish rollback or recovery.

## Reporting results

Separate source inspection, host tests, saved-image analysis, upstream
reports, proposals and local hardware results. Record project revisions,
working-tree differences, installed checksums, firmware identity, access
path and outcome. Unknown installed revisions stay unknown. Use dated
corrections instead of rewriting historical commands/checksums.

Record time source and uptime when the clock is unset; do not treat a 1970
unit timestamp as calendar time. Distinguish a service restart, a forced
QNX reboot and full power-off. A passing host suite or source extraction
establishes none of these. Partial captures and FAT32 file copies are not
tested recovery images.
