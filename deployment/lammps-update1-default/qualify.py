#!/usr/bin/env python3
"""Qualify existing NEPAdapters bytes with the installed update1 release."""
import argparse
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parent
PROJECT = Path('/org/org-open-sai/proj-sai-hpc-software')
LAMMPS = Path('/home/stardust/sai-hpc-software/deployment/lammps-30Sep2026-update1')
NEPADAPTERS = Path('/home/stardust/sai-hpc-software/deployment/nepadapters-1.0.5')
MODULE = 'nepadapters/1.0.5-lammps30Sep2026_update1'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--controller', type=Path, required=True)
    parser.add_argument('--partition', choices=('all', '4V100', '16V100', '8V100V0'), default='all')
    args = parser.parse_args()
    sys.path[:0] = [str(args.controller), str(LAMMPS)]
    import nepadapters_native as native
    spec = importlib.util.spec_from_file_location('update1_install', LAMMPS / 'install.py')
    install = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(install)
    release = install.load_release()
    install.preflight(release, installed=True)
    plugin_release = json.loads((NEPADAPTERS / 'release.json').read_text())
    native.require(plugin_release['version'] == '1.0.5'
                   and plugin_release['source_sha'] == '3ceba8f9b69765ac29d9f2f4f1c4f76cee8ecf37',
                   'unexpected NEPAdapters release')
    shared = plugin_release['shared']
    native.require(shared['prefix'] == '/opt/apps/nepadapters/1.0.5-lammps30Sep2026', 'wrong plugin prefix')
    for name, row in shared['files'].items():
        native.require(native.checksum(Path(shared['prefix']) / name) == row['sha256'], 'plugin changed')
    work = PROJECT / 'operations' / ('nepadapters-update1-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    jobs = []
    for row in release['deliveries']:
        part = row['partition']
        if args.partition != 'all' and part != args.partition:
            continue
        pins = {r['path']: r['sha256'] for r in row['files'] if r['kind'] == 'file'
                and (r['path'] == 'bin/lmp' or r['path'].startswith('lib/liblammps.so'))}
        request = dict(project=str(PROJECT), partition=part, layout='shared',
                       lammps=dict(prefix=row['prefix'], identity=row['identity'], files=pins))
        plan = dict(request=request, installed=True, plugins=shared['prefix'] + '/lib',
                    files={Path(k).name: v['sha256'] for k, v in shared['files'].items() if k.startswith('lib/')},
                    lammps_module='lammps/' + install.MODULE.name, nepadapters_module=MODULE,
                    module_source=str(PACKAGE / 'modulefiles' / MODULE),
                    lammps_release_sha256=native.checksum(LAMMPS / 'release.json'),
                    plugin_release_sha256=native.checksum(NEPADAPTERS / 'release.json'))
        output = work / part
        native.submit_plan(plan, NEPADAPTERS / 'cases' / part, output)
        jobs.append(dict(partition=part, directory=str(output), jobs={p: (output / (p + '.id')).read_text().strip()
                                                                    for p in native.PHASES}))
    (work / 'jobs.json').write_text(json.dumps(jobs, indent=2) + '\n')
    print('Results: ' + str(work))


if __name__ == '__main__':
    main()
