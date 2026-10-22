# Explicit read-only fuse2fs validation

The previous private inherited-OFD adapter rejected Apptainer's read-only image
descriptor. A later stock-driver probe mounted successfully, but the sealed
dependency image's full SHA256 no longer matched its seal afterward. The two
accesses did not have separate before/after hashes, so the mutation cannot be
assigned conclusively to one invocation. The original seal is invalid and is
not replaced by this experiment.

Both pinned upstream `45651efd6db7da8738bc9d8d1f17e99259cabfe4` and stock
e2fsprogs `v1.47.3` set `EXT2_FLAG_RW` before `ext2fs_open2`, even with `-o ro`.
The `ro` option controls FUSE access while libext2fs may still update mount
metadata, inode access times and the superblock. `norecovery` only clears the
write flag when journal recovery is already required; `no_default_opts` only
changes default FUSE arguments. Neither option ensures immutable clean images.

`patch-fuse2fs-readonly.py` starts from the exact pristine source, applies the
previous inherited-OFD adapter, then clears `EXT2_FLAG_RW` for explicit `ro`
**before** `ext2fs_open2`. The underlying `unix_io_manager` therefore opens the
image `O_RDONLY`; the existing adapter consequently keeps a shared inherited
OFD lock. Read-only error diagnostics also leave the on-disk superblock alone.
Writable operation and explicit synchronization are unchanged. An image needing
journal recovery is still rejected on the normal explicit-RO invocation.

The job copies the original build image into a new diagnostic directory and
builds there using the previous validated writable driver. Historical drivers,
sealed images and failed application images are not modified. The probe creates
a fresh 256 MiB image, performs a 161-file compile workload and explicit fsync,
then checks the filesystem and complete image SHA256 across read-only mounts,
remounts, a nonzero container exit, and concurrent writer rejection. It tests
both mode `0600` and mode `0400` to distinguish actual read-only semantics from
file permission protection. Read-write lock exclusion is rechecked separately.

Pinned source inputs:

- Archive: `3ba72ea2ec624fcbe8c155656623c16da92e07dff4a7ffb981dcadbd9990ccf4`
- Pristine `misc/fuse2fs.c`: `ab3794d86b12a8227b4a87823349e974df06f39c42971dcf2983ddf57cf82696`
- Previous OFD-patched source: `2c299a8eb6a26620c629a40a0270543ffb9aec8cfb8abf84613246cab14b63ff`
- New patched source: `c63d1fb25510d316836952953e9adddacd3214cd91d6002bf3b16aed350c4ddc`

The probe records each command, return code, log, syscall trace and image hashes.
`collect.py` reads the retained run without mounting it and gathers small logs,
input checksums, trace hashes, Slurm accounting and the candidate binary hash.
This filesystem experiment does not resolve the prior CMake `link.txt` EISDIR
failure or establish CP2K scientific acceptance.

## Results

Job `1831878` completed `0:0` in `00:01:55` on `d9470n02` (DSPRHBM,
8 CPUs, 28 GiB). All twelve probe stages passed. The final binary SHA256 is
`46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10`;
it is installed mode `0555` under the matching project `cache/tools/fuse2fs-<sha>/bin`.
The combined patch SHA256 is
`92a2a56963874a1249b1b68be69369a9ddcaedc92cd47b8d35da72d36388600c`.

| Check | Result |
| --- | --- |
| Writable 161-file compile workload | Passed; seven successful driver `fsync` calls |
| `e2fsck -fn` after workload and final lock tests | Both passed, image hashes unchanged |
| RO mount, RO remount at mode `0400`, final RO mount | All 161 business-file SHA256 values unchanged; full image SHA256 unchanged |
| RO nonzero container exit (`/usr/bin/false`) | Expected exit `1`; full image SHA256 unchanged |
| RO holder | Shared OFD lock; exclusive POSIX and second Apptainer writers rejected |
| RW holder | Exclusive lock retained; competing writers rejected |
| Locks after unmount | Released after both holder cases |
| RO driver syscall traces | Actual inherited-descriptor `O_RDONLY` open; zero successful writes to the image |

Two failed probe attempts are retained. Job `1831731` correctly preserved the
image hash, but the original content test compared raw directory listings.
Apptainer uses fuse-overlayfs for writable operation and kernel overlay for
read-only operation on this node. The latter exposes the empty
`.wh..wh..opq` directory-opacity marker: the RO listing has 162 entries while
all 161 business files match. The corrected probe accepts only that exact
regular zero-byte control file with the empty-content SHA256, records its
stat and directory xattrs, and still rejects any missing, changed or other
extra file. This is a content verification correction; it does not weaken the
full-image hash requirement. Job `1831841` passed the two RO content checks but
stopped because the minimal base has no `/bin/false`. The final probe uses
`/usr/bin/false` from the already-bound host `/usr`.

The A scripts remain at this directory's top level, the B scripts under
`probe-b/`, and final C scripts under `probe-c/`. No binary change occurred
between A, B and C. [summary.json](summary.json) contains the passing cases,
checksums and limitations. [results-a.json](results-a.json),
[results-b.json](results-b.json), and [results-c.json](results-c.json) retain
the corresponding inputs, command logs, results and syscall-trace hashes.
The complete remote traces remain under
`~/sai-hpc-software/diagnostics/cp2k-release-20261016/fuse2fs-readonly-20261017-{a,b,c}/`.
