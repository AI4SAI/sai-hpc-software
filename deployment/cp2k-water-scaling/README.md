# CP2K 2026.2 large-water GPU/CPU comparison

This experiment uses the accepted physical shared-ELPA release at
`/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/<partition>`.
It does not modify the public installation or its defaults.

| Partition | GPU counts | Physical CPU cores | GPU memory per card |
| --- | --- | --- | --- |
| 4V100 | 1, 4 | 4, 16 | 32 GiB |
| 16V100 | 1, 4, 16 | 4, 16, 64 | 16 GiB |

The user selected **60–75% measured VRAM per card**, with system size increasing
with resources. This is memory-scaled growth, not conventional weak scaling
with constant atoms per rank: dense-matrix storage is quadratic in basis size.
Different GPU memory capacities mean that the two partitions need independent
calibration. Report water count, basis size, peak memory and resource allocation
beside every timing; do not compare different-size cases as fixed-work speedups.

The fixture extends the existing synthetic periodic water lattice (3.1 angstrom
spacing), with PBE, DZVP-MOLOPT-SR-GTH, 300 Ry cutoff, 40 Ry relative cutoff,
`EPS_DEFAULT=1e-12`, and `EPS_SCF=1e-9`. Each water contributes 23 basis functions.
This is a controlled scaling workload, not an equilibrated liquid-water sample
or a universal production performance claim. Both paths read identical original
CP2K basis/potential bytes, checked against the existing fixture hashes.

GPU uses `PREFERRED_DIAG_LIBRARY CUSOLVER` (cuSOLVERMp) and
`DIRECT_GENERALIZED_DIAGONALIZATION T`, with one MPI rank per distinct GPU.
The timing log must prove calls to `eigensolver_generalized` and
`cp_fm_general_cusolver`. CPU uses standard ScaLAPACK diagonalization with the
direct option disabled, CPU GRID, and CPU BLAS. Changing only the eigensolver
on the CUDA executable is not a pure CPU baseline because its FFT and other
components still execute GPU code.

`submit-cpu.py` therefore builds a companion from the same pinned source,
GCC 14.3.0, native CPU optimization, site MPI/BLAS/FFTW/LibXC and accepted
Libint/LIBXSMM dependencies. CPU DBCSR and LIBXS are built explicitly.
CUDA/cuSOLVERMp, ELPA, COSMA and SpLA are disabled. Optional methods unused by
this PBE workload (e.g. TBLITE, PLUMED and HDF5) are not enabled in the companion;
it is a benchmark executable, not a replacement for the complete public release.

Compilation runs in a container, mounting the original sealed dependency overlay
read-only. All new work, installs, caches, MPI scratch and results live under
`/home/stardust/sai-hpc-software`. The CPU install has exactly the same absolute
home path inside and outside the container. Container `/tmp` and `/var/tmp` are
binds of home storage. Native benchmarks run without a container. Existing
installations, artifacts and build overlays are not rewritten.

Example commands from the cluster home checkout:

```bash
python3 -B deployment/cp2k-water-scaling/submit-cpu.py \
  --partition 16V100 --gpus 4 --name UNIQUE_CPU_BUILD
python3 -B deployment/cp2k-water-scaling/run.py submit \
  --partition 16V100 --gpus 1 --name UNIQUE_CALIBRATION
python3 -B deployment/cp2k-water-scaling/run.py submit --stage smoke \
  --partition 16V100 --gpus 1 --name UNIQUE_SMOKE \
  --cpu-binary /home/stardust/sai-hpc-software/runs/cp2k-water-cpu-UNIQUE_CPU_BUILD/install/bin/cp2k.psmp
python3 -B deployment/cp2k-water-scaling/run.py submit --stage compare \
  --partition 16V100 --gpus 1 --name UNIQUE_COMPARISON --shape 6 6 8 \
  --cpu-binary /home/stardust/sai-hpc-software/runs/cp2k-water-cpu-UNIQUE_CPU_BUILD/install/bin/cp2k.psmp \
  --calibration-run /home/stardust/sai-hpc-software/runtime-tests/cp2k-water-UNIQUE_CALIBRATION \
  --cpu-smoke-run /home/stardust/sai-hpc-software/runtime-tests/cp2k-water-UNIQUE_SMOKE \
  --minutes 240 --case-seconds 7200
```

Use a unique run name each time. Submitters freeze the scripts and fixtures.
Do not edit frozen runs to change the experiment. The site assigns eight logical
CPUs (four physical cores) and a fixed RAM quota per GPU; it rejects explicit
`--cpus-per-task` for these GPU jobs. Single-GPU 16V100 jobs use
`flood-1o2gpu` (at most four hours), single-GPU 4V100 jobs use `rush-1o2gpu`,
four-GPU jobs use `rush-gpu`, and 16-GPU jobs use `huge-gpu`. The latter avoids
competing with other four-GPU experiments for the 16-GPU per-user limit of the
rush queue. Retain the normal user environment for native Open MPI startup.
Comparison jobs request enough MPI slots for the largest CPU layout (4G),
while the site still allocates the same 4G physical cores from the GPU count.
The campaign allows six hours per complete calculation at the four- and
16-GPU points, because the equally resourced CPU ScaLAPACK path can take hours
on these matrix sizes. Four-GPU jobs retain the rush queue's one-day limit;
16-GPU comparisons request three days in the huge queue (site maximum 30 days).
These are ceilings, not required durations: a finished comparison exits early.
Single-GPU comparisons keep their two-hour case limit and the queue limits above.

Calibration performs only three SCF steps, allows nonconvergence explicitly,
and must never be reported as scientific or performance acceptance. It samples
each allocated GPU by UUID and the actual cgroup RAM limit. A 92% host-memory
guard and an 86% GPU-memory guard prevent knowingly continuing past the memory
budget. If host RAM, uneven GPU distribution, discrete cell sizes or the probe
limit prevent the requested window, the report records that limitation rather
than pretending the target was achieved. Complete ENERGY_FORCE runs must
recheck the memory peak.

Use `--full-calibration` when later SCF iterations or forces consume more host
RAM than the short probe. It converges ENERGY_FORCE at each trial size and records
the limiting resource. On 4V100, 22 GiB of host RAM per GPU can become the limit
before a 32 GiB card reaches the preferred VRAM range. A campaign point explicitly
marked `allow_host_limited` may use a fully converged selection at 50–75% VRAM
only after an actual larger calculation hits the host-memory guard. This preserves
the user's preference for using most available memory within the identical
GPU-associated CPU/RAM quota, and the result is labelled as limited by host RAM.
Ordinary calibration cannot enable this exception. Failed comparisons remain
listed in `previous_comparisons`, with a new `attempt` name for the replacement.
`--host-limit-run RUN` can also attach a previous larger calculation's measured
RAM-limit evidence to a new complete calibration. Its partition, GPU count and
executable must match, and the failed sample's measured memory must prove that
the host guard was reached. This avoids repeating a known unsafe large size just
to rediscover the same RAM limit; it still requires a converged new selection.

The CPU smoke test verifies the source/version, absent CUDA features, native
libraries, full convergence and agreement with the GPU at 64 waters. CPU MPI ×
OpenMP layouts are G×4, 2G×2 and 4G×1, all using exactly 4G physical cores.
Formal tuning also tests GPU OpenMP counts 4/2/1 and SCF matrix block sizes
32/64. It selects the fastest of these measured choices, not a claimed global
optimum. Each rank records its actual CPU topology and affinity; overlapping
physical cores or duplicate GPU assignments fail validation. CPU runs hide GPUs
and the sampler checks that their rank PIDs never appear as GPU compute clients.

After tuning, one warm-up pair is excluded, followed by three alternating
CPU/GPU pairs at the selected layouts. All cases start from the same atomic
guess in fresh directories; unused wavefunction restart files are disabled.
Absolute total-energy tolerance is 1e-6 Ha and
maximum force-component tolerance is 1e-5 Ha/bohr, unchanged from prior
acceptance. Only fully converged, numerically agreeing runs can produce a speed
ratio. Reports include CP2K internal total time, MPI-launch wall time, SCF step
counts, backend timers, sampled CPU seconds and memory peaks. Reserving all
cores does not imply 100% utilization; measured utilization is retained.

If a comparison stops after all nine tuning cases have passed, a replacement
can use `--tuning-run ORIGINAL_RUN`. Submission, execution and final verification
reparse the original inputs, energies, forces, timers, memory samples and rank
bindings, verify all frozen inputs, and pin hashes of the raw tuning evidence.
The source must be a terminal job with identical binaries, partition, size and
CPU/GPU quota. Its later failure is retained; it is never labelled a successful
comparison. Only tuning is reused. One new warm-up pair and all three new measured
pairs run in the replacement allocation, so timings from different nodes or jobs
are not mixed into the final medians.

The default host-memory early-stop fraction remains 92% of the actual cgroup
limit. `--host-memory-fraction` records a per-run override between 85% and 97%;
it does not change the Slurm RAM quota or any scientific acceptance tolerance.
For the 288-water 16V100 single-GPU recovery, use `--host-memory-fraction .95`:
the last CPU repetition reached 13.83 GiB of its 15 GiB quota and tripped the
92% guard, while all earlier completed calculations passed. The original run
has scheduler status `FAILED`, not `OUT_OF_MEMORY`; its incomplete last case
and two completed measured pairs are excluded from the replacement's result.

Before publishing results, retain scheduler accounting (`COMPLETED`, `0:0`),
original inputs, logs, force files, rank traces, samples and build provenance.
Queued jobs and memory-only probes are progress, not completed benchmark results.
`run.py verify RUN` independently reparses these raw files, checks scheduler
completion and numerical agreement, and writes a hashed `results/verified.json`.

`campaign.py CAMPAIGN.json --watch` can advance a recorded set of runs every
60 seconds. It submits one CPU smoke test per partition after its build finishes,
and submits each comparison only after successful CPU acceptance and a completed
calibration with every GPU in the requested window. It preserves failed jobs for
review rather than silently retrying them. Run one watcher under `flock` and keep
its log, PID and campaign JSON in the task's home directory. Restarting adopts
only an existing recorded submission with matching identity. The current
campaign is `/home/stardust/sai-hpc-software/runtime-tests/cp2k-water-campaign-20261020`.
