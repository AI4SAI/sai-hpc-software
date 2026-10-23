# CP2K development builds and LibXC supplementary acceptance

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
  --build-run-id 39983165813-1-development-4v100-avx512-2026-10-23-cfe5e5d87aaf-node-retry4 \
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
replace the LibXC supplement. The 16V100 final artifact and seven-case science
gate have passed this independent audit; see [16V100-validation.json](16V100-validation.json).
The DSPRHBM seven-case science job `1915020` also completed successfully;
publication is still in progress and its final audit is recorded separately
when available.

## Initial LibXC numerical results

The first supplementary jobs, `1915015` on 16V100 and `1915017` on DSPRHBM,
executed all requested calculations, but **failed the original built-in PBE
versus LibXC energy gate**. Their energy differences were respectively
`4.163449169425348e-6` and `4.163449183636203e-6` Ha, above the predeclared
`1e-6` Ha tolerance. Both maximum force differences were
`1.353720000003139e-7` Ha Bohr⁻¹. The failed records and original tolerances
remain unchanged. Raw requests, inputs, outputs, loader logs and allocations are
archived in [16v100-libxc-1915015](16v100-libxc-1915015/collection.json) and
[dsprhbm-libxc-1915017](dsprhbm-libxc-1915017/collection.json).

Independent parsing of the same raw calculations shows that the 16V100 LibXC
HOST and DEVICE energies differ by `2.3447910280083306e-13` Ha and the recorded
forces are identical. Its profiler records 94 invocations of the LibXC
`work_gga_gpu_vxc_unpol` kernel, totaling `30711322` ns. LibXC HOST results on
16V100 and DSPRHBM differ by `1.3571366253017914e-12` Ha in energy and about
`1e-12` Ha Bohr⁻¹ in force. These observations are preserved in
[libxc-initial-comparison-analysis.json](libxc-initial-comparison-analysis.json);
they do not replace the failed gate or grant supplementary acceptance.

Source review found different default PBE constants: CP2K's built-in
implementation uses beta `0.066725` and PW92 A1 `0.031091`; LibXC uses beta
`0.06672455060314922` and the modified PW92 A1 `0.0310907`. LibXC's exposed PBE
parameters do not include that PW92 A1, so changing beta alone cannot align
the complete functionals. An independently reviewed reference protocol uses
the upstream, pre-existing Density Functional Repository fixtures with the
matching LibXC defaults; see the frozen
[v1 protocol](libxc-reference/reference-v1/protocol.json). It is a separate verification of the same final SIF,
not a relaxation of the failed comparison. The DSPRHBM SIF's static LibXC API
is exported by `lib64/libcp2k.so.2026.2`; its byte hash and symbol inspection are
recorded in [libxc-static-symbols-dsprhbm.json](libxc-static-symbols-dsprhbm.json).

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

That second application recovery, `1914931`, subsequently failed with the same
10-second `fuse2fs` timeout on `4v100n01`. The checkpoint clone completed and
the original source remained unchanged, but no application compiler ran.
[Its raw failure evidence](4v100-mount-failure-1914931/collection.json) is
retained independently of the earlier diagnostic cancellation.

Subsequent submission attempts are also retained:

| Job | Fresh suffix | Outcome |
|---|---|---|
| `1915039` | `-node-retry1` | The installed `sbatch` does not support `SBATCH_EXCLUDE`; the postcheck found no exclusion and allocation on `4v100n01`. Only this newly submitted job was explicitly cancelled. |
| `1915163` | `-node-retry2` | Explicit `--hold --exclude=4v100n01` worked. A guard incorrectly required `CpusPerTres` before scheduling had expanded GPU CPU resources, so it cancelled the held job. Elapsed time was zero and no node was assigned. |
| No allocation | `-node-retry3` | An explicit `--cpus-per-task=8` request was rejected by SAI's submission policy: CPUs must follow GPU count. No job ID or successful request record was created. |

Their full commands, receipts, original input hashes and accounting are in
[4v100-exclusion-cancelled-1915039](4v100-exclusion-cancelled-1915039/collection.json)
and [4v100-held-cancelled-1915163](4v100-held-cancelled-1915163/collection.json).
These are submission/recovery errors, not additional compiler failures.
The [CPU-policy rejection](4v100-cpu-policy-rejected-node-retry3/collection.json)
preserves the exact rejected wrapper command and site error. The site's CPU
policy contradicts the resource override attempted by
[prepare_4v100_node_retry_resources.py](prepare_4v100_node_retry_resources.py).

The [GPU-only recovery](prepare_4v100_node_retry_gpu_only.py) uses fresh suffix
`-node-retry4` and a restricted external `sbatch` wrapper with only
`--hold --exclude=4v100n01`. It checks account, partition, command, work directory,
GPU request, task count, QOS, time limit, exclusion and held status before
release. Pre-scheduling CPU fields are recorded without imposing a value;
the running allocation must then be observed read-only for the expected eight
logical CPUs per GPU. A mismatch preserves the new held job for review.
The original controller, generated job script, recipe, dependency checkpoint
and old application overlays are not edited.

This recovery submitted `1915249`, which ran on `4v100n04` with eight allocated
logical CPUs and one GPU. `ExcNodeList=4v100n01` remained effective. All held
allocation checks, controller/input hashes and the later running resource
checks passed. The exact
[submission and running-allocation records](4v100-node-retry-1915249/collection.json)
preserve this transition; application completion and science acceptance are
still pending.

## 8V100V0 application mount failure

Job `1914617` completed its exact dependency clone on `8v100v0n02`, then failed
with exit `255:0` at the same pre-container 10-second `fuse2fs` mount deadline.
Its elapsed time was `01:01:06`; the long preparation included continuing image
reads and did not enter application compilation. The preparation report records
`status=prepared`, `source_unchanged=true` and `upper_directory_unchanged=true`.
The source checkpoint and failed clone remain intact. See the original
[logs, request, preparation report and accounting](8v100-mount-failure-1914617/collection.json).

The separately reviewed [8V100V0 recovery](prepare_8v100_node_retry.py) prepares
a fresh `-node-retry1` using the same sealed dependency checkpoint, frozen
controller and source/recipe identity. Its external wrapper requests only
`--hold --exclude=8v100v0n02`. An earlier staged helper that explicitly requested
six CPUs was never executed after the 4V100 policy rejection was observed.
The revised helper leaves CPU derivation to the site and expects six logical
CPUs per GPU in the running allocation. All other held allocation and
original-input checks must pass before release; an unexpected field leaves
the new allocation held for review. The cause of either partition's mount
timeout has not been established.

The revised GPU-only recovery submitted `1915260`, which ran on `8v100v0n03`
with six allocated logical CPUs and one GPU, excluding `8v100v0n02` as requested.
The held and running checks passed; see its
[submission evidence](8v100-node-retry-1915260/collection.json).
Application completion and normal science acceptance remain pending.
