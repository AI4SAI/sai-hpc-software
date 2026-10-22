# CP2K water scaling: accepted results and remaining comparisons

**2026-10-22 CPU baseline correction: the speedups below are historical
measurements of the tested binaries, not accepted comparisons against a CPU
baseline that uses every allocated core for diagonalization.** Numerical
acceptance remains valid. Investigation of the long sixteen-GPU comparison
found an upstream ScaLAPACK redistribution defect in the pinned CP2K 2026.2
source. All performance conclusions against that CPU binary require review
and a corrected baseline before being treated as final.

The [minimal repair and MPI retest](../../../deployment/cp2k-water-scaling/README-mpi-repair.md)
is now implemented for the CPU companion. Both ISA builds have passed a
byte-identical original-library relink and a one-object repaired relink.
Actual ScaLAPACK tracing has confirmed the predicted participation change on
16V100: 2→4, 4→8 and 8→16 solving ranks, retained through 24 consecutive
diagonalizations with numerical agreement. Repeated timing and the 64-rank
and 4V100 tests are in progress; these early checks are not final speedups.

The follow-up [source confirmation](scalapack-source-check/README.md) finds
the same defect in both official v2026.2 and the inspected current master,
traces it to a 2023 refactor, and links the existing upstream issue #6074.
A compiled reproducer using the actual source routines confirms the half-rank
selection with GCC 14.3.0 at both `-O0` and `-O3`, and checks why both the
initialization and cleanup paths need repair. No public install was changed.

The initialization in
[`cp_fm_diag_utils.F`](https://github.com/cp2k/cp2k/blob/67b5da876dd6a76b8b021d5a04d1c81ba79a4c50/src/fm/cp_fm_diag_utils.F#L147)
assigns `a`, `x` and the printing/ELPA settings, then overwrites the whole
structure with its defaults, including `a=-1,x=-1`. Its optimal-rank formula
therefore returns `-matrix_order`, rather than the intended positive value.
The requested partition is invalid, so `mp_comm_split` silently retains its
fallback of two balanced groups. Only group zero calls `cp_fm_syevd_base`;
the other half of the MPI ranks wait for redistribution of the result. Cleanup
also overwrites the persistent settings, so a repair must preserve them across
successive diagonalizations, not merely fix the first initialization.

This source file is byte-for-byte identical to the pinned upstream file
(SHA-256 `e3edf28d199b4ba624bd94b0e3e73a8863f6db2a7ffc8e4d8e0017f32f7e8c83`).
The original CPU build's source repair only changed `cp_fm_elpa.F`. Raw timers independently
support the unintended rank split: on the 1,452-water CPU trial, the
`cp_fm_syevd_base` average/max times are **17,092.52 / 34,192.44 seconds**,
while `cp_fm_redistribute_end` takes up to **34,250.02 seconds**. These timers
overlap across ranks and must not be added: non-solving ranks wait while the
solving ranks diagonalize. The complete eigensolver path takes **39,894.78 /
43,506.55 seconds (91.7%)** of CP2K time. The 32 MPI × 2 thread trial shows
the same half-average/max signature and about 92.1% in the eigensolver path.
The recorded bindings cover all 64 physical cores. High process CPU usage
can include MPI busy waiting and is not proof that all cores perform useful
diagonalization work. Changing only the MPI/OpenMP layout cannot remove this
source defect. The direct cuSOLVERMp path bypasses this ScaLAPACK routine.

[cpu-scalapack-diagnosis-20261022.json](cpu-scalapack-diagnosis-20261022.json)
records the exact source excerpts and hashes, raw timer rows, bindings,
numerical results, scheduler state and campaign snapshot. Four-GPU 4V100 job
**1888437** has meanwhile completed `0:0` and passed numerical verification;
its historical medians are GPU 806.865 s and CPU 7,787.227 s. Its ratio is
subject to the same CPU-baseline correction. The watcher's existing
`accepted` flags indicate its numerical/schedule checks; it does not detect
this solver-internal rank-use defect. A corrected CPU binary and repeated
performance acceptance are still required. Earlier reports follow unchanged
for traceability.

**2026-10-22: three of five comparisons accepted.** Jobs **1884617**
(16V100, one GPU), **1884985** (4V100, one GPU) and **1889092** (16V100,
four GPUs) finished `COMPLETED 0:0`. Independent
verification reparsed their frozen inputs, numerical results, backend timers,
GPU samples and CPU bindings, including the original imported tuning evidence.
Each result uses three fresh measured pairs from one allocation, excluding
the first warm-up pair and all timings from earlier failed allocations.

| Partition | Waters / basis functions | GPU / physical CPU cores | GPU median | CPU median | CPU / GPU time | GPU memory |
| --- | --- | --- | ---: | ---: | ---: | --- |
| 16V100 | 288 / 6,624 | 1 / 4 | 269.938 s | 1,262.882 s | **4.678×** | 67.59% of 16 GiB |
| 4V100 | 392 / 9,016 | 1 / 4 | 290.154 s | 1,166.439 s | **4.020×** | 54.99% of 32 GiB; host-RAM limited |
| 16V100 | 648 / 14,904 | 4 / 16 | 653.205 s | 6,804.804 s | **10.418×** | 62.21–63.43% of 16 GiB per card |

Times above are CP2K's reported complete-calculation times, with each mode's
median taken independently. GPU uses cuSOLVERMp with
`DIRECT_GENERALIZED_DIAGONALIZATION TRUE`; CPU uses the CPU-only ScaLAPACK
companion. Each comparison reserves the same physical CPU-core quota for both
modes. These are the fastest among the tested layouts, not a global
optimization claim or the isolated benefit of the direct option. System size
grows with available memory; this is not fixed-atoms-per-GPU weak scaling.

For the single-GPU points, GPU uses 1 MPI rank × 4 OpenMP threads; the selected
matrix block is 64 on 16V100 and 32 on 4V100. CPU uses the CPU-only ScaLAPACK
companion, 4 MPI ranks × 1 thread and block 32. Both reserve the same four
physical cores, with CPU measurements using approximately **99.7–99.8%** of
that budget.

The three 16V100 GPU times are 276.410, 269.938 and 266.073 seconds; CPU times
are 1,252.655, 1,265.812 and 1,262.882 seconds. All converge in 20 SCF steps.
On 4V100, GPU times are 290.154, 289.340 and 291.312 seconds (20 steps each),
while CPU times are 1,166.439, 1,285.829 and 1,118.086 seconds (21, 23 and 20
steps). Thus the latter complete-calculation ratio includes a small variation
in iterations required to meet the same convergence threshold. Maximum
energy differences are 9.20e-10 and 1.33e-9 Ha, respectively; maximum force
differences are 1.01e-12 and 2.55e-10 Ha/bohr, well within acceptance limits.
The two partitions use different system sizes and are not a fixed-work
cross-partition performance comparison.

[single-gpu-accepted.json](single-gpu-accepted.json) contains the requests,
version output, scheduler accounting, verification receipts with raw-file
hashes, selected layouts and all eight new measurements for each point.

The newly accepted **16V100 four-GPU** result uses GPU 4 MPI ranks × 4 OpenMP
threads and CPU 16 MPI ranks × 1 thread, both with block 32. GPU measured times
are **662.472, 653.205 and 651.325 seconds**; CPU times are **6,804.804,
6,781.105 and 6,865.305 seconds**. Every warm-up and measured calculation
converged in 20 SCF steps. The CPU measurements use **93.85–95.70%** of the
16 physical cores and have no GPU compute clients. Measured host-memory peaks
are 51.17 GiB for GPU and 52.30 GiB for CPU, within the common 60 GiB quota.
Maximum energy and force differences are 1.19e-9 Ha and 1.01e-12 Ha/bohr.
Including launcher/setup/teardown time gives an 11.225× median ratio; the
table consistently uses CP2K timing. [four-gpu-accepted.json](four-gpu-accepted.json)
contains all eight compact measurements, selected layouts, versions, the
frozen request, accounting and the independent verifier's raw-file hashes.

Job 1889092 completed on `16v100n01` in 08:55:08. It independently imported
all nine validated tuning cases from **1882313**, which had failed at its old
two-hour case ceiling. All warm-up and measured pairs ran afresh under the
six-hour case ceiling. The old first pair (GPU 684.576 s, CPU 5,705.461 s)
and every other old pair timing remain excluded from the accepted result.

**Remaining points, updated 2026-10-22 00:51 China time:** four-GPU 4V100
job **1888437** is running on `4v100n16`, with 900 waters and 16 physical cores.
All nine tuning cases and the excluded warm-up pair passed. The first measured
pair and the second measured GPU calculation passed; the second CPU measurement
has reached SCF step 7. The selected layouts are GPU 4×4 and CPU 8×2 MPI×OMP,
both block 32. The CPU 8×2 trial was the fastest complete calculation despite
using about 71% of the CPU budget; the 16×1 trial used about 95% but took longer
and needed 22 SCF steps versus 20. All layouts reserve and bind the full
16-core quota. Do not interpret the completed first pair as a repeated speedup.
About 9.5 hours remain on the allocation, enough for the approximately four
hours of remaining work if the observed timings hold.

The sixteen-GPU job **1883254** passed all six complete GPU
tuning cases, then its first CPU calculation hit the six-hour case ceiling
at SCF step 13. Its RAM peak was about 187.09 / 240 GiB; the recorded stop
reason is `case_timeout`. Replacement **1887923** independently imports
those six GPU cases, then runs all three CPU tuning layouts and every
warm-up/measured pair afresh, with 18 hours per calculation and a seven-day
allocation ceiling. It keeps the 1,452-water input and 64 physical cores.
The replacement is running on `16v100n27`. Its first CPU layout (16 MPI ×
4 OpenMP) completed in **43,506.550 CP2K seconds**, or 47,616.625 seconds
including launch/setup/teardown, and passed energy/force acceptance in 22 SCF
steps. It used about 44.4% of the 64-core budget. The second layout (32×2) has
reached SCF step 4; the 64×1 layout and all fresh warm-up/measured pairs remain.
The first CPU time is a tuning sample, not the final CPU baseline. Both active
jobs have fresh resource samples and no recorded stop in their completed cases.

Four-GPU 4V100 calibration **1882713** stopped at the 92% memory guard,
81.26 / 88 GiB, with 900 waters. Replacement **1887901** completed `0:0`
and passed independent verification. Complete 864- and 900-water calculations
passed; 960 and 968 waters hit the host-memory guard. The selected 900-water
case used **52.51–56.56% GPU memory per card** and about 80.48 / 88 GiB host
RAM. This remains explicitly host-RAM limited, below the preferred VRAM range;
the earlier 900-water failure is retained as evidence of limited RAM margin.
[four-gpu-followup.json](four-gpu-followup.json) records the accepted complete
calibration, its raw-file verification hashes, the 16V100 four-GPU timeout
and recovery, and the earlier running comparisons.
[multigpu-progress-20261022.json](multigpu-progress-20261022.json) records the
latest completed tuning and timing samples, active-case observations,
scheduler accounting and campaign state. Both unfinished points remain under
the live `control-v5` watcher. Code and evidence through `c1d2442` passed all
four GitHub CI runs; the preceding code commit `9dc5205` passed all six runs.
[large-point-recovery.json](large-point-recovery.json) records their source
failures, requests, allocations and the earlier campaign snapshot. Eleven focused tests
pass, including recovery after partial GPU tuning and rejection of missing
fresh CPU tuning cases, missing pairs and changed selected layouts. The new
verifier also independently accepted both completed single-GPU jobs.

Earlier snapshots below retain the development and recovery history.

**2026-10-21 follow-up: 4V100 single-GPU measurement recovery.** Job
**1882731** passed all nine tuning cases, both warm-up calculations, the first
measured CPU/GPU pair and the second measured GPU calculation. The second
measured CPU calculation then reached **20.28 GiB / 22 GiB**, crossing the
runner's 92% early-stop threshold. Its retained sample records
`stop=host_memory_limit`; scheduler accounting is `FAILED 1:0`, not an
observed OOM. The stopped CPU calculation used approximately **99.8%** of its
four physical cores. Completed energy and force comparisons still passed.

Replacement **1884985** is running on `4v100n02`, with the same 392 waters,
one GPU, four physical cores and 22 GiB RAM quota. The explicit 95% guard is
**20.90 GiB**. All nine original tuning cases were independently validated
and imported, and their inputs match the new frozen input generator. Only
tuning is reused: the excluded warm-up pair and three measured pairs all run
afresh. Earlier measured timings remain excluded from the replacement result.
This point remains host-RAM limited at approximately 55% VRAM, below the
preferred 60–75% window. The watcher retains both failed predecessors and now
tracks attempt 3.

The 16V100 single-GPU recovery has passed both warm-up calculations and is
measuring its first CPU repetition; CPU warm-up used approximately 99.8% of
the four physical cores. The 16-GPU point has passed three complete GPU tuning
calculations at 1,452 waters, each converging in 22 SCF steps, and is testing
the fourth setting. The four-GPU 16V100 point is still testing its second CPU
layout. Four-GPU 4V100 calibration remains queued for resources.

[4v100-measurement-recovery.json](4v100-measurement-recovery.json) retains the
failed sample and raw-file hashes, stopped job log, completed tuning and
excluded timings, replacement provenance and allocation, and campaign state.
**No final repeated speedup is yet accepted.** Earlier snapshots follow.

**2026-10-21 follow-up: single-GPU measurement recovery.** The original
16V100 single-GPU job **1882305** completed tuning, warm-up and two measured
pairs, then stopped during the last CPU repetition. Its sampled host RAM was
**13.83 GiB / 15 GiB**, just above the runner's 92% early-stop threshold.
Scheduler accounting is `FAILED 1:0`, with `stop=host_memory_limit` in the
runner; it is not an observed cgroup OOM. Completed energy/force comparisons
still passed. This incomplete job does **not** produce an accepted speedup.

Replacement **1884617** runs the same 288-water input with one GPU, four
physical CPU cores and the same 15 GiB RAM quota. Its recorded early-stop
fraction is **95% (14.25 GiB)**. Nine original tuning cases were independently
reparsed and their raw evidence hashed; no tuning calculation is repeated.
The new frozen scientific inputs were also checked against all original tuning
inputs. One fresh excluded warm-up pair and all three fresh measured pairs
run on the replacement node. The previous pairs remain in the evidence but
are excluded from the replacement medians. The watcher now uses `control-v4`
and retains the failed predecessor in `previous_comparisons`.

The 16-GPU point has completed two full GPU tuning calculations at 1,452 waters,
both converging in 22 SCF steps with forces accepted. The four-GPU 16V100 point
has completed its first CPU calculation in **6,286.839 seconds** (20 SCF steps),
with numerical agreement; further CPU layouts are running. The 4V100 single-GPU
point has completed all tuning and warm-up and is measuring repeated pairs.
The 900-water 4V100 four-GPU full calibration remains queued for resources.

[measurement-recovery.json](measurement-recovery.json) records the failed
sample, its peak resource observation, raw hashes, excluded old pairs, the
replacement request and allocation, imported tuning provenance and the updated
campaign state. Ten water-scaling tests and five diagonalization tests pass,
including changed-evidence rejection and requiring all eight fresh timing cases
while excluding warm-up from the medians. **No final repeated speedup is yet
accepted.** Earlier snapshots follow for traceability.

**Follow-up at 2026-10-21 00:00 China time:** four formal comparisons are
running. The latest machine-readable snapshot, completed tuning summaries,
numerical comparisons and raw-file hashes are in
[large-system-followup.json](large-system-followup.json).

| Partition | GPUs / physical cores | Waters / basis functions | Current stage and job |
| --- | --- | --- | --- |
| 16V100 | 1 / 4 | 288 / 6,624 | All nine tuning cases passed; warm-up pair running, **1882305** |
| 16V100 | 4 / 16 | 648 / 14,904 | Six GPU settings passed; CPU tuning running, **1882313** |
| 16V100 | 16 / 64 | 1,452 / 33,396 | Memory calibration passed; full comparison running, **1883254** |
| 4V100 | 1 / 4 | 392 / 9,016 | Six GPU settings passed; CPU tuning running, **1882731** |
| 4V100 | 4 / 16 | 900 / 20,700 proposed | Complete memory calibration **1882713** queued for resources |

The 16-GPU calibration **1882207** completed `0:0` and passed independent
verification: the 11×11×12 water cell used **67.03–68.81% VRAM per GPU**.
This remains a three-SCF-step memory probe; full convergence and force acceptance
are still pending. The 4V100 single-GPU point remains explicitly limited by
host RAM at approximately 55% VRAM.

For 288 waters, all three CPU layouts converged in 20 SCF steps and agreed
with the GPU in energy and forces. Sampled utilization of the four allocated
physical cores was **64.1% (1×4 MPI×OMP), 74.2% (2×2), and 99.3% (4×1)**.
All used the CPU-only executable with no GPU compute clients. The selected
layouts now enter one excluded warm-up pair and three measured pairs.
The tuning timings are not the final repeated comparison or a general speedup.

The initial 16-GPU comparison **1883125** was deliberately cancelled after
16 minutes to correct its inherited two-hour case ceiling. It did not fail a
scientific or memory check. Four-GPU CPU measurements already took about
300 seconds per SCF step, making a two-hour ceiling unsuitable for the much
larger 16-GPU point. Replacement **1883254** keeps the same scientific inputs
and resource quota, with six hours per calculation and 72 hours for the whole
comparison; `huge-gpu` permits up to 30 days. The partial old run is retained
and excluded from performance results. The watcher now uses `control-v3` and
will also allow six-hour cases in the pending 4V100 four-GPU comparison.
Existing smaller comparisons keep their frozen settings.

The pending 900-water calibration has just one calculation with a one-hour
case ceiling. Its allocation was shortened from three hours to 90 minutes,
including setup margin, to allow shorter scheduler backfill windows. It is
still queued. Both scheduling changes and the retained old run are recorded
in the latest JSON. Seven focused water-scaling tests pass.
**No final repeated speedup result is available yet.**

**Follow-up at 23:05 China time:** the initial 448-water 4V100 single-GPU
comparison **1882361** hit the host-memory guard during its fifth SCF update.
The full-calculation calibration **1882535** then rejected 432 waters near the
22 GiB host quota and completed **392 waters (9,016 basis functions)** in 20 SCF
steps, including forces, with **54.99% GPU memory** and **18.35 GiB host memory**.
Its replacement comparison **1882731** is running. This point is explicitly
limited by host RAM rather than satisfying the preferred 60–75% VRAM window.

The 4V100 four-GPU short calibration **1882208** reached the host-memory guard at
1,100 and 1,089 waters and ended `OUT_OF_MEMORY`; it is not accepted evidence.
The new complete 900-water calibration **1882713** is queued. It carries the
earlier larger run's measured RAM-limit evidence, so it need not repeat a known
unsafe size. The watcher requires a converged complete calculation before using
a host-limited size and preserves the superseded jobs. The 16V100 288- and
648-water comparisons remain running, and the 16-GPU calibration remains queued
for resources. Raw follow-up evidence is in [host-memory-followup.json](host-memory-followup.json).
**No final speedup is available.** The following table and progress JSON retain
the earlier snapshot for traceability.

Snapshot: **2026-10-20 22:20 China time**. This is progress evidence, not a
completed performance comparison. The machine-readable snapshot is
[progress.json](progress.json); the executable protocol is
[deployment/cp2k-water-scaling](../../../deployment/cp2k-water-scaling/README.md).
Implementation and validation are committed through `26a8eb955acfddbdb9fb40a548997995c7971712`.

The accepted physical CP2K 2026.2 release has source
`67b5da876dd6a76b8b021d5a04d1c81ba79a4c50`, recipe `aed7aa53ff43`, GCC 14.3.0,
and shared system ELPA. GPU calculations request cuSOLVERMp with
`DIRECT_GENERALIZED_DIAGONALIZATION T`; actual generalized-solver timers are
required. CPU companions were compiled inside containers at their final home
paths using the same source/compiler and PBE dependencies, CPU DBCSR, CPU
BLAS/FFTW/GRID and standard ScaLAPACK. Public installations were not changed.

| Partition | GPUs | Physical cores | Selected waters | Basis size | Sampled VRAM per GPU | Current job |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| 4V100 | 1 | 4 | 448 | 10,304 | 62.68% of 32 GiB | comparison **1882361**, running |
| 4V100 | 4 | 16 | pending calibration | — | — | calibration **1882208**, running |
| 16V100 | 1 | 4 | 288 | 6,624 | 67.59% of 16 GiB | comparison **1882305**, running |
| 16V100 | 4 | 16 | 648 | 14,904 | 61.36–62.72% of 16 GiB | comparison **1882313**, running |
| 16V100 | 16 | 64 | pending calibration | — | — | calibration **1882207**, queued for resources |

The three selected calibrations completed with scheduler status `COMPLETED 0:0`
and every card in the user-selected 60–75% window. Calibration is three SCF
steps only; it does not establish scientific convergence or a performance
ratio. The formal complete ENERGY_FORCE runs recheck memory and convergence.
The first complete 288-water GPU tuning calculation also retained a 67.59%
sampled peak. Sizes are calibrated separately because the two partitions have
different GPU capacities. This is memory-scaled growth, not fixed atoms per GPU.

CPU build jobs **1882046** (16V100) and **1882047** (4V100) completed. Their
version output has CPU MPI/OpenMP, ScaLAPACK, LIBXS/LIBXSMM, Libint, LibXC and FFTW
features, with no CUDA/DBCSR acceleration/cuSOLVERMp. Native libraries resolve.
CPU smoke acceptance completed in **1882180** and **1882304**, respectively:
one 64-water GPU reference and CPU layouts 1 MPI × 4 threads, 2 × 2 and 4 × 1.
All used four physical cores, converged, and matched the GPU. Maximum observed
total-energy differences were **4.78e-12 Ha** (16V100) and **5.23e-12 Ha**
(4V100); force-component differences were approximately **1e-12 Ha/bohr**.
The CPU rank PIDs never appeared as GPU compute clients. The 16V100 CPU samples
used approximately 88%, 91% and 98% of their physical-core CPU-time budget.
These small acceptance timings are not the requested large-system speedup.

Both acceptances and the completed calibrations were reparsed from raw files,
checked against frozen script/fixture hashes, and checked against scheduler
accounting. `progress.json` includes the corresponding verification manifests,
raw-evidence hashes and canonical home paths. Complete raw records remain under
the referenced `runtime-tests/` directories; CPU metadata and builds remain in
the referenced `runs/` directories. All scratch, cache and build writes use home
storage, including the container's home-backed `/tmp` mount.

One 343-water 4V100 run (**1882074**, node `4v100n06`) failed in NVRTC compilation
of a DBCSR transpose kernel, at about 40% VRAM use and below the host RAM limit.
The same-size repeat **1882154** and subsequent 448-water calibration **1882166**
completed. This does not establish a root-cause fix; the failure evidence is
preserved and further complete calculations remain subject to acceptance.

The [2026-10-21 node investigation](../cp2k-nvrtc-node-20261021/README.md)
subsequently reproduced that failure and found 1,456 anomalous bytes in reads
of the toolkit `libnvrtc.so.12.9.86` through `4v100n06`. Using an intact
same-version copy in an isolated home environment restores the original failing
case. The toolkit is on shared NFS. On that same client, direct reads bypassing
the page cache return the expected bytes while ordinary reads remain wrong;
four other clients pass both paths. This identifies inconsistent client-cache
reads, not a damaged node-local installation. Exclude this node until its cache
issue is resolved and its reads and GPU runtime pass the documented checks.

A first 16V100 four-GPU calibration exceeded the memory guard at 720 waters.
Its immediately following 648-water attempt observed residual allocations after
termination and is excluded. The runner now checks for idle GPUs before another
case and cleans up only the terminated launch's recorded rank PIDs, with PID
reuse protection. A fresh 648-water job **1882209** completed with all four cards
at 61.36–62.72%. Initial scheduler/launcher integration failures (explicit CPU
requests forbidden by site policy, missing native HOME with `--export=NIL`, and
insufficient MPI slots for CPU layout changes) were corrected; their records
were retained.

The campaign at
`/home/stardust/sai-hpc-software/runtime-tests/cp2k-water-campaign-20261020`
has a running watcher, protected by `flock`, which checks progress every minute.
It submits a comparison only after calibration and CPU numerical acceptance,
and independently verifies completed comparisons. Failed jobs are held for
review. `campaign.json`, `watch.log` and `watch.pid` describe the live state.
Formal runs test GPU thread/block settings and CPU rank/thread layouts, then
perform one excluded warm-up plus three alternating measured CPU/GPU pairs.
No final acceleration ratio or best-layout recommendation is available yet.

Validation: six focused water-scaling tests and five existing diagonalization
tests passed locally. GitHub CI for `26a8eb9` passed, including CP2K runs
[39437671065](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39437671065)
and [39437684830](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39437684830),
plus HPC, MD and GPUMD checks on the same commit.
