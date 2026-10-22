# CP2K 2026.2: controlled OT backend comparison

All three previously submitted jobs completed successfully. The remote frozen
verifier and a separate local reparse passed all **24 calculations**, including
convergence, energies, forces, actual OT/backend dispatch, input hashes and
physical-core affinity. These use the accepted shared-ELPA release
`v2026.2-g67b5da876dd6-raed7aa53ff43` in its SIF; they are separate from the
[physical installation acceptance](../cp2k-shared-elpa-native-20261020/README.md).

Each experiment uses 216 periodic waters, PBE/DZVP-MOLOPT-SR-GTH, 300 Ry,
`EPS_SCF 1e-9`, OT/DIIS and `FULL_ALL`. Only `PREFERRED_DIAG_LIBRARY CUSOLVER`
versus `ELPA` differs. Direct generalized diagonalization is `FALSE` in both.
One V100 and one MPI rank are used, with four physical cores on 4V100/16V100
and three on 8V100V0. Each backend has one warmup and three alternating measured
repetitions. Times below are median CP2K internal total seconds.

| Partition / job | OT with cuSOLVERMp | OT with shared ELPA | ELPA total-time reduction |
|---|---:|---:|---:|
| 4V100 / 1877200 | 357.948 | 311.043 | 13.10% |
| 16V100 / 1877201 | 651.275 | 605.292 | 7.06% |
| 8V100V0 / 1877206 | 649.035 | 612.902 | 5.57% |

All jobs ended `COMPLETED 0:0`. The logs confirm the requested backend ran
without a different backend fallback or direct-generalized dispatch. For this
OT input, shared ELPA is faster on all three partitions. The earlier
[diagonalization comparison](../cp2k-water216-tuning-20261020/README.md)
favored cuSOLVERMp; solver selection should therefore be measured for the
actual algorithm, system and preconditioner. This does not select a universal
default or establish multi-GPU scaling. No CP2K defaults were changed.

[`local-validation.json`](local-validation.json) contains the independently
reparsed results. [`raw/summary.json`](raw/summary.json) is the remote summary.
All 722 entries in [`raw/SHA256.json`](raw/SHA256.json) were independently
hashed after download. [`archive.json`](archive.json) pins the 5,286,907-byte
archive retained below the cluster home. It includes frozen control scripts,
raw inputs/outputs/forces, GPU samples, MPI placement and Slurm accounting.
Reproduce the offline check with `python3 -B revalidate.py` in this directory.
