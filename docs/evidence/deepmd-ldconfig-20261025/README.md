# DeePMD SIF ldconfig and JIT runtime — 2026-10-25

The published DeePMD beta runtime now provides `/sbin/ldconfig` and its host
library cache. Actual DPA2 CUDA training and checkpoint restart pass on all
three V100 partitions. Triton caches use the invocation's private tmpfs
automatically, including its dump and override directories; users need no
cache environment settings.

```text
deepmd-kit/master-beta-later
  -> master-beta-20261024-558f56962775-r75ef5179f1e8
generation: 20261025-deepmd-ldconfig-r1
runtime SHA256: 75ef5179f1e8d578a4507766dcb521cf7c1db7b889d413f63106462c327d1db4
```

The source remains `558f56962775098b8b554e341c3b37d2f86e6aeb`. The three accepted
SIF files are reused directly. The new immutable runtime lives at
`/home/stardust/sai-hpc-software/sif-beta/runtimes/20261025-75ef5179f1e8`.
Only DeePMD's rolling alias was selected for this generation; the other four
applications retain their existing generations, and previous fixed DeePMD
modules remain available. The administrator's existing static `/opt` gateway
automatically reads the new home-maintained alias.

## Fix

Triton 3.7.1 calls `/sbin/ldconfig -p` before trying `LD_LIBRARY_PATH`. The SIF
already exposes host `/usr` and `/lib`, but lacks the host's `/sbin -> usr/sbin`
alias. DeePMD's runtime adds two read-only binds:

```text
/usr/sbin:/sbin:ro
/etc/ld.so.cache:/etc/ld.so.cache:ro
```

The directory bind preserves Debian/Ubuntu's `ldconfig` shell wrapper and
`ldconfig.real` together. `/usr/sbin` also exists inside the restricted-node
bootstrap, which already mounts `/usr`. Reading the host cache matches the
host libraries already mounted into the SIF. No privileged `ldconfig` update
or system-library installation is performed.

Triton ignores `XDG_CACHE_HOME` and defaults to `~/.triton`. The launcher now
sets `TRITON_HOME` to its private scratch directory. PyTorch's kernel-cache
parent directories are prepared there too. Final tests set no custom Triton
cache path; they observe 17 compiled cache files (about 234 kB) in private
`/dev/shm/sai-<uid>-<job>-0/.../.triton/cache`. Namespace teardown reclaims them.
Inputs, checkpoints and outputs stay in the user's project directory.

## Acceptance and remaining DDP defect

| Job | Partition | Result |
| --- | --- | --- |
| 1930113 | 16V100 | ldconfig/CUDA discovery, GPU inference, `se_e2_a` and DPA2 single-GPU training/restart pass |
| 1930114 | 4V100 | Same checks pass through the AppArmor-compatible bootstrap |
| 1930115 | 8V100V0 | Same checks pass |
| 1930174 | 4V100 | Published `/opt` gateway and ordinary `dp --pt train` path checked separately |

Each full training case runs eight steps, saves a checkpoint, then loads it in
a fresh Python process and continues to step twelve. Curves stay finite; the
host verifier rereads the final checkpoint hashes. GPU inference retains the
existing energy/force/virial tolerances. The final jobs also confirm that the
compiled Triton cache lives under the runtime's private memory root.

**Multi-GPU training remains unaccepted.** Two-GPU DPA2 now completes its first
training step, then fails with the same independent unused-parameter DDP defect
as `se_e2_a`. DPA2's empty trainable compression parameters are indices 6, 7,
14 and 15 in this fixture. The source trainer still sets
`find_unused_parameters=False` for single-task training. This runtime fix
does not patch DeePMD's training algorithm or claim that DDP now passes.
The module help continues to state that multi-GPU training is unaccepted.
Native `/opt` 3.2.0 PyTorch DDP remains the verified multi-GPU route described
in the [earlier training audit](../deepmd-multigpu-20261025/README.md).

## Records

Work and outputs are under
`/org/org-open-sai/proj-sai-hpc-software/runtime-tests/deepmd-ldconfig-20261025-r2`.
Jobs use account `proj-sai-hpc-software`, with no GPU CPU-count override or
physical `/tmp` scratch. Preliminary jobs `1930056`, `1930069`, `1930070`
validated the binds with test-configured caches; the final jobs above validate
automatic cache placement through the updated launcher.

`verify-site.py` reparses real project outputs and binds them to the exact SIF,
catalog and runtime hashes before publication. `publish.py` also checks terminal
successful Slurm states, prepares a new immutable public runtime, and selects
only DeePMD. `raw` contains both rounds of results, final traces, controller and
public runtime bytes, publication records and the public-module check. Large
checkpoint/model binaries stay in the project; their hashes are retained.
`SHA256.json` covers all archived raw files. Existing SIF/module tests pass
(25 tests), with repository CI checked before merging.
