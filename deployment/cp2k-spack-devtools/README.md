# Independent CP2K Spack dependencies

Build fixed library releases once per compatible native configuration, inside a
container at their final `/opt/devtools/<library>/<version>-gcc14.3.0-<cpu>-<dag>`
prefixes. The Libint prefix also identifies the existing source export repair,
which is not represented in Spack's DAG hash. No CP2K source SHA or build date is
part of these library prefixes.

The first set covers Libint 2.6.0, LibXSMM 2.0.0, LibVori 220621, HDF5 1.14.6,
PLUMED 2.10.1, COSMA 2.7.0 and SPLA 1.6.1, plus their complete link/run closure
(including COSTA, CUDA tiled-mm and GCC runtime links). Versions, compiler,
native architecture, MPI/BLAS/CUDA providers, variants and patches must match
before reuse. The site's older HDF5 1.14.6/OpenMPI 5.0.8 installation is not
automatically substituted for the required OpenMPI 5.0.10 configuration.

## Build and verify as stardust

Freeze the controller under the project storage, then submit each native target:

```bash
project=/org/org-open-sai/proj-sai-hpc-software
control=$project/controller/<reviewed-snapshot>
python3 "$control/cp2k_devtools_stage.py" submit "$project" "$control" \
  devtools-<identity>-16V100 16V100
```

Repeat for 4V100, 8V100V0 and DSPRHBM. These jobs use the project account and
project-backed ext3 overlays and preserve the existing managed fuse2fs teardown.
Only existing toolchain directories are bound read-only; a broad host
`/opt/devtools` mount would hide the new libraries and is deliberately omitted.
No compiler writes to the physical host `/opt`.

Each job creates `libraries.sif`, its checksum, the concrete Spack lock and
native/compiler provenance. The final SIF is independently reopened for full
file inventory checks, ELF linkage inspection and a two-rank MPI/Fortran HDF5
write/read test using installed headers, Fortran modules and static archives.
This is library consumer validation, not CP2K scientific acceptance.

```bash
python3 "$control/cp2k_devtools_stage.py" check "$project" devtools-<identity>-16V100
```

If compilation, clean overlay teardown and SIF export succeeded but the consumer
test command needs a fix, freeze the corrected controller and verify the same SIF
in a fresh allocation on the original node:

```bash
python3 "$control/cp2k_devtools_stage.py" reverify "$project" "$control" \
  devtools-<original>-16V100 devtools-<verification>-16V100
```

This copies the checksummed SIF and build evidence without recompiling libraries.
The original failed allocation and its logs remain unchanged. The new acceptance
records both allocations and rejects changed source images or build evidence.

Run `prepare.py --wait` from this repository with four `--run` arguments and a
fresh `--output` beneath `$project/deployment` to observe the existing jobs and
prepare the handoff automatically after all four pass. It
extracts only the inventoried library prefixes from the checksummed SIFs. The
generated handoff contains the administrator scripts below. The repository
directory alone is not an installable release.

Any failed allocation stops preparation and preserves its evidence. Observation
does not resubmit jobs, cancel allocations or install anything in physical `/opt`.

The administrator account `saiuser01` cannot traverse the project directories.
Add `--admin-output /home/stardust/sai-hpc-software/deployment/<new-release>` to
prepare an accessible handoff containing four compressed SIF files, small file
manifests and installer scripts. Build/cache/source data remain in the project;
no expanded library trees are copied into the home directory.

## Administrator installation

Run in the generated handoff directory:

```bash
bash install.sh --check
bash install.sh --install
bash install.sh --verify
```

`--check` is the default, read-only preflight. It checks the immutable SIF hashes
and file manifests (or the expanded project payload). Installation extracts only
the listed package prefixes directly into staging directories beside the final
`/opt` destinations, verifies every file, then renames each complete tree. It
refuses to replace an existing tree with different files. Repeating an identical
installation is safe. SIF headers and Squashfs files are read without executing
container code; the administrator does not compile anything.

Headers, Fortran modules, static and shared libraries, CMake/pkg-config metadata,
standard tools and package runtime data are retained. Spack build logs/database,
source/build trees, our custom tests and handoff evidence are not installed in
`/opt`. GCC-runtime links retain their external GCC 14.3.0 targets, whose contents
are also checksummed. Existing installations are not renamed or overwritten.

## Activation boundary

A failed read-only export can be recovered separately from a dependency
rebuild. `controller/cp2k_devtools_reexport.py submit` takes the project, a new
frozen controller, the original run, a fresh run ID, the completed overlay's
SHA-256 and a selected compute node. It accepts only the exact recorded export
teardown failure following a clean managed build. The new job copies and seals
the overlay, checks its hash, runs read-only fsck, verifies writes are rejected,
exports with managed driver teardown, and runs the complete SIF consumer checks.
The overlay hash is checked again after export. Original failures and controller
snapshots remain unchanged; partial output from the failed export is not reused.

Both writable and read-only image overlays use normal unmount followed by up to
120 seconds for the pinned fuse2fs driver to exit naturally. A forced stop,
missing receipt or incorrect read-only mode fails acceptance. Ordinary immutable
SIF execution is unchanged.

The generated `site-spack-candidate.json` records the full provider inventories,
native CPU flags and Libint repair. After physical installation, use it as
`controller/cp2k_site_spack.json` in an isolated CP2K validation snapshot. The
application verifies installed file hashes and native CPU flags before linking;
the CI controller skips the Spack allocation only for a configured, verified
provider. A configured but damaged or incompatible provider fails explicitly.
An empty registry retains the existing private dependency build path.

Physical file verification and CP2K compile/scientific acceptance must complete
before merging that provider registry into the daily workflow. An uninstalled library
SIF alone does not activate reuse. CP2K releases and development builds may share
the same provider when their exact dependency requirements match. DBCSR, TBLITE,
LibXC, LibXS and wignernj are outside this first Spack migration.
