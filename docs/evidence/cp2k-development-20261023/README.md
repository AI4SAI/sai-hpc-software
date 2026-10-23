# CP2K development LibXC supplementary acceptance

The standard seven-case CP2K science gate uses built-in PBE and does not exercise
the private LibXC 7.1.2 library added for current development. This independent
check uses the **same sealed final SIF**, without rebuilding it, changing its
original request, or granting the normal science/publication receipt.

The reviewed source is `cfe5e5d87aaf302953f14eb43587bef2411a8fb2`.
`src/xc/xc_derivatives.F` dispatches `PBE` directly to the built-in implementation;
explicit `GGA_X_PBE` and `GGA_C_PBE` select LibXC. The supplementary water-dimer
ENERGY_FORCE input, coordinates, basis and pseudopotentials come from the same
pinned fixture as the normal science gate. SCF epsilon remains `1e-9`.

The thresholds below are fixed before any supplementary calculation is run:

| Comparison | Energy / Ha | Maximum force component / Ha Bohr⁻¹ |
|---|---:|---:|
| Built-in PBE versus LibXC HOST | `1e-6` | `1e-5` |
| LibXC HOST versus LibXC DEVICE | `1e-8` | `1e-7` |

The first pair keeps the established science threshold for independently
implemented PBE. The second is tighter because it compares the same library and
functional on two backends. A failure must be investigated; these thresholds
must not be loosened to accept an observed result.

GPU partitions additionally require:

- The actual SIF CMake cache enables `CP2K_ENABLE_LIBXC_GPU`.
- CP2K reports the executed LibXC version as 7.1.2 and its recorded source archive SHA256
  is `3915fac94416e4c415534223ea492ad2663f928acf27e98662c861b094a6c306`.
- Both `ldd` and the loader's actual `calling init:` messages name the same
  identity-specific `dependencies/libxc/lib` provider.
- Nsight Systems records positive invocations and GPU time for LibXC-owned
  `work_gga_gpu_exc_unpol` or `work_gga_gpu_vxc_unpol` kernels. These names derive
  from LibXC 7.1.2 `src/work_gga_inc.c` and `src/work_gga.c`; generic DBCSR CUDA
  activity does not satisfy this gate.

DSPRHBM runs built-in PBE and LibXC HOST. Its static provider is checked through
the version returned by the linked library during functional execution and the
source archive metadata. Build-only CMake configuration and static libraries
remain excluded from the final SIF; they are not required for this check.

`libxc_smoke.py prepare` checks the original delivery manifest and full SIF hash,
copies the frozen launcher into its own supplementary directory and records all
controller, script and fixture hashes. The copy keeps the original artifact
identity validation, read-only site binds and host-MPI launch. Its only execution
changes are project-backed `/tmp` and `/runtime`, a private container HOME,
loader tracing, and Nsight around the DEVICE calculation. No original frozen
file is modified. Nsight runs **inside** the SIF, avoiding loss of injection
through Apptainer's `--cleanenv`. All new files live in the project directory;
neither `/opt` nor the old home-directory runs receive writes.

After the ordinary build produces `artifact.path`, stage this script under the
project maintenance directory and run (substitute the target in both run IDs):

```bash
python3 libxc_smoke.py prepare \
  --controller /org/org-open-sai/proj-sai-hpc-software/controller/10992f818af71543bb39cb81e0f60c9f7ede9535/39983165813-1-development-4v100-avx512-2026-10-23-cfe5e5d87aaf \
  --build-run-id 39983165813-1-development-4v100-avx512-2026-10-23-cfe5e5d87aaf \
  --run-id cp2k-libxc-39983165813-4v100-avx512
sbatch /org/org-open-sai/proj-sai-hpc-software/runtime-tests/cp2k-libxc-39983165813-4v100-avx512/job.sbatch
```

The generated job requests `proj-sai-hpc-software`, sets its working directory
inside the project, uses one MPI rank and one GPU on V100 partitions, and runs the
normal matching host OpenMPI module. Host MPI scratch uses a short
`runtime/lx-<12 hex digits>` path derived from the task directory, leaving room
for PRTE's Unix socket names. `results/accepted.json` contains numerical
deltas, final artifact identity/hash, actual loader messages and LibXC kernel
counts/time. Any failure writes `results/failed.json` and leaves raw evidence
intact. Reruns use a fresh supplementary run ID; existing evidence is never
overwritten. Main single/multinode OT, diagonalization and ELPA acceptance remains
required independently.

After the ordinary workflow publishes a target, `audit.py` performs a separate
read-only revalidation through its original frozen controller: final SIF bytes,
raw seven-case single/two-node science, native export, module and all three
dependency/build/science allocations. Invoke it on the login node with
`python3 -I -B - <target> < audit.py`. This audit does not submit jobs or change
acceptance records; it also does not assert physical `/opt` installation or
replace the LibXC supplement. Its result is pending publication of each target.
