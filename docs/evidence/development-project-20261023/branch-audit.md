# Development branch integration audit — 2026-10-23

The audit compares the three remote branches not ancestry-merged into
`origin/main` at `aac79a9428cb8819d4c388bce1700205bc9c9cfa`.
`git cherry -v`, right-only patch-equivalent history, file diffs and a read-only
three-way merge simulation distinguish unmerged work from squashed history.

| Branch | Evidence | Recommendation |
| --- | --- | --- |
| `feat/multi-software-daily` | Its only commit, `963d296`, is patch-equivalent to `1201755` in main (`git cherry`: `-`). | Already integrated through PR #6; no repeat merge is needed. |
| `fix/validation-partitions` | Its only commit, `17c1b29`, is patch-equivalent to `38d6898` in main (`git cherry`: `-`). | Already integrated through PR #7; no repeat merge is needed. |
| `fix/workflow-closure` | `c2183b3e3cb6905f4984022a18e6228aa3c62615` and `d0d3d475721c67aacd3ac7ee5954d0f8223d2b06` remain unique. | Retain the CPU experiment tools and accepted evidence in a reviewed integration. |

The unique branch adds the CP2K **2026.2** CPU ScaLAPACK redistribution repair,
controlled one-object relinks, real MPI participation tests and archived
numerical/timing evidence under `deployment/cp2k-water-scaling/` and
`docs/evidence/cp2k-scalapack-mpi-repair-20261022/`.
It also binds benchmark acceptance/tuning to the actual `libcp2k` checksum,
since the unchanged executable is only a launcher. Its campaign waits for
successful causal MPI acceptance before proceeding.

No 2026.1 reference or automatic workflow-dispatch change occurs in these
experiment files. Integrating the code does not restart the abandoned site
2026.1 baseline or any historical campaign. Historical submission scripts
retain their original home paths/account behavior; any future execution needs
the current project directory/account policy first. Existing artifacts and
frozen job controllers remain historical evidence.

The common ancestor is `0331caababe64afd6ea1dd88e08b3ebb37d2da58`. Main has
no subsequent changes to the experiment code files touched by the branch.
Only `docs/WORKFLOW-CLOSURE.md` changed on both sides; the read-only merge
simulation produced no conflict markers. `d0d3d47` changes only 68 files
under `docs/`; the implementation is in `c2183b3`.

## Current upstream source already includes the repair

[CP2K PR #6338](https://github.com/cp2k/cp2k/pull/6338) merged on
2026-10-23 at 13:36:34 UTC as
`04b255137f7b472b07f09bec26e7bbf81c366a3a`, the development source selected
for this round. It includes the initialization/cleanup lifetime repair plus
ELPA target-layout validation, the effective redistribution default and MPI
unit tests. The old experiment patch is locked to the exact 2026.2 source
hash and must not be applied to this master revision. No additional change
to `controller/cp2k_source_fixes.py` is needed for this development build.

## Validation

The two historical GitHub `validate` checks on `c2183b3` are successful:
[CP2K](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39754065858/job/119741507755)
and [HPC software](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39754065915/job/119741507348).
The recorded full suite ran 874 tests with two skips. Build jobs skipped on
these validation events are not treated as end-to-end application builds.

On October 23, a fresh `git archive` of exact commit `d0d3d47` was extracted
under the local checkout's ignored `.test-work/branch-audit-d0d3d47/`, without
switching the shared branch. Its `test_cp2k_mpi_repair.py` and
`test_cp2k_water_scaling.py` passed all **18 tests** in 0.672 seconds.
The archived `verify_archives.py` then independently rechecked all **42
calculations** in three compressed raw-evidence bundles: frozen input/file
hashes, successful Slurm accounting, MPI/BLACS participation, repeated solves,
energies, forces and timing medians all passed. No new cluster job was run.

| Archived job | Partition / physical cores | Original / repaired median total seconds | Archive SHA-256 |
| --- | --- | --- | --- |
| `1897997` | 16V100 / 64 | 77.689 / 65.073 | `7f3d01b33788a4034a804ea0a83233dfaed4853f62e0f1b8937df8c236871bfa` |
| `1897952` | 16V100 / 16 | 281.769 / 173.851 | `c22d943a8a621ad41f1124b88c4675d534d457c985dbeaa540a90989e58cefa3` |
| `1897953` | 4V100 / 16 | 97.773 / 92.111 | `dd89f2a073514279e5c54e40a06c4679981d31058415a9bd665b80f77ea2b4d1` |

These checks validate the retained CPU experiment and its provenance. They do
not establish successful builds of the five current development branches.
