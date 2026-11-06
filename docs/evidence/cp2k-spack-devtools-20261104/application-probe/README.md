# CP2K compilation against copied dependency SIFs

CP2K v2026.2 compiled, installed and passed the full single-node scientific
checks on **DSPRHBM, 16V100, 4V100 and 8V100V0** using the prepared shared Spack
libraries. The final 4V100 rebuild fixed the ScaLAPACK/NCCL communication stall
and completed all 14 calculations without diagnostic interposition.

Source: `67b5da876dd6a76b8b021d5a04d1c81ba79a4c50`.
The three earlier accepted partitions use application recipe
`d9f0fb780442993a64a052531f403f7311b429734b9b93a9bd36cb102e1df5b6`.
The repaired 4V100 build uses
`57163d7ef63677e304aece8d15d518ca84bbe976164bb82cd0c4169c1c6b22b3`.
These are different recipes; the new source repair was rebuilt and tested on
4V100. The other accepted partitions were not rerun.
Compilation uses GCC 14.3.0, site OpenMPI 5.0.10, SAIBLAS 2603, CUDA 12.9.1 for
GPU targets, CMake 3.31.6 and the pinned shared ELPA provider.

| Partition | Compilation job | Build phase | Accepted verification | Accepted job elapsed |
| --- | --- | ---: | --- | ---: |
| DSPRHBM | 2198420 | 720.97 s | 2199646 on d9470n18 | 25m24s |
| 16V100 | 2198421 | 820.82 s | 2199518 on 16v100n25 | 16m28s |
| 4V100 | 2201497 | 1076.13 s | 2201497 on 4v100n18 | 27m31s (build + checks) |
| 8V100V0 | 2198456 | 1310.97 s | 2199520 on 8v100v0n12 | 30m49s |

The build-phase durations include checkout, remaining native dependencies,
compilation and installation. They are neither pure CP2K compiler times nor a
controlled speedup comparison. The original four compilation allocations,
including 4V100 job 2198457, remain `FAILED 1:0` because their later science
stages failed. Clean build receipts, installation and loader checks establish
those successful compilation phases separately. The accepted 4V100 job includes
a fresh source build; the other accepted jobs verify copied completed builds.

Each installation receipt records `layout=shared-devtools` and its provider
contract. Prepared Spack libraries were reused without rebuilding them. DBCSR,
TBLITE and dependencies outside this first migration still follow the native recipe.

## Preservation and scope

Builds copied accepted dependency SIFs to separate inodes and installed CP2K into
fresh overlays. Verification jobs copied those clean application overlays and
dependency SIFs again, then mounted them read-only. Source/copy SHA-256 checks,
independent inodes, sealed modes, filesystem checks and natural managed-overlay
teardown were verified. The result is a **copied dependency SIF plus application
overlay**, with these container installation prefixes:

```text
/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-rd9f0fb780442/{DSPRHBM,16V100,8V100V0}
/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-r57163d7ef636/4V100
```

Original environments were preserved. Build/cache/expanded files and container
temporary paths remain below `/org/org-open-sai/proj-sai-hpc-software`; MPI
shared-memory buffers use `/dev/shm`. This test does not use physical `/tmp`
scratch or perform physical `/opt` installation, public SIF/module publication
or shared-provider activation. The production provider registry remains empty.

## Scientific checks

Each accepted partition completed seven unchanged fixtures at one and two MPI
ranks: PBE GPW/OT, ordinary diagonalization, ELPA, DFT-D4, TBLITE, 64-water GPW
and 64-water ELPA. Across four partitions, 56 calculations and 28 rank-count
energy/force comparisons passed. Two-rank GPU tests use two distinct GPUs,
independently confirmed by bus IDs in NCCL logs.

The 42 GPU-versus-CPU comparisons have maximum energy difference
`5.3887561080046e-11 Ha` and maximum force-component difference
`1.9999999920083944e-11 Ha/Bohr`. Unchanged tolerances are `1e-6 Ha` and
`1e-5 Ha/Bohr`. This establishes single-node correctness, not multi-node or
performance acceptance.

## Retained failures

- Original CPU science merged MPI stderr containing `abort` into CP2K output.
  Later probes separate MPI stderr. A subsequent one-thread CPU test hit 600
  seconds while SCF made progress; accepted CPU tests use the established two
  threads and an 1800-second case limit, with unchanged scientific inputs.
- Original GPU probes allocated one GPU for two ranks, which failed cuSOLVERMp.
  Corrected probes allocate two GPUs and two Slurm tasks.
- Agent-issued undersized GPU `srun` diagnostics triggered the site's resource
  policy and cancelled whole jobs 2199161, 2199165 and 2199336. They remain
  cancelled and their copies were not reused. Separate pending-only
  cancellations avoided unavailable nodes.
- 4V100 jobs 2199647 on 4v100n18 and 2200242 on 4v100n20 each passed 11 samples,
  then timed out in `water64-gpw/2` after 600 seconds. Both ranks selected
  distinct GPUs and completed initial NCCL communicators. Rank 0 began a later
  initialization without a corresponding rank-1 entry. Live inspection showed
  busy CPU ranks and idle GPUs. ECC or storage failure has not been established.
  Communication diagnostics are separate from acceptance.

The subsequent [communication investigation](communication-diagnosis/README.md)
located the ranks in ScaLAPACK MPI receive and NCCL bootstrap. Adding an MPI
progress barrier in an isolated interposition trial completed the stalled case
with correct energy/forces. The guarded source repair subsequently passed the
[real rebuild and full verification](rebuilt-4v100/README.md) in job 2201497.
The formerly stalled case completed in 237.49 seconds, and the source repair
receipt and changed `libcp2k` hash confirm the actual rebuilt implementation.

## Evidence

[accepted.json](accepted.json) combines the four final accepted outcomes and
all 42 GPU-versus-CPU comparisons, with each application recipe and source
archive hash. [summary.json](summary.json) preserves the earlier snapshot of
four build receipts, three accepted verifications and unsuccessful attempts;
its partial status is intentionally unchanged.
[build-and-three-partition-evidence.tar.gz](build-and-three-partition-evidence.tar.gz)
contains the summary and 863 individually compressed raw files. Every raw and
compressed hash was checked after download; [archive.json](archive.json) records
the archive hash and source. Collection checked frozen controllers, inputs,
binaries, images, preservation records and terminal scheduler accounting.

The [rebuilt 4V100 archive](rebuilt-4v100/raw.tar.gz) adds 116 raw records whose
compressed and uncompressed hashes were checked independently after download.
The communication archive retains the preceding failed controls and diagnostic
success separately from actual-source acceptance.

Implementation `ab56139` passed 1,212 tests with two skips in
[CI 41892929124](https://github.com/AI4SAI/sai-hpc-software/actions/runs/41892929124).
CI validates workflow code; cluster evidence establishes compilation/science.
