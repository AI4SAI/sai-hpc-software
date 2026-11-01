# October 31 CP2K and LAMMPS compatibility repair

The October 30 retry compiled CP2K but rejected its libgrpp evidence after
upstream moved source lists into `src/CMakeSources.cmake`. LAMMPS failed to
compile the interface matching installed DeePMD v3.2.0 because current LAMMPS
removed the integer `Modify` lookup methods and public arrays.

[PR #38](https://github.com/AI4SAI/sai-hpc-software/pull/38) adds the included
CP2K source list to the pinned evidence and adapts the DeePMD interface checkout
to `get_compute_by_id` / `get_fix_by_id`. Missing-ID, shape and TTM type checks
remain enforced. Only interface sources are patched; installed DeePMD remains
a pinned dependency. Original failed runs retain their controllers and logs.

## Recovery complete — November 1

The [final CP2K observation](cp2k-completion-20261101.json), collected at November
1 03:22 UTC / 11:22 Beijing, closes the CPU gap described in the historical
observations below. Workflow 41169343854 attempt 2 succeeded at October 31
17:22 UTC / November 1 01:22 Beijing.

| CPU stage | Slurm job | Elapsed | Result |
| --- | --- | --- | --- |
| Dependencies | 2073677 | 1:04:13 | Completed, exit 0:0 |
| Application and SIF | 2075397 | 0:57:31 | Verified, exit 0:0 |
| Scientific acceptance | 2076654 | 0:16:55 | Verified, exit 0:0 |

The fresh dependency overlay synchronization succeeded in 0.543 seconds, with
no driver cleanup failure receipt. This accepts the fresh attempt; it does not
change the previous failed checkpoint. The final CPU SIF has SHA256
`074083e11f81e0be106d127cef34708a7ee12fa6050413a892e4c1a0b534730e`.

Every CP2K target passed the seven cases listed below in both single- and
multi-node modes, using energy/force tolerances `1e-6 Ha` and `1e-5 Ha/bohr`.
The public Lmod/SIF checks also passed on all four targets:

| Partition | Public check job |
| --- | --- |
| 16V100 | 2077264 |
| 4V100 | 2077266 |
| 8V100V0 | 2077268 |
| DSPRHBM | 2077270 |

The public checks report maximum energy and force discrepancies of
`1.16e-12 Ha` and `1.01e-12 Ha/bohr` respectively. These are the small public
launch checks; the separate science evidence retains the full case matrix.
The live alias and ordinary module help select
`cp2k/master-beta-20261031-d077e66fe06a-r75ef5179f1e8-bbeff8b74f789`.
The accepted snapshot uses the UTC date, so its label is October 31 despite
completion after midnight in Beijing.

This completes CP2K's four-target development acceptance and publication, and
the October 31 five-application recovery. The final observation also records
the current ABACUS, LAMMPS, DeePMD and GPUMD beta aliases and ordinary help.
The November 1 scheduled source matrix is separate and had not started at the
11:20 Beijing inspection. No new build or test was needed for this evidence
update; the recorded workflow itself completed the required acceptance.

## Validation

Controller commit `2be83eaf69134f42ba4d39c95b6f8b76f1e8c18a` passed the full
GitHub validation suite: 1,125 tests in 52.198 seconds, with two optional tests
skipped because their local upstream source cache is absent in CI. Shell syntax
checks also passed. The [validation job](https://github.com/AI4SAI/sai-hpc-software/actions/runs/41169343854/job/124443716731)
includes both CP2K source-list layouts and the C++ regression for the removed
LAMMPS API, missing IDs and incorrect TTM types.

[Source preflight](source-preflight.json) records hashes of the actual files
downloaded at the newly resolved upstream commits. CP2K's four source files pass
the feature proof. The pinned DeePMD interface adapts successfully and
idempotently to the new LAMMPS header. All three LAMMPS containers emitted the
same patch receipt as this preflight; their complete acceptance is recorded below.

## Accepted results and CPU recovery

The [follow-up observation](acceptance-followup.json) was collected at October
31 14:45 UTC / 22:45 Beijing. It records scientific evidence hashes, identities,
Slurm accounting, public module help and publication receipts.

| Software | Partition | Build job | Science job | Result |
| --- | --- | --- | --- | --- |
| LAMMPS | 16V100 | 2064546 | 2065585 | Accepted and beta-published |
| LAMMPS | 4V100 | 2064544 | 2065583 | Accepted and beta-published |
| LAMMPS | 8V100V0 | 2064551 | 2065587 | Accepted and beta-published |
| CP2K | 16V100 | 2065456 | 2065866 | SIF and science accepted |
| CP2K | 4V100 | 2065047 | 2065663 | SIF and science accepted |
| CP2K | 8V100V0 | 2065449 | 2066463 | SIF and science accepted |
| CP2K | DSPRHBM | 2064494 (dependencies) | — | Teardown failed; fresh retry 2073677 running |

Every LAMMPS target passed 14 cases with 71 packages and 2,645 styles, including
single- and two-node GPU correctness. Public checks 2065799, 2065801 and 2065803
also passed (`max_error=0.0`). Ordinary `module help lammps/develop-beta-later`
now identifies source `7dbed49448a17dbc9d4bc3eee5512e9ea2ad3a04` and fixed module
`develop-beta-20261031-7dbed49448a1-r75ef5179f1e8-b692a31172a2c`.

Each CP2K GPU target passed seven cases in single- and multi-node modes:
`ch2o-tblite`, `water-dftd4`, `water-diag`, `water-elpa`, `water-gpw`,
`water64-elpa`, and `water64-gpw`. Energy/force tolerances are `1e-6 Ha` and
`1e-5 Ha/bohr`. The CPU dependency payload and explicit overlay synchronization
completed, but Apptainer reported a fuse2fs teardown timeout. The driver retained
the failure and returned 74. All five passes of read-only `e2fsck -fn` succeeded;
this diagnostic does not waive the failed teardown or seal that checkpoint.
Workflow attempt 2 submitted a fresh dependency directory/job 2073677 and keeps
the successful GPU results. CPU application/science and public beta checks
remain pending; `cp2k/master-beta-later` still selects October 27.

The two compatibility changes have passed actual upstream compilation and
scientific execution. The CPU dependency teardown predates the changed CP2K
feature proof and is tracked separately. The afternoon daily workflow
41184760477 still used old main `bedd2ba9722286ec6a6b934eedcee6eaecce63d2`, so it
repeated both compatibility errors. It also hit an independent ABACUS DSPRHBM
MPI placement failure in job 2067149 before scientific execution. The existing
accepted ABACUS beta remains selected while that candidate is recovered.

## Fresh build observation

The [read-only cluster observation](rerun-observation.json) was collected at
2026-10-31 04:51:56 UTC / 12:51:56 Beijing. Every job below was `RUNNING`.

| Software | Partition | Slurm job | Stage |
| --- | --- | --- | --- |
| CP2K | 16V100 | 2064495 | Dependencies |
| CP2K | 4V100 | 2064489 | Dependencies |
| CP2K | 8V100V0 | 2064493 | Dependencies |
| CP2K | DSPRHBM | 2064494 | Dependencies |
| LAMMPS | 16V100 | 2064546 | Application build |
| LAMMPS | 4V100 | 2064544 | Application build |
| LAMMPS | 8V100V0 | 2064551 | Application build |

- [CP2K workflow](https://github.com/AI4SAI/sai-hpc-software/actions/runs/41169343854):
  source `d077e66fe06a862d49bdcc2b91c179463e9487eb`, recipe `8c0adc292317`.
- [LAMMPS workflow](https://github.com/AI4SAI/sai-hpc-software/actions/runs/41169355573):
  source `7dbed49448a17dbc9d4bc3eee5512e9ea2ad3a04`.

Both source version labels carry October 30 UTC, while this rerun started on
October 31. The complete source SHA is fixed for every partition in its matrix.
Builds, scratch and evidence use `/org/org-open-sai/proj-sai-hpc-software` and
Slurm account `proj-sai-hpc-software`. Compilation remains inside containers,
using the existing pinned fuse2fs optimization and overlay synchronization.

This initial observation is retained as a historical submission record. The
accepted follow-up above supersedes its in-progress status without changing
the original evidence.
