#!/usr/bin/env bash
#SBATCH --job-name=deepmd-science-dp-science-20261023-16v100-avx2-558f569-c
#SBATCH --partition=16V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=2
#SBATCH --time=30
#SBATCH --account=proj-sai-hpc-software
#SBATCH --chdir=/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/runs/dp-science-20261023-16v100-avx2-558f569-c
#SBATCH --output=/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/runs/dp-science-20261023-16v100-avx2-558f569-c/results/slurm-%j.log
#SBATCH --export=NIL
set -eo pipefail
export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""
source /etc/profile.d/lmod.sh
module load apptainer/1.4.4
set -u
umask 077
export TMPDIR=/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/runs/dp-science-20261023-16v100-avx2-558f569-c/runtime
export APPTAINER_TMPDIR="$TMPDIR"
export APPTAINER_CACHEDIR=/org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/runs/dp-science-20261023-16v100-avx2-558f569-c/apptainer-cache
export APPTAINERENV_SAI_MD_ALLOCATED_JOB="$SLURM_JOB_ID"
export APPTAINERENV_SAI_MD_ALLOCATED_NODE="$(hostname)"
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
apptainer exec --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / --net --network none --nv --bind /usr:/usr:ro --bind /sbin:/sbin:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro --bind /opt/devtools:/opt/devtools:ro --bind /opt/modules:/opt/modules:ro --bind /opt/apps/conda_env/deepmd-kit-3.2.0:/opt/apps/conda_env/deepmd-kit-3.2.0:ro --bind /opt/apps/conda_env/deepmd-kit-3.1.2:/opt/apps/conda_env/deepmd-kit-3.1.2:ro --bind /opt/apps/conda_env/VeloxChem/gpu:/opt/apps/conda_env/VeloxChem/gpu:ro --bind /opt/apps/plumed/plumed-2.10.1:/opt/apps/plumed/plumed-2.10.1:ro --bind /opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-nvhpc263-ompi5010-sm70:/opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-nvhpc263-ompi5010-sm70:ro --bind /opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-plumed2.10.1-nvhpc263-ompi5010-sm70:/opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-plumed2.10.1-nvhpc263-ompi5010-sm70:ro --bind /org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/controller/d8b4c2fe338dbc8626bb78ffbebbf69bda572029/controller:/acceptance:ro --bind /etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro --bind /etc/lmod:/etc/lmod:ro --bind /org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/controller/76869d13905944871c98c24860471883e4ed9aa2/controller:/control:ro --bind /org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/runs/dp-science-20261023-16v100-avx2-558f569-c/runtime:/runtime:rw --env BUILD_JOBS=1 --env TMPDIR=/runtime --bind /org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/runs/dp-science-20261023-16v100-avx2-558f569-c/runtime:/tmp:rw /org/org-open-sai/proj-sai-hpc-software/experimental/deepmd-standalone/runs/dp-independent-20261023-16v100-avx2-558f569-b/candidate.sif /usr/bin/bash -euo pipefail -c 'source /runtime/runtime-env.sh; export SAI_DEEPMD_DELIVERY="$2"; export OMP_NUM_THREADS=1 DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1; exec "$1/bin/python" /acceptance/deepmd_standalone_check.py science' bash /opt/apps/deepmd-kit/deepmd-kit-development-master-2026-10-23-g558f56962775-re04065e624bd/16V100 '{"identity":{"build_id":"master-2026-10-23-g558f56962775-re04065e624bd","cpu_arch":"znver3","cuda_arch":"70","dependency_isa":"avx2","gpus":1,"install_prefix":"/opt/apps/deepmd-kit/deepmd-kit-development-master-2026-10-23-g558f56962775-re04065e624bd/16V100","partition":"16V100","recipe_sha256":"e04065e624bdf113dbcc32e713510472a1416bec81e664571ce84121024d54ea","repository":"deepmodeling/deepmd-kit","schema":2,"software":"deepmd-kit","source_ref":"master","source_sha":"558f56962775098b8b554e341c3b37d2f86e6aeb","source_version":"master-2026-10-23","stack_digest":null,"stack_sources":{},"target":"16v100-avx2","track":"development","version_label":"master-2026-10-23"},"schema":1}'
echo DEEPMD_INDEPENDENT_ACCEPTANCE_VERIFIED_NOT_PUBLISHED
