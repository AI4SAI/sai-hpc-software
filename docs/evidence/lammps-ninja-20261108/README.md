# LAMMPS clean-build generator comparison — 2026-11-08

This pair did **not** demonstrate a Ninja speedup. Ninja took 57m39.64s for
compilation and linking versus Make's 54m33.65s, **5.68% longer**. Keep the
current daily Make generator for now. This is one Ninja-first/Make-second pair
on a shared cluster, with OS caches left intact; the measured difference is
not a stable estimate that Ninja is inherently slower.

The follow-up [source audit](source-audit.md) identifies redundant NVCC
dependency scans, host-source compilation through NVCC, and template
instantiation candidates. Their speedups have not yet been measured.
The [upstream issue/PR review](upstream-followup.md) identifies maintainer
PR #5308 as the preferred next experiment and distinguishes its reported
cache-assisted gains from our clean-build requirements.

| Measurement | Unix Makefiles | Ninja 1.13.2 |
| --- | ---: | ---: |
| Configure | 29.95 s | 30.36 s |
| Compile and link | 3273.65 s (54m34s) | 3459.64 s (57m40s) |
| Install | 54.90 s | 45.10 s |
| Full build/export/SIF-check flow | 3606 s (60m06s) | 3751 s (62m31s) |
| Build process tree CPU time | 34349.18 s | 33123.60 s |
| Average build process tree logical CPUs | 10.49 | 9.57 |
| Sampled build process tree peak PSS | 21.78 GiB | 22.07 GiB |

The full flow was 4.02% longer with Ninja. PSS is sampled every two seconds and
apportions shared pages across CMake's descendants, including the build tool and
compilers. It excludes external container daemons and filesystem
cache, and can miss brief peaks. It is not total allocation memory. Measurements
include the same process sampler in both variants.

## Controls and execution

- Slurm build job **2302625**, `COMPLETED 0:0`, on **16v100n16**; elapsed 2h02m38s,
  TotalCPU 20h56m13s. Both variants ran sequentially within this allocation.
- Identical affinity: **16 physical cores / 32 logical CPUs**, **24 build jobs**.
  The allocation also held four V100 GPUs for the normal build-time checks.
- Same LAMMPS source: `d291d572cf3c73a8a720774b78482a7c931304ca`, develop.
- GCC 14.3.0, CUDA 12.9.1, CMake 3.31.6; `-march=native -mtune=native`, Release,
  shared libraries, MPI/OpenMP, GPU and Kokkos CUDA, SM70, cuFFT, CUDA MPS.
- Both reused the same installed DeePMD 3.2.0, PLUMED, Eigen and Voro++.
  All LAMMPS objects and its bundled build targets were freshly compiled.
- Separate initially empty file-backed overlays; identical canonical install
  prefix inside them; no compiler/object cache. All host storage and billing
  used `/org/org-open-sai/proj-sai-hpc-software`.
- After expanding the actual NVCC response file, **all 2651 compiler commands
  matched**, including **2200 LAMMPS target commands**. Only object/dependency
  output bookkeeping is excluded from comparison. Both candidates have the
  same **71 packages** and identical style inventories.

Ninja ran 17:15:21–18:17:52 CST. Make ran 18:17:52–19:17:58 CST. The build
controller is frozen under
`experimental/deepmd-lammps/generator-benchmarks/ninja-make-20261108-v1-snapshot/controller`;
its complete file hashes, requests, scripts and accounting are in the raw
archive. The measured wrapper is from commit
`94e60abd28e0a0f7d1b6a14322616620eb3a6639`.

Make used `CMakeFiles/gpu_fatbin.dir/includes_CUDA.rsp` for two include paths;
Ninja wrote those same paths inline. The response file was retrieved from the
completed overlay with read-only `debugfs`, then expanded before comparison.
The reusable wrapper now captures response files automatically after the timed
phases. Neither the frozen build snapshot nor its overlays were modified.

## Where the time went

Ninja's log contains 2813 distinct timed build edges. It kept all 24 task slots
occupied for **3249.8 / 3459.612 seconds (93.94%)**, averaging **23.01 active
tasks**. Active tasks include waits; this is not CPU utilization.

The 407 LAMMPS Kokkos translation units span **29m46.84s** of the build timeline.
The slowest single file, `pair_table_rx_kokkos.cpp`, took **343.34s**. Final
`liblammps.so` linking took **34.98s**. Changing the generator does not eliminate
these template compilations.

A 20-second read-only sample during the Make C++ phase observed `fuse2fs` at
**87.12% of one logical CPU**, `fuse-overlayfs` at 10.9%, and `faked` at 3.1%.
This is a lead for a separate filesystem experiment, not proof of the cause
of the Make/Ninja timing difference. The measurement sampler itself used 3.1%
of one logical CPU during that sample.

## Runtime regression checks

Slurm job **2304039**, **16v100n06**, completed `0:0` in **3m06s**. Host
OpenMPI 5.0.10 launched each rank into its candidate SIF. Both candidates passed:

- Version, dynamic-library resolution, 71-package and full style inventory.
- One- and two-rank DeePMD TF/PT/JAX energy, force and virial comparisons,
  including the PLUMED observable: `atol=1e-7`, `rtol=1e-5`.
- One- and two-rank classic GPU and Kokkos LJ/Coulomb/PPPM cases, with explicit
  accelerator/cuFFT log assertions: `atol=2e-6`, `rtol=2e-5`.

There are **28 numerical case executions**, 14 per SIF, plus version/inventory
checks. All use the frozen release reference with SHA256
`e5f75411c7891cb4df63bb71410daa6f21e5c91b6ee7bb8f987b41e4e0880478`.
The reference's 24 input/helper files were verified before submission. DeePMD
backend checks establish numerical compatibility, not backend performance or
that every backend operation runs on a GPU. This one-node regression is not
the production two-node publication contract, and it measures no application
runtime speedup. These experimental SIFs were not published or installed.

## Artifacts and evidence

Each SIF is 285421568 bytes, retained under
`/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-lammps/runs/`:

| Run / candidate.sif | SHA256 |
| --- | --- |
| `ninja-make-20261108-v1-make` | `ba93aa01030a4678108db9126551b5e3106fef64d37a5d7fa4d27e4edcdfec87` |
| `ninja-make-20261108-v1-ninja` | `4ed0072033d86e7d66e0ba02037ff3a31886ed8b80da15efb05e5699f471c373` |

[summary.json](summary.json) and [comparison.json](comparison.json) contain the
unrounded values. [evidence.raw.tar.gz](evidence.raw.tar.gz) contains 275 raw
files, including compiler commands/response files, time/memory traces, build
logs, filesystem checks, Slurm accounting, pinned requests, scientific outputs
and rank/loader traces. Its internal `manifest.json` records every included
file's original path and hash. Model binaries and SIFs remain on the cluster.

Archive SHA256: `0768a5fe78a1f71fed8878b1c0f634b8b216b2ec3cee66421e65548ad8b1d998`.
