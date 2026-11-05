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
`/org/org-open-sai/proj-sai-hpc-software`. Native exports are project artifacts;
no physical `/opt` installation was performed. Failed build and diagnostic runs
are retained under their original identities.
