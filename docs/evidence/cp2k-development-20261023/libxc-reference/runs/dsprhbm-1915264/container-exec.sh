#!/usr/bin/env bash
set -euo pipefail
driver=${LD_LIBRARY_PATH:-}
source "$1"
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH}${driver:+:$driver}"
export HOME=/runtime TMPDIR=/runtime PYTHONDONTWRITEBYTECODE=1
[[ "${2%/bin/cp2k.psmp}" == /opt/apps/cp2k/cp2k-development-master-2026-10-23-gcfe5e5d87aaf-rae6467ac2934/DSPRHBM ]]
exec /usr/bin/python3 -B /work/libxc_reference.py verify --library /opt/apps/cp2k/cp2k-development-master-2026-10-23-gcfe5e5d87aaf-rae6467ac2934/DSPRHBM/lib64/libcp2k.so.2026.2 --expected-library-sha256 7907da4005c694c73db761351d342dc78102968f2bddf2f5b0ba6abcce145516 --prefix /opt/apps/cp2k/cp2k-development-master-2026-10-23-gcfe5e5d87aaf-rae6467ac2934/DSPRHBM --protocol /work/reference-v1/protocol.json --expected-protocol-sha256 270b59212095643bccf398b2cf74e5a09996222d64f830e7ce6f29800adaecf9 --output /work/results/reference-result.json
