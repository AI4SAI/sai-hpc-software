# VPS workflow-dispatch probe, 2026-11-08

The lightweight trigger path passed once: the dedicated `sai` account on DO2
called the GitHub dispatch API, and the existing hosted daily workflow reached
SAI and completed successfully. This does not qualify the full self-hosted
runner or enable a daily timer.

- [Workflow 42448953847](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42448953847)
- Controller: `9022ef5b9d461fab7fc424a65a7a744b33076f4f` (`main`).
- Dispatch: `daily.yml`, `software=qe`, `tracks=release`,
  `targets=16v100-avx2`, `retry_releases=false`.
- Raw measurements, job timestamps and the release receipt: [result.json](result.json).

## Timing and downstream result

All times below are Asia/Shanghai (UTC+8).

| Event | Time |
| --- | --- |
| VPS probe started | 12:59:18.244 |
| GitHub created the workflow | 12:59:19 |
| VPS received HTTP 204 | 12:59:20.002 |
| First hosted job started | 12:59:23 |
| SAI returned the unchanged-release receipt | 13:05:32.608 |
| Workflow completed successfully | 13:06:02 |

The API request took 1.674 seconds. The first job started four seconds after
workflow creation. The complete workflow took 6 minutes 43 seconds, including
hosted validation and the controller upload/remote identity and release checks.

The resolved release was QE 7.6 at
`9f93ddec427d2b9a45bb72d828c6d324f62fcabd`. SAI returned `build=false` and
`previous_run=qe-release-20261105-r7-16v100-avx2`. This proves authenticated
controller transfer and remote release bookkeeping; it created no new Slurm
allocation, rebuilt no application and performed no new scientific acceptance.
No development beta was selected or rotated.

## VPS memory and cleanup

The API request ran in a transient user service with `MemoryMax=32M`,
`MemorySwapMax=0`, `OOMPolicy=kill` and a 50-second runtime ceiling. The API
credential was supplied on stdin and was not saved on the VPS. A Python wrapper
sampled host memory every 20 ms and recorded kernel cgroup counters after curl
exited. The cgroup includes both the measurement wrapper and curl; SSH and the
systemd launcher are outside that cgroup and contribute to host usage.

- Cgroup memory peak: 10,489,856 bytes (10.004 MiB).
- Minimum sampled host `MemAvailable`: 174,280,704 bytes (166.207 MiB).
- Cgroup memory limit hits, OOMs and OOM kills: zero.
- Host memory PSI `some/full avg10`: both 0.00 at completion.
- The transient unit was collected after exit; no timer or new resident
  listener was installed.

The full `sai-hpc-runner.service` remained inactive and disabled with its
128 MiB cap. Its existing startup check still refused startup at 166 MiB
available because it requires 192 MiB. The cold-source preparation,
interruption recovery and overlapping-load qualifications documented in
[the original relay experiment](../self-hosted-relay-20261106/README.md)
remain open. This probe establishes that VPS-originated dispatch can be used
independently of that migration, while orchestration continues on hosted
runners and application jobs remain on SAI.
