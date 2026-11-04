# CP2K shared dependency handoff, 2026-11-04

The administrator package for **16V100, 4V100 and 8V100V0** is ready at:

```text
/home/stardust/sai-hpc-software/deployment/cp2k-spack-devtools-20261104-gpu
```

It contains three dependency SIFs totaling 387,178,496 bytes (about 369 MiB),
30 package variants and 38 files. The handoff preflight passed, and its
ancestor directories and file modes were checked for administrator access.
Builds, caches and expanded preparation data remain in the project directory.

An administrator can install the GPU variants with:

```bash
cd /home/stardust/sai-hpc-software/deployment/cp2k-spack-devtools-20261104-gpu
bash install.sh --check
bash install.sh --install
bash install.sh --verify
```

The libraries were built inside containers at their final versioned
`/opt/devtools` prefixes. Installation preserves headers, Fortran modules,
static/shared libraries, standard tools/data and CMake/pkg-config metadata.
Spack source/build trees, build logs and custom acceptance tests are excluded
from the public installation.

| Partition | Original build job | Successful SIF verification job |
| --- | --- | --- |
| 16V100 | 2170271 | 2173888 |
| 4V100 | 2170272 | 2173240 |
| 8V100V0 | 2170273 | 2173939 |

Each SIF passed 52 ELF linkage checks, a two-rank MPI/Fortran HDF5 numerical
write/read test, Libint Fortran module import and PLUMED verification.
Original builds exported clean SIFs but failed the old HDF5 consumer link
command, which omitted the installed Fortran-to-C bridge archive. Fresh
verification jobs used the corrected link command and byte-identical SIFs;
the original failed allocations remain recorded.

[gpu-handoff.json](gpu-handoff.json) records source hashes, job identities,
the frozen controller commit and the handoff observation. Full controller CI
at `5989ded` completed 1,200 tests successfully, including two skipped tests
([run 41767639226](https://github.com/AI4SAI/sai-hpc-software/actions/runs/41767639226)).

At the recorded observation, DSPRHBM job `2170274` was still running; it has
not been accepted. A bounded observer will verify that image after a clean
build and prepare the full four-partition handoff when all checks pass.
An extraction attempt preceding the successful GPU handoff encountered an
`unsquashfs` timestamp interruption. Its evidence is retained; a fresh full
extraction passed with unchanged images and controller.

Physical `/opt` installation and CP2K scientific acceptance are pending.
The shared-provider registry remains empty. After administrator installation,
the generated `site-spack-candidate.json` must be validated through an isolated
CP2K build and scientific tests before enabling reuse in daily builds.

The [CPU I/O investigation](cpu-io-diagnosis/README.md) records the later
Libint completion and a two-node read comparison. It reproduced long Lustre
read delays on `d9470n01` without containers or FUSE. The CPU dependency
installation completed at 10:05:22 UTC; SIF acceptance is a separate gate.
