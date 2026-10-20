# LAMMPS stable release delivery

## Status audited on 2026-10-20

The latest upstream stable release is `stable_30Sep2026`, commit
`8de817dd79bfe4525d5d39246a212d833e6dee07`. The paired stable DeePMD-kit is
`v3.2.0`, commit `8cfd46e37448feca361ff3427a6c2b514ce5a2bb`.
No accepted or published delivery of this release exists in this workflow.
The 4V100 payload has compiled and passed installed feature parity; its
metadata/export recovery is queued. The 16V100 payload is still compiling.
Both use the qualified CP2K overlay driver; see the live restart below.

The current implementation builds both programs in one Apptainer overlay,
then exports a SIF and runs separate scientific, performance and publication
stages. Its GPU targets are 16V100, 4V100 and 8V100V0; DSPRHBM is not an MD
target. Preserve the installed feature baseline: 70 LAMMPS packages, 2,197
styles, DeePMD TensorFlow/PyTorch/JAX backends, Kokkos CUDA, the GPU package,
and PLUMED 2.10.1. TensorFlow and JAX in the site's dependency environment are
CPU backends; PyTorch supplies CUDA/SM70 inference.

### Live scheduler and failures

The default branch's `daily.yml` still dispatches
`feat/deepmd-lammps-tracking` at `0f247b1`. It does not run the integrated
`fix/workflow-closure` implementation. The latest 20 dispatches, September 30
through October 19, all failed. Green push/PR validation runs execute static
tests, not native compilation or acceptance.

In [run 39218771804](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39218771804):

| Channel/stage | Observed result |
| --- | --- |
| release | Failed in the build-history claim before source transfer or Slurm submission. |
| development / DeePMD | Job `1856546` completed `0:0` in 2:56:57 on `16v100n35`. |
| development / LAMMPS | Job `1856547` failed `1:0` in 9:01 on `16v100n52`, during CUDA configuration. |
| acceptance | Skipped; no new SIF from this run. |

Read-only replay of history validation identified eight recovery requests with
the newer `/opt/apps` identity schema that the old daily controller rejects.
The integrated controller validates all 114 historical identities, including
those recovery requests. Keep the original records; do not delete history to
make the old code succeed.

The development failure is `CMAKE_CUDA_ARCHITECTURES=sm_70`: its newer GPU
package passes this value to CMake's native CUDA language, which requires `70`.
The pinned September 30 **release** still uses FindCUDA and accepts
`GPU_ARCH=sm_70`. Do not apply the development parameter change blindly to the
release. Development compatibility remains a follow-up.

### Historical output is not the latest release

The sole discovered MD SIF is `md-20260922-native-recovery-14.sif` on 16V100
(471,130,112 bytes; SHA256
`a4db8957072c4db679cd33637ed51e46679ce6b73e16f932740e3abf996bb4af`). It contains
LAMMPS development `c8bd2ae5927e` and DeePMD development `1313650a7ecd`.
Its build receipt records verification. The September 24 single-node receipt
records scientific verification, and a two-node run produced successful
six-backend/engine summaries. These do not establish the current release's
performance, native-installation or publication acceptance.

The system module remains
`lammps/4jul2026-deepmd320-plumed2101-nvhpc263-ompi5010-sm70`.
It is an installed baseline, not a delivery from the latest release workflow.

## Release restart

Use `fix/lammps-release-20261020`, based on integrated closure commit
`34b9bfccf361328f5b2254463a4be8a151d4f4f5`. The integrated implementation
already includes registration ordering, native driver paths, final `/opt/apps`
prefixes, raw scientific/performance verification and publication gates.

Pin GCC 14.3.0 explicitly for C, C++, Fortran, MPI wrappers, Kokkos's
`nvcc_wrapper`, and FindCUDA's host compiler. The previous module combination
selected `/usr/bin/gcc` 13.3.0; the explicit module was checked on the cluster.
Keep CUDA 12.9.1, Open MPI 5.0.10 and the existing ABI-matched backend/PLUMED
dependencies. The build still checks C++ ABI and SM70 backend support live.

1. Dispatch `deepmd-lammps.yml` with `software=lammps`, `tracks=release`,
   `targets=16v100-avx2`, `build_candidates=true`, `retry_releases=true`.
   Record exact sources, recipe, image checksum and Slurm handles.
2. Verify final-SIF `lmp -h`, loader resolution, full package/style parity,
   backend inference, PLUMED, and single/two-node MPI scientific results.
3. Run the existing larger-system performance gate. Publish only when its
   trusted controller validates the raw results.
4. Build and accept the same release independently on 4V100 and 8V100V0,
   then prepare the physical `/opt/apps` installation and partition selector
   for administrator execution. Container acceptance alone is not native
   installation acceptance.
5. Integrate the validated changes into the shared branch and update the
   production daily scheduler through the normal review path. Until then,
   the old daily branch remains a separate unresolved operational issue.

All cluster source caches, overlays, runtime files and artifacts stay below
`/home/stardust/sai-hpc-software`. Temporary container paths are backed by
the home-directory overlay; host `/tmp` is not used. No estimated disk reserve
blocks submission, and no old checkpoints are deleted.

## Preflight evidence

[GitHub audit](evidence/lammps-release-20261020/github-audit.json) and
[cluster audit](evidence/lammps-release-20261020/cluster-audit.json) preserve
the observed source identities, failed dispatches, history validation, Slurm
accounting, logs and historical artifact receipt. The
[compiler preflight](evidence/lammps-release-20261020/compiler-preflight.log)
confirms GCC 14.3.0 and the MPI/CUDA/Kokkos host-compiler selections.

The 210 MD regression tests were run in the cluster home directory: 208
applicable tests passed and two optional local upstream-cache tests skipped.
The first remote pass lacked one copied inventory fixture; that exact failing
test passed after the existing fixture was transferred. Both
[original log](evidence/lammps-release-20261020/md-tests.log) and
[fixture recheck](evidence/lammps-release-20261020/md-fixture-recheck.log) are
preserved. Bash syntax and patch whitespace checks passed. The Windows-mounted
local checkout cannot enforce `chmod 0755` (observed `0777`), so its two
permission-sensitive export failures were checked on the native filesystem,
without weakening the export policy. These are controller checks, not release
compilation or acceptance results.

## First live release canary and version fix

[Workflow 39384172116](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39384172116)
ran commit `c6611e6` and recipe
`2a57ec0f43aaee1f2a4d5d5b006a0d343728c6cd0afcd0c90c0535c0011bcfbe`.
GitHub validation passed 840 tests (two optional upstream-cache skips), then
resolved the intended stable pair. DeePMD job `1873708` completed on
`16v100n14`; LAMMPS job `1873709` later started on `16v100n74` and was stopped
after the I/O diagnosis below. Both stages keep their frozen controller and overlay.
[The receipt](evidence/lammps-release-20261020/first-build-progress.json)
records actual source identities, accounting and compiler messages.

The live build exposed missing release tags in the commit-only source
transport: the correct DeePMD 3.2.0 source was installed with the fallback
Python version `0.1.dev1+g8cfd46e37`. This first canary is retained for compile
diagnostics. Its Actions workflow was intentionally cancelled to prevent its
automatic acceptance/publication chain; its LAMMPS job initially continued
for diagnosis and was subsequently stopped. No frozen recipe or installation identity is
silently rewritten.

The corrected recipe restores a local lightweight tag only after checking
that the checkout equals the commit resolved from the upstream release tag.
It rejects conflicting tags, does not tag development snapshots, and checks
the actual installed DeePMD distribution version after wheel installation
and again in the final SIF. The source tree is unchanged. Build job names
include their run ID so Slurm's `singleton` does not serialize unrelated
partition builds. The intermediate 4V100 attempt was cancelled before it ran,
so the next attempts include both the version and I/O repairs.

[The corrected MD regression run](evidence/lammps-release-20261020/md-tests-release-tags.log)
passed 214 tests with the two optional upstream-cache skips. A metadata-only
checkout of the actual locked DeePMD commit also confirmed that the restored
tag makes the site's `setuptools_scm` report exactly `3.2.0`:
[upstream version preflight](evidence/lammps-release-20261020/upstream-version-preflight.json).

## Reuse the qualified overlay driver

The first 16V100 LAMMPS stage passed configuration and all 13 DeepMD registry
checks, then stalled around 14% with 24 compiler processes. Its job had 32
allowed logical CPUs, `cpu.max=max`, zero throttling and zero memory-pressure
or OOM events. Compiler processes waited in `request_wait_answer`; the bundled
`fuse2fs` driver had a thread in Lustre `osc_io_fsync_end`. This is the same
whole-image synchronization bottleneck already diagnosed and repaired for
CP2K; increasing build parallelism does not fix it.

New MD builds reuse the existing `cp2k_image_driver.py` wrapper and the exact
qualified driver SHA256
`46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10`.
A byte-identical, mode-0555 copy lives under the MD project's `cache/tools`.
Build, export and read-only extraction use it; final-SIF validation runs
independently without a writable overlay. The helper is included in the MD
recipe hash and immutable CI upload. This preserves explicit fsync, image
locking and genuine read-only mounts; no system driver is replaced.
[Driver qualification](evidence/fuse2fs-readonly-20261017/README.md) and
[live MD stall/driver evidence](evidence/lammps-release-20261020/io-stall-and-driver.json).

The MD runner also passes its exact requested commit to the existing source
cache inventory. Previously, the 12 most recent development tips could hide
an already-cached release, causing redundant full source uploads (observed
74,425,892 bytes for DeePMD). Cache completeness verification is unchanged.

The superseded 4V100 workflow `39398366074` and its still-pending jobs
`1876915`/`1876916` were cancelled before compilation. The inefficient 16V100
diagnostic `1873709` was also stopped; its completed DeePMD stage, logs,
overlays and controllers remain intact. Fresh builds use a new recipe and
installation identity, incorporating both version and driver corrections.

Validation: the [215-test MD suite](evidence/lammps-release-20261020/md-tests-image-driver.log)
passed with two optional upstream-cache skips, and all
[five shared-driver tests](evidence/lammps-release-20261020/driver-tests.log)
passed on the cluster's native home filesystem. The actual MD driver copy and
configuration passed the shared provenance check before submission:
[driver preflight](evidence/lammps-release-20261020/driver-preflight.json).

## Live restart with the qualified driver

Both new workflows use commit `9d1985ea5ba0030a5df162a93bff2f4496449d4a`
and recipe `296ed3baa11b0bb5629dfb60e7bf396083367a613719dfd8d21767082fa98097`:

| Target | Workflow | DeePMD stage | LAMMPS stage |
| --- | --- | --- | --- |
| 16V100 | [39403084059](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39403084059) | `1877774`, completed in 14:01 | `1877775` failed before entry; retry `1879155` is compiling on `16v100n19` |
| 4V100 | [39403117741](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39403117741) | `1877798`, completed in 7:42 | `1877799` compiled/installed, then failed during native metadata generation in 45:11 |

Both installed wheels report exactly DeePMD 3.2.0. Both checkouts preserve the
locked release tag/SHA, and live NVCC commands use GCC 14.3.0 and SM70.
The 4V100 runner reports cache hits for both exact source commits, with no
redundant source transfer. GitHub validation ran 845 tests, with 843 passing
and two optional upstream-cache skips.

SSH samples read the complete job cgroup, including short-lived compiler
processes. During the six-way DeePMD compilation, 16V100 used 5.710 logical
CPUs and 4V100 used 5.521. During the 24-way LAMMPS Kokkos compilation, 4V100
used 19.355 logical CPUs. All samples had unrestricted `cpu.max` and zero
CPU throttling. The actual running fuse2fs executable and its SHA256 match
the qualified driver. These short samples demonstrate parallel work; they
are not whole-build speedup measurements.
[Raw-derived CPU and driver records](evidence/lammps-release-20261020/driver-live-cpu.json)
retain the original sample paths and hashes;
[CI and cache excerpts](evidence/lammps-release-20261020/github-driver-validation.log)
record the full-suite result and the exact-commit cache hits.

Both DeePMD stages reported an Apptainer driver-unmount timeout. The 4V100
LAMMPS stage mounted successfully and proceeded through configuration and
compilation. The 16V100 stage failed during the first mount, before executing
the container entrypoint. Its retained image has stale free-block/inode
counters after the unmount timeout. The narrowly scoped
[retry tool](evidence/lammps-release-20261020/retry_mount_timeout.py) archives
the original image and failure records, exclusively locks the workspace,
repairs the filesystem and verifies it read-only, then submits the identical
LAMMPS script on hold. It records the new handle before release. It retains
the completed DeePMD stage, source/recipe identities and frozen controller.
This recovery does not establish final-SIF or scientific acceptance.
The [retry receipt](evidence/lammps-release-20261020/16v-mount-retry.json)
records the archived and repaired image hashes and new job `1879155`.
The completed predecessor had expired from Slurm's active job table, so
submission overrides its obsolete `afterok` with `singleton` after checking
the original `COMPLETED 0:0` accounting. The script bytes are unchanged.

## Compiled 4V100 payload and native metadata recovery

Job `1877799` completed LAMMPS compilation, linking, installation, feature
parity and TF/PT/JAX scientific-fixture preparation. Its installed LAMMPS
reports `20260930`, and DeePMD reports `3.2.0`. The
[installed inventory comparison](evidence/lammps-release-20261020/4v-compiled-parity.json)
passes with 71 packages and 2,473 styles, retaining the baseline's 70 packages
and 2,197 styles. The backend set and PLUMED kernel also pass parity.
These checks precede final-SIF runtime and scientific acceptance.

Native metadata generation then rejected the container's literal `/bin` and
`/sbin` runtime paths. These are real directories in the minimal rootfs, while
the previous test host resolved them through merged-/usr symlinks. Exported
PATH now uses `/usr/bin` and `/usr/sbin`. A regression validates the literal
paths without relying on host symlinks. Final read-only SIF verification also
omits fakeroot, whose injected `/.singularity.d/libs/libfakeroot.so` was visible
in build-time `ldd`, and uses a home-backed `/runtime` for temporary files.
Native-path allowlists and science/performance requirements stay enforced.

The [216-test native-filesystem run](evidence/lammps-release-20261020/md-native-fix-tests.log)
passes with two optional upstream-cache skips. Its initial staging attempt
lacked workflow/inventory fixtures and inherited a group-writable umask;
the complete rerun includes those fixtures and uses umask 022.

The [export recovery tool](evidence/lammps-release-20261020/finish_native_export.py)
uses the already compiled image's unchanged source and recipe identities.
The original image is read-only; a separately hashed clone receives only
filesystem preparation and runtime metadata fixes. It reuses the qualified
CP2K work-directory inspector, records hashes of compiled ELF files before
and after metadata changes, writes native manifests through the same shared
validator, and runs the frozen final-SIF verifier without fakeroot. No compiler
command is present in this recovery. New normal builds include the fixes in
their recipe; the current compiled payload is explicitly recorded as a
metadata recovery of recipe `296ed3baa11b`.

The 4V100 recovery is job `1879413`, run
`md-39403117741-1-2026-10-20-9bf6ae82814537d7-export`. It requests one GPU and
six worker processes for metadata/export/verification only. The queued job
was changed from `flood-1o2gpu` to the permitted `rush-1o2gpu` QoS, preserving
its script and compiled identity; `results/qos-update.json` records both
scheduler descriptions. The original compiled overlay remains read-only.

The [follow-up helper](evidence/lammps-release-20261020/follow_export.py) is
running from the cluster home directory. It monitors the export, then calls
the existing frozen controller's two-node scientific, performance and
publication gates. A process lock prevents duplicate chains, per-command
receipts record failures, and submission uses the same short-task QoS. No
acceptance threshold is relaxed. Publication here refers to the home project;
system `/opt` installation still requires the separate native handoff.

The native metadata fix's GitHub validation ran 846 tests: 844 passed and two
optional upstream-cache tests skipped. This is controller regression evidence,
not a substitute for the pending SIF and scientific checks.
