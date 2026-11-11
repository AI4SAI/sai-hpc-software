# Installation and workflow audit — November 12, before 03:00

## Physical applications

Both administrator installers now pass `--verify` against physical `/opt`:

- NepTrainKit 3.1.5: `/opt/apps/neptrainkit/3.1.5`, `neptrainkit/3.1.5`.
  Job 2379816 completed 0:0 in 1m22s on 16v100n05; the bound
  [receipt](neptrainkit-physical-opt.json) covers CPU/CUDA results, loader checks,
  offscreen main-window startup and module unload. Interactive rendering is
  outside the offscreen check.
- GPUMDkit 1.5.9: `/opt/apps/GPUMDkit/1.5.9`, `gpumdkit/1.5.9`.
  Job 2380728 completed 0:0 in 18s on 16v100n09. The
  [physical verification](gpumdkit/verification.json) binds the installed prefix,
  accepted SIF, module, fixed inputs and raw results. Conversions, periodic
  distances, two-worker NEP prediction, PNG generation, dependency checks,
  Python import isolation and unload restoration passed.

The first GPUMDkit physical check, 2380678, also passed its numerical checks,
but its temporary test script passed the image identity under the wrong
environment-variable name. It was retained as a diagnostic and replaced by
2380728 for the final complete provenance receipt. Neither check changed `/opt`.

The GPUMDkit native permission fix is PR #94. Its initial GitHub validation
jobs stopped during apt installation because the Ubuntu 26 hosted runners
could not resolve `security.ubuntu.com`; unit tests had not started. All four
failed workflows were explicitly rerun at the same commit. This infrastructure
failure is separate from the successful cluster installation and science checks.
All four validation reruns then passed, and PR #94 was merged as `bc1486c`.

## Daily builds and scheduler

At this audit, the local date is November 12 and the next scheduled time,
03:00 Asia/Shanghai, has not arrived. The latest full daily run is still
[42880817991](https://github.com/AI4SAI/sai-hpc-software/actions/runs/42880817991),
dispatched November 11 at 03:00:03 and completed at 05:58:18 local time.
The `vm-sai-hpc` runner is online and idle; the old VPS runner is intentionally
offline. VM journals show scheduler invocations every five minutes through
00:50, each correctly reporting `already-requested 2026-11-11` before today's
03:00 boundary. No timer change or duplicate full build was requested.

The public alias targets and version-module hashes were compared with their
generation manifests. Daily publication receipts are copied in this directory:

| Application | Latest daily publication |
| --- | --- |
| ABACUS | Passed on 16V100, 4V100, 8V100V0, DSPRHBM |
| CP2K | Passed on 16V100, 4V100, 8V100V0, DSPRHBM |
| LAMMPS | Passed on all three V100 partitions |
| GPUMD | Passed on all three V100 partitions |
| QE | Passed on 16V100, 4V100, 8V100V0, DSPRHBM |
| DeePMD-kit | Build/science passed; 16V100 two-GPU publication acceptance failed |

DeePMD's latest attempted publication remains failed: se_e2_a and DPA2 hit a
collective tensor-size mismatch during statistics initialization on job
2359111 / 16v100n08. The 4V100 and 8V100V0 training/restart cases passed. The
public alias still selects the accepted `deepmd-host-data-20261110-r1`
generation; the failing candidate was not promoted. No new cause or repair is
claimed by this audit. Detailed prior diagnosis is in the
[November 11 audit](../workflow-audit-20261111/README.md).

The `20261110` labels in the November 11 publications reflect UTC naming, not
a missed November 11 dispatch. Local-date labels remain an optional display fix.

## LAMMPS update1 defaults

The current upstream latest release is
[`stable_30Sep2026_update1`](https://github.com/lammps/lammps/releases/tag/stable_30Sep2026_update1),
published at 2026-11-01T19:08:30Z (November 2 in Asia/Shanghai).

The final queued 8V100V0 jobs completed: 2374775 passed native LAMMPS in 1m55s;
2374898 passed cross-node NEPAdapters in 24s. All nine jobs across the complete
native/plugin matrix are successful. Current raw-results verification rechecked
all three partitions, and the home installer preflight passed.

The preparation monitor finished at November 11 23:21 and produced its manifest
and `DEFAULT_PACKAGE_READY` marker. The two public default files remain absent;
unversioned loading still chooses the old LAMMPS/NEPAdapters dependency pair.
The remaining action is the administrator's installation:

```bash
bash /home/stardust/sai-hpc-software/deployment/lammps-update1-default/install.sh --install
bash /home/stardust/sai-hpc-software/deployment/lammps-update1-default/install.sh --verify
```

This publishes the default update1 pair using the existing plugin files. The
agent did not write public `/opt` modules during the audit.

An additional [default-load verification](lammps-default/verification.json)
passed as job **2381151**, 16v100n13, COMPLETED 0:0 in 2m07s. A private preview
tree produced by the actual installer selected
`lammps/30Sep2026_update1-deepmd320-auto` for plain `module load lammps` and
`nepadapters/1.0.5-lammps30Sep2026_update1` for plain `module load nepadapters`;
the latter automatically selected update1 as its dependency. Loading and
unloading passed with `LMOD_IGNORE_CACHE=no` and the site's
`LMOD_CACHED_LOADS=no`, without `--ignore_cache`. Global cache contents and
timestamps were unchanged. These are prospective-default checks; the public
default files are still awaiting administrator installation.

The same job used the actual `/opt` executable and existing NEPAdapters 1.0.5
plugin for a one-GPU Pb/Te NEP4 calculation. Reparsed differences from the
reference were 1.9069e-7 for energy, 2.7583e-7 for maximum force component and
1.4292e-6 for maximum virial component, all within the existing tolerances.
The [request](lammps-default/request.json), module selections, raw outputs and
verification scripts accompany the receipt. The earlier attempt 2381131
stopped while recording its environment because the compute node lacked `rg`;
replacing that diagnostic command with `grep` enabled the completed run.
It was not a LAMMPS or plugin failure. The earlier complete three-partition
matrix remains the acceptance basis; its final receipts are in
[lammps-matrix](lammps-matrix/).

## Global Lmod cache

Lmod 9.2.4 uses `/opt/modules/lmod/cache`, with timestamp file
`/opt/modules/lmod/cache/system.txt`. Both still had November 9 modification
times at the audit. Normal `module avail` omitted the newly installed toolkit
modules; `module --ignore_cache avail` found both, and their whatis/help are valid.

The cache directory is mode 0775, owned by `entropy:entropy`; its timestamp is
0664 and its Lua/compiled caches are 0644 with the same owner. There are no named
ACL entries. `saiuser01` is not in group `entropy`, so it currently cannot rebuild
or remove the shared cache. Module-file ownership does not grant permission
to the separate cache directory.

The installed upstream helper generates a fresh Lua and compiled spider cache,
retains the prior cache, and publishes the matching timestamp. A separate
[maintenance entry point](../../../deployment/lmod-cache/README.md) uses that
helper and all five initial module roots. No cache or site configuration was
changed by this audit. Delegating maintenance to `saiuser01` requires separate
cache-directory and existing-backup write permission.

The same upstream helper was then run against the real five public module roots
with an isolated cache under project storage. It completed, emitted the Lua and
compiled caches with a valid timestamp order, and included both toolkit modules
and LAMMPS update1; the [receipt](lmod-cache-verification.json) records that scope.
The home `refresh.sh --check` correctly rejected the unprivileged stardust
account before any global mutation. No global refresh has been performed.
