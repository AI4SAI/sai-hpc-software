# Reviewed installed-module descriptions

These are candidate descriptions validated with Lmod; they are not installed yet.

| Public module | Main features | Detailed local information |
| --- | --- | --- |
| `abacus/3.11.0-beta11-auto` | MPI/OpenMP; PW/LCAO; HSE (LibRI); DeePKS (LibTorch); NEP; LibXC; ELPA; CUDA on GPU partitions | After loading in an allocation: abacus --info (full build options and libraries). |
| `abacus/3.11.0-beta12-auto` | MPI/OpenMP; PW/LCAO; HSE (LibRI); DeePKS (LibTorch); NEP; LibXC; ELPA; CUDA on GPU partitions | After loading in an allocation: abacus --info (full build options and libraries). |
| `abacus/develop-git-079fd0c-260724-sm70-auto` | MPI/OpenMP; PW/LCAO; CUDA; cuSOLVERMp; ELPA; LibXC | Full recorded build options: cat /opt/apps/abacus/abacus-develop-git-079fd0c-260724/abacus-info.txt |
| `abacus/v3.11.0-beta9-auto` | MPI/OpenMP; PW/LCAO; ELPA; LibXC; CUDA on GPU partitions | After loading in an allocation: abacus --info (full build options and libraries). |
| `berkeleygw/4.0-pgi-auto` | MPI/OpenMP; OpenACC/CUDA SM70; real/complex executables; HDF5; FFTW; ScaLAPACK; ELPA | Local build flags: cat /opt/apps/BerkeleyGW/4.0/arch.mk |
| `cp2k/2026.1-cuda12.9-sm70-auto` | MPI/OpenMP; CUDA SM70; ELPA; LibXC; FFTW; PLUMED; HDF5 | After loading in an allocation: cp2k.psmp --version (compiler, flags and linked libraries). |
| `cp2k/2026.2-auto` | MPI/OpenMP; DBCSR; ELPA; LibXC/Libint2; PLUMED; DFT-D4/TBLITE; HDF5; CUDA on GPU partitions | After loading in an allocation: cp2k.psmp --version (compiler, flags and linked libraries). |
| `exa-amd/0.2.0` | VASP workflow; CGCNN model; ASE; PyTorch | Exact installed package versions: cat /opt/apps/conda_env/exa-amd-0.2.0/share/sai/requirements.lock |
| `lammps/11jun2026-nep-dev-avx512` | MPI; KOKKOS CUDA SM70; PLUMED 2.9.4; DeePMD 3.1.2 plugin; USER-NEP | After loading in an allocation: lmp -h (full package/style list and compiler information). |
| `lammps/11jun2026-nvhpc245gnu-ompi508-sm70-dev` | MPI; KOKKOS CUDA SM70; PLUMED 2.9.4; DeePMD 3.1.2 plugin | After loading in an allocation: lmp -h (full package/style list and compiler information). |
| `lammps/30Sep2026-deepmd320-auto` | MPI/OpenMP; GPU package + KOKKOS CUDA (V100/SM70); DeePMD; PLUMED; Python; REAXFF; ML-IAP/ML-SNAP | After loading in an allocation: lmp -h (full package/style list and compiler information). |
| `multiwfn/2026.3.18` | CPU/OpenMP; command-line noGUI build; density/orbital/topology analysis | Run Multiwfn interactively; its startup banner and menu describe this build. |
| `nepadapters/1.0.4-lammps30Sep2026` | nep/cpu; nep/gpu/kk (Kokkos CUDA); ordinary NEP4/NEP5 | After loading, lmp -h lists the available packages and pair styles. |
| `nepadapters/1.0.5-lammps30Sep2026` | nep/cpu; nep/gpu/kk (Kokkos CUDA); ordinary NEP4/NEP5 | After loading, lmp -h lists the available packages and pair styles. |
| `orca/6.1.0` | CPU; HF/DFT and correlated wavefunction methods; external OpenMPI 4.1.6 | Local runtime instructions: less /opt/apps/orca-6.1.0/6.1.0/README.md |
| `phonopy/2.48.0` | CPU; harmonic phonons; finite displacements; spglib; Python API | CLI version/options after loading: phonopy --version; phonopy --help. |
| `qe/7.6-auto` | MPI/OpenMP; PW/PH/NEB/PP; HDF5; ScaLAPACK; FFTW3; OSCDFT; CUDA/OpenACC on GPU partitions | Load inside the matching Slurm allocation before running inspection commands. |
| `sctk/1.2.1` | MPI/OpenMP; superconducting DFT; sctk.x + pw.x + ph.x | Installed build identity: cat /opt/apps/sctk/1.2.1/INSTALL_METADATA.txt |
| `sctk/1.2.1-gpu` | MPI/OpenMP; superconducting DFT; sctk.x + pw.x + ph.x; NVHPC CUDA/OpenACC | Installed build identity: cat /opt/apps/sctk/1.2.1-gpu/INSTALL_METADATA.txt |
| `sscha/1.6.0` | CPU; stochastic anharmonic phonons; TD-SCHA 1.6.1; electron-phonon tools | Installed identity: cat /opt/apps/sscha/1.6.0/INSTALL_METADATA.txt |
| `sscha/1.6.2` | CPU/MPI; stochastic anharmonic phonons; CellConstructor; ASE; spglib | Exact installed package versions: cat /opt/apps/conda_env/sscha-1.6.2/share/sai/requirements.lock |
| `elpa/2025.06.001-2509-pgi-sm70-auto` | MPI; symmetric/Hermitian eigensolvers; NVIDIA CUDA; ScaLAPACK | Compiled GPU options: cat /opt/devtools/elpa/elpa-2025.06.001-2509-pgi-avx512/include/elpa-2025.06.001/elpa/elpa_configured_options.h |
| `elpa/2026.02.001-2603-gnu-auto` | MPI/OpenMP; symmetric/Hermitian eigensolvers; AVX2/AVX512; CUDA on V100 | GPU options: cat "$ELPA_ROOT/include/elpa_openmp-2026.02.001/elpa/elpa_configured_options.h". |
| `fuse2fs/1.47.4-sai` | FUSE ext2/3/4; upstream I/O fixes; OFD locks; explicit read-only backing files | Active configuration: cat "$APPTAINER_CONFIG_FILE". |
| `openmpi/4.1.6-cuda12.4-fix` | C/C++/Fortran MPI; CUDA-aware communication; UCX | Full build/component information after loading: ompi_info --all. |

Six beta gateways additionally show their accepted source commit, snapshot, partitions,
feature summary, program-specific inspection command, and the local generation manifest.
