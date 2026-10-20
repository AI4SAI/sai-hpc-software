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
cache to prove it. That commit kept `CP2K_USE_SPLA_GEMM_OFFLOADING=OFF` to
preserve baseline behavior. The
16-test contract suite passes, including CPU/GPU rejection of an absent or
disabled SpLA option. Real compilation/linkage of this correction remains
to be verified in a fresh recipe.

The running controller and its dependency images retain their original
identity. They do not acquire this correction by updating the development
checkout. This is a build-configuration parity correction, not evidence of a
lost baseline GPU execution path: in
[local_gemm_api.F](https://github.com/cp2k/cp2k/blob/67b5da876dd6a76b8b021d5a04d1c81ba79a4c50/src/local_gemm_api.F#L118),
the actual SpLA calls require both `__SPLA` and `__OFFLOAD_GEMM`, and the
installed baseline disables the latter too. Continue the existing builds and
their scientific acceptance; record the configuration difference explicitly.
Real build validation of the corrected configuration belongs to a subsequent
fresh recipe, without relabelling existing dependency overlays. GPU SpLA
offload is an additional capability, not baseline restoration.

## GPU GEMM offload enabled for the next recipe

Following the user's October 17 request, GPU recipes now explicitly set
`CP2K_USE_SPLA_GEMM_OFFLOADING=ON`. DSPRHBM explicitly keeps it OFF. Both retain
`CP2K_USE_SPLA=ON`. The artifact contract rejects a missing/wrong CMake setting
and requires `spla_gemm_offloading` in the GPU executable's version flags;
CPU artifacts must not advertise it. The 17 focused contract tests pass.
Existing frozen jobs still use their recorded recipe.

This enables selected local double-precision matrix multiplies, computing
`C = alpha * op(A) * op(B) + beta * C`. Source callers include RI-MP2
(`src/mp2_ri_gpw.F`), RPA exchange (`src/rpa_exchange.F`) and RPA gradients
(`src/rpa_grad.F`). MP2 and RPA exchange set a `2*128*128*128` operation threshold
(4,194,304 FLOPs); SpLA can leave smaller host-buffer multiplies on the CPU.
Retain these upstream thresholds. The runtime keyword
`GLOBAL / PREFERRED_DGEMM_LIBRARY BLAS` selects the CPU BLAS path for comparison
or a workload where offload is slower; the compiled-in default becomes SPLA.
DBCSR sparse-block, COSMA distributed GEMM, GRID and PW GPU paths are separate.
The ordinary OT water64 acceptance case does not establish this path's speedup.

The pinned SpLA 1.6.1 package already selects `+cuda`; it has **no** `cuda_arch`
variant. Its CMake uses CUDAToolkit's cudart/cuBLAS and its GPU DGEMM wrapper
calls `cublasDgemm`, without compiling custom `.cu` kernels. Do not add an
unsupported `cuda_arch=70` requirement. The toolkit selects the V100 kernels
at runtime; CP2K/DBCSR/tiled-mm retain their explicit SM70 configuration.

The [isolated dependency probe](evidence/cp2k-spla-offload-20261017/spla-probe.cpp)
uses host matrices, CPU and GPU SpLA contexts, the CP2K threshold, one warmup
and three alternating repeats at 1/4 CPU threads. It counts successful actual
cuBLAS DGEMM calls, checks full CPU/GPU results and independent scalar entries,
and includes host/device transfers in the measured call time. A 64-square
matrix must stay on CPU; 256/1024/2048-square cases must call cuBLAS on SM70.
Job `1831559` completed on `4v100n05` in 47 seconds using Tesla V100-SXM2-32GB.
The [raw results and source excerpts](evidence/cp2k-spla-offload-20261017/results.json)
show all numerical checks passed, with zero observed CPU/GPU elementwise
difference for the deterministic binary-fraction inputs. The 64 case made no
cuBLAS calls; every measured larger case made three successful calls.

| Matrix (square, TN) | CPU 4 threads, median | GPU incl. transfers, median | CPU / GPU |
|---|---:|---:|---:|
| 256 | 0.074 ms | 0.247 ms | 0.30x |
| 1024 | 4.009 ms | 3.039 ms | 1.32x |
| 2048 | 32.146 ms | 12.979 ms | 2.48x |

Against one CPU thread, 1024 and 2048 measured 5.09x and 9.53x respectively.
These dependency microbenchmarks support enabling the path for large systems,
as the user confirmed. They do not establish CP2K whole-calculation speedup,
multi-GPU scaling, or correctness for arbitrary inputs. Keep upstream thresholds
and the BLAS override; validate the new application artifact separately.

The first probe exposed a private-driver read-only mount error. Probe B used
stock for the read-only mount, but a subsequent full image checksum showed
that the sealed dependency image changed. Its original seal remains preserved
and must fail validation; do not reseal or reuse it as unchanged. Both stock
and the pinned upstream driver can internally open `-o ro` images writable.
The numerical results stand, but the mount integrity claim is withdrawn.
The [read-only driver repair](evidence/fuse2fs-readonly-20261017/README.md)
subsequently passed all twelve qualification stages in job `1831878`, including
real `O_RDONLY` opens, zero image writes, unchanged full-image hashes and
reader/writer lock exclusion. The original modified checkpoint stays invalid.
The earlier forty fresh-image CMake repeats did not expose retained work files;
a separate [work-directory reproducer](evidence/cp2k-overlay-workdir-20261017/README.md)
now proves that numeric directory collisions cause `EISDIR` during ordinary
file removal. These fixes passed isolated validation and are included in fresh release
workflow [39041714911](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39041714911).
Actual application build and science/performance acceptance remain pending.

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

## Shared ELPA rebuild and issue #9

The October 19 recipe uses the accepted shared ELPA 2026.02.001 libraries;
see [reproduction and pinned partition mapping](ELPA.md). Existing accepted
CP2K installations use private ELPA 2026.02.002. New candidates need their own
science results; standalone ELPA acceptance is not CP2K acceptance. GCC 14.3,
SM70, cuSOLVERMp/NCCL and GPU SpLA GEMM offload remain enabled.

[Issue #9](https://github.com/AI4SAI/sai-hpc-software/issues/9) concerns
`GLOBAL / DIRECT_GENERALIZED_DIAGONALIZATION`, an input option defaulting to
`FALSE`, not a missing compile flag. In v2026.2 ordinary SCF this branch uses
cuSOLVER, not ELPA. It requires a matrix of at least 64, compatible Cholesky and
level-shift settings, and actual dispatch to the supported backend. Setting
the keyword to `TRUE` alone is not proof of execution.

`PREFERRED_DIAG_LIBRARY CUSOLVER` is the input spelling for **cuSOLVERMp** in
this release. There is no separate `CUSOLVER_MP` selector to compare. The pinned
[`cp_fm_cusolver.c`](https://github.com/cp2k/cp2k/blob/67b5da876dd6a76b8b021d5a04d1c81ba79a4c50/src/fm/cp_fm_cusolver.c)
calls `cusolverMpSyevd` for the standard route and `cusolverMpSygvd` for direct
generalized diagonalization, including when run with one rank and one GPU.
Testing that backend on one GPU does not establish its multi-GPU scaling.

After the new candidate passes science, use the same sufficiently large PBE
system, basis, cutoff, SCF tolerance and resources for these independent runs:

| Case | `PREFERRED_DIAG_LIBRARY` | Direct generalized | Purpose |
|---|---|---|---|
| PBE diagonalization | `CUSOLVER` | `FALSE` | Standard reduction control |
| Same diagonalization | `CUSOLVER` | `TRUE` | Measure the actual generalized branch |
| Same diagonalization | `ELPA` | `FALSE` | Shared ELPA control |
| PBE OT | Explicitly recorded backend | `FALSE` | Separate common workload; OT has its own Cholesky |

Use one visible GPU per cuSOLVER MPI rank, preserve affinity and GPU memory
records, perform a warmup followed by alternating repeated runs, and retain
SCF convergence, energies, forces, actual solver calls and CP2K internal
timing. The existing small ELPA cases establish correctness, not large-system
performance. Keep the upstream direct default until SAI measurements justify
a recommendation. No speedup is inferred from replacing the dependency.

Source: [SCF dispatch](https://github.com/cp2k/cp2k/blob/67b5da876dd6a76b8b021d5a04d1c81ba79a4c50/src/qs_scf_diagonalization.F#L236),
[cuSOLVER generalized branch](https://github.com/cp2k/cp2k/blob/67b5da876dd6a76b8b021d5a04d1c81ba79a4c50/src/fm/cp_fm_diag.F#L1503),
and [upstream PR #5108](https://github.com/cp2k/cp2k/pull/5108).

The October 20 [experiment runner](../deployment/cp2k-diagonalization/README.md)
implements this comparison for 64/216 periodic waters, one MPI rank per V100,
the allocated physical CPU cores, one warmup and three alternating repeats.
It checks actual generalized/standard/ELPA/OT dispatch, energy/force agreement,
SCF convergence and affinity before computing timing summaries. GPU memory and
utilization are sampled separately. A watcher can submit the larger experiment
only after the new recipe's science acceptance is recorded. The old accepted
2026.2 SIF is used for a separate 64-water script pilot; that pilot is not
acceptance of the repaired-shared-ELPA CP2K build.

Pilot job `1865445` completed all 16 calculations. Its 64-water medians are
41.391 s (cuSOLVER FALSE), 40.915 s (TRUE), 33.557 s (ELPA), and 155.899 s (OT
FULL_ALL with cuSOLVER). The direct ratio is only 1.0116, with overlapping sample
ranges; retain the upstream default pending the larger new-build measurement.
All numerical/dispatch/affinity checks pass. [Raw pilot results and scope](evidence/cp2k-direct-pilot-20261020/README.md).

That result is restricted to **16V100, one V100, 64 waters and 1472 basis
functions**. It does not establish a size-independent benefit or lack of benefit.
ELPA's faster time in that sample likewise does not establish the best backend
on a different problem or GPU count. Both backends and the direct input option
remain available. Selecting them at runtime does not require another build.

The next automatic experiment uses 216 waters (4968 basis functions) on each
of the three GPU partitions after new-artifact science acceptance. It still
uses one GPU. Subsequent scaling work should compare ELPA, cuSOLVERMp standard
and cuSOLVERMp direct on the **same problem** at 1/2/4 GPUs, then a larger
representative PBE system or basis. Record both total SCF time and solver/
Cholesky/redistribution timers, SCF iterations, memory and GPU placement.
Different systems can shift which stage dominates; a solver speedup can be
hidden in total time, or outweighed by communication. Recommendations should
state the measured problem size and rank/GPU count, not impose one backend on
all workloads. Multi-GPU experiments are planned, not yet implemented or run
by the single-GPU automatic runner.
