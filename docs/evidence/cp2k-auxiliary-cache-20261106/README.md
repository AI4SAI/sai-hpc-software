# CP2K auxiliary cache qualification, November 6

Six CP2K builds and six scientific acceptance jobs completed successfully on
account `proj-sai-hpc-software`. The three warm builds restored accepted
dependencies into different application prefixes, compiled CP2K itself and
passed the same numerical checks as their cold counterparts. No cached
dependency was recompiled on a hit.

Sources and build controller were pinned throughout the campaign:

- Development: `master`, `bc428196533ec1afec9844324d6ed8d98daaa661`.
- Release: `v2026.2`, `67b5da876dd6a76b8b021d5a04d1c81ba79a4c50`.
- Controller: `f44b81d51d78c5c1b8aa58ec6bd6bd914f084537`.
- Build recipe: `f8e67c4b173aa0f33a147b56f63f442267188143416cc9ccebb6061d68bc79b9`.

The toolchain uses the already accepted physical Spack providers, GCC 14.3.0,
OpenMPI 5.0.10 and, on 16V100, CUDA 12.9.1. Each pair uses one source revision;
truthful alternative labels (`master` / `master-2026-11-05` and `2026.2` /
`v2026.2`) create distinct installation prefixes. The dependency contract and
archive hashes must remain equal across the pair.

## Timings and cache contents

| Measured stage, seconds | master 16V100 | master DSPRHBM | v2026.2 16V100 |
| --- | ---: | ---: | ---: |
| LibXC cold | 469.886 | 102.537 | 0.047 (site selection) |
| TBLITE and subprojects cold | 83.251 | 84.470 | 66.266 |
| LibXS cold | 17.647 | 16.922 | 8.155 |
| wignernj cold | 1.007 | 1.996 | Not required |
| DBCSR cold | 81.797 | 76.732 | 75.663 |
| Total cold dependency stages | 653.588 | 282.657 | 150.131 |
| Cold cache packing | 29.651 | 6.224 | 4.318 |
| Warm restore and verification | 8.789 | 6.719 | 2.165 |
| CP2K core cold | 904.835 | 684.948 | 744.736 |
| CP2K core warm | 833.085 | 702.023 | 760.068 |
| Compressed cache, bytes | 198057560 | 55409181 | 21383370 |

The cold stages include configure/build/install of each private dependency;
release LibXC selects the existing site library. The warm stage includes
contract lookup, archive and member checks, extraction and metadata relocation.
Warm receipts contain only the CP2K core compilation timer. Packing is paid
once by a cold build; the cache remains unusable until scientific publication.

These are descriptive measurements on the recorded allocations. Different
nodes and shared-filesystem conditions prevent treating total job differences
as a controlled performance ratio. In particular, CPU science sometimes waited
on filesystem reads; its elapsed time is not a cache performance gate.

| Case | Phase | Build job | Build elapsed | Science job | Science elapsed |
| --- | --- | ---: | --- | ---: | --- |
| master 16V100 | cold | 2250613 | 00:28:25 | 2251284 | 00:13:34 |
| master 16V100 | warm | 2251872 | 00:16:35 | 2252364 | 00:12:07 |
| master DSPRHBM | cold | 2250614 | 00:17:38 | 2251031 | 00:43:14 |
| master DSPRHBM | warm | 2252118 | 00:14:36 | 2252471 | 00:27:02 |
| v2026.2 16V100 | cold | 2250615 | 00:16:39 | 2251874 | 00:11:21 |
| v2026.2 16V100 | warm | 2252210 | 00:15:24 | 2255560 | 00:13:27 |

All twelve allocations are `COMPLETED 0:0`. The caches are at
`/org/org-open-sai/proj-sai-hpc-software/cache/cp2k-auxiliary/<key>/`, with only
`payload.tar.gz`, `manifest.json` and `accepted.json` per accepted entry.

| Case | Contract key | Archive SHA-256 |
| --- | --- | --- |
| master 16V100 | `17be8ee0deb0865687dc85d90e46814858da3963233d5384c30105cb04c7d19f` | `179a392c5baf8127479babae161be9ef6a6ec84d1fd00ec6f00fddeb639deb14` |
| master DSPRHBM | `47f2e834dcaedbb64d804ce8a2c14fc6bb179dac7c2c58fa5794b070804526b8` | `486d704ae13b894425a8e2400628e89fce0239ac8d2b0e8227148fecaadb638b` |
| v2026.2 16V100 | `3e754614232e6206e0808fe0736316943c1612c5b0fdf8a6339fdcf80cd4e0cf` | `e89d7c0de659b97aad307dd6a0b4948d09e34529bf260797b233c938292b1c50` |

## Numerical and provenance checks

The original immutable controller reparsed all 84 calculations: seven cases
per SIF at one and two ranks, with the latter spanning two nodes through host
MPI. Cases cover PBE OT, ordinary diagonalization, ELPA, DFT-D4, TBLITE and
64-water OT/ELPA. All 42 rank-count comparisons and each artifact's solver
comparisons passed. All 42 additional cold/warm comparisons also passed:
maximum energy difference `2.433e-11 Ha`, maximum force-component difference
`1.500e-11 Ha/Bohr`, below unchanged `1e-6 Ha` and `1e-5 Ha/Bohr` tolerances.

Validation includes exact source/recipe/SIF identities, input and launcher
hashes, recorded rank/CPU/GPU mappings, publication receipts and cache manifest
acceptance. All 18 managed-overlay receipts report successful natural teardown
with no forced stop. The final SIF does not require the project build cache at
runtime; it still uses the selected physical site providers.

The earlier cold attempts `2240109` and `2240110` failed while packing DBCSR's
archive: debug information referenced LibXS Fortran source in the old install
prefix. DBCSR now uses `-ffile-prefix-map` during compilation. No binary file is
patched during caching. A native GCC 14 Fortran regression confirms the archive
and module remain byte-identical after restoration and link/run a consumer
after the original prefix is removed. The original failures, the corrected
fixture and pending-only cancellations `2240112` / `2250347` remain archived.

Two completed development science jobs had aged out of `slurmctld` before the
monitor resumed. `squeue` returned `Invalid job id specified`; durable `sacct`
records still reported success. The monitor repair handles only this exact
queue error and then requires the normal accounting and scientific checks.
It was applied to observation through `operations/resume_science.py`, which
asserts that only `monitor()` differs before executing that function with the
original controller's globals. Frozen on-disk controllers and original
verifiers were preserved. The collector subsequently reran those original
verifiers without this adapter. Missing/failed accounting, unrelated scheduler
errors and bad scientific results are covered by the 19-test acceptance suite.

[controller-audit.json](controller-audit.json) confirms the final production
monitor repair changes no build script, cache contract input or scientific
verifier. It changes the overall application recipe fingerprint, but all three
accepted cache keys remain compatible.

## Evidence and scope

[qualification.json](qualification.json) contains all six exact SIF paths and
hashes, identities, timings, numerical comparisons and scheduler accounting.
[archive.json](archive.json) pins the 14,525,456-byte
[evidence archive](evidence.tar.gz), SHA-256
`4be0409618bdcf25dd22dfe0d7d881aaec3790196edef84e9b92fac8486657c0`.
All 2,083 raw files were checked against [file-hashes.json](file-hashes.json)
after download. The archive includes requests, job scripts, raw results,
controllers, collectors, operation logs and original failed attempts. Compiled
dependency archives and SIFs remain on project storage and are not in Git.

This qualification covers warm reuse on master 16V100/DSPRHBM and release
16V100. Other partitions get their own contract keys and populate accepted
caches through the normal first build. Versioned artifact publication completed;
the public beta alias was not rotated by this campaign. No administrator
installation is required for the cache.
