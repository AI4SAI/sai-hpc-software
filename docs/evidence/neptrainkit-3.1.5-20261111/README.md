# NepTrainKit 3.1.5 evaluation — November 11

The user confirmed NepTrainKit as the requested application. Its `nepkit` and
`NepTrainKit` commands are declared in upstream `pyproject.toml` and both start
the Qt desktop application. The latest published release on November 11 is
v3.1.5 (October 15), source commit
`578159e74cc23ae682787593b6aec3997b590fca`.

The upstream source archive used here has SHA256
`cfc46e94516d98c4b45135fcdfe569e2a6b6c2ee6a3fd6545f1c8363bc6864e1`.
The separately versioned Python inference dependency is
`nep-adapters==1.0.5`, the latest NEPAdapters release as of this preparation.

The application has optional preinstallation value for large dataset
preparation/inspection near cluster storage. Interactive use requires a remote
desktop/X11 display; it is not an MPI training or MD executable. Existing
GPUMDkit at `/opt/apps/GPUMDkit/main-20260207` offers command-line workflow
helpers and is complementary. Existing LAMMPS plugins and their dependencies
are not modified or used by this new environment.

Build job 2374978 compiled and installed the application on 16v100n01 in the
project account. The first test collection stopped because the independent
test environment lacked `dpdata`; the installed application itself passed its
dependency and native-helper checks. Its frozen inputs, logs and output are under:

```text
/org/org-open-sai/proj-sai-hpc-software/experimental/neptrainkit/runs/release-3.1.5-20261111-202436
```

The [CPU wheel inspection](cpu-isa.json) records the exact upstream wheel and
CPU ELF hashes. No YMM/ZMM registers or VEX/EVEX mnemonics were found; upstream
`engines/cpu/CMakeLists.txt` defaults `NEP_ADAPTERS_CPU_ENABLE_NATIVE_ARCH` to
OFF, and the release wheel recipe does not enable it. NepTrainKit's own native
helpers explicitly use baseline `-march=x86-64 -mtune=generic`. The build is
shared across compatible partitions; no AVX2/AVX-512 variant is needed.

The compiled environment was then reused read-only for qualification, without
rebuilding or altering the installed libraries. Final SIF qualification
**2375174 completed 0:0 in 3m29s** on 16v100n02. The 45 upstream calculator and
structure tests plus six subtests passed. CPU/CUDA prediction and real main
window startup/clean shutdown passed both before packing and from the final SIF.
See [the SIF acceptance](acceptance.json) and [upstream JUnit results](upstream-tests.xml).

```text
SIF:
/org/org-open-sai/proj-sai-hpc-software/experimental/neptrainkit/runs/qualification-3.1.5-20261111-203608/neptrainkit-3.1.5.sif
SHA256: 59549f1d89878a1f262f7090b60a5a5e73fff7b2c7e2b523a5e1e2e75e72903b
Bytes: 439329873 (419 MiB)
```

CPU predictions were compared to upstream fixtures using absolute tolerances
5e-4 for energy/virial and 5e-6 for forces. CUDA was explicitly selected and
reported a V100 SM70 allocation. CUDA/CPU energy, force, virial and descriptor
checks use **atol 2e-6 plus rtol 1e-6**, rather than an absolute-only threshold.
The largest SIF force difference was 2.1852e-6 and passed that combined bound.

Qt's offscreen plugin cannot create the OpenGL contexts required for drawing
the 3D widgets. The tests establish application startup and clean shutdown;
interactive OpenGL rendering and a real remote-desktop/X11 session are **not
qualified**. Users need a working graphical display to use the desktop GUI.

## Native administrator handoff

The complete readable package is:

```text
/home/stardust/sai-hpc-software/deployment/neptrainkit-3.1.5
```

`install.sh --check` passed. A real trial install under umask 077 passed,
including explicit 0755 directories, 0644/0755 payload files and a 0644 module.
[The log](stage-install.log) records successful extraction and runtime checking.
The projected standard runtime retains 18,106 regular files / 1,208,890,420 bytes;
known test trees and bytecode caches are excluded, while application data and
standard libraries/interfaces are preserved.

Final direct-host native job **2375677 completed 0:0 in 1m24s** on 16v100n04.
It used the pruned trial prefix, the final module with **no LD_LIBRARY_PATH
injection**, and no Apptainer or MPI launcher. Installed helper/Qt/backend
imports, all loader checks, CPU/reference and CUDA/CPU correctness, offscreen
main-window startup/shutdown and module unload passed. The
[native verification](native-verification.json) checks the frozen inputs,
current runtime tree, completed accounting and output hashes. This is a
`staged-native` result, separate from a later physical `/opt` acceptance.

The [Lmod preview](lmod-help.txt) has one Features-bearing whatis entry and
login-readable help. The public native destination is
`/opt/apps/neptrainkit/3.1.5` with module `neptrainkit/3.1.5`.
Administrator installation is still manual; no public `/opt` files were written.

Three focused installer tests cover umask 077, preserving required data and
library links while pruning tests/cache, and rejecting a retained link into an
omitted test tree. Python/shell syntax checks passed. All preparation code,
metadata and lightweight evidence are in Git; wheels, source archives, SIFs
and the full installed tree stay in cluster storage.

Sources:

- https://github.com/aboys-cb/NepTrainKit/releases/tag/v3.1.5
- https://github.com/aboys-cb/NepTrainKit/blob/v3.1.5/pyproject.toml
- https://github.com/aboys-cb/NepTrainKit/blob/v3.1.5/README.md
- https://github.com/MagTheoryLab/NEPAdapters/releases/tag/v1.0.5
- https://github.com/MagTheoryLab/NEPAdapters#project-origin-and-acknowledgements
