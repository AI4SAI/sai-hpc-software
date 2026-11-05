# Quantum ESPRESSO qualification — 2026-11-05

The initial QE 7.6 release is accepted on DSPRHBM, 16V100, 4V100 and
8V100V0. These checks exercise the final read-only SIF through host OpenMPI,
including one/two-rank silicon SCF and forces, Gamma phonons and the charge
density integral. GPU logs record CUDA kernels on both devices in the two-rank
case. This is representative scientific acceptance, not a benchmark or validation
of every installed program.

| Evidence | Scope |
| --- | --- |
| [Site audit](site-audit.json) | Existing QE 7.5 modules, compiler/MPI configuration and executable inventory |
| [GPU release](release-gpu.json) | Three accepted GPU images, identities, numerical results, native exports and scheduler accounting |
| [CPU release](release-cpu.json) | Accepted CPU image, HDF5 interface configuration, numerical results and native export |
| [Development](development.json) | Four accepted develop SIFs, both CUDA devices, numerical results and scheduler accounting |
| [Native release staging](native-release-stage.json) | Administrator-package trial installation and native link/SCF/PH/PP checks passed on all four partitions |
| [Native release installed](native-release-installed.json) | Administrator-installed physical /opt files, public-module hash, four native link/SCF/PH/PP checks and both GPU devices |
| [Current develop confirmation](current-develop-confirmation.json) | Live upstream HEAD, freshly hashed SIFs, twelve successful jobs and the home beta alias; unchanged source reused |
| [Installed-feature audit](feature-audit.md) | Old/new compile options, real D3/TS/MBD calculations and explicit legacy command/GUI limitations |
| [Beta publication](beta-publication.json) | First accepted public beta, all four runtime checks, real login help and public artifact preflight |
| [CPU HDF5 probes](cpu-hdf5-probes.json) | Read-only diagnostics that established the MPI Fortran interface conflict; these are not production acceptance |
| [Dependency cache](dependency-cache.json) | Identical archive bytes across four Actions runners and actual verified remote cache hits |
| [CI validation](ci-validation.json) | Full repository validation at the merged feature head |

Release source is `qe-7.6@9f93ddec427d2b9a45bb72d828c6d324f62fcabd`.
GPU release builds use recipe `02bdbc94280c6262f59ed786392836d68b66a7a98fe1715610bb11faedb6e7af`;
CPU uses `18629d40806e6bb222cd3324ae381ef367b1212bf2a5d72b2193d60514d06ff1`,
which incorporates the proven HDF5 F90 MPI interface selection. Exact controller,
image and input hashes remain attached to each accepted run. Later merges do
not change those identities or relabel existing artifacts.

All eight release SCF runs give `-15.84452726 Ry`. Density integrates to
`7.99999404914205` electrons on GPU and `7.9999948251504` on CPU. The independent
phonon reference and its `2 cm^-1` tolerance come from the pinned upstream test
suite; the reference itself was generated with QE 6.0 in 2017. The CPU runs
print NVFORTRAN IEEE status notices at STOP, while the checked outputs are finite
and within the specified tolerances.

Builds and acceptance evidence remain under
`/org/org-open-sai/proj-sai-hpc-software`. The administrator installed the release
into physical `/opt` on 2026-11-05. The installed 288 files and public module
passed hash verification; the subsequent native checks passed on all four
partitions, separately from the earlier staged installation. Failed build and
diagnostic runs are retained under their original identities.

| Partition | Physical-installation check | Elapsed | Result |
| --- | --- | --- | --- |
| 16V100 | 2212239 | 00:23 | COMPLETED 0:0 |
| 4V100 | 2212240 | 00:12 | COMPLETED 0:0 |
| 8V100V0 | 2212241 | 00:32 | COMPLETED 0:0 |
| DSPRHBM | 2212242 | 01:11 | COMPLETED 0:0 |

All four checks inspect the 67 executable dependencies and run one/two-rank SCF,
forces, Gamma phonons and density integration. All three GPU partitions record
CUDA kernels on devices 0 and 1. They use a byte-identical copy of the installed
public module with actual `/opt` prefixes; the evidence records both module and
installed-program identities.
