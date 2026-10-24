#!/usr/bin/env python3
"""Copy a tested bundle to the administrator-readable deployment directory."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

PACKAGE = Path(__file__).resolve().parent
CONTROL = PACKAGE.parents[1] / 'controller'
sys.path.insert(0, str(CONTROL))
from sif_modules import install, load_snapshot
from sif_runtime import digest, require

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--bundle', required=True, type=Path)
parser.add_argument('--checks', required=True, type=Path, nargs=4)
parser.add_argument('--destination', required=True, type=Path)
args = parser.parse_args()
snapshot = load_snapshot(args.bundle / 'catalog.json')
reports = [json.loads((directory / 'verification.json').read_text()) for directory in args.checks]
require({r['partition'] for r in reports} == {'16V100', '4V100', '8V100V0', 'DSPRHBM'},
        'all four partition checks are required')
for report in reports:
    require(report['passed'] and report['catalog_sha256'] == digest(args.bundle / 'catalog.json')
            and report['runtime_sha256'] == digest(args.bundle / 'libexec/sif_runtime.py'),
            'checks do not describe the prepared bundle')
    accounting = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', report['job'],
                                         '-o', 'JobID,State,ExitCode'], text=True).strip().splitlines()
    require(accounting == [report['job'] + '|COMPLETED|0:0'], 'check allocation did not complete successfully')
require(not args.destination.exists() and args.destination.resolve() == args.destination,
        'handoff destination must be new and canonical')
args.destination.mkdir(parents=True, mode=0o755)
(args.destination / 'controller').mkdir()
for name in ('sif_runtime.py', 'sif_modules.py', 'module_publication.py', 'release_contract.py',
             'delivery_layout.py', 'remote_controller.py', 'source_cache.py', 'resolve_source.py'):
    shutil.copyfile(CONTROL / name, args.destination / 'controller' / name)
shutil.copyfile(PACKAGE / 'install.sh', args.destination / 'install.sh')
if (PACKAGE / 'README.md').exists():
    shutil.copyfile(PACKAGE / 'README.md', args.destination / 'README.md')
(args.destination / 'validation.json').write_text(json.dumps(reports, indent=2, sort_keys=True) + '\n')
install(snapshot, args.destination / 'payload', copy_images=True)
for path in args.destination.rglob('*'):
    path.chmod(0o755 if path.is_dir() else 0o555 if path.name == 'install.sh' or '/bin/' in str(path) else 0o444)
print('Administrator: cd ' + str(args.destination) + ' && bash install.sh --install')
