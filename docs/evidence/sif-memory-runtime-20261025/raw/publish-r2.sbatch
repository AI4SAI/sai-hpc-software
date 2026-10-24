#!/bin/bash
#SBATCH --time=00:12:00
set -eo pipefail
python3 -B - <<'VERIFY'
import hashlib,json
from pathlib import Path
root=Path('/org/org-open-sai/proj-sai-hpc-software/runtime-tests/sif-memory-runtime-20261024/publication-tools-r2')
for name,expected in json.loads((root/'SHA256.json').read_text()).items():
 assert hashlib.sha256((root/name).read_bytes()).hexdigest()==expected,name
VERIFY
python3 -B /org/org-open-sai/proj-sai-hpc-software/runtime-tests/sif-memory-runtime-20261024/publication-tools-r2/deployment/sif-beta/finish-publication.py /org/org-open-sai/proj-sai-hpc-software/runtime-tests/sif-memory-runtime-20261024/publication-plan.json --output /org/org-open-sai/proj-sai-hpc-software/runtime-tests/sif-memory-runtime-20261024/publication-r2
