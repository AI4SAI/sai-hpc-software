# Daily timeout repairs, 2026-11-02

This follows the failures and raw reproductions in
[`daily-timeouts-20261102`](../daily-timeouts-20261102/README.md).
Source transfer and guarded ABACUS phases are repository changes. The runtime
hotfix is an administrator handoff; public `/opt` replacement and full software
rebuild/scientific acceptance are subsequent steps, not claims made by these
filesystem tests.

## Source transfer

The failed LAMMPS release is `stable_30Sep2026_update1`, commit
`098ffdefa525cabc82272925de02e9884f58c657`. The old code uploaded about 908 MB
independently for each target, then restarted interrupted SCP parts from zero.
The cached old release was outside the newest-12-tip inventory window, while
cached develop tips were not themselves ancestors of the release.

The new selector uses a cached tip's shared ancestor. The actual verified
transfer used base `0165101ce505734e8fb249694b22e295a7ab4182` and transferred
**4,960,979 compressed bytes** (about 99.45% less). The receiver verified all
parts and the bundle, fetched the exact commit, and confirmed complete ancestry.
See `lammps-source-manifest.json` and `lammps-cache-receipt.json`.

A per-repository/per-commit SSH session owns a flock until verified receive;
waiting targets recheck the cache and skip the duplicate upload. Disconnecting
releases the lock. The lock ends before build submission, preserving parallel
compilation. Four rsync workers reuse partial files on retry and repair
same-sized corruption using checksums; the receiver's independent SHA-256
validation remains mandatory. ABACUS, CP2K, GPUMD and the independent/paired MD
controllers share this runner helper.

## Writable images

ABACUS now includes the pinned driver and synchronization helpers in its recipe
and uploaded payload. Build, metadata and export synchronize inside the image;
each writable execution is guarded before the next phase. Offline read-only
fsck checks separate successful build/export from reuse. Final SIF verification
remains independent. A forced fuse2fs timeout is rejected even when Apptainer
omits its additional cleanup-error message.

The Apptainer source patch changes normal driver-stop grace from 1 to 30 seconds,
leaving explicit termination and the SIGTERM-to-SIGKILL interval at 1 second.
It does not weaken fuse2fs locking or fsync. The pinned fuse2fs binary is unchanged.

The non-setuid runtime was rebuilt from the official checksum-verified 1.4.4
vendored archive using Go 1.25.4. Build job **2134143** completed successfully and
passed the slow-exit and explicit-termination regressions. Installation uses
only the rebuilt `starter`, preserving the original installed CLI and its
AppArmor-authorized path. See the
[administrator handoff](../../../deployment/apptainer-1.4.4-sai/README.md).

Positive qualification **2135060** completed on `d9470n01` with `0:0`. The
installed original CLI ran with the pinned replacement starter bound privately
at its normal path; public `/opt` bytes were not replaced. Two fresh 512 MiB
images, one fresh sparse 32 GiB image, and a 512 MiB image with strace-injected
2-second fsync delays all passed:

- payload writes and explicit synchronization;
- natural driver exit, without forced-stop or cleanup warnings;
- offline `e2fsck -fn` returning zero;
- a separate read-only remount checking every payload filename, deleted name,
  and all bytes of 1,365 files plus the synchronization marker.

`strace -D` preserves fuse2fs as Apptainer's direct child. The known
fuse-overlayfs `.wh..wh..opq` marker is checked separately as empty overlay
metadata when reading the saved upper directory as a lower layer. Other extra
or missing names still fail the check.

The exact accepted result is
[`qualification.json`](../../../deployment/apptainer-1.4.4-sai/qualification.json).
`runtime-qualification-receipt.json` pins the submitted script, deployed probe,
raw logs and traces, along with completed Slurm accounting.
`qualified-teardown-events.log` extracts the payload, stop and remount events;
full logs stay in the recorded project directory.

Negative control **2135324**, on `d9470n11`, used the original public starter
with the same 2-second fsync injection. The payload synchronized and returned
zero, then Apptainer terminated fuse2fs and reported a cleanup error. The
verification correctly rejected the image. This control job's `0:0` means the
expected failure was reproduced, not that the old image was accepted. See
`original-deadline.json` and `verify_original_deadline.py`.

Probe preparation failures were retained under the same project diagnostics
root. A new unapproved runtime path was rejected by AppArmor; initial nested
probes exposed missing LDAP identity resolution, inherited fakeroot state,
and project default ACLs. The accepted probe uses the existing approved CLI
path, a root-mapped outer namespace without the fakeroot preload command,
cleared inherited bind variables, and production-equivalent private scratch
permissions. Job **2134871** already passed writes, teardown and fsck, but its
strict directory-name assertion rejected the overlay metadata marker; its
unaccepted result is retained as `opaque-marker-probe.json`. Only **2135060**
is the accepted complete image qualification.

## Repository validation

The source/ABACUS revision `7e58a7978188ef66f37f5e263fe6177287f82628` passed all six
triggered GitHub workflows. The full suite ran **1,155 tests, 2 skipped** in
[Build HPC software](https://github.com/AI4SAI/sai-hpc-software/actions/runs/41467206913).
The added installer tests cover read-only checks, idempotent atomic installation,
verification, rollback after removal of the staged payload, rejection of changed
inputs/failed integrity evidence, and failure before replacement on copy damage.

The earlier local full run lacked CMake and ran while the transport payload
manifest was being updated. Its three errors passed a focused rerun after the
environment and payload were complete; the clean GitHub suite above is the full
validation result. Local logs remain in `.work/daily-timeout-fixes-20261102/logs`.
