#!/usr/bin/env bash
set -euo pipefail
source /control/environment.sh 16v100-avx2 cp2k
export OMPI_CC=/opt/devtools/gcc/14.3.0/bin/gcc
export ELPA_ROOT=/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx2/nvidia
export LD_LIBRARY_PATH="$ELPA_ROOT/lib:$LD_LIBRARY_PATH"
export PKG_CONFIG_PATH="$ELPA_ROOT/lib/pkgconfig:${PKG_CONFIG_PATH:-}"
cd /workspace
"$MPI_HOME/bin/mpicc" -shared -fPIC -O2 /control/comm-trace.c -ldl -o comm-trace.so
"$MPI_HOME/bin/mpicc" -std=c11 -O2 /control/smoke.c $(pkg-config --cflags --libs elpa_openmp) -lm -o smoke
ldd ./smoke > consumer-ldd.txt
for gpu in 0 1; do
  timeout 120 "$MPI_HOME/bin/mpiexec" --bind-to none -n 1 env LD_PRELOAD=/workspace/comm-trace.so \
    ./smoke "$gpu" 2 0 > "gpu-$gpu.log" 2>&1
  [[ $(grep -c '^ELPA_NUMERIC PASS' "gpu-$gpu.log") == 16 ]]
  grep 'ELPA_COMM_TRACE' "gpu-$gpu.log"
done
