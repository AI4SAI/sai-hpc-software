# Independent LAMMPS candidates

LAMMPS can use the accepted DeePMD 3.2.0 installations already in `/opt`.
Updating LAMMPS does not require rebuilding DeePMD or waiting for its moving
`master` branch. The `lammps.yml` workflow resolves only LAMMPS's selected
channels and runs an independent job per selected track and GPU partition.
The daily scheduler invokes this reusable workflow separately from DeePMD.

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
host scratch, logs and images for new runs stay under the funded project
`/org/org-open-sai/proj-sai-hpc-software`, with Slurm account
`proj-sai-hpc-software`. Historical home-based runs retain their frozen paths.

Run **Build LAMMPS with installed DeePMD** (`lammps.yml`) on the reviewed branch,
selecting `development` and GPU targets. It builds a verified candidate SIF,
then requires the same 14 single/two-node GPU scientific cases used for the
accepted development candidates. Build and science retain separate records;
the workflow succeeds only after both pass. Native export, publication and
physical installation remain separate stages. The existing paired workflow
remains available for testing simultaneous upstream changes.

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

DeePMD has its own `deepmd.yml` build/acceptance workflow. The daily scheduler
calls the two workflows independently; neither waits for the other's moving
development branch. See the
[current DeePMD evidence](evidence/deepmd-project-20261023/README.md).
CP2K is also a separate workflow.

As of October 23, all three LAMMPS V100 targets are scientifically accepted
for develop commit `a0e2bd6efb3d67a29c0c4e4de3b19556ffdb9d5b`. The previously
compiled 8V100V0 overlay was recovered without changing its original recipe,
then passed all 14 cases. The accepted 4V100/16V100 SIFs and raw results were
independently reread. See the
[three-partition evidence](evidence/lammps-project-20261023/README.md).
The October 22 entries below preserve the earlier build and acceptance history.

## October 22 first live build

[Workflow 39724118766](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39724118766)
submitted Slurm job **1896356** on `16v100n02`, using recipe commit `3546d33` and
LAMMPS develop commit `a0e2bd6efb3d67a29c0c4e4de3b19556ffdb9d5b`. Configuration
passed with GCC 14.3.0 and CUDA 12.9, all 13 DeePMD styles were registered,
and the generated native-CUDA Volta configuration passed its checks. The
container sees 32 logical CPUs and compiles with 24 parallel workers.
The job completed successfully in **55m57s**, consuming 9h04m49s of CPU time
(about 9.7 CPUs averaged over build, linking, packaging and verification).
The workflow is green and the **264,478,720-byte SIF** passed its installed
file inventory and runtime-path audit. Its SHA256 is
`c268e2ec543bce00e2712bb817ceee7841ea693a47b6a858e8b4ad931e4aed54`.
The candidate is under
`experimental/deepmd-lammps/runs/lmp-39724118766-1-16v100-avx2-20261022/candidate.sif`
in the cluster home project. [Completed build evidence](evidence/lammps-independent-20261022/build-completed.json)
supersedes the earlier [progress snapshot](evidence/lammps-independent-20261022/build-progress.json).

The [acceptance harness](evidence/lammps-independent-20261022/acceptance.py)
uses the release's frozen numerical references and inputs. It runs 14
scientific cases: single/two-node TF/PT/JAX DeePMD potentials with PLUMED,
plus GPU-package/Kokkos LJ and PPPM. It also checks the exact SIF executable,
MPI/DeePMD loading, visible V100 devices and preservation of all release
packages/styles. All rank scratch is backed by home. Only LAMMPS runs are
required; this does not rerun a separate DeePMD build or training acceptance.
Scientific acceptance completed on October 22 for 16V100 and 4V100; native
installation is pending.

On the cluster, using a frozen copy of these three evidence helpers:

```bash
python3 acceptance.py submit /home/stardust/sai-hpc-software/experimental/deepmd-lammps/runs/BUILD_RUN \
  --reference /path/to/deployment/lammps-30Sep2026/native-reference.json
# After the recorded job completes:
python3 acceptance.py verify /home/stardust/sai-hpc-software/experimental/deepmd-lammps/runtime-tests/ACCEPTANCE_RUN
```

The acceptance report is separate from the immutable build record; it cannot
mark a candidate as physically installed or published.

Linux CI for the original build recipe passed 895 tests (893 passed, 2 optional
skips). The status query now handles completed jobs that have aged out of
Slurm's live queue; it reads their durable accounting records. That fix passed
896 tests (894 passed, 2 skips). The build's frozen controller is preserved;
acceptance validates its recipe and dependency through its original helpers.

## October 22 GPU scientific acceptance

Job **1896820** completed `0:0` in **1m28s** on `16v100n13` and `16v100n14`.
All **14 scientific cases** passed, alongside executable/loader and feature
checks. Independent raw-result revalidation, image SHA256 verification and
installed DeePMD inventory verification also passed.
[Acceptance summary](evidence/lammps-independent-20261022/scientific-acceptance-summary.json)
and [numerical results, rank placement and raw-file hashes](evidence/lammps-independent-20261022/scientific-acceptance.json).

The candidate has **71 packages and 2,497 styles**; all 71 packages and 2,473
styles from the accepted release remain available. Both single/two-node
DeePMD cases match the accepted release's energy, force and virial values
exactly at recorded precision, and also pass the upstream reference checks.
The GPU/Kokkos LJ/PPPM maximum differences from release are `2.96e-12` for
energy/pressure and `2.22e-15` for forces, within the unchanged tolerances.
All rank traces retain visible allocated GPUs. PyTorch reports loading the
model onto GPU 0 on both nodes; its device is a Tesla V100-SXM2-16GB, SM70.
TF/JAX use the site's CPU backends with the GPUs still visible. This is
correctness acceptance, with no performance or DeePMD training claim.

Three failed acceptance attempts are retained in the home tree and
[documented](evidence/lammps-independent-20261022/acceptance-attempts.json).
The fixes expose the home-backed parent MPI session directory to each SIF
rank and interpret `lmp -h` using upstream's version rule: Development and
Maintenance increment the integer date version, e.g. September 30 becomes
`20260931`. The same SIF was used throughout; no rebuild or `/opt` write was
needed. The accepted run used helper commit `f028ef9`, whose full Linux CI
passed **898 tests (896 passed, 2 optional skips)** in
[workflow 39732789452](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39732789452).

The additional development builds use the same implementation:
[4V100 workflow 39733175749](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39733175749)
and [8V100V0 workflow 39733178970](https://github.com/AI4SAI/sai-hpc-software/actions/runs/39733178970).
At that October 22 checkpoint, the 4V100 build and science were accepted;
8V100V0 still awaited export recovery, completed on October 23 as linked above.

4V100 build job **1896871** completed `0:0` in **41m08s**, producing a
264,884,224-byte SIF. Job **1897165** then passed all **14 scientific cases**
in **57s** on `4v100n18` and `4v100n19`. All 71 release packages and 2,473
release styles remain available among the candidate's 71 packages / 2,497
styles. Independent verification reparsed the raw results and rechecked the
image and installed DeePMD bytes.
[Build record](evidence/lammps-independent-20261022/4V100-build-completed.json),
[scientific summary](evidence/lammps-independent-20261022/4V100-scientific-acceptance-summary.json),
and [numerical/rank evidence](evidence/lammps-independent-20261022/4V100-scientific-acceptance.json).

Two preceding 4V100 attempts failed before application execution during MPI
process binding. The second captured `hwloc_set_cpubind` failure for CPUs
`12,28` on `4v100n18`; neither attempt reached scientific calculations. The
acceptance launcher now uses one MPI rank per node with Slurm's inherited CPU
affinity and no additional PRTE core binding. Thread limits, inputs, tolerances
and SIF bytes are unchanged; timings are not a performance comparison.
[Failed attempts and exact logs](evidence/lammps-independent-20261022/4V100-acceptance-attempts.json)
remain archived. The earlier 16V100 run retains its original frozen launcher
and verifier in its recorded home directory.

At this October 22 checkpoint, the workflow was a manual build entry plus
separate scientific acceptance, with 16V100 and 4V100 accepted for this develop
SHA. The October 23 workflow now requires that scientific acceptance and is
connected to the daily scheduler. Native publication and deployment remain
separate stages.
