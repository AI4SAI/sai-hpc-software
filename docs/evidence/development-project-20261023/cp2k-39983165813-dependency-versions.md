# CP2K master dependency compatibility preflight

Workflow: [39983165813](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39983165813).
Source: `cfe5e5d87aaf302953f14eb43587bef2411a8fb2`.
Controller: `10992f818af71543bb39cb81e0f60c9f7ede9535`.
Recipe: `ae6467ac2934deecf1d610c9d60cb8c5100cf931d2c27aaa66819ba85ca76a79`.

[cp2k-39983165813-preflight.json](cp2k-39983165813-preflight.json) preserves
the four complete resolved delivery identities and upstream toolchain version
declarations. [cp2k-new-source-preflight.json](cp2k-new-source-preflight.json)
preserves the upstream commit, parent and changed-file list. The first
`upstream-sources.json` snapshot is unchanged.

The new source is the direct child of `04b255137f7b472b07f09bec26e7bbf81c366a3a`.
It removes reference counting from several Fortran types; it does not change
CMake or toolchain files. Inspection used the exact new Git object in the
project's bare cache once the normal runner transport had installed it.

| Dependency | Upstream toolchain selection | Workflow provider | Source-level compatibility check |
| --- | --- | --- | --- |
| GCC | 14.3.0 | Site 14.3.0, explicit C/C++/Fortran paths | Exact match; native probe checks actual compiler versions. |
| OpenMPI | 5.0.10 | Site 5.0.10, partition ISA | Exact match; MPI Fortran/F08 required. |
| OpenBLAS | 0.3.34 | SAIBLAS declared OpenBLAS 0.3.32 | CMake requires BLAS/LAPACK interfaces, without a newer minimum. |
| ScaLAPACK | 2.2.3 | SAIBLAS declared 2.2.3 | Exact version; distributed interface enabled. |
| FFTW | 3.3.11 | Site 3.3.10 | Discovery requires FFTW3 libraries/headers, without a newer minimum. |
| Libint | 2.13.1 | Native 2.6.0, Fortran, shared, CP2K lmax-5 | No numeric minimum; CMake explicitly requires `libint_f.mod`. The existing private adapter supplies the Fortran include directory. |
| COSMA | 2.8.4 | Native 2.7.0 with ScaLAPACK and CUDA on GPU targets | Config package and imported targets required, without a newer minimum. |
| LIBXSMM | `3057b23` | Native 2.0.0 | No numeric minimum. Its verified source declares `libxsmm_dispatch_gemm(shape, flags, prefetch_flags)`, the signature used by selected LibXS. |
| ELPA | 2026.02.002 | Accepted site 2026.02.001, partition ISA/backend | No numeric minimum. Pinned files and links verified; the application performs a GCC14 Fortran ABI and numerical consumer probe. |
| HDF5 | 2.2.0 | Native 1.14.6, parallel C/Fortran, static | CMake requires C and Fortran components, without a newer minimum; application separately requires parallel HDF5. |
| Spglib | 2.8.0 | Site 2.7.0 | Config package with `Spglib::symspg` required, without a newer minimum. |
| SpLA | 1.6.1 | Native 1.6.1, CUDA on GPU targets | Exact match; config package with `SPLA::spla` required. |
| PLUMED | 2.10.1 | Native 2.10.1, MPI/shared/all optional modules | Exact match; separate kernel library remains required by the receipt validator. |
| LibVORI | 220621 | Native 220621, PIC | Exact match. |
| DFT-D4 | 4.2.0 | Private 4.2.0 | Meets explicit `>=4.2.0` requirement. |
| TBLITE | 0.7.0 | Private standalone 0.7.0 with pinned subdependencies | Meets explicit `>=0.7.0`; new toolchain installer declares no additional patch. |
| DBCSR | `97a623c` | Pinned archive reporting 2.10.0 | Meets explicit config-package `>=2.9` requirement. |
| WignerNJ | 0.8.0 | Private 0.8.0 | Meets explicit config-package `>=0.8` requirement. |

LibXC 7.1.2 and LibXS 930a2c4 are covered by the preceding targeted preflight
and fixes. Optional disabled libraries, including GauXC, SIRIUS, DLA-Future,
Torch, DeePMD, PEXSI, and OpenCL/LibXStream, do not impose requirements on the
selected build. This review did not change the enabled feature set or relax
any version, linkage or scientific gate.

The upstream toolchain versions are its installation selections, not all
minimum compatible versions. No additional explicit minimum-version conflict
was found for the enabled workflow. Actual compile/link compatibility and
numerical correctness remain the application and science stages' responsibility;
the preflight alone does not mark any CP2K delivery accepted.
