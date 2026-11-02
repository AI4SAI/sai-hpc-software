# NEPAdapters 1.0.4 for the installed LAMMPS release

This optional module adds `nep/cpu` and CUDA Kokkos `nep/gpu/kk` to
`lammps/30Sep2026-deepmd320-auto`. It is tied to that accepted LAMMPS build
and is not a binary plugin for arbitrary LAMMPS releases or daily betas.

Compilation occurs once inside a project-backed Apptainer overlay on 16V100.
All three partitions use the same SIF and native export. The public runtime contains only two plugin
shared libraries and the license. Build sources, metadata, examples, and
validation outputs stay in the project workspace.

`release.json` lists the accepted partitions in this particular package. Other
partitions are unavailable through its module until their own acceptance passes.
The SIF contains the addon and a minimal root filesystem; it reuses the exact
installed LAMMPS and site toolchain. It is not a self-contained LAMMPS distribution.

The shared handoff covers 16V100, 8V100V0 and 4V100. The
administrator runs:

```sh
cd /home/stardust/sai-hpc-software/deployment/nepadapters-1.0.4-shared
bash install.sh
bash install.sh --install
```

The public handoff lives in stardust's home because `saiuser01` cannot traverse
the project ACLs. Compilation and compute scratch remain in the funded project.
After installation, stardust can run `bash submit-native-checks.sh` from the same
package using the project account to recheck the actual `/opt` module.

The default is a read-only preflight. Installation verifies the plugin bytes,
the installed LAMMPS libraries and the previous NEPAdapters files listed for
replacement. An incomplete release has no installable `release.json`.
The shared installation prefix is `/opt/apps/nepadapters/1.0.4-lammps30Sep2026`.
The installer publishes this common payload, replaces the existing module of
the same name, and removes the three superseded partition copies and their empty
recipe directories. This directly replaces the previous installation. Removal
is limited to the exact paths and hashes in `release.json`'s `replaces` list;
unexpected files or modified old libraries stop installation. A repeated run
can finish interrupted cleanup. The installer never changes LAMMPS.

The plugins have no embedded MPI library search path and install no MPI libraries.
They use the MPI environment provided by `lammps/30Sep2026-deepmd320-auto`.
The addon module uses one plugin directory and checks that the loaded LAMMPS
matches an accepted partition. Acceptance verifies identical plugin hashes on
all three partitions and matching MPI library resolution for LAMMPS and both
plugins, in addition to the SIF and numerical checks.

Inside a Slurm GPU allocation:

```sh
module load lammps/30Sep2026-deepmd320-auto
module load nepadapters/1.0.4-lammps30Sep2026
```

Ordinary GPU NEP input uses:

```lammps
units metal
atom_style atomic/kk
newton off
pair_style nep/gpu/kk
pair_coeff * * nep.txt Fe
```

Use the site's host MPI launcher and binding convention. Set LAMMPS
`-k on g N -sf kk -pk kokkos neigh full comm device gpu/aware on`, where
`N` is the number of GPUs visible per node. The addon module only loads the
plugin; it does not rewrite input files or enable Kokkos execution implicitly.
CUDA-aware MPI is required for the device reverse communication used by
multi-rank per-atom virial/stress calculations. The existing LAMMPS module loads
the corresponding site MPI. Setting `gpu/aware off` disables that path in LAMMPS.

Upstream supports ordinary NEP4/NEP5 and spin models. NEP3 and the LAMMPS
CUDA qNEP path are unsupported. Acceptance scope is recorded in `release.json`;
upstream capabilities beyond those checks are not additional site guarantees.

## Rebuilding and preparing a release

Dispatch **Build NEPAdapters release addon** (`nepadapters.yml`) for pinned
v1.0.4, commit `c934e71444cb4099d63dff98478f02c86373b061`. It builds one SIF on
16V100, then checks that exact SIF and native export on all three V100 partitions.
The accepted LAMMPS source/build overlay is a read-only build dependency;
compilation uses a new project-backed overlay and the checksum-pinned fuse2fs
driver. GCC 14.3, CUDA 12.9.1, Kokkos 5.2.1, C++20 and SM70 match the installed
LAMMPS release. CPU native-architecture specialization is disabled; CUDA
targets SM70. A frontend-only compatibility patch adapts the 2026 Kokkos
type/communication names and retains the upstream double-precision assertions.

The Actions artifact provides `accepted-handoffs.json`, with one common `build`
and a separate `native` directory per partition. Run from the repository on SAI:

```sh
python3 -B deployment/nepadapters-1.0.4/prepare-release.py \
  --handoffs accepted-handoffs.json \
  --destination /home/stardust/sai-hpc-software/deployment/nepadapters-1.0.4-shared \
  --previous /home/stardust/sai-hpc-software/deployment/nepadapters-1.0.4-all-v100
```

All three partitions are required by default. `--partitions 16V100,8V100V0`
explicitly prepares a release restricted to those accepted partitions. Use
`--previous` when replacing the known partitioned installation with the shared prefix,
or extending an existing shared release; otherwise omit it. Every handoff must
use the same SIF and plugin hashes. Preparation replays raw numerical/rank
evidence and checks both native Slurm
jobs before exposing a package. The administrator scripts read public package
files and installed LAMMPS; they do not traverse private build runtime directories.
