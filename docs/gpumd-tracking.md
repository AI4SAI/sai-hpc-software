# GPUMD native GPU tracking (experimental branch)

The independent `feat/gpumd-daily-tracking` branch adds a daily poll of upstream
`master` and `latest-release`. Every poll resolves a full current Git SHA; it is
not a webhook or a zero-delay mirror. GitHub schedules become active only after
the workflow reaches the repository default branch. On the experimental branch,
push/PR run static tests; an explicit dispatch is needed for a cluster build.

No CPU-only GPUMD build is planned. Each target compiles on its own partition,
using GCC 13.3 `-march=native -mtune=native` and CUDA 12.9.1 `sm_70`:

| Target | Partition | Native CPU | Site dependency ISA |
| --- | --- | --- | --- |
| `4v100-avx512` | `4V100` | `znver4` | AVX-512 |
| `16v100-avx2` | `16V100` | `znver3` | AVX2 |
| `8v100v0-avx512` | `8V100V0` | `skylake-avx512` | AVX2 (Gold 6146 lacks VNNI) |

The workflow reuses the normal source cache, per-run trusted controller snapshot,
Slurm build, isolated file-backed overlay, read-only SIF and recipe-hashed cache.
GPUMD run IDs are prefixed `gpumd-`, and artifacts/modules are under the GPUMD
catalog, never ABACUS/CP2K. Source checkout, build and installation stay inside
the overlay. No host `/tmp` or host installation tree is used.

## Features and system dependencies

The package installs `gpumd`, `nep`, and `gnep`. Site GPUMD 5.8 and master both
actually print version 5.7 and directly link `libdeepmd_cc.so`; the version label
alone is not evidence of features. The recipe explicitly enables `USE_DEEPMD`
and `USE_PLUMED` through upstream's Make build interface.

| Dependency | Policy |
| --- | --- |
| CUDA/GCC | Read-only `cuda/12.9.1`, `gcc/13.3.0` modules |
| DeePMD C++ interface | Read-only `deepmd-kit/3.2.0`, `/opt/apps/conda_env/deepmd-kit-3.2.0` |
| PLUMED | Read-only `/opt/apps/plumed/plumed-2.10.1`; site has no independent module |
| ELPA | Not a GPUMD dependency; no duplicate ELPA installation |

The installed site DeepMD environment has PyTorch GPU, but its TensorFlow
distribution is CPU-only. This GPUMD integration uses a PyTorch model in the
DeepMD execution test and does not claim TensorFlow GPU support. PLUMED is an
additional GPUMD feature; the inspected site GPUMD did not enable it.

## Acceptance and benchmark gate

No module or `current.sif` is published until all required checks pass:

- Actual ELF `NEEDED`, complete `ldd`, and executable-set comparison to the site
  binary; no unresolved libraries or build-tree runtime loader paths.
- Same-revision 250-atom static NEP gold energy and all force components, plus
  NEP prediction energy/forces/virial arrays.
- Reproducible short NEP training from the same restart, JIT versus generic
  loss comparison, and rejection of the silent JIT-failure fallback.
- GNEP training and prediction, then GPUMD evaluation of the same trained NEP5
  model with numerical energy/force agreement.
- PLUMED distance/restraint bias and actual equal/opposite force feedback.
- A tiny locally generated DeepMD PyTorch model, comparing GPUMD and the
  independent DeepMD Python evaluator on energy and forces, for both the
  candidate and site GPUMD binary. This is an interface test, not a claim that
  a one-step-trained potential has physical predictive accuracy.
- The published host launcher runs the pinned candidate and reproduces the
  static gold result.
- A separate 2000-atom NEP MD throughput workload: baseline pilot calibrates
  at least 10000 steps and approximately five seconds of engine work, then
  freezes identical inputs for candidate and baseline, each with a warmup and
  three repeats. Reports include engine atom-step/s, steps/s, wall time and
  medians. Short static/prediction wall times are labelled microbenchmarks,
  not production throughput. There is no invented speedup/pass threshold.

Each proof is tied to the SIF checksum, launcher/controller scripts, Slurm job,
raw numerical outputs and benchmark files. Failed or stale proof cannot hit the
scheduled artifact cache. All scientific work and timing run on an allocated
GPU, not the login node.

## Installation and copying

Canonical prefix: `/opt/software/gpumd/<version>/<target>`. The SIF contains
only the installation; external dependencies remain read-only at their recorded
site paths. It also contains matching CUDA source/headers under
`share/gpumd/src`: modern NEP training JIT genuinely needs these, so copying only
the two executables is not a complete installation.

If the complete prefix is later copied to physical storage, source
`<prefix>/share/sai/runtime-env.sh` before running it. The script derives its own
prefix and JIT source path relative to its location; JIT writes into the job's
managed `TMPDIR`, never the installed source tree. ELF loader paths may refer to
documented system dependencies but must not refer to `/workspace`, `/control`,
or the remote controller snapshot. Build-info/`__FILE__` strings are not confused
with runtime dependencies. This branch does not itself copy an install tree out
onto the host.

## Evidence so far

On 2026-09-11, site-only 4V100 preflight job `1271028` completed `0:0` in five
seconds inside the minimal SIF. Static energy was `-930.8630471229553 eV` with
zero force difference from its same-source reference. NEP prediction maximum
force difference was `1e-5`, virial difference `1e-6`; JIT/generic two-generation
loss arrays matched exactly. The 2000-atom/1000-step pilot reported
`0.837087 s` engine time and `2.38924e6 atom*step/s`. This is a single baseline
pilot, not a candidate-versus-baseline speedup result.

The first preflight (`1271011`) failed because the minimal rootfs has no usable
`awk` alternative; the environment now parses native GCC flags using Bash.
New package compilation, GNEP/PLUMED/DeepMD acceptance, copied-prefix runtime and
three-target performance results remain unverified until their real jobs pass.
