# Experimental DeePMD-kit / LAMMPS tracking

Work lives on `feat/deepmd-lammps-tracking`, not the production ABACUS branch.
This is an **unpublished experimental pipeline**, not a tested replacement for
the installed software. Do not merge or enable unattended GPU builds until all
live gates below have passed. No production `current.sif` or module is changed.

## Tracking and containment

`deepmd-lammps.yml` resolves DeePMD `master` and LAMMPS `develop` together every
six hours, or explicit branch/tag/release refs on manual dispatch. GitHub cron
is periodic polling with possible scheduling delay, not zero-latency upstream
notification. Schedules only resolve while this branch is experimental; manual
`build_candidates=true` explicitly enables the Slurm build stage. Static CI
success does not mean a candidate compiled or scientific acceptance passed.

Each immutable pair records **both full upstream SHAs** and hashes the actual
deployed trusted recipe, launcher and verifiers. Existing eight-part,
checksum-verified Git bundle transport supplies isolated bare caches; only the
container checks out source. Build/install/temp state stays in a file-backed
overlay. Host `/tmp`, expanded source/install trees and system-module edits are
not used. Candidate SIFs are read-only single-file artifacts.

Remote experimental state stays under
`/home/stardust/sai-hpc-software/experimental/deepmd-lammps/`; the shared minimal
base SIF and system dependencies are read-only inputs. The controller intentionally
has no production publisher and no "unaccepted artifact = cache green" path.
Artifact-cache reuse and automatic publication must be connected to the full
scientific/performance proof before enabling scheduled builds.

## Native target matrix

| Target | Partition | Native CPU | System dependency ISA | GPU |
| --- | --- | --- | --- | --- |
| `4v100-avx512` | `4V100` | Zen 4 | AVX-512 | V100 / SM70 |
| `16v100-avx2` | `16V100` | Zen 3 | AVX2 | V100 / SM70 |
| `8v100v0-avx512` | `8V100V0` | Skylake AVX-512 | **AVX2** | V100 / SM70 |

Every partition runs its own configure/build with `-march=native -mtune=native`,
records `lscpu` and compiler-expanded flags, and never re-labels another target's
binary. The Skylake target does not have AVX512-VNNI and the site's automatic
MPI/BLAS modules correctly select AVX2 dependencies. It gets six build threads
per allocated GPU; GPU jobs do not override Slurm CPU/memory allocation.
LAMMPS has no standalone DSPRHBM product in this experiment; DeePMD retains its
CPU inference backends inside each GPU-target stack.

## System dependencies and required feature parity

The read-only inventory is recorded in
[`evidence/deepmd-lammps-site-2026-09-11.json`](evidence/deepmd-lammps-site-2026-09-11.json).
It is a feature/ABI inventory, **not** scientific acceptance.

The installed baseline is LAMMPS 4 Jul 2026 + DeePMD-kit 3.2.0 + PLUMED 2.10.1.
Its C API reports **70 packages and 2,197 styles in 14 classes**. New candidates
must include every baseline package and style, including DeepMD, DPLR, spin,
Kokkos and PLUMED functionality. `md_build.sh` enables all enumerated baseline
packages, and `verify_parity` rejects omissions rather than silently disabling
an inconvenient package. A missing external dependency is a genuine unfinished
build requirement, not permission to lower the feature baseline.

The site backend stack is Python 3.13.15, PyTorch 2.13.0+cu126,
`tensorflow_cpu` 2.21.0, JAX/JAXLIB 0.11.1. Module wording does not imply a GPU
TensorFlow build. C++11 ABI is 1; the recipe verifies TensorFlow/PyTorch ABI and
requires PyTorch SM70 support on the allocated GPU. It rebuilds the new DeePMD
Python package and C/C++ interfaces against these system frameworks, then builds
LAMMPS with the official built-in integration from **the same DeePMD source SHA**.

PLUMED has no independent site module: the installed LAMMPS module exposes
`/opt/apps/plumed/plumed-2.10.1`. Its kernel SHA256, configured features, GCC and
MPI dependencies are recorded. Reuse this kernel with runtime linking; do not
download/build another PLUMED. ELPA is not a direct dependency of either program
and is not rebuilt or artificially added. For other programs needing ELPA,
prefer the site's preinstalled module.

The installed TF/JAX native libraries contain a stale `.new` RUNPATH. The recipe
explicitly supplies the current TensorFlow library directory and rejects any
remaining unresolved dependency. Actual TF/PT/JAX inference still has to prove
that this dependency composition works. Header/library presence alone is not
backend acceptance.

## Installation paths and copyability

One paired SIF holds two canonical prefixes:

```
/opt/software/deepmd-kit/<paired-version>/<native-target>
/opt/software/lammps/<paired-version>/<native-target>
```

Installation occurs directly at those final paths, not under a host build or
temporary staging prefix. ELF RUNPATH and operational CMake/pkg-config/runtime
metadata are checked for `/workspace`, `/control` and controller-snapshot path
leakage. `$ORIGIN` is used for own shared libraries. System frameworks, MPI and
PLUMED keep their recorded read-only `/opt` paths. This supports copying the
prefixes **to the same paths** on physical storage; arbitrary prefix relocation
and removal of system dependencies are not claimed. No host copy is performed.

## Required live gates before publication

1. Native builds on all three GPU partitions; full `dp --version`, real ELF
   `ldd` and `lmp -h` captured inside the allocation and SIF.
2. Full packages/styles/backend/PLUMED parity against each partition's installed
   baseline; compiler/MPI/CUDA/C++ ABI recorded, all libraries resolved.
3. Actual DeePMD TF, PyTorch and JAX model inference plus LAMMPS DeepMD forces,
   energy and virial; PLUMED distance/bias action executed, not just listed.
   Host Open MPI must launch the SIF ranks for single- and multi-rank tests.
4. Same-node/resource/input benchmark against installed software: warm-up and
   at least three measured repeats, finite positive timings, reference-matched
   energy/forces/virial, median and speedup recorded. No unexplained silent
   performance threshold or comparison across different hardware.
5. Runtime path audit and a read-only final-SIF execution, without the build
   overlay or source/control trees as runtime dependencies.
6. Wire image checksum + both upstream SHAs + recipe/dependency hashes + verified
   Slurm/scientific outputs into artifact-cache and publication gates. Only then
   enable scheduled build/publication and consider a PR.

The current branch contains actual build, inventory, evidence-validation and
test code, but these live gates have **not** all run. In particular a static CI
green or `MD_CANDIDATE_BUILT_NOT_PUBLISHED` cannot be reported as usable software.
