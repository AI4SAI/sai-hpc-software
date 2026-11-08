# LAMMPS Make versus Ninja experiment

This is an opt-in measurement, not a daily-workflow generator change. Both
variants use the normal standalone LAMMPS build/export/verification recipe,
including all site packages, the GPU and Kokkos CUDA backends, GCC 14.3.0,
CUDA 12.9.1, CMake 3.31.6, and the installed DeePMD/PLUMED dependencies.

`submit.py` submits a held job, records its ID, then releases it. The job runs
Ninja and Unix Makefiles sequentially on the same 16V100 node with 24 workers.
Each variant has an initially empty overlay and no compiler cache. Source is
frozen at `d291d572cf3c73a8a720774b78482a7c931304ca`. The canonical installation
prefix inside the two independent overlays is identical. All host writes go
under `/org/org-open-sai/proj-sai-hpc-software`.

The frozen controller snapshot must include these scripts under `benchmark/`,
a verified executable `benchmark/ninja`, and the normal LAMMPS runtime plan.
Ninja 1.13.2 is the official Linux release asset; its archive SHA256 is
`5749cbc4e668273514150a80e387a957f933c6ed3f5f11e03fb30955e2bbead6` and executable
SHA256 is `607e668f90dd6cd82e1a42ae572647ad1b1fd43063964295b9547836d8c15d99`.

`entry.sh` wraps only the experimental CMake calls to select the generator and
collect phase measurements. The daily recipe keeps its existing default.
`measure.py` records configure/build/install wall time, complete child-process
CPU usage, CPU affinity, and two-second samples of the descendant process tree.
Sampled aggregate PSS apportions shared pages; aggregate RSS double-counts
them. These are process-memory measures, not total cgroup memory or page cache,
and can miss short peaks. The largest individual child RSS is also reported
separately; it must not be called aggregate build memory.

Interpretation limits:

- This initial pair runs Ninja first and Make second. OS page caches are not
  flushed. A small difference needs a repeat in reversed order before claiming
  a stable benefit.
- `-march=native` is held constant by using the same node, but this is still a
  shared cluster. Allocation and topology records accompany the measurements.
- Build/package verification alone is not scientific or performance acceptance
  of the resulting LAMMPS executable. Runtime validation is recorded separately.
- No `/opt` installation, public module, or accepted SIF is changed or published.
