# ABACUS feature parity, native builds and scientific benchmarks

This is an implementation/validation contract, not a claim that the new full-feature
recipe has already compiled or won a benchmark. Its initial baseline was inspected
read-only on `SAI-stardust` on 2026-09-11. No system installation was modified.

## Observed baseline

The default `abacus/v3.9.0.26-sm70-auto` has more optional CPU functionality than
`abacus/develop-git-079fd0c-260724-sm70-auto` (3.11.0-beta6). The required feature
set is their union, not whichever binary is easiest to match. This is the first
parity stage, not the end of the user's "enable other feasible capabilities"
request. Additional methods remain subject to dependency/ABI and reference-case
evaluation; `not_in_observed_baseline` is an observation, never a permanent waiver.

| Capability | Default 3.9.0.26 | Develop beta6 | New recipe requirement |
| --- | --- | --- | --- |
| MPI, OpenMP, LCAO, LibXC, FFTW, ELPA | yes | yes | all targets |
| Cereal, LibRI 2.1.1, LibComm | yes | no | all targets |
| LibTorch 2.1.2, libnpy 1.0.1, NEP, RapidJSON | yes | no | all targets |
| CUDA-aware MPI, cuSOLVERMp | yes | yes | GPU targets |
| New Mp libraries and NCCL-backed collectives | old SDK Mp library | new Mp libraries linked | GPU targets, plus actual feature cases |

The LTS module does not support `--info`; no available CMake cache was found below
the inspected installations. Neither inspected `--info` baseline enabled DeePMD,
TensorFlow, PEXSI or cnpy. Those are **not claimed enabled** by this parity recipe.
DeePMD modules exist on SAI, but compatibility and additional scientific tests
would need separate validation. Presence checks do not prove hybrid-functional,
ML model or NEP numerical behavior; those methods need suitable reference cases.
DFT-D4 is a candidate for later reuse of the native dependencies being prepared
for CP2K, provided ABACUS's required API/version is confirmed.

## Dependencies and containment

`controller/abacus_dependency_lock.json` records exact archive SHA-256 values and
the observed module baseline. The builder binds only the site's existing
`/opt/apps/abacus/abacus-develop-3.9.0.26_nvhpc263gnu/toolchain/build` archive directory,
read-only at `/input/abacus-dependencies`. Every archive is checked before extraction.
There is no dependency download during the network-isolated build, and no source,
dependency build, or installation tree is expanded on the host.

`abacus_dependencies.sh` is the reusable overlay recipe. It installs cereal and
RapidJSON's proper CMake CONFIG targets, supplies LibRI/LibComm/libnpy headers,
and includes the site-pinned CPU LibTorch 2.1.2 distribution with C++11 ABI=1.
These dependency versions are content-pinned inputs; successful compilation
against each tracked ABACUS source is still required, not assumed.

The system NEP DSO has no SONAME and contains another ABACUS installation's
RUNPATH. It is therefore **not copied as the new runtime library**: its locked
source is compiled inside the overlay with `-march=native -mtune=native`, a
`libnep.so` SONAME and an `$ORIGIN` RUNPATH. ABACUS itself is built natively on
each target partition, with GCC's selected flags and node hardware recorded.

System ELPA is required from `elpa/2026.02.001-2603-gnu`; no older bundled ELPA
is built. CUDA 12.9.1, NVHPC 26.3, the matching Open MPI, and
`nvmplibs/26.7-tmp` remain the GPU dependency family.

The four independent targets are DSPRHBM, 4V100, 16V100 and 8V100V0. In particular,
8V100V0's Gold 6146 supports AVX-512 but lacks VNNI; the site's auto MPI/BLAS
correctly select their **AVX2** builds. Runtime dependency ISA comes from the
target's `dependency_isa`, not from the target name's suffix.

## Feature and exported-tree gates

The installed tree is `/opt/software/abacus/<version>/<target>`. Optional runtime
libraries reside under that same prefix's `dependencies/` directory. ABACUS uses
`$ORIGIN`-relative paths for them; `share/sai/runtime-env.sh` derives its own prefix
from its location and can be sourced for either a SIF or the corresponding
administrator-exported installation at the same physical `/opt` path.

The final read-only SIF verification checks all required CMake flags and `--info`
features, minimum LibRI/Torch versions, actual `ldd` resolution, and every packaged
ELF's NEEDED/RPATH/RUNPATH tags. Build-only `/workspace`, `/control`, `/input`, and
controller-snapshot loader paths are rejected. CMake caches and compiler source
paths remain legitimate provenance and are not mistaken for runtime dependencies.
The archive directory is not bound during final ABACUS verification.

The external runtime dependency roots remain `/opt/devtools`, `/usr`, `/lib`, and
`/lib64`; this is not a promise of an independently portable distribution on an
unrelated Linux system. After an administrator copies the tree to its matching
`/opt` path, source `share/sai/runtime-env.sh`, then rerun
`python3 share/sai/abacus_features.py PREFIX TARGET` and the scientific benchmark.
This branch does not perform that physical installation or claim it was tested.

## Benchmark interpretation

`abacus_benchmark.py` prepares a job separately from submitting it. A precise
system module and a checksum-pinned candidate SIF/launcher run the same input on
the same allocation, nodes, ranks, threads and GPUs. Warmups precede repeated,
alternating system/candidate runs; each run starts in a new input-only directory.
The result must include SCF convergence, finite matching energies and all requested
runs before timing statistics are accepted.
The full-feature CI now runs PW, HSE and DeePKS paired benchmarks after ordinary
multi-node and GPU-feature acceptance. Each case requires one warmup pair and at
least three measured pairs against the full-feature system default. Publication
and subsequent cache hits revalidate all three raw benchmark proofs; merely
having a feature-enabled binary is insufficient. Timings are measured/reported,
not forced to pass an invented speedup threshold; correctness failures and missing
measurements fail the gate.

Each new SIF includes self-contained `share/sai/benchmark-cases/pw`, `hse`, and
`deepks` inputs. HSE includes the matching pseudopotentials/orbitals; DeePKS also
includes its Torch model and descriptor orbital. The two optional cases retain
their upstream CPU execution mode even when the containing binary supports GPUs.
Their packaged presence is preparation, not evidence that they have run. NEP's
MD/force path needs a separate validator and is not covered by the SCF analyzer.

Report both process wall time and ABACUS's summed printed SCF iteration times,
including warmup policy, repeat count, median, range and candidate/system ratio.
Printed iteration timing has limited precision and is not a GPU kernel profiler.
An `--info` invocation, a single tiny-case run, or timings from different partitions
do not establish a performance improvement. PW smoke and cuSOLVERMp/NCCL feature
cases also do not establish full hybrid-functional or ML performance parity.

## Initial system-case probe (not a performance comparison)

Slurm `1271264` ran the three small cases once with the system default on
`4V100`, two nodes × one GPU/rank, one thread/rank, after two retained environment
diagnostic attempts (`1271192`, `1271222`). All three SCFs converged and their real
stdout was successfully parsed by the benchmark analyzer:

| Case | Energy / eV | Printed iteration seconds | Process wall seconds |
| --- | --- | --- | --- |
| PW | -4869.7470519499775 | 1.05 (10 iterations) | 2.30 |
| HSE | -428.99428271100163 | 0.54 (49 iterations across loops) | 1.89 |
| DeePKS | -71.32204093440238 | 1.07 (24 iterations) | 2.14 |

Raw inputs/logs remain in
`runtime-tests/baseline-parity-536dfc2-nss-4v100` on SAI. This proves the baseline
case/format compatibility, **not** a candidate speedup or repeatability result.
The working environment uses `--export=NIL` and restores the actual account's
HOME (not a scratch-home reassignment); Open MPI fails to initialize if HOME is
missing. A selective `--export=HOME` attempt was cancelled by UID 0 before logging;
its cause is not established and it is not used by the benchmark driver.
