# SAI daily build relay

The `sai` account on DO2 has one repository-scoped GitHub Actions runner,
`do2-sai-hpc`, with label `sai-hpc-relay`. It is currently stopped and disabled:
the optimized runner passes a 128 MiB smoke test, but the full preparation path
has not passed on the existing shared 512 MiB VPS.
The daily workflow remains hosted until this deployment passes live validation.
The separate `cp2k` account and its tester runner are not part of this deployment.
Compilation and scientific acceptance still run in Slurm under
`/org/org-open-sai/proj-sai-hpc-software`.

The proposed daily workflow starts at 03:00 Asia/Shanghai. Its hourly `:17` trigger only
resumes unfinished generations; it never resolves a new source or starts a new
daily build. Separate Actions concurrency groups preserve a queued nightly
request. A filesystem lock serializes the one relay's durable state.

## Runtime and files

```text
/home/sai/ci/
  runner/                  # official Actions runner 2.338.2
  bin/gh                   # official GitHub CLI 2.103.3
  deployment/sandbox.py    # Landlock entrypoint
  deployment/check_capacity.py # one-shot memory check before listener startup
  work/                    # Actions checkout, ephemeral credentials
  tmp/                     # runner and source-transfer scratch
  cache/repositories/      # full Git ancestry, bounded at 1.5 GiB after packing
  state/<origin-run-id>/
    plan.json              # frozen source SHAs, controller SHA, original attempt
    checkout/              # hashed immutable controller/fixture snapshot
    tasks/<task-id>/
      checkpoint.json      # preparation state, submit intent, Slurm receipts
      controller.log
      results/             # metadata and canonical remote evidence paths
      beta-results/        # publication metadata, if applicable
/home/sai/.config/systemd/user/sai-hpc-runner.service
```

No SIF, build tree, training output or raw scientific evidence is copied to the
VPS. Per-task upload scratch is removed after preparation; frozen plans, logs
and metadata remain. The original run identity survives Actions reruns, a new
recovery workflow, a changed checkout, midnight and service restarts.
Checkout uses Git directly and summaries go to the Actions job log. The daily
relay does not run Node checkout/upload actions or create downloadable Actions
artifacts; complete controller logs remain in the durable VPS state directory.

The single worker prepares/submits each task, records its handle, and yields
while Slurm runs. All selected partitions can therefore execute concurrently.
Before advancing, the relay checks queue/accounting and the recorded working
directory. The existing software controller then validates the exact request,
artifact and numerical evidence. Waiting is exit 75, never acceptance. An
observation deadline fails the Actions run and leaves jobs intact for recovery.
Permanent build/science failures are recorded and block that software's beta;
other software continues. Existing unchanged releases remain explicit skips.

A lost submission reply is recoverable only with a complete matching remote
request, script and job handle. An incomplete receipt stops that task instead
of guessing whether another `sbatch` is safe. Recover the retained handle before
retrying; deleting a checkpoint to force submission is not a recovery method.

## Installation and operation

Run `install-user.sh` as the dedicated `sai` account. It downloads and verifies
the two pinned upstream binaries and installs the user service, leaving it
stopped and disabled (including when updating an existing installation). On first
installation, supply a short-lived repository registration token on stdin:

```sh
gh api --method POST repos/AI4SAI/sai-hpc-software/actions/runners/registration-token --jq .token |
  ssh -l sai DO2-cp2k 'bash /path/to/install-user.sh'
```

The installer requires its adjacent Python helpers and service file. It uses no
root installation. Only start the service after capacity and deployment review;
enable lingering and automatic startup only after a successful live validation.
Keep the existing `hpc` Environment secrets
`REMOTE_USER` and `REMOTE_SSH_PRIVATE_KEY`; the CP2K tester's restricted cluster
key is not suitable for build submission. Keys are written only to the current
Actions temporary directory and removed in an `always()` step.

```sh
systemctl --user status sai-hpc-runner.service
journalctl --user -u sai-hpc-runner.service -n 40 --no-pager
```

Dispatch `daily.yml` with `mode=smoke` to check runner identity, project access
and Slurm command availability without allocating nodes. Dispatch `mode=resume`
to continue an interrupted generation. `mode=start` resolves a new frozen
request; a full development partition matrix also runs the existing beta gates.
The legacy per-software workflows and PR validation remain on GitHub-hosted
runners. The proposed daily workflow uses this runner only after the migration
PR is merged; leave it unmerged until live validation passes.

The service applies the same unprivileged Landlock model as the CP2K tester:
system paths are read-only, writable paths stay within this account's CI tree,
TCP connections are limited to HTTPS and the SAI SSH port, and no listeners are
allowed. The runner cannot read personal SSH files or the CP2K account's files.
The runner service, including all worker, Python, Git and SSH descendants, has
`MemoryHigh=128M`, `MemoryMax=128M` and `MemorySwapMax=0`. .NET uses workstation
GC with a 24 MiB heap limit per process and aggressive memory conservation;
tiered PGO and diagnostics are disabled. The public checkout uses no Node
actions. A 32 MiB Node old-space limit remains as a fallback, and Go has a
16 MiB soft memory limit. Native mappings and allocator overhead still count
against the whole cgroup limit; these heap settings are not RSS guarantees.
`OOMScoreAdjust=1000` makes this service a preferred OOM victim, and an OOM
stops its entire process group. Failure restarts wait five minutes and are
limited to two starts per half hour. Source preparation is serial; Slurm
computation is parallel. Git uses one packing thread and 4 MiB delta caches,
source validation discards object-list output, and source splitting streams
1 MiB blocks. The relay uploads one source part at a time. A first full-history
fetch remains materially heavier than an incremental update or cache hit.

The one-shot `ExecCondition` requires `MemAvailable >= 192 MiB` (128 MiB runner
budget plus 64 MiB host headroom) and memory PSI `some avg10 <= 5%`. Unreadable
metrics also prevent startup. Insufficient capacity skips startup without
launching a listener or entering a restart loop. This check is not a memory
reservation and cannot predict another service's future peak; keep the runner
disabled on the current host. It adds no resident monitor. Do not work around a
failed check by raising limits or enabling swap on this shared VPS.

On 2026-11-06 the new service reached 192.2 MiB and the existing CP2K service
reported an OOM kill. After stopping the new runner, available memory recovered
to 197–210 MiB and memory PSI `some avg10` fell from 88.94% to 0.01%. The smoke
test passed, but the three-partition GPUMD relay test was stopped before live
validation completed. A later optimized 128 MiB smoke passed in 18 seconds,
but real GPUMD source preparation lowered host available memory to 58.5 MiB
and the test observer stopped it. A direct SSH/Python control reduced upload
memory to about 43 MiB but then hit a 96 MiB cgroup OOM during the first Git
fetch. Neither experiment accepted or published a build. Keep the existing
hosted daily workflow until the full source/prepare/resume path and peak
overlap with the CP2K service are qualified. See
`docs/evidence/self-hosted-relay-20261106/README.md` for exact runs and limits.

A separate [VPS dispatch probe on 2026-11-08](../../docs/evidence/vps-dispatch-20261108/README.md)
passed using a short API request: HTTP 204 in 1.674 seconds, a 10.004 MiB
measured cgroup peak including its measurement wrapper, and a successful
hosted workflow reaching SAI's release bookkeeping. This provides a lightweight
way to bypass GitHub schedule delays while retaining hosted orchestration.
The probe installed no timer and does not qualify the full runner above.
