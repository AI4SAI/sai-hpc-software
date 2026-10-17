# Workflow closure — 2026-10-17

The code now connects build, scientific/performance acceptance, native export
readback and publication. GPUMD on 16V100 has passed science, performance and
publication; its additional exported-native module execution check remains
pending. Production acceptance is incomplete across the full site matrix. Original dirty
worktrees and recovery checkpoints remain preserved. Administrator installation
into physical `/opt/apps` is outside this task.

## Implemented and locally verified

- Daily and child workflows await reusable jobs and reject unexpected skipped,
  failed or cancelled deliveries. A recorded unchanged release is an intentional
  skip; development must produce a fresh run on every trigger. Explicit retry
  inputs propagate through reusable workflows. Schema-1 history is retained.
- Long jobs are separated into build and acceptance phases. CP2K additionally
  has a dependency phase. CPU and GPU compilation can run independently; CPU
  acceptance waits for a verified GPU reference. Handoffs validate workflow/source/track/target and the original
  immutable controller identity, without uploading or claiming a second build.
- Re-entering CP2K application or any software's acceptance stage validates and
  resumes the existing Slurm handle. Completed MD evidence is reverified without
  rewriting receipts pinned by a performance repeat. An ambiguous submission
  with no recorded job handle fails closed. This does not automatically recover
  the initial dependency/build phase across new workflow attempts; unchanged
  release attempts still require explicit retry authorization.
- ABACUS supports the upstream JSON option change and renamed HSE fixture,
  validates packaged scientific inputs before compiling, and includes all
  required native library paths.
- CP2K dependencies are built on the target partition, sealed with exact
  identity, job/script, graph, installation receipt and overlay hashes, then
  cloned and checked for application compilation. The original overlay remains
  intact. GPU dependency wall time is capped at the observed 240-minute QOS;
  historical 158–167 minute successes are references, not proof for this recipe.
  The required wignernj archive is pinned and checked. All four partition source
  caches passed read-only verification on SAI.
- MD connects two-node source-bound science, TF/PT/JAX raw performance checks,
  one repeat after a slowdown greater than 10%, paired export readback and
  immutable publication/module generation. PT requires actual Nsight CUDA
  kernel evidence. TF/JAX follow the established CPU-only baseline scope.
  Runtime checks bind actual SIF bytes to the paired build identity.
- GPUMD adds frozen-model GNEP numerical forces, stale-output rejection,
  calibrated engine intervals and alternating baseline/candidate trials.
  Scientific and performance verdicts are recomputed from retained raw files.
- Publication requires all scientific gates and blocks a repeated performance
  regression greater than 10%. Repeat records bind original evidence, artifact,
  baseline, inputs and resources; original logs remain available.
- Every accepted single or paired SIF must export its exact prefixes and pass
  independent readback of all bytes, permissions, links, identities and native
  modules before publication. Cache validation rechecks the exported payload.
  Schema-1 `/opt/software` exports are never relabelled as schema-2 `/opt/apps`.
- Per the user's October 17 instruction, shared disk-growth reservations and
  submission/allocation free-space gates are removed. Filesystem limits are
  enforced by actual writes, with failures retained. Builds, caches, overlays
  and temporary files stay in home; host `/tmp` is not used for compilation.
  CP2K keeps target/recipe-scoped Slurm singletons and exclusive ownership of each
  run; other software retains its shared singleton.
  Repacking copies and checksum-checks a checkpoint instead
  of moving the original. Monitors preserve jobs when observation expires.
- Controller staging batches the trusted files into one SFTP connection before
  fingerprinting/submission. Each run still has its own immutable controller;
  application/acceptance handoffs do not reupload it.

## Verified on SAI

Evidence: [workflow-closure-20261015.json](evidence/workflow-closure-20261015.json) and
[GPUMD 16V100 proof](evidence/gpumd-16v100-closure-20261015.json).

- Native export tool fixture: real SIF roundtrip passed for 17 software/partition
  combinations and 102 retained files. This is explicitly a synthetic fixture,
  not scientific software acceptance; no container code or system `/opt` write
  was involved.
- Real Lmod probe job `1790564`: `COMPLETED`, exit `0:0`, 12 seconds. Module load,
  unload, partition selection and subsequent image/launcher switch all passed.
- Initial read-only capacity/reference audit: about 16.05 GiB actually available. Of 17
  proposed old failed routine overlays, one was found referenced by CP2K recovery
  records and protected. The remaining 16 could reclaim 141.92 GiB, subject to
  explicit user approval and immediate file/job revalidation. The latest complete
  audit at 23:25 Asia/Shanghai scanned 17,782 metadata/script files without read
  errors and revalidated all 16 unchanged failed overlays with no new references.
  Exact review list: `/tmp/sai-overlay-cleanup-ready-20261015.md`. Nothing deleted.
- GPUMD 16V100 build `1790933` completed `0:0` in 20:07; acceptance `1792098`
  completed `0:0` in 2:33. All scientific/feature checks and the actual launcher
  passed. Median MD throughput was 2,396,010 versus baseline 2,408,080 atom-steps/s
  (about 0.50% slower), within the 10% gate; no repeat was needed. The exact SIF
  was exported, read back and published. A subsequent read-only verification of
  the published artifact, raw proofs, export bytes and module records passed.
- Additional module job `1803862` ran the published SIF module successfully:
  energy and forces matched its gold fixture exactly. The next exported-native
  step failed because unloading the SIF module also unloaded Apptainer. This is
  a probe sequencing defect; the corrected local probe reloads Apptainer and
  retains the failed attempt. Exported-native execution/JIT is still unproven.
- At 23:02 Asia/Shanghai, project capacity was **0 available bytes**. The normal
  13 GiB GPUMD submission check rejected 4V100 before any upload or allocation.
  Legacy branch job `1798309` subsequently timed out; it is not a closure canary
  and its overlay/logs are retained. Its old workflow can still start subsequent
  stages. This is historical evidence of the old capacity policy, superseded
  by the user's October 17 instruction to run without estimated space gates.

## Remaining acceptance work

Latest follow-up, 21:16 Asia/Shanghai on October 17: 4V100's dependency image
is sealed and application-stage job `1831311` is RUNNING on `4v100n05`
(started 21:09:11), still in image preparation. The other three new dependency
jobs remain RUNNING and no new SIF exists.
[Handoff and seal evidence](evidence/cp2k-production-handoff-20261017.json).

At 21:05 Asia/Shanghai on October 17, private-driver 4V100
dependency job `1830994` completed `0:0` in 37:19. 16V100 `1830993`,
8V100V0 `1830995` and DSPRHBM `1830997` remained in Libint compilation;
HDF5/Cython/PLUMED were complete on all four. The 4V100 seal and all new
application SIFs were absent at the snapshot. The historical DSPRHBM dependency
job `1828946` completed in 3:55:16 and handed off to application job `1831223`,
which was preparing its original image. [Production follow-up and CPU samples](evidence/cp2k-production-followup-20261017.json).

[Review of pinned CP2K/DBCSR sources and older installations](CP2K-V100-TUNING.md)
found that SpLA was installed as a dependency but not enabled in the CP2K
application. Commit `6d9b709` restores the old 2026.1 configuration and checks
the application CMake cache. Its 16 contract tests and 710-test CI passed
(708 passed, two optional skips). Running frozen controllers retain the
configuration difference. Actual SpLA calls also require GEMM offloading,
which the installed baseline disables, so this is not established loss of a
baseline runtime capability. Existing builds and acceptance continue; real
build validation of the corrected configuration awaits a fresh recipe. The
review also records runtime tuning priorities and the recommendation for an
optional shared `/opt/devtools` image driver; no system installation occurred.

The October 17 CPU-efficiency investigation found whole-image `fsync` under
the `fuse2fs 1.47.3` global lock, despite correct `-j8`/`-j12` settings and no
CPU throttling. A pinned upstream fix plus an Apptainer/Lustre OFD-lock adapter
passed isolated DSPRHBM `1830438` and 16V100 `1830468` probes. Two-trial median
32-file compilation improved 27.89x and 20.20x; explicit synchronization,
filesystem checks, 161-file clean-remount verification, and competing-writer
rejection passed. The pinned driver is now part of the CP2K build recipe and
CI deployment contract; existing jobs retain their original drivers/controllers.
Fresh four-target release workflow
[39032306213](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39032306213)
passed validation and submitted dependency jobs `1830993` (16V100), `1830994`
(4V100), `1830995` (8V100V0), and `1830997` (DSPRHBM). All four were RUNNING
at 20:28:46. At 20:30:38, actual 16V100 compilation used 7.144/8 logical CPUs
(89.30%) over ten seconds, with the private driver observed in `/proc`; the
old same-node job used 0.492/8 CPUs (6.15%) over the concurrent sample.
The stages differ, so this is utilization evidence rather than whole-build
speedup or CP2K application acceptance. CI passed 709 tests (707 plus two
optional skips); 20 focused tests passed on SAI. Next: dependencies must seal,
then the existing workflow compiles CP2K, verifies its final SIF, runs science
and performance, exports the native payload and publishes only after those gates.
At 20:36:23 all four new jobs were still RUNNING, each with GCC/G++/GFortran
14.3.0 and the pinned driver recorded. All four HDF5 builds had completed in
1:50–3:27; 16V100 and 4V100 had reached PLUMED compilation, with Libint still
building. No dependency seal or application artifact existed yet.
[Production execution evidence](evidence/cp2k-private-driver-production-20261017.json).
[Efficiency report, patch and retained evidence](BUILD-IO-EFFICIENCY.md).

Earlier official `v2026.2` four-partition attempt:
workflow [39018993951](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39018993951).
At 18:06 Asia/Shanghai, 4V100 `1828944` and DSPRHBM `1828946` were compiling
dependencies. 16V100 `1828943` was cancelled at 18:05:44 after an operator-added
read-only `srun` audit requested one CPU and triggered SAI's eight-logical-CPUs
per GPU rule. This was an agent operational error, not a CP2K compiler result.
The failed audit's output, old overlay and build log remain preserved. The
replacement uses the normal native dependency allocation; do not add a step
with a CPU/GPU request that violates the site's rule.

The home-storage audit observed `TMPDIR=/workspace/tmp`, a FUSE overlay root,
and an open `work.ext3` under `~/sai-hpc-software/runs/`; there was no separate
host `/tmp` or `/var/tmp` mount. The current controller removes both free-space
gates and reservation waiting, while retaining run locks and real write errors.
Its complete SAI suite passed 703 tests (701 passed, two optional MD-cache skips),
with test temporary files also in home. Running old controllers are not rewritten.

Fresh retry workflow [39023790564](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39023790564)
uses controller `975f8f8` for release `v2026.2` on 16V100 and 8V100V0.
At 18:15, actual jobs `1829656` (16V100, `16v100n04`, start 18:13:53) and
`1829644` (8V100V0, `8v100v0n09`, start 18:13:01, two GPUs/twelve CPUs) were
RUNNING and recorded `capacity_policy=filesystem-enforced`. 16V100 was
concretizing dependencies after its GCC 14 profile/bootstrap; 8V100V0 was
preparing the overlay/container. Neither is an application-build success.
[Home-storage policy and audit evidence](evidence/cp2k-home-storage-policy-20261017.json).

Earlier, at 17:51 Asia/Shanghai on October 17, new 16V100 dependency job `1828943`
was RUNNING for 51:44 on `16v100n37` (start 16:59:26), with its disk reservation
and all three GCC 14.3.0 compiler observations. DSPRHBM `1828946` was also
RUNNING for 51:19 with those checks. Both were compiling dependencies, not
the CP2K application. 8V100V0 `1828945` failed `1:0` at 17:29:59 after waiting
30 minutes for disk reservation; compilation never started. 4V100 `1828944`
was allocated at 17:45:59 but was also waiting for disk reservation. The initial Actions
staging delay was per-file SSH overhead, not a Slurm resource wait. Subsequent
controller uploads are batched into one connection.

The prior GCC 14 attempt completed dependency stages on 16V100, 4V100 and
DSPRHBM; 8V100V0 hit its four-hour limit in Libint. All three application jobs
then failed on a false HDF5 check (`ON` versus the expected `yes`). A separate
CMake default also selected GCC 13 `f95/cc` despite the loaded GCC 14 module.
The new recipe checks parallel HDF5 before extra compilation and explicitly
pins C/C++/Fortran and MPI wrapper compilers. Short native probes `1828866`
and `1828868` passed HDF5 checks and real GCC 14 CMake compilation/execution
on 16V100 and DSPRHBM. 4V100 probe `1828867` failed before compiler execution
because Apptainer's `fuse2fs` overlay mount timed out.
The corrected suite ran 702 tests on SAI: 700 passed, two optional MD source
cache checks skipped. The upload change additionally passed 32 lifecycle and
recovery tests. Old overlays/controllers remain preserved under their original
identities; recipe changes require fresh prefixes. These are dependency-stage
and small compiler-probe results, not CP2K application or scientific acceptance.
The subsequent batch-upload revision also passed GitHub's 703-test suite
(701 passed, two optional MD cache checks skipped).
The historical development SIF
was rechecked on 16V100 by `1814450`: checksum, version and `ldd` passed;
scientific acceptance/publication remain unproven. Space recovered to about
109 GiB at release submission and was about 72 GiB at this audit. Live budgets
include the two compiling dependency overlays and old development job `1828523`;
after their remaining growth, 4V100 was roughly 50 MiB short of its 16 GiB
overlay plus the old 4 GiB safety reserve. This historical estimate no longer
gates new jobs. 8V100V0's failed dependency stage skipped application/acceptance;
there is still no successful release application SIF.
[Historical loader/source evidence](evidence/cp2k-release-priority-20261016.json).
[Historical parallel-build evidence](evidence/cp2k-gcc14-parallel-20261017.json).
[Current retry and diagnostic evidence](evidence/cp2k-cmake-hdf5-retry-20261017.json).

| Software | DSPRHBM | 16V100 | 4V100 | 8V100V0 |
|---|---|---|---|---|
| ABACUS | Pending | Pending | Pending | Pending |
| CP2K | GCC 14 dependencies: 1828946 RUNNING | 1829656 RUNNING; dependency configuration | GCC 14 dependencies: 1828944 RUNNING | 1829644 RUNNING; overlay/container preparation |
| DeePMD-kit | Not a GPU target | Pending | Pending | Pending |
| LAMMPS | Not a GPU target | Pending | Pending | Pending |
| GPUMD | Not a GPU target | Published; native module execution pending | Capacity blocked | Pending |

1. The final MD repeat/recovery audit and integrated suite passed. Continue to keep
   controller upload/fingerprint and workflow inputs aligned when diagnosing
   canary failures.
2. Keep all canaries, caches and temporary files in home while preserving
   logs, artifacts and recovery checkpoints. Run without software disk
   reservations or estimated free-space gates, per the user's instruction.
   Diagnose actual write/quota errors if they occur; do not move builds to
   host `/tmp` or delete historical checkpoints automatically.
3. GPUMD job `1790711` compiled and linked `gpumd`, `nep` and `gnep`, but
   failed packaging because current upstream replaced `LICENCE` with `LICENSES/`.
   The license layout is fixed and preflighted before compilation. Fresh job
   `1790933` completed from snapshot `b9cbf83` with exit `0:0` in 20:07 and
   passed artifact verification; science/performance job `1792098` passed
   against that exact SIF, which is now published. The failed overlay/log remain
   preserved. Its 3 GiB overlay is based on measured prior usage of 340 MiB plus
   conservative export/JIT headroom, and still passed the normal 13 GiB free-space
   gate under the historical policy. Rerun the corrected exported-native module
   probe in a new task, then expand to 4V100 and 8V100V0 with that exact recipe.
4. Current priority (2026-10-16): run official CP2K `v2026.2` release canaries
   on 16V100, 4V100 and 8V100V0, followed by DSPRHBM with its GPU scientific
   reference. Explicitly retry the previously attempted release; retain the
   development channel and its remaining acceptance work. The working checkout
   is now `~/sai-hpc-software/development/workflow-closure`. Record source,
   recipe/SIF hashes, Slurm jobs, raw science/performance and any repeat.
5. Validate each real delivery's export, manifests and module invocation, in
   addition to the synthetic tool probes above. Publish only after every gate
   passes. Regressions or failed builds require diagnosis; a passing local test
   or historical result is insufficient.
6. Review the integrated branch/PR and operational workflow evidence. Do not
   describe the matrix as closed until all 17 applicable entries are proven.

ABACUS and CP2K source/dependency preflights are ready. MD's pinned DeepMD
`025f4f698d4ff50c0b5538f19dc0b00ad1459ba3` and LAMMPS
`1b8520aa9c049778e329114ea9d9f51a06dee153` are complete in the remote cache;
dependencies and four `sbatch --test-only` requests passed. These are not real
builds. MD's local plan was regenerated for `6ca236f` in
`/tmp/sai-md-canary-20261015-current`; the original preflight remains preserved.
Any further controller edit requires a fresh recipe/plan before upload.
Queue start estimates from `--test-only` are advisory, not evidence that a
submitted job is blocked or a reason to restart one.

## Local validation

The latest full integration checkpoint ran **692 unittest tests, all passed,
with no skips**, using the preserved upstream cache read-only. Actionlint 1.7.12 validates every
workflow, including reusable phase and final-result jobs. The four PR validation
workflows passed on `b5b1391`; remote builds are intentionally not selected by PR
events. Shell syntax checks and
Python compilation also passed. These checks do not replace the pending real
scientific/performance matrix.
