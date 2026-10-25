#!/usr/bin/env bash
#SBATCH --job-name=deepmd-science-ddp-20261025-science-r3-8v100v0-avx512
#SBATCH --partition=8V100V0
#SBATCH --qos=rush-1o2gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=2
#SBATCH --time=30
#SBATCH --account=proj-sai-hpc-software
#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/runs/ddp-20261025-science-r3-8v100v0-avx512
#SBATCH --output=/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/runs/ddp-20261025-science-r3-8v100v0-avx512/results/slurm-%j.log
#SBATCH --export=NIL
set -eo pipefail
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""
source /etc/profile.d/lmod.sh
module load apptainer/1.4.4
set -u
umask 077
export TMPDIR=/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/runs/ddp-20261025-science-r3-8v100v0-avx512/runtime
export APPTAINER_TMPDIR="$TMPDIR"
export APPTAINER_CACHEDIR=/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/runs/ddp-20261025-science-r3-8v100v0-avx512/apptainer-cache
export APPTAINERENV_SAI_MD_ALLOCATED_JOB="$SLURM_JOB_ID"
export APPTAINERENV_SAI_MD_ALLOCATED_NODE="$(hostname)"
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
apptainer exec --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / --net --network none --nv --bind /usr:/usr:ro --bind /sbin:/sbin:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro --bind /opt/devtools:/opt/devtools:ro --bind /opt/modules:/opt/modules:ro --bind /opt/apps/conda_env/deepmd-kit-3.2.0:/opt/apps/conda_env/deepmd-kit-3.2.0:ro --bind /opt/apps/conda_env/deepmd-kit-3.1.2:/opt/apps/conda_env/deepmd-kit-3.1.2:ro --bind /opt/apps/conda_env/VeloxChem/gpu:/opt/apps/conda_env/VeloxChem/gpu:ro --bind /opt/apps/plumed/plumed-2.10.1:/opt/apps/plumed/plumed-2.10.1:ro --bind /opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-nvhpc263-ompi5010-sm70:/opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-nvhpc263-ompi5010-sm70:ro --bind /opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-plumed2.10.1-nvhpc263-ompi5010-sm70:/opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-plumed2.10.1-nvhpc263-ompi5010-sm70:ro --bind /org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/controller/ddp-science-20261025-r3:/acceptance:ro --bind /usr/sbin:/sbin:ro --bind /etc/ld.so.cache:/etc/ld.so.cache:ro --bind /etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro --bind /etc/lmod:/etc/lmod:ro --bind /org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/controller/ddp-20261025-r2:/control:ro --bind /org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/runs/ddp-20261025-science-r3-8v100v0-avx512/runtime:/runtime:rw --env BUILD_JOBS=1 --env TMPDIR=/runtime --bind /org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/runs/ddp-20261025-science-r3-8v100v0-avx512/runtime:/tmp:rw /org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/runs/ddp-20261025-r2-8v100v0-avx512/candidate.sif /usr/bin/bash -euo pipefail -c 'source /runtime/runtime-env.sh; export SAI_DEEPMD_DELIVERY="$2"; export OMP_NUM_THREADS=1 DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1; exec "$1/bin/python" /acceptance/deepmd_standalone_check.py science' bash /opt/apps/deepmd-kit/deepmd-kit-development-master-2026-10-23-g558f56962775-r7d8ce7396215/8V100V0 '{"identity":{"build_id":"master-2026-10-23-g558f56962775-r7d8ce7396215","cpu_arch":"skylake-avx512","cuda_arch":"70","dependency_isa":"avx2","gpus":1,"install_prefix":"/opt/apps/deepmd-kit/deepmd-kit-development-master-2026-10-23-g558f56962775-r7d8ce7396215/8V100V0","partition":"8V100V0","recipe_sha256":"7d8ce739621511bc42402db94e6b04aceae5577800a34c27bee009e06dbc2568","repository":"deepmodeling/deepmd-kit","schema":2,"software":"deepmd-kit","source_ref":"master","source_sha":"558f56962775098b8b554e341c3b37d2f86e6aeb","source_version":"master-2026-10-23","stack_digest":null,"stack_sources":{},"target":"8v100v0-avx512","track":"development","version_label":"master-2026-10-23"},"schema":1}'
echo DEEPMD_INDEPENDENT_ACCEPTANCE_VERIFIED_NOT_PUBLISHED
