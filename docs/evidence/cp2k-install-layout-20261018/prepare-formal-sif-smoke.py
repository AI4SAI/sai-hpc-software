#!/usr/bin/env python3
"""Prepare (never submit) a three-case PBE runtime smoke for workflow 39078998459.

Run on SAI-stardust: python3 this-file.py BUILD_RUN_ID --name pbe-sif-20261018-a
The completed formal artifact is required. The generated job uses its frozen
launcher and installed runtime. OT and diagonalization use pinned local data;
a third OT case uses the identical installed data without local copies.
This is not scientific/performance/publication acceptance.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path('/home/stardust/sai-hpc-software')
WORKFLOW = '39078998459'
CONTROLLER_SHA = 'c3b65b97f9d95ecb29a453713dfe292af20f6d39'
RECIPE = 'b59c9f26a1846d5a3863c04cefc0f7b746b3078bc48cb0a8138d67084d28e150'
SOURCE_SHA = '67b5da876dd6a76b8b021d5a04d1c81ba79a4c50'
RUNTIME_SHA = '21e5404e8b750c5f1526905ec59fa26e84dd816d36b46148ff310e3aa0b574c0'
FIXTURE = ROOT / 'runs/cp2k-pbe-smoke-20261018-b/input'
FIXTURE_HASHES = {
    'ot.inp': '580c31859e98eb4524c978fb0c556f3179b26d3203cba398b35654eaf78b93f0',
    'diagonalization.inp': '7340da90d70f2768cfcb3bdb06a610360c16ba5533e4511e2b990c57de7e146a',
    'data/BASIS_MOLOPT': '686dd72e7601ee41d4a173e4c7c3946f1e750f03095090f44d3821c180a81a61',
    'data/GTH_POTENTIALS': '5a9bfbc3b37a55917ade4fb704b80546d210d843759b94e3915e74acfc3fafb2',
}
SCOPE = 'formal-sif-runtime-smoke-only-not-scientific-performance-or-publication-acceptance'


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def regular(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and path.resolve() == path,
            'not a canonical regular file: ' + str(path))
    return path


def digest(path):
    h = hashlib.sha256()
    with regular(path).open('rb') as source:
        for block in iter(lambda: source.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def installed_data(artifact, identity):
    """Read exact data bytes from the SIF without executing or extracting it."""
    from export_native import SquashfsReader
    reader = SquashfsReader.from_sif(artifact)
    prefix = identity['install_prefix']
    environment_path = prefix.lstrip('/') + '/share/sai/runtime-env.sh'
    environment = subprocess.check_output(reader.command([environment_path]))
    assignments = [shlex.split(line)[1].partition('=')[2]
                   for line in environment.decode().splitlines()
                   if line.startswith('export CP2K_DATA_DIR=')]
    directory = prefix + '/share/cp2k/data'
    require(assignments == [directory], 'installed CP2K_DATA_DIR differs from the exact prefix')
    records = {}
    for name in ('BASIS_MOLOPT', 'GTH_POTENTIALS'):
        content = subprocess.check_output(reader.command([directory.lstrip('/') + '/' + name]))
        checksum = hashlib.sha256(content).hexdigest()
        require(checksum == FIXTURE_HASHES['data/' + name], 'installed data differs from pinned fixture: ' + name)
        records[name] = dict(bytes=len(content), sha256=checksum)
    return dict(directory=directory, files=records,
                runtime_environment_sha256=hashlib.sha256(environment).hexdigest(),
                method='Read-only SquashfsReader from the frozen formal controller')


def completed_build(job, partition):
    require(re.fullmatch(r'[0-9]+', job), 'invalid build job id')
    output = subprocess.check_output([
        'sacct', '-X', '-nP', '-j', job, '-o',
        'JobIDRaw,Partition,State,ExitCode,Elapsed,NodeList,User'], text=True)
    rows = [line.split('|') for line in output.splitlines() if line.split('|')[0] == job]
    require(len(rows) == 1, 'missing or ambiguous build Slurm accounting')
    row = rows[0]
    require(row[1:4] == [partition, 'COMPLETED', '0:0'] and row[6] == 'stardust',
            'formal build has not completed successfully: ' + '|'.join(row))
    return '|'.join(row)


def render_job(task, lock):
    q = shlex.quote
    partition = lock['identity']['partition']
    # Keep MPI socket paths short even when the workflow run name is long.
    tmp = ROOT / 'runtime' / ('ps-' + hashlib.sha256(str(task).encode()).hexdigest()[:8])
    return '\n'.join([
        '#!/usr/bin/env bash', '#SBATCH --job-name=cp2k-formal-sif-pbe-smoke',
        '#SBATCH --partition=' + partition, '#SBATCH --qos=flood-1o2gpu',
        '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --ntasks-per-node=1',
        '#SBATCH --gpus-per-node=1', '#SBATCH --time=15', '#SBATCH --export=NIL',
        '#SBATCH --output=' + str(task / 'results/slurm-%j.log'),
        'set -euo pipefail', 'umask 077',
        'export HOME=/home/stardust',
        'export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}',
        '[[ "$USER" == stardust ]] || exit 2',
        'export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
        'export LD_LIBRARY_PATH="" LD_PRELOAD=""',
        'export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1',
        'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
        'export SAI_SOFTWARE_ROOT=' + q(str(ROOT)),
        'export SAI_CP2K_VERSION=' + q(lock['identity']['source_version']),
        'export SAI_CP2K_IMAGE=' + q(lock['artifact']),
        'export TMPDIR=' + q(str(tmp)),
        'export APPTAINER_TMPDIR=' + q(str(task / 'apptainer-runtime')),
        'export APPTAINER_CACHEDIR=' + q(str(task / 'apptainer-cache')),
        'mkdir -p "$TMPDIR" "$APPTAINER_TMPDIR" "$APPTAINER_CACHEDIR"',
        'set +u', 'source /etc/profile.d/lmod.sh', 'module purge',
        'export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"',
        'module use /opt/modules/modulefiles/devtools /opt/modules/modulefiles/apps',
        'module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto',
        'source /opt/sai_config/mps_mapping.d/' + partition + '.bash', 'set -u',
        'export OMP_NUM_THREADS=1 OMP_PLACES=cores OMP_PROC_BIND=close',
        'export SLURM_EXPORT_ENV=ALL',
        'export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher',
        'cd ' + q(str(task)), 'sha256sum --check --status prepared.sha256',
        'scontrol show job -o "$SLURM_JOB_ID" > results/allocation.txt',
        'hostname -f > results/node.txt', 'nvidia-smi > results/nvidia-smi.txt',
        'module -t list > results/modules.txt 2>&1',
        'mpirun --version > results/mpi-version.txt',
        'exec python3 input/runner.py --execute ' + q(str(task)), '',
    ])


def prepare(run_name, name):
    require(re.fullmatch(WORKFLOW + r'-1-release-(4v100-avx512|16v100-avx2|8v100v0-avx512)-2026-10-17-67b5da876dd6', run_name),
            'only GPU application runs from the pinned formal workflow are allowed')
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]{0,39}', name), 'invalid short smoke name')
    require(ROOT.resolve() == ROOT, 'noncanonical software root')
    run = ROOT / 'runs' / run_name
    request_path = regular(run / 'request.json')
    request = json.loads(request_path.read_text())
    control = ROOT / 'controller' / CONTROLLER_SHA / run_name
    require(request['controller'] == str(control), 'unexpected frozen controller')
    require(control.is_dir() and control.resolve() == control, 'noncanonical controller')
    sys.path.insert(0, str(control))
    from delivery_layout import CONTRACT_SCHEMA, artifact_path, load_artifact, validate_record
    from software_controller import recipe_fingerprint
    identity = validate_record(request)
    require(request['run_id'] == run_name and request['contract_schema'] == CONTRACT_SCHEMA,
            'build request does not match run/contract')
    require(identity['software'] == 'cp2k' and identity['track'] == 'release'
            and identity['source_sha'] == SOURCE_SHA and identity['source_version'] == 'v2026.2'
            and identity['recipe_sha256'] == RECIPE, 'unexpected formal artifact identity')
    require(recipe_fingerprint('cp2k', control) == RECIPE, 'frozen recipe fingerprint changed')
    # Deployment renames cp2k_runtime.sh to cp2k; require exact source runtime bytes.
    launcher = regular(control / 'cp2k')
    require(digest(launcher) == RUNTIME_SHA, 'not the pinned cp2k_runtime.sh implementation')
    job = regular(run / 'job.id').read_text().strip()
    accounting = completed_build(job, identity['partition'])
    status_path = regular(run / 'results/status.json')
    status = json.loads(status_path.read_text())
    require(status == {'job': job, 'state': 'COMPLETED', 'exit_code': '0:0', 'verified': True},
            'formal controller has not verified the completed build')
    artifact = Path(regular(run / 'artifact.path').read_text().strip())
    require(artifact == artifact_path(ROOT, identity, run_name), 'noncanonical formal artifact')
    manifest = load_artifact(ROOT, artifact, software='cp2k', target=identity['target'])
    require(manifest['identity'] == identity and manifest['controller'] == str(control),
            'sidecar differs from the formal request')
    log = regular(run / 'results' / ('slurm-' + job + '.log'))
    require('BUILD_AND_ARTIFACT_VERIFIED' in log.read_text(errors='replace'),
            'missing completed SIF verification marker')
    for relative, expected in FIXTURE_HASHES.items():
        require(digest(FIXTURE / relative) == expected, 'successful PBE fixture changed: ' + relative)
    installed = installed_data(artifact, identity)
    # Pin the complete frozen controller, including modules imported by the launcher/parser.
    locked_paths = [request_path, run / 'job.id', status_path, run / 'artifact.path',
                    run / 'job.sbatch', log, artifact.with_suffix('.json')]
    locked_paths += [p for p in sorted(control.rglob('*')) if p.is_file() and '__pycache__' not in p.parts]
    hashes = {str(p): digest(p) for p in locked_paths}
    task = ROOT / 'runtime-tests' / name
    require(task.resolve() == task and not task.exists(), 'smoke task already exists or has symlink parents')
    lock = dict(schema=1, scope=SCOPE, prepared_at=datetime.now(timezone.utc).isoformat(),
                workflow=WORKFLOW, build_run=str(run), build_job_id=job,
                build_accounting=accounting, identity=identity, artifact=str(artifact),
                artifact_sha256=manifest['sha256'], artifact_bytes=artifact.stat().st_size,
                controller=str(control), launcher=str(launcher), launcher_sha256=RUNTIME_SHA,
                fixture_source=str(FIXTURE), fixture_hashes=FIXTURE_HASHES,
                fixture_note='OT and diagonalization use pinned local data; ot-installed-data omits local copies and exercises installed-data lookup.',
                installed_data=installed,
                locked_files=hashes)
    script = render_job(task, lock)
    subprocess.run(['bash', '-n'], input=script, text=True, check=True)
    task.mkdir(mode=0o700)
    (task / 'input/data').mkdir(parents=True)
    (task / 'results/cases').mkdir(parents=True)
    for relative in FIXTURE_HASHES:
        shutil.copyfile(FIXTURE / relative, task / 'input' / relative)
    shutil.copyfile(Path(__file__).resolve(), task / 'input/runner.py')
    write_json(task / 'lock.json', lock)
    (task / 'job.sbatch').write_text(script)
    prepared = [task / 'lock.json', task / 'job.sbatch', task / 'input/runner.py']
    prepared += [task / 'input' / relative for relative in FIXTURE_HASHES]
    (task / 'prepared.sha256').write_text(''.join(
        digest(path) + '  ' + str(path.relative_to(task)) + '\n' for path in prepared))
    print(json.dumps(dict(prepared=str(task), scope=SCOPE, submitted=False,
                         build_job=job, artifact=str(artifact), artifact_sha256=manifest['sha256'],
                         submit_command='sbatch --parsable ' + shlex.quote(str(task / 'job.sbatch'))), indent=2))


def check_lock(task, lock):
    for filename, expected in lock['locked_files'].items():
        require(digest(Path(filename)) == expected, 'locked formal file changed: ' + filename)
    for relative, expected in lock['fixture_hashes'].items():
        require(digest(task / 'input' / relative) == expected, 'prepared fixture changed: ' + relative)
    require(digest(Path(lock['artifact'])) == lock['artifact_sha256'], 'formal SIF changed')


def execute(task):
    task = Path(task)
    require(task.resolve() == task and task.parent == ROOT / 'runtime-tests', 'invalid smoke directory')
    lock = json.loads(regular(task / 'lock.json').read_text())
    require(lock['workflow'] == WORKFLOW and lock['scope'] == SCOPE, 'wrong smoke lock')
    require(os.environ.get('SLURM_JOB_PARTITION') == lock['identity']['partition'], 'wrong partition')
    require(os.environ.get('SLURM_JOB_NUM_NODES') == '1'
            and os.environ.get('SLURM_NTASKS') == '1', 'smoke requires one node and one MPI rank')
    check_lock(task, lock)
    before = completed_build(lock['build_job_id'], lock['identity']['partition'])
    sys.path.insert(0, lock['controller'])
    from cp2k_benchmark import parse_science, compare_science, ENERGY_TOLERANCE_HA, FORCE_TOLERANCE_HA_BOHR
    cases = {}
    for name, input_name, local_data in (
            ('ot', 'ot', True), ('diagonalization', 'diagonalization', True),
            ('ot-installed-data', 'ot', False)):
        work = task / 'results/cases' / name
        work.mkdir()
        trace = work / 'ranks'
        trace.mkdir()
        shutil.copyfile(task / 'input' / (input_name + '.inp'), work / 'input.inp')
        if local_data:
            for data in ('BASIS_MOLOPT', 'GTH_POTENTIALS'):
                shutil.copyfile(task / 'input/data' / data, work / data)
        env = dict(os.environ, SAI_CP2K_TRACE_DIR=str(trace))
        started = time.monotonic()
        result = dict(input_sha256=digest(work / 'input.inp'), passed=False,
                      data_source='pinned-local-fixture' if local_data else 'installed-sif-data')
        try:
            if not local_data:
                require(result['input_sha256'] == cases['ot']['input_sha256'],
                        'installed-data case differs from the reference OT input')
                require(all(not (work / data).exists() and not (work / data).is_symlink()
                            for data in lock['installed_data']['files']),
                        'installed-data case unexpectedly has local data files')
                result.update(installed_data=lock['installed_data'], local_data_absent_before=True)
            with (work / 'stdout.log').open('w') as out, (work / 'stderr.log').open('w') as err:
                process = subprocess.run(['mpirun', '-np', '1', '--map-by', os.environ['MAP_OPT'],
                                          '--report-bindings', lock['launcher'],
                                          '-i', 'input.inp', '-o', 'output.log'],
                                         cwd=work, env=env, stdout=out, stderr=err)
            elapsed = time.monotonic() - started
            (work / 'elapsed.txt').write_text(str(elapsed) + '\n')
            result['exit_code'] = process.returncode
            require(process.returncode == 0, 'CP2K/MPI exit ' + str(process.returncode))
            result['science'] = parse_science((work / 'output.log').read_text(),
                                             (work / 'forces.xyz').read_text(), str(elapsed), 'water-gpw')
            rows = list(trace.glob('rank-*.tsv'))
            require(len(rows) == 1, 'expected exactly one SIF launcher rank trace')
            fields = rows[0].read_text().strip().split('\t')
            require(len(fields) == 6 and fields[1:4] == ['0', lock['identity']['target'], lock['artifact']],
                    'SIF launcher trace differs from locked artifact')
            if not local_data:
                require(all(not (work / data).exists() and not (work / data).is_symlink()
                            for data in lock['installed_data']['files']),
                        'installed-data case created unexpected local data files')
                result['local_data_absent_after'] = True
            result.update(passed=True, normal_termination=True, scf_converged=True, finite_energy=True)
        except Exception as exc:
            result['error'] = type(exc).__name__ + ': ' + str(exc)
        cases[name] = result
        write_json(work / 'result.json', result)
        print(name.upper() + ' ' + json.dumps(result), flush=True)
    report = dict(schema=1, scope=SCOPE, passed=False, workflow=WORKFLOW,
                  slurm_job_id=os.environ['SLURM_JOB_ID'], artifact=lock['artifact'],
                  artifact_sha256=lock['artifact_sha256'], build_job_id=lock['build_job_id'],
                  build_accounting=before, energy_tolerance_ha=ENERGY_TOLERANCE_HA,
                  force_tolerance_ha_bohr=FORCE_TOLERANCE_HA_BOHR, cases=cases)
    try:
        check_lock(task, lock)
        require(all(item['passed'] for item in cases.values()), 'a PBE case failed')
        report['comparison'] = compare_science(cases['ot']['science'], cases['diagonalization']['science'])
        report['installed_data_comparison'] = compare_science(cases['ot']['science'], cases['ot-installed-data']['science'])
        report.update(passed=True, formal_files_unchanged=True)
    except Exception as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)
    write_json(task / 'results/smoke-result.json', report)
    print('FORMAL_SIF_PBE_SMOKE ' + json.dumps(report), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build_run_id', nargs='?')
    parser.add_argument('--name', default='pbe-sif-20261018-a')
    parser.add_argument('--execute', metavar='TASK', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.execute:
        sys.exit(execute(args.execute))
    if not args.build_run_id:
        parser.error('build_run_id is required; this command prepares but never submits')
    prepare(args.build_run_id, args.name)
