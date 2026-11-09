# Self-hosted relay qualification, 2026-11-06

Status: deployment retained, new runner stopped and disabled; migration is not
approved by live validation. Main still uses hosted runners at 03:00
Asia/Shanghai. Existing scientific acceptance and beta publication are intact.

## Successful checks

- [Original daily run 42195260368](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42195260368)
  completed successfully on the existing hosted workflow.
- [New runner smoke 42237014017](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42237014017)
  passed on `do2-sai-hpc`: checkout, temporary credentials, real SAI project/Slurm
  access, and cleanup. It did not submit a Slurm allocation.
- At controller `2046292ebb09dbd32c23728e2f6d0d9be0740450`, all 13 hosted
  validation jobs passed. The full suite ran 1,280 tests with two skips.
- After adding startup capacity checks and the beta one-poll test, local
  capacity/relay/beta suites passed 5 + 11 + 23 tests. Unit syntax verification
  passed on DO2; the capacity check refused startup on the actual host.

## Memory and stopped live build

The VPS reports 458 MiB physical memory and no swap. The existing CP2K service
logged an OOM kill at 14:54:47 UTC and restarted. The new SAI service reached
192.2 MiB peak memory. The attempted
[GPUMD relay 42238869985](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42238869985)
lost responsiveness and was stopped at 15:13:38 UTC. GitHub recorded failure;
this was not a completed build or an accepted artifact.

Immediately after stopping the new service, available memory was 197 MiB;
a later sample was 210 MiB. Memory PSI `some avg10` dropped from 88.94% to 0.01%,
then 0.00%. This establishes a memory-pressure contribution independently of
possible cross-border network problems.

The frozen generation remains in `/home/sai/ci/state/42238869985`. The first
GPUMD task was still preparing, with only `identity` and `run_id` checkpoint
fields and no Slurm handle. The other two partitions and beta task were `new`.
Both `squeue` and `sacct` contained no job for workflow `42238869985`.
The interrupted workflow's temporary HPC key was removed explicitly because
the Actions cleanup step was skipped. No accepted SIF or published alias was
modified by this test.

## Initial idle safeguards (superseded by the measured 128 MiB configuration)

Verified on DO2 after reloading the unit and replacing the earlier runtime
overrides:

```text
ActiveState=inactive
SubState=dead
UnitFileState=disabled
MemoryHigh=134217728
MemoryMax=167772160
MemorySwapMax=0
OOMScoreAdjust=1000
Restart=on-failure
RestartUSec=5min
StartLimitIntervalUSec=30min
StartLimitBurst=2
Runner left stopped: MemAvailable=186 MiB, need 288 MiB
```

The startup check also rejects high memory pressure or unavailable metrics.
It is one-shot and creates no resident monitor. The installer leaves the
service disabled. The 160 MiB cgroup limit is a safety boundary, not a measured
working requirement: an end-to-end relay run within that limit remains
unverified. Keep the migration PR in draft and do not switch daily scheduling
on this host without adequate headroom and successful live validation.

## Peak-memory qualification, 2026-11-07 Asia/Shanghai

The experiment measured the whole service cgroup (including children and
charged file cache), process PSS, system `MemAvailable` and memory PSI once
per second. The separate Python observer and its SSH session also consume
host memory; they are included in the host-headroom figures, not the service
cgroup peak. Existing CP2K workers were not modified or used as a stress load.
The observer stops only the tested service if host available memory falls
below 64 MiB, full PSI avg10 exceeds 20%, or a CP2K worker starts. Tests use
bounded runtime and no service restart. A transient cgroup charge can briefly
exceed `memory.max`; peaks below are observed counters, not inferred limits.

| Trial | Service limit | Observed peak | Minimum host available | Outcome |
|---|---:|---:|---:|---|
| Initial tuned runner, 96 MiB | 96 MiB | 88.7 MiB | 116.5 MiB | MemoryHigh was 88 MiB; reclaim stalls stopped it before checkout |
| 128 MiB with invalid Node option | 128 MiB | 128.0 MiB | 116.0 MiB | Rejected Node option; not a capacity result |
| Corrected Node actions | 128 MiB | 128.0 MiB | 73.0 MiB | Checkout/SSH succeeded; upload action caused stalls and was stopped |
| Shell checkout/reporting | 128 MiB | 128.0 MiB | 106.6 MiB | Full smoke passed in 17.7 seconds |
| Shell runner, real GPUMD preparation | 128 MiB | 128.6 MiB | 58.5 MiB | Observer stopped the service during the first source fetch |
| Direct SSH/Python, same frozen generation | 96 MiB | 97.1 MiB | 106.4 MiB | Upload used about 43 MiB; source fetch hit the test cgroup OOM limit |

The passing smoke is
[42253379143](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42253379143).
The failed real GPUMD run is
[42255319358](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42255319358).
Both use controller `f9e769c4b46ada46f6004c4aaa95cb6974f7495b`. The preparation
test resolved GPUMD `aa63ffdd60dc...` and retained its generation under
`/home/sai/ci/state/memory-trials/generations/42255319358`; it did not reach a
Slurm submission or beta promotion. The direct probe reused that exact source
and controller, not a new upstream resolution. Its OOM was inside the bounded
test cgroup, with 106 MiB system memory still available, not a host-wide OOM.
Per-trial summary JSON files are in `raw/`; one-second process/cgroup samples
remain in `/home/sai/ci/state/memory-trials/*.jsonl`.

Changes exercised: 24 MiB .NET heaps/workstation GC, conservation level 9,
disabled tiered PGO/diagnostics, two malloc arenas, Go 16 MiB soft limit, Git
4 MiB delta caches/one packing thread, streaming source validation/splitting,
serial source-part uploads, and shell checkout/reporting instead of Node
actions. PSS shows that runtime mappings and native memory remain significant;
managed heap limits alone do not bound the process RSS.

The direct-resume probe also found an independent preparation gap: after a
failed source fetch, a retry tries to overwrite launcher files already set to
0555. Its 4V100 task failed on these permissions; 16V100 reached a fresh source
fetch before the bounded OOM. A guarded permission repair checked that no
request/job/script existed and that all three launcher hashes still matched
the frozen source; launcher contents were not changed. Future production
recovery must make controller-file upload idempotent before migration. Do not
equate a failed preparation or a passed smoke with an accepted build.

The deployed default now uses a 128 MiB cgroup limit and the measured runtime
settings. The conservative startup check requires 192 MiB available; the
experiments temporarily allowed startup at 160 MiB and used the active observer
described above. Experimental overrides are removed after testing. Full daily
operation, overlap with the existing CP2K worker, and a complete cold source
cache fill are still unqualified. Keep PR #72 in draft and the 03:00 hosted
SSH workflow active. Hosted checks at `ccbd6a2` pass after updating the checkout
contract test; the full suite has 1,286 tests with two skips.
