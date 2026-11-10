# Module descriptions and local build information

`module whatis` describes the installed configuration in two short lines:
program/version, then `Features:` with its main capabilities and CPU/GPU backend.
`module help` gives the program-specific inspection command, readable local
build record, and relevant limits. This documentation does not run `--version`,
import scientific Python packages, or start the application during discovery.

Example after this update:

```text
lammps/30Sep2026-deepmd320-auto : LAMMPS 30 Sep 2026 / DeePMD 3.2.0; V100 partition selection
lammps/30Sep2026-deepmd320-auto : Features: MPI/OpenMP; GPU package + KOKKOS CUDA (V100/SM70); DeePMD; PLUMED; Python; REAXFF; ML-IAP/ML-SNAP
```

After loading the corresponding application module in an allocation:

```bash
lmp -h                         # full compiled package/style inventory
cp2k.psmp --version             # CP2K build, compiler and library information
abacus --info                   # ABACUS enabled/disabled build options
ompi_info --all                 # OpenMPI configuration/components
```

Some programs have no standalone version option. For GPUMD, use its normal job
banner and the source/image catalog shown in beta help. For installed BerkeleyGW,
`cat /opt/apps/BerkeleyGW/4.0/arch.mk` shows local build flags. Python environments
point to their public `share/sai/requirements.lock` and `packages.json`, rather
than restricted provenance records.

## Coverage and installation

The owner-based audit on 2026-11-10 found 34 files owned by `saiuser01` in the
readable public module tree: **31 public entries and 3 hidden historical
backups**. This package updates all 31 entries (27 apps and 4 devtools), including
six beta gateways. The backups remain unchanged. `manifest.json` binds every
candidate to the exact original and resulting file hashes. `descriptions.json`
reviews the 25 fixed application/library descriptions; six beta gateways inherit
rotating source/version/acceptance details.

One directory, `/opt/modules/modulefiles/apps/pwmat`, is owned by `entropy` and
is inaccessible to `stardust`. Its contents and file owners could not be audited.
The reusable audit reports this as an error, not a complete inventory. Run it
from an account with access to check for additional `saiuser01` files there:

```bash
python3 audit.py > owner-audit.json
```

With the account allowed to update these `/opt` modulefiles:

```bash
bash install.sh --check
bash install.sh --install
python3 check.py --installed
module --ignore_cache whatis lammps/30Sep2026-deepmd320-auto
module --ignore_cache help abacus/develop-git-079fd0c-260724-sm70-auto
```

Default operation is read-only. Installation preflights all 31 sources, targets,
owners and advertised record paths before replacing anything. A different site
module or symlink stops the update. Original files are backed up in the installing
account's `~/sai-hpc-module-backups/<timestamp>/`, preserving relative paths.
Replacement is atomic per file and preserves owner, group and mode. Rerunning
is idempotent. Application binaries, libraries and environment selection remain
unchanged; no new files are installed under `/opt/apps`.

To preview without installing, use a fresh shell in this directory:

```bash
module use "$PWD/modulefiles/apps" "$PWD/modulefiles/devtools"
module --ignore_cache whatis berkeleygw/4.0-pgi-auto
module --ignore_cache help phonopy/2.48.0
python3 check.py
```

`--ignore_cache` inspects current metadata immediately. This package does not
alter the site's shared Lmod cache configuration.

## Subsequent publications

SIF/beta and native generators share `feature_summary` and `inspection_hint` in
`controller/module_publication.py`. Maintain these alongside the configured
recipes and accepted inventories. Describe each specific build, not upstream
defaults or another installed version's capabilities.

Fixed beta gateways supply missing descriptions for existing immutable
generations. New generations identify their own documentation and bypass this
fallback, so future feature changes need no further `/opt` edit. Beta help includes
a local catalog/manifest path, source commit and inspection command. SIFs, current
beta links and sealed generation files are not edited by this updater.

Observed differences reflected in the descriptions:

- The July ABACUS build has LibRI, LibTorch and NEP disabled; its readable
  `abacus-info.txt` is authoritative. New release functionality is not assigned to it.
- DeePMD's PyTorch backend uses CUDA; its TensorFlow/JAX backends are CPU-only.
- The old ELPA 2025 module has `-auto` in its name but points to fixed AVX512.
  Its `.pc` retains the old build prefix. This update describes that state; it
  does not change the library or compiler flags.
- Public `fuse2fs/1.47.4-sai` uses the existing `1.47.4-sai1` payload directory.
- NEPAdapters remains a separate plugin for the named LAMMPS release.

Validation and the owner inventory are in
[`docs/evidence/module-features-20261110`](../../docs/evidence/module-features-20261110).

## LAMMPS release check

As checked on 2026-11-10, the latest stable release is
[`stable_30Sep2026_update1`](https://github.com/lammps/lammps/releases/tag/stable_30Sep2026_update1),
published at 2026-11-01 19:08:30 UTC (2026-11-02 03:08:30 Asia/Shanghai).
The 2026-11-03 retry built source `098ffdefa525` on all three V100 partitions.
Recorded SIF acceptance jobs 2156051 (16V100), 2156109 (4V100) and 2156216
(8V100V0) each passed 14 single/two-node GPU cases, with 71 packages and 2,473
styles. Physical export, installation and native runtime acceptance of update1
remain separate work. The public native module is still the original 30Sep2026
release. Updating descriptions does not upgrade LAMMPS.
