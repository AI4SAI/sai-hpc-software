#!/usr/bin/env bash
set -eo pipefail
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/apps /opt/modules/modulefiles/devtools
check_dir=$1
output=$2
export TMPDIR="$output/scratch"
export OMP_NUM_THREADS=1
mkdir -p "$TMPDIR"

module --ignore_cache load nepadapters/1.0.4-lammps30Sep2026
printf 'info styles pair\n' > "$output/in.styles"
lmp -in "$output/in.styles" -log none > "$output/lmp-styles.txt"
/usr/bin/python3 -B "$check_dir/module-environment.py" auto "$output"
module unload nepadapters/1.0.4-lammps30Sep2026
/usr/bin/python3 -B "$check_dir/module-environment.py" auto-unloaded "$output"

module load lammps/30Sep2026-deepmd320-auto
/usr/bin/python3 -B "$check_dir/module-environment.py" baseline "$output"
module load nepadapters/1.0.4-lammps30Sep2026
/usr/bin/python3 -B "$check_dir/module-environment.py" attached "$output"
module unload nepadapters/1.0.4-lammps30Sep2026
/usr/bin/python3 -B "$check_dir/module-environment.py" detached "$output"
module purge
/usr/bin/python3 -B "$check_dir/module-environment.py" purged "$output"
