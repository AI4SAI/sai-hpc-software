# Installed LAMMPS update1 and NEPAdapters qualification

The administrator installed the three-partition update1 release on November 11.
`deployment/lammps-30Sep2026-update1/install.sh --verify` passed against the
actual `/opt` payloads, the published module, and installed DeePMD dependencies.
The complete native scientific matrix uses host Open MPI and physical binaries;
it does not run through Apptainer.

| Partition | Physical LAMMPS job | Result |
| --- | --- | --- |
| 4V100 | 2374773 | COMPLETED 0:0, 37 seconds, 14 cases |
| 16V100 | 2374774 | COMPLETED 0:0, 79 seconds, 14 cases |
| 8V100V0 | 2374775 | Pending resources; physical runtime qualification incomplete |

[4V100](native-4V100.json) and [16V100](native-16V100.json) verification reports
replay raw numerical and inventory checks and confirm completed accounting.
Raw frozen inputs and outputs are under:

```text
/org/org-open-sai/proj-sai-hpc-software/operations/lammps-update1-native-20261111-201352-476274
```

All three original SIFs already have accepted single/two-node science evidence.
The old pending 8V100V0 trial-extraction job 2372849 was cancelled after the
administrator installed; physical job 2374775 now provides the required check.

## Reusing the installed NEPAdapters 1.0.5 plugins

The new compatibility module uses the same two files already installed in
`/opt/apps/nepadapters/1.0.5-lammps30Sep2026/lib`. Neither LAMMPS nor the plugins
were rebuilt or modified. [The ABI review](abi-review.json) pins both upstream
source revisions and verifies the directly and transitively included headers,
Kokkos implementation, compiler settings and required dynamic symbols.

Of 34 LAMMPS header dependencies, 30 are byte-identical; the other changes are
comments, an anonymous enum with unchanged values, and the update version string.
Kokkos runtime code is unchanged. All three update1 libraries provide the CPU
plugin's 24 and GPU plugin's 29 required LAMMPS symbols. The KSpace enum change
does not enter the plugin's dependency closure. These checks support reuse of
the exact pinned binaries; they do not establish compatibility with other builds.

Each partition tests the candidate module, automatic LAMMPS dependency loading,
loader/MPI selection, module unloading, one GPU, two local GPUs and two nodes.
The scientific checks compare ordinary Pb/Te NEP4 energy, forces and virial with
pinned labels, run 100 steps of GPU dynamics, and cover periodic, mixed and free
boundaries. Tolerances remain 1e-6 eV energy, 1e-6 eV/angstrom force and 5e-6 eV
virial. This does not certify every NEP5/spin model or large-scale performance.

| Partition | Local job | Distributed job | State |
| --- | --- | --- | --- |
| 4V100 | 2374889 | 2374890 | Both COMPLETED 0:0; 14s / 9s |
| 16V100 | 2374893 | 2374894 | Both COMPLETED 0:0; 46s / 30s |
| 8V100V0 | 2374897 | 2374898 | Local completed in 1m48s; distributed pending |

Replayed complete reports are available for
[4V100](nepadapters-4V100.json) and [16V100](nepadapters-16V100.json).
Raw records and frozen candidate modulefiles are under:

```text
/org/org-open-sai/proj-sai-hpc-software/operations/nepadapters-update1-20261111-202034
```

The first attempts finished their numerical checks but failed in Lmod unloading
because shell `set -u` encountered an unset `LD_LIBRARY_PATH`. The test harness
now disables nounset for site Lmod teardown, then explicitly asserts the expected
remaining environment. Those failed first attempts are not acceptance receipts.

## Default version handoff

`module --ignore_cache show lammps` currently selects the original 30Sep2026
version. Update1 is the preferred next default after the complete installed
matrix passes. The [administrator package](../../../deployment/lammps-update1-default/README.md)
prepares standard `.modulerc.lua` defaults for LAMMPS and NEPAdapters and a new
NEPAdapters module selecting update1. Old explicit module names remain usable.

The installer refuses publication without complete three-partition physical and
NEP acceptance. The two 8V100V0 cross-node jobs are pending because only one node
has spare resources. No public default has been changed during preparation.
