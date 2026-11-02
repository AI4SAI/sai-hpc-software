# One shared NEPAdapters 1.0.4 plugin payload

NEPAdapters v1.0.4 (`c934e71444cb4099d63dff98478f02c86373b061`) was built once
for the installed LAMMPS stable_30Sep2026 release (`8de817dd79bf`) and accepted
on all three V100 partitions. GCC 14.3, CUDA 12.9.1, Kokkos 5.2.1, C++20,
double precision and SM70 are unchanged.

The [ELF comparison](elf-comparison.json) checks the installed original plugins
and the new shared export. All four copies have identical `.text` and `.rodata`
for each backend, and identical CUDA `.nv_fatbin` for the GPU backend. Native
CPU architecture specialization is disabled. The shared plugins have no
RPATH/RUNPATH: CMake's install setting alone retained the MPI wrapper's path,
so the build removes it with `chrpath -d` and rejects any remaining search path.

One SIF, SHA256
`88c0618173d18e3d3abfe03794d3dc8c13c93ee357724ef6a1484ca496c2ceb5`, is 19,464,192
bytes (18.56 MiB). The common CPU plugin SHA256 is
`1c0c9d84816ca945d6deb9f975b88ad3ccb056dabbcb3678a80dee9b72180c69`; the GPU
plugin SHA256 is `65eb2f9a2493787c2445e1a63993bbd1711541ee5ec9c25be04873c110904496`.
The SIF is an addon and reuses the installed LAMMPS/toolchain through read-only
binds. It does not contain another LAMMPS or MPI installation.

| Partition | Shared SIF build | SIF plus native single/two GPUs | Native two nodes | Site MPI from LAMMPS |
| --- | --- | --- | --- | --- |
| 16V100 | 2102398 | 2102562 | 2102563 | Open MPI 5.0.10, avx2 |
| 4V100 | same image | 2112122 | 2112123 | Open MPI 5.0.10, avx512 |
| 8V100V0 | same image | 2103050 | 2103051 | Open MPI 5.0.10, avx2 |

All jobs completed with exit `0:0`; see [accounting](accounting.txt). Each
partition verifies the same SIF and both native plugin hashes. `ldd` checks
confirm LAMMPS and both plugins resolve the same physical `libmpi.so.40` for
that partition. Native receipts record its path, checksum, launcher, numerical
results and per-rank LAMMPS/GPU identity:
[16V100](native-16V100.json), [4V100](native-4V100.json),
[8V100V0](native-8V100V0.json).

Acceptance uses the pinned eight-atom Pb/Te NEP4 fixture, translated so both
MPI domains own interacting atoms. CPU/GPU energies, forces and virial are
checked against upstream labels; every layout completes 100 GPU MD steps.
GPU total-energy error is 1.91e-7 eV, maximum force-component error 2.76e-7
eV/angstrom and maximum virial-component error 1.46e-6 eV, below the existing
1e-6/1e-6/5e-6 limits. GPU banners require CUDA Kokkos, double precision and
device neighbor lists. This does not establish performance or validate every
NEP model. Multi-rank runs retain `comm device gpu/aware on`.

The [handoffs](accepted-handoffs.json) identify the unchanged common build and
the accepted native runs. Release preparation replayed their raw numerical and
rank evidence before generating the [release manifest](../../../deployment/nepadapters-1.0.4/release.json).
Complete raw cases remain in those project directories. Earlier failures are
retained separately:

- Build 2101708 hung during Apptainer startup and was cancelled. Build 2102131
  compiled but the packaging guard rejected an embedded MPI RUNPATH. Neither
  is accepted. Build 2102398 completed in 4:35 with the corrected packaging.
- 8V100V0 jobs 2102570 and 2102981 hit an uncorrectable ECC error on
  `8v100v0n09`; see [GPU log](8v100v0-ecc-failure.log). The environment-only
  exclusion did not reach Slurm. The retry records `#SBATCH --exclude` in its
  frozen script and passed on `8v100v0n05` and `8v100v0n07`.
- 4V100 jobs 2102567 and 2102977 failed in MPI startup. Diagnostic job 2103074
  reproduced failure [without plugins](4v100-no-plugin-failure.log), and
  exposed [PRTE core binding outside the allocated CPU mask](4v100-core-binding-failure.log).
  Its shell status-printing command also failed, so it is diagnostic evidence
  only. The accepted retry uses the existing `lammps_acceptance.command()`
  approach for distributed checks: one rank per node, Slurm's CPU allocation,
  and `--bind-to none`. Local single/two-GPU tests keep core binding.

The administrator package is
`/home/stardust/sai-hpc-software/deployment/nepadapters-1.0.4-shared`.
[The shared installation check](shared-installation-state.json) confirms
`/opt/apps/nepadapters/1.0.4-lammps30Sep2026` is installed and contains only
`lib/nepadapterscpuplugin.so`, `lib/nepadaptersgpuplugin.so` and
`share/licenses/nepadapters/LICENSE.NEP_CPU` (30.84 MiB total).
Their hashes match the accepted export. The three old partition copies and
their empty recipe directories are gone; the installed LAMMPS hashes still match.

The updated package keeps the module name `nepadapters/1.0.4-lammps30Sep2026`
and removes its redundant node/partition checks. It depends on
`lammps/30Sep2026-deepmd320-auto` and sets the fixed shared plugin directory;
LAMMPS supplies its own executable and MPI selection. The plugins, acceptance
files and LAMMPS pins are unchanged. Tests, receipts, sources and manifests stay
outside `/opt`.

[Read-only preflight](simple-module-preflight.txt) passed as `saiuser01`, and
[help](simple-module-login-help.txt) passed with the login node's actual Lmod.
Seventeen unit tests pass, including module use without partition variables,
preservation of the host MPI environment, cleanup, recovery from interrupted
cleanup and preservation of unrelated files. The installed `/opt` module still
contains the earlier partition checks. The administrator updates it by rerunning:

```sh
cd /home/stardust/sai-hpc-software/deployment/nepadapters-1.0.4-shared
bash install.sh
bash install.sh --install
```

After installation, `bash submit-native-checks.sh` can submit checks against
the actual public module. The installer keeps the accepted shared binaries and
replaces only the known previous module. Historical observations are retained in
[the initial installation check](installation-state.json) and
[replacement preflight](replacement-preflight.txt); they describe the state before
the administrator installed the shared release. The original partitioned manifest
and module remain archived in [the earlier evidence directory](../nepadapters-20261101/README.md).
