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

A separate recovery diagnostic `1837454` is running on a copy of the completed
4V100 application image, in the home directory
`runs/cp2k-install-libdir-recovery-20261018-a`. It will check reconfiguration,
installation, export, a diagnostic SIF and PBE execution. The original failed
image and controller remain unchanged. A diagnostic SIF will not be relabelled
as a new-recipe formal build.

Production commit `c3b65b97f9d95ecb29a453713dfe292af20f6d39` passed
[CI 39078979787](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39078979787):
735 tests, 733 passed and two optional skips, in 39.123 seconds.
The three real installation/contract tests and 18 CP2K contract tests also
passed locally. Fresh four-target release workflow
[39078998459](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39078998459)
uses this commit and recipe
`b59c9f26a1846d5a3863c04cefc0f7b746b3078bc48cb0a8138d67084d28e150`.
Final SIF and scientific/performance acceptance remain pending.
At 07:32 Asia/Shanghai, 16V100 `1837489`, 4V100 `1837488` and DSPRHBM
`1837486` were running; 8V100V0 `1837487` was pending.
[Actual requests, scripts and frozen identities](formal-start.json).
