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

## Deployed idle safeguards

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
