# Apptainer 1.4.4: allow image drivers to finish their final flush

The installed Apptainer stops a normally unmounted FUSE driver after one second,
then sends SIGKILL one second later. On SAI shared storage, fuse2fs can still be
flushing clean-state metadata at that point, even after the payload's fsync.
This patch gives normal teardown up to 30 seconds. Explicit termination and the
SIGTERM-to-SIGKILL timeout remain one second. A driver that exits promptly adds
no waiting time.

The administrator applies this to the **existing** non-setuid
`/opt/devtools/apptainer/1.4.4` installation:

```bash
cd /home/stardust/sai-hpc-software/deployment/apptainer-1.4.4-sai
bash install.sh             # read-only checks and proposed change
sudo bash install.sh --install
bash install.sh --verify
```

Live checks as `saiuser01` can read the full handoff and verify all checksums,
but that account cannot write the existing starter or receipt directory.
Installation therefore requires an administrator with write access (using
`sudo` where available). The preflight reports this before changing any file.

Only `libexec/apptainer/bin/starter` is replaced, using an atomic rename. The
installer checks the original CLI/helper hashes and the qualified replacement,
keeps a checksum-verified backup, and writes
`share/sai/driver-stop-grace.json`. It does not change the CLI, modules, site
configuration, setuid permissions, or AppArmor profiles. This preserves the
existing permitted CLI path. Loading `apptainer/1.4.4` therefore uses the fix
without a new module name; `apptainer --version` still reports upstream 1.4.4.
Existing processes keep their original executable; new invocations use the fix.

The handoff contains the checksum-pinned `payload/starter` outside Git.
`manifest.json` records the exact source archive, source commit, patch, Go
version and installed bytes. `qualification.json` records the accepted private
mount-namespace tests with the original installed CLI and this replacement
starter. Public installation and a subsequent direct Slurm run remain distinct
acceptance steps. The workflow still rejects forced driver termination and
preserves failed overlays.

To restore the original helper:

```bash
sudo bash install.sh --rollback
```

Rollback restores the original one-second behavior, so the daily teardown
failure can recur. It preserves the backup and requires the known original or
patched helper; an unrelated runtime upgrade is never overwritten.

Rebuild in a compute allocation, entirely on project storage:

```bash
bash build.sh /org/org-open-sai/proj-sai-hpc-software/path/apptainer-1.4.4.tar.gz \
  /org/org-open-sai/proj-sai-hpc-software/path/new-build
```

`build.sh` uses the official vendored archive, Go 1.25.4, no network dependency
resolution, no setuid helper, and a staged installation. It never writes `/opt`.
It also runs a slow-helper regression and an explicit-termination test. A
rebuild at a different source path can have a different binary hash and must be
qualified before updating the manifest.

`verify.py` is run in Slurm, with an empty project directory. It creates fresh
512 MiB and 32 GiB sparse images, explicitly synchronizes payload data, requires
clean driver teardown and offline `e2fsck -fn`, and verifies every expected file
on a separate read-only remount. A strace `-D` case delays fsync by two seconds
while preserving fuse2fs as Apptainer's direct child. All scratch, images and
logs stay under the funded project. No verification files are installed in
`/opt`.
