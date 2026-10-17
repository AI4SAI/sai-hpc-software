# CP2K install directory correction

Both formal workflows `39054631316` and `39056762371` completed their native
dependency stages on all four targets. Seven completed application attempts
compiled and linked CP2K fully, then failed during installation of
`/workspace/build/src/libcp2k.so.2026.2` into read-only `/lib64`.
The corrected-runtime 8V100V0 application was still running at the first audit.
No formal SIF had been produced.
[Original logs, accounting, identities and preserved overlays](failed-builds.json).

The pinned CP2K source declares `CMAKE_INSTALL_LIBDIR` as `CACHE PATH`.
An untyped command-line value `-DCMAKE_INSTALL_LIBDIR=lib64` becomes absolute
relative to the configure working directory. With the container working
at `/`, the cache and generated install rules contain `/lib64`, even though
`CMAKE_INSTALL_PREFIX` is correct. The same error affects CMake exports.

The recipe now uses `-DCMAKE_INSTALL_LIBDIR:STRING=lib64`. The feature contract
also rejects absolute, missing or unexpected library directories before the
long compilation begins. Reconfiguring regenerates library and export install
rules together; merely changing `cmake --install --prefix` would leave absolute
rules intact. No system `/lib64` write or feature removal is required.

A real CMake regression configures a shared library with the same upstream
cache convention and actual production options, installs it under a private
prefix, then builds and runs a consumer through its installed CMake export.
The old untyped option reproduces `/lib64` at configure time and is rejected
without attempting an installation there.

Recovery diagnostic `1837454` completed `0:0` in 18:52 on `4v100n09`.
Reconfiguration, installation, export and verification of the final SIF passed
on a copy of the completed 4V100 application image. Of 1,487 object files,
1,486 retained both their SHA256 and mtime; only the version-information object
was recompiled before relinking. All 1,643 CMake installation entries are now
under the delivery prefix. The original failed image's full SHA256 stayed
unchanged. [Recovery evidence and raw-file hashes](recovery-sif.json).

The 187,449,344-byte diagnostic SIF is retained at
`/home/stardust/sai-hpc-software/runs/cp2k-install-libdir-recovery-20261018-a/diagnostic.sif`,
with SHA256 `4ab1a5c534879236a45345c607addc48b901aedb030aa001ebf7186b6765bead`.
Verification used the SIF without the build overlay: version 2026.2,
source `67b5da876d`, GCC 14.3.0, all required features, 47 ELF files and no
missing `ldd` dependencies. PBE OT executed from the same SIF, converged in
46 SCF steps and exited normally, with energy `-34.446821294788720 Ha`.
The log reports one GPU and nonzero GPU grid work. Input data came from the
pinned smoke fixture; installed data, multi-GPU mapping and comparative
performance were not tested by this smoke.

This is a usable diagnostic SIF with its original source identity. There is
no `artifact.path` or publication record, and it is not a formal artifact of
the corrected recipe. Formal delivery and scientific/performance acceptance
remain pending.

Production commit `c3b65b97f9d95ecb29a453713dfe292af20f6d39` passed
[CI 39078979787](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39078979787):
735 tests, 733 passed and two optional skips, in 39.123 seconds.
The three real installation/contract tests and 18 CP2K contract tests also
passed locally. Fresh four-target release workflow
[39078998459](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39078998459)
uses this commit and recipe
`b59c9f26a1846d5a3863c04cefc0f7b746b3078bc48cb0a8138d67084d28e150`.
At 07:55 Asia/Shanghai, all four dependency jobs were running:
16V100 `1837489` on `16v100n02`, 4V100 `1837488` on `4v100n12`,
8V100V0 `1837487` on `8v100v0n03` and DSPRHBM `1837486` on `d9470n02`.
The live observer is retained in the home directory at
`diagnostics/cp2k-release-20261016/install-layout-fix/formal-progress.json`.
[Actual requests, scripts and frozen identities](formal-start.json).
