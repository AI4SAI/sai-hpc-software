# Pinned upstream LAMMPS registry fixture

`StyleHeaderUtils.cmake` is unmodified upstream code from LAMMPS commit
`c8bd2ae5927ee236a8892dbd51c18a92cc9c33cf`, path
`cmake/Modules/StyleHeaderUtils.cmake`, under LAMMPS's GPL-2.0 licensing.
Upstream: https://github.com/lammps/lammps

SHA256: `18164522c46a9010a6db237d5c64cb5d18860382f222810be7434bcdbeef81bb`.
It was extracted read-only from the terminal `md-20260920-layout-recovery-01`
overlay and retained in the September 20 diagnostic fixtures.

Tests use minimal DeepMD declaration headers to exercise the real CMake registry
generator. This checks registration only, not compilation, GPU functionality,
scientific correctness or performance.
