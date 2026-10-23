# GPUMD native GPU tracking and acceptance

October 23: the latest release is `v5.9.1` at
`11ba6fbdb4d71b2cdb351b39972fa057521585b3`; current `master` resolves to
`2c93346fce4461de1c42c77e7fe74cdb6b0e3df6` (October 21).
The latest release now has fresh native builds on all three V100 partitions
from [run 39955137406](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39955137406).
All three passed science, performance, native export readback and
publication; the full release workflow concluded successfully.
Current `master` now also passes on all three targets in
[run 39963334471](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39963334471).
Builds `1912478` (4V100), `1912477` (16V100) and `1912479` (8V100V0) completed
in 73, 129 and 194 seconds. Science jobs `1912533`, `1912536` and `1912539`
completed in 94, 122 and 125 seconds, all `COMPLETED / 0:0`.
Independent revalidation reparsed raw numerical outputs, verified SIF and
controller hashes, re-read 457 exported files per master target, and checked
the published module. All six release/master candidates are accepted;
physical `/opt` installation and native execution remain pending.
See the [project delivery evidence](evidence/gpumd-project-20261023/README.md).

The first master attempt (`39961430052`) exposed a packaging bug: unanchored
`tar` exclusions for the `nep` executable also removed the new `main_nep/nep/`
source directory, so relocated NEP JIT fell back and failed acceptance.
Binary exclusions now apply only at the source root; inspection recognizes
both upstream layouts while actual JIT execution remains mandatory.
The corrected master passes relocated JIT and JIT/generic loss comparison
with zero difference. Its NEP MD throughput differs from the site baseline
by less than 0.3% on each target; release differences are below 0.5%.
Neither track needed a performance retest. These are the fixed 2000-atom
NEP acceptance cases, not a general production performance claim.
The original failed builds and their overlays are preserved.

New GPUMD CI builds and acceptance use
`/org/org-open-sai/proj-sai-hpc-software` with Slurm account
`proj-sai-hpc-software`. The controller receives this root explicitly,
including when continuing an existing build. Both build and science job
scripts pin the account and working directory. Three targets can run in
parallel, with singleton build names separated by partition and recipe.
New project jobs use `rush-1o2gpu` on 4V100/8V100V0 and `flood-1o2gpu` on
16V100, with 30 minutes for builds and 15 minutes for science. The account
and the individual partition must both allow the selected QoS. 16V100
does not permit `rush-1o2gpu`, and its `rush-gpu` requires at least four GPUs;
its one-GPU jobs retain flood. Both selected QoS entries have `UsageFactor=1`
and no configured preemption targets. The original pending master job
`1912383` was cancelled and replaced by the corrected workflow. The frozen release run
began with the old flood QoS; its queued 4V100 build and science jobs were
updated in Slurm, with original/updated accounting records retained. All
three builds finished in 80–176 seconds. The longer flood requests had
forecast starts one to four days away on the busy 4V100 partition.
The base SIF and self-contained bare source cache were copied into the new
tree; original home data and frozen controllers stay in place. This change
does not move existing CP2K, ABACUS or MD automatic-build continuations.

The first project-account attempt (`39953638995`, jobs `1911630`, `1911632`,
`1911636`) stopped before compilation: the project's inherited POSIX ACLs
were copied into the fakeroot overlay's `upper` and `work` directories,
which Apptainer could not write. Build/acceptance scratch directories now
discard inherited access/default ACLs and set mode 0700 before Apptainer
creates temporary files; project/input/result permissions remain intact.
A small container write probe in the project tree passed with this fix.
The failed attempts and their overlays are retained.

The integrated workflow builds upstream
`development`, `prerelease`, and `release` through the shared source resolver.
Development uses `master`; release channels resolve their live upstream tags.
Every poll resolves a full current Git SHA; it is
not a webhook or a zero-delay mirror. Daily polling calls `gpumd.yml` as a reusable workflow from the shared
`daily.yml`; the parent checks its result. `gpumd.yml` has no independent cron trigger.
Push/PR run static tests; workflow dispatch starts
a cluster build. Explicitly absent release/prerelease channels are reported as
skipped for every dispatch; an entirely empty result skips the build job.

Development builds run every time, both on schedule and manual dispatch.
Release and prerelease builds run once per upstream ref/SHA and GPU target;
failed attempts also count, and recipe changes do not automatically retry them.
Manual dispatch can explicitly enable `retry_releases` (default `false`).
An attempt record only controls compilation and never certifies an artifact or
scientific acceptance.

No CPU-only GPUMD build is planned. Each target compiles on its own partition,
using GCC 13.3 `-march=native -mtune=native` and CUDA 12.9.1 `sm_70`:

| Target | Partition | Native CPU | Site dependency ISA |
| --- | --- | --- | --- |
| `4v100-avx512` | `4V100` | `znver4` | AVX-512 |
| `16v100-avx2` | `16V100` | `znver3` | AVX2 |
| `8v100v0-avx512` | `8V100V0` | `skylake-avx512` | AVX2 (Gold 6146 lacks VNNI) |

The workflow reuses the normal source cache, per-run trusted controller snapshot,
Slurm build, isolated file-backed overlay, read-only SIF and recipe-hashed cache.
GPUMD run IDs are prefixed `gpumd-` and include the UTC submission date through
the shared CI naming. Artifacts/modules are under the GPUMD
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
- GNEP training and prediction, plus numerical finite-difference force checks
  for 12 Cartesian components across four structures using its frozen model.
  The ±0.025 Å displacements must reproduce the analytical force within
  0.02 eV/Å plus the calculated rounding of upstream's six-significant-digit
  energy output. The rounding allowance is recorded and capped at 0.08 eV/Å;
  less precise output cannot certify this check. GPUMD cross-evaluation is also required for supported formats;
  upstream revisions whose GNEP produces NEP5 while GPUMD accepts only NEP4
  explicitly record that format boundary and still require the independent
  numerical derivative. This does not claim NEP5 GPUMD support.
- PLUMED distance/restraint bias and actual equal/opposite force feedback.
- A tiny locally generated DeepMD PyTorch model, comparing GPUMD and the
  independent DeepMD Python evaluator on energy and forces, for both the
  candidate and site GPUMD binary. This is an interface test, not a claim that
  a one-step-trained potential has physical predictive accuracy.
- The published host launcher runs the pinned candidate and reproduces the
  static gold result.
- A separate 2000-atom NEP MD throughput workload: both binaries run a 1000-step
  pilot, then the faster pilot determines a shared workload of at least 10000
  steps targeting six seconds. Each actual measurement must contain at least five seconds
  of engine work. Identical frozen inputs run once for warmup and three times
  per binary, alternating which binary runs first in each pair. Raw evidence
  rechecks calibration, invocation order, executable hashes, allocation and
  thread settings. Reports include engine atom-step/s, steps/s, wall time and
  medians. More than 10% slower time for identical work triggers one full repeat
  on the same artifact; a repeated regression blocks publication. The repeat
  reuses the original calibrated inputs and generated DeepMD model. Submission
  and publication both reparse the original verified raw evidence, require its
  actual regression, and pin its job, request and evidence hashes. Changing the
  image, controller, candidate or baseline binary, input files, thread settings
  or GPU model/driver configuration invalidates the repeat. Short static
  and prediction wall times are startup microbenchmarks, not throughput.

Each proof is tied to the SIF checksum, launcher/controller scripts, Slurm job,
raw numerical outputs and benchmark files. Failed or stale proof cannot hit the
scheduled artifact cache. All scientific work and timing run on an allocated
GPU, not the login node.

## Installation and copying

Canonical prefix: `/opt/apps/gpumd/GPUMD-<track>-<build_id>/<partition>`, using
the shared schema-2 release contract. Historical schema-1 `/opt/software`
identities remain readable without relabeling. Development labels include the pinned commit's UTC
date; the shared build ID adds source and recipe hashes. The SIF contains
only the installation; external dependencies remain read-only at their recorded
site paths. It also contains matching CUDA source/headers under
`share/gpumd/src`: modern NEP training JIT genuinely needs these, so copying only
the executables is not a complete installation.

The existing final inspection writes `share/sai/native-entry.json` and observed
site-library provenance, then calls the shared `export_native.write_manifests`.
That writer packages native Tcl modules and one schema 2
`share/sai/manifest.json` inside the complete prefix. The final read-only SIF
inspection recomputes the full inventory, including portability evidence and
JIT sources. The native module selects the real Slurm partition, executes its
own installed commands directly, and records the exact external site modules,
DeepMD and PLUMED roots; it does not load the DeepMD application module's
unrelated LAMMPS executable paths.

The separate SIF launcher validates a pinned artifact through shared
`delivery_layout`; its full checksum is reused only within the same Slurm job
and host, with file and sidecar invalidation. Scientific reports and publication
proofs carry the same release identity. Packaging a complete candidate does not
set scientific acceptance or publish a module.

If the complete prefix is later copied to physical storage, source
`<prefix>/share/sai/runtime-env.sh` before running it. The script derives its own
prefix and JIT source path relative to its location; JIT writes into the job's
managed `TMPDIR`, never the installed source tree. ELF loader paths may refer to
documented system dependencies but must not refer to `/workspace`, `/control`,
or the remote controller snapshot. Build-info/`__FILE__` strings are not confused
with runtime dependencies. The relocation probe stays inside the build overlay;
publication separately exports a verified native tree into project staging.
It does not install that tree into physical `/opt`.

## Historical preflight evidence

On 2026-09-11, site-only 4V100 preflight job `1271028` completed `0:0` in five
seconds inside the minimal SIF. Static energy was `-930.8630471229553 eV` with
zero force difference from its same-source reference. NEP prediction maximum
force difference was `1e-5`, virial difference `1e-6`; JIT/generic two-generation
loss arrays matched exactly. The 2000-atom/1000-step pilot reported
`0.837087 s` engine time and `2.38924e6 atom*step/s`. This is a single baseline
pilot, not a candidate-versus-baseline speedup result.

The first preflight (`1271011`) failed because the minimal rootfs has no usable
`awk` alternative; the environment now parses native GCC flags using Bash.
At that point, new package compilation, GNEP/PLUMED/DeepMD acceptance,
copied-prefix runtime and three-target performance were still unverified.
The October 23 release/master evidence above now covers those checks.

## 2026-10-15 gate audit

The local GPUMD suite checks all three GPU partitions, source/recipe binding,
complete native payload inventories, launcher checksum invalidation, raw
scientific-output tampering, GNEP force derivatives, inherited-output removal,
calibrated alternating performance trials and the publication slowdown boundary.
These fixture tests establish gate behavior; no new cluster scientific or
performance result was asserted by this audit. The initial development canary
had to pass on 16V100 before expansion to 4V100 and 8V100V0. All three targets
have since passed through the shared delivery workflow as recorded above.
