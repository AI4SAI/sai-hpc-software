# Writable-image teardown latency on Lustre

A fresh reproduction localized a 28.884-second normal teardown to two successful
`fsync` calls on the Lustre-backed `work.ext3`: 16.616 and 12.223 seconds, or
99.84% of teardown time. The unmount command took 0.018 seconds. The trace stacks
identify `op_destroy -> ext2fs_flush2 -> unix_flush -> fsync`; the driver exited
naturally with status zero. A separate native file control, outside all
containers and image drivers, also observed a 4.128-second `fsync` for a 1 MiB
file on Lustre.

This establishes the immediate source of the reproduced latency: synchronous
backing-file persistence during filesystem shutdown. Client-side evidence does
not distinguish storage-server queues, network delays, or disk response. The
earlier 6.894-second sample had no syscall tracing and cannot be reconstructed
retroactively. The new slow sample used OST 1, but the same target also completed
other samples quickly. These observations do not establish a permanently slow
or faulty storage target.

## Experiments

All runs kept the installed Apptainer 1.4.4, pinned fuse2fs and minimal base SIF.
Only private copies of the workflow helper received diagnostic timing/tracing.
Production code and shutdown policy were not changed. Builds, images, trace
files and native controls remained on project storage. The original images
and failed scientific runs were not modified.

| Job | Node | Cases | Result |
| --- | --- | --- | --- |
| 2151763 | 16v100n01 | 12 fresh images; alternate stage timing and narrow syscall tracing | Completed `0:0`, all checks passed |
| 2151858 | 16v100n61 | 8 fresh images; production marker fsync on/off, OST 1/5, syscall stacks; 16 native fsync controls | Completed `0:0`, all checks passed |

Job 2151617 was cancelled while pending because its requested original node was
unavailable. No workload ran in that allocation. Job 2151763 used any available
16V100 node instead; every comparison within a job stayed on that job's node.

Every image case used the same 4,096-file workload and parallel compilation of
64 C translation units as the earlier efficiency experiment. Each passed the
full NVIDIA driver check, natural driver exit, offline `e2fsck -fn`, and an
independent read-only remount checking file bytes/deletions and the executable.
There was no artificial fsync delay or weakened synchronization.

The timing-only and narrow-trace cases in job 2151763 ranged from 0.272 to
1.314 seconds. The slowest narrow-trace case spent 0.829 and 0.312 seconds in its
two shutdown fsync calls. Mountpoint inspection and `umount` were small; waiting
for the writer accounted for most elapsed time.

## Reproduced slow case

Job 2151858, case index 4, used OST 1 and the **existing production marker-file
fsync helper** before container exit:

| Stage | Seconds |
| --- | ---: |
| Pre-exit marker fsync (inside payload, separate from teardown) | 5.726949 |
| Mountpoint inspection | 0.000213 |
| Unmount command | 0.017875 |
| Wait for actual fuse2fs child | 28.865967 |
| Total teardown | 28.884065 |
| First shutdown image fsync | 16.616070 |
| Second shutdown image fsync | 12.222651 |

The two calls belong to `op_destroy`. In the pinned e2fsprogs source
`45651efd6db7da8738bc9d8d1f17e99259cabfe4`, this callback marks the filesystem
clean and flushes its state. `ext2fs_flush2` synchronizes preceding writes before
writing the primary superblock, then synchronizes again afterward. These are
the two ordered persistence calls in the captured stacks. They are not a
workflow sleep or the 120-second deadline.

Source references:

- [fuse2fs shutdown callback](https://github.com/tytso/e2fsprogs/blob/45651efd6db7da8738bc9d8d1f17e99259cabfe4/misc/fuse2fs.c#L827)
- [libext2fs ordered superblock flush](https://github.com/tytso/e2fsprogs/blob/45651efd6db7da8738bc9d8d1f17e99259cabfe4/lib/ext2fs/closefs.c#L420)
- [backing-file fsync](https://github.com/tytso/e2fsprogs/blob/45651efd6db7da8738bc9d8d1f17e99259cabfe4/lib/ext2fs/unix_io.c#L1521)

The native controls alternated eight fresh 1 MiB files on each of OST 1 and
OST 5. Each file was written, explicitly synchronized and read back unchanged.
Observed fsync durations ranged from 0.057 to 4.128 seconds. The long native
call demonstrates that second-scale synchronization latency also occurs without
container startup, overlays, or fuse2fs.

## Syncfs versus the production marker

The earlier microbenchmark called `syncfs` on a directory in the merged overlay.
In this installed stack, that successful call produced no image-level fsync
before shutdown. It is not evidence that userspace filesystem metadata had
already been persisted. The earlier offline/remount integrity results remain
valid, but that microbenchmark did not duplicate the production pre-exit sync.

The real workflow uses `cp2k_overlay_sync.synchronize`, which opens a writable
marker and calls `fsync` on it. Stack traces confirm this enters `op_fsync` and
executes the corresponding two backing-image fsync calls before shutdown.
Shutdown still needs to persist its final clean-state update. Case 4 shows that
even successful pre-exit synchronization cannot guarantee a short final flush.
Moving a synchronization call earlier changes where its latency is recorded;
these tests do not demonstrate a reduction in total build time.

The current 120-second natural-exit window correctly allowed the reproduced
28.884-second teardown to finish. Keep both persistence barriers and the existing
failure guard. No further fuse2fs/runtime change is supported by these results.

## Evidence

`summary.json` separates unmount, driver wait and individual image fsync calls.
The two `benchmark-*.json` files retain payload and helper hashes, all case
receipts, validation predicates, stripe information and native controls.
`slow-example/` exposes the selected complete trace, receipt, sync record and
integrity logs directly for review.

The two `raw-*.tar.gz` archives preserve all 560 collected text files, including
the diagnostic scripts, original/timed/traced controller snapshots, raw traces,
logs, receipts and accounting. Each `manifest-*.json` contains the SHA256 of every
uncompressed member. Archives use deterministic metadata. The cancelled pending
submission is retained in the first archive. Originals remain in project
`diagnostics/overlay-teardown{,-sync}-20261103` and fresh
`runs/overlay-teardown-*`, `runs/overlay-teardown-sync-*` and
`runs/overlay-host-sync-*` directories.
