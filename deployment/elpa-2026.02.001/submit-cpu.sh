#!/usr/bin/env bash
set -euo pipefail
package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
root=/home/stardust/sai-hpc-software
stamp=${1:-$(date +%Y%m%d-%H%M%S)}
[[ "$stamp" =~ ^[A-Za-z0-9][A-Za-z0-9-]+$ ]]
run=$root/runs/elpa-2026.02.001-$stamp-dsprhbm
[[ ! -e "$run" ]]
mkdir -p "$run"/{control,input/reference,install,work,runtime,cache}
cp "$package"/{build.sh,build-cpu.sbatch,container.sh,environment.sh,smoke.c,verify.sh,cuda-aware-cpu.patch} "$run/control/"
cp "$package/../../controller/create_rootfs.sh" "$run/control/"
reference=/opt/apps/abacus/abacus-develop-3.9.0.26_nvhpc263gnu/toolchain
cp "$reference/build/elpa-2026.02.001.tar.gz" "$run/input/"
cp "$reference/scripts/stage3/install_elpa.sh" "$run/input/reference/"
cp /opt/modules/modulefiles/devtools/elpa/2026.02.001-2603-gnu "$run/input/reference/original-module.tcl"
sha256sum "$run/control/"* "$run/input/"*.tar.gz > "$run/inputs.sha256"
job=$(sbatch --parsable --job-name=elpa-cpu-DSPRHBM --output="$run/slurm-%j.out" \
  "$run/control/build-cpu.sbatch" "$run")
printf '%s\n' "$job" > "$run/job.id"
printf 'DSPRHBM %s %s\n' "$job" "$run"
