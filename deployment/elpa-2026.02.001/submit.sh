#!/usr/bin/env bash
# Run on SAI as stardust. Freeze each recipe before submitting independent jobs.
set -euo pipefail
package=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
root=/home/stardust/sai-hpc-software
stamp=${1:-$(date +%Y%m%d-%H%M%S)}
[[ "$stamp" =~ ^[A-Za-z0-9][A-Za-z0-9-]+$ ]]
reference=/opt/apps/abacus/abacus-develop-3.9.0.26_nvhpc263gnu/toolchain
for isa in avx2 avx512; do
  run=$root/runs/elpa-2026.02.001-$stamp-$isa
  [[ ! -e "$run" ]]
  mkdir -p "$run"/{control,input/reference,install,work,runtime,cache}
  cp "$package"/{build.sh,build.sbatch,container.sh,environment.sh,smoke.c,verify.sh,cuda-aware-cpu.patch} "$run/control/"
  cp "$package/../../controller/create_rootfs.sh" "$run/control/"
  cp "$reference/build/elpa-2026.02.001.tar.gz" "$run/input/"
  cp "$reference/scripts/stage3/install_elpa.sh" "$run/input/reference/"
  cp /opt/modules/modulefiles/devtools/elpa/2026.02.001-2603-gnu "$run/input/reference/original-module.tcl"
  sha256sum "$run/control/"* "$run/input/"*.tar.gz > "$run/inputs.sha256"
  if [[ "$isa" == avx2 ]]; then partition=16V100; else partition=4V100; fi
  # SAI assigns CPU resources from the requested GPU count; explicit -c is rejected.
  job=$(sbatch --parsable --partition="$partition" --job-name="elpa-$isa" \
    --output="$run/slurm-%j.out" "$run/control/build.sbatch" "$run" "$isa")
  printf '%s\n' "$job" > "$run/job.id"
  printf '%s %s %s\n' "$isa" "$job" "$run"
done
