# ELPA auto module: physical installation accepted on all four partitions

After the administrator installed the CPU supplement and replaced the existing
`elpa/2026.02.001-2603-gnu-auto` module, `stardust` verified the installed files
and ran these native compute jobs. All completed `0:0`.

| Partition | Job | Node | Backend | Numerical solves | Elapsed |
| --- | --- | --- | --- | --- | --- |
| 16V100 | 1861947 | 16v100n07 | AVX2 / NVIDIA | 48 | 1:00 |
| 4V100 | 1861948 | 4v100n03 | AVX512 / NVIDIA | 48 | 0:20 |
| 8V100V0 | 1861949 | 8v100v0n02 | AVX2 / NVIDIA | 48 | 0:44 |
| DSPRHBM | 1861950 | d9470n02 | AVX512 / CPU | 24 | 0:15 |

All 168 solves passed. Each partition compiled a fresh C consumer using the
original `elpa_eigenvectors_double` / `elpa_eigenvectors_double_complex` entry
points and the installed headers/libraries. Tests cover 128-by-128 analytic
real/complex Hermitian matrices, ELPA1/ELPA2, one/two MPI ranks, two repeated
solves and ELPA2 nonblocking options. V100 partitions run both CPU and GPU
solves; DSPRHBM runs CPU solves. No host-array aliases were substituted.
Fresh Fortran consumers also compiled and passed MPI/ELPA initialization.

The maximum spectrum error was `2.22e-15`, residual `6.46e-16`, and orthogonality
error `1.665e-15`, all below `1e-10`. DSPRHBM's respective maxima were
`1.332e-15`, `5.477e-16`, and `1.665e-15`. This verifies the installed CPU
library fixes the original explicit-C-interface failure without a CUDA driver.
Its ELF dependencies contain no CUDA/cuBLAS/cuSOLVER libraries and its symbols
contain neither `is_device_ptr` nor `cudaPointerGetAttributes`.

Every job verified all three physical library inventories and the upgraded auto
module, loaded that exact module from `/opt/modules/modulefiles/devtools/elpa`,
and recorded both MPI ranks initializing the expected ELPA, site OpenMPI,
OpenBLAS and actual HPCX HCOLL libraries. No `/opt/mellanox/hcoll` runtime alias
was used. The module unloaded successfully even after the current partition
variable was changed. The original module backup also matches the pinned prior
checksum, as recorded in `installed-module.json`.

All application execution used physical `/opt`; Apptainer was not invoked.
Results, compilation scratch and MPI/CUDA caches were stored under the invoking
account's home. The Slurm site's own MPS pipe location is managed separately.
These are small single-node correctness checks, not a large-system performance
or multi-node scaling measurement.

Raw results and frozen scripts are alongside this report. The original run is:

```text
/home/stardust/sai-hpc-software/runtime-tests/elpa-auto-physical-20261019-210611-238697
```

[Independent revalidation](physical-native-acceptance.json) pins the release,
all four Slurm completions, exact C API/case identities, physical module and
library paths, and hashes 205 raw files. The prior GPU-only, host-array diagnostic
and container results remain preserved in their original evidence directories.
To repeat validation from the repository:

```bash
python3 deployment/elpa-2026.02.001/verify-native-results.py \
  docs/evidence/elpa-auto-native-20261019 \
  --release deployment/elpa-2026.02.001-cpu/release.json
```

Submit fresh physical checks with
`bash deployment/elpa-2026.02.001-cpu/submit-native-checks.sh all`.
Installation and usage instructions are in the
[CPU supplement](../../../deployment/elpa-2026.02.001-cpu/README.md).
