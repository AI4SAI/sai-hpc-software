# LAMMPS PR 5308 clean rebuild on SAI

Status: paired build **2306184** is running. This is not a speedup or runtime
acceptance claim. It started on `16v100n32` at `2026-11-08T22:49:56+08:00`.

The experiment compares upstream [PR 5308](https://github.com/lammps/lammps/pull/5308)
head `f1a6e5cf5167deeb1f70bc8bd38ff8c2e1a853a7` with its parent
`d291d572cf3c73a8a720774b78482a7c931304ca`. Both use Unix Makefiles and 24
workers, complete independent rebuilds, the existing package/optimization
settings, and read-only installed dependencies. PR first, base second; OS
page caches are not flushed. Canonical prefixes retain the actual source SHA.

Frozen controller: `ef2bc61c542bc393b4e0ce20a6749de7ffff6998`. The source import
preserved and verified original Git object hashes; its isolated repository has
no alternates or dependency on network access from compute nodes.

Cluster evidence root:
`/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-lammps`.

- Request and monitor: `generator-benchmarks/pr5308-20261108-v1`.
- Immutable controller: `generator-benchmarks/pr5308-20261108-v1-snapshot/controller`.
- Fresh overlays/SIF outputs: `runs/pr5308-20261108-v1-pr5308` and
  `runs/pr5308-20261108-v1-base`.

The comparator permits only intended `--host-only` additions and the distinct
install prefix. Timings include configuration, compilation/linking, and
installation. Sampled descendant-process PSS excludes page cache and external
container daemons. Successful SIF construction is followed by separate GPU
numerical checks through host MPI using frozen accepted fixtures.

Preparation checks: 43 targeted unit tests ran (40 passed, 3 skipped), plus
compiler-comparison mutation checks, old/new scientific request compatibility,
production recipe hook checks, Python syntax, and shell syntax. No daily
workflow default, accepted image, physical `/opt` installation, or module is
changed by this experiment.
