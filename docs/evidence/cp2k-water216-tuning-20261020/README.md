# CP2K 2026.2 shared-ELPA: completed 216-water comparison

The accepted release uses recipe `aed7aa53ff43`; all four builds and 56 science
calculations passed before these experiments were submitted. See the
[release and 4V100 evidence](../cp2k-shared-elpa-release-20261020/README.md).

The same 216-water PBE/DZVP-MOLOPT-SR-GTH input, 300 Ry cutoff and `EPS_SCF 1e-9`
was run on each GPU partition, with one V100, one MPI rank and the allocated
physical cores (four on 4V100/16V100, three on 8V100V0). Each mode has one warmup
and three alternating measured repetitions. Values below are median CP2K
internal total times in seconds, excluding launcher/image checks.

| Partition / job | cuSOLVERMp standard | cuSOLVERMp direct | Shared ELPA | OT with cuSOLVERMp |
|---|---:|---:|---:|---:|
| 4V100 / 1870680 | 123.823 | 111.638 | 143.875 | 368.940 |
| 16V100 / 1871044 | 266.766 | 207.497 | 319.984 | 553.069 |
| 8V100V0 / 1872055 | 289.649 | 248.545 | 357.341 | 651.133 |

All three jobs ended `COMPLETED 0:0`. The frozen remote verifiers and a separate
local reparse passed all 48 calculations, including numerical agreement,
convergence, actual solver dispatch and physical-core affinity. The completed
4V100 data were archived with the release. This supplement contains the other
32 calculations, their frozen controls, raw inputs/outputs, forces, GPU samples,
Slurm accounting and verification results. All 661 entries in
[`raw/SHA256.json`](raw/SHA256.json) were independently hashed after download;
[`archive.json`](archive.json) pins the 5,256,619-byte source archive.
See [`local-validation.json`](local-validation.json).

Direct reduced total time by 9.84%, 22.22% and 14.19%, respectively, on this
input. These results support offering the direct option; they do not identify
a universal default, measure multi-GPU scaling, or compare the new shared
ELPA against the earlier private build under otherwise identical conditions.
The OT column changes the SCF algorithm and therefore does not measure the
effect of changing OT's internal eigensolver.

## OT backend comparison

Controller `4aa35c8` adds the `ot-backends` profile to the
[runner](../../../deployment/cp2k-diagonalization/README.md). Both modes retain
OT/DIIS, `FULL_ALL`, the same input/resources and direct `FALSE`; only
`PREFERRED_DIAG_LIBRARY CUSOLVER/ELPA` differs. Each pair uses one warmup and
three alternating measured repetitions. Verification requires actual OT and
the requested backend, with no direct generalized dispatch or other-backend
fallback. Numerical checks precede timing summaries.

| Partition | Job | Run suffix |
|---|---|---|
| 4V100 | 1877200 | `39365139034-4v100-avx512-ot-water216` |
| 16V100 | 1877201 | `39365139034-16v100-avx2-ot-water216` |
| 8V100V0 | 1877206 | `39365139034-8v100v0-avx512-ot-water216` |

Runs are under `/home/stardust/sai-hpc-software/runtime-tests/cp2k-direct-<suffix>`.
Their submission identities are preserved in [`ot-submissions.json`](ot-submissions.json).
All three jobs subsequently completed and passed 24 calculations with remote
and independent local verification. Shared ELPA reduced OT total time by
13.10%, 7.06% and 5.57%, respectively, for this input. See the
[completed controlled OT comparison](../cp2k-ot-backends-20261020/README.md).
