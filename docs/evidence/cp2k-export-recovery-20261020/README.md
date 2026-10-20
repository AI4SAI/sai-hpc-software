# Completed CP2K compilation: recover skipped export

All four recovered SIFs now pass version/features, dynamic loading and native
inventory checks. DSPRHBM passed all 14 scientific calculations, the frozen
numerical verifier and publication. With the currently installed shared ELPA,
the GPU candidates **failed science**: 16V100 fails the 64-water ELPA2
calculation; 4V100 and 8V100V0 fail the small distributed ELPA2 calculation at
`cudaHostRegister`. An isolated ELPA workaround has now passed all **42/42**
unchanged CP2K calculations on the three GPU partitions. Its two-library
administrator package passed preflight and was subsequently installed.
[Four-partition physical acceptance](../elpa-backtransform-native-20261020/README.md)
then passed 460 numerical solves, and the CP2K dependency lock was updated.
New-identity CP2K compilation and science remain pending; the original GPU
deliveries retain their failures. No 216-water GPU tuning run has started.
The sections below retain the original failures and recovery snapshots.

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

The first 16V100 recovery `1867803` stopped before metadata because its frozen
renderer still bound `input/cp2k-updates`, which the new run had not created.
The empty export-only mount directories were added to all four runs; future
production metadata/export commands omit unused source/cache mounts entirely.
`retry_missing_bind.py` preserves the failed attempt, repairs stale filesystem
counters only on its disposable clone, and continues metadata/export/verify
without copying or compiling again. Job **1867844** produced a new SIF and
passed version/features, loader and native-inventory checks. Science job
**1867852** then started; this snapshot does not yet establish science success.
The 21 files in `raw-retry16/` independently match their remote hashes and
preserve both the failed attempt and successful continuation. Its separate
follower PID is `2742475`; PID `2616159` continues the other three targets.

Linux CI for the stdin fix at `3ebee81` passed **837 tests: 835 passed, two
optional skips**. The follow-up export mount changes also passed the focused
contract/integration/recovery suites.

The 64-water pilot's 1.2% difference applies only to one V100 on 16V100, with
1472 basis functions. In this CP2K version `PREFERRED_DIAG_LIBRARY CUSOLVER`
calls cuSOLVERMp even on one GPU. No separate `CUSOLVER_MP` input is needed.
Larger systems and multiple GPUs can change the ranking against ELPA and the
benefit of direct generalized diagonalization; see the expanded
[measurement plan](../../CP2K-V100-TUNING.md#shared-elpa-rebuild-and-issue-9).

## Subsequent export completion and integration diagnosis

| Partition | Successful export job | Science job | Observed outcome |
| --- | --- | --- | --- |
| 16V100 | 1867844 | 1867852 | 12/14 calculations ended normally; large ELPA2 GPU case diverges |
| 4V100 | 1867800 | 1867888 | Small two-node ELPA2 GPU host registration fails |
| 8V100V0 | 1868476 | 1868525 | Small two-node ELPA2 GPU host registration fails |
| DSPRHBM | 1868315 | 1868396 | All 14 calculations and numerical acceptance passed |

DSPRHBM's first recovery `1867798` timed out mounting the writable image,
before metadata execution. `retry_mount_timeout.py` exclusively locks and
checks its prepared clone, preserves the failed attempt, and submits only the
remaining export/verification. 8V100V0's `1867805` hit its 60-minute Slurm limit
after clone cleanup and filesystem checks, while completing image hashes.
The scheduler denied a wall-limit extension. `continue_timed_out_clone.py`
finishes the recorded source/clone checks on the login node, repairs only stale
clone counters under an exclusive image lock, and resumes export. It retains
the interrupted preparation logs and writes a separate continuation receipt;
the interrupted attempt is not relabelled successful. Neither recovery
recompiles CP2K or modifies the sealed compiler overlay.

`diagnose_shared_elpa.py` submits explicitly non-publishing controls on the
same SIF, same scientific inputs and same resources. Jobs `1868438` (16V100)
and `1868440` (4V100) reproduce large ELPA2 GPU nonconvergence. The cuSOLVERMp,
ELPA CPU-kernel and ELPA1 GPU alternatives converge in 24 SCF steps for the
64-water input. The small distributed GPU failure reproduces on 4V100, while
its CPU-kernel and ELPA1 controls converge. These are diagnostic calculations,
not repeated performance measurements or a replacement acceptance protocol.

`diagnose_elpa_phases.py`, job `1868492`, forwards explicit `ELPA_FORCE_*`
controls only through a recorded diagnostic launcher copy. Disabling the GPU
`band_to_full` phase restores convergence; disabling band reduction, the
tridiagonal solve or only the `tridi_to_band` GPU kernel does not. This narrows
the large-matrix issue to the ELPA2 back-transformation path. The separate
small-matrix error occurs in GPU registration of `hh_trans` in
`elpa2_trans_ev_tridi_to_band_template.F90`. A standalone 128-square analytic
spectrum test did not cover these CP2K matrix sizes, partial eigenspaces and
multi-node distributions.

`gpu-backtransform-host-staging.patch` is a **qualified local workaround** for
the tested cases, not an upstream repair or a performance result. It chooses the upstream
host-staged paths for the two ELPA2 back-transformations while retaining GPU
matrix operations, GPU kernels, cuSOLVER, the five CUDA architectures, and
CUDA-aware MPI elsewhere. `build_elpa_host_staging.py` freezes builds inside
containers at the existing final `/opt/devtools/elpa/…/nvidia` prefixes; the
host install directories are task-owned home paths. AVX512 build `1868554`
and AVX2 build `1868591` both passed standalone numerical and
communicator-lifecycle checks. No public library is overwritten.

`check_elpa_candidate.py` binds the isolated library into its final container
path and reruns all 14 **unmodified** CP2K science inputs. Each MPI rank records
the candidate library hash. Its verifier checks unchanged scientific inputs,
rank placement, positive backend execution, convergence, energies and forces,
and rechecks the candidate files. Jobs `1868636` (4V100), `1868645` (16V100)
and `1868647` (8V100V0) completed this protocol, all `COMPLETED 0:0`.
`follow_candidates.py` independently verified all three jobs without installing or publishing.
The result is explicitly ineligible for
publication: a changed shared-library inventory needs its own dependency lock
and accepted delivery identity. `collect_state.py` archives these attempts,
receipts and diagnostics with hashes; earlier snapshots remain immutable.

Linux CI for `e06e7e5` also passed. The focused export contract, delivery
integration and recovery suites passed 35 tests before that commit.

## Qualified ELPA handoff and final integration snapshot

| Partition | Candidate science job | Calculations | Frozen numerical verifier |
| --- | --- | --- | --- |
| 4V100 | 1868636 | 14/14 passed | Passed |
| 16V100 | 1868645 | 14/14 passed | Passed |
| 8V100V0 | 1868647 | 14/14 passed | Passed |

Both small distributed ELPA2 and 64-water ELPA2 cases now converge. Across
single/two-node and solver comparisons, the maximum energy difference is
`4.798e-11 Ha` and maximum force difference is `1.539e-9 Ha/Bohr`, within the
unchanged tolerances. These are correctness checks, not a timing comparison.
The per-rank library hashes match the exported AVX2/AVX512 payloads. AVX2 serves
both 16V100 and 8V100V0; AVX512 serves 4V100.
After download, the hash-matched parser independently reproduced all 42 samples
and every comparison from the raw inputs, logs and forces; see
[local revalidation](handoff/local-science-revalidation.json).

The [administrator package](../../../deployment/elpa-2026.02.001-backtransform/README.md)
contains 19.31 MiB AVX2 and 20.44 MiB AVX512 payloads. Its generated
`release.json` pins both SIFs, new/previous inventories, the unchanged public
module, patch and all three candidate-science receipts. Package generation
reverified the 42 calculations and both standalone numerical/Fortran/lifecycle
protocols, then compared SIF extraction with the actual CP2K-tested libraries.
The installer preserves old trees and changes only the two NVIDIA prefixes.
Physical installation has not been performed by stardust.

`raw-integration/` contains **2,156 files**: 2,155 remotely hashed evidence files
and their hash index, all independently verified after download. The archive
SHA256 is `4f4544ef5fec6e6f679c5c0aba699fb774ee30762d59472bd609231933c4b13d`;
its size is 14,343,068 bytes. [Import receipt](integration-archive.json).
It preserves all four export outcomes, failed original GPU science, DSPRHBM
acceptance/publication, the control experiments, both ELPA builds and all 42
candidate calculations. The earlier `raw/` and `raw-retry16/` snapshots remain
unchanged. Deployment preflight and effective cross-account access evidence
are retained separately in `handoff/`.

[Linux CI at `0e0ed1c`](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39360092429)
passed **840 tests: 838 passed, two optional skips**. The NVIDIA-only handoff and
existing backup/rollback suites passed all 10 focused tests.
