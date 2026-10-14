# Workflow closure implementation checkpoint — 2026-10-15

This branch is an integration checkpoint, not evidence that the production
software matrix is accepted. Existing worktrees and recovery checkpoints were
preserved before integration. No administrator installation is included.

## Implemented

- Daily orchestration calls local reusable workflows and checks their outcomes,
  rather than reporting success after dispatching independent runs.
- Software-specific selection covers DSPRHBM plus 16V100, 4V100 and 8V100V0 where
  supported. Development remains rebuildable; unchanged release attempts remain
  deduplicated unless explicitly retried. Schema-1 history remains readable.
- ABACUS upstream JSON configuration compatibility and CP2K's pinned wignernj
  source dependency are integrated with the preserved software work.
- ABACUS, CP2K and GPUMD publication performance checks allow one repeat on the
  same artifact after a regression greater than 10%; repeated regression blocks
  publication. CI collects both original and repeat evidence.
- Build submissions use a shared Slurm singleton name and capacity checks both
  before submission and at execution. Capacity requires three overlay sizes plus
  4 GiB. This is conservative, not a reservation against unrelated users.
- CP2K native Spack probes cover all four partitions. 8V100V0 uses the site's
  AVX2 MPI/BLAS modules despite the CPU target name containing avx512.
- MD runtime checks canonical image path, successful build record, paired delivery
  identity and actual SIF SHA256 before execution. CI requests two-node science
  acceptance. Performance measurements alternate baseline and candidate, with
  separate CUDA tracing excluded from timings.

## Remaining acceptance work

1. Connect MD raw performance evidence verification, repeat policy, validated
   native export, module generation and publication. MD remains candidate-only;
   passing its current science workflow does not imply a published delivery.
2. Complete CP2K dependency-stage lifecycle and prove it fits site queue limits.
3. Exercise automated native export roundtrips and module payloads for all five
   packages; audit uploaded controller dependencies and validate workflows with
   actionlint in addition to Python YAML tests.
4. Run real development canaries on 16V100 and DSPRHBM, then expand GPU acceptance
   to 4V100 and 8V100V0. Require scientific parity and performance evidence before
   publishing each delivery. No new Slurm builds were submitted at this checkpoint.
5. Obtain adequate site capacity without deleting recovery checkpoints or logs.
   The previous read-only inspection found about 17 GiB free and about 313 GiB of
   old failed overlays; this inventory is not authorization to delete them and
   must be revalidated against active jobs and resume references before cleanup.

## Local validation

The full unittest suite passed with 573 tests (2 skipped) after the partition,
MD image-binding and alternating measurement changes. This does not establish
real scientific correctness, GPU execution, performance or export usability on
SAI. The skipped tests and cluster acceptance must not be represented as passed.
