# Private-memory beta runtime acceptance — 2026-10-25

All 17 application/partition combinations passed with runtime
`a4c6a24c229f7a696b202fb1bfc4b18045a60e93f7bc7477cb7d1d911100c611`.
The application SIFs and their 20261024 development-source snapshots are
unchanged. The tested catalog SHA256 is
`55c54d87653e79cbaec6d6f1dda2a3867776d9029c05666ce2f8734c15391454`.

| Partition | Job | Coverage | Result |
| --- | --- | --- | --- |
| 16V100 | 1924611 | Two nodes, two host-MPI ranks, one GPU/node; five applications | Passed, 78 seconds |
| 4V100 | 1924610 | Two host-MPI ranks, two GPUs; five applications | Passed, 75 seconds |
| 8V100V0 | 1924612 | Two host-MPI ranks, two GPUs; five applications | Passed, 151 seconds |
| DSPRHBM | 1924601 | Two host-MPI ranks, two CPUs/rank; CP2K and ABACUS | Passed, 38 seconds |

These jobs unset caller scratch variables and use the existing site GPU mapping
helper with one rank/GPU. No user sbatch template is generated. Each report
binds the exact catalog and runtime hash, checks numerical results, and verifies
the private-memory marker, real UID, host MPI prefix, `--sharens`, GPU `--nv`,
and one full image hash per job/node. The GPUMD small fixture uses one GPU; the
DeePMD inference check measures CUDA allocation. Neither is forced onto CPU.
The GPU MPI allocation still exposes both GPUs to CP2K/ABACUS/LAMMPS.

CP2K energy differences are below `4e-13` Ha and force differences at most
`1e-12` Ha/bohr. ABACUS energy differences are below `8e-9` eV. LAMMPS and
GPUMD differences are zero in these fixtures; DeePMD GPU inference differences
are at floating-point roundoff. Reports and raw outputs are in `raw/science-r7`.
These short checks establish correctness and launch behavior, not general
performance or a speedup over a native installation.

Single-GPU DeePMD `se_e2_a` training passed again in **1924667**, with two finite
loss steps and a checkpoint, in 16 seconds. This does not accept multi-GPU
training: the previously recorded unused-parameter and Triton/runtime failures
remain outside this publication's scope.

## Actual concurrent epilog and teardown

| Partition | Survivor | Peer | Later teardown observer |
| --- | --- | --- | --- |
| 16V100 | 1924723 | 1924724 | 1924749 |
| DSPRHBM | 1924725 | 1924726 | 1924750 |

Each pair ran simultaneously under the same UID on the same node. The survivor
created markers in private `/dev/shm`, `/tmp`, and its MPI scratch, performed a
CP2K calculation, and waited. The peer waited until those markers and the first
calculation were ready, ran host-MPI CP2K, then exited normally. The controller
confirmed the peer was COMPLETED with exit 0 and absent from squeue before
releasing the survivor. The survivor retained every marker and repeated the
calculation with matching energy/forces. Its effective capabilities were zero.

Read-only SSH inspection from the physical node namespace could not see the
private markers. The site disallows SSH after the last allocation ends, so
separate short observer allocations on the same nodes checked teardown: no
process retained the recorded mount namespace and no marker appeared on the
host. Both observers completed successfully. Raw reports, scripts, before/after
science and observer receipts are in `raw/concurrent-r7b`.

## Runtime and publication

Ordinary `mpirun`/`mpiexec` entry wrappers prepare an unprivileged user/mount
namespace and exec the accepted physical host MPI; remote `prted` does the same.
The runtime preserves UID and drops setup capabilities before MPI/application
execution. A fresh private tmpfs backs `/dev/shm`, `/tmp`, and `/var/tmp`, so the
site epilog's global UID-based deletion cannot reach another job's state.
The calling shell's TMPDIR remains unchanged. Input and output stay in the
user's project directory.

4V100 and DSPRHBM enforce AppArmor restrictions on plain unshare. They use the
installed Apptainer profile to bootstrap the same private-memory setup, with
host executables, libraries, MPI devices and NSS account lookup. The independent
mountpoint-only SIF is **36 KiB** with SHA256
`eb467d87aa7a41ab80f982929d23a1fff1d59e1625fae57e62fba03fc643c7e2`.
It is unrelated to any application's SIF. A bounded checksum index and small
bootstrap configuration live in `/run/lock` tmpfs, outside the site epilog;
application scratch never uses that small filesystem. Tmpfs remains subject
to the node's existing swap policy.

No software tree is extracted and no entire application SIF is copied to RAM.
Startup still incurs namespace/container setup and one full SHA256 image read
per job/node. The public runtime at
`/home/stardust/sai-hpc-software/sif-beta/runtimes/20261025-a4c6a24c229f`
contains **112,992 bytes** of launchers, metadata, environments and the bootstrap;
it refers to the existing public application SIF files.

Publication **1924922** completed in 47 seconds. Before changing aliases it
rechecked all four scientific jobs, both concurrency/teardown pairs, training,
image hashes and relocated catalog equivalence. Actual Lmod checks exercised
login refusal with full help, loading/unloading, rotation and rollback. It then
selected `20261025-memory-r1` and rechecked the complete public payload.
The five home `*-beta-later` links now point to fixed names ending in
`-ra4c6a24c229f`; previous fixed versions and generations were preserved.

The administrator installer passed `--check` and installation into a private
test module root. Real Lmod tested those exact static gateways against the
current home aliases; **1925077** loaded/unloaded all five through that path and
ran CP2K/LAMMPS/DeePMD startup checks in 34 seconds. Public mode/ACL checks confirm
readable files and traversable ancestors. **No `/opt` gateway was installed by
this work.** The administrator runs:

```bash
cd /home/stardust/sai-hpc-software/deployment/sif-beta
bash install.sh --check
bash install.sh --install
```

Only the five static module forwarders go into `/opt`. There are no new `/opt`
application copies, tests or sbatch templates. `module --ignore_cache avail
beta-later` discovers the new names after installation; subsequent rotations
only change the home aliases and their metadata.

## Retained failed attempts and checks

- r1 exposed incompatible simultaneous PRRTE global/local/remote tmpdir options;
  r2 retained only the global option. Native-node r2 checks passed, but the
  restricted nodes rejected plain unshare.
- r3/r4 exposed truncated tiny SIF partitions during file-to-file packaging on
  project storage. Piping the squashfs into `sif add`, followed by byte-for-byte
  partition verification, produced the complete image. The checked-in builder
  was exercised successfully on project storage without physical `/tmp`.
- r3's 16V100 request accidentally retained four expected ranks for a two-rank
  allocation; the verifier correctly rejected it. Later requests fixed this
  before submission; the failed result was not relabeled.
- r5 lost the host command search path across the bootstrap; r6 lacked the host
  NSS socket needed for nested Apptainer account lookup. r7 preserves both and
  passes the complete matrix.
- The first native concurrency attempt landed on a node whose remaining GPUs
  were occupied. Its pending peer and waiting survivor were canceled, and a
  coordinated pair was submitted together on a node with space for both.
- A publication preparation on the login node stopped before selection when
  the compute check required a Slurm allocation. The final driver now checks
  the allocation before mutation and can resume an unchanged prepared
  generation. Job 1924922 completed that exact generation's checks and selection.

Archived receipts preserve unsuccessful jobs and canceled attempts. Local
focused verification passes 25 tests. The initial PR revision passed all
1,082 repository tests (2 skipped) in GitHub CI; the final CI receipt is tracked
with the PR. `SHA256.json` covers all archived raw files.
