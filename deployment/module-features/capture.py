#!/usr/bin/env python3
"""Capture module documentation once in a compute allocation, never during discovery."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys


def run(argv, *, stdin=None, timeout=90):
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as error:
        return dict(command=argv, returncode=127, output=str(error))
    try:
        data, _ = process.communicate((stdin or '').encode(), timeout=timeout)
        code = process.returncode
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        data, _ = process.communicate()
        code = 124
    return dict(command=argv, returncode=code, output=data.decode(errors='replace'))


def capture(item, output):
    result = dict(module=item['module'], partition=os.environ['SLURM_JOB_PARTITION'],
                  job=os.environ['SLURM_JOB_ID'], node=os.environ['SLURMD_NODENAME'],
                  captured=datetime.now(timezone.utc).isoformat(), commands=[], files=[])
    for command in item.get('commands', []):
        result['commands'].append(run(command['argv'], stdin=command.get('stdin')))
    for pattern in item.get('files', []):
        path = Path(os.path.expandvars(pattern))
        try:
            data = path.read_bytes()
            result['files'].append(dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(),
                                        text=data.decode(errors='replace')))
        except OSError as error:
            result['files'].append(dict(path=str(path), error=str(error)))
    result['modulefiles'] = []
    for name in os.environ.get('_LMFILES_', '').split(':'):
        if name:
            p = Path(name)
            result['modulefiles'].append(dict(path=name, sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    output.write_text(json.dumps(result, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--one', type=int)
    args = parser.parse_args()
    if not os.environ.get('SLURMD_NODENAME'):
        raise SystemExit('Capture requires a compute allocation.')
    items = json.loads(args.config.read_text())
    if args.one is not None:
        capture(items[args.one], args.output)
        return
    args.output.mkdir(parents=True, exist_ok=False)
    for index, item in enumerate(items):
        if os.environ['SLURM_JOB_PARTITION'] not in item['partitions']:
            continue
        path = args.output / (item['module'].replace('/', '__') + '.json')
        loads = item.get('loads', [item['module']])
        script = ('source /etc/profile.d/lmod.sh\nmodule purge\n'
                  'module use /opt/modules/modulefiles/apps /opt/modules/modulefiles/devtools\n'
                  'module load ' + shlex.join(loads) + '\n'
                  'exec /usr/bin/python3 -B ' + shlex.join([str(Path(__file__).resolve()),
                      str(args.config), str(path), '--one', str(index)]))
        row = run(['/usr/bin/bash', '-e', '-c', script], timeout=600)
        path.with_suffix('.load.json').write_text(json.dumps(row, indent=2) + '\n')
        print(item['module'], row['returncode'], flush=True)


if __name__ == '__main__':
    main()
