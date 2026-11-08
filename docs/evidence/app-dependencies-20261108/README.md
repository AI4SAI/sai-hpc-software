# Application dependency preinstallation, November 8

The administrator handoff is ready at:

```text
/home/stardust/sai-hpc-software/deployment/qe-hdf5-abacus-libtorch
```

Run `bash install.sh --check`, `bash install.sh --install`, then
`bash install.sh --verify` there. Installation was not performed by the workflow.
The generated package passed read-only preflight and its directories/files are
traversable/readable by other users. This permission check is not an impersonated
`saiuser01` session.

| Library | Final physical prefix | Size / entries |
| --- | --- | --- |
| QE HDF5 1.14.6 | `/opt/apps/qe/dependencies/hdf5/1.14.6-nvhpc26.3-ompi5.0.10-f90/DSPRHBM` | 32.4 MiB / 170 |
| ABACUS LibTorch 2.1.2 CPU, C++11 ABI=1 | `/opt/apps/abacus/dependencies/libtorch/2.1.2-cpu-cxx11abi1` | 670.3 MiB / 8,553 |

Entries count files, directories and symlinks beneath each prefix. The handoff
contains one 178,298,880-byte SIF (170.0 MiB), not expanded library directories.
Its SHA-256 is
`8c92b3e9ffb5ea9f637c4f642aced4262ac24d53eca5e8d7f94e0f3afcc3f1f9`.
See [the checked summary](raw/summary.json), [preflight](raw/handoff-preflight.txt)
and [permissions](raw/handoff-permissions.txt).

## Contents and compatibility

HDF5 was compiled inside an isolated project-backed overlay at its final prefix,
using NVHPC 26.3, OpenMPI 5.0.10 and `-O3 -tp=host` on DSPRHBM. Static C, Fortran
and high-level libraries, parallel MPI I/O, zlib and SZIP are enabled. The
existing QE F90-interface selection is retained; optional `mpi_f08` overloads
are off. This provider is for CPU QE. GPU QE already selects its site HDF5
module, and CP2K's separate GCC HDF5 provider is not replaced.

LibTorch is the unchanged CPU distribution already pinned by the accepted
ABACUS dependency recipe, archive SHA-256
`904b764df6106a8a35bef64c4b55b8c1590ad9d071eb276e680cf42abafe79e9`.
It is unpacked in the container, not recompiled and not replaced by CUDA Torch.
One installation serves ABACUS across partitions; the GPU build still uses
this CPU LibTorch dependency for DeePKS. The upstream PyTorch 2.1.2 license is
included under `share/licenses/pytorch`.

Both prefixes retain standard headers/modules, libraries, CMake/pkg-config
metadata, wrapper tools and upstream data/licenses. Controller receipts,
tests, source checkouts and build trees stay outside `/opt`. The administrator
installer reads the SIF without running its contents, extracts only the two
allowlisted prefixes, verifies every installed file and installs each complete
tree by rename. It accepts an identical installation and refuses a different
existing tree.

## Validation and preserved failure

All build and test storage is below
`/org/org-open-sai/proj-sai-hpc-software`. The compressed handoff is copied to
the existing readable home deployment directory for the administrator.

| Job | Partition / node | Result | Elapsed |
| --- | --- | --- | --- |
| 2299553 | DSPRHBM / d9470n02 | Libraries built/exported; initial consumer verification failed | 7:04 |
| 2299710 | DSPRHBM / d9470n02 | Identical copied SIF passed both consumers | 1:06 |
| 2299809 | 16V100 / 16v100n18 | Same SIF passed additional LibTorch consumer | 0:18 |

The first verification failed because NVHPC attempted to create its
configuration under read-only `/root/.config`. Both managed-overlay teardowns,
the filesystem check, and the library SIF had already succeeded. The fix binds
the verification run's private project scratch to `/root/.config`. Recovery
pins the source SIF/evidence hashes, preserves the failed run, and verifies an
identical SIF copy without rebuilding or modifying the libraries.

The successful SIF tests exercised:

- Two MPI ranks compiling and running an HDF5 Fortran write/read roundtrip
  using the NVHPC/OpenMPI stack.
- A CMake LibTorch C++ consumer performing checked matrix multiplication and
  TorchScript serialization/reload.
- Loading the actual DeePKS model from ABACUS commit
  `27c3e1065bfe0ca6be2d9b90a93da91bfea1ffe0`, path
  `tests/09_DeePKS/Model_ProjOrb/model_lda_pbe_18.ptg`.
- The LibTorch tests on both DSPRHBM (site GCC 13.3.0) and AVX2 16V100
  (the existing ABACUS GPU recipe's system GCC 13), with resolved runtime
  libraries and complete library inventory checks.

The 13 targeted application-prefix and shared installer tests passed on POSIX
project scratch; logs are in [raw/tests](raw/tests). These include the existing
CP2K provider and real Squashfs extraction tests. Raw jobs, controller/source
hashes, timings and consumer logs are retained alongside
[Slurm accounting](raw/accounting.txt).

Measured preparation costs were **2:51.67** for HDF5 configure/build/install and
**1:11.39** for LibTorch unpacking, validation and license installation. These
are costs on the sampled filesystem/node, not guaranteed daily savings.

The daily QE/ABACUS recipes do not yet select these physical providers. After
administrator installation, activation still needs a build and representative
application acceptance against the installed prefixes. These library consumers
do not substitute for a QE calculation or an ABACUS DeePKS calculation.

## LAMMPS full-rebuild efficiency

The existing November 8 standalone builds used LAMMPS
`d291d572cf3c73a8a720774b78482a7c931304ca`. The
[observed daily build records](raw/lammps-daily-builds.json) show:

| Partition | Job | Complete build allocation elapsed |
| --- | --- | --- |
| 4V100 | 2296792 | 43:51 |
| 16V100 | 2296820 | 53:23 |
| 8V100V0 | 2296810 | 1:17:46 |

These are completed build records, not a new audit of the daily publication or
science gates. CMake 3.31 uses Unix Makefiles and 24 workers. The recipe already
reuses installed DeePMD, PLUMED, Eigen and Voro++, and selects SM70 alone. It
retains 71 packages, classic GPU and Kokkos CUDA. Logs contain 2,650 compilation
or generated-CUDA messages, including 2,200 LAMMPS C++ compilation messages.

On 16V100 the build container took 51:49, export 48 seconds and read-only
Squashfs emission 16 seconds. All three managed teardowns took under one second.
The current long duration is dominated by building, not delayed container exit.
Whole-job CPU use averaged about 11 cores; that alone cannot distinguish SMT,
compiler dependencies, memory pressure and filesystem waits.

Job **2299708**, on `16v100n11`, compared the existing fakeroot preload with
`--ignore-fakeroot-command` in order **stock, bypass, bypass, stock**. Each pass
recompiled four source files with the exact saved build flags, deleting each
previous output. No compiler/object cache was used. The accepted build overlay
was copied to an independent project-backed overlay and mounted read-only.
The initial attempt 2299639, rejected before compilation for using another
run's overlay path, is retained in accounting.

| Source | Stock mean seconds | Bypass mean seconds |
| --- | --- | --- |
| `angle.cpp` | 4.251 | 4.003 |
| `pair.cpp` | 5.084 | 4.930 |
| `MANYBODY/pair_tersoff.cpp` | 4.850 | 4.738 |
| `KOKKOS/pair_lj_cut_kokkos.cpp` | 47.073 | 46.314 |
| Four-file total | 61.259 | 59.986 |

All 16 compilations and four container teardowns passed. The **2.08%** difference
does not establish a significant full-build improvement. This sequential sample
does not measure 24-worker fakeroot contention; page caches were not flushed.
The [probe scripts, raw samples and receipts](raw/lammps-probe) are preserved.

The next useful controlled experiment is a clean **Ninja versus Make** build:
the current logs complete the GPU dependency target before starting LAMMPS
objects. Ninja may overlap independent targets more effectively, but the gain
is unmeasured. This needs a pinned Ninja binary and adapting the existing CUDA
verification, which currently reads `flags.make`, before switching generators.
Use the same source, packages, CPU allocation and runtime correctness gates.

Also measure 16/24/32 workers against physical-core topology and memory use;
32 logical CPUs need not mean 32 independent physical cores. Do not claim that
simply increasing `-j` improves the build. The current
[upstream Kokkos integration](https://github.com/lammps/lammps/blob/d291d572cf3c73a8a720774b78482a7c931304ca/cmake/Modules/Packages/KOKKOS.cmake#L182)
explicitly sends all translation units through the device compiler. Separating
ordinary C++ compilation from CUDA is a larger integration change, not an
already supported one-flag optimization.

No LAMMPS compiler cache, package removal, generator change or production
fakeroot change is introduced by this preinstallation work.
