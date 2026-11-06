# Physical CP2K dependency reuse, November 6

The administrator installed all 39 prepared library variants into their final
`/opt/devtools` prefixes. `install.sh --verify` returned zero and verified every
installed inventory against the accepted dependency SIFs on November 6.
The [installation receipt](installation.json) pins the registry and raw
[verification output](install-verify.log). All four partitions are present,
including the previously missing 16V100 HDF5 prefix.

The PR registry now selects those physical providers. The controller checks
versions, ABI, installed file inventories and native compiler flags, and skips
the Spack build allocation when the provider is valid. Application SIFs retain
the provider receipt and use the read-only physical libraries. DBCSR, TBLITE,
LibXC and other inputs outside the provider contract retain their existing
application build/selection paths.

CP2K master `bc428196533ec1afec9844324d6ed8d98daaa661` now requires TBLITE 0.8.0
and DFT-D4 4.3.1. The complete upstream TBLITE bundle supplies both and their
matching subprojects. The runner verifies and stages the archive before any
compute job; source declarations and archive hashes must match. Release
TBLITE 0.6.0 retains its reviewed gradient patches. None of these changes
requires rebuilding the installed Spack libraries.

## Completed physical-provider qualification

[Workflow 42139209945](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42139209945)
succeeded at 03:06 UTC on November 6 using controller
`6613ee2c1b009b63eaeeb0c8d8cda8553f2f97df` and application recipe
`3561960f4431188f0debe4ba95facb3571dd0a078a0cc846e44ea0cb7df030c8`.
All eight standard application builds used the installed providers. No Spack
build allocation or dependency overlay was created. The shared toolchain uses
GCC 14.3.0 and OpenMPI 5.0.10; GPU builds use CUDA 12.9.1.

The source revisions were resolved when the workflow started:

- Release: `v2026.2`, `67b5da876dd6a76b8b021d5a04d1c81ba79a4c50`.
- Development: `master`, `bc428196533ec1afec9844324d6ed8d98daaa661`.
  Its source label is `master-2026-11-05`; these builds and publication are
  from November 6.

| Track | Partition | Build job | Build elapsed | Science job | Science elapsed |
| --- | --- | ---: | --- | ---: | --- |
| release | DSPRHBM | 2232891 | 00:13:31 | 2233133 | 00:23:51 |
| release | 16V100 | 2232887 | 00:16:40 | 2233206 | 00:14:36 |
| release | 4V100 | 2232890 | 00:23:51 | 2233407 | 00:11:58 |
| release | 8V100V0 | 2232885 | 00:23:42 | 2233406 | 00:15:54 |
| development | DSPRHBM | 2233817 | 00:19:38 | 2234193 | 00:24:52 |
| development | 16V100 | 2233755 | 00:26:52 | 2234259 | 00:12:44 |
| development | 4V100 | 2233724 | 00:31:12 | 2234329 | 00:07:51 |
| development | 8V100V0 | 2234006 | 00:39:27 | 2234937 | 00:14:34 |

Every job completed with `0:0` on account `proj-sai-hpc-software`. These build
times include application compilation, export and build checks; they are not a
controlled performance comparison. The release SIFs are 125.04–127.92 MiB.

Each SIF passed seven fixtures with one and two ranks, including two-node host
MPI: PBE OT, ordinary diagonalization, ELPA, DFT-D4, TBLITE, 64-water OT and
64-water ELPA. Revalidation with the original immutable controllers reparsed
112 calculations, 56 rank-count comparisons and the actual rank/GPU mappings.
All 84 GPU-versus-CPU comparisons also passed. Maximum cross-partition energy
and force differences were `3.73e-11 Ha` and `2.10e-11 Ha/Bohr`, against unchanged
`1e-6 Ha` and `1e-5 Ha/Bohr` tolerances. Both tracks passed with the same guarded
MPI/NCCL source repair included in their build recipe.

Direct SIF inspection confirmed that each embedded provider receipt matches the
installed provider and that shared package prefixes are absent from the image.
All 24 managed-overlay receipts show successful natural driver exit, including
the read-only exports. Embedded source receipts pin TBLITE 0.6.0 for release,
TBLITE 0.8.0 for master and LibXC 7.1.2 for master. Application SIFs intentionally
depend on the selected physical `/opt/devtools` prefixes at runtime.

## Published beta and audit trail

The four Lmod/SIF/host-MPI beta checks also passed: `2235490` (16V100, 32 s),
`2235492` (4V100, 21 s), `2235494` (8V100V0, 50 s) and `2235496` (DSPRHBM, 42 s).
The existing `cp2k/master-beta-later` entry now selects:

```text
master-beta-20261106-bc428196533e-r4d85319d4295-b71b0be44395a
```

The publication receipt and public SIF checksums were verified after rotation.
[workflow.json](workflow.json) records the successful GitHub run and tested commit.
[qualification.json](qualification.json) indexes all eight SIFs, source and
recipe identities, numerical summaries, 20 successful Slurm allocations and
the exact beta generation. [archive.json](archive.json) pins
[evidence.tar.gz](evidence.tar.gz), whose 2,735 raw files were individually
verified after download. It contains the full `accepted.json`, original
controllers, collector, requests, job scripts, raw outputs, receipts and beta
traces. The preserved scheduling record documents a pending-only 30-minute
backfill minimum for release science job `2233406`; its original one-hour limit,
script, input and two-node requirements were retained.

The original [installation receipt](installation.json) remains unchanged: its
`application_qualification: pending` records the earlier 00:43 UTC observation.
The completed qualification above supersedes that status. Earlier copied-SIF
probes remain separate historical evidence. No additional administrator
installation is required to use these shared providers in the workflow.
