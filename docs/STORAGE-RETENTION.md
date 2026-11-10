# Build storage and beta retention

## What rotation currently does

`daily_beta.py` revalidates each partition, makes a public SIF bundle, checks the
host-MPI/container launch route, and atomically switches `*-beta-later` to a
fixed dated module. The public dispatcher in `/opt/modules` does not change.
The old fixed modules and bundles remain available. **Alias rotation is not
garbage collection:** historical SIFs are not currently removed automatically.

There are usually two retained SIF copies: the original accepted build image
on project storage, and the public beta copy under
`/home/stardust/sai-hpc-software/sif-beta/runtimes`. Some early bundles share
images outside their own directory. A recursive removal based only on age can
therefore break another module, an acceptance record, or a deployment handoff.

## Successful build scratch

The main ABACUS/CP2K/QE/GPUMD controller already removes its own `work.ext3` and
`final.squashfs` after the final SIF passes build verification. Independent
LAMMPS and DeePMD builds now do the same: LAMMPS after SIF verification, DeePMD
after its scientific/training receipt is sealed. Earlier failed steps retain
their scratch for diagnosis. These are exact-file removals inside the current
allocation, not recursive deletion of a run directory.

SIFs, requests, source and recipe hashes, compiler/cache receipts, Slurm logs,
runtime/scientific evidence and compiler caches remain. In particular, DeePMD's
`runtime` directory is evidence, not generally disposable temporary space.
LAMMPS now also copies `ccache-layout.json` into `results`.

LAMMPS ccache has a 20 GiB limit per target. CP2K dependency/auxiliary caches and
accepted library seeds remain useful for avoiding repeated compilation; there
is no general automatic CP2K cache eviction yet.

## Manual historical cleanup

The read-only planner stats the direct build files without walking expanded
source trees. It reports both apparent and allocated bytes; sparse ext3 sizes
must not be mistaken for actual space consumed.

```bash
python3 controller/storage_retention.py plan /absolute/path/cleanup-plan.json
```

Only standalone MD runs with matching request/status identities, a sealed SIF,
a successful build, and status at least 24 hours old become cleanup candidates.
DeePMD additionally requires scientific success. A `.keep-build` file in a run
directory excludes it. Unrecognized, failed, recent and incomplete runs are
listed separately for review.

```bash
python3 controller/storage_retention.py check /absolute/path/cleanup-plan.json --sha256 REVIEWED_SHA256
python3 controller/storage_retention.py apply /absolute/path/cleanup-plan.json --sha256 REVIEWED_SHA256
```

`check` is read-only, including on a busy cluster. `apply` repeats the entire
check and requires the invoking user's Slurm queue to be empty. It checks
successful scheduler accounting, retained SIF hashes, request/status hashes and
each scratch file's device/inode/size/timestamps/mode before deletion. It only
unlinks the listed `work.ext3` and `final.squashfs`, through directory handles
that reject symlink ancestors. Run cleanup between workflow jobs. If a check
fails or a partial cleanup is interrupted, generate and review a fresh plan.

The prepared November 10 package is on SAI at:

```text
/org/org-open-sai/proj-sai-hpc-software/maintenance/storage-20261110/
  REVIEW.md
  cleanup-plan.json
  cleanup.sh
  storage_retention.py
```

As `stardust`, `bash cleanup.sh` checks the reviewed package without deleting;
`bash cleanup.sh --delete` performs only its listed scratch cleanup. Both the
plan and executable script are pinned by SHA256. No historical files have been
deleted by preparing this package.

The first plan selects 24 files from 12 successful November 8 builds, totaling
143.60 GiB allocated. The broader three-directory inventory has 447 ext3 build
images totaling about 2.0 TiB allocated; it includes failed/recovery builds and
unrelated experiments that this cleanup deliberately does not select. The
60 standard beta generations inspected contain about 54.19 GiB of public image
copies. Legacy generations with shared or external images are reported as
requiring separate review; the figure is not a total for every historical SIF.

## Proposed SIF retention policy

The planner proposes retaining at least three distinct successful source/recipe
build sets per application branch and at least seven days. Current beta,
release/prerelease and manually pinned generations are always retained. Add
generation names to `sif-beta/storage-pins.json` (a JSON list), or create
`sif-beta/generations/GENERATION/.keep`, to pin a generation in the proposal.

This SIF policy is **a review report, not an enabled deletion timer**. Its older
generation list must still be checked for shared images, fixed module users,
running/queued calculations, acceptance provenance, original image references
and administrator handoffs before a separate SIF retirement. The manual script
above cannot delete SIFs or modulefiles. This avoids silently invalidating the
paths in existing batch scripts while addressing the largest ongoing growth:
the disposable build disks.
