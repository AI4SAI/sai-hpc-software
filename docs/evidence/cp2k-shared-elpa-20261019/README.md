# CP2K with the accepted shared ELPA — 2026-10-19

The new recipe selects the physical ELPA 2026.02.001 libraries by target and
pins their headers, Fortran modules, pkg-config metadata and shared-library bytes.
It retains GCC 14.3.0 for CP2K. The shared ELPA was compiled with site GCC 13.3.0.

## Cross-compiler preflight

| Partition | Job | Node | Result |
|---|---|---|---|
| 16V100 | 1862298 | 16v100n04 | COMPLETED 0:0 |
| 4V100 | 1862299 | 4v100n01 | COMPLETED 0:0 |
| 8V100V0 | 1862300 | 8v100v0n02 | COMPLETED 0:0 |
| DSPRHBM | 1862301 | d9470n02 | COMPLETED 0:0 |

Each job compiled the same Fortran source with GCC 14.3 inside Apptainer,
against the read-only physical ELPA selected for that partition. It used
ELPA's type-bound Fortran API to solve a 32-square Toeplitz matrix and checked
its analytic spectrum and residual. Maximum errors were `1.33e-15` and
`8.88e-16`. The MPI process was a single-rank CPU solve, including when linked
to the GPU-capable library. This probe does not establish CP2K GPU or scientific
acceptance; those are separate full-application tests.

[preflight.json](preflight.json) records Slurm accounting, results and all
archived file hashes. `preflight/<target>/` contains exact submitted controls,
compiler version, consumer loader output and numerical logs. These probes used
unprivileged `--userns`; the full application build uses the existing fakeroot
overlay workflow and permits its singleton MPI probe to run as container root.
All writable paths were under `/home/stardust/sai-hpc-software`.

GPU job `1862389` (16V100, `16v100n09`) and CPU job `1862390` (DSPRHBM,
`d9470n02`) then both completed `0:0` with the full build's fakeroot and
isolated network settings. They compiled and ran the same probe directly and
through CMake/FindPkgConfig. CMake selected the pinned ELPA version/library and,
on the GPU target, the pinned CUDA link libraries. These checks used the
final provider verifier from recipe commit `797d6c3`.
[cmake-preflight.json](cmake-preflight.json) pins the 30 raw files under
`cmake-preflight/`, including CMakeCache and the exact submitted scripts.

The full rebuild must use a new recipe identity and final `/opt/apps/cp2k`
prefix. The earlier private-ELPA CP2K installations remain independently
accepted. See [ELPA integration](../../ELPA.md) and the
[issue #9 experiment protocol](../../CP2K-V100-TUNING.md#shared-elpa-rebuild-and-issue-9).

## Actual release rebuild

Workflow [39262701906](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39262701906)
uses commit `797d6c3`, source `67b5da876dd6a76b8b021d5a04d1c81ba79a4c50`,
and recipe SHA256 `46ac9460660f9bf876ddfaacd2b0c5e38c8f2df161edff34035aa8335a1467be`.
The candidate prefix is
`/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-r46ac9460660f/<PARTITION>`.

| Partition | Dependency job | Initial node |
|---|---|---|
| 16V100 | 1862478 | 16v100n04 |
| 4V100 | 1862509 | 4v100n01 |
| 8V100V0 | 1862503 | 8v100v0n11 |
| DSPRHBM | 1862508 | d9470n02 |

[build-start.json](build-start.json) records the observation time, scheduler
states, resources and log tails. The submitted scripts, requests and CI
handoffs are preserved under `build-start/`. This is a running dependency
stage, not a completed CP2K SIF. The workflow will continue with application
compilation and single/two-node science when each dependency checkpoint passes.

## Repeated GPU object lifecycle audit

Job `1862456` completed `0:0` on `16v100n01` in six seconds. Each process
created/destroyed eight ELPA objects and solved twice per object. Both CPU and
GPU runs passed all 16 numerical checks, but the GPU run created eight tracked
temporary MPI communicators and explicitly freed none before exit. The CPU
run created none of these communicators. The tracer intercepts the Fortran MPI
symbols called by ELPA; it does not alter solver results or communicator lifetime.

[communicator-audit.json](communicator-audit.json) binds exact library/source
bytes, controls and raw logs. The upstream source confirms the missing free
after `MPI_Comm_size`; the applicable upstream PR #85 fix is archived as
`communicator-audit/upstream-communicator-fix.patch` and has **not** been applied
to the shared installation. The previous private-ELPA recipe already included
this repair. Repeated GPU diagonalization needs a repaired standalone library
and renewed integration qualification before a long-running production recommendation.

## Controller validation

Linux CI [39262616115](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39262616115)
passed all 818 tests (816 passed, two optional skips) at `797d6c3`.
[validation.json](validation.json) records this result and the local mounted
filesystem's two permission-mode test failures; those same tests passed on
Linux CI. No artifact or permission check was waived.
