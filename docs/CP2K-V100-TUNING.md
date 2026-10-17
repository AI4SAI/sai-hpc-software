# CP2K V100 source and installed-feature review — 2026-10-17

The release recipe already selects V100/SM70 and GPU implementations of the
main sparse-matrix, grid and FFT paths. The useful next runtime experiments
are MPI/OpenMP placement, dense eigensolver selection and workload-specific
DBCSR tuning. Source inspection establishes available paths; it does not
establish a performance improvement on SAI.

The review pins CP2K v2026.2 at
`67b5da876dd6a76b8b021d5a04d1c81ba79a4c50` and the recipe's DBCSR archive at
SHA-256 `d963cac9cb79e04d82bedf0f4febce9cd06470f84c7d5a45f74645d11f8c4875`.
[Source excerpts, installation settings and hashes](evidence/cp2k-v100-review-20261017/source-installations.json)
were collected read-only at 21:00 Asia/Shanghai. The
[collector](evidence/cp2k-v100-review-20261017/collect-audit.py) does not run
installed applications or inspect users' calculation directories.

## What older installations tell us

| Installation | Observed configuration | Implication for this workflow |
|---|---|---|
| 2024.3, revision `6712648` | Separate CPU and CUDA V100 builds; Libint, LibXC, FFTW, ELPA, COSMA, LIBXSMM, PLUMED, SPGLIB, VORI, QUIP | Preserve broad scientific capabilities and CPU delivery alongside GPU delivery. |
| 2025.1, revision `9635df4` | CPU/CUDA modules, AVX2 and AVX-512 trees, similarly broad feature flags | Partition-specific CPU instruction compatibility matters even for GPU jobs. The directory named `nccl` alone does not prove NCCL usage. |
| 2026.1, revision `5e54ba2` | GCC 14.3, CUDA 12.9.1, V100; additionally HDF5, TBLITE/DFTD4 and SpLA; cuSOLVERMp OFF; SpLA GEMM offload OFF | Retain the newer scientific interfaces and distinguish added GPU backends from the baseline configuration. |

These configurations suggest support for GPW/hybrid calculations and integrals
(Libint), exchange-correlation functionals (LibXC), enhanced sampling (PLUMED),
xTB/GFN calculations (TBLITE), D4 dispersion, symmetry handling (SPGLIB),
trajectory/data compression (VORI/BQB), parallel HDF5 and dense/sparse linear
algebra. They do not establish how frequently users need each capability.
DeepMD, LibTorch, SIRIUS and PEXSI were disabled in the inspected 2026.1 caches;
commented module paths are not evidence that they were compiled in.

The 2026.1 module loads standalone ELPA 2026 but also prepends an old bundled
ELPA 2024 library path. Its actual loaded library must be established by
runtime evidence. The new workflow already pins and verifies the standalone
2026 library, and rejects the older path. The auto modules test AVX512_VNNI;
8V100V0's Gold 6146 has AVX-512 without VNNI, so its site dependencies correctly
use AVX2 while the application itself is compiled for the observed native CPU.

QUIP's upstream removal is the existing user-approved exception. Libgrpp is
now built unconditionally upstream. The historical 16V100 development SIF's
version and loader checks passed on October 16, but it has neither scientific
acceptance nor publication and is not a multi-partition delivery; see
[its recorded status](evidence/cp2k-release-priority-20261016.json).

## Configuration omission found and fixed

The frozen controller `1e7d736` builds private SpLA 1.6.1 but does not pass
`CP2K_USE_SPLA=ON`. Upstream defaults this option to OFF when
`CP2K_USE_EVERYTHING=OFF`, which is the recipe's setting. Old 2026.1 caches
explicitly enable SpLA. Plain SpLA has no standalone `--version` feature flag:
`src/cp2k_info.F:300` prints `spla_gemm_offloading` only when that additional
offload path is enabled. Therefore comparing version flags missed this gap.

Commit `6d9b709` explicitly enables SpLA and requires the application CMake
cache to prove it. `CP2K_USE_SPLA_GEMM_OFFLOADING=OFF` preserves the baseline
behavior while its GPU path remains a separate measured experiment. The
16-test contract suite passes, including CPU/GPU rejection of an absent or
disabled SpLA option. Real compilation/linkage of this correction remains
to be verified in a fresh recipe.

The running controller and its dependency images retain their original
identity. They do not acquire this correction by updating the development
checkout. Their results remain useful build evidence, but cannot establish
complete installed-feature parity with 2026.1. Before declaring closure,
build and accept a new identity containing this correction; do not relabel
the existing dependency overlay or silently waive SpLA.

## GPU paths already selected

- CP2K and DBCSR use CUDA, `WITH_GPU=V100` and architecture 70. DBCSR maps V100
  to SM70 and generates its kernel header from the V100 parameter table.
  That pinned table already contains **74,092 entries**; it is not an untuned
  generic GPU build. Its SHA-256 is in the source audit.
- CP2K enables GRID, DBM, PW and ELPA GPU support by default under CUDA.
  [GRID AUTO](https://github.com/cp2k/cp2k/blob/67b5da876dd6a76b8b021d5a04d1c81ba79a4c50/src/grid/grid_task_list.c#L50)
  selects the GPU implementation unless explicitly disabled. DBCSR's
  `RUN_ON_GPU` default is true. COSMA and tiled-mm dependencies also select CUDA.
- [ELPA AUTO](https://github.com/cp2k/cp2k/blob/67b5da876dd6a76b8b021d5a04d1c81ba79a4c50/src/fm/cp_fm_elpa.F#L223)
  prefers the NVIDIA GPU kernel when compiled with CUDA/ELPA offload.
  Selecting an ELPA kernel only matters when the calculation actually calls ELPA.
- [The default diagonalizer](https://github.com/cp2k/cp2k/blob/67b5da876dd6a76b8b021d5a04d1c81ba79a4c50/src/fm/cp_fm_diag.F#L90)
  becomes cuSOLVER when cuSOLVERMp is compiled in; otherwise ELPA is preferred
  when present. This is a real default difference from the installed 2026.1.
  Matrices below size 64 use a ScaLAPACK fallback in the cuSOLVER path.
  Merely running a tiny input does not prove GPU eigensolver execution.

## Prioritized experiments after obtaining the SIF

| Priority | Experiment | Evidence needed to choose a setting |
|---|---|---|
| 1 | At fixed GPU/CPU allocation, compare one MPI rank per GPU with OpenMP 1/2/available physical cores; then more ranks per GPU if CPU work dominates | Actual CPU affinity, GPU assignment, CP2K timing breakdown and GPU kernel activity. On the current one-GPU acceptance allocation, 16V100/4V100 have four physical cores and 8V100V0 three. Do not equate Slurm logical CPUs with OpenMP threads. |
| 2 | On a sufficiently large diagonalization workload, explicitly compare `PREFERRED_DIAG_LIBRARY ELPA` and `CUSOLVER` | Same matrix/problem, numerical acceptance, actual backend call evidence and GPU-memory use. The current OT-based water64 case cannot establish a general eigensolver speedup. |
| 3 | Profile DBCSR block shapes, then test stack/buffer/offload thresholds or tune only dominant missing/slow shapes | The existing V100 parameter table is the starting point. Relevant CP2K input controls include `GLOBAL/DBCSR/ACC` `THREAD_BUFFERS`, `MIN_FLOP_PROCESS`, `STACK_SORT`, and `AVOID_AFTER_BUSY`, plus `MM_STACK_SIZE`; change one family at a time. |
| 4 | Compare SpLA local GEMM offload and FFTW planning only for workloads spending time in those paths | SpLA affects some local DGEMMs, not every distributed PDGEMM. FFTW wisdom/planning helps CPU FFTW calls; CUDA PW FFTs use a different implementation. Retain a wisdom file per CPU/library configuration under home. |

The existing acceptance runner fixes one MPI rank and one OpenMP thread per
GPU node for reproducible baseline/candidate comparisons. Keep that comparison
intact; perform tuning in separate allocations with fixed resources, warmup
and alternating repeats. Preserve energies/forces, basis sets and convergence
settings. Record CP2K compute time separately from SIF launcher/integrity
overhead, and use the verified native export for native performance claims.
Multi-GPU scaling needs its own evidence; current two-node acceptance uses one
GPU per node. Read-only diagnosis uses direct SSH, never an extra attached
`srun` step on an existing GPU job.

## Build efficiency and shared container tooling

Libint still compiles many generated integral kernels. Keep the pinned
`cp2k-lmax-5` capability; reducing it solely to shorten compilation changes
the supported basis/integral range. Current dependency stages already build
once per target/recipe and hand a sealed image to the application stage.
Reusable caches across future identities would need explicit source/compiler/
ISA/ABI validation and relocatability checks before relaxing that boundary.

The private fuse2fs fix addresses build I/O and does not accelerate CP2K's
GPU kernels. A shared, opt-in tool under a versioned
`/opt/devtools/fuse2fs/<source-commit>-sai-ofd/` would benefit other Lustre-backed
writable-image builds and reduce per-user maintenance. It is not necessary
to continue these builds or to run the final read-only SIF. See
[the deployment recommendation](BUILD-IO-EFFICIENCY.md#shared-opt-recommendation).

Release 2026.2 remains the first acceptance target. The source review and
configuration correction do not establish current-development acceptance;
release and development retain independent source, recipe and artifact records.
