# CP2K build I/O efficiency — 2026-10-17

The immediate bottleneck observed in the four CP2K release dependency builds
is synchronous I/O through the home-backed ext image, not missing `make -j`
or a CPU quota. All build storage, caches and temporary files remain in home.
No software free-space reservations are needed for this investigation.

## Live build evidence

Read-only SSH from the login node to the four compute nodes sampled the jobs
from approximately 19:05:46 to 19:05:56 Asia/Shanghai. CPU usage comes from
the complete job cgroup, including short-lived compiler processes. One core
means one logical CPU fully occupied.

| Partition | Job | Allocated CPUs | Used cores during sample | Allocation utilization |
|---|---|---:|---:|---:|
| 16V100 | 1829656 | 8 | 0.0650 | 0.81% |
| 8V100V0 | 1829644 | 12 | 0.0483 | 0.40% |
| 4V100 | 1828944 | 8 | 0.0618 | 0.77% |
| DSPRHBM | 1828946 | 8 | 2.3286 | 29.11% |

All four had the expected CPU affinity, `cpu.max=max`, zero additional CPU
throttling, and live `MAKEFLAGS` containing `-j8` or `-j12`. Every partition
showed `fuse2fs` waiting at Lustre `osc_io_fsync_end`; compiler/CMake and
`fuse-overlayfs` processes waited at `request_wait_answer`. These snapshots
do not establish Lustre's maximum bandwidth or whole-build CPU utilization.

Site cgroups use opaque names, not `job_<id>`. The corrected sampler locates
processes by `SLURM_JOB_ID`, then samples the entire corresponding job cgroup.
An earlier sample based on the wrong naming assumption is retained but
excluded. Diagnostics used SSH, without adding an `srun` step to any GPU job.

## Driver behavior and candidate

Apptainer 1.4.4 uses bundled `fuse2fs 1.47.3` and `fuse-overlayfs 1.15`.
In [fuse2fs 1.47.3](https://github.com/tytso/e2fsprogs/blob/v1.47.3/misc/fuse2fs.c),
`op_rename` calls `ext2fs_flush2(fs, 0)` while holding the global filesystem
mutex. The library flushes the entire image and calls the backing file's
`fsync` twice. Thus frequent atomic dependency-file replacements can serialize
otherwise parallel compilation on remote storage. `fuse-overlayfs` also uses
temporary-file renames for copy-up and some creation/ownership paths. Direct
creation is possible for matching ownership, so not every creation flushes.

Upstream [8e09e6b](https://github.com/tytso/e2fsprogs/commit/8e09e6bb49baf22a4c0555ea098f95182a1e8dc6)
limits these directory-operation flushes to explicitly requested `dirsync`
semantics. [45651ef](https://github.com/tytso/e2fsprogs/commit/45651efd6db7da8738bc9d8d1f17e99259cabfe4)
also avoids flushing ordinary files on close. Explicit `fsync` and clean
unmount flushing remain. These changes are **after** v1.47.4; upgrading only
to that release does not include them.

The isolated candidate pins upstream commit
`45651efd6db7da8738bc9d8d1f17e99259cabfe4` (18 commits after v1.47.4).
Its source archive SHA-256 is
`3ba72ea2ec624fcbe8c155656623c16da92e07dff4a7ffb981dcadbd9990ccf4`.
It is built with GCC 14.3.0 inside a new home-backed overlay. FUSE development
headers come from Ubuntu's matching `libfuse3-dev_3.14.0-5build1_amd64.deb`,
checked against the site's apt metadata SHA-256
`cfa8460d23b5143d14bb336b3008784df72a5e9fc32057ec1dc85beadca9aecb`.
No system driver or active controller is replaced. The candidate's version
string still says 1.47.4; the exact source commit and binary checksum identify
it, not that string.

## Isolated validation

Job `1830278` on DSPRHBM built the upstream candidate and tried the original
driver, `cache_size=256M`, and `directio`. The original workload completed in
49.90 seconds, the cache variant took 80.40 seconds, and direct I/O exceeded
the 240-second limit. These are single trials, not broad tuning conclusions.
The job stopped on that timeout; no candidate comparison ran in this job.

Unmodified upstream failed to mount in 16V100 job `1830364`: its newly added
BSD image lock conflicts on Lustre with Apptainer's existing OFD lock. The
[small adapter](evidence/fuse2fs-efficiency-20261017/fuse2fs-inherited-ofd.patch)
uses `F_OFD_SETLK` on the inherited `/proc/self/fd/N` descriptor for regular,
whole-file images. It keeps the descriptor and lock alive until driver exit.
Normal pathname mounts retain upstream's BSD lock; no `fsync` is intercepted
or disabled. The resulting binary SHA-256 is
`0680f553de17cba684a6f5ea265c9d80bd6d576d68aebc4e788e2609a66ad5e9`.

Corrected comparison `1830438` completed `0:0` on DSPRHBM in 2:34. Four fresh
256 MiB images ran in order: stock, candidate, candidate, stock. The 16V100
counterpart `1830468` completed `0:0` in 2:50, using the same candidate bytes
on `16v100n04`, where the ongoing CP2K job was also allocated.
The workload creates and renames 64 files, compiles 32 C++ units with GCC 14.3.0
at eight-way parallelism, atomically renames dependency files, and explicitly
calls `fsync`. It retains syscall traces and checks the image with read-only
`e2fsck`, then remounts and verifies all file hashes. The same Apptainer/FUSE
overlay stack and host read-only compiler/library binds are used. Syscall
tracing is enabled equally for both variants.

| DSPRHBM, two trials per driver | Original median | Candidate median | Original / candidate |
|---|---:|---:|---:|
| 64 atomic renames | 20.122 s | 0.0356 s | 565.0 |
| 32 C++ compilations at `-j8` | 24.862 s | 0.8915 s | 27.89 |
| Explicit file `fsync` | 0.261 s | 0.211 s | 1.24 |

| 16V100, two trials per driver | Original median | Candidate median | Original / candidate |
|---|---:|---:|---:|
| 64 atomic renames | 32.387 s | 0.0228 s | 1422.6 |
| 32 C++ compilations at `-j8` | 32.455 s | 1.6066 s | 20.20 |
| Explicit file `fsync` | 0.353 s | 0.447 s | 0.79 |

The candidate used 6.59–6.68 client CPU cores during compilation, compared
with 0.16–0.42 for the original. Stock rename phases each issued 128 backing
image `fsync` calls; candidate rename phases issued zero. Compilation issued
128–130 versus zero. Both variants' explicit `fsync` phases still issued two
successful backing-image `fsync` calls. All 161 file hashes were identical
across runs, all read-only filesystem checks passed, and clean remounts
reproduced every file. A concurrent POSIX write lock and a second writable
Apptainer mount were both rejected while the candidate was mounted; a write
lock succeeded after unmount.

16V100 independently passed those same checks, with all 161 file hashes
identical across its four runs. Compilation used 7.34–7.46 cores with the
candidate and 0.28–0.37 cores with stock. Its tracing likewise recorded 128
`fsync` calls per rename phase and 128–130 per compilation phase for stock,
zero for those phases with the candidate, and two successful calls per
explicit-sync phase for both. Thus the improvement comes from avoiding
unrequested whole-image synchronization, while requested synchronization
continues to reach Lustre.

Raw scripts, archives, build logs, traces and images are under
`~/sai-hpc-software/diagnostics/cp2k-release-20261016/fuse2fs-efficiency-20261017-a/`.
Corrected runs use sibling directories `fuse2fs-efficiency-ofd-20261017-a`
and `fuse2fs-efficiency-16v100-ofd-20261017-a`. The preceding live utilization
samples are in their parent. [DSPRHBM raw-derived summary and hashes](evidence/fuse2fs-efficiency-20261017/dsprhbm-summary.json)
and [live job measurements](evidence/fuse2fs-efficiency-20261017/live-cpu-io.json)
are retained in the repository, along with the exact experimental scripts.
[16V100 comparison and hashes](evidence/fuse2fs-efficiency-20261017/16v100-summary.json)
and [cache/direct-I/O experiment](evidence/fuse2fs-efficiency-20261017/options-summary.json)
retain the independent confirmation and unsuccessful options.

At 19:49 Asia/Shanghai, the four original CP2K jobs remained RUNNING:
`1829656` (16V100), `1829644` (8V100V0), `1828944` (4V100), and `1828946`
(DSPRHBM). Neither driver experiment changed their mounts or recipes.
[Final Slurm accounting and evidence hashes](evidence/fuse2fs-efficiency-20261017/final-accounting.json).

## Integration boundary

A measured driver improvement should be introduced through a new immutable
controller/recipe and a pinned driver checksum. An exact per-run Apptainer
configuration can select a private driver; its `binary path` must put that
driver before `$PATH`, because Apptainer inserts its bundled drivers at the
`$PATH` position. Merely prepending the shell's PATH does not override them.
Use the exact private driver with its checksum verified before launch. Do not
replace the system installation or switch a mounted image to another driver.
The production integration below uses fresh CP2K 2026.2 identities with this
pinned adapter; actual dependency/application results remain acceptance gates.
Adding CPUs or moving files to host `/tmp` is not the remedy supported by
these measurements.

Running dependency jobs and their checkpoints retain their original identities.
A small compilation and successful clean remount do not constitute CP2K
application, scientific, crash-recovery or publication acceptance. Subsequent
validation must run the actual CP2K dependency/application stages and retain
the existing science/export/publication gates.

## Production integration (2026-10-17)

New CP2K recipes use `controller/cp2k_image_driver.py` for dependency and
application overlay mounts, export and read-only squashfs extraction. The
final SIF verifier continues to run independently without an overlay. The
wrapper verifies the exact binary checksum and creates a per-run Apptainer
configuration whose `binary path` places the private driver before `$PATH`.
It leaves the existing container bind/isolation arguments intact.

The tested binary is provisioned read-only at
`~/sai-hpc-software/cache/tools/fuse2fs-0680f553de17cba684a6f5ea265c9d80bd6d576d68aebc4e788e2609a66ad5e9/bin/fuse2fs`.
Its source and exact rebuild inputs are archived above. A missing, writable,
redirected or changed binary is rejected before mounting the build image.
`results/image-driver.json` records the source commit, patched source hash,
binary hash and configuration hash; dependency sealing includes this receipt,
and the application retains it under `share/sai/image-driver.json`.

The driver wrapper is part of the recipe fingerprint and CI upload contract.
New dependency and application singleton names include the recipe hash, so
they can proceed alongside a historical recipe without modifying its controller
or overlay. No prior checkpoint is relabelled with this driver. All driver,
configuration, temporary and image files remain under the project home.

Workflow [39032306213](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39032306213)
uses controller `1e7d736e5884fe207460a3e6baf0aeddb5bdb4aa` and recipe
`9c87f65a7c0a1bb7638f5e942f59bd7bdde750d7cf6cd5420c90cfa13cc517ad`.
Real dependency jobs started on all four partitions: 16V100 `1830993`,
4V100 `1830994`, 8V100V0 `1830995`, and DSPRHBM `1830997`.

At 20:30:38 Asia/Shanghai, a ten-second cgroup sample of new 16V100 job
`1830993` measured 7.144 of eight logical CPUs (89.30%). Its live driver process
used the pinned private executable. Old job `1829656` on the same node measured
0.492 CPUs (6.15%) over the concurrent sample. These are different dependency
stages, so the ratio is not a whole-build speedup. The new job had already
installed tiled-mm in 17 seconds and costa in 45 seconds while compiling the
remaining dependencies. Raw samples remain in the home diagnostics directory
as `cpu-io-1830993-production1-20261017.json` and
`cpu-io-1829656-production1-20261017.json`.

At 20:36:23, all four new allocations were still RUNNING and had completed
HDF5: 16V100 2:50, 4V100 1:50, 8V100V0 2:38, DSPRHBM 3:27. 16V100 and
4V100 had also finished Cython and entered PLUMED compilation; Libint remained
in progress. A later 20:33 sample measured 21.83%, 23.89%, 36.66% and 35.30%
allocation CPU utilization respectively, with Libint generation and other
serial/file operations visible. The earlier 89.30% sample is not a promise
of constant utilization. [Production snapshot, driver/compiler pins and raw
sample hashes](evidence/cp2k-private-driver-production-20261017.json).

CI validation passed 709 tests (707 passed, two optional skips). The 20 focused
driver, dependency-seal and seed-order tests also passed on SAI with temporary
files in home. Build success, final-SIF loader/version checks, science and
publication are reported separately from these integration checks.

