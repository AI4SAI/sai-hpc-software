# Independent Apptainer 1.4.4 installation

**Automated builds no longer require this installation.** The workflow's
[`managed_overlay.py`](../../controller/managed_overlay.py) uses the existing
Apptainer 1.4.4 and pinned fuse2fs, owns the writable mount, and waits for its
natural exit before returning. It requires no public installation or AppArmor
change. The package below remains an optional runtime patch, with its original
native-launch prerequisite; it has not been activated by the workflow change.

Install into **`/opt/devtools/apptainer-sai/1.4.4`**. This is a sibling of
`/opt/devtools/apptainer`, whose existing directories are not writable by
`saiuser01`. Live permission checks confirm that `saiuser01` can create the new
parent under `/opt/devtools` and write the new module under
`/opt/modules/modulefiles/devtools/apptainer`.

```bash
cd /home/stardust/sai-hpc-software/deployment/apptainer-1.4.4-sai
bash install.sh
bash install.sh --install
bash install.sh --verify
```

These commands require no sudo. They never write the original installation.
The first command verifies the payload and reports permissions. Installation
checks every file, stages the complete distribution under the new parent and
renames it into place. An existing different runtime is not overwritten.
`--verify` checks installed files; it does **not** claim containers can start.
No module is published by `--install`.

The package is the checksum-pinned site distribution, including its CLI,
configuration, FUSE/squashfs helpers and standard manuals. Only the `starter`
binary differs: it gives normally unmounted image drivers up to 30 seconds to
finish, instead of one second. Explicit termination and SIGTERM-to-SIGKILL
remain one second. Prompt exits add no waiting time. There is no setuid helper.
The only extra installed metadata is `share/sai/driver-stop-grace.json`.
Tests, build trees and probe results stay outside `/opt`.

The existing `apptainer/1.4.4` module and executable keep their original behavior.
The new module is **`apptainer/1.4.4-sai`**. Upstream `apptainer --version` still
reports 1.4.4; the module and receipt identify the SAI patch. `payload-files.json`
records every delivered file/link/mode; `manifest.json` pins the archive and
inventory. The archive is supplied in `payload/` outside Git (about 90 MiB,
188 MiB expanded).

## The remaining system prerequisite: AppArmor

The cluster enables `kernel.apparmor_restrict_unprivileged_userns=1`. Its current
profile grants user namespaces only to the executables at the original 1.2.4
and 1.4.4 paths. A new folder solves the installation permission problem, but
**does not grant permission to run containers from that folder**. `--version`
can succeed while `exec` fails with `Could not write info to setgroups:
Permission denied`.

A system administrator must deploy the supplied additional profile to the
login and computation nodes using the cluster's configuration management.
Equivalent commands, executed as root on each relevant node, are:

```bash
install -m 0644 apparmor.apptainer-sai /etc/apparmor.d/apptainer-sai-1.4.4
apparmor_parser -r /etc/apparmor.d/apptainer-sai-1.4.4
```

The profile grants `userns` to exactly
`/opt/devtools/apptainer-sai/1.4.4/bin/apptainer`, matching the existing profile's
policy. It does not change the existing profile or disable the kernel's
restriction. The installer does not write system policy, and neither SSH
account currently has write access to `/etc/apparmor.d`.

After policy deployment, run native overlay qualification as the project user
`stardust`, from the new path:

```bash
sbatch --partition=DSPRHBM --qos=rush-cpu --cpus-per-task=4 --mem=8G verify-install.sbatch
sbatch --partition=4V100 --qos=rush-1o2gpu --gpus-per-node=1 verify-install.sbatch
sbatch --partition=8V100V0 --qos=rush-1o2gpu --gpus-per-node=1 verify-install.sbatch
sbatch --partition=16V100 --qos=flood-1o2gpu --gpus-per-node=1 verify-install.sbatch
```

Check accounting and each `result.json` before publishing the module. All four
cases must pass: two small overlays, a sparse 32 GiB overlay and a two-second
fsync delay, with clean teardown, offline fsck and byte-for-byte read-only
remount verification. Logs and overlays are retained on project storage.
The earlier private namespace qualification is evidence for the patched
runtime, not proof that a new public executable path is permitted.

Then, as `saiuser01` on a node where the new profile is active:

```bash
bash install.sh --activate --work "$HOME/apptainer-sai-activation-20261103"
module --ignore_cache load apptainer/1.4.4-sai
```

`saiuser01` cannot traverse `/org/org-open-sai`. The short activation probe
therefore uses that account's home and the checksum-pinned `payload/native-check.sif`
in this readable handoff. This probe creates only its small log/cache directory;
compilation and full overlay qualification remain on project storage as
`stardust`. No project access permissions need changing.
Use a fresh `--work` path for each attempt. Activation performs a direct native
container launch and writes the new module only if it succeeds. A successful
launch proves the current node only; it does not configure other nodes.
The module sets `SAI_HPC_APPTAINER`. Guarded ABACUS/CP2K/DeePMD/LAMMPS build jobs
select this module once published, verify the new CLI/starter hashes, and record
the selected executable in their provenance. Invalid explicit selections fail;
they do not fall back silently. Forced driver termination still invalidates an
overlay. Existing jobs keep their submitted scripts and runtime selection.

To withdraw the new module while retaining the installed bytes and evidence:

```bash
bash install.sh --rollback
```

Future guarded jobs then load the original runtime. This can reintroduce its
one-second teardown failure, so withdrawing the module is not a build fix.

## Rebuilding

`build.sh` rebuilds the patched helper from the official vendored 1.4.4 archive,
using Go 1.25.4 and project storage, without writing `/opt`. The stock build prefix
is intentional: the existing qualified helper and non-setuid CLI support runtime
relocation. Requalify rebuilt bytes before changing the manifest.

`package.py SITE_PREFIX QUALIFIED_STARTER NEW_OUTPUT_DIRECTORY` prepares a
complete relocatable archive and file inventory from the site distribution.
It preserves required tools and standard data, rejects external links and
privileged files, and never changes its inputs. Repackaging changes the archive
hash; update its pins only after validation.
