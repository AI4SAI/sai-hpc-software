# LAMMPS PR 5308 clean rebuild — 2026-11-08/09

Upstream [PR 5308](https://github.com/lammps/lammps/pull/5308) reduced complete
clean compilation/linking from **52m53.71s to 38m49.57s**, saving **14m04.14s
(26.60%)**, or **1.36x** throughput in this pair. The full build/export/SIF-check
flow fell from **58m56s to 44m32s (24.43%)**. Both artifacts retained all **71
packages and identical style inventories**, and all **28 numerical executions
passed** through host MPI.

This supports the upstream host-compiler change as a useful build optimization
for our tested configuration. Unlike the preceding Make/Ninja experiment, it
reduced actual compiler work: descendant-process CPU time fell **19.84%**.
This is one sequential PR-first/base-second pair on a shared cluster with OS
page caches intact. It establishes a measured improvement in this run, not a
cluster-wide repeatability estimate or an application runtime speedup. No
production recipe default, installed software, public module, or accepted SIF
was replaced.

| Measurement | Parent/base | PR 5308 |
| --- | ---: | ---: |
| Configure | 29.20 s | 32.05 s |
| Compile and link | 3173.71 s (52m54s) | 2329.57 s (38m50s) |
| Install inside container | 38.89 s | 36.02 s |
| Full build/export/SIF-check flow | 3536 s (58m56s) | 2672 s (44m32s) |
| Build process tree CPU time | 32639.56 s | 26164.03 s |
| Average build process tree logical CPUs | 10.28 | 11.23 |
| Sampled build process tree peak PSS | 23.43 GiB | 22.91 GiB |

The approximately 0.52 GiB peak-memory difference is small. PSS is sampled
about every two seconds across CMake's descendants; it excludes filesystem
cache and external container daemons and can miss short peaks. It is not total
allocation or cgroup memory. Slurm batch MaxRSS, recorded separately, was
34919808 KiB for the complete paired job and must not be assigned to either
individual variant.

## Source, toolchain, and comparison controls

- PR head: `f1a6e5cf5167deeb1f70bc8bd38ff8c2e1a853a7`.
- Its sole parent/base: `d291d572cf3c73a8a720774b78482a7c931304ca`.
- Upstream diff: 19 additions to `cmake/CMakeLists.txt` only; SHA256
  `26ca4b7c9b61077653c51bf8b9e4879cafbfa8459449be282807c9f3d7361b1f`.
- Frozen experimental controller: `ef2bc61c542bc393b4e0ce20a6749de7ffff6998`.
  Complete snapshot hashes accompany the evidence.
- Same `16v100n32` allocation, **16 physical cores / 32 logical CPUs**, identical
  CPU affinity, **24 workers**, and four V100 GPUs for normal build-time checks.
- Both use Unix Makefiles, GCC **14.3.0**, CUDA **12.9.1**, CMake **3.31.6**,
  Release `-O3`, native CPU tuning, MPI/OpenMP, GPU and Kokkos CUDA, SM70, cuFFT,
  CUDA MPS, installed DeePMD **3.2.0**, PLUMED **2.10.1**, Eigen, and Voro++.
- Each used a separate initially empty overlay, with no object or compiler
  cache. LAMMPS and its bundled build targets were fully rebuilt; installed
  dependencies were bound read-only.
- The normal DeePMD CMake registration is the only tracked-source adaptation.
  The wrapper verifies the actual source against upstream plus that adaptation
  before configuration. No additional repair of PR 5308 was required.
- All **2651 expanded compiler commands** match after accounting for only
  **1716 new `--host-only` flags** and authentic per-commit install prefixes.
  No other compiler flags, source selection, or feature settings changed.
  Kokkos and other CUDA-dependent files retain their CUDA compilation path.

The cluster could not fetch GitHub during preparation. The fixed commit and
changed Git objects were fetched locally, transferred with original object
hash verification, and repacked with the cached parent into a self-contained
isolated repository. No alternates or compute-node network access were needed.
Previous caches, overlays, and accepted artifacts were preserved.

## Execution and GPU regression

Build job **2306184** completed `0:0`, elapsed **1h43m31s**, TotalCPU
**18h06m46s**, on `16v100n32`:

- PR: `2026-11-08T22:49:56+08:00` to `2026-11-08T23:34:28+08:00`.
- Base: `2026-11-08T23:34:28+08:00` to `2026-11-09T00:33:24+08:00`.

GPU regression job **2307487** completed `0:0` in **2m56s** on `16v100n12`,
with two V100 GPUs. Host OpenMPI **5.0.10** launched each LAMMPS rank into its
candidate SIF. Both candidates passed:

- Version, dynamic-library resolution, 71-package and full style inventory.
- One/two-rank DeePMD TF/PT/JAX energy, force, and virial comparisons, including
  the PLUMED observable: `atol=1e-7`, `rtol=1e-5`.
- One/two-rank classic GPU and Kokkos LJ/Coulomb/PPPM cases, with explicit
  GPU/cuFFT execution assertions: `atol=2e-6`, `rtol=2e-5`.

There are **28 numerical case executions**, 14 per SIF, plus four
version/inventory executions. All use the previously accepted frozen reference
with SHA256 `e5f75411c7891cb4df63bb71410daa6f21e5c91b6ee7bb8f987b41e4e0880478`.
DeePMD checks establish interface and numerical compatibility, not backend
performance or that every operation executes on a GPU. This one-node
regression does not establish the production two-node publication contract or
acceptance on other partitions.

Preparation validation ran 43 targeted unit tests (40 passed, 3 skipped),
compiler-comparison mutation checks, old/new scientific request compatibility,
production recipe hook checks, Python syntax, and shell syntax. Four GitHub
checks passed for the frozen controller commit. One polling monitor was
restarted after encountering an initially empty base log; this did not restart
or alter the build. Its first broad error matcher also captured C++ `***`
pointer declarations in warning context; raw logs and phase exit codes confirm
there were no actual compiler errors.

## Artifacts and evidence

Cluster root:
`/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-lammps`.

- Experiment/request/accounting: `generator-benchmarks/pr5308-20261108-v1`.
- Frozen controller: `generator-benchmarks/pr5308-20261108-v1-snapshot/controller`.
- Science outputs: `runtime-tests/pr5308-20261108-v1-science`.

Candidate images remain under the cluster root's `runs/` directory:

| Run / candidate.sif | Bytes | SHA256 |
| --- | ---: | --- |
| `pr5308-20261108-v1-pr5308` | 285216768 | `5dea7facfb5906669316ed5ea9d19e3a637f3a65b844beadd954d4e756f7b10b` |
| `pr5308-20261108-v1-base` | 285421568 | `77bf68a56947a6b4acc9281f053212da7f86e2b436d59b55da674124821fd7fa` |

[summary.json](summary.json) and [comparison.json](comparison.json) contain
unrounded values. [evidence.raw.tar.gz](evidence.raw.tar.gz) contains **295
files**: upstream diff and metadata, snapshot hashes and harness, requests,
build logs, compiler commands/response files, time/memory samples, filesystem
checks, Slurm accounting, scientific outputs, and rank/loader traces. The
archive's `manifest.json` records original paths, sizes, and SHA256 hashes;
every included file was independently verified after download. SIFs and model
binaries remain on the cluster.

Archive SHA256:
`62ea350da5d06d691a30cd921778bb14fbc216b5b9459fd953b785e4ddb5d91f`.
