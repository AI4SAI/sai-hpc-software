# ABACUS v3.11.0-beta11 — October 22 delivery

Upstream's [latest release](https://github.com/deepmodeling/abacus-develop/releases/tag/v3.11.0-beta11)
was published on October 18, with `prerelease=false`, at
`b0411118d360eebe3563f28aeb502ae9d5a9d200`.

## Corrected release delivery — October 23

[Workflow 39808766467](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39808766467)
built four corrected SIFs concurrently from controller
`9e08533ddbe308f45dc1f1189225bfb1cc1a8444` and recipe
`92ecfac1f62a48bd94b0ec779ff75774bb6f9b3dd489815bab0eb9b9133b91d7`.
Each passed version, dynamic-library, feature and native inventory checks.
Build storage and scratch remain under the account's home directory, with
compilation inside writable containers. The public optimized
`fuse2fs/1.47.4-sai` helper was selected for these builds.

| Partition | Build job | Build elapsed | Acceptance/publication |
| --- | --- | --- | --- |
| DSPRHBM | 1901140 | 22m31s | Passed after CPU reference ELPA recovery; published |
| 4V100 | 1901178 | 11m10s | Automatic acceptance and publication passed |
| 16V100 | 1901186 | 17m01s | Automatic acceptance and publication passed |
| 8V100V0 | 1901180 | 25m15s | Built; two-node runtime job 1901416 queued for resources |

The accepted partitions passed two-node SCF and paired PW, HSE and DeePKS
calculations, including one warmup and three measured pairs per case. The
accepted GPU partitions additionally passed actual distributed cuSOLVERMp
eigensolving and NCCL collectives. PW uses GPU kernels on GPU partitions;
the packaged HSE/DeePKS comparisons use CPU kernels in those GPU-enabled
binaries. Native exports were checked against their
inventories, and publication was independently revalidated from raw science
outputs, SIF checksums, exported file bytes and module metadata.
[corrected-delivery.json](corrected-delivery.json) records these results,
exact artifacts, hashes, job accounting and the outstanding 8V100V0 job.
The existing GitHub acceptance job continues to own the queued GPU test and
its following acceptance/publication stages. No duplicate build is needed.

The home module selects an accepted partition in a Slurm allocation:

```bash
module use /home/stardust/sai-hpc-software/modulefiles/apps
module load abacus/release/v3.11.0-beta11-gb0411118d360-r92ecfac1f62a
```

The SIFs and sibling `.native` export directories are below
`/home/stardust/sai-hpc-software/containers/software/abacus/release/v3.11.0-beta11-gb0411118d360-r92ecfac1f62a/<PARTITION>/`.
These are home publications. No physical beta11 `/opt` installation has been
performed, and 8V100V0 is not yet an accepted module target.

### CPU reference-library recovery

The original CPU HSE benchmark job `1901343` failed inside the old site
`v3.9.0.26` reference before executing the candidate. Its loader selected
`elpa-2026.02.001-2603-gnu/nvidia`, followed by ELPA/MPI errors. Reducing the
MPI ranks from eight to two did not fix the reference (`1901377`). A new
comparison kept the original eight MPI ranks, two threads, executable and
input, and changed only the reference library to the installed
`elpa/2026.02.001-2603-gnu-auto` selection:
`/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx512/cpu`.
HSE job `1901386` passed, followed by DeePKS `1901442` and PW `1901444`.
Their median SCF iteration-time candidate/reference ratios were approximately
0.851, 0.803 and 1.000. These small cases check regressions; they do not
measure production-scale throughput.

The fresh recovery requests pin the same candidate SIF and frozen controller;
the original failed evidence remains intact. The normal publication gate
revalidated the recovered proofs before publishing the CPU artifact.
[cpu-reference-recovery.json](cpu-reference-recovery.json) records the failed
and corrected comparisons, actual library paths and raw-output checksums.
The current benchmark also checks the exact CPU reference module/library,
including a negative test for a successful calculation using the wrong ELPA.
All 31 benchmark tests passed; the current verifier independently accepted
the recovered HSE and DeePKS raw evidence. The original GitHub CPU acceptance
job remains failed; the manual recovery does not turn it into a successful
automatic run.

## Initial daily builds

The [daily workflow](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39742021186)
built all four partitions from that commit using controller
`f1f19288b87512b4a17da7ebb619250f5a1233d6` and recipe
`8511375340556e9e44c0935b36d7421cb7d7cb5b66380f731708bdc2105f606f`.

| Partition | Build job | Elapsed | Result |
| --- | --- | --- | --- |
| DSPRHBM | 1899611 | 38m42s | SIF built and verified |
| 4V100 | 1899855 | 23m10s | SIF built and verified |
| 16V100 | 1900076 | 29m58s | SIF built and verified |
| 8V100V0 | 1900302 | 37m27s | SIF built and verified |

`builds.json` records exact artifact paths, hashes, build identities,
accounting, `abacus --info`, required features and library-resolution checks.
The SIFs are approximately 173–176 MiB each. Build, cache and scratch storage
reside under `/home/stardust/sai-hpc-software`; compilation occurred inside
the writable container image.

All builds retain MPI/OpenMP, LCAO, ELPA, LibXC, FFTW, LibRI/LibComm,
Cereal, LibTorch, libnpy, NEP and JSON. GPU builds additionally verify CUDA
sm70, CUDA-aware MPI, cuSOLVERMp, cuBLASMp and NCCL. This is the feature union
of the two established site modules recorded in the inventory. The optional
full profile (DeepMD/TensorFlow, DFT-D4, PEXSI and cnpy) is not enabled.

## Acceptance handoff repair

`skipped-acceptance-job.log` records the actual failing result gate:
`dependencies=skipped`, `build=success` with its nonempty `run_id`, and
`acceptance=skipped`. GitHub applied implicit `success()` to the acceptance
condition, including the CP2K-only ancestor that ABACUS and GPUMD skip.
The condition now uses an explicit cancellation check and requires the build
to succeed with a run ID. Cancellation, failed builds and release deduplication
still skip acceptance; the existing final result gate is unchanged.

The regression replays this state for ABACUS, GPUMD and CP2K and covers
cancellation, failed/skipped builds and deduplication. The 22 workflow
outcome/source-resolution tests passed locally. Restoring the original
condition makes the ABACUS and GPUMD cases fail.

## Recovery

Acceptance reuses the exact original SIFs and frozen controllers. Recovery
commands and logs are recorded under the cluster home directory
`/home/stardust/sai-hpc-software/diagnostics/abacus-beta11-20261022/`.
Scientific outputs remain in the original `runtime-tests/` tree.
The new fuse2fs selection merged in PR #16 applies to future builds and does
not change these frozen build identities.

All three GPU partitions passed the initial two-node SCF smoke test (jobs
1900862–1900864) and actual distributed cuSOLVERMp/NCCL execution. PW paired
acceptance passed on 16V100 and 8V100V0. `initial-acceptance.json` records
those proofs and failures. DSPRHBM job 1900865 requested 32 CPUs and remained
queued behind another task at the per-user QOS limit. It was cancelled before
execution when the ELPA repair required new candidates; CPU acceptance now
defaults to 16 CPUs across two nodes.

The recovery identified three additional issues:

* HSE selected the old unified ELPA library. GDB job 1900902 stopped at
  `elpa_index_get_int_loc` executing `vmovdqu8 %zmm0,...` on 16V100 (AVX2).
  `elpa-sigill-gdb.log` records the instruction and actual library path.
  Diagnostic job 1900917 changed only library selection to the installed
  AVX2 variant. The same binary/input converged to
  `-428.9943139416807298 eV`, versus the pinned upstream reference
  `-428.9943139416827000 eV` (about `2e-12 eV` difference).
  `partition-elpa-hse-scf.log` retains its raw calculation output.
  The recipe now loads `elpa/2026.02.001-2603-gnu-auto`, checks the exact
  partition path at build/loader/native-export stages, and carries the Slurm
  partition into the clean build container. Both GPU and CPU variants reuse
  the existing public CP2K-qualified installation.
* The 4V100 PW benchmark failed during the reference's MPI startup twice
  (1900881, 1900892). A minimal hostname probe reproduced the failure without
  ABACUS. Job 1900958 showed PRTE selecting cores outside the remote Slurm
  cpuset. Removing additional PRTE binding let both ranks run inside their
  allocated four physical cores. GPU comparisons now use one rank per node
  with inherited affinity. The existing physical/logical core-count,
  non-overlap and identical-resource audits remain mandatory.
* The old system HSE result is `-428.9942827110015742 eV`, approximately
  `3.12e-5 eV` away from beta11's upstream reference. It cannot define the
  newer release's numerical correctness. Packaged HSE/DeePKS candidates now
  match their own checksum-pinned `result.ref` within the original `1e-5 eV`
  tolerance. System and candidate repetitions must each remain consistent;
  the system binary remains the timing comparator. PW/custom comparisons
  retain their original numerical criterion, and performance thresholds are
  unchanged. Missing, nonfinite, duplicate or mismatched references fail.

The initial four SIFs are not accepted releases. They are superseded by the
corrected build record above. The workflow permits four
concurrent partitions; Slurm singleton names now include ABACUS's partition
and recipe so independent targets no longer serialize. Native export,
publication and physical `/opt` installation are separate milestones.
