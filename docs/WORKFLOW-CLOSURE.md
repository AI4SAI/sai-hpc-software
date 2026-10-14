# Workflow closure — 2026-10-15

The code now connects build, scientific/performance acceptance, native export
readback and publication. Production acceptance is still incomplete: none of
the new software builds has yet passed the complete site matrix. Original dirty
worktrees and recovery checkpoints remain preserved. Administrator installation
into physical `/opt/apps` is outside this task.

## Implemented and locally verified

- Daily and child workflows await reusable jobs and reject unexpected skipped,
  failed or cancelled deliveries. A recorded unchanged release is an intentional
  skip; development must produce a fresh run on every trigger. Explicit retry
  inputs propagate through reusable workflows. Schema-1 history is retained.
- Long jobs are separated into build and acceptance phases. CP2K additionally
  has a dependency phase, and its GPU deliveries finish before CPU references
  are selected. Handoffs validate workflow/source/track/target and the original
  immutable controller identity, without uploading or claiming a second build.
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
- Submission and allocation-start capacity checks plus a shared Slurm singleton
  serialize builds. Repacking copies and checksum-checks a checkpoint instead
  of moving the original. Monitors preserve jobs when observation expires.

## Verified on SAI

Evidence: [workflow-closure-20261015.json](evidence/workflow-closure-20261015.json).

- Native export tool fixture: real SIF roundtrip passed for 17 software/partition
  combinations and 102 retained files. This is explicitly a synthetic fixture,
  not scientific software acceptance; no container code or system `/opt` write
  was involved.
- Real Lmod probe job `1790564`: `COMPLETED`, exit `0:0`, 12 seconds. Module load,
  unload, partition selection and subsequent image/launcher switch all passed.
- Read-only capacity/reference audit: about 16.05 GiB actually available. Of 17
  proposed old failed routine overlays, one was found referenced by CP2K recovery
  records and protected. The remaining 16 could reclaim 141.92 GiB, subject to
  explicit user approval and immediate file/job revalidation. Nothing deleted.

## Remaining acceptance work

| Software | DSPRHBM | 16V100 | 4V100 | 8V100V0 |
|---|---|---|---|---|
| ABACUS | Pending | Pending | Pending | Pending |
| CP2K | Pending | Pending | Pending | Pending |
| DeePMD-kit | Not a GPU target | Pending | Pending | Pending |
| LAMMPS | Not a GPU target | Pending | Pending | Pending |
| GPUMD | Not a GPU target | 1790933: corrected build running | Pending | Pending |

1. The final MD repeat audit and integrated suite passed. Continue to keep
   controller upload/fingerprint and workflow inputs aligned when diagnosing
   canary failures.
2. Obtain adequate space for the normal ABACUS/CP2K/MD canaries while preserving
   logs, artifacts and recovery checkpoints. A smaller GPUMD canary may proceed
   only if its actual capacity check passes after source staging.
3. GPUMD job `1790711` compiled and linked `gpumd`, `nep` and `gnep`, but
   failed packaging because current upstream replaced `LICENCE` with `LICENSES/`.
   The license layout is fixed and preflighted before compilation. Fresh job
   `1790933` is rebuilding from snapshot `b9cbf83`; the failed overlay/log remain
   preserved. Its 3 GiB overlay is based on measured prior usage of 340 MiB plus
   conservative export/JIT headroom, and still passed the normal 13 GiB free-space
   gate. No scientific acceptance or published SIF is claimed.
4. Run complete development canaries on 16V100 and DSPRHBM, then GPU acceptance
   on 4V100 and 8V100V0. Record source/recipe/SIF hashes, Slurm jobs, raw scientific
   comparisons, performance and any repeat for each accepted delivery.
5. Validate each real delivery's export, manifests and module invocation, in
   addition to the synthetic tool probes above. Publish only after every gate
   passes. Regressions or failed builds require diagnosis; a passing local test
   or historical result is insufficient.
6. Review the integrated branch/PR and operational workflow evidence. Do not
   describe the matrix as closed until all 17 applicable entries are proven.

## Local validation

The latest full integration checkpoint ran **671 unittest tests, all passed,
with no skips**, using the preserved upstream cache read-only. Actionlint 1.7.12 validates every
workflow, including reusable phase and final-result jobs. The four PR validation
workflows passed on `03af2ab`; remote builds are intentionally not selected by PR
events. Shell syntax checks and
Python compilation also passed. These checks do not replace the pending real
scientific/performance matrix.
