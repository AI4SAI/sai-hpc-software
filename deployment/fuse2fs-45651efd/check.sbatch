#!/usr/bin/env bash
#SBATCH --job-name=fuse2fs-public-check
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --time=00:10:00
set -euo pipefail
package=${1:?package directory}
output=${2:?new results directory under home}
shift 2
export TMPDIR="$HOME/.cache/sai-fuse2fs-check/$SLURM_JOB_ID"
mkdir -p "$TMPDIR"
chmod 700 "$TMPDIR"
export TMP="$TMPDIR" TEMP="$TMPDIR" APPTAINER_TMPDIR="$TMPDIR"
export APPTAINER_CACHEDIR="$TMPDIR/cache" PYTHONDONTWRITEBYTECODE=1
exec /usr/bin/python3 -B "$package/check.py" "$output" "$@"
