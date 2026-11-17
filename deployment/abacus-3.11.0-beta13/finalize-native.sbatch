#!/usr/bin/env bash
#SBATCH --partition=DSPRHBM
#SBATCH --qos=rush-cpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=1G
#SBATCH --time=00:05:00
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
unset PYTHONHOME PYTHONPATH
/usr/bin/python3 -B - "${1:?native result directory required}" <<'PY'
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

root = Path(sys.argv[1]).resolve(strict=True)
submission = json.loads((root / 'submission.json').read_text())
partition = submission['partitions'][0]
if partition not in ('DSPRHBM', '4V100', '16V100', '8V100V0'):
    raise SystemExit('Unknown partition in submission')
verifier = root / partition / 'control/native_checks.py'
for attempt in range(6):
    process = subprocess.run([sys.executable, '-B', str(verifier), 'verify', str(root)],
                             text=True, capture_output=True)
    report = json.loads(process.stdout)
    accounting_pending = any('Slurm job not COMPLETED' in row.get('error', '')
                             for row in report['partitions'].values())
    if process.returncode == 0 or not accounting_pending or attempt == 5:
        break
    time.sleep(5)
report['verified_at_utc'] = datetime.now(timezone.utc).isoformat()
with (root / 'final-status.json').open('x') as stream:
    json.dump(report, stream, indent=2, sort_keys=True)
    stream.write('\n')
if process.stderr:
    print(process.stderr, file=sys.stderr)
print(json.dumps(report, indent=2, sort_keys=True))
raise SystemExit(process.returncode)
PY
