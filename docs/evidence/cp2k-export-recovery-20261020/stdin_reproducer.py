#!/usr/bin/env python3
"""Run with the site Open MPI module and a home-backed TMPDIR; no calculation."""
import json
import os
from pathlib import Path
import subprocess
import sys

script = ('mpiexec --allow-run-as-root --bind-to none -n 1 /bin/true\n'
          'echo EXPORT_SENTINEL\necho VERIFY_SENTINEL\n')
report = dict(host=os.uname().nodename, tmpdir=os.environ['TMPDIR'], script=script,
              mpi_version=subprocess.check_output(['mpiexec', '--version'], text=True), cases={})
for mode, argv in (('legacy', ['bash', '-seu', '-o', 'pipefail']),
                   ('isolated', ['bash', '-euc', '-o', 'pipefail', script])):
    result = subprocess.run(argv, input=script if mode == 'legacy' else '',
                            text=True, capture_output=True, timeout=30)
    report['cases'][mode] = dict(returncode=result.returncode, stdout=result.stdout,
        stderr=result.stderr, export_executed='EXPORT_SENTINEL' in result.stdout,
        verify_executed='VERIFY_SENTINEL' in result.stdout)
Path(sys.argv[1]).write_text(json.dumps(report, indent=2) + '\n')
assert all(case['returncode'] == 0 for case in report['cases'].values())
assert not report['cases']['legacy']['export_executed']
assert not report['cases']['legacy']['verify_executed']
assert report['cases']['isolated']['export_executed']
assert report['cases']['isolated']['verify_executed']
print('SITE_MPI_STDIN_REPRODUCED_AND_ISOLATED')
