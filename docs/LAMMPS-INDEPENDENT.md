# Independent LAMMPS candidates

LAMMPS can use the accepted DeePMD 3.2.0 installations already in `/opt`.
Updating LAMMPS does not require rebuilding DeePMD or waiting for its moving
`master` branch. The new `lammps.yml` workflow resolves only LAMMPS's selected
channel and builds one native GPU partition per dispatch.

The installed dependency is selected from the reviewed
[`runtime-plan.json`](../deployment/lammps-30Sep2026/runtime-plan.json).
Before submission and again inside the container, all 1,165 recorded paths
for that partition are checked for content, permissions and symlink targets.
The exact dependency identity and inventory digest are recorded with the
LAMMPS candidate. Its prefix is mounted read-only. The matching DeePMD source
commit supplies only LAMMPS interface sources and scientific fixtures; neither
the Python package nor the C/C++ libraries are rebuilt or exported.

`md_build.sh lammps-installed` shares the accepted GPU/Kokkos/cuFFT, MPI,
PLUMED, package/style parity and runtime-path checks. Native-CUDA upstreams use
`GPU_ARCH=70` and `CMAKE_CUDA_HOST_COMPILER`; legacy FindCUDA releases retain
`sm_70` and their supported options. The generated build is checked, including
Volta-only fatbinary flags. The container must see at least the 24 requested
compilation CPUs. Builds reuse the qualified file-backed overlay driver; all
host scratch, logs and images stay under the home project.

Run **Build LAMMPS with installed DeePMD** (`lammps.yml`) on the reviewed branch,
selecting `development` and a GPU target. This workflow currently produces a
**build-verified candidate**, not a scientific acceptance or `/opt` publication.
The status record keeps `scientific_verified`, `native_runtime_verified` and
`published` false. GPU numerical checks, DeePMD-potential calculations and
native export/install remain separate follow-up stages. The existing paired
workflow remains available for testing simultaneous upstream changes.

The cluster controller can also be used with a frozen copy of `controller/`
and the runtime plan at `controller/runtime-plan.json`:

```bash
python3 controller/md_lammps_standalone.py plan --target 16v100-avx2 --track development > delivery.json
python3 controller/md_lammps_standalone.py submit UNIQUE_RUN_ID delivery.json
python3 controller/md_lammps_standalone.py status UNIQUE_RUN_ID
```

Source objects must already be in the shared bare caches. The Actions entry
fills missing commits through the existing source-transfer mechanism. A run
directory is never silently reused; its recorded Slurm job is monitored rather
than resubmitted. No public module or installed dependency is modified.

DeePMD's independent build/acceptance entry and the default daily scheduler
have not yet been switched to this model. CP2K is already a separate workflow.

## October 22 first live build

[Workflow 39724118766](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39724118766)
submitted Slurm job **1896356** on `16v100n02`, using recipe commit `3546d33` and
LAMMPS develop commit `a0e2bd6efb3d67a29c0c4e4de3b19556ffdb9d5b`. Configuration
passed with GCC 14.3.0 and CUDA 12.9, all 13 DeePMD styles were registered,
and the generated native-CUDA Volta configuration passed its checks. The
container sees 32 logical CPUs and compiles with 24 parallel workers. C++ and
CUDA compilation has started; there is no completed candidate or scientific
acceptance yet. [Recorded progress](evidence/lammps-independent-20261022/build-progress.json).

Linux CI for the recipe passed all 895 tests (893 passed, 2 optional skips).
