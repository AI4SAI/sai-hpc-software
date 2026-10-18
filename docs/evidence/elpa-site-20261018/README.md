# ELPA 2026.02.001: two ISA builds ready for administrator deployment

Both final SIFs completed compilation and numerical acceptance on 2026-10-18.
The administrator handoff is at
`/home/stardust/sai-hpc-software/deployment/elpa-2026.02.001`.
No physical `/opt` installation or public module was changed by this work.

| ISA | Build/acceptance job | Node | Elapsed | Module load/unload job | Solves |
| --- | --- | --- | --- | --- | --- |
| AVX2 | 1846755 | 16v100n10 | 2:59 | 1846787 | 48/48 |
| AVX512 | 1846756 | 4v100n27 | 1:30 | 1846788 | 48/48 |

All four jobs completed with exit `0:0`. Each SIF was tested without the build
installation bind or the historical HCOLL alias. Tests compile fresh C and
Fortran consumers against the installed headers/modules and library. Numerical
coverage includes real/complex Hermitian matrices, ELPA1/ELPA2, CPU/V100,
one/two MPI ranks, two OpenMP threads, repeated solves, and the ELPA2
nonblocking option. Maximum eigenvalue error was `2.22e-15`, residual
`6.46e-16`, and orthogonality error `1.665e-15`; the threshold was `1e-10`.
The actual deployment module also passed load and unload inside each SIF.

The 128-by-128 analytic tridiagonal matrices test numerical correctness, not
large-system performance or multi-node scaling. GPU execution was tested on
V100; sm75/80/86/89 images were checked for presence, not executed on those GPUs.

The build retains the site's ELPA version, GCC 13.3.0, OpenMPI 5.0.10,
CUDA 12.9.1 / NVHPC 26.3, OpenBLAS 0.3.32 and ScaLAPACK 2.2.3, plus OpenMP,
cuSOLVER, CUB, CUDA-aware MPI and all five GPU architectures. The new prefixes
are `/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-{avx2,avx512}/nvidia`,
identical in the SIFs and future physical installations. The automatic module
uses the site's VNNI criterion so its MPI/BLAS and ELPA ISA selections agree.

The small [compatibility patch](../../../deployment/elpa-2026.02.001/cuda-aware-cpu.patch)
addresses observed failures while keeping CUDA-aware GPU communication:

- CPU ELPA2 paths in `tridi_to_band` and `band_to_full` incorrectly passed
  uninitialized device-array descriptors to MPI. The trace captured an
  attempted allocation of `1125823790914056` bytes. CPU paths now use their
  existing host arrays for broadcasts and reductions.
- Site OpenMPI's `libnbc` faulted on a device-buffer reduction with two ranks.
  Three GPU call sites already waited immediately after starting a collective;
  they now use the equivalent blocking collective on the same device buffers.
  CPU nonblocking collectives are preserved and tested.

The source archive and patch checksums are recorded in
[release.json](../../../deployment/elpa-2026.02.001/release.json).
The original test failures are retained beside the successful raw logs so the
compatibility changes can be reviewed independently.

The handoff was exported from the accepted SIFs and checked against per-file
SHA256 manifests. It contains about 19.43 MiB (AVX2) and 20.56 MiB (AVX512),
including libraries, development headers/Fortran modules, pkg-config/libtool
metadata, kernel information tool, documentation and provenance. Source trees,
objects, test executables, caches and system dependencies are excluded.
The package is readable/traversable by other accounts, without granting write
permission. Administrator instructions are in the
[deployment README](../../../deployment/elpa-2026.02.001/README.md).
