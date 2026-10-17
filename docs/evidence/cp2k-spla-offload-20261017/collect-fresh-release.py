#!/usr/bin/env python3
"""Read the four submitted release jobs without changing jobs or images."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

PROJECT = Path('/home/stardust/sai-hpc-software')
WORKFLOW = '39041714911'
CONTROLLER = '2c7fd3e097cf61c0d0ee6612cb2cc96eebb96677'
RECIPE = 'a280393e353fee8faa1c09209b34c42eb0f55d0a44ad0543bed1ed57101735ec'
SOURCE = '67b5da876dd6a76b8b021d5a04d1c81ba79a4c50'


def collect():
    rows = []
    for target in ('16v100-avx2', '4v100-avx512', '8v100v0-avx512', 'dsprhbm'):
        matches = sorted((PROJECT / 'runs').glob(
            WORKFLOW + '-*-release-' + target + '-*-dependencies'))
        if len(matches) != 1:
            raise ValueError('expected one submitted dependency run for ' + target)
        run = matches[0]
        job = (run / 'job.id').read_text().strip()
        request = json.loads((run / 'request.json').read_text())
        identity = request['identity']
        if (not job.isdigit() or request['job_id'] != job or
                identity['source_sha'] != SOURCE or identity['track'] != 'release' or
                identity['recipe_sha256'] != RECIPE or identity['target'] != target or
                Path(request['controller']).parent != PROJECT / 'controller' / CONTROLLER):
            raise ValueError('submitted job identity differs from the reviewed recipe')
        script = (run / 'job.sbatch').read_bytes()
        if hashlib.sha256(script).hexdigest() != request['script_sha256']:
            raise ValueError('submitted script changed')
        results = run / 'results'
        log = results / ('slurm-' + job + '.log')
        raw = log.read_bytes() if log.exists() else b''
        receipt = results / 'image-driver.json'
        rows.append(dict(target=target, run=str(run), job_id=job, request=request,
            accounting=subprocess.check_output(['sacct', '-X', '-nP', '-j', job, '-o',
                'JobIDRaw,Partition,State,Start,Elapsed,AllocCPUS,NodeList,ExitCode'], text=True),
            script_sha256=hashlib.sha256(script).hexdigest(),
            log_sha256=hashlib.sha256(raw).hexdigest(),
            log_tail='\n'.join(raw.decode('utf-8', errors='replace').splitlines()[-40:]),
            image_driver=json.loads(receipt.read_text()) if receipt.exists() else None))
    return dict(schema=1, checked_at=datetime.now(timezone.utc).isoformat(),
                workflow_url='https://github.com/AI4SAI/sai-hpc-software/actions/runs/' + WORKFLOW,
                controller_sha=CONTROLLER, recipe_sha256=RECIPE, source_sha=SOURCE,
                submitted_dependencies=rows, accepted_application=False,
                note='Submitted or running dependency jobs do not establish a completed CP2K application or scientific acceptance.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(PROJECT / 'diagnostics'):
        raise ValueError('evidence must remain in project home diagnostics')
    record = collect()
    with args.output.open('x') as stream:
        json.dump(record, stream, indent=2, sort_keys=True)
        stream.write('\n')
    print(json.dumps(dict(output=str(args.output), jobs=[row['job_id'] for row in record['submitted_dependencies']])))
