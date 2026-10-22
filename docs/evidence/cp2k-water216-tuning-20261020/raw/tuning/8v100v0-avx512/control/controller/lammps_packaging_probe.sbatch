#!/bin/bash
# Read-only diagnosis of the packaging container startup; never writes the
# completed build overlay.
#SBATCH --partition=16V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --gpus-per-node=1
#SBATCH --time=10
#SBATCH --job-name=md-packaging-probe
#SBATCH --output=/home/stardust/sai-hpc-software/diagnostics/unlimited-recovery-20260922/md-packaging-probe-%j.log
#SBATCH --export=NIL
set -exo pipefail
export LOGNAME=${LOGNAME:-stardust} USER=${USER:-stardust}
export PATH=/usr/bin:/bin LD_LIBRARY_PATH='' LD_PRELOAD=''
source /etc/profile.d/lmod.sh
module load apptainer/1.4.4
set -u
export TMPDIR=/home/stardust/sai-hpc-software/diagnostics/unlimited-recovery-20260922/probe-runtime
export APPTAINER_TMPDIR="$TMPDIR" APPTAINER_CACHEDIR=/home/stardust/sai-hpc-software/diagnostics/unlimited-recovery-20260922/probe-cache
mkdir -p "$TMPDIR" "$APPTAINER_CACHEDIR"
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
apptainer exec --fakeroot --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / --net --network none --nv \
  --bind /usr:/usr:ro --bind /sbin:/sbin:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro \
  --bind /opt/devtools:/opt/devtools:ro --bind /opt/modules:/opt/modules:ro \
  --bind /etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro --bind /etc/lmod:/etc/lmod:ro \
  --bind /home/stardust/sai-hpc-software/experimental/deepmd-lammps/controller/recovery-20260922-native-r4/controller:/control:ro \
  --bind /home/stardust/sai-hpc-software/diagnostics/unlimited-recovery-20260922/lammps_manifest_diagnose.sh:/diagnose.sh:ro \
  --bind /home/stardust/sai-hpc-software/diagnostics/unlimited-recovery-20260922/probe-runtime:/runtime:rw \
  --bind /opt/apps/conda_env/deepmd-kit-3.2.0:/opt/apps/conda_env/deepmd-kit-3.2.0:ro \
  --bind /opt/apps/conda_env/deepmd-kit-3.1.2:/opt/apps/conda_env/deepmd-kit-3.1.2:ro \
  --bind /opt/apps/conda_env/VeloxChem/gpu:/opt/apps/conda_env/VeloxChem/gpu:ro \
  --bind /opt/apps/plumed/plumed-2.10.1:/opt/apps/plumed/plumed-2.10.1:ro \
  --bind /opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-plumed2.10.1-nvhpc263-ompi5010-sm70:/opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-plumed2.10.1-nvhpc263-ompi5010-sm70:ro \
  --overlay /home/stardust/sai-hpc-software/experimental/deepmd-lammps/runs/md-20260922-native-recovery-08/work.ext3:ro \
  --env TMPDIR=/runtime /home/stardust/sai-hpc-software/containers/base/minimal-v1.sif \
  /usr/bin/bash /diagnose.sh
