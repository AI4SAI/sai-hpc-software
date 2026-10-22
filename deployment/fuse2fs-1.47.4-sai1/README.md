# Optional public fuse2fs 1.47.4-sai1 for Apptainer 1.4.4

SAI already provides Apptainer 1.4.4, including its private `fuse2fs 1.47.3`
executable at `/opt/devtools/apptainer/1.4.4/libexec/apptainer/bin/fuse2fs`.
fuse2fs mounts ext filesystem images for Apptainer; it is an executable helper,
not a scientific library. This package adds a separately selectable helper for
the existing Apptainer installation.

The build revision is **1.47.4-sai1**; the administrator chose the shorter public
module name **fuse2fs/1.47.4-sai**. The installation prefix and module help still
identify the pinned build revision. Renaming only the module file does not
change the selected driver or its configuration. The `sai1` suffix identifies our first
site package: upstream commit `45651efd6db7da8738bc9d8d1f17e99259cabfe4`
includes I/O fixes after v1.47.4, plus our OFD/read-only patches. It is not the
unmodified v1.47.4 release. The binary still reports `1.47.4`; `release.json`
records the package version, exact source revision and binary checksum.

This package publishes the exact driver already used by accepted CP2K and
LAMMPS builds. It avoids unnecessary ext-image synchronization on Lustre,
preserves Apptainer's inherited OFD lock and opens explicitly read-only images
with an actual read-only backing descriptor. Explicit fsync and clean unmount
synchronization remain enabled.

`saiuser01` belongs to `sai-partner`. Both `/opt/devtools` and
`/opt/modules/modulefiles/devtools` are group-writable (`0775`), and the
prepared source package is readable by that account. `install.sh --check`
evaluates all ancestor/source/target POSIX ACLs for `saiuser01`, without
impersonating the account. The current `stardust` connection cannot write
these `/opt` parents; the administrator runs the installation below.

[Public installation acceptance](../../docs/evidence/fuse2fs-installed-20261022/README.md)
passed all 13 driver/module checks in DSPRHBM job `1900053`, using the actual
`/opt` files and module as account `stardust`, after installation by
`saiuser01`. Seven installer tests also passed on the site's home filesystem.
The earlier [pre-install qualification](../../docs/evidence/fuse2fs-versioned-20261022/README.md)
retains its original version name and paths.

## Installation

On the cluster, as `saiuser01`:

```bash
cd /home/stardust/sai-hpc-software/deployment/fuse2fs-1.47.4-sai1
bash install.sh --check
bash install.sh --install
bash install.sh --verify
```

The new prefix is `/opt/devtools/fuse2fs/1.47.4-sai1/`, containing
only four files (about 1.7 MiB total):

```text
bin/fuse2fs
etc/apptainer.conf
share/man/man1/fuse2fs.1
share/licenses/e2fsprogs/NOTICE
```

The module is
`/opt/modules/modulefiles/devtools/fuse2fs/1.47.4-sai`.
No runtime libraries need copying: this binary links the site OS's libfuse3,
libblkid, libuuid and libc. It requires neither GCC nor CUDA at runtime.
The installer checks exact contents/modes, stages in the destination parent
and publishes without replacing an existing path. Repeating installation
verifies an identical release. A differing version stops the operation.
An interrupted staging directory is retained for inspection, never treated
as installed. Tests, source code, receipts and logs stay in the home project.

## Use

Load both modules in one command to enable the optimized helper. Loading only
`apptainer/1.4.4` selects its bundled fuse2fs 1.47.3. The same Apptainer binary
is used in both cases.

```bash
module use /opt/modules/modulefiles/devtools
module --ignore_cache load apptainer/1.4.4 fuse2fs/1.47.4-sai
fuse2fs -V
# Keep host build, cache and scratch storage in home:
export TMPDIR="$HOME/.cache/apptainer/tmp"
export APPTAINER_TMPDIR="$TMPDIR"
export APPTAINER_CACHEDIR="$HOME/.cache/apptainer/cache"
mkdir -p "$TMPDIR" "$APPTAINER_CACHEDIR"
chmod 700 "$TMPDIR" "$APPTAINER_CACHEDIR"
# Run your usual apptainer exec command with its home-backed writable overlay.
# Bind a directory in home to container /tmp when the application uses /tmp.
```

The module uses Apptainer's native `APPTAINER_CONFIG_FILE` setting. Its
standard config copies every site setting except `binary path`, where the
new helper directory comes before `$PATH`. PATH alone cannot override
Apptainer's bundled helper. No shell alias or custom executable wrapper is
needed. The config is administrator-owned, shared and read-only; it contains
no user-specific paths, scratch or mutable state, so a per-user generated
copy is unnecessary for this public entry. Existing workflow controllers
with an explicit `--config` continue to use their own pinned configuration.

New ABACUS and GPUMD build jobs load both modules inside their batch scripts,
including the writable build/export and read-only overlay extraction phases.
The jobs use `--export=NIL`, so loading a module only in the submission shell
would not select it inside the allocation. CP2K and the DeePMD/LAMMPS workflows
already select the same qualified driver bytes through their pinned per-run
configuration. New controller revisions receive new recipe identities;
existing frozen controllers retain their recorded configuration.

Loading verifies the binary, selected config and original site config hashes.
If the site config changes, the module fails with a request to regenerate and
revalidate this version. The checksum and source commit identify the build;
its upstream version banner still says `1.47.4`, although the two performance
fixes are later commits. `module unload fuse2fs/1.47.4-sai` restores
the previous config environment. The original Apptainer installation and
site defaults are untouched. Native CP2K/LAMMPS and final read-only SIF
execution do not require this module.

## Filesystem qualification

`check.py` reuses the frozen 12-stage driver probes: write/rename/32-unit
parallel compilation, explicit fsync traces, filesystem checks, full-image
hashes across read-only mounts at modes 0600/0400, nonzero container exit,
competing writer rejection and lock release. An additional mount uses only
the module's environment setting and confirms Apptainer selects this helper;
module unload must restore the previous setting. Every image/cache/log is
under the caller's home. `--stage` uses a temporary home installation/config
for pre-install qualification and never claims the public prefix is installed.

After installation, from the `stardust` account:

```bash
package=/home/stardust/sai-hpc-software/deployment/fuse2fs-1.47.4-sai1
result="$HOME/sai-hpc-software/diagnostics/fuse2fs-public-installed-$(date +%Y%m%dT%H%M%S)"
sbatch --partition=DSPRHBM --qos=rush-cpu --cpus-per-task=8 --mem=8G \
  --output="$HOME/sai-hpc-software/diagnostics/fuse2fs-public-%j.log" \
  "$package/check.sbatch" "$package" "$result"
```

The summary is `$result/result.json`. The post-install run must succeed before
claiming that the actual public deployment is accepted. Existing scientific
build evidence and pre-install tests do not replace that check. Broader
cross-account/partition and abrupt-termination recovery qualification remains
tracked in [issue #12](https://github.com/AI4SAI/sai-hpc-software/issues/12).

Two independent fixes have also been submitted upstream after review:
[norecovery read-only handling](https://github.com/tytso/e2fsprogs/pull/296) and
[inherited image locks](https://github.com/tytso/e2fsprogs/pull/297). Their
[candidate validation record](../../docs/evidence/fuse2fs-upstream-review-20261022/README.md)
includes independent/combined regression tests and unpatched negative controls
on Lustre and private tmpfs. These candidates have not replaced this installed
package. The current package is qualified for the workflow's home-backed
Lustre images; making it the site-wide default should separately account for
the candidates' broader inherited-lock compatibility and journal semantics.

`prepare.py` is a maintainer-only packaging step. It verifies the pinned source
archive, extracts only its NOTICE/man text, copies the already qualified binary
and records hashes in `release.json`. It performs no compilation or host
software installation. The binary's original container build inputs and
patches are in `docs/evidence/fuse2fs-{efficiency,readonly}-20261017/`.
