#!/usr/bin/env bash
# One authorized 10-minute, two-node probe; small task inputs only.
#SBATCH --job-name=abacus-baseline-parity
#SBATCH --partition=4V100
#SBATCH --qos=flood-1o2gpu
#SBATCH --nodes=2
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=1
#SBATCH --gpus-per-node=1
#SBATCH --time=10
#SBATCH --output=/home/stardust/sai-hpc-software/runtime-tests/baseline-parity-536dfc2-nss-4v100/slurm-%j.log
#SBATCH --export=NIL
set -euo pipefail
root=/home/stardust/sai-hpc-software
task="$root/runtime-tests/baseline-parity-536dfc2-nss-4v100"
export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}
export HOME=/home/stardust
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LD_LIBRARY_PATH='' LD_PRELOAD=''
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools /opt/modules/modulefiles/apps
module load apptainer/1.4.4 abacus/v3.9.0.26-sm70-auto
export TMPDIR="$task/mpi-runtime" APPTAINER_TMPDIR="$task/apptainer-runtime" APPTAINER_CACHEDIR="$task/apptainer-cache"
mkdir -p "$TMPDIR" "$APPTAINER_TMPDIR" "$APPTAINER_CACHEDIR"
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
export SLURM_EXPORT_ENV=ALL OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher
source /opt/sai_config/mps_mapping.d/4V100.bash
export OMP_NUM_THREADS=1
module -t list > "$task/modules.log" 2>&1
abacus --info > "$task/info.log"
ldd "$(command -v abacus)" > "$task/ldd.log"
lscpu > "$task/lscpu.log"
nvidia-smi -L > "$task/gpus.log"

# Only selected, known INPUT/STRU/KPT/pseudopotential/model blobs leave the
# container. The external repository remains read-only and is never checked out.
apptainer exec --cleanenv --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd /work \
  --bind /usr:/usr:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro \
  --bind "$root/cache/repositories/abacus:/input/repository:ro" --bind "$task:/work:rw" \
  "$root/containers/base/minimal-v1.sif" /usr/bin/bash -s <<'CONTAINER'
set -euo pipefail
sha=9cbcc0b55deba41428f4fd4f9964bf099fdfb589
blob() { git -c safe.directory=/input/repository --git-dir=/input/repository show "$sha:$1" > "$2"; }
for pair in pw:11_PW_GPU/scf_cg hse:08_EXX/01_GO_S1_HSE deepks:09_DeePKS/01_NO_GO_deepks_scf; do
  case_name=${pair%%:*}; source_case=${pair#*:}
  mkdir -p "/work/$case_name/PP_ORB"
  for file in INPUT STRU KPT; do blob "tests/$source_case/$file" "/work/$case_name/$file"; done
  sed -i 's#../../PP_ORB#./PP_ORB#g;s#../Model_ProjOrb#./Model_ProjOrb#g' "/work/$case_name/INPUT" "/work/$case_name/STRU"
done
for file in As_dojo.upf Ga_dojo.upf; do blob "tests/PP_ORB/$file" "/work/pw/PP_ORB/$file"; done
printf '\nkpar 2\nbndpar 1\n' >> /work/pw/INPUT
for file in H_ONCV_PBE-1.0.upf O_ONCV_PBE-1.0.upf H_gga_6au_60Ry_1s.orb O_gga_6au_60Ry_1s1p.orb; do blob "tests/PP_ORB/$file" "/work/hse/PP_ORB/$file"; done
for file in C_ONCV_PBE-1.0.upf H_ONCV_PBE-1.0.upf C_gga_8au_100Ry_2s2p1d.orb H_gga_8au_100Ry_2s1p.orb; do blob "tests/PP_ORB/$file" "/work/deepks/PP_ORB/$file"; done
mkdir /work/deepks/Model_ProjOrb
for file in model_lda_pbe_18.ptg 2au_20Ry_jle.orb; do blob "tests/09_DeePKS/Model_ProjOrb/$file" "/work/deepks/Model_ProjOrb/$file"; done
CONTAINER

for case_name in pw hse deepks; do
  cd "$task/$case_name"
  /usr/bin/time -f %e -o wall-seconds.txt mpirun -np 2 --map-by "$MAP_OPT" --report-bindings \
    abacus > stdout.log 2>&1
  grep -q '#SCF IS CONVERGED#' OUT.autotest/running_scf.log
  printf 'BASELINE_CASE_COMPLETED %s\n' "$case_name"
done
echo BASELINE_PARITY_PROBE_COMPLETED
