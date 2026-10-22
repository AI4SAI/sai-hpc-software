# Minimal CP2K ScaLAPACK MPI repair: controlled execution evidence

The one-file, two-hunk repair restores the intended MPI participation in
CP2K 2026.2. Real `pdsyevd_` calls confirm that the original library used only
half the MPI ranks for these matrices; the repaired library uses every rank.
The behavior remains correct through 22 or 24 successive diagonalizations.
The repair is restricted to the experimental CPU companion. Public CPU/GPU
installations and the direct cuSOLVERMp GPU benchmark path are unchanged.

The implementation and test protocol are in
[README-mpi-repair.md](../../../deployment/cp2k-water-scaling/README-mpi-repair.md),
commit `c2183b3e3cb6905f4984022a18e6228aa3c62615`.
The source defect is upstream [CP2K #6074](https://github.com/cp2k/cp2k/issues/6074).

## Causal build controls

| Partition | Container build job | Original and repaired CPU target | Result |
| --- | --- | --- | --- |
| 4V100 | 1897877 | AMD Ryzen 9 9950X3D, GCC 14.3.0 | COMPLETED 0:0, 57 s |
| 16V100 | 1897876 | AMD Threadripper PRO 5995WX, GCC 14.3.0 | COMPLETED 0:0, 45 s |

Only `src/fm/cp_fm_diag_utils.F` changes: move structure initialization before
configuration assignment, and preserve configuration when clearing temporary
work pointers. Both source hashes and the exact diff are retained under
`builds/<partition>/results/source-fix.json` and `source.patch`.
The original SHA-256 is `e3edf28d199b4ba624bd94b0e3e73a8863f6db2a7ffc8e4d8e0017f32f7e8c83`;
the repaired SHA-256 is `d2f091078e805fed2ea8b1be8e387d2a48abeb98408c6e62b280a637f74c6a90`.

Each build reuses all other **1,389 objects** and the same library dependencies.
The original control relink is byte-identical both before and after installing
its RPATH. The repaired Fortran public `.mod` file is also byte-identical.
The original and repaired objects' compiler records differ only in the output
directory for `.mod` files; their resolved native architecture flags match.
`cpu-target-verification.json` retains both records and the matching CPU flags.
No compiler upgrade, solver substitution or runtime MPI workaround is part of
the A/B comparison. All build/cache/temporary storage is physically in the
stardust home directory, including the container's bound `/tmp` and `/var/tmp`.

## MPI and numerical results

| Allocation | MPI layouts tested | Original solving ranks | Repaired solving ranks | Repeated solves per calculation |
| --- | --- | --- | --- | --- |
| 4V100, 16 physical cores | 4 / 8 / 16 | 2 / 4 / 8 | 4 / 8 / 16 | 24 |
| 16V100, 16 physical cores | 4 / 8 / 16 | 2 / 4 / 8 | 4 / 8 / 16 | 24 |
| 16V100, 64 physical cores | 16 / 32 / 64 | 8 / 16 / 32 | 16 / 32 / 64 | 22 |

The diagnostic records actual world ranks, BLACS grid coordinates, matrix
orders, workspace queries and solve return codes. Every solve must have the
complete expected grid without duplicate coordinates. Core bindings cover the
entire physical CPU quota. CPU processes have no GPU compute clients.
These cases are large enough that the intended rank heuristic selects all
ranks; legitimate redistribution for smaller matrices is not disabled.

All accepted calculations converge `ENERGY_FORCE`. Energy/force tolerances
remain 1e-6 Ha and 1e-5 Ha/bohr. The independent archive check compares all
same-size calculations, including repetitions and MPI layouts, not just each
original/repaired pair. All **42 calculations** passed the cluster and local
raw-evidence checks. The largest same-size energy/force differences are
**1.82e-12 Ha** and **1.00e-12 Ha/bohr** (rounded), respectively. Exact maxima and per-sample times are recorded in
[local-verification.json](local-verification.json).

## Timing results

The timed input is **216 waters / 4,968 basis functions**, periodic PBE with
DZVP-MOLOPT-SR-GTH. Each A/B pair uses the same node, physical cores, input,
compiler and dependencies. Timing uses one MPI rank per physical core and one
thread per rank. The diagnostic interposer is disabled. One warm-up pair is
excluded; the table uses separate medians of three alternating measured pairs.
Every timed calculation converges in 22 SCF steps.

| Partition / physical cores | Job | Original total | Repaired total | Time reduction | Original / repaired ScaLAPACK time |
| --- | --- | ---: | ---: | ---: | ---: |
| 4V100 / 16 | 1897953 | 97.773 s | 92.111 s | 5.79% | 35.696 / 26.441 s |
| 16V100 / 64 | 1897997 | 77.689 s | 65.073 s | 16.24% | 39.441 / 26.248 s |
| 16V100 / 16 | 1897952 | 281.769 s | 173.851 s | 38.30% | 177.412 / 67.631 s |

All three jobs completed `0:0` and passed independent verification on the
cluster and locally. The 16-core 16V100 calculation spends 109.781 s less in ScaLAPACK,
accounting for its 107.918 s reduction in total time. The same source repair
therefore explains both the observed MPI participation and the measured
eigensolver improvement, without changing the other numerical kernels.

The measured effect is specific to these cases and CPU allocations. Restoring
all solving MPI ranks does not imply a twofold complete-calculation speedup.
Other work and communication remain, and waiting MPI ranks can report high
CPU use even in the original defective path. Actual participation and solver
timings, rather than sampled CPU percentage alone, identify this repair.

## Larger-water campaign and reproducibility

The previous campaign is paused with its original metadata and results kept
under `campaigns/cp2k-water-campaign-20261020`. Its historical CPU/GPU ratios
are superseded as comparisons against a fully participating CPU solver.
The replacement campaign is
`/home/stardust/sai-hpc-software/runtime-tests/cp2k-water-campaign-mpi-fixed-20261022`.
It requires successful causal MPI retests, then fresh CPU/GPU smoke tests,
before submitting the five original memory-calibrated water sizes. It imports
no old CPU tuning or timing pairs. The accepted 95% host-RAM early-stop setting
is recorded; CPU/GPU quotas and scientific tolerances are unchanged.

The frozen initial replacement configuration and submission snapshot are
retained under `campaigns/`. Both CPU/GPU smoke jobs (`1898135`, `1898234`)
passed their four calculations and independent cluster verification. All five
larger comparisons have now been submitted with the repaired library hash,
fresh tuning and fresh warm-up/measurement pairs:

| Partition | GPUs / physical CPU cores | Waters | Job |
| --- | --- | ---: | --- |
| 4V100 | 1 / 4 | 392 | 1898166 |
| 4V100 | 4 / 16 | 900 | 1898169 |
| 16V100 | 1 / 4 | 288 | 1898482 |
| 16V100 | 4 / 16 | 648 | 1898484 |
| 16V100 | 16 / 64 | 1,452 | 1898487 |

At the initial submission check, four jobs were running and 4V100/four GPUs
was waiting for resources. These are ongoing GPU/CPU scaling measurements,
not final results from the smaller causal test. The detached watcher records
progress and verifies successful runs; failed jobs remain visible for review.

The compressed files in [raw/](raw/) contain complete frozen test controls,
inputs, logs, forces, rank/ScaLAPACK traces, resource samples and verification
manifests. They exclude runtime caches and executables. The build evidence
records artifact hashes, reused object hashes, dependencies and actual load
paths. Recheck the archived execution evidence without cluster access:

```bash
python3 docs/evidence/cp2k-scalapack-mpi-repair-20261022/verify_archives.py \
  --work .test-work/mpi-repair-archives \
  --output .test-work/mpi-repair-reverified.json
```

[ci.json](ci.json) records successful CP2K and HPC workflow validation:
**874 tests, two skipped**, plus shell syntax checks. The 18 targeted local
tests also passed. The broad local run was stopped after CI completed and is
not counted as a successful full-suite run.
