# CP2K 2026.2 direct generalized diagonalization pilot

Job `1865445` completed `0:0` in 19m 27s on `16v100n01`. It uses the previously
accepted v2026.2 SIF with private ELPA (recipe `4d454c740309`), not the new
repaired-shared-ELPA candidate currently compiling. The image hash and prior
science proof are pinned in `raw/request.json`.

The workload is the existing synthetic periodic 64-water PBE system (1472 basis
functions), DZVP-MOLOPT-SR-GTH, 300 Ry, `EPS_SCF=1e-9`, one MPI rank, one V100,
four physical CPU cores/OpenMP threads. Four modes each run one warmup followed
by three alternating forward/reverse repetitions. Each starts from atomic
guess with identical basis/potential bytes. Times below are CP2K internal total
time; launcher/integrity overhead is recorded separately.

| Mode | Three measured times (s) | Median (s) |
| --- | --- | --- |
| cuSOLVER, direct FALSE | 41.011, 42.410, 41.391 | 41.391 |
| cuSOLVER, direct TRUE | 40.862, 40.915, 41.051 | 40.915 |
| ELPA, direct FALSE | 33.594, 33.557, 33.512 | 33.557 |
| OT FULL_ALL, cuSOLVER | 160.177, 155.145, 155.899 | 155.899 |

The standard/direct ratio is **1.0116**: about 1.2% in this small sample, with
overlapping observed time ranges. This does not justify changing the upstream
default. ELPA is faster for this particular diagonalization control. The OT
case has its own preconditioner behavior and 112 cuSOLVER calls, versus 24
diagonalizations in each SCF diagonalization case; this is not a general ranking
of OT versus diagonalization or an optimal OT configuration.

All 16 calculations passed convergence, energy/force consistency, actual solver
dispatch and rank/physical-core checks. Direct runs call
`eigensolver_generalized` and `cp_fm_general_cusolver` 24 times each. Standard
runs call `cp_fm_diag_cusolver` 24 times and never call the generalized path.
ELPA and OT have their own positive dispatch timers. Maximum energy/force
differences across all modes/repeats are `4.7066e-11 Ha` and `1.5430e-9 Ha/Bohr`.
GPU utilization/memory sampling is retained, along with SCF steps, full timers,
CPU affinity and loader/runtime identity. Samples describe the assigned GPU,
not an exact per-process peak.

The **328 raw files** in `raw/evidence-sha256.json` were hashed on SAI and checked
after download. `validation.json` independently re-parses raw results and Slurm
accounting, checks all input and basis hashes and recomputes medians. Reproduce:

```bash
python3 deployment/cp2k-diagonalization/run.py verify \
  docs/evidence/cp2k-direct-pilot-20261020/raw
```

The new workflow uses recipe `38dfba7e3ed7`. Its frozen watcher will submit
216-water measurements on the three V100 partitions only after each new SIF
passes science. This pilot proves the experiment works and supplies an initial
measurement; it does not accept the new build or establish large-system scaling.
