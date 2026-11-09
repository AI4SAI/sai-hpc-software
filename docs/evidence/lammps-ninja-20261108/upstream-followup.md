# Upstream compilation-time discussions — 2026-11-08

The upstream search changes the recommended next experiment: test LAMMPS
maintainer Axel Kohlmeyer's [PR #5308](https://github.com/lammps/lammps/pull/5308)
before developing a separate wrapper or template patch. No upstream patch was
applied and no new cluster compilation was submitted during this review.

## Directly applicable pending change: PR #5308

“Use only the host compiler for sources that do not need Kokkos with KOKKOS for
CUDA” was opened on 2026-11-07 by `akohlmey`. At the time checked it was open,
not a draft, and unmerged. Its head was
`f1a6e5cf5167deeb1f70bc8bd38ff8c2e1a853a7`; its base was exactly our measured
revision, `d291d572cf3c73a8a720774b78482a7c931304ca`.

The patch adds 19 lines to `cmake/CMakeLists.txt`. It marks eligible source
files with `nvcc_wrapper --host-only`, excluding KOKKOS sources and sources
including known headers that bring in Kokkos. It handles both the explicit
wrapper compiler and Kokkos's compiler-launcher setup. The intended change is
the compiler route, with no feature reduction or changed scientific equations.

The author's reported evidence, not independently reproduced by us:

- With the KOKKOS presets and `kokkos-packages.cmake`, 858 of 1,315 sources
  qualify for host-only compilation.
- Five directly compared source files required about five additional CPU
  seconds each with NVCC. The author estimates about 19 minutes saved on a
  four-core full build. This is not a published matched total timing for our
  71-package, 16-physical-core build.
- “Up to 25x” explicitly combines the change with `ccache`. It does not predict
  a 25x improvement for our clean builds without object/compiler caching.
- On V100 with CUDA 12.9 and GCC 14, the `kokkos-cuda-nowrapper.cmake` setup
  completed double- and mixed-precision builds, with all 1,150 unit tests passing
  in each configuration.
- For the explicit `kokkos-cuda.cmake` wrapper setup, all 858 eligible objects
  and two retained NVCC objects were compiled; that setup was not linked or run.

All 11 check runs returned by the GitHub API for that head passed. They cover
general Linux/macOS/Windows builds and tests, style checks and C++23 checks;
they do not independently establish acceptance of our CUDA/DeePMD/PLUMED
configuration. There were no submitted reviews or issue comments at the time
checked.

## Existing maintainer advice: issue #4853

In [this comment](https://github.com/lammps/lammps/issues/4853#issuecomment-3796898898),
Axel explains that selecting `nvcc_wrapper` globally processes host sources
through the CPU and CUDA compilers, even where the CUDA compiler is unnecessary,
and significantly slows compilation. He recommends the
`kokkos-cuda-nowrapper.cmake` preset.

The presets at our pinned revision confirm that the latter avoids forcing
`CMAKE_CXX_COMPILER=nvcc_wrapper`. This is a useful existing configuration
mechanism, but it does not make the new PR redundant: PR #5308's commit message
explains that the entire `lammps` target receives the Kokkos dependency marker,
so its launcher still redirects all of those sources to NVCC. The PR handles
individual sources within that target. Any recipe experiment should preserve
our current OpenMP, CUDA architecture and package selections instead of
blindly adopting the preset's other defaults.

## An improvement already included: PR #5198

[PR #5198](https://github.com/lammps/lammps/pull/5198) migrated the GPU package to
native CMake CUDA/HIP compilation. The author reports about 2.5x faster GPU
kernel rebuilds after header changes by removing FindCUDA's separate NVCC
dependency scan, and byte-identical generated CUDA kernel headers.

It merged on 2026-10-15 at
`b43f83afa6dc1e6e8ec72227d18f83f1b08d071f`. GitHub's compare API confirms that
our measured `d291d572` is 490 commits ahead of that merge, zero behind, with
the merge itself as the common ancestor. Our measured build already uses its
native `gpu_fatbin` path and SM70-only settings. Its reported 2.5x kernel-rebuild
gain is neither an additional available gain nor a whole-LAMMPS speedup.

Older [issue #148](https://github.com/lammps/lammps/issues/148) and merged
[PR #741](https://github.com/lammps/lammps/pull/741) also addressed excessive
Kokkos header dependencies and recompilation when installing/uninstalling the
package. They document a longstanding concern, rather than another pending
optimization for our current source.

## What this says about our hour-long build

The existing measurement includes 2,651 compiler commands, 71 packages and
407 LAMMPS Kokkos source files. Make's compilation/linking took 54m34s and
the full build/export/check flow took 60m06s. The build process tree averaged
10.49 logical CPUs of CPU time; 24 occupied task slots do not imply equivalent
CPU utilization. These measurements explain the scale of the workload and
leave efficiency questions open. They do not establish one hour as a normal
or unavoidable lower bound.

The next useful comparison is the pinned upstream #5308 change against the
existing source, keeping every package, optimization setting, dependency and
no-cache full-rebuild policy constant. It still requires a complete local
build, feature inventory, host-MPI GPU numerical checks, wall time and peak
memory measurements. The separate dependency-generation and template ideas
in the source audit remain secondary, unmeasured candidates.
