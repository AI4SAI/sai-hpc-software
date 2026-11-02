# Daily build timeout diagnosis, 2026-11-02

The [scheduled run 41421871101](https://github.com/AI4SAI/sai-hpc-software/actions/runs/41421871101)
used controller `401436540ac2189556486bc0d57a166564c7debc`. Its failures include
two independent timeout mechanisms: writable container driver shutdown and
Actions-to-cluster source transfer. The affected Slurm jobs ended `FAILED`,
well before their allocation time limits; they did not end `TIMEOUT`.
This record diagnoses the failures and preserves separate reproductions. It
does not change the production controller or claim a successful rebuild.

## Writable image shutdown

CP2K dependency jobs `2128152`/`2128156`, DeePMD jobs
`2128183`/`2128202`/`2128215`, and LAMMPS job `2128190` completed their container
payload phase with exit zero, then reported `Terminating fuse2fs after timeout`
and `Cleanup error: while stopping driver`. The controller correctly rejected
these phases with exit 74. These are not six independently diagnosed compiler
failures, nor are they accepted final artifacts.

[Failure receipts and accounting](failed-stages.json) confirm that all six used
the existing pinned optimized driver with SHA-256
`46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10`.
Their explicit overlay synchronization completed. For example, the CP2K
dependency syncs took 1.615 seconds on 4V100 and 1.046 seconds on DSPRHBM;
DeePMD's 4V100 sync took 13.952 seconds. Adding the same sync again does not
address the remaining shutdown operation.

[Apptainer 1.4.4](https://github.com/apptainer/apptainer/blob/v1.4.4/internal/pkg/image/driver/imagedriver.go#L505)
waits only one second for a driver to exit after unmount, sends SIGTERM, then
sends SIGKILL after one more second. This deadline is independent of Slurm and
GitHub job limits. The
[pinned fuse2fs destroy callback](https://github.com/tytso/e2fsprogs/blob/45651efd6db7da8738bc9d8d1f17e99259cabfe4/misc/fuse2fs.c#L827)
marks the filesystem clean, dirties its superblock and calls `ext2fs_flush2`
again. Thus an earlier successful payload fsync cannot eliminate the final
metadata writes and backing-file fsyncs.

Fresh project-backed probes used the same driver and Apptainer, 2,048 generated
4 KiB files with every third file removed, and an explicit writable-file fsync.
No failed production image was reopened writable. All scratch paths were under
`/org/org-open-sai/proj-sai-hpc-software/diagnostics/`.

| Slurm job | Case | Payload sync | Driver cleanup |
| --- | --- | ---: | --- |
| 2131730 | 512 MiB, no tracing | 0.461 s | SIGTERM then SIGKILL |
| 2131730 | 512 MiB, foreground tracer | 0.331 s | Timeout; trace ends inside final image fsync |
| 2131730 | 32 GiB sparse, no tracing | 7.712 s | SIGTERM then SIGKILL |
| 2131730 | 32 GiB sparse, foreground tracer | 0.664 s | Normal exit |
| 2132097 | 512 MiB, detached tracer A | 0.847 s | Normal exit |
| 2132097 | 512 MiB, detached tracer B | 0.476 s | Normal exit |

Both diagnostic allocations completed `0:0`; the scripts record individual
container cleanup failures instead of treating that allocation status as a
passing container result. [Timestamped events](teardown-events.log) and
[per-case results](teardown-probes.json) retain the distinction.

In the first untraced case, Apptainer began waiting at epoch
`1793624698.7277012`, sent SIGTERM at `1793624699.7280955` and SIGKILL at
`1793624700.7284544`. Its payload fsync had already completed. The failed
foreground trace closes `/dev/fuse`, writes the image's metadata and enters a
new backing-image `fsync` at `1793624724.575236`; the call has not returned when
the tracer is killed. That tracing arrangement changes the immediate child
of Apptainer, so it is not used to assert the driver's precise signal status or
a completed fsync duration. The two untraced reproductions establish that the
failure also occurs without instrumentation. The subsequent `strace -D` probes
preserve the actual driver as Apptainer's child and show normal final fsyncs
and exit zero when latency stays within the deadline.

The `Ignoring invalid max threads value 4294967295` message occurs at startup
in successful cases too. It is retained and repeated by Apptainer when reporting
an abnormal driver exit; its presence is not evidence that it caused the exit.
The generic suggestion about a running background process likewise does not
identify such a process in these failures.

Read-only `e2fsck -fn` returned zero on the fresh diagnostic images. This does
not certify the failed production overlays. The foreground-traced failure
also does not guarantee that its driver had exited before the check. The
existing cleanup-failure guard must remain in place.

The confirmed mechanism is an insufficient shutdown window for the observed
variable filesystem-flush latency. No storage-server telemetry was collected,
so this does not identify the storage-side source of that latency. An attempted
independent graceful-unmount comparison, job `2131989`, was stopped before
mounting by `unshare: write failed /proc/self/uid_map: Operation not permitted`;
[that attempt](grace-attempt.json) is not evidence that a longer deadline has
already fixed the issue.

## ABACUS-specific propagation

ABACUS DSPRHBM job `2128143` reported the same driver teardown failure after
its build/metadata phase, then continued to the next writable container. That
mount failed at Apptainer's separate 10-second startup deadline. The generated
job loads the public `fuse2fs/1.47.4-sai` module, but
[`software_controller.py`](../../../controller/software_controller.py) only
wraps CP2K in the cleanup-failure guard, and
[`container_entry.sh`](../../../controller/container_entry.sh) has no equivalent
explicit overlay synchronization. This explains why ABACUS reports the later
mount failure rather than stopping at the preceding failed shutdown. The
original ABACUS overlay has not been declared corrupt or reusable by this check.

## LAMMPS release source transfer

All three new `stable_30Sep2026_update1` release candidates failed before Slurm
submission. [Transfer evidence](source-transfer.json) records approximately
907.6–908.0 MB per target with `base=None`. Each upload has eight approximately
113.5 MB parts. `md_lammps_ci.upload` imposes 1,800 seconds per attempt and
retries three times without resuming partial transfers. The observed jobs spent
about 90 minutes in transfer attempts before failing; the final 8V100V0 attempt
failed at 21:06:51 China time.

The full-history transfer was avoidable:

1. The remote cache contains the previous accepted release
   `8de817dd79bfe4525d5d39246a212d833e6dee07`.
2. That ref ranks 16th by commit date. `source_cache.inventory` returns only the
   newest 12 tips, which here are development commits.
3. The controller only considers a returned tip itself as an incremental base
   when it is an ancestor of the requested commit. It does not derive a common
   ancestor from cached development history.
4. GitHub's commit comparison confirms that the excluded old release is an
   ancestor of new release `098ffdefa525cabc82272925de02e9884f58c657`, with 129
   commits ahead and none behind.

The same uncached source is prepared independently by three target jobs,
duplicating the approximately 0.9 GB upload. At the first remote snapshot, the
failed 16V100 transfer retained zero complete parts, and 4V100 retained only two
of eight. These partial-file sizes are not reliable throughput measurements:
retries can overwrite existing partial files. The transport logs prove the
per-attempt deadline expired; they do not locate an ISP or server bottleneck.

## Follow-up work supported by this diagnosis

- Give writable drivers a longer bounded graceful-shutdown window, and verify
  the change against these reproductions before using it for builds. Retain
  payload fsync, driver-exit checking and offline filesystem checks.
- Apply the same synchronization and failure guard to ABACUS, so an unclean
  shutdown cannot silently advance to export or remount.
- Select a valid shared-history base for new releases, stage each source SHA
  once before the target matrix, and use verified resumable transfer for large
  parts. Increasing the compilation time limit does not address either failure.

The tested diagnostic scripts and their exact submission hashes are retained
alongside this record. Full syscall traces remain in the corresponding project
diagnostic directories; their hashes are in `teardown-probes.json`.
