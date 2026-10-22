# CP2K application cleanup: retained overlay work directory

Status: the failure mechanism is reproduced on SAI in isolated job `1831861`.
The application clone repair is implemented in `cp2k_overlay_workdir.py` and
integrated into `cp2k_dependency_stage.py`. Historical and sealed images stay
unchanged. A successful production application build is still required for
workflow closure.

The clean control completed all **8192** create/unlink operations without error.
With 4096 old numeric work directories, the same workload hit its first `EISDIR`
at iteration **98** and recorded 16 such failures. The driver trace directly shows
`renameat(5, "workspace/rm-probe/file-00098", 6, "204") = -1 EISDIR`, followed by
the same error for destinations `206`, `208`, and later numeric names. Read-back
confirms that these destinations remain directories in `/work/work`. Full logs
and traces remain in the home diagnostic directory; paths, hashes, workload
results and trace excerpts are in `results.json`.

The 4V100 application job `1831311` reported `EISDIR` while unlinking
`multicharge-build/.../link.txt`. The 16V100 job `1831609` reported the same error
for two other regular generated files: `mstore-build/.../flags.make` and
`DependInfo.cmake`. OpenMP detection itself succeeded. CMake 3.31.6 calls `unlink`
and captures its errno immediately; its later recursive cleanup explains why the
reported files are no longer present in the stopped images.

Read-only `debugfs` evidence in `failed-image-readonly-evidence.json` found many
old numeric directories in `/work/work` whose timestamps precede the application
and belong to the dependency build. Examples are 4V100 `13335` and `12696`, and
16V100 `12858`; each retains `.wh..wh..opq`. Neither failed image was mounted,
written, or repaired. The recorded inode, image size and mtime stayed unchanged.

The deployed fuse-overlayfs reports version 1.15. Its upstream source has a
specific mechanism that can turn these leftovers into an `EISDIR` unlink error:

1. `get_next_wd_counter`, lines 271–276, restarts the temporary filename counter
   at `1` for every new driver process.
2. `hide_node`, lines 1114–1202, implements deletion by renaming the file to
   `/work/work/<counter>` without checking whether that name is an old directory.
   Renaming a regular file onto such a directory returns `EISDIR`.
3. Startup lines 5910–5935 call `empty_dirfd`, but ignore its result.
4. `empty_dirfd`, lines 2883–2942, deletes entries during one `readdir` traversal.
   The pinned e2fsprogs `45651efd` fuse2fs implementation uses ordinal `dirpos`
   values over currently present directory entries. Deleting entries can change
   later ordinal positions. The live reproduction confirms that startup leaves
   old work directories behind and later rename operations collide with them.
   The exact reason startup fails to remove every entry is not separately
   instrumented; even a successful return would not prove the directory is empty.

Exact source excerpts, upstream URLs and source digests are preserved beside this
file. The ext2fs source came from the pinned site tar archive, not another release.
The ordinary inode cache is updated by `ext2fs_write_inode_full`, so a stale
libext2fs inode type is not currently the strongest explanation.

`repro.py` and `job.sbatch` use only newly created 256 MiB images under
`~/sai-hpc-software/diagnostics/cp2k-release-20261016/overlay-workdir-repro-20261017-b`.
The clean case has no retained work entries; the second case starts with 4096
numeric directories containing `.wh..wh..opq`. Both run the same 8192 create/unlink
workload with the file descriptor held through unlink. Driver syscall traces
record whether `renameat` hits an existing directory. All diagnostic files and
images are retained. No historical or sealed image is an input to this probe.
The initial `-a` probe job `1831825` stopped before the workload because `mkfs -d`
preserved host ownership of the synthetic upper directory. Its image and logs are
retained; `-b` uses standard `apptainer overlay create --fakeroot` and populates
only the newly created image with a recorded `debugfs` input file.

The implemented application handoff repair cleans only the
transient work subtree of a newly created, unmounted application clone before
its first submission, keeping the dependency seal and original image untouched:

- Require an exclusively created new clone whose pre-cleanup digest matches the
  verified dependency image; do not accept arbitrary existing image paths.
- Enumerate `/work/work` using read-only `debugfs`. Accept only numeric directory
  names with regular `.wh..wh..opq` children and regular `.fuse_hidden*` files;
  reject symlinks, unexpected paths or contents for separate review.
- Generate literal `rm /work/work/N/.wh..wh..opq` and `rmdir /work/work/N`
  commands, plus individual `rm /work/work/.fuse_hidden...` commands. Keep the
  `/work/work` directory itself. Do not use inode-level `kill_file` or `clri`,
  which could affect links elsewhere in the image.
- Inspect debugfs output as well as its exit code, independently read back an
  empty work subtree and run `e2fsck -fn`. Record the derived clone digest and
  cleanup commands as application preparation evidence rather than rewriting
  the dependency seal.

The helper holds an OFD shared lock on the read-only source and an OFD exclusive
lock on the new clone. It checks inode ancestry, file types and link counts before
mutation, and runs `e2fsck -fn` before and after cleanup. Failed preparation writes
a failure receipt and prevents copying the application dependency receipt. The
`upper_directory_unchanged` field means an exact comparison of `/upper`'s
immediate directory listing; it is not a recursive payload hash. The fixture
independently reads the upper sentinel's bytes before and after the workload.

The first fix fixture, job `1832088`, removed all 8193 reviewed entries, then
stopped at readback: debugfs prints inode-zero unused slots at the start of later
directory blocks (for example `/0/000000/0/0/340/0/`). These are not live paths.
The parser now ignores only that exact zero-inode, zero-mode, zero-owner,
zero-size format. A real 400-directory ext3 regression covers this behavior;
malformed variants still fail. All failed fixture files remain in the home
diagnostic directory identified by `fix-failed-attempt-a.json`.

The final fresh-image proof **1832137** completed on **d9470n02** in **70 seconds**
with exit **0:0**. The exact production helper cleaned **8193** entries from a
new application clone of a newly created 256 MiB sealed fixture. The clone then
completed all **8192** create/open/unlink/close operations with **zero EISDIR**
and zero other errors using the current pinned driver
`46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10`.
The sealed fixture's SHA-256 and upper sentinel bytes remained unchanged, and
all three filesystem checks passed (before cleanup, after cleanup, after the
mounted workload). `fix-proof-result.json` records the exact helper digest,
image digests, receipt summary, command logs, artifact hashes and accounting.
The full receipt and logs remain at
`~/sai-hpc-software/diagnostics/cp2k-release-20261016/overlay-workdir-fix-20261017-b/`.

The final focused local checks passed **16 tests**: eight for clone preparation
and eight for dependency stage integration; commands and file hashes are in
`tests.json`. This verifies the isolated repair and handoff failure behavior,
not the remaining production CP2K builds.
