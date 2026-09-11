# CP2K validation status

This is an implementation checklist, not a release acceptance result. The
`feat/cp2k-parity-benchmarks` branch is not yet a four-target accepted release.

## Verified existing work (2026-09-11)

CP2K was compiled on SAI outside Actions. Job `1216695` completed installation
then failed its final command because `libcp2k.so.2026.2` was not on the library
path. Repack job `1226525` completed and produced
`runs/cp2k-repack-1216695/cp2k-latest-v100.sif` (SHA256
`f2c9b32f9314c96f4c840d3c5027227235019c749eafce6fd541bd47adf7badf`).
Its GPU-allocation log identifies CP2K 2026.2 revision `6d276e9` and flags
`omp parallel scalapack dbcsr_acc openblas offload_cuda cusolvermp cusolvermp_nccl`.
The final SIF was independently inspected: its install path is incorrectly
`/opt/software/latest-v100`, not `/opt/software/cp2k/latest-v100`, and it lacks
saved runtime metadata. Manually reconstructing its diagnostic environment gives
no missing `ldd` libraries, but that is not scientific acceptance or publication.

TBLITE/DFT-D4 dependency job `1227313` succeeded. Its archive and the project
cache copy have SHA256
`a2f54e22b9397841cb414696abbcd001d94b3599eb6aeabe222d0dd4e8e69a52`.
No accepted CP2K catalog/module existed at the time of inspection.

## Native artifact contract

Each of DSPRHBM, 4V100, 16V100 and 8V100V0 must build independently on that
partition with `-march=native`; CPU dependency selection is taken from modules
resolved on its compute node. In particular, 8V100V0 is Skylake AVX-512 without
VNNI and uses the site's AVX2 MPI/BLAS builds. The artifact prefix is always
`/opt/software/cp2k/<version>/<target>` and survives export unchanged. Source,
compilation and installations remain inside the file-backed overlay/SIF.

The final-SIF verifier checks saved provenance, native CMake flags, all required
feature switches, runtime version flags, correct ELPA 2026/cuSOLVERMp/NCCL
resolution, installed scientific data, and the absence of transient RPATHs.
Runtime environment and data cannot depend on `/workspace`, `/input` or the
controller snapshot. The module launcher uses installed `CP2K_DATA_DIR`; the
same absolute `/opt` layout can later be used on physical storage with the same
read-only system dependencies. No host installation tree is copied into a SIF.

Candidate builds are not published or cache-reused before a verified scientific
benchmark tied to their exact SIF, launcher, input bytes and verifier succeeds.

## Source and baseline

- Candidate source: CP2K commit `6d276e9c480f2b55f5a0b9166e22d20d9a3f8da0`.
- Baseline module: `cp2k/2026.1-cuda12.9-sm70-auto`.
- CPU baseline module: `cp2k/2025.1-cpu-auto` (version 2025.1, revision
  `git:9635df4`, Open MPI 5.0.8); 2026.1's installed binary requires a GPU.
- Baseline installation: `/opt/apps/cp2k/cp2k-2026.1-avx512`.
- Baseline version checks must run in a GPU allocation.
- Recheck upstream branch HEAD before selecting the final release candidate.

## Required feature parity

Baseline version output includes the following features. The candidate must
verify these from its own build configuration and runtime version output.
Existing dependency directories alone do not prove ABI compatibility.

| Baseline feature | Candidate configuration or verification |
| --- | --- |
| omp | Check OpenMP compiler detection and runtime flags |
| parallel, scalapack | `CP2K_USE_MPI=ON`; verify ScaLAPACK linkage |
| mpi_f08 | `CP2K_USE_MPI_F08=ON` |
| dbcsr_acc, offload_cuda | CUDA-enabled DBCSR; `CP2K_USE_ACCEL=CUDA` |
| libint | `CP2K_USE_LIBINT2=ON` |
| fftw3 | `CP2K_USE_FFTW3=ON` |
| libxc | `CP2K_USE_LIBXC=ON` |
| elpa | `CP2K_USE_ELPA=ON`; verify compatible GPU build |
| cosma | `CP2K_USE_COSMA=ON`; inspect CUDA dependency build |
| xsmm | Check latest LIBXS/LIBXSMM integration and required versions |
| plumed2 | `CP2K_USE_PLUMED=ON` |
| spglib | `CP2K_USE_SPGLIB=ON` |
| libdftd4, mctc-lib, tblite | `CP2K_USE_DFTD4=ON`, `CP2K_USE_TBLITE=ON` |
| libvori, libbqb | `CP2K_USE_VORI=ON`; verify both runtime flags |
| hdf5 | `CP2K_USE_HDF5=ON` |

Also requested for the GPU candidate: `CP2K_USE_CUSOLVER_MP=ON`.
Do not silently disable unavailable features to pass a build.

CP2K 2026.2 requires both LIBXS 1.0.0 and LIBXSMM for the old XSMM capability;
the recipe builds LIBXS, DBCSR and the seven-library TBLITE dependency chain
natively inside each target overlay and adapts the
site's pkg-config-only Libint2/LIBXSMM to CMake without copying their libraries.
ELPA discovery is pinned to the loaded 2026.02.001 module, never to the old
2024 toolchain copy. The successful TBLITE archive establishes working locked
source versions, but its own precompiled AVX-512-linked executables are not
blindly reused on AVX2 targets. Each target compiles TBLITE 0.7.0 and DFT-D4 4.2.0
from the same checksum-locked source archives in the existing project cache.

The older CPU baseline advertises `libgrpp` and `quip`. In 2026.2 libgrpp is
compiled unconditionally and no longer appears in version flags; the recipe
records source evidence for this equivalence. QUIP's interface was removed
upstream before 2026.1. The user explicitly approved omitting QUIP from CP2K
2026 on 2026-09-11. It is the sole approved parity exception, recorded in
`upstream-feature-changes.json` and benchmark reports; no other missing
baseline feature is implicitly waived, and no legacy QUIP companion is planned.

## Probe evidence

- Job `1214324`: standalone CUDA/MPI DBCSR build succeeded.
- Job `1215004`: DBCSR build and install succeeded; CP2K configuration failed
  because CuSolverMP include/library paths were not discovered.
- Job `1215480`: submitted with `nvmplibs/26.7-tmp` and explicit CP2K
  CuSolverMP/NCCL roots; timed out while compiling at about 75%.
- The current probe is not feature complete. CP2K optional dependencies default
  to OFF, so CUDA/MPI configuration alone does not establish feature parity.
- CUDA Toolkit is provided by SAI at `/opt/devtools/nvidia/cuda-12.9.1`.

## Remaining acceptance

- Build and run the full-feature candidate on the requested CPU/GPU targets.
- Persist and verify the container artifact and runtime launcher.
- Integrate CP2K into the controller/workflow without changing ABACUS behavior.
- Run controller tests and an ABACUS regression check.
- Compare candidate and baseline on the same fixed input, node/GPU allocation,
  MPI ranks, thread count, and scientific settings; validate results before
  reporting timings. Record input identity, versions, energies and wall times.
  TBLITE is the only approved input-syntax exception: CP2K 2026.1 selects the
  backend with `&TBLITE T`, while the pinned development source requires
  `GFN_TYPE TBLITE`. Both exact input hashes and a reversible two-line syntax
  contract are checked; GFN2, geometry, SCF settings and tolerances stay fixed.
  This correctness comparison is not described as same-byte and reports no
  cross-version TBLITE speed ratio.
- Use one warmup and at least three alternating repeats in fresh directories;
  validate energies and forces before reporting a speed ratio. CPU baseline
  2025.1 does not support TBLITE/DFT-D4: those candidate-only paths require
  independent checked scientific references, not self-consistency alone.
- A fixed periodic 64-water (192-atom) GPW workload is the required compute-time
  benchmark, separate from the tiny correctness probes. It must spend at least
  two seconds in CP2K's reported core computation on each run; timings report
  both CP2K TOTAL MAXIMUM and elapsed wall time. It is a workload-specific
  comparison, not a claim about every production calculation. A separate ELPA
  input must record actual `cp_fm_diag_elpa` execution in its timing section.
  Both ELPA inputs set `GLOBAL/TIMINGS/THRESHOLD 0` and `TIMINGS_LEVEL 1` so
  the default two-percent output filter cannot conceal short solver calls.
- Artifact validation recursively inspects all packaged ELF objects and
  symlinks, rejects path-valued DT_NEEDED, old installation/build/home RPATHs,
  and escaping symlinks. Only the current prefix and explicit system runtime
  roots are allowed. This validates the loader contract, not a physical copy
  deployment that has not been performed.
