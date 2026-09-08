# sai-hpc-software

Independent GitHub Actions → SSH → Slurm → Apptainer builds on SAI.
The implemented recipe is ABACUS; the controller/cache/container policy is reusable for additional recipes.

## Container contract

Host source/build/install trees are **not** mounted writable. A build uses the pre-provisioned
~1.9 MB base SIF plus one sparse ext3 image. Git checkout, CMake, compiler temporary files,
installation and final filesystem assembly all happen inside that image.

The successful artifact is one read-only SquashFS-backed SIF. It contains
`/opt/software/abacus/<version>/<target>` at exactly that path. The installed
administrator dependencies are reused read-only at their original paths:
`/opt/devtools`, `/opt/modules`, `/usr`, `/lib`, `/lib64`.
The entire `/opt` is never bound over the installation.

The trusted entrypoint and bare repository are mounted read-only. Host home, working directory,
site-wide bind paths and host temporary directories are disabled; the build also has a private
PID namespace and a network namespace with no external network. This is filesystem/process
containment using the shared host kernel, **not a VM or a guarantee against kernel exploits**.

`4V100` and `16V100` are separate build targets. A real compute-node probe established
`znver4` plus AVX-512 dependencies on `4V100`, and `znver3` plus AVX2 dependencies on
`16V100`; both use V100 `sm_70`. Builds use those explicit CPU architectures and fail if
the target node or auto-selected MPI/BLAS dependency does not match the profile.

Successful builds remove their ext3 work image after verifying the final SIF. Failed builds
retain just that single image for diagnosis, not an expanded sandbox.
The small base SIF is provisioned separately; the workflow fails clearly if it is absent and
does not silently create an on-host sandbox.

## Storage on SAI

All project files live below `/home/stardust/sai-hpc-software`:

```text
containers/base/minimal-v1.sif
containers/software/abacus/<version>/<target>/<run-id>.sif
cache/repositories/abacus/        # bare Git objects, never checked out on host
controller/<controller-sha>/<run-id>/ # trusted code snapshot, never overwritten by another run
runs/<run-id>/input/             # verified compressed bundle parts when needed
runs/<run-id>/results/           # Slurm log, state, artifact checksum
runs/<run-id>/runtime/           # Apptainer runtime work, not source/build/install
runs/<run-id>/work.ext3          # one temporary file, retained on failure
runs/<run-id>/artifact.path      # published SIF location after verification
modulefiles/apps/abacus/<version> # generated user module for a verified version
runtime-tests/<run-id>/          # bounded multi-node acceptance inputs, logs and rank evidence
```

Old sandbox-based runs are legacy leftovers; this controller does not delete those automatically.
Do not infer success from earlier smoke images or a GitHub validation-only run.

## Source tracking and cache

Dispatch `Build HPC software` with a branch/tag, a full commit, `latest-release`
or `latest-prerelease`. Release and branch selectors resolve live to the actual upstream
commit. No synthetic/orphan commits are substituted.

The daily tracker runs at 02:23 UTC using `profiles/tracking.json`. It checks the
branch, stable release and prerelease channels. Scheduled runs skip an unchanged
version only when the published SIF's verification metadata and checksum match;
manual dispatch rebuilds deliberately. A100 remains selectable manually, but is
not in the daily matrix while both SAI A100 nodes are unavailable.

Cache hits upload **zero source bytes**. Cache misses bundle only changes against an available
ancestor (or a full seed if no ancestor exists). The bundle is gzip-compressed, split into
exactly eight byte chunks, and transferred concurrently. Each part and the full compressed
and decompressed streams have checked sizes and SHA-256 hashes. The host receiver verifies
Git prerequisites before importing into the bare cache under a lock; it never runs source code.
All child transfer failures are checked.

An administrator can seed the cache from an existing full-history local Git checkout:

```bash
python3 controller/source_cache.py pack /path/to/local/repository upstream-sha ./seed
# Upload manifest.json and source.part.00 through source.part.07 to an input directory.
# On SAI, run the trusted controller already provisioned outside any build container:
python3 controller/source_cache.py receive \
  /home/stardust/sai-hpc-software/cache/repositories/abacus \
  /home/stardust/sai-hpc-software/runs/manual-seed/input
```

Use `--base <cached-ancestor-sha>` when making an incremental seed.
No source checkout or build/install directory is created by the receiver.

## GitHub configuration

The public host key is versioned in `.ci/slurm/known_hosts`.
`REMOTE_SSH_PRIVATE_KEY` and `REMOTE_USER` are Actions secrets
(current deployment: the repository's `hpc` Environment).
No private key is committed. Only manually dispatched trusted workflow runs access the SSH key;
push and PR runs only validate. Keep the `hpc` Environment limited to trusted branches.

Targets: `cpu-misc` (CPU-MISC), `4v100-avx512` (4V100),
`16v100-avx2` (16V100), `a100` (8A100M40).
Pass a comma-separated subset to dispatch. CPU is the default acceptance target.
Every build runs independently with its own overlay, logs and SIF path. GitHub retains logs
and the SAI artifact location, while the container itself stays on SAI.

## Host MPI runtime

The generated module is loaded **inside the Slurm allocation**, so the site `*-auto`
dependency modules inspect the actual compute-node CPU. The trusted `abacus` command then
selects the SIF from `SLURM_JOB_PARTITION`. The host Open MPI launches one wrapper per rank;
each wrapper enters the same read-only SIF and runs its ABACUS binary:

```bash
source /etc/profile.d/lmod.sh
module use /home/stardust/sai-hpc-software/modulefiles/apps
module load abacus/<version>
source /opt/sai_config/mps_mapping.d/${SLURM_JOB_PARTITION}.bash
export MAP_OPT SLURM_EXPORT_ENV=ALL
export OMPI_MCA_plm_slurm_args=--external-launcher
export PRTE_MCA_plm_slurm_args=--external-launcher
mpirun -np "$SLURM_NTASKS" --map-by "$MAP_OPT" abacus
```

Runtime does not use the build container's network isolation or fakeroot. `--nv` exposes
the Slurm-assigned NVIDIA devices and matching host driver libraries; MPI/network setup
remains host managed. The launcher binds only the calculation directory writable, keeps
the SIF and `/opt/devtools` read-only, and puts Apptainer runtime files below
`/home/stardust/sai-hpc-software/runtime/jobs`. It rejects an unknown partition rather than
falling back to an incompatible image.

Every new precise V100 build is followed by a two-node, one-rank-per-GPU scientific smoke.
This deliberately avoids the site's multi-rank-per-GPU MPS path, which currently uses
host `/tmp`. The acceptance records rank/hostname/image selection, requires two distinct
nodes, exercises CUDA and MPI in a short PW SCF case, and requires SCF convergence.

## Manual inspection

Use the published path from `runs/<run-id>/artifact.path`, not a legacy image:

```bash
ssh SAI-stardust
source /etc/profile.d/lmod.sh
module load apptainer/1.4.4
apptainer exec --cleanenv --containall --no-home \
  --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / \
  --bind /usr:/usr:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro \
  --bind /opt/devtools:/opt/devtools:ro \
  /home/stardust/sai-hpc-software/containers/software/abacus/VERSION/TARGET/RUN.sif \
  /usr/bin/find /opt/software -maxdepth 5 -type f
```

The SIF records the upstream SHA, module list and CMake cache below the installation's
`share/sai/` directory. Loading its recorded modules is required to run software
that dynamically links to the cluster environment. Administrative deployment into the host
`/opt` is out of scope.

For a read-only runtime, source `share/sai/runtime-env.sh` inside the container
instead of initializing Lmod there; it records the original absolute dependency
paths without needing writable temporary files. Packaging/verification failures
can be retried using the workflow's `resume_run` input: this moves the old run's
single ext3 image into the new run and repacks the existing installation. The
source SHA, version and target must match, and active jobs cannot be resumed.

## Local checks

```bash
python3 -m unittest discover -s tests -v
bash -n controller/container_entry.sh controller/abacus_build.sh controller/environment.sh
```

Tests cover real full/incremental Git cache reception, missing prerequisites, transport
corruption, path injection, generated Slurm syntax and the read-only bind policy.
