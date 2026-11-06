# QE beta MPI rank output acceptance, 2026-11-05

The standard beta workflow accepted all four existing QE development SIFs and
rotated the home-managed beta alias. No source, compiler options, SIF or public
application launcher changed for this repair.

## Failure and repair

The original 4V100 job `2216535` completed both `manypw.x` images at
`-15.84452726 Ry`, but MPI-forwarded stderr spliced bytes from different ranks.
The tagged output attributed one device-1 kernel to rank 0. Other lines contained
two partially joined kernel records with a single MPI tag, so changing the tag
regular expression would not reliably recover the originating rank.

[`rank-output-failure.json`](rank-output-failure.json) records the original path,
SHA256, observed lines, attribution counts and failed Slurm allocation. That
allocation and its output were preserved.

The acceptance harness now redirects stdout and stderr inside each MPI rank
before `exec manypw.x`. The wrapper is hashed with the request, refuses to
overwrite existing streams, records its rank, and preserves the executable's
exit status. Verification requires two canonical rank directories and matching
markers, hashes both streams, rejects malformed kernel records, and requires a
different GPU for each rank. The existing two-image numerical checks, independent
scratch trees, distinct command/rank traces and host MPI checks still apply.
Infrastructure retry detection also reads the separate stderr streams.

## Real workflow result

- Tested controller: `271a3918eff3cf36a2ac561427995f474b249f85`.
- [Beta workflow 42026314156](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42026314156): success.
- Reused build workflow: `42003384294`, attempt `1`.
- QE develop source: `61569eb480231b649c47f631df9c5c83537df461`.
- Four-partition artifact and runtime receipts: [`rank-output-acceptance.json`](rank-output-acceptance.json).

| Partition | Job | Node | Elapsed | Result |
| --- | --- | --- | --- | --- |
| 16V100 | 2218384 | 16v100n05 | 11 s | COMPLETED, 0:0 |
| 4V100 | 2218386 | 4v100n36 | 21 s | COMPLETED, 0:0 |
| 8V100V0 | 2218388 | 8v100v0n03 | 51 s | COMPLETED, 0:0 |
| DSPRHBM | 2218390 | d9470n05 | 29 s | COMPLETED, 0:0 |

Each GPU partition recorded 8,683 kernel launches on device 0 from rank 0 and
8,683 on device 1 from rank 1. Both images converged to `-15.84452726 Ry` on all
four partitions. The ordinary `pw.x` SCF and force checks also passed. These are
the existing single-node beta qualification cases, not an exhaustive physics or
performance benchmark.

The publication receipt reports generation `daily-20261105-42003384294-1-qe`.
The verified alias is:

```text
/home/stardust/sai-hpc-software/modulefiles/apps/qe/develop-beta-later
 -> develop-beta-20261105-61569eb48023-r4d85319d4295-b1344de3afc7f
```

The published catalog SHA256 is
`8976043d4f9947de0a7b986684c3d168338507f6fb60d645b7e2a64c5dff4b39`.
The `/opt` installation and gateway are outside this repair.

Local regression validation passed all 22 `test_daily_beta` tests, including
concurrent shell writes, unchanged nonzero exit status, the observed forwarded
log corruption, strict rank/device evidence rejection, and ECC retry detection.
