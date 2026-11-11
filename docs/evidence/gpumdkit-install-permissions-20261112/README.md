# GPUMDkit native installation permission fix — November 12

The administrator extracted the accepted 1.5.9 SIF, but installation stopped
before publishing the runtime or module with `changed mode: include/curses.h`.
The original installer compared native permissions directly with the archived
build-tree inventory. That inventory contains 21,630 regular files at 0664 and
620 executable files at 0775; the administrator's extraction has those files at
0644 and 0755. All other 143 regular files already have standard public modes.

Read-only verification of the administrator's complete extraction checked all
22,393 regular-file hashes, all names/types, links, and public permissions. It
passed. No content corruption was found and no public module had been created.

The failed extraction is at:

```text
/opt/apps/GPUMDkit/.1.5.9-incoming-1609074/opt/apps/GPUMDkit/1.5.9
```

## Fix

The installer now derives a native permission manifest from the authenticated
archive inventory: directories and executable files use 0755, other regular
files use 0644, and links retain their recorded targets. It verifies the whole
payload's content and structure before changing permissions in its staging
tree, then strictly verifies all permissions before publication. Normal
`--verify` remains strict; no hash or link check was weakened. Accepted SIF
bytes, archived inventory and the module are unchanged.

`--install --resume-stage` accepts only an explicitly selected sibling
`.1.5.9-incoming-<pid>` extraction owned by the installing account, with the
expected wrapper directories. It checks the entire payload again, reuses the
existing files, and removes only the emptied extraction directories after
publishing. This avoids extracting another 872.5 MiB / 22,393-file copy.

The existing stardust trial extraction retained the archived group-write bits.
A small control experiment using stardust's `/usr/bin/unsquashfs` and umasks
0002, 0022 and 0077 also retained them in every case. Therefore the observed
account/tool/storage difference is not attributed to umask alone. The fix
makes native publication independent of those preserved build permissions.

## Checks

Six focused installer tests cover public directory creation, inventory/link
safety, extraction-mode differences, content rejection before any chmod, strict
post-install verification, and constrained resume paths/ownership.

The full SIF was freshly extracted under umask 0077. A deliberate interruption
after normalization and before publication left no final runtime or module.
Resuming the complete stage (including a header reset to mode 0600) succeeded;
the empty extraction wrappers were removed and ordinary repeated installation
and verification both passed. `native-install.json` and `native-install.log`
record this integration test using the exact revised installer hash.

Job **2379958** then completed 0:0 in 16 seconds on 16v100n04. Through the real
module it selected the native Python, converted POSCAR and compressed LAMMPS
data, checked periodic distance, ran two-worker NEP prediction, produced a PNG,
checked dependencies and unloaded cleanly. Maximum energy error was zero;
force/virial errors were 8.33e-16 and 5.00e-16 respectively. The functional
receipt's raw-output hashes and completed accounting were independently checked.
The revised home handoff passed preflight and verification of this trial tree.

Cluster evidence is recorded under:

```text
/org/org-open-sai/proj-sai-hpc-software/operations/gpumdkit-install-permissions-20261112
```

`administrator-extraction.json` records the read-only check of the actual failed
installation. `umask-reproduction.json` records the independent extraction
experiment. Native trial and functional results are recorded separately from
the administrator's later completion of physical `/opt` publication.

## NepTrainKit physical installation

The other package had installed successfully. Its actual `/opt` runtime was
then checked in job **2379816**, which completed 0:0 in 1m22s on 16v100n05.
Installed-library resolution, CPU/reference and CUDA/CPU results, offscreen
window startup/closure and module unload passed. The bound
`neptrainkit-physical-opt.json` receipt checks the installed payload, job inputs,
completed accounting and result files. Interactive OpenGL rendering remains
outside this offscreen check.
