# CP2K validation status

This is an implementation checklist, not a release acceptance result.

## Source and baseline

- Candidate source: CP2K commit `6d276e9c480f2b55f5a0b9166e22d20d9a3f8da0`.
- Baseline module: `cp2k/2026.1-cuda12.9-sm70-auto`.
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

## Probe evidence

- Job `1214324`: standalone CUDA/MPI DBCSR build succeeded.
- Job `1215004`: DBCSR build and install succeeded; CP2K configuration failed
  because CuSolverMP include/library paths were not discovered.
- Job `1215480`: submitted with `nvmplibs/26.7-tmp` and explicit CP2K
  CuSolverMP/NCCL roots. Outcome pending.
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
