# Concrete module help paths

Thirteen of the 31 reviewed public modules described installation or data paths
using environment variables that do not exist before module load. Login nodes
cannot load many of these modules, so those descriptions were not usable there.

The updated administrator package writes out the fixed NEPAdapters and fuse2fs
paths directly. Ten partition/ISA selectors instead point to small static indexes
under `/home/stardust/sai-hpc-software/module-info/20261111-paths/`. Each index
lists the exact installation, data or configuration paths and links to the
original complete build captures. The old records and sealed beta generations
remain intact. The new indexes are committed in `deployment/module-features/records`.

Validation:

- All 31 modules passed real login-node `module whatis` and `module help` checks:
  62 checks, zero failures. The full output is in [lmod-preview.json](lmod-preview.json).
- Every module still produces exactly one whatis entry with Features. Help has
  no unresolved installation variables, and all advertised indexes are publicly
  readable. All explicit paths in the ten new indexes exist on the cluster.
- Byte comparison after removing `ModulesHelp` confirms the 13 changed module
  runtime bodies are unchanged. The four maintained native release copies and
  their applicable manifest hashes match the reviewed module sources.
- The 29 existing ABACUS beta12, QE and LAMMPS deployment tests passed. The first
  ABACUS invocation needed `PYTHONPATH=tests` for its sibling test import; it
  then passed without source changes.
- Installer preflight accepted all 31 currently installed modules: 13 help
  updates pending, 18 already current. No application files were changed.

The checked package is ready at:

```text
/home/stardust/sai-hpc-software/deployment/module-features
```

An administrator can apply it with the existing `install.sh --install` and then
run `check.py --installed`. Preparing the package did not modify `/opt`.
