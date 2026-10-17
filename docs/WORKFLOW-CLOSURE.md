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
- Submission and allocation-start capacity checks remain active. CP2K uses
  target-scoped Slurm singletons and shared disk-growth reservations to allow
  parallel builds; other software retains its shared singleton.
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
  stages, so capacity must be checked again immediately before any submission.

## Remaining acceptance work

Current CP2K priority: official `v2026.2` release across all four partitions,
workflow [39018993951](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39018993951).
At 17:06 Asia/Shanghai on October 17, new 16V100 dependency job `1828943`
was RUNNING on `16v100n37` (start 16:59:26), with its disk reservation and all
three GCC 14.3.0 compiler observations. DSPRHBM was also RUNNING with those
checks; 8V100V0 had allocated twelve CPUs/two GPUs but was waiting for disk
reservation; 4V100 was PENDING for scheduler priority. The initial Actions
staging delay was per-file SSH overhead, not a Slurm resource wait. Subsequent
controller uploads are batched into one connection.

The prior GCC 14 attempt completed dependency stages on 16V100, 4V100 and
DSPRHBM; 8V100V0 hit its four-hour limit in Libint. All three application jobs
then failed on a false HDF5 check (`ON` versus the expected `yes`). A separate
CMake default also selected GCC 13 `f95/cc` despite the loaded GCC 14 module.
The new recipe checks parallel HDF5 before extra compilation and explicitly
pins C/C++/Fortran and MPI wrapper compilers. Short native probes `1828866`
and `1828868` passed HDF5 checks and real GCC 14 CMake compilation/execution
on 16V100 and DSPRHBM. 4V100's corresponding probe remains pending.
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
109 GiB at release submission; later stages still recheck actual free capacity.
[Historical loader/source evidence](evidence/cp2k-release-priority-20261016.json).
[Historical parallel-build evidence](evidence/cp2k-gcc14-parallel-20261017.json).
[Current retry and diagnostic evidence](evidence/cp2k-cmake-hdf5-retry-20261017.json).

| Software | DSPRHBM | 16V100 | 4V100 | 8V100V0 |
|---|---|---|---|---|
| ABACUS | Pending | Pending | Pending | Pending |
| CP2K | GCC 14 dependencies: 1828946 RUNNING | GCC 14 dependencies: 1828943 RUNNING | Dependencies: 1828944 PENDING | 1828945 allocated; waiting for disk |
| DeePMD-kit | Not a GPU target | Pending | Pending | Pending |
| LAMMPS | Not a GPU target | Pending | Pending | Pending |
| GPUMD | Not a GPU target | Published; native module execution pending | Capacity blocked | Pending |

1. The final MD repeat/recovery audit and integrated suite passed. Continue to keep
   controller upload/fingerprint and workflow inputs aligned when diagnosing
   canary failures.
2. Obtain adequate space for all further canaries while preserving
   logs, artifacts and recovery checkpoints. A smaller GPUMD canary may proceed
   only if its actual capacity check passes after source staging. Read-only
   storage checks found no existing authorized alternative with 100 GiB headroom;
   `/org/home/stardust` shares the same project quota as `/home/stardust`.
3. GPUMD job `1790711` compiled and linked `gpumd`, `nep` and `gnep`, but
   failed packaging because current upstream replaced `LICENCE` with `LICENSES/`.
   The license layout is fixed and preflighted before compilation. Fresh job
   `1790933` completed from snapshot `b9cbf83` with exit `0:0` in 20:07 and
   passed artifact verification; science/performance job `1792098` passed
   against that exact SIF, which is now published. The failed overlay/log remain
   preserved. Its 3 GiB overlay is based on measured prior usage of 340 MiB plus
   conservative export/JIT headroom, and still passed the normal 13 GiB free-space
   gate. After capacity recovers, rerun the corrected exported-native module
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
