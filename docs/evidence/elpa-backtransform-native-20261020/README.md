# Installed ELPA back-transform update: physical acceptance

The administrator installed both GPU variants from
[`elpa-2026.02.001-backtransform`](../../../deployment/elpa-2026.02.001-backtransform/README.md).
The complete physical inventories match the candidate bytes previously used in
all 42 CP2K integration calculations. The CPU variant and 36-line public module
retain their accepted inventories.

| Partition | Native job | Result | Numerical solves |
| --- | --- | --- | --- |
| 16V100 | 1869223 | COMPLETED 0:0 | 131 |
| 4V100 | 1869224 | COMPLETED 0:0 | 131 |
| 8V100V0 | 1869225 | COMPLETED 0:0 | 131 |
| DSPRHBM | 1869226 | COMPLETED 0:0 | 67 |

All **460** solves passed using physical `/opt` libraries and the real public
module, without Apptainer. Each GPU MPI rank created/freed 32/32 temporary
communicators with zero remaining. The CPU paths create none. Checks cover
real/complex ELPA2, nonblocking selection, repeated solves, C/Fortran consumers,
actual loader paths, module selection/unload and the CP2K GCC 14.3 type-bound
Fortran numerical probe. Maximum spectral/residual/orthogonality errors across
these checks are below `1.7e-15`. CPU ELPA remains independent of CUDA.

Every compute node verified all three physical inventories before and after
execution. The independent verifier reparses numerical logs, MPI instrumentation,
Slurm accounting and loader traces; a marker alone does not establish acceptance.
[`native-checks.py`](../../../deployment/elpa-2026.02.001-backtransform/native-checks.py)
freezes all controls and reproduces the submission/verification.

The first submission (`1869172`–`1869175`) failed before ELPA execution because
the new wrapper used `--export=NIL`; site Open MPI could not resolve the user
home directory. Restoring the previously verified `--export=ALL` convention
fixed the harness. Both attempts retain separate frozen scripts and logs under
the project home. No failed receipt is rewritten.

The CP2K lock now uses these new GPU manifests and pins
`share/sai/gpu-backtransform-host-staging.patch`, retaining the communicator
repair pins and unchanged CPU variant. This generates a new CP2K recipe identity
and final install prefix. Prior failed GPU SIFs are not relabelled accepted;
the new build still needs its own scientific and native-export acceptance.

`raw/` preserves 206 files (205 remotely hashed inputs/results and their index),
including both the failed submission and successful physical acceptance. All
file hashes were checked after download; the local independent reparse passed
all 460 solves. See [archive receipt](archive.json),
[remote verification](raw/native/validation.json) and
[local verification](local-validation.json).

## Fresh CP2K release build

[Workflow 39365139034](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39365139034)
uses commit `13e1a7e` and recipe `aed7aa53ff43`. Its validation passed 840 tests
(838 passed, two optional skips). Source resolution still selects v2026.2 at
`67b5da876dd6a76b8b021d5a04d1c81ba79a4c50`.

| Partition | Dependency job | Observed state |
| --- | --- | --- |
| 16V100 | 1869397 | RUNNING |
| 4V100 | 1869396 | RUNNING |
| 8V100V0 | 1869360 | RUNNING |
| DSPRHBM | 1869407 | RUNNING |

The workflow owns the dependency/application/science/export handoffs. The
container and physical install prefix is
`/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/<partition>`.
GCC 14.3, V100 SM70, cuSOLVERMp/NCCL and SpLA GEMM offload are retained.
The [submission snapshot](build-start/snapshot.json) and its hashed raw records
establish the observed jobs and identities, not a completed build.

The frozen [tuning watcher](tuning-watch.json) is bound to this workflow and
recipe. It waits for each new GPU artifact's verified scientific receipt before
submitting the 216-water ELPA/cuSOLVERMp standard/direct/PBE-OT experiment.
No new 216-water measurement has completed; multi-GPU scaling remains separate.
