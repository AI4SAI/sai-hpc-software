#!/bin/bash
# Diagnostic only: the completed overlay is read-only; a separate tiny upper
# overlay receives candidate metadata so the failed helper can be traced.
#SBATCH --partition=16V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=20
#SBATCH --job-name=md-packaging-debug
#SBATCH --output=/home/stardust/sai-hpc-software/diagnostics/unlimited-recovery-20260922/md-packaging-debug-%j.log
#SBATCH --export=NIL
set -eo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin LD_LIBRARY_PATH='' LD_PRELOAD=''
source /etc/profile.d/lmod.sh
module load apptainer/1.4.4
set -u
base=/home/stardust/sai-hpc-software
diag=$base/diagnostics/unlimited-recovery-20260922
run=$base/experimental/deepmd-lammps/runs/md-20260922-native-recovery-08
export TMPDIR=$diag/packaging-debug-runtime APPTAINER_TMPDIR=$TMPDIR APPTAINER_CACHEDIR=$diag/packaging-debug-cache
mkdir -p "$TMPDIR" "$APPTAINER_CACHEDIR"
upper=$diag/packaging-debug.ext3
if [[ ! -e "$upper" ]]; then truncate -s 2G "$upper"; mke2fs -t ext3 -F -m 0 -q "$upper"; fi
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
apptainer exec --fakeroot --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / --net --network none --nv \
  --bind /usr:/usr:ro --bind /sbin:/sbin:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro \
  --bind /opt/devtools:/opt/devtools:ro --bind /opt/modules:/opt/modules:ro \
  --bind /opt/apps/conda_env/deepmd-kit-3.2.0:/opt/apps/conda_env/deepmd-kit-3.2.0:ro \
  --bind /opt/apps/conda_env/deepmd-kit-3.1.2:/opt/apps/conda_env/deepmd-kit-3.1.2:ro \
  --bind /opt/apps/conda_env/VeloxChem/gpu:/opt/apps/conda_env/VeloxChem/gpu:ro \
  --bind /opt/apps/plumed/plumed-2.10.1:/opt/apps/plumed/plumed-2.10.1:ro \
  --bind /opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-plumed2.10.1-nvhpc263-ompi5010-sm70:/opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-plumed2.10.1-nvhpc263-ompi5010-sm70:ro \
  --bind /etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro --bind /etc/lmod:/etc/lmod:ro \
  --bind "$diag/md_packaging_recovery.sh:/packaging/md_packaging_recovery.sh:ro" \
  --bind "$run/request.json:/delivery.json:ro" \
  --bind /home/stardust/sai-hpc-software/experimental/deepmd-lammps/controller/recovery-20260922-native-r4/controller:/control:ro \
  --overlay "$run/work.ext3:ro" --overlay "$upper" --env BUILD_JOBS=1 --env TMPDIR=/runtime \
  /home/stardust/sai-hpc-software/containers/base/minimal-v1.sif /usr/bin/bash -x -c \
  'delivery=$(/usr/bin/python3 -c "import json; print(json.dumps(json.load(open(\"/delivery.json\"))[\"delivery\"], separators=(\",\",\":\")))"); /usr/bin/bash -x /packaging/md_packaging_recovery.sh package "$delivery"'
