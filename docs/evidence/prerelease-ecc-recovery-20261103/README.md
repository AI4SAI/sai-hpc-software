# Historical prerelease compatibility and ABACUS ECC recovery

This continues the [failure diagnosis](../build-failure-diagnosis-20261103/README.md)
without replacing the container runtime, changing `/opt`, or altering failed
allocations. All fresh build/runtime work uses project storage and account
`proj-sai-hpc-software`.

## Source and inventory fixes

[PR #54](https://github.com/AI4SAI/sai-hpc-software/pull/54) skips only the exact
ABACUS prerelease `DCU@e07f9d7213f51b72a9f77e7f832855d5d6cf9484`. Live source
resolution retains `develop` and release `v3.11.0-beta12`. Future prereleases
remain eligible; network/source errors still propagate.

DeePMD inventory accepts either backend plugins or defined backend API symbols
in the installed native `libdeepmd_cc.so`. The first retry found another
inventory omission: `ptexpt` is part of the 3.2.0 baseline and is compiled into
the v3.2.0b0 library. Read-only inspection of the failed overlay confirmed its
`DeepPotPTExpt::init` definition. [PR #55](https://github.com/AI4SAI/sai-hpc-software/pull/55)
adds that backend and optional Paddle detection. Neither patch relaxes baseline
feature parity, numerical checks, measured GPU execution, or two-GPU
training/restart acceptance. Missing capabilities now appear by name.

PR #54 full CI passed 1,175 tests (2 skipped); PR #55 passed 1,176 (2 skipped).
Real ELF fixtures distinguish defined native implementations from undefined
symbols and Python-only libraries. New beta GPU checks also probe the host CUDA
runtime: an error 214 receipt must match the job, node, partition, work directory
and pinned helper before the existing single node-excluding retry is allowed.
The new healthy-device probe ran successfully on all three GPU partitions.

## ABACUS accepted results

The unchanged release SIF has SHA256
`b40db0d2dfe710bf7f242cf314b993bf0ebd9e0c60d0eaed2efff10745d7a4db`.
The original failed multi-node job 2151551 is retained. The original frozen
controllers verified fresh jobs and published the existing release artifact in
the project catalog; no native `/opt` installation was performed. All four
release partitions now have the required verified proofs and published catalog
records.

| Gate | Job | Nodes | Result |
|---|---|---|---|
| Two-node SCF / host MPI | 2155781 | 8v100v0n03/n04 | Accepted; energy -4869.747051950222 eV |
| NCCL and cuSOLVERMp | 2155872 | 8v100v0n02/n03 | Accepted |
| PW benchmark | 2155889 | 8v100v0n02/n08 | Accepted |
| HSE benchmark | 2155920 | 8v100v0n02/n08 | Accepted |
| DeePKS benchmark | 2155985 | 8v100v0n02/n08 | Accepted |

The first fresh SCF allocation selected healthy nodes. Its attempted
`SBATCH_EXCLUDE` environment setting was not honored by Slurm; the following
feature/benchmark submissions pass `--exclude=8v100v0n09` explicitly. Their
submission commands are preserved. All five jobs completed `0:0`; the frozen
verifiers rechecked scientific evidence before publication.

The development beta was revalidated using today's already accepted SIFs,
without rebuilding them. Workflow
[41623923019](https://github.com/AI4SAI/sai-hpc-software/actions/runs/41623923019)
rotated `abacus/develop-beta-later` from October 31 to source `c9079539e69a`,
fixed module `develop-beta-20261103-c9079539e69a-r75ef5179f1e8-be00ef62a2ffe`.
Jobs 2156066 (16V100), 2156068 (4V100), 2156070 (8V100V0), and 2156072 (DSPRHBM)
all passed numerical/module-route verification and completed `0:0`.

## DeePMD reruns

The first retry workflow
[41623755044](https://github.com/AI4SAI/sai-hpc-software/actions/runs/41623755044)
keeps its three failed allocations. It successfully built/exported SIFs and
stopped at the `ptexpt` inventory omission; those SIFs were not accepted or
published. The second retry,
[41625676017](https://github.com/AI4SAI/sai-hpc-software/actions/runs/41625676017),
passed build/installation/backend verification and sealed all three SIFs, then
failed real PyTorch inference. The old `tabulate_ops.py` eagerly registers a
Meta kernel for an existing `CompositeImplicitAutograd` operator. Each failed
allocation and its sealed but unaccepted SIF remain intact.

The third retry,
[41628542583](https://github.com/AI4SAI/sai-hpc-software/actions/runs/41628542583),
packaged the upstream dispatcher guard. All three builds again passed, but
PyTorch inference failed later when TorchScript could not resolve the model's
Python `ModelOutputDef` class. These failures and artifacts are retained too.

[PR #56](https://github.com/AI4SAI/sai-hpc-software/pull/56) backports two upstream
runtime fixes:

- The dispatcher guard from
  [`1ded5d242946`](https://github.com/deepmodeling/deepmd-kit/commit/1ded5d2429467fb16cb1d6c3d3059fac881f82a7)
  preserves the existing composite decomposition instead of registering a separate
  Meta kernel.
- In `_eval_lower_vesin`, use the evaluator's Python `self.output_def` instead of
  calling `inner.model_output_def()` through the scripted model. This behavior is
  present in upstream release commit
  [`8cfd46e37448`](https://github.com/deepmodeling/deepmd-kit/commit/8cfd46e37448feca361ff3427a6c2b514ce5a2bb).

Each change applies only to the exact reviewed historical file digest, after
Python wheel installation. The upstream checkout and version metadata stay
intact. Installed-file hashes and a schema-2 receipt bind both changes to the
final image. Final verification reconstructs the original files and checks their
hashes; the immutable build receipt also binds the compatibility receipt. Other
runtime errors still fail normally. The final code at
`c646e8824fab1a1fe7c89f77086fbb838dac6018` passed 48 focused DeePMD tests and
[full CI](https://github.com/AI4SAI/sai-hpc-software/actions/runs/41632525042)
with 1,182 tests (2 skipped). See [upstream backport identity](upstream-backport.json).

Diagnostic job 2156986 on 16v100n43 completed `0:0` in 1:39. It reused the third
retry's SIF and bound the corrected `deep_eval.py` read-only. All six TF/PT/JAX
Python and C API cases, measured CUDA execution, and two-GPU `se_e2_a`/`dpa2`
training and checkpoint restart passed. Because the file bind changed its
runtime, this diagnostic cannot certify or publish the original SIF.

The fresh normal build passed in workflow
[41632594236](https://github.com/AI4SAI/sai-hpc-software/actions/runs/41632594236)
at `c646e8824fab1a1fe7c89f77086fbb838dac6018`, packaging both fixes in the SIF.
All three final images passed the unchanged installation/version/backend checks,
six TF/PT/JAX Python and C API numerical cases, measured CUDA execution, and
two-GPU `se_e2_a`/`dpa2` training and checkpoint restart. The final frozen
controllers verified the immutable build and scientific receipts.

| Partition | Job | Node | Complete job elapsed | Result |
|---|---|---|---|---|
| 16V100 | 2157188 | 16v100n11 | 6:52 | Accepted, `0:0` |
| 4V100 | 2157184 | 4v100n31 | 5:34 | Accepted, `0:0` |
| 8V100V0 | 2157191 | 8v100v0n03 | 8:24 | Accepted, `0:0` |

These times include compilation, export and acceptance; they are not a general
application performance comparison. Each image is `candidate.sif` below:

```text
/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/runs/
  dp-41632594236-1-prerelease-16v100-avx2-2e3117e9e4c4/
  dp-41632594236-1-prerelease-4v100-avx512-2e3117e9e4c4/
  dp-41632594236-1-prerelease-8v100v0-avx512-2e3117e9e4c4/
```

The source is `v3.2.0b0@2e3117e9e4c4e0225cae5b1b43d50bd4f03811f6`;
the recipe is `e428ed8865862ae8f84f65e290a00a335970c3be50bfa62c87645220c54faab3`.
The `/opt/apps/deepmd-kit/...` prefix in their receipts is inside the SIF.
This work did not install those prerelease builds into native `/opt` or publish
them as development beta modules.

## Retained evidence

[summary.json](summary.json) contains the accepted image paths, full SHA256
digests, accounting, numerical/backend coverage and publication boundaries.
[snapshot.json](snapshot.json) preserves the complete accepted and failed
status records. [accounting.txt](accounting.txt) includes the fresh allocations
and the preserved DeePMD failures.

[raw.tar.gz](raw.tar.gz) retains 1,238 source-plan, job, log, receipt, numerical
and training evidence files. Every raw member was verified against
[manifest.json](manifest.json); [archive-verification.json](archive-verification.json)
records the archive digest. The archive excludes SIFs, model binaries and build
caches. Original failed run directories and their artifacts remain on project
storage. The initial ECC diagnosis remains in the linked earlier evidence.
