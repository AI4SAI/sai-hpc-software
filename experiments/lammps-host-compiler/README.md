# LAMMPS PR 5308 clean-build experiment

Test upstream [PR 5308](https://github.com/lammps/lammps/pull/5308) head
`f1a6e5cf5167deeb1f70bc8bd38ff8c2e1a853a7` against its sole parent
`d291d572cf3c73a8a720774b78482a7c931304ca`. The upstream change adds
`--host-only` to eligible non-Kokkos LAMMPS C++ compilation units. Both builds
use the existing Unix Makefiles recipe, all site packages and GPU backends,
installed dependencies, and 24 workers. No objects or compiler cache are reused.

The PR runs first and the base second on one 16V100 allocation, in distinct
initially empty overlays. OS page caches are not flushed; a single pair cannot
establish repeatable cluster-wide timing. Canonical install prefixes retain
the real source SHA of each variant. All host writes use the project directory.

The frozen controller's `benchmark/` directory contains these scripts plus
`measure.py` and `science.py` from `experiments/lammps-ninja`, and that directory's
`compare.py` renamed `generator_compare.py`. It also needs the normal runtime
plan. The source repository must contain both commits with no Git alternates.

`submit.py CONTROL EXPERIMENT SOURCE_REPOSITORY` verifies the pinned upstream
diff and installed dependency, records a held job, then releases it. `entry.sh`
measures configure/build/install; `provenance.py` verifies that the only local
tracked-source adaptation is the existing DeePMD CMake registration. Raw
compiler commands, response files and descendant-process PSS samples are kept.
PSS excludes filesystem cache and external container daemons and can miss
short peaks; it is not total cgroup memory.

`compare.py EXPERIMENT` rejects any compiler-input change except eligible
`--host-only` additions and the canonical install prefix. After successful
builds, `science.py prepare EXPERIMENT FROZEN_REFERENCE OUTPUT` checks both SIFs
with the previously accepted one/two-host-MPI-rank GPU and DeePMD/PLUMED cases.
This regression check is separate from production two-node publication.

Daily defaults, accepted images, installed software, and public modules are
unchanged. Failed attempts and their receipts remain available for diagnosis.
