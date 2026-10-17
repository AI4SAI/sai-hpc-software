# CP2K 2026.2 static HDF5 linkage

The corrected TBLITE diagnostic `1833642` compiled all CP2K sources,
including `tblite_interface.F`, but failed while linking the executables.
`libcp2k.so.2026.2` retained undefined HDF5 Fortran C stub symbols such as
`h5tis_variable_str_c` and `h5gmove_c`.

The recipe installs HDF5 1.14.6 with MPI and Fortran enabled and shared
libraries disabled. CMake's FindHDF5 defaults to shared config targets; when
those are absent, its wrapper/manual fallback lists `hdf5_fortran` and
`hdf5` without `hdf5_f90cstub`. Selecting `HDF5_USE_STATIC_LIBRARIES=ON`
uses HDF5's native static target dependencies instead.

Diagnostic `1833954` proved that static discovery succeeds, then exposed a
second defect: HDF5's installed config references `ZLIB::ZLIB` without
finding its external provider. The recipe now uses the project-specific
`CMAKE_PROJECT_cp2k_INCLUDE` hook to run `find_package(ZLIB 1.3 REQUIRED)`.
`ZLIB_ROOT=/usr` matches the actual Spack external `zlib@1.3` provider.
The hook is included in the frozen controller contract and recipe hash.
Neither HDF5 functionality nor CP2K source is changed.

[Failed diagnostics](failed-diagnostics.json) retain accounting, original
requests, log/input checksums, error excerpts and unchanged image hashes.

Diagnostic `1833977` on 4V100 passed configure/generate and the CP2K CMake
feature contract. Its link command now includes
`libhdf5_fortran.a`, `libhdf5_f90cstub.a` and `libhdf5.a`. It reuses the prior
CP2K build tree; imported-target definitions cause CMake to recompile affected
objects. All output is under the project home. The old application image and
COSMA diagnostic are mounted read-only. Full compile, version, ldd and feature
flag results are pending; this diagnostic is not a formal SIF delivery.

Three regression tests use the actual production configuration and hook with
small static libraries to exercise FindHDF5 and a real shared-library/executable
link. They verify the complete dependency chain and reject missing static
selection, missing ZLIB target and a missing Fortran-to-stub edge. C symbols
model the interfaces; the fixture does not establish Fortran ABI or scientific
HDF5 IO correctness.

The packaging review also found setgid directory modes (`02755`) in the
actual Spack dependency store. `copytree` preserves them, but native manifests
reject mode bits outside `0777`. Export normalization now clears setuid/setgid
on the staged copy with `chmod ... a-s`; the original dependency store remains
unchanged. A real filesystem regression verifies rejection before normalization,
successful manifest write/read afterward, and retention of the original mode.
[Read-only image evidence](payload-mode-evidence.json).
