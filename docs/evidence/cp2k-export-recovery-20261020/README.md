# Completed CP2K compilation: recover skipped export

Workflow [39293908513](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39293908513)
completed all four dependency builds and all four application compiler jobs.
The application jobs ended `COMPLETED 0:0`:

| Partition | Application job | Elapsed |
| --- | --- | --- |
| 16V100 | 1865877 | 28m46s |
| 4V100 | 1865642 | 41m20s |
| 8V100V0 | 1866021 | 1h27m11s |
| DSPRHBM | 1865863 | 35m54s |

Their logs reach successful CP2K linking/installation and contain no missing
runtime library in the final `ldd` output. Read-only `debugfs` inspection found
the expected installed release identity, native entry, and successful GCC14
ELPA consumer probe on every partition. There is no `artifact.path`, SIF or
export-stage invocation. GitHub's delivery monitor correctly reported failure
despite Slurm's successful exit; scientific acceptance and tuning did not start.

The build lock launched `/bin/bash -seu -o pipefail` on a heredoc. The new
ELPA ABI probe's `mpiexec` inherits that stdin and forwards it, consuming the
outer shell's remaining export/verification commands. A stdin-reading child
reproduces the failure locally; the actual site Open MPI launcher reproduces
it with `/bin/true`. See `stdin_reproducer.py` and its captured JSON. This is
independent of compilation correctness or ELPA communicator handling.

Production `build_capacity.py --shell-script-stdin` first reads the complete
script, then invokes Bash with `-c` and child stdin from `/dev/null`. Both CP2K
dependency and application launchers use it. Regression tests execute a child
that reads stdin and verify export/verification still run, while failed pipelines
still stop execution and release the lock.

`submit.py` recovers only this diagnosed workflow. It checks the frozen recipe,
terminal source jobs and installed identities, seals the old overlay, and uses
the original controller to render metadata/export/verify commands. A new
`-export` run receives a checked clone; the existing validated helper removes
only the clone's overlayfs work debris. The wrapper supplies the frozen command
text through an argument and isolates stdin. No compiler or installed recipe
file is changed. The original `38dfba7e3ed7` identity and final `/opt/apps/cp2k/…`
prefix remain intact; source and recovery jobs have separate receipts.

`follow.py` monitors each recovered artifact, runs the frozen one/two-node
science protocol, and publishes only on success. Each accepted GPU artifact then
gets the existing 216-water single-GPU experiment. It records per-partition
failures independently. These recovery runs do not turn the original failed
GitHub workflow green, and submission is not scientific acceptance.

The four recovery jobs were observed running: 16V100 `1867803`, 4V100
`1867800`, 8V100V0 `1867805`, DSPRHBM `1867798`. The replacement follower is
PID `2616159`; the obsolete watcher for the original run paths was stopped
after the new follower started. `raw/` preserves **49 files** hashed on SAI and
independently verified after download, including original compiler logs,
installed identity/ELPA probe records, recovery submissions, the real MPI
stdin reproducer and the observed accounting. This is a timestamped recovery
submission snapshot, not final acceptance. All 50 focused regression tests
passed (build lock, dependency stage, CP2K contract, delivery integration,
and export recovery).

The 64-water pilot's 1.2% difference applies only to one V100 on 16V100, with
1472 basis functions. In this CP2K version `PREFERRED_DIAG_LIBRARY CUSOLVER`
calls cuSOLVERMp even on one GPU. No separate `CUSOLVER_MP` input is needed.
Larger systems and multiple GPUs can change the ranking against ELPA and the
benefit of direct generalized diagonalization; see the expanded
[measurement plan](../../CP2K-V100-TUNING.md#shared-elpa-rebuild-and-issue-9).
