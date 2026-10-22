# CPU ScaLAPACK redistribution repair and causal retest

CP2K v2026.2 resets its configured redistribution settings immediately after
initialization and again during temporary-work cleanup. With the default
settings overwritten, its negative target rank count triggers a fallback MPI
split. Half of the ranks diagonalize; the other half wait. This is upstream
[issue #6074](https://github.com/cp2k/cp2k/issues/6074).
The [source investigation](../../docs/evidence/cp2k-water-scaling-20261020/scalapack-source-check/README.md)
documents the pinned source and a compiled control-flow reproducer.

`fix-redistribution.py` makes exactly two functional changes in
`src/fm/cp_fm_diag_utils.F`: initialize the structure **before** storing its
configuration, and clear only the four temporary pointers during cleanup.
The CPU build applies this repair; GPU production builds and public `/opt`
installations are outside this experiment. Both the original file and repaired
file are pinned by SHA-256; another source revision is rejected.

## Isolated build

```bash
python3 deployment/cp2k-water-scaling/submit-relink.py \
  --partition 16V100 --name 20261022-mpi-fix-16v100-a
python3 deployment/cp2k-water-scaling/submit-relink.py \
  --partition 4V100 --name 20261022-mpi-fix-4v100-a
```

The container mounts the original CPU build and sealed dependency overlay
read-only. All physical writes, including container `/tmp` and `/var/tmp`, go
to the new run under `/home/stardust/sai-hpc-software/runs`. GCC 14.3.0 and
the original compile/link options are retained. A new object for the single
modified source file is compiled, and all other 1,389 CP2K objects are reused.
The public Fortran `.mod` file must remain byte-identical.

The script first relinks a control using every original object. Both its build
library and installed library must be byte-identical to the original. It then
relinks the repaired library with exactly one substituted object. Hashes of
all reused objects, directly linked libraries, source patches and command
arguments are retained. The launcher is identical in both prefixes. The CPU
benchmark now records `libcp2k.so.2026.2` hashes as well as launcher hashes,
preventing old CPU tuning or acceptance from being reused for the repaired
library. `before/` is the control and `install/` is the repaired CPU companion;
both reuse the same original read-only dependencies and CP2K data.

These are experimental CPU companions, not public deployment packages. Keep
their original build/dependency tree while running the experiment.

## Real MPI and timing tests

```bash
python3 deployment/cp2k-water-scaling/retest-mpi.py submit \
  --partition 16V100 --gpus 4 --name 20261022-16v100-g4-a \
  --build /home/stardust/sai-hpc-software/runs/cp2k-water-cpu-20261022-mpi-fix-16v100-a
python3 deployment/cp2k-water-scaling/retest-mpi.py verify RUN
```

Each allocation tests original and repaired CPU libraries on the same node,
using the same physical cores, inputs, basis/potential files and thread
settings. All runs converge `ENERGY_FORCE`; no restart state is imported.
The 64-water diagnostic (216 waters for a 64-core allocation) runs the three
MPI/OpenMP layouts G×4, 2G×2 and 4G×1. The input is large enough that the
intended redistribution heuristic should use every MPI rank.

For these diagnostic calculations only, `trace-scalapack.c` intercepts
`pdsyevd_` with `LD_PRELOAD`, forwards its unchanged arguments and records the
world rank/size, actual BLACS grid, matrix order, workspace queries, return
codes and repeated solve calls. Verification requires complete nonduplicated
grids for every solve and at least three successive solves, so a cleanup
regression cannot pass by fixing only the first diagonalization. The expected
original participation is P/2 (one rank for P=1); repaired participation is P.
This is evidence from the real CP2K/ScaLAPACK execution, not a source simulator.

The independent timing phase disables the interposer. It uses 216 waters by
default and 4G MPI ranks × one thread, excluding one warm-up pair and taking
separate medians of three alternating pairs. This phase measures the causal
effect of the repair on a moderate-size input. It does **not** replace the
original five-point experiment with larger, memory-calibrated water systems.
Energies must agree within 1e-6 Ha and force components within 1e-5 Ha/bohr.
Rank bindings, absence of CPU-process GPU clients, solver timers, CPU samples,
raw logs and forces are verified independently after `COMPLETED 0:0`.

No speedup threshold is assumed: MPI participation can be repaired even when
communication or other stages limit a particular case. High sampled CPU usage
alone is insufficient, because waiting MPI ranks can busy-spin. Report actual
solving ranks and complete-calculation/eigensolver times together.

After causal acceptance, rerun the original five CPU/GPU points using the
repaired companion, original calibrated sizes and new CPU tuning. Keep the
same resource quotas and direct cuSOLVERMp GPU path. Historical timing pairs
cannot be included in the new medians.
