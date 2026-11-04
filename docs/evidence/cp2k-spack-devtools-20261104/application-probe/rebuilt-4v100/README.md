# Rebuilt CP2K v2026.2 accepted on 4V100

Job **2201497** on **4v100n18** completed `0:0` in **27m31s**. It compiled and
installed the actual repaired source, checked loading/features, and passed all
seven scientific fixtures at one and two MPI ranks. No diagnostic `LD_PRELOAD`
interposition or MPI/NCCL configuration workaround was used.

The previously stalled `water64-gpw/2` calculation completed in **237.486 s**.
Against the accepted CPU sample, its energy difference was
`3.296918293926865e-11 Ha` and maximum force-component difference was
`1.3000000381735433e-11 Ha/Bohr`. All 14 CPU comparisons and seven rank-count
comparisons passed the existing `1e-6 Ha` / `1e-5 Ha/Bohr` tolerances. NCCL logs
confirm rank 0 used CUDA device 0, bus `5000`, and rank 1 used CUDA device 1,
bus `8000`.

## Build identity and preservation

- CP2K source: `v2026.2`, `67b5da876dd6a76b8b021d5a04d1c81ba79a4c50`.
- Frozen controller implementation: `ab561391e273dad3eb2287246b67f190f2f1ff14`.
- Application recipe: `57163d7ef63677e304aece8d15d518ca84bbe976164bb82cd0c4169c1c6b22b3`.
- Container prefix: `/opt/apps/cp2k/cp2k-release-v2026.2-g67b5da876dd6-r57163d7ef636/4V100`.
- Run: `/org/org-open-sai/proj-sai-hpc-software/runs/cp2k-devtools-app-20261105-n-4V100`.

The original dependency SIF and its separate-inode copy both retained SHA-256
`a9a2153148490b1ee78d0585d6050b132e0dfc25b4e3bdfaf339c47568ce147d`.
The application used a fresh overlay, sealed to mode `0400` after successful
build teardown and filesystem checks. Both writable and read-only phases
completed with natural fuse2fs exit; teardown took 0.133 s and 0.013 s,
respectively. Original environments and earlier failed evidence were preserved.

The installation receipt records `layout=shared-devtools`: the prepared Spack
libraries were reused. The complete build phase took 1076.13 s, including
checkout, remaining native dependencies, compilation and installation. This is
not a controlled speedup measurement.

The source-repair receipt verifies all three NCCL bootstrap sites and exact
reviewed before/after source hashes. Rebuilt `lib64/libcp2k.so.2026.2` has SHA-256
`4651f72675b3e8421f197a072851b3bec4a2d225634d443d8f1817a1ff193df6`, different from
the earlier clean build. Feature flags match that build, including CUDA,
cuSOLVERMp/NCCL, ELPA, COSMA and SPLA GEMM offloading.

## Evidence and scope

[summary.json](summary.json) contains the request, scheduler accounting,
preservation and lifecycle records, repair receipt and numerical comparisons.
[raw.tar.gz](raw.tar.gz) contains the summary and 116 individually compressed raw
records. [archive.json](archive.json) pins the archive and collector hashes;
every raw and compressed hash was verified after download. [collect.py](collect.py)
is the exact independent collector, which also reparsed the pinned scientific
outputs and verified the earlier three-partition archive.

The four-partition [combined result](../accepted.json) uses the earlier recipe
on DSPRHBM, 16V100 and 8V100V0, and the repaired recipe on 4V100. It does not
claim that the new source repair was rebuilt or tested on all four partitions.
These results qualify copied dependency SIFs plus sealed application overlays
for the tested single-node cases. Standalone CP2K SIF publication, physical
installation, daily provider activation, multi-node qualification and
performance qualification are separate.
