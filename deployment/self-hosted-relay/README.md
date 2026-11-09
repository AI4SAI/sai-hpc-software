# SAI daily build relay

The relay runs the official GitHub Actions runner and source preparation on a
separate machine. Compilation, scientific acceptance and SIF storage remain
in Slurm under `/org/org-open-sai/proj-sai-hpc-software`.

The current deployment target is the `sai` account on `STARDUST-VM` (Ubuntu
26.04 in WSL2), runner name `vm-sai-hpc`, label `sai-hpc-relay`. The previous
`do2-sai-hpc` runner remains stopped and disabled: the shared 512 MiB VPS did
not pass full source preparation. The VM has about 16 GiB RAM available to
WSL and eight logical CPUs. Initial source preparation and runtime qualification
passed. The [live VM validation](../../docs/evidence/vm-self-hosted-relay-20261110/README.md)
records the complete GPUMD build/science/Lmod/beta workflow on 4V100, 16V100 and
8V100V0, recovery after a deliberate idle runner crash, and the installed
Windows boot/login task running WSL in session 0. The measured whole-service peak was
705.34 MiB during that workflow.

## Layout and resource limits

```text
/home/sai/ci/
  runner/                  # official Actions runner 2.338.2
  bin/gh                   # official GitHub CLI 2.103.3
  deployment/              # sandbox and capacity check
  work/                    # Actions checkout and ephemeral credentials
  tmp/                     # source-transfer scratch
  cache/repositories/      # complete Git ancestry, bounded at 1.5 GiB
  state/<origin-run-id>/
    plan.json              # frozen SHAs, controller and original attempt
    checkout/              # immutable controller/fixture snapshot
    tasks/<task-id>/
      checkpoint.json      # preparation, submit intent and Slurm receipts
      controller.log
      results/             # metadata and remote evidence paths
      beta-results/
/home/sai/.config/systemd/user/sai-hpc-runner.service
```

The service cgroup includes Listener, Worker, Python, Git, SSH and charged file
cache. It has `MemoryHigh=1G`, `MemoryMax=2G`, no swap, and a two-CPU quota.
Startup requires at least 3 GiB available memory and low memory pressure.
This is a one-shot capacity check, not a reservation or resident monitor.
Startup retries once per minute if capacity is insufficient or the runner
exits; it never launches a listener when the capacity check fails. A deliberate
`systemctl --user stop` still stops it without restarting.
Keep the old 512 MiB host disabled; these limits are for the larger VM.

Source preparation is serial, while submitted Slurm jobs run concurrently.
Git uses one packing thread and bounded delta caches; validation and bundle
splitting stream data. .NET uses workstation GC, a 24 MiB managed-heap limit
per process and conservation settings. Heap limits do not bound aggregate RSS.

Landlock restricts reads/writes to the runtime and CI tree, denies personal
home/SSH files and Windows mounts, and permits TCP connections only to HTTPS
and SAI port 12022. The Linux account has no sudo or Docker membership.
`NoNewPrivileges` prevents gaining privileges through setuid/setgid executables.
Do not add `RestrictSUIDSGID`: on systemd 259 it returns `ENOSYS` for `openat2`,
which Ubuntu 26.04's tar requires for safe archive path traversal. A minimal
trace reproduced that failure independently of Landlock and the syscall list.
No SIF, build tree or raw scientific output is copied to this machine.

## Install and start

Create the dedicated `sai` Linux account and enable user lingering. Copy the
files in this directory to a location readable by that account, then run the
installer as `sai` with `SAI_RUNNER_NAME=vm-sai-hpc`. For the first registration,
supply a short-lived repository registration token on stdin:

```sh
gh api --method POST repos/AI4SAI/sai-hpc-software/actions/runners/registration-token --jq .token |
  ssh <runner-host> 'SAI_RUNNER_NAME=vm-sai-hpc bash /path/to/install-user.sh'
```

The installer verifies pinned upstream archives and leaves the service stopped
and disabled. On a qualified host, start it and enable future Linux starts:

```sh
systemctl --user enable --now sai-hpc-runner.service
systemctl --user show sai-hpc-runner.service -p ActiveState -p MemoryPeak -p MemoryMax
journalctl --user -u sai-hpc-runner.service -n 40 --no-pager
```

Keep `REMOTE_USER` and `REMOTE_SSH_PRIVATE_KEY` in the GitHub `hpc` Environment.
The current job's key is written only to Actions temporary storage and removed
in an `always()` step. No persistent cluster private key is installed here.

## Windows and WSL startup

Linux lingering starts the runner whenever WSL boots. To also start WSL before
Windows login, run `install-wsl-startup.ps1` in an **Administrator PowerShell**
window as the Windows user owning the `Ubuntu` distribution. It registers a
passwordless S4U task at startup and login and starts a foreground WSL sleep
process to keep the distribution alive. The task has no execution time limit;
failures retry once per minute. Verify the task stays Running and the runner
is online. A configured trigger is not proof of a successful physical reboot.

A current-user login startup entry can provide a fallback without administrator
rights: run the same installer with `-LogonOnly`. It also starts a hidden WSL
keepalive client for the current session. It does not start WSL before login.
Do not describe that fallback
as unattended boot recovery. Windows may lock or switch off its display;
AC sleep/hibernate must remain disabled for continuous service.

## Daily operation and recovery

The daily workflow requests new work at 03:00 Asia/Shanghai. Its
hourly `:17` trigger only resumes unfinished generations, never a new daily
source resolution. Separate concurrency groups protect the queued nightly
request; a filesystem lock serializes durable relay state.

Dispatch `daily.yml` with `mode=smoke` to validate runner identity, cluster
credentials, project access and Slurm commands without allocating nodes.
Use `mode=start` for real builds and `mode=resume` after interrupted observation.
Selecting all development partitions also runs existing beta gates. PR and
legacy per-software workflows remain on GitHub-hosted runners. The complete
live GPUMD build and the Windows unattended-startup task have been verified
on the VM. Physical Windows reboot recovery has not been tested.

Plans retain the original source, controller and run identity across reruns,
midnight and service restarts. Before advancing, the relay checks Slurm
queue/accounting and work directories. Existing software controllers validate
artifacts and numerical evidence. Waiting is exit 75, never acceptance.
An observation timeout preserves jobs for recovery. Permanent build/science
failures block that software's beta while other software continues.

Controller upload stages complete, hash-verified files and atomically links
them into the per-run directory. Retries reuse matching read-only files and
reject changed contents. A lost submission reply is recoverable only with
matching complete request/script/job receipts; incomplete submission evidence
stops rather than guessing that another `sbatch` is safe.

Earlier VPS results are retained in
[`docs/evidence/self-hosted-relay-20261106`](../../docs/evidence/self-hosted-relay-20261106/README.md)
and the lightweight
[dispatch probe](../../docs/evidence/vps-dispatch-20261108/README.md).
