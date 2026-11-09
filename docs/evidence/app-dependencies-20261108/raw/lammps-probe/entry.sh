#!/usr/bin/env bash
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
source /control/md_environment.sh 16v100-avx2
export TMPDIR=/runtime
python3 /bench/compile_probe.py "$1"
