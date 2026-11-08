# QE HDF5 and ABACUS LibTorch

This handoff installs two application-private dependency releases:

```text
/opt/apps/qe/dependencies/hdf5/1.14.6-nvhpc26.3-ompi5.0.10-f90/DSPRHBM/
  bin/  include/  mod/  lib/  cmake/  share/
/opt/apps/abacus/dependencies/libtorch/2.1.2-cpu-cxx11abi1/
  bin/  include/  lib/  share/  build-version  build-hash
```

HDF5 is built inside a new project-backed container at its final prefix, using
NVHPC 26.3, OpenMPI 5.0.10, native DSPRHBM optimization, static C/Fortran/high-level
libraries, parallel I/O, zlib and SZIP. The optional mpi_f08 overloads stay off,
matching QE's accepted F90 interface. GPU QE already uses its site HDF5 module.

LibTorch is the exact SHA-256-pinned 2.1.2 CPU C++11-ABI binary distribution used
by the accepted ABACUS builds. It is unpacked inside the container at one shared
prefix. It is not rebuilt or changed to a CUDA distribution. Headers, libraries,
CMake metadata, standard tools and licenses are retained. Build trees, our tests,
logs and provenance records are kept outside the installed prefixes.

## Prepare as stardust

Freeze the controller in the project, then run:

```bash
python3 "$control/app_dependency_stage.py" submit "$project" "$control" \
  app-dependencies-<identity> --hdf5-input <existing-pinned-QE-input-directory>
python3 "$control/app_dependency_stage.py" check "$project" app-dependencies-<identity>
python3 deployment/qe-hdf5-abacus-libtorch/prepare.py \
  --project "$project" --run app-dependencies-<identity> \
  --output /home/stardust/sai-hpc-software/deployment/qe-hdf5-abacus-libtorch
```

If construction/export succeeded but a verification job failed, preserve that
run and verify an identical copy with a corrected, frozen controller:

```bash
python3 "$control/app_dependency_stage.py" reverify "$project" "$control" \
  app-dependencies-<original> app-dependencies-<verification>
```

This checks the original build/export receipts and records the source SIF and
evidence hashes. It does not rebuild or modify the original libraries.

The final SIF is reopened for complete inventory verification, a two-rank
MPI/Fortran HDF5 write/read test, and C++ LibTorch tensor multiplication,
TorchScript serialization/reload and loading ABACUS's existing DeePKS model.
The latter does not replace an ABACUS DeePKS calculation.

## Administrator installation

In the generated handoff directory, run:

```bash
bash install.sh --check
bash install.sh --install
bash install.sh --verify
```

The repository template alone is not an installable handoff. The prepared copy
contains the accepted SIF and checksummed inventories, readable by saiuser01.
The installer reads the SIF without executing it, extracts only the two listed
prefixes, verifies every file, and renames complete staging trees into place.
An identical installation is reusable; a different existing tree is refused.

The administrator does not compile anything. Application workflow activation
follows physical verification and consumer checks; this package alone does not
change existing QE/ABACUS modules or daily build dependency selection.
