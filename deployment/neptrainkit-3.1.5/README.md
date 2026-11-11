# NepTrainKit 3.1.5 SIF qualification

`nepkit` is the upstream command for **NepTrainKit**, not a separate NEP engine.
Release [v3.1.5](https://github.com/aboys-cb/NepTrainKit/releases/tag/v3.1.5)
(October 15, 2026) is pinned to commit
`578159e74cc23ae682787593b6aec3997b590fca`.

## Preinstallation value

This is a useful **optional desktop application** for users who prepare and
audit NEP training data on the cluster. It generates candidate structures,
filters outliers, selects representative configurations, inspects training
errors and visualizes structures. Keeping it close to large training datasets
can avoid copying those datasets to a personal workstation.

It complements GPUMD's training/MD executable and GPUMDkit's command-line
preprocessing/postprocessing helpers. It does not replace either, and its
`nepkit` command starts a Qt GUI rather than an MPI program. A remote desktop or
working X11 display is needed for interactive use. Offscreen startup checks do
not establish that a particular remote-display connection works.

The Python `nep-adapters==1.0.5` dependency supplies CPU/CUDA inference. It is
installed privately in this application's environment and does not load or
modify the separate LAMMPS NEPAdapters plugin or any LAMMPS installation.
Explicit CUDA selection fails if CUDA is unavailable; upstream Auto mode can
explain a fallback to CPU. No MPI is needed for this application.

## Build and payload

The application is installed **inside Apptainer** at:

```text
/opt/apps/neptrainkit/3.1.5/
  bin/nepkit
  bin/NepTrainKit
  bin/python
  lib/python3.12/site-packages/NepTrainKit/
  lib/python3.12/site-packages/nep_adapters/
  ... Python, Qt, NumPy/SciPy/ASE and their standard runtime files
```

The build mounts a new private project directory at this final prefix. Host
`/opt` is read-only; no physical public installation occurs. A minimal root
filesystem plus the completed environment is packed into one SIF. There is no
writable overlay or overlay flush phase. Build inputs, caches, output and
temporary data all use `/org/org-open-sai/proj-sai-hpc-software`.

Python 3.12 comes from the previously cached, checksum-verified conda seed.
The five NepTrainKit native helpers compile from the pinned source with
GCC 14.3.0, OpenMP and portable `-march=x86-64` flags. Runtime dependencies are
frozen as wheel hashes in the job request. CUDA inference uses the upstream
NEPAdapters wheel, which includes CUDA runtime libraries and PTX for compatible
NVIDIA GPUs; it is not linked to a partition-specific MPI installation.

The NEPAdapters 1.0.5 upstream wheel recipe leaves
`NEP_ADAPTERS_CPU_ENABLE_NATIVE_ARCH=OFF`. Inspection of the actual CPU extension
found no YMM/ZMM registers or VEX/EVEX vector instructions; it does not require
AVX2 or AVX-512. NumPy/OpenBLAS use their normal runtime CPU dispatch. This is
one shared build, with no per-partition copies. The application helpers target
baseline x86-64; the complete Python/Qt environment is qualified on SAI's tested
hosts, not claimed tested on every historical x86 CPU.

The deployment is one optional application and one module, `neptrainkit/3.1.5`.
Application state, generated data
and upstream runtime-update sidecars belong to the user's configuration/work
directory, not the read-only SIF. The supplied build is a pinned environment;
user-installed runtime sidecars would be a distinct runtime configuration.

## Administrator installation

The accepted SIF and installer are readable from stardust's home. With the
administrator account, run:

```bash
bash /home/stardust/sai-hpc-software/deployment/neptrainkit-3.1.5/install.sh --check
bash /home/stardust/sai-hpc-software/deployment/neptrainkit-3.1.5/install.sh --install
bash /home/stardust/sai-hpc-software/deployment/neptrainkit-3.1.5/install.sh --verify
```

The installer extracts the accepted SIF into a sibling staging directory,
removes known test/bytecode cache trees, and verifies the complete standard
runtime tree (file hashes, modes and symlinks) before publishing. Application
data, licenses, native helpers and third-party libraries remain intact. Created
directories are explicitly 0755, regular files 0644/0755 and the module 0644,
including when the administrator uses umask 077. A different existing
installation or module is rejected. The native payload is approximately
1.126 GiB / 18,106 regular files. Self-written validation scripts and logs stay
outside `/opt`.

The module adds the application's `bin` to PATH. It does not add MPI, require a
CPU partition variant, or modify the shell's shared-library search path.

Like the installed phonopy, SSCHA and exa-amd environments, this module belongs
to the `python` family and selects Python through PATH without activating Conda.
It temporarily clears `PYTHONPATH`/`PYTHONHOME`, disables user-site packages and
bytecode writes, and restores the previous values on unload. Unload it before
switching to another Python environment. `SAI_PYTHON_ENV` identifies this prefix.

Commands:

```bash
module load neptrainkit/3.1.5
nepkit
```

One `module whatis` entry lists Features. Login-readable help points to:

```bash
cat /home/stardust/sai-hpc-software/deployment/neptrainkit-3.1.5/build-info.txt
```

The optional `run-sif.sh` invokes the compressed image without installing it,
binds `/home` and `/org` at their original paths, and adds NVIDIA support when
a GPU allocation exposes `CUDA_VISIBLE_DEVICES`. A working GUI display is still
required. This launcher is outside the native `/opt` payload.

## Qualification

The build verifies installed native-helper imports and runs the upstream
calculator and structure tests against the **installed wheel**. It explicitly
checks CPU prediction against the upstream energy/force/virial fixtures, then
compares CUDA energy, forces, virials and descriptors to CPU predictions.
It also creates and closes the real Qt main window using the offscreen plugin.
The scientific and GUI checks run again from the final immutable SIF.
CPU/reference absolute tolerances are 5e-4 for energy/virial and 5e-6 for force.
CUDA/CPU checks use the combined tolerance `atol=2e-6, rtol=1e-6` for energy,
force, virial and descriptors; this is not an absolute-only bound.

After an administrator installs, stardust can submit the same direct-host
native checks with:

```bash
python3 -B /home/stardust/sai-hpc-software/deployment/neptrainkit-3.1.5/native-checks.py
```

`native-checks.py <project-stage-root>` tests a trial installation instead;
such results are explicitly separate from physical `/opt` acceptance.

See [recorded status and evidence](../../docs/evidence/neptrainkit-3.1.5-20261111/README.md).
Preparation is separate from an administrator's public installation decision.

Upstream license: GPL-3.0-or-later; dependency licenses and notices remain in
their standard Python distribution metadata.
