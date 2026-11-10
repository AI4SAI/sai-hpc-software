# Static build information on login nodes

All 31 readable public modulefiles owned by `saiuser01` now have prepared static
build records. The `/opt` documentation update is administrator-installed using
`deployment/module-features/install.sh --install`; preparing these records did
not change any application, SIF, sealed beta generation, or public modulefile.

Native records:

```text
/home/stardust/sai-hpc-software/module-info/20261111/<application>/<module-version>/index.txt
```

Beta records are bound to each current generation, source SHA and SIF hash:

```text
/home/stardust/sai-hpc-software/sif-beta/build-info/<generation>/<application>/index.txt
```

The manifest in the deployment package pins the prepared text hashes. The index
points directly to full per-partition text files. It is readable without loading
modules or executing programs. The 100 indexed text files occupy about 10.4 MiB;
they include complete LAMMPS package/style registries, ABACUS build options, CP2K
compiler/library information, DeePMD CLI help, ELPA configuration/kernel lists,
OpenMPI component/configuration output, and installed Python package inventories.

Capture jobs 2354743 (4V100), 2354744 (16V100), 2354745 (8V100V0) and 2354746
(DSPRHBM) recorded help/configuration using the actual installed modules.
Supplement jobs 2354864, 2354865 and 2354867 captured the correct ELPA command,
NEPAdapters plugin information and ORCA's banner. 2354866 was canceled while
pending: 8V100V0 and 16V100 select the identical ELPA `avx2/nvidia` prefix. Their
configuration files were compared, and both index entries point to the original
16V100 capture, retaining its actual allocation identity.

Findings incorporated into the documentation:

- `pw.x -h` is not a supported independent help option. QE uses its saved CMake
  cache/configure command, plus accepted calculation output for beta. Image
  configuration bytes are verified against the embedded installation manifest.
- `lmp -h` does not initialize NEPAdapters. The additional recorded `plugin load`,
  `plugin list`, and `info styles` output shows `nep/cpu` and `nep/gpu/kk` for both
  installed NEPAdapters versions.
- ELPA 2026 installs `elpa2_print_kernels_openmp`, not `elpa2_print_kernels`.
- Phonopy does not accept the guessed `--version`; its complete `--help` and
  installed package records are used instead.
- ORCA's complete banner was recorded in a small successful serial H2 run.
- Multiwfn identifies itself as version 2026.3.11, released on 2026-03-18. Its
  initial banner capture retains the EOF diagnostic; it is not a science check.
- The historical CP2K 2026.1 CUDA build cannot start on DSPRHBM without
  `libcuda.so.1`. Its GPU captures document the installed build; no CPU support
  or repair is claimed.

Validation: 136 focused controller/deployment tests passed; all 62 real Lmod
whatis/help checks passed on the login node, including public read/traverse
permission checks for every advertised static index. The 25 native module runtime
bodies are unchanged. The six gateways only add static documentation, and new
daily generation modules carry their own paths. This does not constitute new
scientific acceptance of any historical installation.

Raw capture records and diagnostics remain under
`/org/org-open-sai/proj-sai-hpc-software/operations/module-static-info-20261110/`.
`lmod-preview.json` contains the complete login-node preview. The original owner
audit and its inaccessible `pwmat` directory limitation remain documented in
`../module-features-20261110/`.
