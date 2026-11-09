# VM relay deployment and end-to-end validation, 2026-11-10

`vm-sai-hpc` completed the full GPUMD workflow on 4V100, 16V100 and 8V100V0:
builds, GPU scientific acceptance, SIF publication, Lmod runtime checks and beta
promotion all passed. Its Linux user service is enabled and recovered from a
deliberate idle-process crash. GitHub confirmed the restarted runner online and
idle. The migration PR remains separate from the main daily workflow pending
the Windows unattended-startup step below.

Windows login startup is configured. Unattended startup before Windows login
still requires the prepared Administrator PowerShell installer: the current
Windows token cannot register a boot/S4U scheduled task (`Access is denied`).
No Windows reboot qualification has been claimed.

## Executed workflows

- [Smoke 42663533690](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42663533690),
  controller `3ce5426aa773194fe0c0f049558fa9f350134acf`: succeeded on the VM,
  including real GitHub checkout, temporary credentials, SAI project access,
  Slurm command discovery and credential cleanup. It allocated no compute node.
- [Initial real run 42664109758](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42664109758),
  controller `f854c0c9ed7b5fbfdd8c93be1a458e4f33c050f3`: failed during local
  controller snapshot extraction, before a plan or any Slurm submission.
- [Real run 42666380120](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42666380120),
  controller `8c647277af6d34225ec6291617fdd0e5df4d04d0`: succeeded across all
  three partitions and published the complete beta generation. Runner identity
  is `vm-sai-hpc`, job `129331016236`. The job ran from 01:37:12 to 02:06:27
  Asia/Shanghai on November 10 (29m15s, including scheduler waits).

The real run resolves GPUMD master to
`8bf530101c6a6a5946192629c059e8c4761f19d2`. SAI already had its parent
`9cbf049b24fd3827f3a3f052e2ffffe38b2bd609`, so the first target transferred an
8441-byte compressed Git delta; the other targets reused the verified remote
source. Local preparation still fetched full ancestry. All compilation and
scientific work ran in the funded SAI project.

| Partition | Build job / elapsed | Scientific job / elapsed | Beta runtime job / elapsed |
|---|---|---|---|
| 4V100 | 2331843 / 1m44s | 2331899 / 1m33s | 2332400 / 2s |
| 16V100 | 2331856 / 3m03s | 2331954 / 1m54s | 2332398 / 5s |
| 8V100V0 | 2331871 / 4m14s | 2332336 / 2m08s | 2332402 / 10s |

All nine Slurm jobs finished `COMPLETED`, exit `0:0`. The three accepted SIFs
have `build_verified=true`, `verified=true` and
`published=true`. Their static reference energy/force errors are zero; the
existing NEP/gNEP prediction/training, JIT, PLUMED and installed-dependency
checks also passed. This is the repository's representative acceptance, not
an exhaustive qualification of every application feature or software package.

- 4V100: 22,249,472 bytes, SHA-256
  `cc91a7cb1ce7e3994f13373c316cca3a728af7bdcbc5abb868b1e745bb94ddce`.
- 16V100: 22,228,992 bytes, SHA-256
  `85eef638131b97fa7e21bbf5e1a182e801d2bce51357e4628ee27295883ac6d7`.
- 8V100V0: 22,249,472 bytes, SHA-256
  `4e9e1b26a6d3e01a9c553b285ef1a9d2d355cc50f1cfbceae54400492040f364`.

Exact artifact paths, identities, manifests and acceptance receipts are in
[`raw/slurm-artifacts.json`](raw/slurm-artifacts.json), scheduler records in
[`raw/accounting.txt`](raw/accounting.txt), and frozen task states in
[`raw/plan-state.json`](raw/plan-state.json). All four planned tasks and the
generation finished `succeeded`; the completed GitHub job and cleanup steps are
recorded in [`raw/workflow-result.json`](raw/workflow-result.json).

All three Lmod/SIF checks passed with `automatic_memory=true` and zero static
energy/force errors. The public alias `gpumd/master-beta-later` now selects
`master-beta-20261109-8bf530101c6a-r4d85319d4295-b00ef7a1240f5`, using bundle
`/home/stardust/sai-hpc-software/sif-beta/runtimes/daily-20261109-42666380120-1-gpumd`.
The generation date uses UTC; this run occurred on November 10 in China.
See [`raw/beta-validation.json`](raw/beta-validation.json) and
[`raw/beta-publication.json`](raw/beta-publication.json).

## Resource and persistence controls

The VM reports eight logical CPUs and 15,951 MiB WSL memory. The dedicated
Linux `sai` account has no sudo or Docker group membership; lingering is enabled.
The service uses a 2 GiB hard memory limit, 1 GiB high threshold, zero swap,
and two CPUs of quota. The measured cgroup peak is **739,602,432 bytes
(705.34 MiB)**, including all runner/worker/Python/Git/SSH descendants and
charged file cache. No high/max/OOM event occurred in the successful run.
Minimum sampled host available memory was 15,240,925,184 bytes (14.19 GiB).

Sampling every five seconds ran outside the runner cgroup and is now stopped.
The kernel cgroup peak captures intervening peaks; sampled host availability
is only an observed minimum. See [`raw/e2e-memory-summary.json`](raw/e2e-memory-summary.json)
and [`raw/e2e-memory-20261110.jsonl`](raw/e2e-memory-20261110.jsonl).

`Restart=always` retries after one minute, without a permanent start-rate
lockout. `ExecStartPre` refuses to launch the listener below 3 GiB available
memory or under high memory pressure and lets systemd retry later. A deliberate
service stop remains a stop. Windows AC sleep/hibernate are disabled; secure
screen locking is separate. The running service matches the checked-in unit:
SHA-256 `d0c72da9d571b62304501f10fc00abde750a524653bb5a2bb5417ba6b4d725a6`.
Effective properties are in [`raw/service-state.txt`](raw/service-state.txt).

After the workflow finished and its temporary HPC key was removed, an idle
restart probe sent `SIGKILL` to service main PID 6577 at 02:08:45 Asia/Shanghai.
systemd restarted it as PID 10895 at 02:09:46; it was listening for jobs again
at 02:09:51. `NRestarts=1`, `ActiveState=active`, service enablement and user
lingering were read back, and GitHub reported the runner online and idle.
[`raw/idle-restart-probe.json`](raw/idle-restart-probe.json) retains the before/
after states and journal. Its new idle cgroup peak does not replace the
705.34 MiB peak measured over the completed workflow.

The Windows login fallback and detached WSL keepalive are active, but the
boot/S4U task is not registered yet; see
[`raw/windows-startup.json`](raw/windows-startup.json). The prepared installer
is already on the VM. Run this once in an Administrator PowerShell window as
the Windows account owning the Ubuntu distribution:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\SAI\runner-deployment\install-wsl-startup.ps1"
```

The remaining block is Windows task-registration permission, not a runner or
Slurm failure. Verify the task remains running and the runner stays online
after installation; physical reboot recovery has not been tested.

## Fixes qualified during deployment

1. The runner reloads its registration-time `.path`. The installer now records
   the CI tool directory explicitly so jobs find the pinned `gh`, without
   inheriting Windows or personal PATH entries.
2. systemd 259's `RestrictSUIDSGID` returns `ENOSYS` for `openat2`. Ubuntu
   26.04 tar uses that syscall for safe traversal and failed snapshot creation.
   A minimal tar extraction passed with Landlock alone and the explicit syscall
   denylist, and failed with `RestrictSUIDSGID` alone. The trace was:

   ```text
   mkdirat(4, "sub", 0755) = 0
   openat2(4, "sub/", {flags=O_RDONLY|O_NOFOLLOW|O_CLOEXEC|O_PATH|O_DIRECTORY,
                     resolve=RESOLVE_BENEATH}, 24) = -1 ENOSYS
   ```

   Removing that redundant setting fixed the real workflow. `NoNewPrivileges`,
   the dedicated account, Landlock and explicit syscall restrictions remain.
3. An interrupted preparation previously retried `scp` over an existing 0555
   launcher. Controller files now upload to a per-attempt staging directory,
   verify all digests before committing, and link into place without overwriting
   prior files. Matching files are reused; changed contents fail closed.
4. The relay branch incorporates current main, including LAMMPS standard
   ccache/host compiler changes and the accepted GPUMD NEP-format adaptation.

The full hosted validation at `f854c0c` passed **1320 tests, two skipped**
([run 42664008320](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42664008320)).
Later commits change only deployment service settings/documentation and a
capacity-check comment; capacity tests and live unit verification passed.
The new upload regression cases cover read-only retries, truncated uploads,
interrupted commits and changed preexisting files.
