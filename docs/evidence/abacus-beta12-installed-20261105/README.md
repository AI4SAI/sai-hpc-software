# ABACUS beta12 physical installation acceptance

The administrator installed `v3.11.0-beta12` from the reviewed
[deployment package](../../../deployment/abacus-3.11.0-beta12/README.md).
On November 5, all four physical installations passed complete file/module
verification and 30 native scientific calculations. These runs execute the
installed ELF programs directly with the host MPI; no container is involved.

Source: `6b24db05dc175d6e1d7894ac380f48118b43fbaa`.
Deployment controller: main commit `f349d880640800b1d8c6fb26a835b1bfaf5d1f04`
([PR #64](https://github.com/AI4SAI/sai-hpc-software/pull/64)). The original
accepted binaries and SIFs were reused without rebuilding.

| Partition | Native job | Nodes | Scientific cases | Elapsed | Result |
| --- | --- | --- | ---: | ---: | --- |
| 16V100 | 2217409 | 16v100n33, 16v100n34 | 8 | 49 s | COMPLETED 0:0; passed |
| 4V100 | 2217408 | 4v100n31, 4v100n36 | 8 | 106 s | COMPLETED 0:0; passed |
| 8V100V0 | 2217410 | 8v100v0n03, 8v100v0n05 | 8 | 58 s | COMPLETED 0:0; passed |
| DSPRHBM | 2217411 | d9470n03, d9470n04 | 6 | 414 s | COMPLETED 0:0; passed |

Every partition passed PW, HSE and DeePKS on one and two nodes. V100 partitions
also passed two-node cuSOLVERMp and NCCL cases, with actual solver/collective
calls in the logs and GPU execution in the relevant scientific outputs.
HSE and DeePKS use their accepted CPU algorithms, including on GPU partitions;
allocated GPUs remain visible. CPU runs use four ranks per node and two
threads per rank; GPU partitions use one rank and one GPU per node.

The maximum absolute energy error against the pinned reference is
`2.0453626348171383e-08 eV`, below the unchanged `1e-5 eV` threshold. Every
calculation also matches the accepted SIF candidate, and single/two-node
energies agree. Version/features, every rank's executable hash, MPI placement,
thread count, loader paths and module unload all passed. Replaying the raw
checks and checking final Slurm accounting passed for all four jobs.

The installed module is:

```text
/opt/modules/modulefiles/apps/abacus/3.11.0-beta12-auto
```

Load it inside a supported Slurm allocation:

```bash
module load abacus/3.11.0-beta12-auto
```

It selects the corresponding native prefix under
`/opt/apps/abacus/abacus-release-v3.11.0-beta12-g6b24db05dc17-r20e07b5cdfb3/`.
All 76 regular runtime files, four command links and the public module match
the deployment manifest. Login-node help prints the version/source and system
template path; loading outside a Slurm allocation is rejected without leaving
an ABACUS prefix. A cache-bypassing module query lists the public version.

## Evidence

| File | Contents |
| --- | --- |
| [summary.json](summary.json) | Per-case energies/timings, errors, required features, nodes and controller hashes |
| [verification.json](verification.json) | Successful replay of the pinned native verifier |
| [accounting.json](accounting.json) | Slurm account, work directory, resources and final state |
| [installed-files-and-module.json](installed-files-and-module.json) | Full installed-file verification, public module hash/help/login rejection |
| [manifest.json](manifest.json) | SHA256 and size of every archived file plus the archive digest |
| [raw.tar.gz](raw.tar.gz) | Frozen controls/inputs, rank and loader traces, scientific outputs, submission records and job logs |

All test work and scratch use the project account/storage. The complete
remote results remain at:

```text
/org/org-open-sai/proj-sai-hpc-software/runtime-tests/abacus-beta12-physical-20261105-190518-384810
```

The submission and collection operation is
`/org/org-open-sai/proj-sai-hpc-software/operations/abacus-beta12-installed-20261105-190516`.
The four original jobs all passed; no retries or scheduler changes were made.
4V100 started through normal backfill after approximately five minutes queued.

## Timing limits

This is correctness/installation acceptance, not a performance comparison.
DSPRHBM's two-node HSE and DeePKS invocations took 136.47 s and 224.77 s.
The host MPI reported shared-memory backing-file warnings on the project
Lustre scratch path, including an available-space reading of zero. The runs
nevertheless completed with correct energies. The warnings and timings are
preserved in the raw logs; they do not establish the physical storage quota or
prove which component caused the longer wall time.
