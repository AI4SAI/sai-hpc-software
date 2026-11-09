# VM relay deployment and end-to-end validation, 2026-11-10

Status at this checkpoint: `vm-sai-hpc` is online and its Linux user service is
enabled. GPUMD passed complete build, GPU scientific acceptance and accepted
SIF publication on 4V100 and 16V100. The 8V100V0 build is pending for resources,
so the complete three-partition workflow and beta promotion remain unfinished.
The migration PR remains separate from the main daily workflow.

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
  controller `8c647277af6d34225ec6291617fdd0e5df4d04d0`: the fixed service
  prepared and submitted all three partitions and independently accepted the
  two completed partitions. Runner identity is `vm-sai-hpc`, job `129331016236`.

The real run resolves GPUMD master to
`8bf530101c6a6a5946192629c059e8c4761f19d2`. SAI already had its parent
`9cbf049b24fd3827f3a3f052e2ffffe38b2bd609`, so the first target transferred an
8441-byte compressed Git delta; the other targets reused the verified remote
source. Local preparation still fetched full ancestry. All compilation and
scientific work ran in the funded SAI project.

| Partition | Build job / elapsed | Scientific job / elapsed | Result |
|---|---|---|---|
| 4V100 | 2331843 / 1m44s | 2331899 / 1m33s | Both COMPLETED, exit 0:0; SIF accepted and published |
| 16V100 | 2331856 / 3m03s | 2331954 / 1m54s | Both COMPLETED, exit 0:0; SIF accepted and published |
| 8V100V0 | 2331871 / pending | Not submitted yet | Scheduler reason Resources |

The two accepted SIFs have `build_verified=true`, `verified=true` and
`published=true`. Their static reference energy/force errors are zero; the
existing NEP/gNEP prediction/training, JIT, PLUMED and installed-dependency
checks also passed. This is the repository's representative acceptance, not
an exhaustive qualification of every application feature or software package.

- 4V100: 22,249,472 bytes, SHA-256
  `cc91a7cb1ce7e3994f13373c316cca3a728af7bdcbc5abb868b1e745bb94ddce`.
- 16V100: 22,228,992 bytes, SHA-256
  `85eef638131b97fa7e21bbf5e1a182e801d2bce51357e4628ee27295883ac6d7`.

Exact artifact paths, identities, manifests and acceptance receipts are in
[`raw/slurm-artifacts.json`](raw/slurm-artifacts.json), scheduler records in
[`raw/accounting.txt`](raw/accounting.txt), and frozen task states in
[`raw/plan-state.json`](raw/plan-state.json). Only the complete matrix can
advance the beta alias; two accepted SIFs do not imply completed beta promotion.

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
