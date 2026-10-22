# CP2K ScaLAPACK redistribution source confirmation

The unintended half-rank ScaLAPACK solve is an upstream logic defect, present
in official **v2026.2** (`67b5da876dd6a76b8b021d5a04d1c81ba79a4c50`) and the
**master** revision checked on 2026-10-22
(`50c68efc33e91eaa6cc9adaf699862f528428a52`). The release's initialization,
cleanup and optimal-rank functions are identical to those in that master.
The cluster build's affected files match the official release byte for byte;
its only source edit is the separately recorded ELPA GPU setup repair.

The existing upstream [issue #6074](https://github.com/cp2k/cp2k/issues/6074),
opened on 2026-09-21, independently describes the same complete causal chain
and small CP2K reproductions. It remains open, with no comments at inspection.
This is our source and control-flow confirmation, not a claim that maintainers
have accepted or merged a fix. The history points to the 2023 refactor
[`3c1a8767`](https://github.com/cp2k/cp2k/commit/3c1a8767f5432c037bf6c68b63f89bb625f1557b),
which replaced a temporary-pointer reset with a whole-structure assignment.
[upstream.json](upstream.json) preserves the inspected versions, issue and
relevant introducing/candidate-fix diffs.

The source path is:

1. [`environment.F:1189`](https://github.com/cp2k/cp2k/blob/67b5da876dd6a76b8b021d5a04d1c81ba79a4c50/src/environment.F#L1189)
   reads `FM_DIAG_SETTINGS` and passes its four settings to initialization.
   The documented defaults are `a=4`, `x=60`.
2. [`cp_fm_diag_utils.F:147`](https://github.com/cp2k/cp2k/blob/67b5da876dd6a76b8b021d5a04d1c81ba79a4c50/src/fm/cp_fm_diag_utils.F#L147)
   saves those settings, then executes
   `work_redistribute = cp_fm_redistribute_type()`. That constructor resets
   `a=x=-1` and both flags to false. The optimal-rank formula at line 170,
   `((K+a*x-1)/(a*x))*a`, consequently returns **-K** for positive order K.
3. [`cp_fm_diag_utils.F:351`](https://github.com/cp2k/cp2k/blob/67b5da876dd6a76b8b021d5a04d1c81ba79a4c50/src/fm/cp_fm_diag_utils.F#L351)
   always selects redistribution for ScaLAPACK, requesting two groups with
   sizes `[-K, P+K]` for P original ranks.
4. [`message_passing.F:2441`](https://github.com/cp2k/cp2k/blob/67b5da876dd6a76b8b021d5a04d1c81ba79a4c50/src/mpiwrap/message_passing.F#L2441)
   creates balanced groups as its fallback. Its check at line 2468 rejects
   the negative requested group size, silently preserving that fallback.
   For the benchmark's even MPI sizes, each group contains P/2 ranks.
5. [`cp_fm_diag.F:605`](https://github.com/cp2k/cp2k/blob/67b5da876dd6a76b8b021d5a04d1c81ba79a4c50/src/fm/cp_fm_diag.F#L605)
   calls `cp_fm_syevd_base` only on ranks holding the new matrix, which was
   created for group zero. Other ranks wait in result redistribution.
6. [`cp_fm_diag_utils.F:117`](https://github.com/cp2k/cp2k/blob/67b5da876dd6a76b8b021d5a04d1c81ba79a4c50/src/fm/cp_fm_diag_utils.F#L117)
   also resets the whole structure during cleanup. Fixing initialization
   alone leaves this independent path for losing the settings.

The compiled [reproducer](reproduce.py) extracts the actual release type,
initialization, cleanup and rank-count routines, plus the group-assignment
statements. It uses inert MPI/BLACS handle types and does **not** invoke MPI
communication, ScaLAPACK or a numerical calculation. It tests both unchanged
source logic and the two-part approach suggested in the issue author's
[candidate commit](https://github.com/MGraml/cp2k/commit/bb6330448f2e82573c30efe1ee3b861ccc1e8d5e).
The generated Fortran files are retained alongside [report.json](report.json).

GCC 14.3.0 with both `-O0` and `-O3` (and runtime checks) produced identical
results for matrix order 33,396:

| Variant | Settings after initialization | Heuristic target | Solving ranks out of 16 / 32 / 64 | Target after cleanup |
| --- | --- | ---: | --- | ---: |
| Original | a=-1, x=-1; flags false | -33,396 | 8 / 16 / 32 | -33,396 |
| Initialization fixed only | a=4, x=60; flags true | 560 | 16 / 32 / 64 | -33,396 |
| Initialization and cleanup fixed | a=4, x=60; flags true | 560 | 16 / 32 / 64 | 560 |

The positive target 560 exceeds all three available rank counts, so the
intended heuristic retains every available rank for this large matrix.
This agrees with the actual benchmark's half-average/max solver timing
signature recorded in the [CPU diagnosis](../cpu-scalapack-diagnosis-20261022.json).
The reproducer establishes the defect and validates the proposed state
lifecycle repair; it does not quantify a performance improvement.

To reproduce on the cluster, choose a new output directory on home storage:

```bash
python3 reproduce.py \
  --source /home/stardust/sai-hpc-software/runs/cp2k-water-cpu-20261020-16v100-a/work/source \
  --out /home/stardust/sai-hpc-software/development/cp2k-scalapack-source-check-NEW \
  --fc /opt/devtools/gcc/14.3.0/bin/gfortran
```

No installed CP2K binary or source was modified for this confirmation. The
corrected CPU companion still needs a separate build, full numerical checks,
and new timing runs. Restoring the shared settings also re-enables the
documented ELPA redistribution flag; a future repair must explicitly test
its intended CPU/GPU scope. Do not infer a twofold speedup from doubling the
number of solver ranks, or carry the old CPU/GPU ratios into the corrected
benchmark. The `PRINT_FM_REDISTRIBUTE` setting is itself affected by this bug,
so changing only input settings cannot work around it in the current binary.
