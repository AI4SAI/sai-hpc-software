# Quantum ESPRESSO delivery

QE follows the existing release/development workflow on DSPRHBM, 16V100, 4V100,
and 8V100V0. The upstream mirror is QEF/q-e; development follows `develop`.
GitHub release resolution selects the latest non-prerelease tag. Every delivery
pins the complete source SHA and the exact submodule commits from that source.

## Site reference and build profile

The 2026-11-05 read-only audit is in
[evidence/qe-20261105/site-audit.json](evidence/qe-20261105/site-audit.json).
The public 7.5 GPU module points to a fixed zen5 prefix, while separate zen3 and
zen5 installations exist. The CPU module uses oneAPI 2024/Intel MPI. The existing
GPU binaries use NVHPC 26.3, OpenMPI 5.0.10, vendor BLAS/LAPACK and ScaLAPACK,
FFTW and static HDF5. The old HDF5 wrapper was built with NVHPC 24.5/OpenMPI 5.0.8;
the GPU builds retain it and check compatibility in the final-image science gate.
On the CPU target, a compilation probe found that its Fortran modules conflict
with MPI unless CUDA-Fortran mode is enabled. The CPU recipe builds the same
HDF5 1.14.6 release with NVHPC 26.3/OpenMPI 5.0.10 inside
`<QE-prefix>/deps/hdf5`, with parallel I/O and Fortran support retained.

This recipe uses NVHPC 26.3/OpenMPI 5.0.10 on all four targets, with native host
CPU code generation. CPU uses a separate build with GPU disabled. V100 builds
use QE 7.6's `QE_GPU=openacc;cuda`, `QE_GPU_ARCHS=sm_70`, GPU-aware MPI,
OpenMP, ScaLAPACK and HDF5. The site's OpenMPI auto selector chooses AVX2 on
16V100 and 8V100V0, and AVX512 on 4V100 and DSPRHBM. This dependency choice is
separate from the compiler's native CPU code generation.

The `pwall` and `upf` targets, plus four explicitly selected tools omitted by
those CMake umbrella targets, cover the site's existing executable suite,
including pw.x, ph.x, neb.x, pwcond.x and postprocessing tools. OSCDFT is enabled.
Only installed executables, required dependency files, licenses and delivery metadata enter the SIF;
source, compiler objects and custom acceptance cases remain outside the payload.

## Isolation and validation

All builds run inside the private container overlay on project storage at
`/org/org-open-sai/proj-sai-hpc-software`. Site devtools are read-only binds.
No build writes physical /opt or uses physical /tmp. Managed fuse2fs teardown
covers build, export and the final read-only squashfs readout; clean receipts
and filesystem checks are required. The public Apptainer installation is unchanged.

Acceptance uses host OpenMPI to launch the final SIF: one-rank and two-rank
silicon SCF/forces, Gamma-point phonons and the density integral from pp.x.
Inputs and independent numerical references are frozen from upstream QE 7.6.
GPU targets additionally require actual CUDA kernel launch output during SCF.
Raw logs, input hashes, rank/MPI provenance, job accounting and image identity
are rechecked before publication. These are correctness checks, not a performance
claim or an exhaustive scientific validation of every executable.

## Operation

The `Track and build Quantum ESPRESSO` workflow accepts the same tracks, targets
and explicit release retry policy as the other programs. `daily.yml` includes QE.
Development SIFs enter the existing independent beta verification/rotation path;
the module uses the PGI/NVHPC MPI ABI and standard command names such as pw.x.
No payload extraction is needed for beta launches.

Once the complete develop matrix has passed acceptance and the home-maintained
beta has been selected, the administrator can add only the QE gateway:

```sh
bash deployment/sif-beta/install.sh --check /opt/modules/modulefiles/apps qe
bash deployment/sif-beta/install.sh --install /opt/modules/modulefiles/apps qe
```

This installs `qe/develop-beta-later`, which follows the immutable, dated module
selected under `/home/stardust/sai-hpc-software/modulefiles/apps/qe/`.
The preflight rejects an absent or unaccepted generation.

Native installation uses `/opt/apps/qe/qe-<track>-<version>-g<SHA>-r<recipe>/<partition>`
inside the image, ready for the existing administrator-controlled native export.
Physical /opt installation is a separate step after acceptance.

## Initial qualification

Implementation and local validation are in progress. Initial release source:
`qe-7.6@9f93ddec427d2b9a45bb72d828c6d324f62fcabd`.
Initial develop source observed on 2026-11-05:
`61569eb480231b649c47f631df9c5c83537df461`.
Cluster job and acceptance results will be recorded alongside the site audit.
