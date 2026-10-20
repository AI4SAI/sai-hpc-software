# CP2K water scaling: calibration and CPU acceptance

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
