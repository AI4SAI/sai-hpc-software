# MD overlay synchronization — 2026-10-26

The October 26 [scheduled workflow](https://github.com/AI4SAI/sai-hpc-software/actions/runs/40288381664)
compiled DeePMD development source `4e1479d8a23fcc1c9baf36004d5685898ad421a8`
on all three V100 partitions, but fuse2fs teardown failed before final SIF
acceptance. Jobs 1951355 (16V100), 1951360 (4V100) and 1951352 (8V100V0)
returned `74:0`. The [original failure records](original-failures.json) retain
their controller identities, accounting, log hashes and cleanup reports.
The compression-table DDP source patch had been applied, but these failed
builds did not establish working training in a final SIF.

LAMMPS job 1951327 on 16V100 hit the same teardown error after mksquashfs
completed. Its [failure record](daily-blockers.json) is retained. The previous
accepted public versions remain available.

## Repair and validation

Both independent MD builders now call the existing CP2K writable-overlay
synchronization helper after compilation and after export, while the container
payload is still alive. The helper fsyncs a writable file inside the overlay,
flushing fuse2fs metadata before Apptainer starts its teardown timeout.
Each successful exit is followed by `e2fsck -fn`; an error stops the job without
repairing or consuming that image. Transport and recipe fingerprints include
the helper. DeePMD's immutable build receipt additionally binds both sync
reports and both filesystem-check logs, and rejects changed evidence.

GitHub Linux validation on `33525c740bed2b384f6e4198c22511a2420796c0` ran
1,112 tests with two optional skips; shell syntax checks passed. The focused
DeePMD suite passes all 38 tests. Existing exact-permission tests remain
unchanged; the workstation's Windows-mounted filesystem cannot represent
their required modes, so the Linux CI results are authoritative for those tests.

The [fresh DeePMD workflow](https://github.com/AI4SAI/sai-hpc-software/actions/runs/40316174805)
uses recipe `8a404f71ac4c` and the original resolved source above. The
[collected acceptance](deepmd-acceptance.json) revalidates the original frozen
controllers and immutable receipts after Slurm completion:

| Partition | Job | Build and scientific acceptance | Build/export sync |
| --- | --- | --- | --- |
| 16V100 | 1955178 | Completed 0:0, 17m57s | 0.469 s / 1.201 s |
| 8V100V0 | 1955183 | Completed 0:0, 22m30s | 1.424 s / 1.716 s |
| 4V100 | 1955181 | Completed 0:0, 10m38s | 0.670 s / 0.933 s |

All three targets passed both filesystem checks. Their immutable SIF
SHA256 values are `2a60daa610420959522cffba477664e2e53db76498eeb201676a31dea3334682`
(16V100), `2acc7a7dc4c269457a25ed95b312c9cb6053bc10c04540c2eab14971d5d68d32`
(4V100), and `f7353391fcb0f61a7314c8725ecabbcff6128ea343afab3d3169fd08ca856451`
(8V100V0).

The final installed `deepmd/pt/train/training.py` SHA256 is
`6299f10b2d134051c395c0a7bd77655043352ca3ba7c8b136605428f4b92a69e`,
matching the recorded source patch. Actual `torch.distributed.run` training of
both `se_e2_a` and DPA2 completed steps 1–8 and restarted from their checkpoints
through step 12. Both ranks used separate CUDA devices, NCCL, finite gradients,
and updated model parameters with identical final hashes across ranks.
Empty trainable parameters were absent and `find_unused_parameters=False`
remained active. `/sbin/ldconfig` was present. TF/PT/JAX Python and C-API
inference and independently traced CUDA execution also passed.

This establishes single-node, two-GPU PyTorch training and restart on all three
accepted targets. It does not establish multi-node scaling; TF/JAX use the
existing CPU frameworks.

The workflow completed successfully at 12:52 UTC (20:52 Asia/Shanghai).
[Public-route acceptance](deepmd-publication.json) also passed on 16V100
(1956382), 4V100 (1956384), and 8V100V0 (1956386), including inference and
both actual two-GPU training/restart cases through the SIF runtime.
The manifest and immutable module hashes were rechecked, and
`deepmd-kit/master-beta-later` now selects
`master-beta-20261026-4e1479d8a23f-r75ef5179f1e8-b9fb80a6e4fee`.
Generation `daily-20261026-40316174805-1-deepmd-kit` closes this campaign's
three-partition SIF acceptance and publication. The native `/opt` DeePMD
installation is outside this update.

## Other retained blockers

The [LAMMPS rebuild](https://github.com/AI4SAI/sai-hpc-software/actions/runs/40316850352)
uses jobs 1955492 (16V100), 1955376 (4V100) and 1955489 (8V100V0). Original
4V100 job 1951325 was cancelled while still queued. Original 8V100V0 job
1951344 was cancelled after nearly four hours stalled in Apptainer startup,
before its compiler payload ran; its replacement runs on another node.
Original controllers, overlays and logs are preserved. The
[replacement record](lammps-replacements.json) documents these operator actions.

The replacement builds completed on 16V100 in 53m25s and 8V100V0 in 1h11m25s.
Their [scientific submissions](lammps-science-submissions.json) use the original
frozen controllers, delivery files, accepted reference and existing Actions
handoffs, so the science CI jobs can reuse them when the candidate matrix ends.
[16V100 verification](lammps-16v100-science.json) passed all 14 scientific cases
in job 1958567 (1m42s), retaining 71 packages and 2,500 styles. 8V100V0 science
job 1958570 and the 4V100 build remain queued in the
[status snapshot](gap-status.json). LAMMPS's public beta retains the preceding
accepted recipe until its complete matrix passes.

DeePMD 4V100 job 1955181 was initially queued for priority after its time limit was
reduced from four hours to one hour to improve backfill opportunities. The
[scheduler change](deepmd-4v100-backfill.json) preserves the original job
identity and full training gates; it subsequently completed successfully.
LAMMPS's queued replacement was shortened
to 150 minutes. These are scheduler changes to these allocations, not changes
to their frozen controllers.

CP2K 8V100V0 build 1953607 completed in 2h05m17s. Its science job 1956309 failed
after 19 seconds during the two-node `--version` probe, with
`CUDA RUNTIME API error: SetDevice failed with error cudaErrorECCUncorrectable`.
The MPI log identifies failing rank 1 with node `8v100v0n09`; scientific cases
had not started. [Retry 1958543](cp2k-ecc-retry.json) preserves the original
failed task, SIF SHA256
`ddc8c044eb43294da94130ed2c88ccc74c67522cc35552f28819e4cac460ca6c`
and frozen controller, using a new `-science-ecc-retry1` directory. This is a
retry of an explicit node/GPU failure, not a numerical or performance retest.
The site's submission path did not retain `SBATCH_EXCLUDE`, so the actual
pending allocations were updated with `scontrol`. [Scheduler evidence](ecc-node-exclusion.json)
confirms `ExcNodeList=8v100v0n09` for CP2K 1958543 and LAMMPS 1958570.
The two [observers](science-observers.json) monitor these existing jobs and
invoke their original verifiers; they do not publish unverified artifacts.
CP2K's other three targets passed science. Its public beta remains at the
preceding accepted generation while this gap is open.

ABACUS DSPRHBM passed numerical checks but failed its performance gate against
the existing native baseline. PW candidate/baseline median SCF-time ratios
were 1.12024 initially and 1.10909 in the permitted retest; the retest HSE ratio
was 1.20. All exceed the applicable 1.10 threshold. The paired raw results,
scientific energy errors, allocation nodes and evidence hashes are retained in
[daily-blockers.json](daily-blockers.json). Candidate/reference iteration counts
and thread counts match. The [upstream changes](abacus-upstream-diff.json)
since the last accepted branch are an SCCS feature and regression tests;
this inspection alone does not identify a performance cause. No threshold
was loosened and no further retry was used to replace the failed retest.
ABACUS's public beta therefore retains the previous accepted generation.

All new allocations and scratch use
`/org/org-open-sai/proj-sai-hpc-software` and account
`proj-sai-hpc-software`. Application execution continues directly from SIF.
