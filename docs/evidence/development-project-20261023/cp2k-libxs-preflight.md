# CP2K development LibXS preflight, 2026-10-23

The pinned CP2K master source `04b255137f7b472b07f09bec26e7bbf81c366a3a`
requires a different LibXS source from the accepted 2026.2 release. This was
found before any application allocation started; it is not an observed
application build failure or a successful scientific acceptance.

Upstream commit
[`0bfd3646c76889ab2a36ecc7290e5e3adddb3cf8`](https://github.com/cp2k/cp2k/commit/0bfd3646c76889ab2a36ecc7290e5e3adddb3cf8),
dated 2026-10-21, changed
`tools/toolchain/scripts/stage4/install_libxs.sh` from LibXS 1.0.0 to commit
`930a2c4` and deleted `libxs-1.0.0-jit-handle.patch`. The exact new declarations
in the selected CP2K source are:

```sh
libxs_ver="930a2c4"
libxs_sha256="552f9dd5af66b593d81aeb5aeb8acedba01943ae8d0c1bc036ea79f5fa591e88"
```

The previous recipe always unpacked 1.0.0 and applied the patch only when
present in the selected CP2K source. On current master this would silently
build pristine 1.0.0 again. That version calls a non-NULL `dgemm_jit` or
`sgemm_jit` with its `jitter` handle without checking whether the handle is
NULL. The verified 930a2c4 archive instead snapshots both the function and
handle and requires both to be non-NULL before the call, retaining the BLAS
fallback. No minimum LibXS version in CP2K's CMake discovery would catch this
regression during configuration.

The fix reads the exact version/checksum declarations without executing
upstream shell code. Release 2026.2 keeps pristine 1.0.0; intermediate
development sources keep their declared 1.0.0 JIT patch; the selected master
uses 930a2c4. Unknown versions, changed checksums, damaged archives, and a
declared but missing patch stop before compilation. The archive and any
applied patch remain in the private dependency provenance checksums.

The 8,584,208-byte archive was downloaded from
`https://codeload.github.com/hfp/libxs/tar.gz/930a2c4`, verified against the
upstream SHA256, and seeded once into the project content cache. See
[cp2k-libxs-source-cache.json](cp2k-libxs-source-cache.json). Future matrix
targets use the verified local cache instead of re-uploading this archive.

Validation: all 11 tests in `test_cp2k_dependencies.py` passed, including the
new version/checksum selection cases, and both shell scripts passed `bash -n`.
Other application prerequisites were checked read-only: four ELPA providers
matched every pinned file hash and symlink; GCC 14.3, CUDA 12.9 libraries and
compatibility libraries, Spglib 2.7, cuSOLVERMp, both OpenMPI/SAIBLAS variants,
and the application source archives were present. The DBCSR archive reports
2.10.0, satisfying the selected CP2K source's 2.9 CMake requirement.

## Actions pause and retained dependency jobs

[Actions run 39976482528](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39976482528)
was explicitly cancelled to prevent automatic application submission with
the superseded LibXS recipe. GitHub reports `completed/cancelled`; this does
not cancel the already submitted Slurm jobs. At 15:58:12 UTC all four original
dependency allocations were still RUNNING and no application `job.id`
existed. The exact job IDs, run IDs, and accounting are in
[cp2k-libxs-pause-status.json](cp2k-libxs-pause-status.json).

The original controller
`e29f05a67f890b612f7035120d0cd0abf503afc2` and all existing requests, prefixes,
overlays and receipts are preserved. Successful dependencies will be sealed
with that original controller. They are not accepted CP2K applications.

## Binary reuse assessment

The pinned Spack `3e19345b6e12f5ff1b874f4059622fc6a1fd804a` implements
`buildcache push` (`create` alias) and installation relocation of ELF RPATHs,
text configuration files and symlinks. Equal-length old/new application
prefixes fit its binary string replacement mechanism. This was a source
inspection, not a successful relocation experiment.

The current dependency workflow explicitly disables binary mirrors and uses
`--no-cache`. Its local Libint export repair is not part of Spack's DAG hash,
and its receipt records `binary_cache_publish_allowed: false`. Safe future
reuse therefore needs a dedicated migration cache tied to the original sealed
receipt and patch, full dependency-closure relocation checks, absence of old
runtime paths, and a newly generated receipt at the new canonical prefix.
Simply copying or relabelling the old store is invalid. This change keeps the
existing identity contract and normal native dependency build; no unchecked
cache reuse or installation-prefix override was introduced.
