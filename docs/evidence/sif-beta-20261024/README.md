# Branch-beta Lmod delivery — 2026-10-24

The five branch-beta modules are active in
`/home/stardust/sai-hpc-software/modulefiles/apps`, pointing to generation
`/home/stardust/sai-hpc-software/sif-beta/generations/20261024-r1`.
Their fixed launchers and 17 readonly SIFs are the public-readable home copies
under `deployment/sif-development-20261024/payload`. Promotion rehashed all
images and compared the relocated catalog with the catalog that passed the
four original SIF/MPI scientific check jobs. Runtime SHA256 remains
`49c575bd293fddf094e4f736fb33c3d0a0522b174e53554e43c89999deaa1759`.

`raw/generation.json` binds the catalog, runtime, original validation and every
generated module/template. `SHA256.json` covers the archived raw records here.

Actual Lmod 9.2.4 checks (`raw/login/verification.json`) passed:

- All five login-node loads returned failure after printing full source commit,
  fixed version and template-directory help. PATH and loaded-module state did
  not change, and application environment variables did not leak.
- `module help/show` followed the same gateway into home metadata.
- A private synthetic module demonstrated selection, rotation, unload and
  rollback through an unchanged public gateway. It did not spoof Slurm or
  execute HPC software on the login node. The loaded generation's PATH survived
  rotation; unload restored the original PATH; subsequent load selected the new
  generation. Help in an already loaded environment described the pinned one.

Real compute job **1921631**, 16V100, one node/two ranks/one GPU, completed
`0:0` in **30 seconds** using account `proj-sai-hpc-software`:

- All five actual beta modules loaded and unloaded, exposing readable images
  and templates and then removing their saved application environment.
- Host MPI launched CP2K `--version` and LAMMPS `-h` inside the home SIFs;
  DeePMD `dp --version` also started successfully. ABACUS and all three GPUMD
  commands resolved to their fixed SIF wrappers.
- Five runtime traces bind the CP2K/LAMMPS/DeePMD commands to the selected
  checksums, MPI, affinity and allocation. Each image was fully hashed once;
  second MPI ranks reused the checksum cache.

This is a module/startup check. It reuses the prior 17 numerical checks in
[sif-lmod-20261024](../sif-lmod-20261024/README.md), whose image/runtime bytes
are unchanged. It does not claim new performance or numerical measurements.

Job **1921621** remains failed (`1:0`, six seconds). The check script set
`SAI_SIF_TRACE_DIR` without creating it, so the runtime rejected the diagnostic
directory before launching CP2K. Its raw error and accounting are retained in
`raw/failed`. The retry created the private trace directory; no SIF or runtime
change was needed.

Local validation passed 17 SIF tests, covering all partition selections,
login/salloc rejection, saved-generation unload after pointer removal, complete
matrix/acceptance validation, catalog relocation and shell-template syntax.
The installer passed its check and two consecutive installations into a private
test module directory. `/opt` remains administrator-driven; the prepared public
handoff at `/home/stardust/sai-hpc-software/deployment/sif-beta` contains only
the installer, README and five fixed forwarding modules.
