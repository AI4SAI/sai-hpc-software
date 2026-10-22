# ELPA 2026.02.001: container and physical installation acceptance

Both final SIFs completed compilation and numerical acceptance on 2026-10-18.
The administrator handoff is at
`/home/stardust/sai-hpc-software/deployment/elpa-2026.02.001`.
The administrator subsequently installed the two physical prefixes and public
module. The native acceptance below reads that installation without modifying it.

| ISA | Build/acceptance job | Node | Elapsed | Module load/unload job | Solves |
| --- | --- | --- | --- | --- | --- |
| AVX2 | 1846755 | 16v100n10 | 2:59 | 1846787 | 48/48 |
| AVX512 | 1846756 | 4v100n27 | 1:30 | 1846788 | 48/48 |

All four jobs completed with exit `0:0`. Each SIF was tested without the build
installation bind or the historical HCOLL alias. Tests compile fresh C and
Fortran consumers against the installed headers/modules and library. Numerical
coverage includes real/complex Hermitian matrices, ELPA1/ELPA2, CPU/V100,
one/two MPI ranks, two OpenMP threads, repeated solves, and the ELPA2
nonblocking option. Maximum eigenvalue error was `2.22e-15`, residual
`6.46e-16`, and orthogonality error `1.665e-15`; the threshold was `1e-10`.
The actual deployment module also passed load and unload inside each SIF.

The 128-by-128 analytic tridiagonal matrices test numerical correctness, not
large-system performance or multi-node scaling. GPU execution was tested on
V100; sm75/80/86/89 images were checked for presence, not executed on those GPUs.

The build retains the site's ELPA version, GCC 13.3.0, OpenMPI 5.0.10,
CUDA 12.9.1 / NVHPC 26.3, OpenBLAS 0.3.32 and ScaLAPACK 2.2.3, plus OpenMP,
cuSOLVER, CUB, CUDA-aware MPI and all five GPU architectures. The new prefixes
are `/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-{avx2,avx512}/nvidia`,
identical in the SIFs and future physical installations. The automatic module
uses the site's VNNI criterion so its MPI/BLAS and ELPA ISA selections agree.

The small [compatibility patch](../../../deployment/elpa-2026.02.001/cuda-aware-cpu.patch)
addresses observed failures while keeping CUDA-aware GPU communication:

- CPU ELPA2 paths in `tridi_to_band` and `band_to_full` incorrectly passed
  uninitialized device-array descriptors to MPI. The trace captured an
  attempted allocation of `1125823790914056` bytes. CPU paths now use their
  existing host arrays for broadcasts and reductions.
- Site OpenMPI's `libnbc` faulted on a device-buffer reduction with two ranks.
  Three GPU call sites already waited immediately after starting a collective;
  they now use the equivalent blocking collective on the same device buffers.
  CPU nonblocking collectives are preserved and tested.

The source archive and patch checksums are recorded in
[release.json](../../../deployment/elpa-2026.02.001/release.json).
The original test failures are retained beside the successful raw logs so the
compatibility changes can be reviewed independently.

The handoff was exported from the accepted SIFs and checked against per-file
SHA256 manifests. It contains about 19.43 MiB (AVX2) and 20.56 MiB (AVX512),
including libraries, development headers/Fortran modules, pkg-config/libtool
metadata, kernel information tool, documentation and provenance. Source trees,
objects, test executables, caches and system dependencies are excluded.
The package is readable/traversable by other accounts, without granting write
permission. Administrator instructions are in the
[deployment README](../../../deployment/elpa-2026.02.001/README.md).

## Physical installation acceptance

Follow-up: the [October 19 CPU-only supplement](../elpa-cpu-20261019/README.md)
passes the original explicit C API without a CUDA driver. Its container/module
acceptance is complete; the new CPU prefix awaits administrator installation.
The results below retain the actual behavior of the original NVIDIA build.

The `stardust` account verified both installed file inventories, byte hashes,
symlinks, modes and the exact public module. It then loaded the module on each
compute node and compiled fresh consumers against physical `/opt`. Apptainer
was not used. All scratch, compiler and MPI/CUDA temporary files stayed in home.

| Partition | ISA | Job | Node | Result |
| --- | --- | --- | --- | --- |
| 16V100 | AVX2 | 1847651 | 16v100n17 | 48/48 CPU/GPU solves; COMPLETED 0:0 |
| 4V100 | AVX512 | 1847652 | 4v100n05 | 48/48 CPU/GPU solves; COMPLETED 0:0 |
| 8V100V0 | AVX2 | 1847653 | 8v100v0n04 | 48/48 CPU/GPU solves; COMPLETED 0:0 |
| DSPRHBM | AVX512 | 1847654 | d9470n02 | Explicit C pointer-detection API failed without CUDA driver |
| DSPRHBM | AVX512 | 1847682 | d9470n02 | Host-array C API: 24/24 CPU solves; COMPLETED 0:0 |

Each successful job also checked Fortran module compilation and init/uninit,
the CPU kernel list, public module load/unload and library resolution. Loader
initialization traces from both MPI processes prove that the selected physical
ELPA, matching MPI/BLAS and real HPCX HCOLL were loaded. No legacy
`/opt/mellanox/hcoll` library was initialized. Maximum eigenvalue, residual and
orthogonality errors across the successful jobs were `2.22e-15`, `6.46e-16`, and
`1.665e-15`, respectively, against a `1e-10` threshold.

The DSPRHBM failure is narrower than a general CPU-kernel failure. In this
NVIDIA-enabled ELPA release, `elpa_eigenvectors_double` and
`elpa_eigenvectors_double_complex` use `is_device_ptr` before dispatching to the
host-array solver. `src/GPU/CUDA/elpa_explicit_name_nvidia_gpu.cu:54` calls
`cudaPointerGetAttributes`; its error branch exits when no CUDA driver exists.
The C/C++ generic `elpa_eigenvectors(...)` instead dispatches directly to the
public host-array routines `elpa_eigenvectors_a_h_a_d` / `elpa_eigenvectors_a_h_a_dc`.
The diagnostic compiled the unchanged numerical test with aliases for those two
routines, and passed all 24 CPU cases. It changed no installed library and does
not repair or accept the failing explicit API. CPU-only applications must use a
compatible entry point or a future CPU build/driver-independent dispatch fix.
Full Fortran eigensolver application acceptance is not inferred from init/uninit.

[GPU-partition report and raw hashes](physical/physical-native-acceptance.json)
and [CPU host-array report and raw hashes](cpu-host-api/physical-native-acceptance.json)
were generated by
[`verify-native-results.py`](../../../deployment/elpa-2026.02.001/verify-native-results.py).
It independently checks Slurm completion, exact case identities, two repeats,
finite numerical errors, selected C API, module/ELPA identities and actual
loader paths. The failed DSPRHBM test is retained under `physical/DSPRHBM/`.
Raw logs preserve MPI's occasional NUL byte after its shared-filesystem warning;
the parser trims only a NUL prefix before a result line.

Original results remain at:

```text
/home/stardust/sai-hpc-software/runtime-tests/elpa-physical-20261018-234556-2719551
/home/stardust/sai-hpc-software/runtime-tests/elpa-physical-20261018-234810-2748000
```

Earlier runner attempts are retained in `runner-diagnostics.json`: `--export=NIL`
omitted the user home required by native OpenMPI; after preserving it, the first
single-rank requests did not provide two MPI slots. The final runner preserves
the account environment, resets compiler/loader paths, loads the public module,
and requests two tasks. These corrected runner failures are separate from the
remaining DSPRHBM explicit-API limitation. No large-system performance or
multi-node scaling claim is made by these small single-node checks.
