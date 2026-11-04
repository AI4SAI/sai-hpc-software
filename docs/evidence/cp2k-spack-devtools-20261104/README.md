# CP2K shared dependency handoff, 2026-11-04

The combined administrator package for **16V100, 4V100, 8V100V0 and DSPRHBM**
is ready at:

```text
/home/stardust/sai-hpc-software/deployment/cp2k-spack-devtools-20261104
```

It contains four dependency SIFs totaling 515,842,048 bytes (about 492 MiB),
39 package variants and 48 files. The handoff preflight passed, and its
ancestor directories and file modes were checked for administrator access.
Builds, caches and expanded preparation data remain in the project directory.

An administrator can install all four partitions with:

```bash
cd /home/stardust/sai-hpc-software/deployment/cp2k-spack-devtools-20261104
bash install.sh --check
bash install.sh --install
bash install.sh --verify
```

The libraries were built inside containers at their final versioned
`/opt/devtools` prefixes. Installation preserves headers, Fortran modules,
static/shared libraries, standard tools/data and CMake/pkg-config metadata.
Spack source/build trees, build logs and custom acceptance tests are excluded
from the public installation.

| Partition | Original build job | Successful SIF verification job | Dynamic ELF checks |
| --- | --- | --- | ---: |
| 16V100 | 2170271 | 2173888 | 52 |
| 4V100 | 2170272 | 2173240 | 52 |
| 8V100V0 | 2170273 | 2173939 | 52 |
| DSPRHBM | 2170274 | 2193082 | 51 |

Each SIF passed the listed ELF checks, a two-rank MPI/Fortran HDF5 numerical
write/read test, Libint Fortran module import and PLUMED verification. The GPU
partitions have ten package variants each; CPU has nine because its dependency
graph does not require the GPU Tiled-MM library.

Original GPU builds exported clean SIFs but failed the old HDF5 consumer link
command, which omitted the installed Fortran-to-C bridge archive. Fresh
verification jobs used the corrected link command and byte-identical SIFs;
the original failed allocations remain recorded.

[full-handoff.json](full-handoff.json) records the final image identities,
accounting, lifecycle evidence, preflight and public file access checks.
[install-layout.md](install-layout.md) lists all 39 exact installation prefixes.
[gpu-handoff.json](gpu-handoff.json) retains the earlier GPU-only handoff record.

DSPRHBM completed its dependency installation but its original read-only export
hit the stock runtime's driver-exit deadline. Recovery job `2193082` on
`d9470n18` completed in **10m46s**, reusing the finished libraries without a
rebuild. The recovery copied and sealed the original overlay, verified its
SHA-256 before and after export, confirmed attempted writes fail with `EROFS`,
and completed normal unmount and natural driver exit. The original job remains
`FAILED 74:0`. See [CPU recovery evidence](cpu-reexport/README.md).

Both writable and read-only image overlays now use workflow-owned lifecycle
management. Implementation `6f405fa` passed the full 1,204-test suite with two
skips ([CI run 41820258383](https://github.com/AI4SAI/sai-hpc-software/actions/runs/41820258383)).

An extraction attempt preceding the successful GPU handoff encountered an
`unsquashfs` timestamp interruption. Its evidence is retained; a fresh full
extraction passed with unchanged images and controller.

The workflow has not performed physical `/opt` installation. Existing matching
prefixes are checked and skipped by the administrator installer; the observation
records any prefixes already present. Full physical verification and CP2K
scientific acceptance remain separate gates. The shared-provider registry is
still empty. After administrator installation,
the generated `site-spack-candidate.json` must be validated through an isolated
CP2K build and scientific tests before enabling reuse in daily builds.

The [CPU I/O investigation](cpu-io-diagnosis/README.md) records the later
Libint completion and a two-node read comparison. It reproduced long Lustre
read delays on `d9470n01` without containers or FUSE. The CPU dependency
installation completed at 10:05:22 UTC. The subsequent accepted recovery and
combined handoff are recorded above.
