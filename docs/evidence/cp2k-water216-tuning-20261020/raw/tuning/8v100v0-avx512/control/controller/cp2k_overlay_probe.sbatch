#!/bin/bash
# Read-only probe of the timed-out CP2K main overlay. It does not alter the
# overlay and does not run build/export; it only checks whether the build and
# metadata stages completed before the walltime cutoff.
#SBATCH --partition=DSPRHBM
#SBATCH --qos=rush-cpu
#SBATCH --exclude=d9470n01,d9470n02
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=20
#SBATCH --job-name=cp2k-overlay-probe
#SBATCH --output=/home/stardust/sai-hpc-software/diagnostics/unlimited-recovery-20260922/cp2k-overlay-probe-%j.log
#SBATCH --export=NIL
set -eo pipefail
export PATH=/usr/bin:/bin LD_LIBRARY_PATH='' LD_PRELOAD=''
export USER=${USER:-stardust} LOGNAME=${LOGNAME:-stardust}
source /etc/profile.d/lmod.sh
module load apptainer/1.4.4
set -u
base=/home/stardust/sai-hpc-software
run=$base/runs/cp2k-final-main-20260922-08-DSPRHBM
export TMPDIR=$base/diagnostics/unlimited-recovery-20260922/cp2k-overlay-probe-runtime
export APPTAINER_TMPDIR=$TMPDIR APPTAINER_CACHEDIR=$base/diagnostics/unlimited-recovery-20260922/cp2k-overlay-probe-cache
mkdir -p "$TMPDIR" "$APPTAINER_CACHEDIR"
unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH
read -r sha version target prefix < <(/usr/bin/python3 -c 'import json; r=json.load(open("/home/stardust/sai-hpc-software/runs/cp2k-final-main-20260922-08-DSPRHBM/request.json")); i=r["identity"]; print(i["source_sha"], i["source_version"], i["target"], i["install_prefix"])')
delivery=$(/usr/bin/python3 -c 'import json; r=json.load(open("/home/stardust/sai-hpc-software/runs/cp2k-final-main-20260922-08-DSPRHBM/request.json")); print(json.dumps(r["identity"], separators=(",", ":")))')
apptainer exec --fakeroot --cleanenv --containall --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd / --net --network none \
  --bind /usr:/usr:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro \
  --bind /opt/devtools:/opt/devtools:ro --bind /opt/modules:/opt/modules:ro \
  --bind /var/lib/dpkg:/var/lib/dpkg:ro --bind /usr/lib/os-release:/etc/os-release:ro \
  --bind /etc/alternatives:/etc/alternatives:ro --bind /usr/bin:/bin:ro \
  --bind /etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro --bind /etc/lmod:/etc/lmod:ro \
  --bind "$base/controller/cp2k-final-spack-20260922-r2/controller:/control:ro" \
  --overlay "$run/work.ext3:ro" --env BUILD_JOBS=2 --env TMPDIR=/workspace/tmp \
  "$base/containers/base/minimal-v1.sif" /usr/bin/bash -c 'set -euxo pipefail; echo CP2K_OVERLAY_BEGIN; test -x "$1/bin/cp2k.psmp"; test -s "$1/share/sai/CMakeCache.txt"; test -s "$1/share/sai/release-identity.json"; find "$1/bin" -maxdepth 1 -type f -printf "%f\\n" | sort; echo CP2K_OVERLAY_FILES_PRESENT' probe "$prefix"
echo CP2K_OVERLAY_METADATA_PROBE_PASSED
