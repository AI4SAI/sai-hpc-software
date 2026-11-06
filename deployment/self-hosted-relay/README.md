# SAI daily build relay

The `sai` account on DO2 runs one repository-scoped GitHub Actions runner,
`do2-sai-hpc`, with label `sai-hpc-relay`. The separate `cp2k` account and its
tester runner are not part of this deployment. Compilation and scientific
acceptance still run in Slurm under `/org/org-open-sai/proj-sai-hpc-software`.

The daily workflow starts at 03:00 Asia/Shanghai. The hourly `:17` trigger only
resumes unfinished generations; it never resolves a new source or starts a new
daily build. Separate Actions concurrency groups preserve a queued nightly
request. A filesystem lock serializes the one relay's durable state.

## Runtime and files

```text
/home/sai/ci/
  runner/                  # official Actions runner 2.338.2
  bin/gh                   # official GitHub CLI 2.103.3
  deployment/sandbox.py    # Landlock entrypoint
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
the two pinned upstream binaries and installs the user service. On first
installation, supply a short-lived repository registration token on stdin:

```sh
gh api --method POST repos/AI4SAI/sai-hpc-software/actions/runners/registration-token --jq .token |
  ssh -l sai DO2-cp2k 'bash /path/to/install-user.sh'
```

The installer requires its adjacent `sandbox.py` and service file. It uses no
root installation. Enable `loginctl enable-linger sai` once if needed so logout
does not stop the service. Keep the existing `hpc` Environment secrets
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
The legacy per-software workflows remain available on GitHub-hosted runners;
PR validation also stays hosted. Only scheduled and manually dispatched daily
jobs use this runner.

The service applies the same unprivileged Landlock model as the CP2K tester:
system paths are read-only, writable paths stay within this account's CI tree,
TCP connections are limited to HTTPS and the SAI SSH port, and no listeners are
allowed. The runner cannot read personal SSH files or the CP2K account's files.
On this 512 MiB VPS, .NET uses workstation GC with a 96 MiB per-process heap
limit. Source preparation is serial; Slurm computation is parallel.
