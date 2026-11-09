# Strict ccache cold/warm comparison

Build LAMMPS `f1a6e5cf5167deeb1f70bc8bd38ff8c2e1a853a7` twice on one 16V100
allocation, with identical options and 24 workers. Each variant has a fresh
overlay, checkout and build tree. Only the compiler cache is shared. The first
variant requires an empty cache; existing caches are never deleted by this
experiment. OS caches are not flushed.

Freeze the full controller and the accepted `runtime-plan.json` in a new
project-backed snapshot. In its `controller/benchmark` directory put this
directory's scripts, `experiments/lammps-host-compiler/provenance.py`, and
`experiments/lammps-ninja/{measure,science}.py`. Provision the checksum-pinned
ccache with `md_ccache.py install-tool` before running `submit.py` with the
controller, fresh experiment directory and self-contained source repository.

Before configuration, `probe.py` checks real GCC, host-only `nvcc_wrapper`, CUDA
`nvcc_wrapper` and NVCC invocations. It requires cold misses, warm hits, misses
after same-size/same-mtime header changes, and misses after comment-only source
changes. Linked executables must return the changed expected value. Probe
caches are private to each overlay and do not warm the measured LAMMPS cache.

`entry.sh` measures configure, compile/link and install separately. Raw ccache
statistics and toolchain contexts remain in each run's `runtime` and `results`.
`science.py prepare` runs the existing one/two-rank GPU, Kokkos, PPPM and
DeePMD TF/PT/JAX plus PLUMED cases against both resulting SIFs. This is a
one-node regression check, not a runtime performance benchmark or production
two-node publication acceptance.
