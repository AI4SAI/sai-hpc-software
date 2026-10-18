# CP2K installation and ELPA closure evidence

Current state: the private-ELPA release has accepted/published deliveries on
4V100, 16V100 and 8V100V0; DSPRHBM's recovery dependencies completed and its
application phase is in progress.
[Third accepted partition and independent validation](native-elpa-third-publication.json).
The exported 16V100 native tree also passed version/loader and three PBE cases
at its canonical prefix with the legacy HCOLL alias absent, using a private
mount namespace and host runtime libraries. Physical `/opt` installation is
not claimed. [Native runtime proof](hcoll-native-alias-free.json),
[reproducer](hcoll-native-alias-free.py),
[two-node native proof](hcoll-native-multinode-alias-free.json),
[two-node diagnostic derivation](prepare-hcoll-native-multinode.py),
[real ELPA diagnostic-copy normalization](real-elpa-normalization.json).

All four original-recipe targets had built SIFs; none became a
published four-partition release. 8V100V0 `1838191` and DSPRHBM retry `1838412`
completed successfully after the earlier snapshots below.
[8V100V0 complete artifact](third-artifact-8v100v0.json),
[DSPRHBM complete artifact](dsprhbm-retry-artifact.json).
The original GitHub workflow ended with failure and remains unchanged.

The system ELPA allocation problem is independently isolated by a minimal
MPI/C probe and GDB: [raw source](elpa-allocate-probe.c),
[CPU flags, faulting instruction, module text and hashes](system-elpa-isa-probe.json).
The installed binary contains AVX2 and AVX512 kernels, but its generic allocation
path executes AVX512 before choosing a kernel. It faults on the tested AVX2-only
16V100 node. This does not imply that ELPA intrinsically requires AVX512.

A native ELPA 2026.02.002 pilot installed under the CP2K software prefix and
completed compilation and small OT/ordinary diagonalization/DFT-D4/TBLITE checks:
[numerical evidence and limitations](native-elpa-16v100-pilot.json).
The small nominal ELPA case fell below the default selection threshold and
therefore does not prove ELPA solver execution. Fresh candidate-only acceptance
forces ELPA and checks solver timers, with one/two-node numerical comparisons
and larger water fixtures. The old 2026.1 comparison is retired by user request.

The following sections preserve the sequence of earlier installation snapshots.

## Original installation correction

Both formal workflows `39054631316` and `39056762371` completed their native
dependency stages on all four targets. Seven completed application attempts
compiled and linked CP2K fully, then failed during installation of
`/workspace/build/src/libcp2k.so.2026.2` into read-only `/lib64`.
The corrected-runtime 8V100V0 application was still running at the first audit.
No formal SIF had been produced.
[Original logs, accounting, identities and preserved overlays](failed-builds.json).
The final old 8V100V0 application `1837029` subsequently failed at the same
installation step at 08:35, completing the eight failed application attempts.
[Retained terminal evidence](old-8v100-terminal.json).

The pinned CP2K source declares `CMAKE_INSTALL_LIBDIR` as `CACHE PATH`.
An untyped command-line value `-DCMAKE_INSTALL_LIBDIR=lib64` becomes absolute
relative to the configure working directory. With the container working
at `/`, the cache and generated install rules contain `/lib64`, even though
`CMAKE_INSTALL_PREFIX` is correct. The same error affects CMake exports.

The recipe now uses `-DCMAKE_INSTALL_LIBDIR:STRING=lib64`. The feature contract
also rejects absolute, missing or unexpected library directories before the
long compilation begins. Reconfiguring regenerates library and export install
rules together; merely changing `cmake --install --prefix` would leave absolute
rules intact. No system `/lib64` write or feature removal is required.

A real CMake regression configures a shared library with the same upstream
cache convention and actual production options, installs it under a private
prefix, then builds and runs a consumer through its installed CMake export.
The old untyped option reproduces `/lib64` at configure time and is rejected
without attempting an installation there.

Recovery diagnostic `1837454` completed `0:0` in 18:52 on `4v100n09`.
Reconfiguration, installation, export and verification of the final SIF passed
on a copy of the completed 4V100 application image. Of 1,487 object files,
1,486 retained both their SHA256 and mtime; only the version-information object
was recompiled before relinking. All 1,643 CMake installation entries are now
under the delivery prefix. The original failed image's full SHA256 stayed
unchanged. [Recovery evidence and raw-file hashes](recovery-sif.json).

The 187,449,344-byte diagnostic SIF is retained at
`/home/stardust/sai-hpc-software/runs/cp2k-install-libdir-recovery-20261018-a/diagnostic.sif`,
with SHA256 `4ab1a5c534879236a45345c607addc48b901aedb030aa001ebf7186b6765bead`.
Verification used the SIF without the build overlay: version 2026.2,
source `67b5da876d`, GCC 14.3.0, all required features, 47 ELF files and no
missing `ldd` dependencies. PBE OT executed from the same SIF, converged in
46 SCF steps and exited normally, with energy `-34.446821294788720 Ha`.
The log reports one GPU and nonzero GPU grid work. Input data came from the
pinned smoke fixture; installed data, multi-GPU mapping and comparative
performance were not tested by this smoke.

This diagnostic SIF retains its original source identity. There is no
`artifact.path` or publication record, and it is not a formal artifact of
the corrected recipe. The two later formal SIFs below have separate identities
and actual checksum verification.

Production commit `c3b65b97f9d95ecb29a453713dfe292af20f6d39` passed
[CI 39078979787](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39078979787):
735 tests, 733 passed and two optional skips, in 39.123 seconds.
The three real installation/contract tests and 18 CP2K contract tests also
passed locally. Fresh four-target release workflow
[39078998459](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39078998459)
uses this commit and recipe
`b59c9f26a1846d5a3863c04cefc0f7b746b3078bc48cb0a8138d67084d28e150`.
At 08:11 Asia/Shanghai, 4V100 dependency `1837488` had completed `0:0` in
37:21, its checkpoint was sealed, and application `1837815` was RUNNING
on `4v100n01`. The source, controller, recipe, scripts and home-backed storage
were checked for both phases. The other dependencies remained RUNNING:
16V100 `1837489` on `16v100n02`, 8V100V0 `1837487` on `8v100v0n03`
and DSPRHBM `1837486` on `d9470n02`.
The live observer is retained in the home directory at
`diagnostics/cp2k-release-20261016/install-layout-fix/formal-progress.json`.
[Actual requests, scripts and frozen identities](formal-start.json).
[Stage handoff and artifact absence at this snapshot](formal-progress.json).

By 09:12, all four dependencies had succeeded and their sealed checkpoints
had been handed to application jobs. [Four stage handoffs](formal-applications.json).
4V100 application `1837815` completed `0:0` in 47:17 and 16V100 application
`1838194` completed `0:0` in 20:27. Their formal SIFs passed CP2K 2026.2,
GCC 14.3.0, required features, `ldd` and all 47 ELF checks. They are retained
under the exact build ID `v2026.2-g67b5da876dd6-rb59c9f26a184` in the project's
`containers/software/cp2k/release` catalog:

| Partition | Bytes | SHA256 |
|---|---|---|
| 4V100 | 187,445,248 | `1ec8fdf693f4273f7fa1c52fcc27b4aba858d6077a45ff569f2b421677aa3b00` |
| 16V100 | 186,290,176 | `c746fed344d9e909332e7fc40ead03bcc155458c02787cd1eac710ae87bfc355` |

[First artifact and log evidence](first-artifact.json),
[first independent complete SIF checksum](formal-artifact-checksums.json),
[16V100 artifact and frozen-script verification](second-artifact-16v100.json),
[both complete SIF checksums, paths and contracts](two-formal-artifacts.json).
The initial first-artifact metadata audit explicitly did not hash the SIF;
the later checksum collectors did read every byte and matched each manifest.

8V100V0 application `1838191` was still preparing the dependency image.
DSPRHBM `1838161` failed `255:0` in 22:15 before application compilation:
the fuse2fs mount exceeded Apptainer's 10-second startup deadline. The clone
had passed the before/after filesystem checks; subsequent read-only superblock
inspection reported clean state. No corruption diagnosis is supported by the
retained error. [Complete failure](dsprhbm-mount-failure.json),
[overlay health inspection](dsprhbm-overlay-health.json),
[original filesystem-check logs](dsprhbm-existing-fsck-logs.json).

At 08:00, read-only compute-node SSH samples measured whole-batch CPU usage
while Libint compiled: 4V100 used 6.046 of eight logical CPUs (75.57%), and
16V100 used 7.754 of eight (96.92%). Both had `make -j8`, eight GCC 14.3.0
compiler processes, no CPU quota and no throttling during the interval.
These are approximately ten-second samples, not whole-build utilization.
Counting only processes present at both ends misses most short-lived compiler
work; the cgroup counters include it. [Counters and raw-sample hashes](live-cpu.json).

The diagnostic SIF's installed data were independently read without running
the image: its manifest contains 280 data files; the runtime exports the exact
`CP2K_DATA_DIR`, and BASIS_MOLOPT/GTH_POTENTIALS bytes match the successful
fixture. The fixed source tries this directory after the working directory.
This static verification does not replace a dynamic installed-data lookup.
[Installed-data evidence](installed-data.json).

A bounded review found no confirmed blocker in the frozen formal acceptance
entry. One known difference remains: the launcher forwards the site's absolute
NCCL topology path without mounting that file. The installed NCCL library
continues successfully when the file is missing; communication and timing
effects still need the actual two-node test. The 130-byte MPI temporary path
also passed a localhost-only `/bin/true` launcher check, as did a 52-byte
control. This did not exercise CP2K MPI initialization or remote transport.
The first unavailable-component harness attempt is retained and explicitly
excluded from the passing result. [Readiness review](post-sif-readiness.json),
[valid MPI path probe](mpi-tmpdir-localhost.json) and
[retained harness failure](mpi-tmpdir-localhost-harness-failure.json).

[Prepared formal-SIF smoke helper](prepare-formal-sif-smoke.py) pins this
workflow, controller, recipe, artifact checksum and deployed launcher. It
prepares but never submits a job. The single job runs PBE OT, ordinary
diagonalization and the same OT input without local data copies, then compares
energies and forces. It reads the new formal SIF's installed runtime/data
before preparation; it cannot substitute the diagnostic SIF for that artifact.
The new data-reader function was exercised read-only against the recovered
SIF and returned the independently verified hashes above. Python parsing and
generated shell syntax checks passed. Formal-SIF smoke `1838355` subsequently
completed `0:0` in 45 seconds on `4v100n04`, with all three cases converged and
normally terminated. OT and diagonalization differed by `2.91e-13 Ha` in energy
and at most `2.71e-10 Ha/Bohr` in forces. The installed-data case had no local
basis/potential files before or after execution, differed by `1.42e-14 Ha`, and
had identical printed forces. Formal files remained unchanged. The job uses
one node, one GPU and one MPI rank and does not replace two-node science and
performance acceptance. The formal benchmarks `1838283` (4V100) and `1838359`
(16V100) were running at 09:12; no publication is claimed.
