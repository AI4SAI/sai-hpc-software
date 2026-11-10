"""Complete this runtime-only handoff after the three recorded Slurm checks."""
import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path('/org/org-open-sai/proj-sai-hpc-software/operations/deepmd-host-data-20261110')
PUBLIC = Path('/home/stardust/sai-hpc-software/sif-beta')
MODULES = Path('/home/stardust/sai-hpc-software/modulefiles/apps')
GENERATION = 'deepmd-host-data-20261110-r1'
sys.path.insert(0, str(ROOT / 'controller'))
import beta_check
import sif_beta
from sif_modules import install, load_snapshot
from sif_runtime import digest, require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs-sha256', required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    pins = ROOT / 'finish-inputs.json'
    require(digest(pins) == args.inputs_sha256, 'completion inputs changed')
    inputs = json.loads(pins.read_text())
    for relative, expected in inputs['files'].items():
        require(digest(ROOT / relative) == expected, 'completion file changed: ' + relative)
    jobs = json.loads((ROOT / 'jobs.json').read_text())
    require({row['partition'] for row in jobs} == {'4V100', '8V100V0', '16V100'}, 'incomplete matrix')
    # In check mode validate setup even while the remaining GPU jobs are queued.
    reports = []
    for row in jobs:
        output = Path(row['output'])
        require(output.is_relative_to(ROOT) and output.resolve() == output, 'redirected check directory')
        require(digest(output / 'job.sbatch') == row['script_sha256']
                and digest(output / 'request.json') == row['request_sha256'], 'check inputs changed')
        request = json.loads((output / 'request.json').read_text())
        for relative, expected in request['files'].items():
            require(digest(output / relative) == expected, 'numerical input changed: ' + relative)
        accounting = subprocess.check_output(['sacct', '-X', '-j', row['job'], '-n', '-P', '-o',
            'JobIDRaw,State,ExitCode,Partition,Account,WorkDir%400'], text=True).strip().splitlines()
        records = [line.split('|') for line in accounting if line.split('|')[0] == row['job']]
        require(len(records) == 1, 'missing or ambiguous job accounting')
        record = records[0]
        require(record[3:] == [row['partition'], 'proj-sai-hpc-software', str(output)],
                'allocation changed project, partition or work directory')
        if args.check and record[1] in ('PENDING', 'RUNNING', 'COMPLETING', 'CONFIGURING'):
            print(row['partition'] + ': waiting for job ' + row['job'], flush=True)
            continue
        require(record[1:3] == ['COMPLETED', '0:0'], 'failed check job: ' + '|'.join(record))
        report = beta_check.verify(output)
        require(report == json.loads((output / 'verification.json').read_text()), 'stored result changed')
        paths = json.loads((output / 'case/paths.json').read_text())
        require(paths['passed'] is True and paths['home'] == '/home/stardust'
                and paths['shared_data'] == str(ROOT / 'our-datasets/fixture.json'),
                'missing home and sibling path acceptance')
        require(Path(paths['output']).is_relative_to(ROOT)
                and Path(paths['output']).read_text() == 'host data write through verified\n',
                'missing host-visible output')
        require(json.loads((output / 'gpu-health.json').read_text())['healthy'] is True,
                'GPU health probe failed')
        reports.append(report)
        print(row['partition'] + ': validated job ' + row['job'], flush=True)
    if args.check:
        print('Completion preflight passed; no publication performed.', flush=True)
        return
    require(len(reports) == 3, 'not all three partitions passed')
    checks = ROOT / 'validation.json'
    beta_check.write(checks, reports)
    snapshot = load_snapshot(ROOT / 'bundle/catalog.json')
    public_bundle = PUBLIC / 'runtimes' / GENERATION
    with (PUBLIC / '.publication.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        link = MODULES / 'deepmd-kit/master-beta-later'
        expected = sif_beta.version_name('deepmd-kit', snapshot)
        if link.resolve().name not in (inputs['previous_fixed'], expected):
            beta_check.write(ROOT / 'publication.json', dict(status='superseded',
                current=link.resolve().name, expected_previous=inputs['previous_fixed']))
            print('A different beta was selected; leaving that selection unchanged.', flush=True)
            return
        if not (public_bundle / 'catalog.json').exists():
            # Existing immutable public SIFs remain in place; only the tiny
            # bootstrap and launch metadata get a new versioned bundle.
            install(snapshot, public_bundle, resume=True)
        sif_beta.prepare(PUBLIC, MODULES, GENERATION, public_bundle,
                         ROOT / 'bundle/catalog.json', checks, ['deepmd-kit'], resume=True)
        sif_beta.select(PUBLIC, GENERATION, ['deepmd-kit'])
        require(link.resolve().name == expected, 'beta did not switch to accepted runtime')
        beta_check.write(ROOT / 'publication.json', dict(status='published', generation=GENERATION,
            fixed=expected, runtime_sha256=snapshot['runtime_sha256'], jobs=jobs,
            images={part:dict(image=row['image'],sha256=row['sha256'])
                    for part,row in snapshot['applications']['deepmd-kit']['partitions'].items()}))
    print('Published ' + expected, flush=True)


if __name__ == '__main__':
    main()
