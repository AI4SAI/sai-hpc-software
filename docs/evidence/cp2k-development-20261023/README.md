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

The frozen controller stores its launcher as `cp2k`; the source checkout calls
the same file `cp2k_runtime.sh`. The first 16V100 supplementary preparation used
the checkout filename and stopped before any Slurm submission. Its incomplete
`runtime-tests/cp2k-libxc-39983165813-16v100-avx2` directory is retained. The
corrected helper reads and validates the frozen `cp2k` launcher before creating
a task, and uses fresh run `cp2k-libxc-39983165813-16v100-avx2-prep2` for the next
attempt. This preparation error did not change the SIF, controller or normal
science acceptance.

After the ordinary build produces `artifact.path`, stage this script under the
project maintenance directory and run (substitute the target in both run IDs):

```bash
python3 libxc_smoke.py prepare \
  --controller /org/org-open-sai/proj-sai-hpc-software/controller/10992f818af71543bb39cb81e0f60c9f7ede9535/39983165813-1-development-4v100-avx512-2026-10-23-cfe5e5d87aaf \
  --build-run-id 39983165813-1-development-4v100-avx512-2026-10-23-cfe5e5d87aaf-mount-retry2 \
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

## 4V100 mount failure and fresh application attempt

The original application job `1914241` failed with exit `255:0` before entering
the container. Dependency cloning finished and both filesystem checks returned
zero; Apptainer 1.4.4 then reported that `fuse2fs` had not mounted the cloned
overlay within its 10-second deadline. No application or LibXC compilation ran
in that attempt. The original workflow failure remains part of the evidence;
this recovery does not turn the original GitHub job into a successful job.
See [the failure record](4v100-mount-failure-1914241.json).

The independent diagnostic job `1914582` ran on the same `4v100n01` node and
mounted that failed attempt's overlay **read-only** twice, in 2.008 and 0.502
seconds; both commands returned zero. The captured helper used `-o allow_other,ro`,
its backing descriptors had access mode `O_RDONLY`, and its observed
`write_bytes` was zero. Overlay size, inode, ownership, mode, block count and
mtime remained identical before and after the probe. No full-image checksum
was recomputed for this diagnostic. This establishes subsequent read-only
mountability; it neither reproduces the original read-write startup nor proves
the cause of its timeout. Raw
[probe results](mount-probe-1914582/results/probe.json),
[allocation](mount-probe-1914582/results/allocation.txt), logs and the
[collection manifest](mount-probe-1914582/collection.json) preserve that boundary.
The executed [probe source](mount-probe-1914582/mount_probe.py) has SHA256
`e5e9b879395b8bbdb6d2fc4145b89047c64f560aa353d6f614b35460b8646aa7`
and is byte-identical to the top-level `mount_probe.py`.

The reviewed [recovery script](prepare_4v100_retry.py) submitted job `1914645`
using the original frozen controller, exact source/recipe identity and the same
sealed dependency checkpoint. Its new run is
`39983165813-1-development-4v100-avx512-2026-10-23-cfe5e5d87aaf-mount-retry1`.
It creates a fresh clone through the original controller and does not reuse or
modify the failed overlay. Required archives are copied and verified;
original small-file and controller hashes were unchanged across submission.
The [retry plan](4v100-mount-retry-1914645/input/retry-plan.json),
[submission receipt](4v100-mount-retry-1914645/results/retry-submitted.json) and
[collection manifest](4v100-mount-retry-1914645/collection.json) bind that recovery
to its inputs. Its application build, normal science acceptance and independent
LibXC supplement remain required before declaring this target accepted.

The fresh job passed the mount step (2.125 seconds), but was cancelled at
2026-10-23 17:46:24 UTC by an **agent diagnostic resource mistake**. A read-only
`srun --overlap` observation requested one CPU per task inside its GPU allocation.
The SAI site hook printed that 4V100 requires eight logical CPU cores per GPU,
then cancelled the allocation three seconds after the diagnostic step started.
The diagnostic's outer 30-second timeout had not expired; no `scancel` command
was issued. This cancellation is not attributed to CP2K, LibXC or the image
driver. The script did not reach its `/proc` observation. Other active builds
were not probed with `srun` and remain independent.

[The exact diagnostic command and times](4v100-readonly-step-1914645/diagnostic.json),
[SAI policy output](4v100-readonly-step-1914645/stdout.txt),
[step errors](4v100-readonly-step-1914645/stderr.txt) and
[Slurm cancellation evidence](4v100-readonly-step-1914645/cancellation.json)
preserve the failed diagnostic. Its [script](observe_processes.py) is retained
as incident evidence, not recommended as an active-job diagnostic procedure.
Both application attempts and their overlays remain intact. A subsequent fresh
application attempt must use the same original controller and sealed dependency
checkpoint, and must still pass the complete application and science gates.

The independently reviewed
[recovery script](prepare_4v100_retry_after_diagnostic.py), SHA256
`3e8715e5fb83f1430a07ef90d9f5a5f7a08d2061f7da9ea6cbd1082f531838f3`,
submitted job `1914931` with fresh run suffix `-mount-retry2`. Despite the suffix,
this second recovery addresses the diagnostic cancellation, not a second mount
failure. It uses the original sealed dependency checkpoint, controller and
identity, and does not reuse either prior application's overlay. The exact
[plan](4v100-retry-after-diagnostic-1914931/input/retry-plan.json) records both
prior attempts and the SAI policy output; the
[submission receipt](4v100-retry-after-diagnostic-1914931/results/retry-submitted.json)
confirms all tracked original, first-retry and diagnostic files stayed unchanged.
The [collection manifest](4v100-retry-after-diagnostic-1914931/collection.json)
records the raw submission file hashes. All normal acceptance requirements still
apply to this new attempt.
