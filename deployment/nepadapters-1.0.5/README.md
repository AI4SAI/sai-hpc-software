# NEPAdapters 1.0.5 for LAMMPS 30Sep2026

This optional module supplies `nep/cpu` and CUDA Kokkos `nep/gpu/kk` for the
installed `lammps/30Sep2026-deepmd320-auto`. It uses that LAMMPS executable and
MPI environment, with one identical plugin payload across 16V100, 4V100 and
8V100V0. It is not compatible with arbitrary LAMMPS releases or daily betas.

Upstream release v1.0.5 is pinned to
`3ceba8f9b69765ac29d9f2f4f1c4f76cee8ecf37`. It adds mixed/nonperiodic LAMMPS
boundaries and fixes the CPU Spin1 per-atom virial allocation. The container
build retains GCC 14.3, CUDA 12.9.1, Kokkos 5.2.1, C++20, double precision and
SM70. CPU native architecture specialization is disabled and the plugins have
no MPI RPATH/RUNPATH. The existing frontend-only 2026 Kokkos compatibility
patch remains necessary. LAMMPS and its dependencies are read-only inputs.

## Administrator installation

A complete accepted package is prepared at:

```sh
cd /home/stardust/sai-hpc-software/deployment/nepadapters-1.0.5
bash install.sh --check
bash install.sh --install
bash install.sh --verify
```

Do this only after `release.json` has been populated from all three successful
partition handoffs. A checkout without accepted artifacts is not installable.
The installer verifies plugin hashes and the installed LAMMPS ABI pins before
publishing the new versioned module. It preserves LAMMPS and NEPAdapters 1.0.4.
Repeated installation is supported; different destination contents stop it.

The entire physical runtime is:

```text
/opt/apps/nepadapters/1.0.5-lammps30Sep2026/
  lib/nepadapterscpuplugin.so
  lib/nepadaptersgpuplugin.so
  share/licenses/nepadapters/LICENSE.NEP_CPU
/opt/modules/modulefiles/apps/nepadapters/1.0.5-lammps30Sep2026
```

Build sources, tests, metadata and SIFs stay outside `/opt`. The public handoff
is readable in stardust's home because the administrator account cannot traverse
the project ACLs; compilation and compute scratch use funded project storage.
The SIF is an addon using the installed LAMMPS/toolchain through read-only binds,
not another LAMMPS installation.

Inside a Slurm GPU allocation:

```sh
module load nepadapters/1.0.5-lammps30Sep2026
```

The module loads the accepted LAMMPS module and adds the shared plugin directory.
Use host MPI and the normal site binding convention. For GPU NEP use LAMMPS
`-k on g N -sf kk -pk kokkos neigh full comm device gpu/aware on`, where `N`
is the GPUs visible per node, with `atom_style atomic/kk`, `newton off`,
`pair_style nep/gpu/kk` and the appropriate `pair_coeff` model/elements.
The module does not modify input files or implicitly activate Kokkos.

## Acceptance and rebuilding

The build runs upstream Spin1 per-atom virial tests with one and four OpenMP
threads. The SIF and native export are tested on all three partitions using
pinned Pb/Te NEP4 energy, forces and virial plus 100 GPU MD steps. Every
partition checks single GPU, two GPUs on one node, and two nodes with host MPI.
Tests cover `p p p`, `p p f` (including periodic x-face ghosts) and `f f f`.
GPU banners, actual rank/device identities and matching MPI library resolution
are required. Numerical tolerances are 1e-6 eV energy, 1e-6 eV/angstrom force
components, and 5e-6 eV virial components. This is correctness acceptance, not
a performance benchmark or validation of every supported model. NEP3 and the
LAMMPS CUDA qNEP path remain unsupported upstream.

Dispatch **Build NEPAdapters release addon** (`nepadapters.yml`). Build occurs
once in a new project-backed overlay; all targets test the identical SIF.
The Actions artifact contains `accepted-handoffs.json`. Prepare the package
from the matching repository revision on SAI:

```sh
python3 -B deployment/nepadapters-1.0.5/prepare-release.py \
  --handoffs accepted-handoffs.json \
  --destination /home/stardust/sai-hpc-software/deployment/nepadapters-1.0.5
```

After the administrator installs it, stardust can run
`bash submit-native-checks.sh` from that package to recheck the actual public
module with the project account. Raw failures and successful receipts remain
in separate immutable run directories.
