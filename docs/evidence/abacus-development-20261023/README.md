# ABACUS development acceptance — 2026-10-23

The four targets built the same resolved `develop` commit, completed every declared science and performance gate, published their SIF/native exports to the project catalog, and passed an independent read-only audit using each run's original frozen controller. All 23 build/acceptance jobs completed with `0:0`, account `proj-sai-hpc-software`, and work directories under `/org/org-open-sai/proj-sai-hpc-software`.

- Workflow: [39971072923](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39971072923), completed successfully; the final Actions job states are saved in `workflow.json`.
- Source: `deepmodeling/abacus-develop`, `develop@370ba685e6707b3625c5580ff9d02ef0afb248e8`.
- Frozen controller: `23bb4c1f13e02a87f010db74189341d8fc60f058`.
- Recipe SHA256: `e5ab58f508b4c736f405f252419e1d4fccc14be8963df4af88cd8ae5a99cd25a`.
- Build ID: `develop-2026-10-23-g370ba685e670-re5ab58f508b4`.

| Partition | Build job (elapsed) | Multinode | GPU features | PW | HSE | DeePKS |
| --- | --- | --- | --- | --- | --- | --- |
| 4V100 | 1913214 (00:11:42) | 1913403 | 1913546 | 1913618 | 1913633 | 1913639 |
| 16V100 | 1913204 (00:17:31) | 1913404 | 1913410 | 1913442 | 1913460 | 1913477 |
| 8V100V0 | 1913207 (00:26:40) | 1913524 | 1913532 | 1913547 | 1913595 | 1913610 |
| DSPRHBM | 1913205 (00:21:54) | 1913466 | CPU target | 1913476 | 1913503 | 1913534 |

The GPU targets passed real two-node `cusolverMpSygvd` and NCCL `AllGather` execution checks, including energy validation and the original raw traces. PW runs used GPUs on these targets. The existing HSE and DeePKS benchmark cases use CPUs, including within GPU allocations; they do not establish HSE/DeePKS GPU performance. DSPRHBM uses its CPU contract.

PW energy agreement across the measured baseline/candidate runs was at most `5.775e-10 eV`. HSE and DeePKS candidate errors against the pinned upstream `result.ref` were at most `1.080e-12 eV` and `5.002e-12 eV`, respectively, against the existing `1e-5 eV` tolerance. For HSE/DeePKS, the old site executable is the timing comparator; the candidate uses the pinned upstream numerical reference.

The publication performance metric is the median rounded SCF iteration time, candidate/system, with a limit of `1.10`. Each case retains one warmup pair and three measured pairs. Ratios below one mean the candidate is faster for that metric.

| Partition | PW iteration | HSE iteration | DeePKS iteration | PW wall | HSE wall | DeePKS wall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 4V100 | 0.9340 | 0.2857 | 0.5660 | 1.1266 | 1.1416 | 0.8834 |
| 16V100 | 0.9316 | 0.4222 | 0.7095 | 1.1303 | 0.9435 | 0.9740 |
| 8V100V0 | 0.9524 | 0.3402 | 0.6848 | 1.1825 | 0.8732 | 0.9548 |
| DSPRHBM | 0.9975 | 0.8478 | 0.8033 | 1.0863 | 1.2448 | 1.2700 |

All declared iteration-time gates pass. Total wall time is recorded separately and can be slower for these small cases; the results do not establish a speedup for arbitrary production workloads.

Each `<PARTITION>-validation.json` contains the original identity, request and manifest, complete acceptance evidence, script/evidence hashes, source paths, and durable job accounting. `audit.py` reparses the original scientific data and performance results, verifies the SIF hash, checks the rendered frozen build script, reads back the native export, and checks the module bytes. Each native export contains 8,713 verified manifest entries.

Artifacts are under:

```text
/org/org-open-sai/proj-sai-hpc-software/containers/software/abacus/development/
  develop-2026-10-23-g370ba685e670-re5ab58f508b4/<PARTITION>/
```

Physical `/opt` installation and native execution remain the administrator deployment stage. The audits explicitly retain `native_runtime_verified=false` and `installed_to_opt=false`. The exported runtime profile omits custom test cases, build logs, controller scripts, and other `share/sai` build/acceptance material.

To repeat one read-only audit from the repository:

```bash
ssh -o BatchMode=yes SAI-stardust python3 -I -B - 4v100-avx512 < docs/evidence/abacus-development-20261023/audit.py
```

Other target arguments are `16v100-avx2`, `8v100v0-avx512`, and `dsprhbm`. The audit uses the original frozen controller and does not submit jobs or publish artifacts.

Two existing pending 4V100 jobs received authorized scheduling adjustments after checking account/partition policy and admission: multinode `1913403` switched to `rush-1o2gpu` with a 10-minute limit (historical completed runtimes 4–15 seconds); GPU features `1913546` switched QoS only and retained its 30-minute limit. Their final runtimes were 12 and 33 seconds. Both retained their job IDs, scientific inputs, ranks, GPUs, account and frozen request/script bytes. No owner jobs were cancelled or resubmitted.

The before/after scheduling records are `scheduling-adjustment.json` and `scheduling-1913546-gpu-features.json`; `scheduling-final-outcomes.json` records successful completion and unchanged input hashes. `adjust_pending_qos.py` is the narrow historical QoS adjustment helper and is not part of the read-only audit. No scheduling changes were needed for the three 4V100 benchmark jobs or for the other partitions.
