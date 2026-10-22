# CP2K 2026.2 TBLITE compatibility

The full compile diagnostic `1833253` failed at
`src/tblite_interface.F:479`: CP2K v2026.2 writes
`tb%calc%mixer_damping`, while the previously hard-coded TBLITE 0.7.0 API
uses `mixer_input%damping`. Passing CMake configuration did not establish
source compatibility, and that diagnostic produced no verified executable.

The fixed CP2K release source
[`67b5da876dd6a76b8b021d5a04d1c81ba79a4c50`](https://github.com/cp2k/cp2k/blob/67b5da876dd6a76b8b021d5a04d1c81ba79a4c50/tools/toolchain/scripts/stage8/install_tblite.sh)
pins the complete `tblite-0.6.0.tar.xz` archive and two gradient fixes.
The development API migration occurred later in
[`88ac9fd`](https://github.com/cp2k/cp2k/commit/88ac9fdbaa27d593220004fed8cc76b291627b30).
Changing only the release's member access would also bypass upstream's
dependency and scientific regression changes.

The controller now reads `tblite_ver` from the pinned source without executing
the upstream installer. It rejects missing, ambiguous and unsupported versions.
For 0.6.0 it verifies the official complete archive, applies the two release
patches to its bundled subprojects, and builds those libraries with GCC 14.3.0
and native flags. Development's 0.7.0 source chain remains selected only when
the source declares that version. The CP2K source itself is unchanged.

| Input | SHA256 |
|---|---|
| `tblite-0.6.0.tar.xz` | `372281aedb89234168d00eb691addb303197a9462a9c55d145c835f2cf5e8b42` |
| `simple-dftd3-1.4.0-gradient-fixes.patch` | `6a20b4622821018954a204a33dcb2128ddfaec04a37f3b4a625a71058ba2a40b` |
| `dftd4-4.2.0-gradient-fixes.patch` | `5335cb7d02a8c3141f28967ef61418f425fb1e638d51fe75618b700321d9087a` |

The complete archive contains toml-f 0.5.0, mctc-lib 0.5.1, multicharge 0.5.0,
s-dftd3 1.4.0, mstore 0.3.0 and dftd4 4.2.0. The patches apply with `-p1` in
`subprojects/s-dftd3` and `subprojects/dftd4`; their contexts differ from the
standalone D3/D4 GitHub tag archives. Archive, patch and toolchain script
checksums are retained in the installed dependency provenance file.

The runner downloads and verifies the archive before submission and uploads
it with the existing wignernj update. Compute nodes receive these inputs through
the read-only `/input/updates` bind. All host files remain in project home.

Diagnostic `1833463` verified and applied both patches, then exposed the bundled
mctc-lib export's missing OpenMP target when configuring mstore. Diagnostic
`1833582` established that this DFT-D4 bundle discovers mstore unconditionally,
even with tests disabled. The corrected recipe supplies the OpenMP imported
target through CMake's project include hook and retains all six subprojects,
without editing their source or disabling parallelism. The hook's checksum is
also retained. Both failed diagnostics remain available.

Diagnostic `1833642` uses the old failed application image read-only, the
successful COSMA diagnostic libraries, and a new CP2K build tree plus TBLITE
prefix under `runs/cp2k-tblite-release-preflight-20261018-c`. Its final image hash
matches diagnostic `1833463`'s verified hash. All CP2K source objects compiled,
including the formerly incompatible TBLITE interface. Executable linking then
failed because static HDF5 discovery omitted its Fortran C stub archive.
[The follow-up repair and retained failure](../cp2k-hdf5-static-20261018/README.md)
track that separate blocker. This diagnostic does not establish a release SIF,
science/performance acceptance or publication.

Local regression coverage includes rejection of unknown/missing source versions,
both archive downloads and cache reuse, corrupted-cache replacement, failed
checksum rejection, and upload ordering before dependency submission.

Commit `b9ee5fadd0de708a7fa528f4a3a3f0786532ac8c` is synchronized to GitHub and
SAI's development checkout. [CI 39051265416](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39051265416)
ran all 725 tests in 28.142 seconds: 723 passed, two optional skips. The local
CP2K-only run passed 111 tests and could not run two existing adapter tests
because CMake is absent locally; both are covered by the successful CI run.
[Validation record](validation.json).

[Retained failed attempts](failures.json) include the exact requests, input
hashes, error excerpts and unchanged image hashes. Old application `1833599`
was deliberately cancelled after verifying its immutable 0.7.0 recipe and
exact Slurm handle; its successful dependency checkpoint is preserved.
