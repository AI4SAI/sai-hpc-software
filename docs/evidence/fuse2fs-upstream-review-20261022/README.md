# fuse2fs upstream candidates prepared for fork review

Two independent PRs target **Stardust0831/e2fsprogs:master** for human review:

- [PR 1: open norecovery images read-only](https://github.com/Stardust0831/e2fsprogs/pull/1), commit `025071169ddc9fd8cf44e7aa8d7a826f74a261bc`.
- [PR 2: retain image locks on inherited file descriptions](https://github.com/Stardust0831/e2fsprogs/pull/2), commit `b4f3234b1c1240b18dd87d7aed37e49c8247a35f`.

Both use upstream master `8fd79523d051d5ea881237f27eb1c664486e0078` as their
base. Upstream submission and merging these fork PRs await human approval.
The existing public binary remains the separately qualified build described
in [the installed acceptance](../fuse2fs-installed-20261022/README.md).
Its installation/module files were not replaced by these candidate binaries.

The performance changes for dirsync and ordinary-file close are already in
upstream master (`8e09e6bb49baf22a4c0555ea098f95182a1e8dc6` and
`45651efd6db7da8738bc9d8d1f17e99259cabfe4`), so neither PR duplicates them.

## Candidate behavior

The read-only fix applies to `norecovery`/`noload`: open the backing image with
`O_RDONLY` even when clean, force the read-only mount regardless of `rw` option
order, and avoid on-image error bookkeeping when the backend is read-only.
Ordinary `ro` retains journal-recovery semantics. The tests inspect actual
backing descriptor flags, `EROFS`, complete-image hashes, the recovery flag,
and a deliberately corrupt directory. The error-action switch still executes.
The recovery case sets `needs_recovery` on an otherwise clean image; it does
not simulate arbitrary damaged journal transactions.

The lock fix retains BSD flock and acquires it on a duplicate of an inherited
`/proc/self/fd/N` regular-file descriptor. On Lustre this also avoids conflict
with the caller's OFD record lock. Tests cover writer/reader exclusion,
ordinary paths, paths relative to directory descriptors, image offsets,
daemonization with the image on stdin, and release after all references close.
It does not explicitly unlock a caller-owned open file description. Retaining
the usual BSD lock matters on local filesystems where OFD and BSD locks are
independent. Non-Linux builds retain the existing ordinary locking path.

## Container builds and real filesystem tests

All four variants were built inside Apptainer 1.4.4 using GCC 14.3.0 and
libfuse3 3.14.0. Job **1900307**, DSPRHBM `d9470n03`, completed `0:0` in
**3m31s**. The tests then ran as the ordinary `stardust` user with image files
on home-directory Lustre. Job **1900435**, `d9470n01`, completed `0:0` in
**1m38s** and exercised the same binaries with image files on private tmpfs
inside an Apptainer user namespace. The tmpfs mount was placed below the
home-bound work directory, and its images/logs were archived to home before
unmount. All container `/tmp` paths and host caches were backed by home.

| Variant | Lustre | Private tmpfs |
| --- | --- | --- |
| Unpatched master, norecovery suite | Expected failure; unittest reports 6 failed assertions | Expected failure; 7 failed assertions |
| Unpatched master, lock suite | Expected failure; 5 tests fail | Expected failure; 3 fail, 1 skipped |
| Norecovery fix alone | 7 tests pass | 7 tests pass |
| Lock fix alone | 8 tests pass | 7 pass, 1 skipped |
| Both fixes, norecovery suite | 7 tests pass | 7 tests pass |
| Both fixes, lock suite | 8 tests pass | 7 pass, 1 skipped |

The tmpfs shared-reader case is skipped because its permission-based fallback
requires a non-root effective UID; this case passed on Lustre. The tmpfs
daemon cleanup emits a fusermount3 warning about a read-only mount-table lock
file, but unmount, lock release, and teardown assertions all pass. This is
local kernel-lock coverage, not qualification of ext4/XFS/NFS backing storage
or abrupt-failure recovery. Logs retain every baseline failure and skip.

The pinned master omits `stdbool.h`. Every variant uses the same narrowly
scoped build-only workaround, `LOCAL_CFLAGS="-include stdbool.h"` for the
`misc` target. A separate upstream contribution already covers this header
issue; neither candidate contains a duplicate fix. The build preserves
configure's FUSE include/library flags.

`retained-artifacts.json` records distinct executable hashes for all four
variants. `input-e/source.json` records the exact C-source and regression-test
hashes. The committed fork sources and tests were compared with those hashes
after the runs. The ordinary and combined patches, build/test scripts, source
pin, per-variant compilation/loader logs, unittest output, and per-test results
are included here. Full C snapshots, executables, and test images remain at:

```text
/home/stardust/sai-hpc-software/diagnostics/fuse2fs-upstream-review-20261022/
```

The source archive and tmpfs image archive also have recorded hashes. The
tmpfs archive contains all test images/logs, including baseline failures.
`evidence-SHA256.json` inventories this repository evidence, excluding itself.

## Earlier attempts retained

| Job / attempt | Outcome |
| --- | --- |
| 1900144 / A | Compilation fails because pinned master omits stdbool.h. |
| 1900171 / B | Applying the header globally conflicts with TDB's `typedef int bool`. |
| 1900208 / C | Overriding CFLAGS discards configure's required FUSE include flags. |
| 1900258 / D | Libraries build, but make does not rebuild the driver for subsequent source replacements; all four binary hashes are identical. Its results do not validate the fixes. |
| 1900307 / E | Reuses D's configured libraries and forces a new driver object/executable per variant; all expected results pass. |

Attempt E retains the previous object and executable before rebuilding and
asserts that the four executable hashes differ. The precise reason for D's
make timestamp behavior was not established; the comment in the frozen build
script names timestamp skew as a suspicion. Failed input scripts, available
build logs, accounting, and D's identical binary hashes remain recorded.
The readonly-source/test snapshots and retained binary hashes make E the
authoritative candidate run. The separate tmpfs capability probe, job 1900308,
passed before the actual tmpfs suite.
