# CP2K 2026.2 with shared ELPA: physical installation accepted

The administrator installed release `v2026.2-g67b5da876dd6-raed7aa53ff43`
and updated the public `cp2k/2026.2-auto` module. The ordinary `stardust` account
verified installed bytes, links, permissions, the module and all three shared
ELPA variants, then ran **56 successful calculations** using the physical
`/opt` installation. No application container was used.

| Partition | Job | Compute nodes | Elapsed | Calculations |
|---|---|---|---|---:|
| 4V100 | 1879431 | `4v100n[02-03]` | 7m37s | 14/14 |
| 16V100 | 1879432 | `16v100n[13-14]` | 14m35s | 14/14 |
| 8V100V0 | 1879433 | `8v100v0n[04-05]` | 15m13s | 14/14 |
| DSPRHBM | 1879434 | `d9470n[03,05]` | 17m02s | 14/14 |

All four jobs ended `COMPLETED 0:0`. Each ran the original seven accepted
inputs on one and two nodes: PBE OT, standard diagonalization, forced ELPA,
DFT-D4, TBLITE/GFN1, 64-water PBE OT and 64-water ELPA. The original installed
CP2K data were used; no basis/potential files were copied beside the inputs.
All 280 original data files and two links per partition match their receipts.

Independent remote and local revalidation reproduced every numerical sample
from raw outputs and forces. Maximum differences from the accepted SIF were
`2.87e-11 Ha` and `1.30e-11 Ha/Bohr`, below the fixed `1e-6 Ha` and
`1e-5 Ha/Bohr` limits. Single/two-node results also agree. These calculations
establish the recorded scientific coverage; timings are descriptive and do
not measure production scaling or every workload.

The exact version is `2026.2`, revision `67b5da876d`. GPU feature lists retain
CUDA offload, DBCSR, cuSOLVERMp/NCCL and SpLA GEMM offload. Actual loader traces
on both nodes show ELPA from the accepted shared prefixes:

| Partition | Shared ELPA prefix |
|---|---|
| 4V100 | `/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx512/nvidia` |
| 16V100, 8V100V0 | `/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx2/nvidia` |
| DSPRHBM | `/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx512/cpu` |

There are no missing `ldd` dependencies. HCOLL loads from its real NVIDIA
HPCX 2.25.1 installation, without the `/opt/mellanox/hcoll` alias. MPI rank
records prove distinct hosts, the expected physical executable and allocated
GPU visibility. Scratch and results remain under the home directory; PRTE's
shared-filesystem advisory is expected under this storage policy.

The install path, identical to the build's compiled prefix, is:

```text
/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/<partition>
```

Inside a Slurm allocation:

```bash
module use /opt/modules/modulefiles/apps
module load cp2k/2026.2-auto
command -v cp2k.psmp
# Use the MPI rank/thread layout requested by your job:
mpirun ... cp2k.psmp -i input.inp
```

The public module SHA256 is
`48f0471fd950fa954b040e4766bd3d3f7cf5a89a1f9799cc6a43ad37b31746d9`.
The old installation is retained for rollback. The four-node-family deployment
is complete; this does not change development-branch acceptance.

## Reproducible evidence

The entire submission package was frozen before testing. For the queued 4V100
job only, QoS was changed from `flood-1o2gpu` to the account's authorized
`rush-1o2gpu`, and its time limit from 40 to 25 minutes. Inputs and node/GPU
requests were unchanged. [Before/after scheduling records](raw/scheduling-adjustment.json)
and [frozen submission hashes](raw/submission.json) retain the distinction.

- [Installed-file verification and submission log](raw/submit.log).
- [Terminal Slurm accounting](raw/accounting.txt).
- [Remote independent revalidation](remote-validation.json).
- [Local independent revalidation](local-validation.json).
- [All 525 archived-file hashes](raw/SHA256.json).
- [Source archive hash and location](archive.json): 2,096,996 bytes.

Raw logs, inputs, forces, rank/loader traces, installed receipts/data hashes,
accepted SIF reference metadata and frozen parser sources are retained under
`raw/`. No binaries or SIFs are added to Git. The original run remains at
`/home/stardust/sai-hpc-software/runtime-tests/cp2k-2026.2-physical-20261020-191622-447975`.

[`collect.py`](collect.py) documents the read-only collection from that run and
the physical installation. After downloading, reproduce the independent
offline check with `python3 -B revalidate.py` in this directory.
