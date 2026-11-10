# Module feature descriptions

`module whatis` must show the enabled application features, including the GPU
backend and major optional interfaces. Version, partition selection and SIF
identity alone do not describe a usable software configuration.

Follow the site's existing LAMMPS summaries: retain the version line and add a
compact `Features:` line. Put these lines before the allocation check so they
work on login nodes without loading any dependencies. Keep detailed commands,
source identity and acceptance limits in `module help`.

The SIF, beta and native generators share `controller/module_publication.py`'s
`feature_summary`. Update it whenever build options change and check the
configured recipe and accepted feature inventory. Do not copy an upstream
feature list or label TensorFlow/JAX as CUDA backends just because PyTorch uses
CUDA. External NEPAdapters is a separate plugin; the LAMMPS feature summary does
not claim it is bundled.

This directory updates descriptions for the currently installed ABACUS
3.11.0-beta12, CP2K 2026.2, LAMMPS 30Sep2026 and QE 7.6 release modules. It compares
all non-`module-whatis` lines before changing any file. Executables, libraries,
paths, dependencies and module load/unload logic are unchanged. A different
site module body stops the update. Backups are saved in the installing account's
`~/sai-hpc-module-backups/`, not `/opt`.

From the repository checkout, preview and install with the account that can
write those four `/opt/modules/modulefiles/apps` files:

```bash
python3 deployment/module-features/install.py --check
python3 deployment/module-features/install.py --install
module --ignore_cache whatis lammps/30Sep2026-deepmd320-auto
```

The generator change applies to subsequent beta publications. Existing fixed
beta generations retain their original descriptions and acceptance hashes; the
permanent `/opt` beta forwarding modules need no update.

LAMMPS release check on 2026-11-10: upstream's latest stable release is
[`stable_30Sep2026_update1`](https://github.com/lammps/lammps/releases/tag/stable_30Sep2026_update1),
published on 2026-11-01 at 19:08:30 UTC (2026-11-02 03:08:30 Asia/Shanghai).
The 2026-11-03 retry built source `098ffdefa525` on all three V100 partitions.
Recorded SIF acceptance jobs 2156051 (16V100), 2156109 (4V100) and 2156216
(8V100V0) each passed 14 single/two-node GPU cases, with 71 packages and 2,473
styles. These are SIF correctness results; physical export, installation and
native runtime acceptance of update1 are still separate work. The public native
module remains the original 30Sep2026 release. This description update does not
upgrade LAMMPS.
