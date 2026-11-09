# Physical QE HDF5 1.14.6 installation acceptance, 2026-11-08

The installed HDF5 tree passes complete inventory verification and a native,
two-rank MPI/Fortran consumer built through its installed CMake config. This
qualifies the physical dependency installation; QE application compilation,
scientific acceptance, and daily-workflow provider activation remain separate.
No installed library, modulefile, workflow, or frozen controller was modified.

- Physical prefix:
  `/opt/apps/qe/dependencies/hdf5/1.14.6-nvhpc26.3-ompi5.0.10-f90/DSPRHBM`
- Inventory: 170 entries, 33,986,628 regular-file bytes, matching the accepted
  administrator handoff exactly (file contents, modes, types, and links).
- Inventory SHA256:
  `d64e243bd677023d476c906a33c9869ade4dcd705077f33cf992d39e9121e698`.
- Accepted job: `2301062`, DSPRHBM, `d9470n02`, `COMPLETED 0:0`, elapsed 6 s,
  TotalCPU 5.163 s, batch MaxRSS 495,640 KiB. Start: 2026-11-08 16:04:21 CST;
  refer to `r3/results/sacct.txt` for authoritative timestamps.
- Native host execution; no SIF or Apptainer launch. `findmnt` identifies the
  shared physical `/opt/apps` NFS mount. All sources, builds, runtime temporary
  files and configuration/cache directories were in project storage.
- NVHPC 26.3.0 and OpenMPI 5.0.10, selected by the same frozen
  `qe_environment.sh dsprhbm` used by the QE build. Compiler/wrapper real paths
  and SHA256 values are recorded in `r3/results/tool-identity.json`.
- `find_package(HDF5 1.14.6 EXACT CONFIG ... NO_DEFAULT_PATH)` resolves the
  physical installation. Imported C/Fortran static target locations are asserted
  to stay below the intended prefix. ZLIB and SZIP are found explicitly first,
  matching the QE integration requirements.
- Build/link logs show the physical `libhdf5_fortran.a`, `libhdf5_f90cstub.a`,
  and `libhdf5.a`. `ldd` has no missing libraries and resolves MPI/NVHPC to the
  intended site stack. Static HDF5 correctly does not appear in `ldd`.
- Two MPI ranks create/write/flush/read a parallel HDF5 file. Actual linked
  library version is 1.14.6; ranks 0/1 read back 17/18 respectively and emit
  `SAI_HDF5_MPI_ROUNDTRIP_PASS`.
- Runtime emitted site-stack advisory messages about shared-filesystem scratch,
  hwloc PCI domains, and ignored CUDA components on the CPU node. They did not
  prevent initialization or successful numerical checks. This is a functional
  library-consumer check, not a performance benchmark.

The first two attempts remain intact:

1. `2300961`, r1, FAILED before consumer compilation: the container-oriented
   `--export=NIL` removed the real user environment; MPI wrapper initialization
   reported that it could not obtain the user home directory.
2. `2300975`, r2, CANCELLED by root after 2 s before any payload log. It used
   partial variable export (`HOME,USER,LOGNAME`), which may request Slurm's
   user-environment retrieval. Scheduler data reports no detailed cancellation
   reason, so that causal explanation is unconfirmed.
3. r3 used the normal host `--export=ALL`, then the existing QE environment
   script reset compiler/library/module variables. The real HOME was preserved;
   TMPDIR and XDG cache/config paths remained under the project run directory.

Remote run root:
`/org/org-open-sai/proj-sai-hpc-software/runs/qe-hdf5-installed-check-20261108-r3`.
This directory retains the binary and HDF5 output as well as raw logs. The local
r1/r2/r3 directories hold the small requests, scripts, source, inventories,
CMake/link/runtime logs, selected effective environment, and scheduler evidence.
Full inherited environment is deliberately not copied into this dossier.

Successful receipt: `r3/results/acceptance.json`.
