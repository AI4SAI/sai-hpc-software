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
COSMA diagnostic are mounted read-only. Full compile, version, ldd and all required feature flags passed. Job `1833977`
completed `0:0` in 15:51 on `4v100n04`, with the old image hash unchanged.
The executable reports CP2K 2026.2, source `67b5da876d`, GCC 14.3.0, CUDA,
cuSOLVERMp/NCCL, SpLA GEMM offload, TBLITE and HDF5.
[Complete diagnostic proof](complete-build-proof.json). This is not a formal SIF delivery.

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

Commit `9f17f715263016c4e063580b4e97c1ea2f6df2c9` is synchronized to GitHub
and SAI's home development checkout. [CI 39053853904](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39053853904)
ran 729 tests: 727 passed, two optional skips. Local focused tests passed;
the broad local suite was interrupted/timed out on slow repository-mounted
fixture IO and is not claimed as a completed pass. [Validation](validation.json).

A ten-second SSH/cgroup sample during diagnostic compilation measured
7.13 logical CPUs out of eight allocated (89.18%), with no cgroup CPU quota
or throttling. This is a stage sample, not whole-build or application runtime
performance. [Accounting evidence](cpu-sample.json).

A second sample from formal dependency job `1834214`, while compiling Libint,
used 7.20 of eight allocated logical CPUs (89.96%) with no quota or throttling.
The sample reads the job cgroup, so compiler processes that exit during the
interval are included. [Formal build sample](formal-cpu-sample.json).

The superseded workflow `39047391019` completed all four dependency jobs.
Its four old application jobs were cancelled only after checking their exact
Slurm handles and incompatible frozen recipes; sealed dependencies remain
preserved. [Final accounting and seals](superseded-workflow.json).

Fresh production workflow [39054631316](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39054631316)
was dispatched after the complete diagnostic passed. It selects release
v2026.2 on 16V100, 4V100, 8V100V0 and DSPRHBM, controller `9f17f71`, recipe
`e139a320651ce69c11cdbd1763ae875f98992de261a465e9e19a24849086bf40`.
Its final SIF checks and science/performance acceptance are still pending.

The formal release dependency jobs started on all four native partitions:
16V100 `1834216`, 4V100 `1834214`, 8V100V0 `1834213` and DSPRHBM `1834205`.
Their frozen controller/source/recipe identities and job-script hashes were
checked against the requested production identity. [Submission evidence](formal-start.json).

## Actual PBE runtime and CUDA compatibility

The first PBE smoke job `1834231` failed in both OT and STANDARD diagonalization
before SCF. DBCSR's CUDA 12.9 NVRTC generated PTX rejected by the injected host
`libcuda` (`CUDA_ERROR_UNSUPPORTED_PTX_VERSION`). The actual node kernel driver
was 550.163.01, reporting CUDA 12.4 capability; the kernel architecture was
correctly `compute_70` for V100. Version/ldd success did not prove JIT execution.

Job `1834460` used the same read-only binary and inputs with the existing NVIDIA
SDK CUDA 12.9 compatibility directory first in the loader path. It completed
`0:0` in 31 seconds on the same node. Both PBE calculations ended normally with
SCF convergence; OT/diagonalization differed by `5.684e-14 Ha` in energy and
`2.710e-10 Ha/Bohr` in forces. Actual process maps proved loading of
`compat/libcuda.so.575.57.08`, the matching PTX JIT compiler and toolkit
`libnvrtc.so.12.9.86`. Full before/after image and executable hashes matched.
[Both attempts and raw evidence](pbe-smoke.json). This small diagnostic is not
a final SIF, multi-node or performance acceptance.

The next production recipe places the exact compatibility directory in GPU
build/runtime environments and preserves the saved library order in the SIF
launcher, with Apptainer driver libraries as a fallback. Its final GPU linkage
contract rejects the old host `libcuda` path. CPU deliveries do not require
compat. Three actual shared-library loader tests exercise the launcher order,
fallback and empty initial path. The existing frozen workflow `39054631316`
continues toward a SIF but does not include this runtime correction. No shared
module, library or kernel driver was changed.

The pinned CP2K 2026.1 native baseline passed the same small PBE OT input in
its default module environment (`1834594`, `COMPLETED 0:0`, 16 seconds).
Its actual process loaded host driver 550.163.01; sampled CP2K maps contained
no dynamic NVRTC. The conditional compatibility retry was therefore unnecessary.
The unchanged baseline executable and candidate diagnostic differ by
`8.53e-13 Ha` in energy and `1.00e-12 Ha/Bohr` in maximum force. The three
preceding harness attempts failed before PBE because of omitted login
environment variables; their logs and final accounting remain retained.
[Baseline evidence](baseline-pbe-smoke.json). This comparison uses separate
single-node diagnostic jobs and makes no performance claim.

Production commit `9352d6dabca0a28e7ea81b2acec8977e7ca9ec06` passed
[CI 39056288064](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39056288064):
732 tests, 730 passed and two optional skips. Fresh release workflow
[39056762371](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39056762371)
selects all four targets using recipe
`4b6cb146fa983f69d82e5f7a990eba00b879a1c1e7464e317cc4740dbb41793b`.
Final artifact and scientific/performance outcomes are pending.
At 02:12 Asia/Shanghai, native dependency jobs were `1834668` (16V100),
`1834662` (4V100), `1834667` (8V100V0) and `1834666` (DSPRHBM).
[Exact requests, scripts and accounting](compat-formal-start.json).
