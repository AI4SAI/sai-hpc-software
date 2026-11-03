# Managed overlay efficiency and complete GPU driver discovery

The first production daily run after PR #50 exposed a missing outer-container
binding: nested `--nv` calls `ldconfig -p` in the management namespace, but the
minimal base has no host `/etc/ld.so.cache`. CUDA initialization alone passed
because `/usr` was available; the complete DeePMD/LAMMPS injected-library check
correctly rejected the incomplete set. The original failures are retained under
`raw/deepmd-before` and `raw/lammps-before`.

The manager now binds the matching host `/etc/ld.so.cache` read-only alongside
host `/usr`, `/lib` and `/sbin`. GPU library injection still happens in the inner
container. No Apptainer, fuse2fs, base SIF, public installation or system policy
was replaced. Every affected recipe already fingerprints `managed_overlay.py`.

## Same-allocation comparison

Job **2150498**, on `16v100n04`, completed `0:0`. Both paths use the same base SIF,
GCC 14.3.0, pinned fuse2fs, eight allocated logical CPUs, GPU and container flags.
Each sample gets a fresh sparse 1 GiB image. The direct baseline uses stock
Apptainer supervision with the existing guarded `execute` function; the managed
case uses the current production helper. No tracing or artificial fsync delay
is enabled.

Each mechanism gets one warmup, then three measured samples in alternating
pair order: direct/managed, managed/direct, direct/managed. The image-contained
payload writes, removes and reads 4,096 files; compiles 64 C translation units
with eight workers; links and executes their combined program; verifies all
required injected NVIDIA libraries through `md_driver_paths.normalize`; calls
CUDA initialization and device enumeration; and explicitly synchronizes the
filesystem. Identical source SHA256 is recorded in every sample.

| Metric, median of three measured samples | Direct | Managed |
| --- | ---: | ---: |
| Compile and link | 6.387 s | 6.626 s |
| File operations | 1.883 s | 1.732 s |
| Complete payload | 9.168 s | 9.198 s |
| Average logical CPUs used by compilers | 7.696 | 7.646 |
| Invocation including setup and teardown | 10.741 s | 12.002 s |
| Time outside payload | 1.113 s | 2.813 s |

The small benchmark shows maintained parallelism and similar payload time, with
additional setup/teardown cost. The compile median is 3.7% longer, within the
overlapping observed ranges (direct 6.248–7.122 s, managed 6.450–6.877 s); three
samples do not establish a precise long-build regression bound. One managed
sample took 17.405 s overall because its recorded natural teardown took 6.894 s.
The other two managed teardowns took 0.375 and 1.176 s. Waiting for actual
filesystem completion is intentional; 120 seconds is a deadline, not a fixed
sleep. These measurements do not quantify full scientific-build throughput or
published SIF application performance.

All eight samples passed clean shutdown, offline `e2fsck -fn`, and an independent
read-only remount verifying file bytes/deletions and the linked executable.
No forced teardown was accepted. Image creation, offline checking and read-only
verification are outside the reported invocation timing and are identical for
both mechanisms.

## Other GPU partitions

Jobs **2150499** (4V100) and **2150500** (8V100V0) also completed `0:0`.
Both passed the real `md_driver_paths.normalize` check, including `libcuda.so.1`,
`libnvidia-ml.so.1` and `libnvidia-ptxjitcompiler.so.1`, byte-identical native
replacements, CUDA initialization/device enumeration, normal driver teardown,
and offline filesystem checking. The previous ldconfig warning disappeared.
The 16V100 benchmark exercises the same complete driver check in every sample.

Scripts, raw logs, lifecycle receipts, measurements, controller hashes and Slurm
accounting are preserved here. Remote originals and images remain under
`/org/org-open-sai/proj-sai-hpc-software/diagnostics/overlay-efficiency-20261103`
and fresh `runs/overlay-efficiency-*` / `runs/overlay-gpu-cache-*` directories.
These checks qualify the wrapper change; fresh DeePMD/LAMMPS scientific builds
still require their usual independent acceptance before publication.
