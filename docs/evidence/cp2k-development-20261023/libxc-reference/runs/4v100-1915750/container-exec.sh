#!/usr/bin/env bash
set -euo pipefail
driver=${LD_LIBRARY_PATH:-}
source "$1"
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH}${driver:+:$driver}"
export HOME=/runtime TMPDIR=/runtime PYTHONDONTWRITEBYTECODE=1
[[ "${2%/bin/cp2k.psmp}" == /opt/apps/cp2k/cp2k-development-master-2026-10-23-gcfe5e5d87aaf-rae6467ac2934/4V100 ]]
exec /usr/bin/python3 -B /work/libxc_reference.py verify --library /opt/apps/cp2k/cp2k-development-master-2026-10-23-gcfe5e5d87aaf-rae6467ac2934/4V100/dependencies/libxc/lib/libxc.so.15 --expected-library-sha256 f35c9ec520c7a46dfd762972af15ffad280825a4d8d7abaf6d6cf9c10f752f78 --prefix /opt/apps/cp2k/cp2k-development-master-2026-10-23-gcfe5e5d87aaf-rae6467ac2934/4V100 --protocol /work/reference-v1/protocol.json --expected-protocol-sha256 270b59212095643bccf398b2cf74e5a09996222d64f830e7ce6f29800adaecf9 --output /work/results/reference-result.json
