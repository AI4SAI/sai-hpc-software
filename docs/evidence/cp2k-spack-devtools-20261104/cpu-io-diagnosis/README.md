# DSPRHBM dependency build I/O diagnosis

The slow build `2170274` on `d9470n01` experienced long delays reading its
Lustre-backed image. A separate two-node control reproduced multi-second
4 KiB reads on that node without Apptainer or FUSE. This establishes a problem
on the node-to-Lustre read path; it does not identify a particular client,
network or storage-server component as the root cause.

Libint subsequently completed its build and container installation in
**7h24m**. All dependency installation finished at **2026-11-04 10:05:22 UTC**.
The existing workflow then completed Squashfs creation, explicit synchronization
and natural managed-overlay teardown. SIF consumer acceptance remains a
separate gate; see the handoff evidence for its eventual status.

## Resource and process evidence

At 09:28:38 UTC the complete job cgroup had used 15,156 CPU seconds during
24,993 elapsed seconds: **0.606 cores on average, or 7.58% of the eight-core
allocation**. This includes exited compiler processes. A separate 12-second
interval used 3.11 cores, illustrating why a short snapshot is insufficient
to describe the whole build.

- `MAKEFLAGS` was `-j8`; affinity and cgroup cpuset both allowed CPUs 93–100.
- Every readable cgroup ancestor had unlimited CPU quota and zero throttling.
- Job memory was 5.60 GB against a 30.06 GB limit (28 GiB), with no memory-limit
  events or OOM events in that job. Historical events in unrelated parent
  cgroups were not attributed to this build.
- The job's accumulated full I/O pressure was 13,586 seconds, **54.36%** of
  elapsed time. This is the job cgroup, not the whole-node pressure metric.
- Compiler processes waited in `request_wait_answer`; a fuse2fs worker was
  observed in Lustre's `cl_sync_io_wait`.
- The managed driver's backing descriptor was the expected project
  `work.ext3`, opened read/write without `O_DIRECT`, using the pinned driver
  SHA-256 `46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10`.

Entering the existing driver namespace with `nsenter --preserve-credentials`
allowed read-only inspection of the live build without remounting its image.
At 09:36 UTC, 5,683 of 5,963 generated `.cc` files had complete `.lo` objects.
The Makefile builds these generated sources; this count describes source
compilation only, excluding linking, installation and SIF acceptance.

A short syscall trace observed a 4 KiB `pread64` on the backing image remain
unfinished through the tracing window. It observed no `fsync` calls in that
window. Another later window, during installation, predominantly observed
fast buffered writes. Both raw traces are retained, and the original driver
remained alive and detached from the tracer afterwards. Tracing may perturb
timing; the independent comparison below did not use tracing.

The pinned fuse2fs source holds its filesystem mutex around `op_read` and
`ext2fs_file_read`. A slow backing read can therefore delay other filesystem
requests as well. This explains amplification of storage latency; removing
that mutex without establishing libext2fs thread safety is not a validated
fix. The evidence does not justify replacing Apptainer or reverting the
existing flush/teardown repairs.

## Independent read comparison

Job **2191287** completed `0:0` on `d9470n01` and `d9470n18` in 57 seconds,
allocating one CPU per node. The script opened three completed GPU build images
read-only and read the same 24 deterministic offsets per image, twice. Each
read was 4 KiB. It never opened the active CPU image and never wrote to the
input images. All scratch, logs and scripts were in project storage.

| Completed image / Lustre target | First-pass total on d9470n01 | First-pass total on d9470n18 | Maximum single read on d9470n01 |
| --- | ---: | ---: | ---: |
| 16V100 / OST 3 | 0.0176 s | 0.0965 s | 0.00421 s |
| 4V100 / OST 2 | 26.6799 s | 0.8912 s | 7.33494 s |
| 8V100V0 / OST 1 | 13.9142 s | 0.1345 s | 13.88870 s |

Including metadata inspection and both read passes, the same probe took
**40.792 s** on d9470n01 and **1.169 s** on d9470n18. Repeated reads were fast
on both nodes: each 24-read repeat took less than 0.00009 seconds. The first
passes contained 22 nonzero blocks out of 24 on both nodes for each image.

Caches were not dropped. These are first-pass and repeat measurements, not
controlled cold-cache benchmarks. The result demonstrates severe read latency
outliers on d9470n01 during the observation; it is not a whole-build speedup
claim or proof of a permanently faulty node. Multiple storage targets showed
outliers, and the current CPU image shares OST 3 with the successfully built
16V100 image, so this evidence does not support blacklisting one OST.

## Operational disposition

The original build was allowed to finish its compilation; no source, driver,
CPU flags, live controller or allocation was changed. The separate read probe
temporarily waited for `rush-cpu` allocation capacity, then completed normally.
That queue limit does not throttle CPU execution inside the original job.

For a new recovery allocation, prefer a node without the observed read stalls
and retain the existing immutable-run acceptance gates. A permanent scheduler
exclusion or storage-client change requires broader evidence. Once the shared
libraries have been installed and CP2K reuse has passed scientific acceptance,
daily CP2K revisions will skip this dependency rebuild altogether.

`summary.json` contains the calculated metrics and raw-file hashes. Compressed
raw observations and the three probe scripts are included here. The independent
cluster probe and Slurm logs are also retained under:

```text
/org/org-open-sai/proj-sai-hpc-software/operations/cp2k-dsprhbm-io-20261104-a
```
