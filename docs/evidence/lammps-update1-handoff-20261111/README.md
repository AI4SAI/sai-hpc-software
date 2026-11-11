# LAMMPS update1 native administrator handoff — November 11

The administrator package is available at
`/home/stardust/sai-hpc-software/deployment/lammps-30Sep2026-update1`.
See [installation commands and the complete layout](../../../deployment/lammps-30Sep2026-update1/README.md).
Public `/opt` installation remains an administrator action; it has not been
performed by this handoff preparation.

## Accepted inputs

All three original SIF scientific receipts were reverified against their raw
evidence by the current `controller/lammps_acceptance.py`. The source tag is
`stable_30Sep2026_update1`, commit `098ffdefa525cabc82272925de02e9884f58c657`.

| Partition | Original build job | Original SIF science job | Result |
| --- | --- | --- | --- |
| 4V100 | 2153881 | 2156109 | COMPLETED, 14 cases |
| 16V100 | 2153453 | 2156051 | COMPLETED, 14 cases |
| 8V100V0 | 2154373 | 2156216 | COMPLETED, 14 cases |

The pinned inventories, SIF digests and original scientific receipts are stored
in the [release package](../../../deployment/lammps-30Sep2026-update1/release.json).
The public compressed handoff contains about 757 MiB of SIF images. Extraction
retains 340 regular files, one library symlink and 17 directories per partition,
including all 287 potentials. Total installed file content is 3,017,739,316 bytes
(2.8105 GiB). Existing physical DeePMD 3.2.0 inventories match their pinned hashes.

## Trial installation and execution

All three partitions were extracted and fully checked under:

```text
/org/org-open-sai/proj-sai-hpc-software/operations/lammps-update1-20261111/stage
```

[The installation log](stage-install.log) records `LAMMPS_UPDATE1_INSTALLED`.
[Real Lmod output](lmod-help.txt) confirms one whatis entry containing Features
and login-readable help with a concrete static-build-information path.

Native execution uses host MPI and the extracted binary, without Apptainer.
It compares the original 14 scientific cases and full 71-package / 2473-style
inventory, verifies native binary and loader paths, and checks module unloading.

| Partition | Native trial job | State at handoff preparation |
| --- | --- | --- |
| 16V100 | 2372848 | COMPLETED 0:0 in 1m25s; all 14 cases passed |
| 4V100 | 2372847 | Queued; not yet native-execution-qualified |
| 8V100V0 | 2372849 | Queued; not yet native-execution-qualified |

The [16V100 verification](native-16V100.json) recomputes the native result from
raw output and checks completed Slurm accounting. Its route is `staged-native`,
not `physical-opt`. The frozen requests and raw outputs are under:

```text
/org/org-open-sai/proj-sai-hpc-software/operations/lammps-update1-native-20261111-182859-1639600
```

An initial native test failed before LAMMPS started because `--export=NIL` did
not provide the account's `HOME` to host Open MPI. The submitted test script now
preserves the submitter's real HOME/USER/LOGNAME. Initial job 2372801 and cancelled
queued jobs 2372800/2372802 are not acceptance evidence. No SIF or native library
was changed to resolve this test-harness failure.

Five focused tests cover actual Squashfs extraction, rejection of corrupt
payloads, preservation of an existing different installation, rejection of
symlink ancestors, and rejection of retained links into omitted workflow files.
Python/shell syntax checks passed. Full repository checks are reported on
[PR #90](https://github.com/AI4SAI/sai-hpc-software/pull/90).

## Publication boundaries

The new module is `lammps/30Sep2026_update1-deepmd320-auto`. It points at:

```text
/opt/apps/lammps/lammps-release-stable_30Sep2026_update1-g098ffdefa525-rb6a6aadddb8b/<PARTITION>
```

The installer publishes the module after all three payloads are checked.
It installs standard binaries, libraries, headers, CMake/pkg-config interfaces,
profile scripts, potentials and the man page. Workflow material, tests, logs and
benchmark inputs stay outside `/opt/apps`.

The original LAMMPS module remains available. Existing NEPAdapters 1.0.4/1.0.5
modules still select the original 30Sep2026 release; their compatibility with
update1 has not been qualified. Public physical installation and its separate
native acceptance remain to be completed after the administrator installs.
