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
idempotently to the new LAMMPS header. All three running LAMMPS containers emitted
the same patch receipt as this preflight. This establishes patch application;
complete compilation and scientific acceptance are still pending.

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

The workflows still require final SIF verification, scientific acceptance and
public Lmod launch checks before beta rotation. This observation is not a
completed build, numerical acceptance or publication claim.
