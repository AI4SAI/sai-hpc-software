# CP2K release rebuild with the repaired shared ELPA

The administrator installed the simplified public ELPA module. Its hash and all
three physical-library inventories match the replacement package. Four native
public-module jobs passed with 456 numerical solves, correct CPU/GPU selection,
zero remaining tracked GPU communicators, actual loader paths and unload checks.

| Partition | Public module | GCC 14.3 / CMake / Fortran probe |
| --- | --- | --- |
| 16V100 | 1865327 | 1865334 |
| 4V100 | 1865328 | 1865335 |
| 8V100V0 | 1865329 | 1865336 |
| DSPRHBM | 1865330 | 1865337 |

All eight jobs completed with `0:0`. `public-validation.json` and
`gcc14-validation.json` record the checks. The 191 archived input/result files
in `evidence-sha256.json` were hashed on SAI and verified after download.
The public checks use physical `/opt` and the real public module; GCC 14 probes
use the production container isolation and match the repaired lock files.

The CP2K lock now uses the three communicator-repair manifests, including hashes
of `share/sai/mpi-communicator.patch` and `mpi-communicator-fix.json`.
Commit `c1f63be` starts a fresh recipe, without changing the earlier artifacts.
Release source remains `67b5da876dd6a76b8b021d5a04d1c81ba79a4c50` (v2026.2).

[Workflow 39293908513](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39293908513)
passed validation and source resolution and submitted all four dependency jobs:
16V100 `1865393`, 4V100 `1865374`, 8V100V0 `1865375`, DSPRHBM `1865391`.
All four were observed running. `build-start.json` and `build-start/` retain the
observed status and frozen requests. Application compilation and new CP2K
scientific acceptance are still pending. The workflow handles their handoff.

GCC 14.3, V100/SM70, cuSOLVERMp/NCCL, SpLA GEMM offload, full Libint capability,
fixed final prefixes and home-backed build/cache/temp storage are retained.
The [independent direct-generalized experiment](../../../deployment/cp2k-diagonalization/README.md)
does not change the build default or its scientific acceptance protocol.

The frozen tuning watcher is active on SAI; `tuning-watch.json` records its
workflow, recipe, source checkout and three GPU targets. It waits for new
`cp2k_science` acceptance before submitting the 216-water experiment.
CI at `d36b666` passed 831 tests with two optional skips (833 total).

The [separate 64-water pilot](../cp2k-direct-pilot-20261020/README.md) completed
all 16 cases on the earlier accepted 2026.2 SIF. Direct TRUE/FALSE differ by
about 1.2% in median total time with overlapping samples; no default is changed.
