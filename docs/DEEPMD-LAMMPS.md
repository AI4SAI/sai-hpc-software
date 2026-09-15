# Experimental DeePMD-kit / LAMMPS tracking

Work lives on `feat/deepmd-lammps-tracking`, not the production ABACUS branch.
This is an **unpublished experimental pipeline**, not a tested replacement for
the installed software. Daily builds remain unpublished candidates until all
live gates below have passed. No production `current.sif` or module is changed.

## Tracking and containment

The default-branch daily scheduler dispatches `deepmd-lammps.yml` on this feature
branch with `software=all`, all three channels and GPU targets,
`build_candidates=true`, and `retry_releases=false`. The MD workflow has no cron
of its own, preventing duplicate polling. Manual dispatch uses the same explicit
build input and independently resolves each component's latest requested channel.
GitHub scheduling can be delayed; it is not an upstream push notification. Static CI
success does not mean a candidate compiled or scientific acceptance passed.

Within an enabled build invocation, development primaries always compile, even
when their SHA is unchanged. Release and prerelease primaries attempt only the
latest resolved ref/SHA per target, once across all previous attempts, including
failures. A manual dispatch with `retry_releases=true` explicitly retries the
selected versions; an unchanged failed version is never retried automatically.
The shared `release_contract.claim_build` records the decision before source
transport in `runs/<run>/input/build-attempt.json`. It considers only requested
primary triggers, not their companions; recipe or companion changes do not count
as a new primary release. Any selected primary builds the locked pair once.
An all-skipped pair emits only a build-attempt receipt, with no artifact or new
acceptance claim. A scheduler dispatch is a build trigger, never publication or
scientific acceptance.

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
Candidate builds now automatically submit a separate scientific acceptance
allocation. Its trusted host controller rechecks raw LAMMPS output/PLUMED data,
all host-MPI rank traces, exact image/executable/ISA, fixed tolerances and the
oracle read from the requested upstream commit; six-backend/engine summaries
alone cannot establish success. This wiring still needs a live candidate.
Artifact-cache reuse and automatic publication still require the full
scientific/performance proof; scheduled builds do not bypass these gates.

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
`tensorflow_cpu` 2.21.0, JAX/JAXLIB 0.11.1. Both TensorFlow and JAX were confirmed
**CPU-only** in allocated-node probe `1271205`; only PyTorch exposed CUDA in that
dependency environment. Module wording does not imply GPU TF/JAX. C++11 ABI is 1;
the recipe verifies TensorFlow/PyTorch ABI and
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

The follow-up contained GPU probe
[`1271205`](evidence/deepmd-lammps-native-probe-1271205.json) completed `0:0` on
`4v100n15`: real `lmp -h`, all backend `ldd`, ABI checks and a PyTorch SM70 GPU
sum kernel passed after correcting cleanenv/Lmod, real BLAS/LAPACK paths and
TensorFlow library lookup. It did **not** execute a DeepMD model, PLUMED case,
new build or benchmark. Its log also records an Apptainer fuse2fs cleanup warning.

## Installation paths and copyability

One paired SIF holds two canonical prefixes:

```
/opt/software/deepmd-kit/<actual-track>/<build_id>/<PARTITION>
/opt/software/lammps/<actual-track>/<build_id>/<PARTITION>
```

Installation occurs directly at those final paths, not under a host build or
temporary staging prefix. ELF RUNPATH and operational CMake/pkg-config/runtime
metadata are checked for `/workspace`, `/control` and controller-snapshot path
leakage. `$ORIGIN` is used for own shared libraries. System frameworks, MPI and
PLUMED keep their recorded read-only `/opt` paths. This supports copying the
prefixes **to the same paths** on physical storage; arbitrary prefix relocation
and removal of system dependencies are not claimed. No host copy is performed.

The workflow resolves `development`, `prerelease`, and `release` independently
for each software. A manual `software` filter selects either primary; identical
pairs share a build. A missing companion prerelease uses its latest stable
release only after an explicit no-prerelease result. Network/ref failures abort.
The runner locks schema-2 plans to both shared release identities before source
transport; submit, container verification, sidecar, and scientific runtime check
the same full source SHAs, recipe digest, actual channels, and real partition.
Legacy schema-1 pairs are rejected by submission. Branch build labels include
the pinned commit's UTC date before the SHA, using the shared source resolver.
Candidate filenames use `md-<run>-<attempt>-<UTC-build-date>-<selection-SHA>.sif`,
matching the main workflow's date-before-SHA convention. Build, acceptance and
runtime obtain the paired SIF location from the same existing controller module;
images outside that canonical source/recipe/partition path are rejected.

The recipe passes observed dependencies and installed `dp`/`lmp` commands
directly to `export_native.write_manifests`, after scientific fixture preparation.
Each prefix contains its own inventory and native Tcl module. Embedded inventories
are rechecked against both identities in the final SIF. These are candidate
delivery metadata, not successful native deployment or scientific evidence.
The PB-to-PT conversion blocker below still prevents completing this recipe;
no model metadata is invented, and no candidate is automatically published.

## Required live gates before publication

1. Native builds on all three GPU partitions; full `dp --version`, real ELF
   `ldd` and `lmp -h` captured inside the allocation and SIF.
2. Full packages/styles/backend/PLUMED parity against each partition's installed
   baseline; compiler/MPI/CUDA/C++ ABI recorded, all libraries resolved.
3. Actual DeePMD TF, PyTorch and JAX model inference plus LAMMPS DeepMD forces,
   energy and virial; PLUMED distance/bias action executed, not just listed.
   Host Open MPI must launch the SIF ranks for single- and multi-rank tests.
4. Same-node/resource/input benchmark against installed software: the six-atom
   scientific smoke is **not** the performance workload. `md_performance.py`
   defines a separate 2,058-atom synthetic fixed-geometry force-evaluation case,
   baseline calibration to a five-second engine interval and frozen common step
   count/input. Both implementations require warm-up and three measured repeats
   of at least three seconds each, raw LAMMPS engine throughput plus separate
   whole-process walltime, and independently verified large-system numerics.
   Record real backend execution device; TF/JAX baselines are CPU-only and cannot be
   relabelled as a fair GPU comparison. Candidate too fast for the minimum
   interval requires common recalibration, not a different candidate workload.
   The timing contract and allocated runner have unit/protocol tests; their
   device-trace/numerical proof still need live testing and acceptance integration.
5. Runtime path audit and a read-only final-SIF execution, without the build
   overlay or source/control trees as runtime dependencies.
6. Wire image checksum + both upstream SHAs + recipe/dependency hashes + verified
   Slurm/scientific outputs into artifact-cache and publication gates. Only then
   enable scheduled build/publication and consider a PR.

`md_relocate_audit.py` now uses explicit runtime root allowlists, validates final
symlink destinations (including broken/escaping chains), rejects paths embedded
in `DT_NEEDED`, checks RUNPATH/operational metadata, and handles static ELF
without trying `ldd` on it. Passing this static/runtime-reference audit is still
not proof that an actual same-prefix physical copy has been executed.

The current branch contains actual build, inventory, evidence-validation and
test code, but these live gates have **not** all run. In particular a static CI
green or `MD_CANDIDATE_BUILT_NOT_PUBLISHED` cannot be reported as usable software.

### Offline native dependencies (2026-09-15)

Compute nodes do not provide DNS or outbound HTTP. The LAMMPS CMake recipe
therefore disables its Voro++ and Eigen3 `ExternalProject` downloads and uses
the read-only site copies bound at `/opt/apps`: Voro++ 0.4.6 from the
DeepMD 3.1.2 environment and Eigen 3.4.0 from the VeloxChem GPU environment.
The build still enables the same `VORONOI` and `MACHDYN` packages as the site
baseline; only dependency transport is changed. The site LAMMPS potential set
is copied into the overlay before configuration for the same offline reason.

## Historical bounded diagnostics (2026-09-11)

LAMMPS source bootstrap is now complete in the independent MD bare cache at
`experimental/deepmd-lammps/cache/repositories/lammps`, exact commit
`ac6d475f60ec86678d9b633ff99914bdea5b9c94`. The existing local eight-part bundle
was **not** uploaded again: seven remote parts already matched SHA256; only
part 02 was resumed (111,489,034 transfer bytes). A separate `source-bootstrap-r2`
hard-linked receive staging preserved all original r1 parts. Receiver checksum,
Git bundle verification, full `git fsck` and cache inventory succeeded. The
unborn bare-cache `HEAD` notice is expected: the exact commit lives at
`refs/cache/<SHA>`. No host source checkout or new candidate build was performed.

The old three-backend baseline attempt stopped with the following evidence:

| Allocated probe | Actual result |
| --- | --- |
| `1273701`, baseline r3 | CPU-only conversion with faulthandler still segfaulted in the native Triton extension import. |
| `1274280`, import-order r1 | `triton`, `torch→triton`, and `deepmd.pt.model.descriptor` imported successfully. Both orders with TensorFlow loaded before Triton (`TF→Torch→Triton`, `Torch→TF→Triton`) terminated with SIGSEGV. |
| `1274406`, import-order r2 | `Triton→TF→Torch` and `Torch→Triton→TF` succeeded. Preloading Triton avoided the conversion segfault, but the exact original graph then failed conversion with `GraphWithoutTensorError`: missing `train_attr/training_script:0`. |

These results establish an import-order conflict in the installed combination;
they do not identify its native-symbol root cause or prove backend inference.
The probes changed neither installed packages nor model weights/metadata. The
Triton-preload experiment is **not** enabled as an automatic production workaround.
The old `deeppot.pbtxt` fixture cannot establish three-backend acceptance; its
missing training metadata is not reconstructed or invented.
Precise commands, hashes and non-acceptance status are recorded in
[`evidence/deepmd-lammps-import-diagnostics-20260911.json`](evidence/deepmd-lammps-import-diagnostics-20260911.json).

### Serializable fixture preparation (2026-09-15; live validation pending)

Preparation now uses the upstream-committed
`source/tests/infer/deeppot_sea.yaml` and the first periodic six-atom case in
`source/tests/infer/deeppot-testcase.yaml`, from the exact requested DeepMD SHA.
The serialized model contains its original weights and real `model_def_script`;
the separate test case contains pre-committed energy, all 18 force components,
and all 54 atomic virial components. These are not generated by the candidate.
The YAML virials have the physical sign, unlike the old LAMMPS stress oracle.
The models and numbers are different from the old fixture and are not relabelled
as equivalent. Both baseline and candidate evaluate the same new model inputs.

At reviewed upstream commit `28b7d068801716765ab8119257f814596e49a10c`, the model
SHA256 is `a1056a028be81b02757a164c917a6f0ff0d5e642da3d7c861f8d278aa82cf016`
and the reference-file SHA256 is
`d383b5d80040a94683c2da416d7a58f7519ef2c39f83a77c9492bf2b5a41cf99`.
Upstream `infer/case.py` converts this serialized model with `convert_backend`;
`infer/convert-models.sh` explicitly includes its JAX SavedModel export.

Each TF/PT/JAX model is converted directly from that same YAML in a fresh CPU
process using the narrow `deepmd.entrypoints.convert_backend.convert_backend`
API. This removes the old PB-to-PT metadata dependency and avoids the CLI's
unrelated eager imports. No Triton preload or installed-package patch is used.
Safe YAML loading rejects duplicate keys and unexpected model/topology changes.
PyYAML is required on the verifier host and in the existing DeepMD environment.
The acceptance controller re-reads both committed files and checks their hashes,
paths, case index, all numerical references and existing raw execution evidence.
Conversion failure still stops preparation without a completed fixture manifest.
These code and protocol checks are not proof of successful live conversion,
candidate compilation, inference, feature parity or performance.

`md_performance_run.py` now implements the allocated execution layer: all 2,058
forces and virial components are checked, timing runs are pinned to one actual
CPU/core and one thread, and final-container binding is recorded and compared.
PyTorch requires a separate same-input Nsight kernel trace for each implementation;
profiled runs are excluded from the speed comparison. Trace checksums and the
launcher checksum are rechecked. This runner has unit/protocol coverage but has
**not** completed a live candidate benchmark or been wired into publication.

The newer common partitioned delivery contract is being integrated separately.
The old paths shown above are an honest description of this branch's current
recipe, not the final deliverable. Integration must replace source tracking,
request identities, build prefixes, runtime paths and acceptance assertions as
one change; old-layout images cannot be relabelled. The shared identities will
use `/opt/software/<software>/<track>/<build-id>/<literal-partition>` and lock
both exact source SHAs for the paired stack.
