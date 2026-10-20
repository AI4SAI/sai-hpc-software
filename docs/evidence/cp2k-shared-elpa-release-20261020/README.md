# CP2K 2026.2 with repaired shared ELPA: accepted release

[Workflow 39365139034](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39365139034)
compiled all four partitions from controller `13e1a7e`, source
`67b5da876dd6a76b8b021d5a04d1c81ba79a4c50` and recipe
`aed7aa53ff436310f28e5fdc21e1c3ca211f2422c5dd6abe44b19aa1d3ae9277`.
All four SIFs passed artifact verification, numerical acceptance and native
export readback, and are published in the user's delivery tree.
The entire GitHub workflow concluded successfully; its completed jobs and
timestamps are preserved in [`workflow.json`](workflow.json).

| Partition | Application job | Science job | Calculations |
| --- | --- | --- | ---: |
| 4V100 | 1869890 | 1870420 | 14 |
| 16V100 | 1870431 | 1870898 | 14 |
| 8V100V0 | 1870216 | 1871559 | 14 |
| DSPRHBM | 1870183 | 1870992 | 14 |

All eight jobs ended `COMPLETED 0:0`. Each partition ran the same seven cases
on one and two nodes, including PBE OT, diagonalization, actual ELPA dispatch,
64-water ELPA/OT consistency, GFN1 and D4. The frozen controller independently
revalidated all 56 calculations, input hashes, launcher identity and rank
placement. After download, a separate local reparse reproduced every recorded
numerical sample and energy/force comparison.

GPU builds retain GCC 14.3, V100 SM70, cuSOLVERMp/NCCL and SpLA GEMM offload.
They now use the physically accepted shared ELPA 2026.02.001 GPU libraries
with the communicator repair and host-staged back-transformation workaround.
DSPRHBM uses the pinned shared CPU library. The failed older GPU deliveries
have not been relabelled.

The new install prefix, identical inside and outside the container, is:

```text
/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-raed7aa53ff43/<partition>
```

The SIFs and checked `.native` exports are under:

```text
/home/stardust/sai-hpc-software/containers/software/cp2k/release/v2026.2-g67b5da876dd6-raed7aa53ff43/<partition>/
```

This does not establish physical installation of the new CP2K build. At the
14:47 Asia/Shanghai check, its public `/opt` directory did not exist. The public
`cp2k/2026.2-auto` deployment and existing installer still refer to the earlier
accepted private-ELPA release. Updating the administrator handoff and verifying
the new physical installation remain separate work.

## 216-water tuning on 4V100

Job `1870680` completed in 50m52s on `4v100n25`. The experiment uses 216 periodic
waters, 4968 basis functions, PBE/DZVP-MOLOPT-SR-GTH, a 300 Ry cutoff and
`EPS_SCF 1e-9`, with one MPI rank, one V100 and four physical CPU cores.
Each mode has one warmup and three alternating measured repetitions.

| Mode | CP2K internal total time, median |
| --- | ---: |
| cuSOLVERMp, direct FALSE | 123.823 s |
| cuSOLVERMp, direct TRUE | 111.638 s |
| Shared ELPA | 143.875 s |
| PBE OT, FULL_ALL with cuSOLVER | 368.940 s |

Direct reduced total time by **9.84%** (1.1091x standard/direct). All 16 runs
passed convergence, actual backend dispatch, affinity and numerical checks.
Maximum differences from the warmup reference were `1.192e-10 Ha` in energy
and `3.045e-9 Ha/Bohr` in force. The remote frozen verifier and independent
local verification both passed.

This result supports testing direct on larger diagonalization inputs. It does
not change the upstream default, qualify all large systems, or measure
multi-GPU scaling. OT uses a different minimization algorithm; this timing is
specific to the recorded input and preconditioner. The earlier 1.2% pilot was
64 waters on 16V100 with a different accepted ELPA build, so it is not a
controlled size-only comparison.

At 14:47 Asia/Shanghai, the same 216-water protocol was still running on
16V100 (`1871044`, nine validated samples) and 8V100V0 (`1872055`, one
validated sample). Their final medians are not reported here.

## Evidence

The archive contains 1,327 files. All 1,326 entries in
[`raw/SHA256.json`](raw/SHA256.json) were independently hashed after download.
[`archive.json`](archive.json) pins the 9,524,193-byte archive and its source
under the cluster home directory. Raw science inputs, outputs, forces, MPI
traces, Slurm accounting, artifact manifests and the full completed 4V100
tuning experiment are retained. No SIF or installation binaries are added to
Git.

See the [remote acceptance summary](raw/summary.json) and
[independent local validation](local-validation.json). The latter reparses
56 scientific calculations and all 16 completed tuning calculations.
