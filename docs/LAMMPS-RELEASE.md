# LAMMPS stable release delivery

## Status audited on 2026-10-20

The latest upstream stable release is `stable_30Sep2026`, commit
`8de817dd79bfe4525d5d39246a212d833e6dee07`. The paired stable DeePMD-kit is
`v3.2.0`, commit `8cfd46e37448feca361ff3427a6c2b514ce5a2bb`.
No accepted or published delivery of this release exists in this workflow.
There was no MD build in the user's Slurm queue at the audit.

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
`16v100n14`; LAMMPS job `1873709` was submitted with `afterok` and waits for
its four-GPU allocation. Both stages keep their frozen controller and overlay.
[The receipt](evidence/lammps-release-20261020/first-build-progress.json)
records actual source identities, accounting and compiler messages.

The live build exposed missing release tags in the commit-only source
transport: the correct DeePMD 3.2.0 source was installed with the fallback
Python version `0.1.dev1+g8cfd46e37`. This first canary is retained for compile
diagnostics. Its Actions workflow was intentionally cancelled to prevent its
automatic acceptance/publication chain; the already submitted Slurm build
jobs continue. No frozen recipe, old overlay or installation identity is
silently rewritten.

The corrected recipe restores a local lightweight tag only after checking
that the checkout equals the commit resolved from the upstream release tag.
It rejects conflicting tags, does not tag development snapshots, and checks
the actual installed DeePMD distribution version after wheel installation
and again in the final SIF. The source tree is unchanged. Build job names
include their run ID so Slurm's `singleton` does not serialize unrelated
partition builds. The corrected release starts on 4V100 while the original
16V100 diagnostic continues; each partition still compiles natively.

[The corrected MD regression run](evidence/lammps-release-20261020/md-tests-release-tags.log)
passed 214 tests with the two optional upstream-cache skips. A metadata-only
checkout of the actual locked DeePMD commit also confirmed that the restored
tag makes the site's `setuptools_scm` report exactly `3.2.0`:
[upstream version preflight](evidence/lammps-release-20261020/upstream-version-preflight.json).
