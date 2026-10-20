# LAMMPS stable release delivery

## Status audited on 2026-10-20

The latest upstream stable release is `stable_30Sep2026`, commit
`8de817dd79bfe4525d5d39246a212d833e6dee07`. The paired stable DeePMD-kit is
`v3.2.0`, commit `8cfd46e37448feca361ff3427a6c2b514ce5a2bb`.
No published delivery of this release exists yet.
Both 4V100 and 16V100 release SIFs pass complete build verification, including
native inventory and permissions. Each also passed candidate-only two-node
numerical checks against the locked upstream reference, across TF/PT/JAX and
Python/LAMMPS. Both also passed paired system-baseline science. PyTorch GPU
comparisons completed on both partitions with actual CUDA kernel traces and
no observed regression. The user has scoped delivery to GPU functionality,
feature/option parity and numerical correctness; CPU performance comparisons
are no longer a delivery gate. Publication under this explicit scope remains
pending. Baseline plugin lookup, warmup timing and Nsight's literal temporary
directory have been repaired. 8V100V0 completed compilation, parity and
SquashFS packaging; read-only extraction is recovering from a mount timeout.
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
3. Run the larger-system PyTorch CUDA check with explicit `--scope gpu`.
   Retain TF/PT/JAX numerical correctness checks. Publish only when the trusted
   controller validates the raw results; record `performance_scope=gpu` and
   `performance_backends=["pt"]` in the delivery manifest.
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
| 16V100 | [39403084059](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39403084059) | `1877774`, completed in 14:01 | `1877775` failed before entry; retry `1879155` compiled/installed on `16v100n19`, then reached the known metadata failure in 51:21 |
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

The [follow-up helper](evidence/lammps-release-20261020/follow_export.py)
runs from the cluster home directory. It monitors the export, then calls
the existing frozen controller's two-node scientific, performance and
publication gates. A process lock prevents duplicate chains, per-command
receipts record failures, and submission uses the same short-task QoS. No
acceptance threshold is relaxed. Publication here refers to the home project;
system `/opt` installation still requires the separate native handoff.

The native metadata fix's GitHub validation ran 846 tests: 844 passed and two
optional upstream-cache tests skipped. This is controller regression evidence,
not a substitute for the pending SIF and scientific checks.

### Preserve real permissions across fakeroot sessions

Recovery job `1879413` produced `candidate.sif` (348,381,184 bytes) in 3:37.
DeePMD version, `lmp -h` and all 29 runtime-path checks passed. Final native
manifest validation rejected mode `0644` where `0444` was required. A fresh
read-only SIF comparison found six metadata payload files and the two
manifests affected; every payload byte, symlink and file-set entry matched.
[The complete difference](evidence/lammps-release-20261020/4v-packaged-mode-diff.json)
is retained. This failed candidate and its logs remain preserved.

The site's fakeroot keeps owner-write on backing files and represents a
read-only chmod in its process-local state. The later export process loses
that state. `md_packaging.py` verifies the entire copied installation against
the embedded inventory, permits only this extra owner-write bit, then
materializes the declared modes without `LD_PRELOAD`. Squashfs packaging also
runs without the preload. Content changes, additional files and unexpected
permission changes still fail; manifests and compiled files are not rewritten.

[The packaging recovery](evidence/lammps-release-20261020/repack_permissions.py)
reuses the existing export tree, archives the failed job records, and writes
new Squashfs/SIF filenames. It does not recompile or overwrite the failed SIF.
The 16V100 metadata recovery includes the same packaging fix from the start.
[Its clone preparation](evidence/lammps-release-20261020/prepare_export_clone.py)
requires terminal accounting and the exact post-install failure before
sealing the compiled source and creating a separately locked copy.

[The native-filesystem regression run](evidence/lammps-release-20261020/md-packaging-tests.log)
ran 219 MD tests: 217 passed and two optional upstream-cache tests skipped.
The new test checks real file permissions through a Squashfs round trip and
rejects changed content, added files and changed identities before chmod.

## First verified release SIF

4V100 recovery job `1879803` completed `0:0` in 2:10 on `4v100n27`.
It verified 3,865 payload entries and both manifests, restoring the six
payload metadata modes and both manifest modes without changing file bytes.
Final-SIF DeePMD version, LAMMPS help, all 29 runtime-path checks and the
entire native inventory passed. [The build receipt](evidence/lammps-release-20261020/4v-built-sif.json)
records `build_verified: true` and the full artifact path; [the recovery receipt](evidence/lammps-release-20261020/4v-permission-recovery.json)
binds the retained failed SIF, helper and replacement job hashes.

- Run: `md-39403117741-1-2026-10-20-9bf6ae82814537d7-export`.
- SIF size: 348,381,184 bytes.
- SHA256: `86a575fc46daf8426b9b7b1de2b93c69619c4219f14b8eaefd886ae94007acf2`.
- Locked sources: LAMMPS `stable_30Sep2026`, DeePMD `v3.2.0`.
- Compiled recipe remains `296ed3baa11b`; this is the recorded metadata and
  permissions recovery, not a rebuild under a new identity.

The automatic continuation submitted two-node/four-rank scientific job
`1879995`; its startup failure and subsequent recovery are recorded below.
No scientific, performance or physical `/opt` acceptance is claimed by the
build receipt.
The packaging fix's full GitHub suite ran 855 tests: 853 passed and two
optional upstream-cache tests skipped.

## Both release artifacts execute correctly against upstream references

16V100 export job `1880105` completed `0:0` in 2:31 on `16v100n12`.
[Its build receipt](evidence/lammps-release-20261020/16v-built-sif.json) records
the full SIF path, 347,705,344 bytes, and SHA256
`04c8b3c209000dc8241847d6b45d4b24550b6beeec540981d324eceeabe9522c`.
Both artifacts retain compiled recipe `296ed3baa11b` and the same locked
stable sources. Final-SIF version, loader paths and native manifests pass.

The [candidate oracle helper](evidence/lammps-release-20261020/candidate_oracle.py)
ran the existing scientific inputs on each new artifact while baseline startup
was being repaired. It verifies energy, forces and virial against the locked
upstream source; reparses LAMMPS stdout/dumps and PLUMED COLVAR; checks all
MPI rank traces, actual executable/image paths and dependency ISA; and retains
raw file hashes. It does not substitute for paired acceptance or performance.

| Partition | Job | Nodes | Elapsed | Source-oracle result |
| --- | --- | --- | --- | --- |
| 16V100 | `1880588` | `16v100n[65-66]` | 1:20 | [24 records passed](evidence/lammps-release-20261020/16v-candidate-source-oracle.json) |
| 4V100 | `1881087` | `4v100n[01,03]` | 0:54 | [24 records passed](evidence/lammps-release-20261020/4v-candidate-source-oracle.json) |

Each job used four MPI ranks across two nodes and one GPU per node. Each of
TF/PT/JAX through Python/LAMMPS ran one warmup plus three trials. The diagnostics
and their read-only revalidation passed. TF/JAX use the site's CPU backends;
PT supplies CUDA inference. These small scientific cases do not establish
large-system throughput, GPU performance or physical native installation.

## Resume paired acceptance without changing the compiled identity

The first 4V100 science job `1879995` lacked HOME under Slurm `--export=NIL`,
so host Open MPI failed before calculation. The renderer now restores the
account home explicitly. Two subsequent retries (`1880186`, `1880251`) were
cancelled by Slurm before execution, without a stated reason. An explicit
minimal submission environment with `SBATCH_EXPORT=ALL` allowed job `1880390`
to enter the 16V100 paired tests. Keep all of these original records.
16V100 uses permitted `flood-1o2gpu`; 4V100 uses `rush-1o2gpu`.

Job `1880390` passed baseline TensorFlow Python inference, then the installed
July LAMMPS failed to find `libdeepmd_backend_tf.so`. The baseline's top-level
`lib` lacks that plugin; all backend plugins are under its Python wheel's
`site-packages/deepmd/lib`. The runtime now sets `DP_BACKEND_PLUGIN_PATH` and
adds that directory to the baseline loader search. It also restores the
requested OpenMP thread count after the system module forces it to two.

For these already compiled SIFs, `md_acceptance_controller.py submit
--recover-controller REASON` explicitly records separate build and acceptance
controller paths and recipe hashes, plus the original build-request hash.
Both snapshots, build scripts, image and delivery are revalidated. The binding
must remain identical through science, performance and publication. Without
this explicit recovery, differing recipes are rejected as before. Published
launchers pin the accepted runtime snapshot while retaining the original
compiled recipe and installation identity. Neither frozen controller nor SIF
is edited. The [recovery follower](evidence/lammps-release-20261020/resume_acceptance.py)
uses a new scientific task and retains failed original tasks; it keeps all
existing numeric and performance thresholds and the single permitted retest.

The [228-test MD run](evidence/lammps-release-20261020/md-acceptance-recovery-tests.log)
passed 226 tests with two optional upstream-cache skips. An initial run with
umask 077 produced private fixture directories and failed existing native
permission checks; the preserved cluster log records that setup error. The
successful run used normal 022 test-fixture permissions. Recovery tests reject
changed source/build/runtime bindings and mismatched performance evidence;
the baseline startup test executes the actual shell command.

### Paired science passed; repair performance warmup parsing

The pinned acceptance controller `0ab58dd`, recipe `795dbd80e2a4`, completed
16V100 science job `1881404` in 2:34 on `16v100n[65-66]`. All 48 records
(baseline/candidate, TF/PT/JAX, Python/LAMMPS, warmup plus three trials) passed
numeric, PLUMED and MPI-distribution checks.
[Read-only raw revalidation](evidence/lammps-release-20261020/16v-paired-science-1881404.json)
also passed; the small-case elapsed times are not performance acceptance.
[The launch receipt](evidence/lammps-release-20261020/acceptance-restart-0ab58dd.json)
pins the original compiled recipe and separate acceptance snapshot.

Performance job `1881437` ran the installed baseline for 100 steps on the
2,058-atom input in 10.1153 engine seconds, but failed before freezing the
calibration. LAMMPS prints a `Loop time` even for `run 1 post no`: the preceding
one-step warmup took 0.108853 seconds. The parser had required exactly one
line. It now accepts the exact one-step warmup followed by one measured
interval, checks both atom/rank counts and finite positive timings, and
excludes the warmup from the reported time. Extra intervals, a different
warmup or inconsistent allocation still fail. Read-only revalidation of the
failed job's 2,058-atom energy, every force, virial and PLUMED data passed.

The [setup recovery](evidence/lammps-release-20261020/prepare_timing_retry.py)
requires that exact terminal parser failure, a successful process exit, no
frozen calibration and no comparison records. It archives the original task
under `recovery-attempts/1881437` with
[file hashes and the original controller binding](evidence/lammps-release-20261020/16v-performance-setup-recovery.json).
This preserves the measured comparison's one permitted regression retest.
Queued 4V100 science `1881402` was cancelled before execution so the next
snapshot can perform both science and performance consistently. Its frozen
task and scheduler record remain retained. The original 16V100 science is
also retained; the new snapshot must rerun science before performance.

The [230-test MD run](evidence/lammps-release-20261020/md-warmup-timing-tests.log)
passed 228 tests with two optional upstream-cache skips. GitHub's preceding
`0ab58dd` full suite passed 862 tests with two skips (864 total).

The 8V100V0 release build is
[workflow 39427644750](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39427644750),
using compiler/version/overlay/packaging fixes from `0ab58dd` and the same
locked stable pair. The partition allows both stage QoS values and allocates
six logical CPUs per GPU. This compilation remains valid; its frozen
acceptance snapshot predates the timing-parser repair and will need an
explicit recovery before performance acceptance.

### Both paired science gates passed; home-backed Nsight profiling

With snapshot `8f7d954` / recipe `6a03d0a37eda`, science jobs `1881610`
(16V100, 2:42) and `1881609` (4V100, 1:30) each passed all 48 paired records
and the full raw verifier. Performance jobs `1881635` and `1881749` then
completed the 2,058-atom TensorFlow comparisons before stopping at PyTorch's
separate Nsight baseline profile. TF engine medians were 12.8058 versus
12.85 seconds on 16V100 (baseline/candidate); 4V100's baseline/candidate
ratio was 1.0078195. Both are within 1% and below the 10% regression limit.
These partial measurements do not establish complete performance acceptance.

Nsight failed with `Cannot create CUDA config file`. A short `/runtime`
alias alone did not help. File tracing in diagnostic job `1882139` showed
literal `/tmp/nsys-RunShellCommand-*` and `/tmp/injection_config_*` writes
failing with `EROFS`. The runtime now binds the allocated job's home-backed
runtime directory to the container's `/tmp` **only for explicit profiling**.
Automatic host `/tmp` mounts remain disabled; no host temporary disk is used.

The [probe](evidence/lammps-release-20261020/probe_nsight_temp.py), job
`1882158`, completed in 15 seconds. Its
[raw CUDA trace receipt](evidence/lammps-release-20261020/nsight-home-tmp-probe.json)
records 1,505 GPU kernel events on a Tesla V100-SXM2-16GB. This was a baseline
profiler preflight; candidate profiling and complete performance still run
through the normal gate. Failed diagnostics `1882081` (missing initial loader
environment), `1882099` (short alias only) and `1882139` (file tracing) remain
retained under the cluster's runtime-tests directory.

[Explicit profiler setup recovery](evidence/lammps-release-20261020/prepare_nsight_retry.py)
revalidates completed TF raw evidence and rejects recovery if it already
exceeds the regression limit. It requires the exact terminal Nsight failure
before any PT comparison or JAX trial, then archives all earlier records,
including TF timings and frozen calibrations:
[16V100](evidence/lammps-release-20261020/16v-nsight-recovery.json) and
[4V100](evidence/lammps-release-20261020/4v-nsight-recovery.json).
The full new snapshot must rerun science and performance; the one allowed
regression retest is not consumed or bypassed by these incomplete setup runs.

[231 MD tests](evidence/lammps-release-20261020/md-nsight-runtime-tests.log)
passed with two optional upstream-cache skips, including actual shell
argument assembly proving that profiling's `/tmp` source is job home storage.
The preceding `8f7d954` GitHub suite ran 866 tests, with 864 passing and two
optional skips.

### 8V100V0 compilation continues under a home-directory follower

DeePMD job `1881545` completed in 17:46 on `8v100v0n02`; LAMMPS job
`1881546` failed before entry with the known fuse2fs mount timeout. The
[scoped recovery](evidence/lammps-release-20261020/8v-mount-retry.json)
preserves the original image and logs, validates terminal accounting and
unchanged controller/script hashes, repairs/checks the locked image, then
submits the same stage as `1882104`. It runs 24 compile processes on
`8v100v0n05`. Completed DeePMD is reused without changing its source or recipe.

GitHub workflow `39427644750` was intentionally cancelled after both Slurm
stages had been submitted, so its old timing parser cannot launch acceptance.
The [home-directory follower](evidence/lammps-release-20261020/follow_8v_build.py)
monitors the existing build and explicitly selects a newer, hash-pinned
acceptance controller. Cancelling the hosted observer does not cancel or
replace the Slurm compilation. The original GitHub/controller records remain.

## GPU delivery scope requested by the user

The user clarified that delivery must compile correctly on GPU partitions,
preserve the previous release's functions/build options, and compute correct
results. Pure CPU LAMMPS timing is not a release requirement. New CI acceptance
therefore selects an explicit GPU performance scope. All TF/PT/JAX scientific
checks, two-node MPI checks, feature parity, loader checks and native export
validation remain required. The manifest records the scope; a GPU result is
never represented as an all-backend performance result. Historical requests
without a scope still require all three performance backends. A regression
retest cannot change scope or relax the existing single-repeat limit.

Full-performance jobs `1882230` (4V100) and `1882253` (16V100) completed TF and
PT measurements, then failed during the installed JAX baseline's first
calibration. That old harness hid GPUs with `CUDA_VISIBLE_DEVICES=""` to force
CPU inference; DeePMD's CUDA-enabled JAX C++ initialization then raised
`no CUDA-capable device is detected`. Ordinary GPU-visible JAX science had
already passed. The [diagnostic helper](evidence/lammps-release-20261020/probe_jax_device.py)
ran as `1882465`, completed successfully, and preserved its raw traces. Further
CPU benchmarking is outside the requested delivery scope.

Completed PT results from those attempts, on 2,058 atoms and three measured
pairs after warmup:

| Partition | Installed median | Candidate median | Baseline / candidate |
| --- | ---: | ---: | ---: |
| 4V100 | 5.77933 s | 5.77429 s | 1.00087 |
| 16V100 | 5.70660 s | 5.62264 s | 1.01493 |

Each uses an independent Nsight trace for baseline and candidate; timed
intervals are unprofiled. This synthetic fixture is not a claim about all
production simulations. The [scope transition helper](evidence/lammps-release-20261020/prepare_gpu_acceptance.py)
revalidates both completed raw comparisons and archives all failed-attempt
files before a new, explicitly scoped request. It never converts a failed
full comparison into a successful one.

The [GPU functionality check](evidence/lammps-release-20261020/gpu_functional_smoke.py)
additionally tests explicit GPU-package and Kokkos CUDA pair styles, including
Kokkos PPPM/CUFFT, on a deterministic neutral 256-atom LJ/Coulomb system. It
compares energy, pressure and every force component after five integration
steps to the installed reference. This is a correctness check, not a CPU
throughput benchmark. The 233-test MD controller suite passed with two optional
cache-dependent skips, including scope identity and script-provenance checks.

### GPU package, Kokkos and PPPM results

The [4V100](evidence/lammps-release-20261020/4v-gpu-functional.json) and
[16V100](evidence/lammps-release-20261020/16v-gpu-functional.json) raw rechecks
pass all 12 cases per partition. They exercise explicit `lj/cut/gpu`,
`lj/cut/coul/long/gpu`, Kokkos CUDA pair styles and `pppm/kk` with double
precision cuFFT. All 768 force components and seven energy/pressure values
are checked for each case. Matched-precision force differences are at most
`5.78e-15`; the largest double-precision energy/pressure difference is
`4.78e-12` in reduced LJ units.

The initial Slurm jobs `1882690` and `1882701` finished every calculation with
exit 0 but their final comparison exited 1: the baseline GPU package itself
uses mixed precision, with forces about `4.16e-6` from the CPU double reference.
The initial generic cross-precision force tolerance was too tight for that
installed baseline. The revised raw verifier retains the original tolerance,
compares the mixed-precision GPU path to the installed GPU path, and compares
double-precision paths to the installed CPU reference. It also checks the
reported precision/acceleration and records every difference from the CPU
reference. The old failed Slurm verdicts and frozen helpers are preserved;
the linked results are separate raw revalidation, not rewritten job statuses.

### 8V100V0 final artifact extraction

Job `1882104` completed compilation, 71-package/2,473-style parity, all backend
fixture preparation and packaged-mode verification. It built a 331.92 MiB
SquashFS before a third overlay mount timed out while copying that finished
file to the host. No final SIF had been constructed yet.

[Recovery job `1882907`](evidence/lammps-release-20261020/8v-extraction-recovery.json)
uses the [scoped extraction helper](evidence/lammps-release-20261020/recover_8v_squashfs.py).
It locks the existing image, opens it read-only, hashes it, and uses read-only
`debugfs` to dump the already completed SquashFS inode. It checks the inode
size and SquashFS magic, then runs the original SIF construction and frozen
final verifier. It does not rebuild, repack, repair or write the source overlay.
The original request, script, failure log and empty host extraction are kept
under `recovery-attempts/1882104`. The new extraction script/handle and unchanged
compiled recipe are explicitly recorded in the current request.

The [resumed observer](evidence/lammps-release-20261020/8v-extraction-observer.json)
continues with the same immutable GPU-scope acceptance snapshot as 4V100 and
16V100: recipe `345e160f793a5c2c724a13ccf52191e0f5034a5fee637238a2f42a9467d9eb6e`.
No physical `/opt` installation is implied by these container checks.
