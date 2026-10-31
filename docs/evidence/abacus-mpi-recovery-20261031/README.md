# ABACUS CPU multi-node acceptance recovery

The October 31 daily workflow used source
`6b24db05dc175d6e1d7894ac380f48118b43fbaa`. Its DSPRHBM build succeeded, but
multi-node job 2067149 failed before any rank entered ABACUS. Open MPI/PRTE
reported that the requested two CPUs per rank would span packages. The script
already used `ppr:4:node:pe=2`; the shared-filesystem warning was not the fatal
error. The exact historical allocated CPU masks were not retained, so the
particular placement that caused the failure is not reconstructed.

A fresh allocation, job 2074089, used the same SIF, frozen controller and
two-node/eight-rank/two-CPU configuration on `d9470n[01-02]`. It passed the
multi-node energy check. The original failed allocation and logs remain intact.
The SIF SHA256 is
`946352bdbe3630e632e58c78743084efeb95bf8ded35e7edf9c01ee61e409010`.

The remaining CPU acceptance checks passed without a performance retest:

| Case | Slurm job | Candidate/system iteration-time median ratio |
| --- | --- | --- |
| PW | 2074122 | 1.0000 |
| HSE | 2074180 | 0.8269 |
| DeePKS | 2074326 | 0.9250 |

These are the existing small acceptance workloads with one warm-up and three
measured runs per implementation. They establish the numerical and iteration
time gates, not general performance equivalence: DeePKS whole-process wall time
was noisy and its median ratio was 2.7312. Full statistics, evidence hashes,
artifact identity and terminal accounting are in [the recovery record](recovery.json).
The original controller accepted the CPU artifact after checking all four proofs.

CI now permits one new allocation for this exact pre-execution mapping failure.
It checks frozen controller/script/image pins, CPU resources, `FAILED/254:0`
Slurm accounting, the specific diagnostic, and absence of rank or scientific
output. A receipt binds the original request and diagnostic hashes. A workflow
rerun monitors the recorded retry; it does not allocate a third attempt.
Scientific failures and performance retest limits are unchanged.

[Cluster replay](real-e2e.log) exercised the updated CI orchestration against
both original allocations: the original monitor returned failure, the evidence
classifier accepted that narrow condition, and the unchanged-SIF retry monitor
verified success. No new job was submitted by this replay. The full Linux CI
suite passed 1,132 tests with two optional source-cache skips in
[validation run 41213052758](https://github.com/AI4SAI/sai-hpc-software/actions/runs/41213052758).

## Public beta completion

The resumed daily workflow 41184760477 attempt 2 passed public module checks on
16V100 (2074535), 4V100 (2074537), DSPRHBM (2074541), and 8V100V0 (2074782).
The initial 8V100V0 allocation 2074539 stalled on `8v100v0n09`. A native CUDA
probe on its allocated GPUs returned error 214, `uncorrectable ECC error
encountered`, when creating a primary context on device 1; device 0 succeeded.
The diagnostic was retained and the stuck allocation cancelled. The existing
hardware retry rule read that diagnostic, excluded `8v100v0n09`, and passed on
`8v100v0n04` in a separate run directory. No failed allocation was relabelled.

[The final status observation](final-status.json) includes the CUDA diagnostic,
original/retry accounting, publication receipt and ordinary module help. It
confirms `abacus/develop-beta-later` selects
`develop-beta-20261031-6b24db05dc17-r75ef5179f1e8-b51dec325dca1` as of October 31
15:25 UTC / 23:25 Beijing. This closes ABACUS's four-partition daily acceptance
and publication. The old daily workflow still contains unrelated historic
CP2K/LAMMPS failures; their compatibility recovery is recorded separately.

The same observation confirms October 31 public snapshots for LAMMPS, DeePMD
and GPUMD. DeePMD `e6f7357379f7` passed public GPU inference plus actual two-GPU
se_e2_a/DPA2 training and restart on all three targets. CP2K CPU dependency job
2073677 remains running, so its public snapshot remains October 27.

PR #39 merged as `52989279f6e60629057924f562e4adc73291ad54`. GitHub Linux CI is
the full validation result (1,132 tests, two optional skips). The additional
local suite completed the same 1,132 tests but reported two errors because
`cmake` is absent locally; those two cases pass in CI. Fourteen local optional
tests were skipped for unavailable tools/source caches.
