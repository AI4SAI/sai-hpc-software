# Installed DeePMD NCCL verification, 2026-11-15

The installed `deepmd-kit/3.2.0` module has the same NCCL selection problem as the inspected SIF. Native PyTorch loads `/opt/apps/conda_env/deepmd-kit-3.2.0/lib/python3.13/site-packages/nvidia/nccl/lib/libnccl.so.2`, despite the module selecting SAI NCCL and enabling its dual-rail policy. `libtorch_cuda.so` and `libtorch_global_deps.so` have DT_RPATH entries for that bundled directory.

| Library | SHA256 |
| --- | --- |
| Bundled upstream NCCL, default selection | `e0875bd54d68b31426deb6ea897aaa885c055ae9d8c3794859bb9d2b91cab5e7` |
| Existing SAI `2.29.3-1-sai.2` | `af89e7e8f5a25daacc0831cb423faee23fa03407158458be45eb242e81b1193f` |

Both report NCCL 2.29.3. Checking version strings or NCCL_ROOT alone misses the problem. The SAI library is selected by adding one `prepend-path LD_PRELOAD` setting to the native DeePMD module; the exact candidate is in `deployment/deepmd-site-nccl-20261115`. Patch activation, successful communication and training are checked in addition to mapped-library paths, because PyTorch may also map its bundled library after preloading the SAI implementation.

## Native reproduction and validation

No SIF was used. All runs selected the unchanged native `/opt/apps/conda_env/deepmd-kit-3.2.0` environment and used two V100 GPUs.

| Allocation | Partition / node | Result |
| --- | --- | --- |
| 2483437 | 16V100 / 16v100n09 | Original module: cross-subnet IB warning and `IBV_WC_RETRY_EXC_ERR`. Candidate: repeated length broadcast, all-reduce and object broadcast pass. Initial training encountered a separate diagnostic TMPDIR issue. |
| 2483571 | 16V100 / 16v100n40 | Normal transport and forced IB: `se_e2_a` and `dpa2` training/restart all pass; module unload preserves an existing preload. COMPLETED, 1m40s. |
| 2483659 | 8V100V0 / 8v100v0n09 | Same eight training/restart checks and unload check pass. COMPLETED, 1m57s. |
| 2483656 | 4V100 | Pending capacity when this report was prepared; not a pass. |

Each training check verifies GPU gradients, finite values, actual parameter updates, agreement of final parameters across ranks, expected step counts and checkpoint restart. All successful forced-IB logs record `NCCL-SAI rail-by-channel communicator agreement enabled` and real NET/IB/GDRDMA traffic. Both rails remain visible. `NCCL_P2P_DISABLE=1` was used only for the explicit IB diagnostic, not added to the module.

The initial native training used a long project TMPDIR. PyTorch appends `/torch-shm-dir-XXXXXX/manager.sock`, exceeding the Unix-domain pathname limit. A minimal CPU shared-tensor probe reproduced the error with that path and passed using a short private `/dev/shm` directory. This diagnostic defect did not require changing PyTorch or DeePMD. Persistent training outputs and evidence stayed under project storage; `/tmp` storage was not used.

Machine-readable results, timing, log excerpts and checksums are in `results.json`; full acceptance summaries and communication logs are preserved in adjacent `.gz` files (SHA256 values refer to the uncompressed originals). Original evidence is under `/org/org-open-sai/proj-sai-hpc-software/operations/diagnostics/deepmd-native-nccl-20261115`.

## Scope and deployment

The prepared installer changes only `/opt/modules/modulefiles/apps/deepmd-kit/3.2.0`. It verifies the exact original/candidate module and tested NCCL hash, backs up the old file, and supports idempotent installation and rollback. Tests cover replacement/rollback, newer administrator edits, binary mismatch and redirected paths.

At preparation time, the public module is still unchanged. The `stardust` account cannot write its parent directory. The parent directory belongs to group `sai-partner` with write access; `saiuser01` is a member and can atomically replace the file. Root can preserve the original `entropy` ownership; a group maintainer's replacement is owned by that maintainer.

This update covers processes launched with `module load deepmd-kit/3.2.0`. The old 3.1.2 module uses a different environment and NCCL 2.28.9; it is not updated or claimed validated here. Direct conda activation, exported release virtual environments and the daily SIF launcher do not necessarily load this module, so their entry points still require explicit SAI NCCL selection. Multi-node qualification is outside these two-GPU native checks.
