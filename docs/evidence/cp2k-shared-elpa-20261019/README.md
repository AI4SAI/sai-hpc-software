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

The full rebuild must use a new recipe identity and final `/opt/apps/cp2k`
prefix. The earlier private-ELPA CP2K installations remain independently
accepted. See [ELPA integration](../../ELPA.md) and the
[issue #9 experiment protocol](../../CP2K-V100-TUNING.md#shared-elpa-rebuild-and-issue-9).
