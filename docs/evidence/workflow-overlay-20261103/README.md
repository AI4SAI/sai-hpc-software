# Workflow-owned writable mounts with stock Apptainer

All tests use the existing `/opt/devtools/apptainer/1.4.4/bin/apptainer` and the
existing pinned fuse2fs. No public file, system policy, SIF base or driver binary
was replaced. The stock CLI SHA256 is
`2ba9ae17c087a0d8a6bbfeaae0b12775bd8724f51d5452c41e951ea853a8e5da`, its starter is
`6c1b664b849689a30b8ad8ed4a09bf8aa177ced8188c37d28f44cd9fbfe414d1`, and fuse2fs is
`46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10`.

The workflow starts a short outer invocation using that permitted executable
and the same minimal SIF. Within its root-mapped namespace, it starts fuse2fs
in foreground mode and gives the mounted image to the original build command
as a directory overlay. The build retains its existing flags, dependency binds,
GPU selection and final installation prefix. After the payload finishes, the
manager performs a normal unmount and waits for its actual fuse2fs child.
Apptainer's one-second image-driver deadline does not supervise that child.

The manager allows up to 120 seconds for natural driver exit. A failed unmount,
nonzero driver status, forced termination, or absent completed receipt rejects
the phase. A payload failure is preserved when cleanup succeeds. Existing
offline filesystem checks remain required before consuming writable output.
This does not weaken fsync, locking, image validation or scientific acceptance.

Build trees remain inside the ext image on project storage. Runtime scratch
also stays within the project. The manager adds small receipts and mount/session
directories. Final read-only SIF launch and native `/opt` applications are unaffected.
There is one additional container setup per writable phase, with no persistent
service and no additional MPI/GPU launch wrapper for published software.

## Qualification

| Partition | Four-case qualification | Final receipt-guard retest |
| --- | --- | --- |
| DSPRHBM | 2149250 | 2149471 |
| 16V100 | 2149429 | 2149472 |
| 4V100 | 2149430 | 2149473 |
| 8V100V0 | 2149431 | 2149474 |

Every allocation completed with `0:0`; each case also independently passed its
recorded acceptance predicates. The four cases are a fresh 512 MiB image, a
sparse 32 GiB image, injected two-second fsync delays, and a payload that exits
37. Each writes 2,048 files, deletes every third file, explicitly synchronizes,
compiles and executes a 32-unit C program in parallel, checks environment
forwarding, and then undergoes offline `e2fsck -fn` and separate read-only
remount verification of all remaining file names/bytes and the executable.
The GPU cases load `libcuda.so.1`, call `cuInit`, and enumerate devices through
the unchanged inner `--nv` command.

The injected delay uses `strace -D`, preserving fuse2fs as the actual managed
child. The `controller-trace-*` copies add only the recorded tracing command
before driver execution. They leave I/O results unchanged. The final `v2`
controller additionally requires the completed driver receipt even when the
outer Apptainer reports zero; its delayed-fsync case passed on all four targets.
The historical stock supervision negative control remains in
[`daily-timeout-fixes-20261102`](../daily-timeout-fixes-20261102/README.md): job
2135324 reproduced premature termination under the same two-second fsync delay.

Lock/stream job **2149440** also completed `0:0`: a competing nonblocking write
lock was rejected while the payload held the image, the lock became available
after driver exit, and stdout bytes `00 ff 0d 0a` survived both containers
unchanged. The intentional payload-error case is a passing rejection test;
its overlay is retained as diagnostic evidence and is not published.

The original small pilot, job 2149153, demonstrated a 4.45-second natural
teardown under injected delay before integration. It is not counted as one of
the final controller qualification cases.

Raw logs, traces, scripts, controller snapshots, hashes and Slurm accounting
are retained here and under
`/org/org-open-sai/proj-sai-hpc-software/diagnostics/fuse2fs-workflow-20261103`.
Images remain in fresh `runs/overlay-qual-*` and `runs/overlay-lock-*` directories.
These checks establish writable-image lifecycle and GPU access, not completion
of a fresh ABACUS/CP2K/DeePMD/LAMMPS scientific build matrix.
