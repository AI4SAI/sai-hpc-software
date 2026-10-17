# CP2K validation status

This records implementation and live build progress, not release acceptance.
No four-target CP2K 2026.2 release has passed all acceptance gates yet.

## Release-first execution (2026-10-16)

On October 17 the user explicitly required running without software disk
reservations or free-space admission gates. The current controller implements
that policy: `require_capacity` is a compatibility no-op, and `build_capacity`
only holds an exclusive per-run lock. No `statvfs` estimate, other-job budget,
4 GiB reserve, or 30-minute capacity wait can reject a new build. Actual write
failures still fail normally and preserve their evidence. The complete SAI
suite passed 703 tests (701 passed, two optional MD-cache skips).

All build storage stays under `~/sai-hpc-software/`. Host Apptainer temporary
and cache paths are per-run home directories; compiler and Spack temporary
files live under container `/workspace`, backed by the home `work.ext3`.
A live-node audit observed this overlay file open and `TMPDIR=/workspace/tmp`,
with no separate host `/tmp` or `/var/tmp` mount.

The subsequent [CPU/I/O investigation](BUILD-IO-EFFICIENCY.md) attributed the
observed low utilization to `fuse2fs 1.47.3` whole-image synchronization under
a global lock. Correct parallelism and unthrottled cgroups were verified.
Isolated probes `1830438` (DSPRHBM) and `1830468` (16V100) passed with an upstream
performance fix plus an inherited-OFD-lock adapter: small compiler workloads
improved about 28x and 20x, with matching output bytes and clean-remount/lock
checks. The pinned driver is now integrated into new immutable production
recipes, including dependency/app mounts and export. Historical jobs still
use their original recipes/drivers. Final SIF verification remains independent
of the build overlay; scientific/export/publication gates remain required.

New release workflow
[39032306213](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39032306213)
pins controller `1e7d736e5884fe207460a3e6baf0aeddb5bdb4aa` and recipe
`9c87f65a7c0a1bb7638f5e942f59bd7bdde750d7cf6cd5420c90cfa13cc517ad`.
At 20:28:46 Asia/Shanghai, all four dependency jobs were RUNNING:

| Partition | Slurm job | Node |
|---|---|---|
| 16V100 | 1830993 | 16v100n04 |
| 4V100 | 1830994 | 4v100n27 |
| 8V100V0 | 1830995 | 8v100v0n02 |
| DSPRHBM | 1830997 | d9470n05 |

The new 16V100 job recorded GCC/G++/GFortran 14.3.0 and the pinned driver
checksum. A ten-second cgroup sample ending at 20:30:38 measured 7.144/8
logical CPUs (89.30%) while actual dependencies were compiling. The old
same-node job measured 0.492/8 (6.15%) in the concurrent sample; these were
different stages, not a controlled whole-build timing comparison. CI passed
709 tests (707 passed, two optional skips) and the 20 focused SAI checks passed.
No new application SIF or accepted release is claimed from these observations.
The [20:36 production snapshot](evidence/cp2k-private-driver-production-20261017.json)
confirms all four jobs still RUNNING with GCC/G++/GFortran 14.3.0 and the
same pinned private driver. HDF5 completed in 2:50 / 1:50 / 2:38 / 3:27 for
16V100 / 4V100 / 8V100V0 / DSPRHBM respectively. 16V100 and 4V100 had also
finished Cython and entered PLUMED compilation. Libint remained in progress;
dependency seals, application SIFs and scientific acceptance were still absent.

At 18:06, 4V100 `1828944` and DSPRHBM `1828946` were compiling dependencies.
16V100 `1828943` was cancelled at 18:05:44: the agent's additional read-only
`srun` audit incorrectly requested one CPU, triggering SAI's rule requiring
eight logical CPU cores per 16V100 GPU. The agent is responsible for this
operational error. Original evidence/checkpoints are retained; fresh 16V100
and 8V100V0 retries use the updated controller. Running controllers remain
immutable. A changed recipe is never assigned to the old dependency overlay.

Retry workflow [39023790564](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39023790564)
pins controller `975f8f8dd4681b12ad3173622a9e0a50a7d320b9` and recipe
`495cbd26fc50bb5371613753f7aca3d0214271116e8238efdbccc0632f8119e5`.
It selects release `v2026.2` for 16V100 and 8V100V0, leaving the existing
4V100/DSPRHBM jobs running with their original identities. GitHub validation
and source resolution passed. At 18:15 Asia/Shanghai, actual Slurm submissions
were:

| Partition | Slurm job | Actual stage |
|---|---|---|
| 16V100 | 1829656 | RUNNING on 16v100n04 since 18:13:53; GCC 14 profile ready, dependency concretization |
| 8V100V0 | 1829644 | RUNNING on 8v100v0n09 since 18:13:01; 2 GPUs / 12 CPUs, overlay/container preparation |

Both record `capacity_policy=filesystem-enforced`; no capacity reservation or
capacity wait is active. The old 4V100 `1828944` and DSPRHBM `1828946` jobs were
also RUNNING. Allocation/startup is not a completed CP2K application build.
The [home-storage evidence](evidence/cp2k-home-storage-policy-20261017.json)
retains the live mount/path observations, the audit incident and test log hash.

Historical snapshot before that policy change:

Current workflow: [39018993951](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39018993951),
controller `a65c62db0999e7f01138a09d647597dbc3702a1f`. At 17:51 Asia/Shanghai
on October 17, actual dependency submissions were:

| Partition | Slurm job | State / actual stage |
|---|---|---|
| 16V100 | 1828943 | RUNNING; reserved disk, GCC/G++/GFortran 14.3.0, installing dependencies |
| 4V100 | 1828944 | RUNNING allocation since 17:45:59; waiting for disk reservation, compilation not started |
| 8V100V0 | 1828945 | FAILED 1:0 at 17:29:59; 30-minute disk reservation timeout, compilation never started |
| DSPRHBM | 1828946 | RUNNING; reserved disk, GCC/G++/GFortran 14.3.0, installing dependencies |

16V100 had run for 51:44 and DSPRHBM for 51:19. Both were building Libint
and configuring HDF5; neither had started the CP2K application. About 72 GiB
was physically free, but live reservations also cover remaining growth of
those two dependency overlays and older development job `1828523` on 16V100.
After those budgets, 4V100 was roughly 50 MiB short of its 16 GiB overlay plus
the unchanged 4 GiB safety reserve. Its allocation has not begun compiling.
The failed 8V100V0 stage has no dependency seal; its application/acceptance
jobs were skipped. These were outcomes of the old policy; its estimates no
longer gate new jobs. Old checkpoints remain preserved.

16V100 started at 16:59:26 on `16v100n37`. Before submission, GitHub spent
several minutes opening a new SSH connection per controller file; triggering
Actions was not evidence of an actual Slurm job. The subsequent CI transport
fix batches the same trusted files into one SFTP connection before fingerprinting
or submission. Its lifecycle and recovery suites passed 32 tests on SAI.
It does not change this running workflow's immutable controller or recipe.

The previous GCC 14 workflow `38929566155` completed dependencies on 4V100
(`1819528`, 2:53:17), 16V100 (`1819537`, 3:28:36), and DSPRHBM
(`1819530`, 4:39:51), each exit `0:0`. Their application jobs `1821074`,
`1821478`, and `1822145` all failed after TBLITE installation: parallel HDF5
reports `ON` in its CMake-generated settings, while the old shell check required
`yes`. Read-only `debugfs` inspection confirmed parallel HDF5 and GCC 14 in
those dependency installations. It also exposed a separate compiler selection
defect: CMake had selected the system `/usr/bin/f95` (GCC 13.3) for TBLITE,
despite the module and executable version checks passing for GCC 14.

The repair exports absolute C/C++/Fortran compiler paths, passes them explicitly
to every application-side CMake configuration, pins the Open MPI wrapper
compilers, and rejects system aliases in the CP2K CMake cache. The HDF5 gate
accepts `ON` or `yes`, rejects disabled/ambiguous settings, and runs before
TBLITE compilation. Read-only dependency-overlay probes `1828866` (16V100)
and `1828868` (DSPRHBM) passed `0:0`: HDF5 checks, real CMake compiler metadata,
three compiled test executables, and `mpifort --version` all passed with 14.3.0.
4V100 probe `1828867` failed `255:0` after one minute: Apptainer's `fuse2fs`
overlay mount exceeded its 10-second limit. Its compiler probe did not run;
this is not a compiler or HDF5 validation result. Earlier failed probes are retained.

8V100V0 dependency `1819531` timed out at four hours while building Libint.
The retry requested two GPUs and actually allocated twelve CPUs (`1828945`),
instead of the previous six, but the capacity timeout prevented any compiler
work, so the twelve-CPU Libint build remains untested. Application/scientific
resources are unchanged.
The older GCC 13 job `1817573` also timed out; its checkpoint is preserved.
Changed recipes get fresh identities and prefixes; no old overlay is relabelled.

The corrected build suite ran 702 tests on SAI with umask 022: 700 passed,
two optional MD source-cache checks skipped. The subsequent upload fix also
passed the [GitHub full suite](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39019592103)
of 703 tests (701 passed, the same two optional checks skipped).
The first run under site umask
0002 failed strict native-export permission fixtures; that log is retained.
Raw accounting, receipts, compiler caches, HDF5 settings and probe logs are in
[the retry snapshot](evidence/cp2k-cmake-hdf5-retry-20261017.json).
The earlier [parallel-build snapshot](evidence/cp2k-gcc14-parallel-20261017.json)
is historical. There is still no successful release CP2K application SIF,
scientific/performance acceptance, or publication.

Previous execution:

Workflow [38879044765](https://github.com/AI4SAI/sai-hpc-software/actions/runs/38879044765)
was dispatched from `bd89e49` for release only, all four targets, with explicit
release retry. Resolution pinned the exact `v2026.2` commit on every target.
At 23:48 Asia/Shanghai, 16V100 dependency job `1814499` had timed out at the
four-hour GPU QOS limit. 4V100 job `1817424` failed concretization: its allocated
Ryzen 9950X3D is Zen 5, unsupported by the pinned GCC 13.3 compiler target model.
8V100V0 dependency job `1817573` was still running after 2h05m. DSPRHBM had not
been submitted because the original workflow waits for the whole GPU matrix.
These are not application successes.
The workflow ran 694 tests: 692 passed and two optional MD source-cache tests
were skipped on the runner; both were rerun locally with the preserved cache
and passed.

The corrected recipe pins GCC **14.3.0**, matching the upstream v2026.2
`tools/toolchain/scripts/stage0/install_gcc.sh`. SAI's `gcc`, `g++`, and
`gfortran` executables at `/opt/devtools/gcc/14.3.0/bin` each report 14.3.0.
The module is explicitly switched after the shared site environment is loaded,
and native profiles record all three compiler versions. Historical GCC 13
checkpoints retain their original identities and cannot enter a GCC 14 build.

The GPU matrix now allows three concurrent targets; CPU compilation starts
independently. Only CPU science acceptance waits (at most 90 minutes) for a
reverified GPU reference. Dependency/application singleton names are scoped to
the target. Native CP2K stages use 16 GiB overlays and an exclusive per-run
lock. The previous shared reservations and 4 GiB reserve have been removed
per the user's instruction. Real filesystem errors are not suppressed.
No old overlay or scientific/publication gate was removed.

A fresh read-only final-SIF loader check `1814450` completed `0:0` in seven
seconds on 16V100. It verified the image checksum, version/revision and all
64 loader-output lines with no unresolved libraries in the current site
environment. Scientific/performance acceptance and publication remain pending.

The current priority is the official `v2026.2` release, commit
`67b5da876dd6a76b8b021d5a04d1c81ba79a4c50`, on 16V100, 4V100,
8V100V0 and DSPRHBM. Development remains a required channel. Persistent
work now lives in `~/sai-hpc-software/development/workflow-closure`, with
operational records in `diagnostics/cp2k-release-20261016`.

The historical 16V100 development SIF did compile and pass the final-image
`--version` and `ldd` checks in job `1345682`: version
`2026.2 (Development Version)`, revision `3aa79cf7c5`, 64 loader-output
lines with no unresolved libraries. Its current SIF checksum still matches
its manifest. This is historical loader evidence, not scientific/performance
acceptance, publication, or proof on another partition; it also uses site
CP2K 2026.1 toolchain libraries.

The release source is now received and complete in the remote cache using
only a 932-byte compressed delta from its verified common ancestor.
Exact-commit inventory lookup prevents older release commits being missed
when the recent-12-tip window is filled by daily development builds.
Real-Git regression tests cover that case and missing/incomplete sources.

The release's toolchain uses pristine LIBXS 1.0.0; later development adds a
JIT-handle patch. Preparation now follows the pinned source rather than
unconditionally requiring that development-only file. Real upstream archive
preflights passed for both sources and rejected a declared-but-missing patch.
The historical final-prefix CPU dependency job `1440608` is now confirmed
`COMPLETED 0:0` in 5:44:54. The workflow therefore requests 720 minutes for
DSPRHBM dependencies while retaining the 240-minute GPU QOS bound. The CPU
observer allows 350 minutes within its six-hour runner limit; expiration
preserves the Slurm job and requires handle-based continuation, not a rebuild.
No release application or four-partition scientific acceptance is claimed yet.
Evidence: [release priority and loader audit](evidence/cp2k-release-priority-20261016.json).

## Recovery update (2026-09-20)

After connectivity returned, jobs `1383003` and `1383002` were confirmed FAILED
(exit 3:0, 2:00:41 and 2:40:05). They passed the prior MPI and `/bin/rm`
problems and installed Cython, HDF5 and PLUMED. Both stopped at Libint's
`make export`: GNU tar reported `generated: file changed as we read it`.
The filesystem/concurrency cause of that warning is not yet established.

The user then authorized at most six **additional** attempts per software,
minimizing retries. The remote ledger is
`diagnostics/recovery-20260920/attempts/cp2k`; each newly submitted native
partition job counts separately. Historical two-repair receipts are retained.

Read-only inspection of the pinned Spack recipe and failed source trees shows
that Spack builds directly in `generated/`, never consumes `generated.tgz`,
and only removes the upstream directory-deletion suffix despite its comment
about skipping tarball creation. `cp2k_spack_libint_repair.py` removes only the
`tgz` prerequisite from one exact export rule. Code generation, preprocessing,
configuration, Fortran/shared features and installation remain required.
This is a recorded **local source patch**, with before/after hashes, not a
claim of pristine upstream or a binary-cache hit. Tar errors are not ignored.

Attempt 1, job `1415685`, ran on DSPRHBM using a fresh clone of the terminal
checkpoint, checked before first mount. Snapshot:
`diagnostics/cp2k-libint-recovery-20260920-01`; run:
`runs/cp2k-libint-recovery-20260920-DSPRHBM`. The existing exact lock and
native ABI/features are retained. It has 8 CPUs, 28 GiB and a 12-hour limit
(rush-cpu allows 48 hours). The GPU counterpart is not yet resubmitted.

That attempt was cancelled after 32:03: a recovery-script omission meant
Spack's default initial restaging would discard the patch. `--keep-stage`
preserves the stage after success, not before installation. Allocated read-only
process inspection found the worker in `unlinkat` inside the generated source
tree, waiting through FUSE on Lustre `osc_io_fsync_end`; the underlying storage
cause is not established. The cancelled attempt and its overlay are retained.

The revised explicit Libint repair mode now passes `--dont-restage` and requires
a successful `.spack_patched` checkpoint without `.spack_patch_failed`.
The pinned Spack parser was exercised locally to prove the old flags set
`restage=True` and revised flags set `restage=False`, with clean builds and no
binary cache still enforced. All 38 Spack-focused tests passed. Attempt 2,
job `1416362`, uses
new snapshot `diagnostics/cp2k-libint-recovery-20260920-02` and run
`runs/cp2k-libint-recovery-20260920-02-DSPRHBM`, cloned from the original terminal
checkpoint, not from the cancelled partial stage cleanup.
The complete local suite passed **239 tests** from the repository directory
after this revision; shell syntax and pinned Spack CLI behavior checks passed.

Local validation: 236 tests passed before the additional parallel-export
test, and all three Libint patch tests passed afterward. The `make -n -j8`
test proves the modified target schedules generation/preprocessing/configure
but not tar. Shell syntax and Python compile checks passed. This remains a
dependency-install pilot, not CP2K native/scientific/performance acceptance.

## Final-prefix integration (2026-09-22)

Both historical dependency pilots completed (DSPRHBM `1416362`, 16V100
`1422298`). They are not CP2K application/acceptance successes, and their old
internal stores cannot be relocated by renaming. Old main job `1436043`
installed CP2K but failed loading the separate `libplumedKernel.so` omitted by
its frontend-only copy rule.

The new recipe consumes a receipt-checked private Spack store at its final
`/opt/apps/cp2k/cp2k-<track>-<build>/<PARTITION>` prefix. No old CP2K app is bound
into new builds. The probe, fetch validator and installation evidence now
carry and verify the exact prefix; DAG hashes alone do not encode that path.
Libint's diagnosed one-rule export repair is applied after `do_patch()` in a
fresh stage, then preserved with `--dont-restage`. Version/features are retained.

`cp2k_spack_link.py` requires all seven roots and their installed link/run
closure, checks PLUMED frontend and kernel independently, and emits exact
private library roots. Site `spglib/2.7.0` was inspected and supplies
`/opt/devtools/spglib/spglib-2.7.0`; other infrastructure remains module-provided.
`cp2k_payload.py` exports only runtime portions of the link/run closure at
unchanged paths, omitting build-only Python packages, includes, static archives,
package-manager metadata and dependency test/docs directories. CP2K data and
PLUMED runtime data are retained; post-export ELF/manifest checks remain required.

The first stage is DSPRHBM CPU dependencies (8 CPUs, 28 GiB, rush-cpu, 12-hour
ceiling), cloning only the pinned source/solver seed to a new 32-GiB overlay.
The main application must follow in a separate verified stage at the **same
identity/prefix**, cloning the successful terminal overlay without modifying it.
Do not run the normal all-in-one GPU profile for this initial integration.

Validation: 261 full local tests passed; additionally 8 final-prefix tests
(including real CMake adapter resolution, source-clone ordering and runtime
payload exclusion) and 11 delivery-integration tests passed. This is preflight,
not native CP2K scientific acceptance. Controller snapshot:
`controller/cp2k-final-spack-20260922-r1/controller`, recipe
`a89d548857fe901c3a05128a16f7f38faffb8eb036635122f46e3ef77f8fb4dd`.

The first native stage `1440487` exposed an additional fresh-stage assumption:
the old repair guard expected Spack build() to have removed upstream's
`&& rm -rf $(EXPORTDIR)` archive suffix, which do_patch() does not remove.
No compilation was reached. The guard now explicitly distinguishes pristine
and historical checkpoint forms; the correction still alters only the tgz
prerequisite. All 48 Spack tests and the real pinned archive/filter regression
passed. New stage `1440608` uses fresh source-only state and snapshot r2,
recipe `a0eb9c619c3bce78c2f15405d35001456d3d9e99feff133d425a97bdb2aaf91e`.
No old snapshot was edited. This repair is not yet a dependency-install success.

## Current blockers and Spack cache (2026-09-17)

The latest daily run `35196109864` did not produce an accepted candidate:

- DSPRHBM job `1366216` reached the final link, then failed on `-lcudart`.
  The selected standalone ELPA 2026 module is its `nvidia` build even on the
  CPU partition; its pkg-config dependency list includes CUDA. CPU CP2K
  configuration alone does not eliminate this transitive dependency.
- 16V100 job `1368587` installed CP2K, then failed the loader check on
  `libplumedKernel.so`. Copying only `libplumed.so*` does not include the
  separately named kernel library. Neither result passed scientific acceptance.

The ELPA link repair now explicitly passes CUDA 12.9.1's `lib64` directory
to CMake on both CPU and GPU targets. Inspection of the actual site module
confirmed that it sets `LD_LIBRARY_PATH`, but not `LIBRARY_PATH`, while the
ELPA `.pc` file lists `-lcusolver -lcudart -lcublasLt -lcublas` without their
`-L` directory. The configuration gate now requires these four pkg-config
libraries to resolve to full paths in the pinned CUDA toolkit, before the
long CP2K compilation. DSPRHBM retains `CP2K_USE_ACCEL=NONE`.

Contained DSPRHBM probe `1382379` completed `0:0` in 1m09s using upstream
`FindElpa.cmake` at `26ffdda5eaee8dc2f3d8268cb2fd92854b42740a`.
The original configuration reproduced a bare unresolved `cudart`; the repaired
configuration resolved all four libraries to CUDA 12.9.1. A real C executable
linked with `--no-as-needed`, passed `ldd` with no unresolved libraries, and
exited successfully. Results are in
`runs/cp2k-elpa-link-20260918-a-DSPRHBM/results`; checksums are recorded in
`docs/evidence/cp2k-elpa-link-1382379.json`. This verifies the isolated link
repair, not CP2K compilation or scientific acceptance. The independent
dependency pilots continue using their unchanged frozen snapshots.

The user approved starting a new Spack cache rather than locating an existing
one. These directories have been created on SAI:

```
/home/stardust/sai-hpc-software/cache/spack/sources
/home/stardust/sai-hpc-software/cache/spack/buildcache/16V100
/home/stardust/sai-hpc-software/cache/spack/buildcache/DSPRHBM
```

The source cache is now populated (see the September 18 probe below); the
binary caches remain empty. The current production recipe does **not** invoke
Spack yet, and no binary-cache hit is claimed. Existing Git/source-archive
caches and the hand-built TBLITE bundle are not Spack buildcaches. Integration
must lock the concrete dependency DAG, reuse standalone MPI/BLAS/ELPA where
compatible, mirror sources before offline compute builds, and keep native
binary caches separate by partition and dependency ABI. Build/install trees
must stay inside the overlay; only archives and file-backed bootstrap images
belong in this cache, never expanded source/build/install directories.
Missing libraries must be built independently, not borrowed from an older CP2K.

The preparation manifest is generated by `controller/cp2k_spack.py`. It pins
Spack `3e19345b6e12f5ff1b874f4059622fc6a1fd804a` and package snapshot
`535022224b610cb07dcbf63cef5bd68c6a2aae24`. It gives each native partition a
separate binary cache and records GCC 13.3 and the site dependency ISA (not a
native CPU detection result). The store is under the canonical CP2K prefix
inside the overlay; staging and unpacked Spack/package trees are in
`/workspace`, never the host cache. This is not `spack.yaml`, a concrete DAG,
or an integrated build. Native dependency resolution is being probed separately;
offline installation and buildcache validation remain incomplete. CP2K's cuSOLVERMp
settings and the site's CUDA-linked ELPA must also be preserved and tested.

The user-authorized reference
[`SAI-autocompile-kit`](https://github.com/AI4SAI/SAI-autocompile-kit/tree/5fa2632771c654ac81d40be31fb8e4f921dbdf97)
contains no CP2K plugin or Spack implementation. Its LAMMPS compilation
settings were adapted on the separate MD branch; they do not establish a
CP2K dependency solution.

## Native Spack preparation (2026-09-18)

`cp2k_spack_environment.py` generates actual JSON-compatible `spack.yaml`,
separate from the preparation manifest. Seven roots replace the borrowed
toolchain: Libint 2.6.0 (CP2K lmax 5, Fortran), LIBXSMM 2.0.0, libvori
220621, parallel Fortran HDF5 1.14.6, PLUMED 2.10.1, COSMA 2.7.0 and
SpLA 1.6.1. GPU COSMA/SpLA retain CUDA; tiled-mm is constrained to sm_70.
This intentionally does not use Spack's CP2K recipe, which has a different
feature contract. Existing standalone ELPA and CP2K's cuSOLVERMp integration
are not removed or relabelled as CUDA-free.

The generator uses explicit standalone GCC/MPI/BLAS/CUDA and verified system
tools, with no `/opt/apps` paths. Native CPU/OS identifiers must be observed
on the compute node. Source staging and installation remain in the overlay;
public source mirrors, bootstrap downloads and unvalidated binary reuse are
disabled. Only the prepared local source mirror is enabled.

Local solver rehearsal resolved all seven GPU roots and mirrored 13 source
archives. The pinned compiler-wrapper source was also mirrored for native
compiler runtime injection. The host source cache now contains these 14
archives, the two pinned Spack/repository archives, and checksum-pinned
Python 3.12 clingo/cffi/pycparser solver wheels. This proves source preparation,
not native compilation, ABI compatibility, scientific correctness or speed.

`cp2k_spack_probe_submit.py`, `cp2k_spack_probe.sh` and
`cp2k_spack_native.py` implement a short offline native resolver/fetch probe.
Only metadata is exported to the host, and every attempt uses a fresh overlay
and an immutable controller snapshot. Submission records a held job before
release and refuses duplicate run IDs. Repairs require a terminal failed
parent, a diagnosis, and at most two diagnosed attempts per chain.

- Initial jobs `1380832` (16V100) / `1380833` (DSPRHBM) failed because the
  container bound `/usr` but not the node's dpkg database. Repair 1 adds
  read-only node metadata and alternatives bindings.
- Repair-1 jobs `1380859` / `1380860` recorded native profiles but rejected
  Ubuntu's `1:1.3.dfsg-3.1ubuntu2.1` as a zlib version mismatch. This was a
  validator error, not an upstream zlib mismatch. Repair 2 normalizes only
  Debian epochs, revisions and dfsg repack suffixes (covered by a regression
  test), and verifies compiler-detected glibc against the actual node runtime.
- Repair-2 jobs `1380946` / `1380947` were submitted from snapshot
  `/home/stardust/sai-hpc-software/diagnostics/cp2k-spack-native-20260918-v3`.
  Results are under `runs/cp2k-spack-native-20260918-c-<PARTITION>/results`.
  Job `1380946` was unintentionally cancelled at 14:25:07 CST by a diagnostic
  `srun` command that specified `--cpus-per-task=1`: SAI prohibits explicit
  CPU/memory requests on GPU partitions, including diagnostic job steps.
  This was an operator error, not a dependency solver/compiler failure.
  At that point no replacement GPU probe was submitted. Job `1380947` timed out at
  14:34:47 CST after 15:16, before the offline solver import/configuration
  marker. Its overlay was still growing during preparation; at 12:20 it had
  allocated about 127 MiB. The two unpacked upstream trees total about
  202 MiB and 23,206 tar entries. A request to extend this active job to
  30 minutes was denied by Slurm permissions; its original limit remained.
  After Slurm reported terminal TIMEOUT, read-only `debugfs` inspection showed
  the package extraction stopped at `py_mako`, and `/workspace/solver` was
  still empty. Thus this was unpacking overhead, not a solver timeout.
  Neither repair-2 job produced a concrete native lockfile or a successful
  mirror-only fetch receipt. No native dependency installation was started.

Monitor through `squeue`, `sacct`, `sstat` and saved logs. Do not introduce
diagnostic GPU job steps with CPU/memory overrides; site enforcement can
cancel the parent allocation, not merely reject the step.

No direct incidental retry was used for these deterministic probe failures.
The two diagnosed repair attempts were exhausted; do not reset the retry
counter or submit a third repair under a fresh name without fresh approval. The production
`cp2k_build.sh` still uses old CP2K toolchain dependencies and is not yet fixed.
Future packaging must select the runtime dependency closure rather than
blindly export the entire Spack store: build-only Python environments can
contain external symlinks that violate the final artifact's self-contained
tree contract. Preserve PLUMED's kernel library as well as its frontend.

Local validation: 20 Spack-focused tests, 50 CP2K tests, and the complete
220-test controller suite passed; the probe shell passes `bash -n`. These
tests do not substitute for the unfinished native solver/fetch probes.
Commit `4f14543` passed GitHub validation runs `35315304415` and `35315304368`;
these are not HPC build successes. Further native probe submissions require
the user's approval to go beyond the two diagnosed repair attempts. A next
probe should budget for the observed unpacking cost (or prepare a file-backed
source image) and retain the ban on GPU CPU/memory overrides.

### One additionally authorized probe round

After the explicit request for an extra CP2K probe round, the user replied
"continue". This authorizes one additional attempt per initial partition,
not a reset of the two-repair budget. The submitter retains the original
lineage, records `diagnosed_repairs_used: 3` and the extra authorization,
and refuses another attempt using the same approval.

`cp2k_spack_seed.py` prepares an ext3 overlay on the connected workstation
from the same checksum-locked Spack/package archives and solver wheels.
The seed contains no compiled application/dependency libraries. Root ownership
is normalized directly in the unmounted image using `debugfs`, and `e2fsck -fn`
checks the complete image. The initial local fakeroot/chown approach failed
before image population; the cause was local ownership emulation, and the
replacement does not chown host files. Only the file-backed image is sent to
SAI. Every probe copies it to a private overlay and rechecks its SHA-256
before mounting; no old failed overlay or native install store is reused.

Seed: `cache/spack/bootstrap/spack-source-v2.ext3`, SHA-256
`80497b787dc178876e71098840cf465deb63c6c9bec27c9dd5cc0bed3643c337`,
4 GiB logical size, 23,302 filesystem entries including directories.
Jobs `1381761` (16V100) and `1381762` (DSPRHBM), with 45-minute limits, use
immutable controller snapshot `diagnostics/cp2k-spack-native-20260918-v4`.
Results: `runs/cp2k-spack-native-20260918-d-<PARTITION>/results`.
The GPU probe reached offline clingo and native concretization in about
40 seconds. Both probes completed successfully: 16V100 in 1:41 and DSPRHBM
in 2:39. Their checked native targets are `zen3` and `sapphirerapids` on
Ubuntu 24.04, using GCC 13.3.0 and `-O3 -march=native -mtune=native`.
The GPU probe fetched 14 archives and the CPU probe 13, all mirror-only.
Each exported `spack.yaml`, `spack.lock`, `validated-graph.json`,
`native-profile.json` and `probe-success.json`. Neither installed dependencies.

### Native dependency installation pilot

The separate install stage clones only a terminal-successful probe overlay,
records its SHA-256, and verifies the exact lock before and after installation.
It rechecks the node-native architecture and external dependencies and invokes
`spack install --only-concrete --no-cache --fail-fast`, with no reconcretization
or binary cache reuse. The original probe image and results remain unchanged.
The installer refuses a changed dependency recipe or duplicate install child.
All native libraries remain inside the new overlay under the diagnostic
canonical prefix `/opt/software/cp2k/development/spack-native-probe/<PARTITION>`;
this is a pilot, not an accepted CP2K delivery or a publication.

Jobs `1381871` (16V100, one GPU) and `1381876` (DSPRHBM, eight CPUs) have
four-hour limits and immutable snapshot `diagnostics/cp2k-spack-install-20260918-v1`.
Results are under `runs/cp2k-spack-install-20260918-a-<PARTITION>/results`.
This is the first install-stage attempt, following successful resolver probes;
its retry policy is recorded separately. The success check requires all locked
dependencies to be installed and specifically checks `libplumedKernel.so`.
It explicitly records `cp2k_built: false`, `scientific_verified: false` and
`published: false`; native CP2K integration/ELPA linkage/runtime packaging and
scientific/performance acceptance still remain.

The initial install jobs failed deterministically in COSTA's CMake MPI C++
try-link (`1381871`: 8:13, `1381876`: 8:48, both exit `3:0`). The underlying
`CMakeConfigureLog.yaml` in each terminal overlay shows `libhcoll.so.1 not
found` and unresolved `hcoll_*` symbols, not a missing MPI header or a source
compilation error. The logs were recovered read-only as
`results/costa-CMakeConfigureLog.yaml`. Site `libmpi.so` has a stale
`/opt/mellanox/hcoll/lib` RUNPATH; the loaded module compensates with the
standalone HPCX 2.25.1 HCOLL/SHARP library paths, which Spack's clean build
environment had removed.

Install repair 1 adds only the external OpenMPI runtime environment to the
Spack environment (the exact HPCX HCOLL/SHARP directories and site prefix
variables). `cp2k_spack_mpi_repair.py` rejects any other configuration change,
preserves the old lock and configuration, then explicitly reconcretizes and
repeats native graph/mirror validation before installing. It does not set
`--dirty`, disable MPI/COSTA, reuse a failed install prefix, or alter upstream
sources. Jobs `1382045` (16V100) and `1382054` (DSPRHBM) use snapshot
`diagnostics/cp2k-spack-install-20260918-v2`, with original probe lineage and
`diagnosed_repairs_used: 1` recorded. Their results are under
`runs/cp2k-spack-install-20260918-b-<PARTITION>/results`. No incidental retry
was used; at most one more diagnosed install repair remains if needed.
Both repaired native locks and mirror-only fetches passed. Comparing their
package names, versions, variants/compiler flags, external identities and CPU
architectures against the original locks found only the intended OpenMPI
environment change (33 total GPU nodes / 31 CPU nodes unchanged). Repaired
lock SHA-256 values are `a11e6d7039a12864d4d9d39d79b100de6c79876fad093ae37d9cb31cf978731f`
(16V100) and `3e7abe04768f5b8ef641fda299582baf3a7df88c1384f8078a729714de1c9e0e`
(DSPRHBM). Both jobs installed COSTA, COSMA, SpLA, libvori and LIBXSMM,
passing the original MPI fault, but failed in Libint (`1382045`: 28m08s,
`1382054`: 29m00s, exit `3:0`).
The MPI repair passes 31 Spack tests and the complete 231-test suite.

Install repair 2 addresses the exact subsequent failure: Libint 2.6.0's
Makefile hardcodes `/bin/rm`, while the minimal SIF contains only bash/sh
under `/bin` and the read-only node tools are bound under `/usr/bin`. Both
logs show `make[3]: /bin/rm: No such file or directory` / Error 127. The
terminal filesystems are clean with about 2.8 GiB free; this was not ENOSPC,
OOM, or a compiler incompatibility. Spack's recorded build environment still
has `-O3 -march=native -mtune=native` in `SPACK_CFLAGS`, `SPACK_CXXFLAGS` and
`SPACK_FFLAGS` (the visible Libint command invokes the compiler wrapper).

Jobs `1383003` (16V100) and `1383002` (DSPRHBM) use immutable snapshot
`diagnostics/cp2k-spack-install-20260918-v3`. They clone the respective
**terminal** MPI-repaired native checkpoint, verify its SHA and exact repaired
lock, bind `/usr/bin:/bin:ro`, and resume the concrete installation with no
reconcretization or source/feature changes. Completed independent dependencies
remain reusable only on the same native partition. Failed images are untouched.
Only the new unmounted verified clones are expanded from 4 to 16 GiB to
provide headroom for generated Libint code and PLUMED objects; this is not
presented as the failure cause. Results are under
`runs/cp2k-spack-install-20260918-c-<PARTITION>/results`.
Each receipt records `diagnosed_repairs_used: 2` and
`incidental_retries_used: 0`; no further diagnosed installation retry is
authorized in this chain. The repair passes 33 Spack-focused tests and all
234 controller tests. These jobs are still running, not accepted dependencies.

Validation for this added seed/install implementation: 28 Spack-focused
tests and the complete 228-test controller suite passed, along with shell
syntax and Python import/compile checks. This includes refusal of live or
failed probe parents, changed recipes, duplicate install submissions and
unapproved extra repairs. These checks do not establish build completion.

## Verified existing work (2026-09-11)

CP2K was compiled on SAI outside Actions. Job `1216695` completed installation
then failed its final command because `libcp2k.so.2026.2` was not on the library
path. Repack job `1226525` completed and produced
`runs/cp2k-repack-1216695/cp2k-latest-v100.sif` (SHA256
`f2c9b32f9314c96f4c840d3c5027227235019c749eafce6fd541bd47adf7badf`).
Its GPU-allocation log identifies CP2K 2026.2 revision `6d276e9` and flags
`omp parallel scalapack dbcsr_acc openblas offload_cuda cusolvermp cusolvermp_nccl`.
The final SIF was independently inspected: its install path is incorrectly
`/opt/software/latest-v100`, not `/opt/software/cp2k/latest-v100`, and it lacks
saved runtime metadata. Manually reconstructing its diagnostic environment gives
no missing `ldd` libraries, but that is not scientific acceptance or publication.

TBLITE/DFT-D4 dependency job `1227313` succeeded. Its archive and the project
cache copy have SHA256
`a2f54e22b9397841cb414696abbcd001d94b3599eb6aeabe222d0dd4e8e69a52`.
No accepted CP2K catalog/module existed at the time of inspection.

## Native artifact contract

Each of DSPRHBM, 4V100, 16V100 and 8V100V0 must build independently on that
partition with `-march=native -mtune=native`; CPU dependency selection is taken from modules
resolved on its compute node. In particular, 8V100V0 is Skylake AVX-512 without
VNNI and uses the site's AVX2 MPI/BLAS builds. The artifact prefix is always
`/opt/software/cp2k/<track>/<build_id>/<PARTITION>` and survives export unchanged.
The canonical identity binds its upstream ref/SHA, recipe digest and target;
the partition directory uses `DSPRHBM`, `4V100`, `16V100` or `8V100V0`. Source,
compilation and installations remain inside the file-backed overlay/SIF.

The final-SIF verifier checks saved provenance, native CMake flags, all required
feature switches, runtime version flags, correct ELPA 2026/cuSOLVERMp/NCCL
resolution, installed scientific data, and the absence of transient RPATHs.
Runtime environment and data cannot depend on `/workspace`, `/input` or the
controller snapshot. The module launcher uses installed `CP2K_DATA_DIR`; the
same absolute `/opt` layout can later be used on physical storage with the same
read-only system dependencies. The current recipe still vendors PLUMED and
LIBXSMM shared objects from the old CP2K toolchain; this is incomplete (see
the loader failure above) and does not satisfy build-time independence.

The build records the observed runtime in `share/sai/native-entry.json`, then
packages the shared inventory, native Tcl fragment and `modulefiles/cp2k/...`
selector inside that same installation folder. TBLITE, LIBXS and DBCSR stay
below its `dependencies/`; the export stage copies only the selected prefix.
The final SIF check compares every installed file with the packaged inventory.
The native module loads the recorded site dependency modules, sets
`CP2K_DATA_DIR` to the packaged data and exposes `bin/cp2k.psmp` directly. It
records the observed `/opt/modules/modulefiles/devtools` search root so that
loading does not depend on a caller's default `MODULEPATH`. It contains no
Apptainer launcher. Physical export and native scientific/speed
acceptance still require a separate allocated run.

Candidate builds are not published or cache-reused before a verified scientific
benchmark tied to their exact SIF, launcher, input bytes and verifier succeeds.

## Source and baseline

- Candidate source: CP2K commit `6d276e9c480f2b55f5a0b9166e22d20d9a3f8da0`.
- Baseline module: `cp2k/2026.1-cuda12.9-sm70-auto`.
- CPU baseline module: `cp2k/2025.1-cpu-auto` (version 2025.1, revision
  `git:9635df4`, Open MPI 5.0.8); 2026.1's installed binary requires a GPU.
- Baseline installation: `/opt/apps/cp2k/cp2k-2026.1-avx512`.
- Baseline version checks must run in a GPU allocation.
- Recheck upstream branch HEAD before selecting the final release candidate.

## Required feature parity

Baseline version output includes the following features. The candidate must
verify these from its own build configuration and runtime version output.
Existing dependency directories alone do not prove ABI compatibility.

| Baseline feature | Candidate configuration or verification |
| --- | --- |
| omp | Check OpenMP compiler detection and runtime flags |
| parallel, scalapack | `CP2K_USE_MPI=ON`; verify ScaLAPACK linkage |
| mpi_f08 | `CP2K_USE_MPI_F08=ON` |
| dbcsr_acc, offload_cuda | CUDA-enabled DBCSR; `CP2K_USE_ACCEL=CUDA` |
| libint | `CP2K_USE_LIBINT2=ON` |
| fftw3 | `CP2K_USE_FFTW3=ON` |
| libxc | `CP2K_USE_LIBXC=ON` |
| elpa | `CP2K_USE_ELPA=ON`; verify compatible GPU build |
| cosma | `CP2K_USE_COSMA=ON`; inspect CUDA dependency build |
| xsmm | Check latest LIBXS/LIBXSMM integration and required versions |
| plumed2 | `CP2K_USE_PLUMED=ON` |
| spglib | `CP2K_USE_SPGLIB=ON` |
| libdftd4, mctc-lib, tblite | `CP2K_USE_DFTD4=ON`, `CP2K_USE_TBLITE=ON` |
| libvori, libbqb | `CP2K_USE_VORI=ON`; verify both runtime flags |
| hdf5 | `CP2K_USE_HDF5=ON` |

Also requested for the GPU candidate: `CP2K_USE_CUSOLVER_MP=ON`.
Do not silently disable unavailable features to pass a build.

CP2K 2026.2 requires both LIBXS 1.0.0 and LIBXSMM for the old XSMM capability;
the recipe builds LIBXS, DBCSR and the seven-library TBLITE dependency chain
natively inside each target overlay and adapts the
site's pkg-config-only Libint2/LIBXSMM to CMake. The later runtime-vendoring
change copies LIBXSMM shared objects; the build still depends on the old
CP2K toolchain and must be replaced by independent dependencies.
ELPA discovery is pinned to the loaded 2026.02.001 module, never to the old
2024 toolchain copy. The successful TBLITE archive establishes working locked
source versions, but its own precompiled AVX-512-linked executables are not
blindly reused on AVX2 targets. Each target compiles TBLITE 0.7.0 and DFT-D4 4.2.0
from the same checksum-locked source archives in the existing project cache.

The older CPU baseline advertises `libgrpp` and `quip`. In 2026.2 libgrpp is
compiled unconditionally and no longer appears in version flags; the recipe
records source evidence for this equivalence. QUIP's interface was removed
upstream before 2026.1. The user explicitly approved omitting QUIP from CP2K
2026 on 2026-09-11. It is the sole approved parity exception, recorded in
`upstream-feature-changes.json` and benchmark reports; no other missing
baseline feature is implicitly waived, and no legacy QUIP companion is planned.

## Probe evidence

- Job `1214324`: standalone CUDA/MPI DBCSR build succeeded.
- Job `1215004`: DBCSR build and install succeeded; CP2K configuration failed
  because CuSolverMP include/library paths were not discovered.
- Job `1215480`: submitted with `nvmplibs/26.7-tmp` and explicit CP2K
  CuSolverMP/NCCL roots; timed out while compiling at about 75%.
- The current probe is not feature complete. CP2K optional dependencies default
  to OFF, so CUDA/MPI configuration alone does not establish feature parity.
- CUDA Toolkit is provided by SAI at `/opt/devtools/nvidia/cuda-12.9.1`.

## Remaining acceptance

- Build and run the full-feature candidate on the requested CPU/GPU targets.
- Persist and verify the container artifact and runtime launcher.
- Integrate CP2K into the controller/workflow without changing ABACUS behavior.
- Run controller tests and an ABACUS regression check.
- Compare candidate and baseline on the same fixed input, node/GPU allocation,
  MPI ranks, thread count, and scientific settings; validate results before
  reporting timings. Record input identity, versions, energies and wall times.
  TBLITE is the only approved input-syntax exception: CP2K 2026.1 selects the
  backend with `&TBLITE T`, while the pinned development source requires
  `GFN_TYPE TBLITE`. Both exact input hashes and a reversible two-line syntax
  contract are checked; GFN2, geometry, SCF settings and tolerances stay fixed.
  This correctness comparison is not described as same-byte and reports no
  cross-version TBLITE speed ratio.
- Use one warmup and at least three alternating repeats in fresh directories;
  validate energies and forces before reporting a speed ratio. CPU baseline
  2025.1 does not support TBLITE/DFT-D4: those candidate-only paths require
  independent checked scientific references, not self-consistency alone.
- A fixed periodic 64-water (192-atom) GPW workload is the required compute-time
  benchmark, separate from the tiny correctness probes. It must spend at least
  two seconds in CP2K's reported core computation on each run; timings report
  both CP2K TOTAL MAXIMUM and elapsed wall time. It is a workload-specific
  comparison, not a claim about every production calculation. A separate ELPA
  input must record actual `cp_fm_diag_elpa` execution in its timing section.
  Both ELPA inputs set `GLOBAL/TIMINGS/THRESHOLD 0` and `TIMINGS_LEVEL 1` so
  the default two-percent output filter cannot conceal short solver calls.
  Two-node GPU comparisons keep one rank/GPU per node, with four physical
  cores per rank on 4V100/16V100 and three on 8V100V0, matching site mappings.
  DSPRHBM keeps eight ranks/node and two OpenMP threads/rank. Recorded Linux
  CPU topology distinguishes physical cores from SMT siblings and rejects
  overlapping core assignments even when logical CPU sets are disjoint.
  CP2K core time excludes the preceding SIF integrity checks; elapsed time
  includes per-rank hashing and container startup. These SIF measurements do
  not establish a native-export speed claim.
- Artifact validation recursively inspects all packaged ELF objects and
  symlinks, rejects path-valued DT_NEEDED, old installation/build/home RPATHs,
  and escaping symlinks. Only the current prefix and explicit system runtime
  roots are allowed. This validates the loader contract, not a physical copy
  deployment that has not been performed.

## Fresh canary handoff (2026-09-15)

The last full-feature canary, `manual-cp2k-4v100-20260911-a1` / Slurm `1272985`,
used source `6d276e9c480f2b55f5a0b9166e22d20d9a3f8da0` and controller `4c69ae5`.
Its log reaches CMake generation, then the old cache parser reports
`required CP2K option is disabled: MPI`. The committed parser fix reads one
physical cache line at a time and has regression coverage for blank/comment
lines and duplicate options. This fixes that parser defect; no subsequent
full-feature build or scientific comparison has passed yet. Baseline job
`1273757` only reports `CP2K_BASELINE_INPUTS_CHECKED_NOT_CANDIDATE_ACCEPTANCE`.
Both observations were rechecked from SAI logs on 2026-09-15.

Start a fresh manual canary with `tracks=development`,
`targets=4v100-avx512`, empty `resume_run` and `reference_run`, and
`retry_releases=false`. The old canary uses delivery schema 2 and cannot be
resumed into the current canonical schema. The dedicated CP2K workflow has no
cron; the shared daily dispatcher owns scheduling. Missing upstream release
or prerelease channels are skipped, including in manual mixed-track requests,
and an entirely empty resolution creates no build matrix.

DSPRHBM added-feature acceptance still requires an explicit accepted GPU
`reference_run`. A later automatic lookup can select a completed, verified
GPU benchmark with the same fixture/verifier contract and revalidate it through
`accepted_reference`, pinning its run ID and evidence SHA before CPU submission.
The stored success marker alone is insufficient. Until that lookup is wired,
a CPU candidate without a supplied reference cannot be published.
