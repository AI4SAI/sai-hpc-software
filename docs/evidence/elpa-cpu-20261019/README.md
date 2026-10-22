# ELPA CPU supplement: explicit C API works without a CUDA driver

CPU build/acceptance job `1848059` completed `0:0` on `d9470n02` (DSPRHBM) in
2:04. The final SIF passed all 24 solves through the original
`elpa_eigenvectors_double` and `elpa_eigenvectors_double_complex` entry points.
No host-array aliases or replacement libraries were used. These are the same
128-by-128 analytic Hermitian matrices used for the prior NVIDIA builds, with
ELPA1/ELPA2, real/complex, one/two MPI ranks, two repeats and ELPA2 nonblocking
options. Fortran header/module compilation and init/uninit also passed.

Maximum eigenvalue error: `1.332e-15`; residual: `5.477e-16`; orthogonality:
`1.665e-15`. All are below the `1e-10` threshold. ELF dynamic records and `ldd`
contain no CUDA, cuBLAS or cuSOLVER dependency; dynamic symbols contain neither
`is_device_ptr` nor `cudaPointerGetAttributes`. The CPU node has no CUDA driver,
so this directly covers the previous failing environment.

The build retains GCC/GFortran 13.3.0, OpenMPI 5.0.10, OpenBLAS 0.3.32,
ScaLAPACK 2.2.3, OpenMP and the original ELPA 2026.02.001 archive. CPU tuning is
`-march=x86-64-v4 -mtune=sapphirerapids`. NVIDIA/AMD/SYCL kernels, CUDA-aware MPI,
cuSOLVER and CUB are disabled for this target. The original shared MPI remains a
site dependency; its optional CUDA plugins can report a missing driver without
preventing these CPU solves. HCOLL loads at its actual HPCX path.

The build, installation and SIF validation use the final physical prefix:

```text
/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx512/cpu
```

The accepted SIF is:

```text
/home/stardust/sai-hpc-software/runs/elpa-2026.02.001-20261019-001258-dsprhbm/elpa-dsprhbm.sif
SHA256 e4599af8bef108552dc5fe71aa12734d6dbc86dfd039f010a36cf9bc180bf33c
```

The new module `elpa/2026.02.001-2603-gnu-partition` passed these checks:

| Partition | Job | Node | ISA / backend | Execution |
| --- | --- | --- | --- | --- |
| 16V100 | 1848160 | 16v100n02 | AVX2 / NVIDIA | Existing physical installation |
| 4V100 | 1848161 | 4v100n04 | AVX512 / NVIDIA | Existing physical installation |
| 8V100V0 | 1848162 | 8v100v0n09 | AVX2 / NVIDIA | Existing physical installation |
| DSPRHBM | 1848163 | d9470n02 | AVX512 / CPU | Final CPU SIF |

All four jobs completed `0:0`. Each loaded the exact candidate module, verified
matching ELPA/MPI/BLAS roots, compiled a fresh C consumer and passed two repeated
complex ELPA2 solves with two MPI ranks and the nonblocking option. Loader traces
from both ranks prove the selected library was actually initialized. The module
then unloaded successfully after changing the current partition environment,
confirming it uses the saved load identity. Selection requires a Slurm allocation
and rejects a partition/CPU-ISA mismatch. The original GPU `-auto` module remains
available with its original mapping.

The administrator supplement at
`/home/stardust/sai-hpc-software/deployment/elpa-2026.02.001-cpu` was extracted from
the accepted SIF. Its roughly 12.81 MiB inventoried payload and both existing GPU
installations passed preflight as `stardust`. Other accounts can read/traverse
the package without receiving write access. The selective installer adds only
the CPU prefix and new module; it does not replace the NVIDIA installations.
Instructions and pinned SIF/manifest/module hashes are in
[the deployment package](../../../deployment/elpa-2026.02.001-cpu/README.md).

Raw CPU results are in `cpu/`, and module results, allocation records, loader
traces and frozen controls are in `partition-module/`. Build accounting and
stdout are adjacent. The release preparer rechecks job completion, the SIF hash,
all numerical case identities/errors, the tested module hash and both rank
loader traces before exporting. Its `--verify-only` option repeats these checks
without extraction. [Independent revalidation](revalidation.json) and
[raw evidence hashes](evidence-sha256.json) accompany this report.

The new CPU prefix has not yet been installed on physical `/opt`; that step is
reserved for the administrator, followed by native CPU acceptance. These small
single-node checks do not establish large-system performance or multi-node scaling.
