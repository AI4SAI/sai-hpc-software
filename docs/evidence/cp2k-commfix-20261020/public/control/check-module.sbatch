#!/usr/bin/env bash
# Check a home-directory candidate module against the physical /opt libraries.
#SBATCH --nodes=1
#SBATCH --ntasks=2
#SBATCH --ntasks-per-node=2
#SBATCH --time=00:10:00
#SBATCH --export=ALL
set -euo pipefail
control=${1:?frozen controls}; output=${2:?results}
mode=${3:-candidate}
partition=${SLURM_JOB_PARTITION:?}
export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD=
export TMPDIR=$output/runtime CUDA_CACHE_PATH=$output/runtime/cuda-cache XDG_CACHE_HOME=$output/runtime/cache
export PRTE_MCA_prte_tmpdir_base=$TMPDIR OMPI_MCA_orte_tmpdir_base=$TMPDIR SLURM_EXPORT_ENV=ALL
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$TMPDIR" "$CUDA_CACHE_PATH" "$XDG_CACHE_HOME"
hostname > "$output/hostname.txt"
scontrol show job -o "$SLURM_JOB_ID" > "$output/allocation.txt"
if [[ "$partition" != DSPRHBM ]]; then
  set +u
  source "/opt/sai_config/mps_mapping.d/$partition.bash"
  set -u
fi
python3 - "$control" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import install
import json
release = json.loads((install.PACKAGE/'release.json').read_text())
for target, prefix in install.PREFIXES.items():
    install.verify(prefix, install.read_manifest(target+'-manifest.json', release['builds'][target]['manifest_sha256']))
print('ELPA_REPAIRED_PHYSICAL_FILES_PASS', flush=True)
PY
if [[ "$mode" == public ]]; then
  python3 "$control/install.py" --verify > "$output/installed-files.txt"
fi
bash "$control/check-partition.sh" "$control" "$output" "$partition" "$mode"
