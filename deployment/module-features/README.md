# Module descriptions and local build information

`module whatis` uses one entry per module: program/version followed by
`Features:` with its main capabilities and CPU/GPU backend.
`module help` gives a concrete path to a **static text record**. Users can read
it on a login node without loading a module, starting a container, or running the
program. The `Features:` description is included in the same `module whatis` entry.

For example, the installed LAMMPS release's complete recorded help is available now:

```bash
cat /home/stardust/sai-hpc-software/module-info/20261111/lammps/30Sep2026-deepmd320-auto/index.txt
cat /home/stardust/sai-hpc-software/module-info/20261111/lammps/30Sep2026-deepmd320-auto/4V100.txt
```

The index lists the exact file for every captured partition. Records contain the
unabridged standard program output, command, capture job/node/time, module hashes,
and saved configuration files where appropriate. NEPAdapters also includes an
explicit plugin load/list and style inventory: LAMMPS help by itself does not
initialize these plugins. QE has no standalone `-h` option; its record contains
the original configure command and full CMake cache. Beta QE and GPUMD also
include output from their successful accepted calculations.

The 31 public module entries have 100 static text files, about 10.4 MiB total.
These are documentation only, outside the application prefixes and SIFs. No
scientific installation is expanded or changed. ORCA's standard banner was
captured during a small successful serial H2 run. The historical Multiwfn startup
capture includes its EOF diagnostic; it identifies version 2026.3.11, released on
2026-03-18. The module name retains the installation's release-date label.

## Coverage and installation

The owner-based audit on 2026-11-10 found 34 files owned by `saiuser01` in the
readable public module tree: **31 public entries and 3 hidden historical
backups**. This package updates all 31 entries (27 apps and 4 devtools), including
six beta gateways. The backups remain unchanged. `manifest.json` binds every
candidate to the exact original and resulting file hashes. `descriptions.json`
reviews the 25 fixed application/library descriptions; six beta gateways inherit
rotating source/version/acceptance details. The manifest also pins every prepared
static record by SHA256. Both the original modulefiles and the preceding feature
documentation update are valid upgrade inputs.

One directory, `/opt/modules/modulefiles/apps/pwmat`, is owned by `entropy` and
is inaccessible to `stardust`. Its contents and file owners could not be audited.
The reusable audit reports this as an error, not a complete inventory. Run it
from an account with access to check for additional `saiuser01` files there:

```bash
python3 audit.py > "$HOME/sai-module-audit.json"
```

With the account allowed to update these `/opt` modulefiles:

```bash
bash /home/stardust/sai-hpc-software/deployment/module-features/install.sh --check
bash /home/stardust/sai-hpc-software/deployment/module-features/install.sh --install &&
python3 /home/stardust/sai-hpc-software/deployment/module-features/check.py --installed
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

Daily beta acceptance captures the program's complete build output on its
allocated compute node using `controller/build_info.py`. Publication verifies
that each record belongs to the accepted source, SIF hash, catalog and allocation.
The publisher copies only text to:

```text
/home/stardust/sai-hpc-software/sif-beta/build-info/<generation>/<software>/index.txt
/home/stardust/sai-hpc-software/sif-beta/build-info/<generation>/<software>/<PARTITION>.txt
```

The generation manifest hashes those files, and selection checks them before
rotating the beta link. A missing record in a new acceptance job prevents its
publication. Historical accepted generations remain unchanged: their complete
records were prepared separately, and the fixed `/opt` gateway displays the
corresponding path. New generation modules provide their own path, with no second
`/opt` update needed. Generic staging selectors expose their existing manifests or
receipts; they do not ask users to execute an inspection command.

`capture.py` and `capture.json` describe the one-time owner audit. Captures live in
project storage; only reviewed text is published by `prepare_records.py`. The
administrator installer verifies public read/traverse permissions as well as all
record hashes before updating modules. `check.py` exercises real login-node Lmod
and checks that every help output points to a public static index and every
whatis output contains exactly one entry. Beta gateways combine descriptions
from historical sealed generations without rewriting their files.

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
