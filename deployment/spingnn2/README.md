# SpinGNN2 formal-release SIF and native delivery

The existing Daily relay resolves `Hyyu-Lab/SpinGNN` formal releases and builds
SpinGNN2 on DSPRHBM (CPU/OpenMP), 16V100, 4V100 and 8V100V0 (one V100/SM70).
Development and prerelease tracks do not build this software. CPU-MISC is
excluded; A100 remains disabled pending real runtime qualification. Other
software matrices and the existing 03:00 scheduler are unchanged.

Use the existing `daily.yml` workflow on the reviewed ref with
`software=spingnn2`, `tracks=release`, and the required target(s), or `all` for
the four qualified targets. Only a changed release tag/SHA/target starts a new
attempt; a failed attempt or changed recipe requires `retry_releases=true`.
Resuming reattaches to the frozen request and held Slurm handle, without
submitting a duplicate or silently changing its controller.

## Build and acceptance

`spingnn_ci.py` prepares pinned sources and an offline Python wheel archive.
`spingnn_release.py` retains claims and held submissions; `spingnn_sif.py` uses
the shared container policy, pinned managed overlay driver, `create_rootfs.sh`
and `export_native` protocol. The base SIF and SHA256 are fixed in site config.

Compilation and fixture export execute inside the writable overlay. The image
contains `/opt/apps/spingnn/v<release>/<PARTITION>` with corresponding sources,
the SAI SM70 patch, notices and a declarative export inventory. System OpenMPI,
GCC, CUDA and MKL remain external read-only dependencies; a broad host
`/opt/apps` bind cannot hide the application stored in the image.

The final read-only SIF executes a six-atom SOC fixture in two spin
configurations, with native FP64 inference and LAMMPS SPIN/KOKKOS. The native
exporter then extracts verified image contents into a new project directory.
That exported runtime executes the same science directly on the allocated
host, without a container wrapper. Owned ELF libraries use `$ORIGIN`; Python
commands locate their adjacent venv. Both routes record actual binary and
dependency hashes, namespaces, Slurm identity, resources and full results.
Every partition includes the relative link `bin/lmp -> lmp_spingnn2`, recorded
in the SIF/export inventory. Both scientific routes invoke `lmp` and verify
that it resolves to the same qualified binary; the original name is retained.

Native and oracle error bounds remain `<1e-10`; LAMMPS energy, force and spin
force bounds remain `<=1e-11`. Both configurations require nontrivial forces
and spin response. CUDA acceptance requires executed `sp2_` kernels, SM70 JIT
cubins and the LAMMPS GPU pair style. Scope is a deterministic untrained
fixture, one rank and one GPU; training, long trajectories, production
material accuracy, multi-GPU scaling and A100 are not qualified here.

Successful stage receipts pin the request and output hashes. Incomplete
unrecorded builds remain failed with their overlays and logs preserved and
require explicit retry. Completed stages/jobs can be reobserved without
recompilation. Modified receipts and incompatible frozen controllers fail
closed. Native first-install receipts remain historical evidence and cannot
satisfy SIF acceptance; native-only generations need a new SIF generation.

## Native installation

Images, receipts and exports stay under
`/org/org-open-sai/proj-sai-hpc-software/experimental/spingnn2-release/sif`.
Images use the existing identity-aware `containers/software` layout; every
attempt has a separate image and export even though runtime prefixes agree.

Each accepted run provides `install.sh` and `payload.tar.gz`. An administrator
runs `bash install.sh --check payload.tar.gz`, then
`bash install.sh --install payload.tar.gz`. The Bash checks the archive digest;
the packaged verifier checks its complete export inventory. Installation uses
the same versioned `/opt` path and a `spingnn/<release>` Lua module without
recompilation or runtime container wrappers. `DESTDIR` enables isolated
rehearsal. Existing partition prefixes and differing public modules are
rejected before writes; Daily never automatically replaces the installed
`/opt/apps/spingnn/v2.0.0` or its module.

Loading requires a positive canonical integer `SLURM_JOB_ID` and a supported
partition. Missing/unknown partitions, CPU-MISC and A100 fail before environment
changes. `help`, `whatis` and `show` work on login nodes without runtime loads.
Unload uses the current module's saved identity even if Slurm variables change
or disappear; no old-module migration fallback is included.

The PR branch is independently testable. Scheduled activation still requires
the normal review and main integration boundary.
