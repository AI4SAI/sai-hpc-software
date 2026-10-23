#!/usr/bin/env python3
"""Stage and execute a separate LibXC reference allocation; never submit here."""
import argparse
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

ROOT = Path('/org/org-open-sai/proj-sai-hpc-software')
SOURCE = 'cfe5e5d87aaf302953f14eb43587bef2411a8fb2'
PROTOCOL = '270b59212095643bccf398b2cf74e5a09996222d64f830e7ce6f29800adaecf9'
CPUS = {'dsprhbm': 2, '4v100-avx512': 8, '16v100-avx2': 8, '8v100v0-avx512': 6}
for location in (Path(__file__).resolve().parent, Path(__file__).resolve().parent.parent):
    if (location / 'libxc_reference.py').is_file():
        sys.path.insert(0, str(location))
        break
import libxc_reference as reference


def modules(control):
    os.environ['SAI_SOFTWARE_ROOT'] = str(ROOT)
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(control))
    return (importlib.import_module('delivery_layout'), importlib.import_module('remote_controller'))


def replace_once(text, before, after):
    reference.require(text.count(before) == 1, 'original runtime launcher differs')
    return text.replace(before, after)


def launcher(control):
    text = (control / 'cp2k').read_text()
    text = replace_once(text,
        'launcher_control=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)',
        'launcher_control=' + shlex.quote(str(control)))
    text = replace_once(text,
        '       --env "OMP_NUM_THREADS=${OMP_NUM_THREADS:-1}")',
        '       --env "OMP_NUM_THREADS=${OMP_NUM_THREADS:-1}"\n'
        '       --bind "$rank_runtime:/tmp:rw" --env HOME=/runtime\n'
        '       --env PYTHONDONTWRITEBYTECODE=1)')
    terminal = '''"${args[@]}" "$image" /usr/bin/bash --noprofile --norc -c \\
  'driver=${LD_LIBRARY_PATH:-}; source "$1"; export LD_LIBRARY_PATH="${LD_LIBRARY_PATH}${driver:+:$driver}"; exec "$2" "${@:3}"' \\
  bash "$prefix/share/sai/runtime-env.sh" "$prefix/bin/cp2k.psmp" "$@"
'''
    reference.require(text.endswith(terminal), 'unexpected final runtime command')
    return text[:-len(terminal)] + '''"${args[@]}" "$image" /usr/bin/bash --noprofile --norc /work/container-exec.sh \\
  "$prefix/share/sai/runtime-env.sh" "$prefix/bin/cp2k.psmp"
'''


def prepare(args):
    control = args.controller.resolve(strict=True)
    reference.require(control.is_relative_to(ROOT / 'controller'), 'expected frozen project controller')
    delivery, remote = modules(control)
    build = ROOT / 'runs' / remote.safe_name(args.build_run_id)
    build_request = json.loads((build / 'request.json').read_text())
    reference.require(build_request['controller'] == str(control), 'build uses another controller')
    image = Path((build / 'artifact.path').read_text().strip())
    manifest = delivery.load_artifact(ROOT, image, software='cp2k')
    identity = manifest['identity']
    reference.require(identity == build_request['identity'] and identity['source_sha'] == SOURCE
                      and identity['track'] == 'development', 'unexpected artifact identity')
    reference.require(image == delivery.artifact_path(ROOT, identity, args.build_run_id), 'wrong build artifact')
    protocol_source = Path(__file__).resolve().parent / 'reference-v1'
    reference.require(reference.sha(protocol_source / 'protocol.json') == PROTOCOL, 'protocol differs')
    reference.require(json.loads((protocol_source / 'protocol.json').read_text()) == reference.protocol_for(protocol_source),
                      'raw reference or protocol differs')
    task = ROOT / 'runtime-tests' / remote.safe_name(args.run_id)
    reference.require(task.resolve() == task, 'task must be canonical')
    task.mkdir(mode=0o700)
    (task / 'results').mkdir()
    shutil.copytree(protocol_source, task / 'reference-v1')
    shutil.copyfile(reference.__file__, task / 'libxc_reference.py')
    shutil.copyfile(__file__, task / 'run_reference.py')
    (task / 'runtime.sh').write_text(launcher(control))
    prefix = Path(identity['install_prefix'])
    library = prefix / args.library_relative
    reference.require(library.is_relative_to(prefix) and '..' not in library.parts,
                      'library must stay in the install prefix')
    inner_argv = ['/usr/bin/python3', '-B', '/work/libxc_reference.py', 'verify',
        '--library', str(library), '--expected-library-sha256', args.library_sha256,
        '--prefix', str(prefix), '--protocol', '/work/reference-v1/protocol.json',
        '--expected-protocol-sha256', PROTOCOL, '--output', '/work/results/reference-result.json']
    (task / 'container-exec.sh').write_text(
        '#!/usr/bin/env bash\nset -euo pipefail\n'
        'driver=${LD_LIBRARY_PATH:-}\nsource "$1"\n'
        'export LD_LIBRARY_PATH="${LD_LIBRARY_PATH}${driver:+:$driver}"\n'
        'export HOME=/runtime TMPDIR=/runtime PYTHONDONTWRITEBYTECODE=1\n'
        '[[ "${2%/bin/cp2k.psmp}" == ' + shlex.quote(str(prefix)) + ' ]]\n'
        'exec ' + shlex.join(inner_argv) + '\n')
    target = identity['target']
    runtime = ROOT / 'runtime' / ('lr-' + reference.hashlib.sha256(str(task).encode()).hexdigest()[:12])
    q = shlex.quote
    lines = ['#!/usr/bin/env bash', '#SBATCH --job-name=cp2k-libxc-ref-' + target,
        '#SBATCH --partition=' + identity['partition'], '#SBATCH --qos=' + remote.project_qos(ROOT, target),
        '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --time=15', '#SBATCH --export=NIL',
        '#SBATCH --output=' + str(task / 'results/slurm-%j.stdout'),
        '#SBATCH --error=' + str(task / 'results/slurm-%j.stderr'),
        *remote.project_batch_directives(ROOT, task)]
    # The SAI submission policy rejects explicit CPUs on GPU allocations.
    # Request the GPU only, then verify its 8/6 logical CPUs once running.
    if identity['gpus']:
        lines += ['#SBATCH --gpus-per-node=1']
    else:
        lines += ['#SBATCH --cpus-per-task=' + str(CPUS[target])]
    lines += ['set -eo pipefail', 'umask 077', 'export HOME=' + q(str(task)),
        'export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}',
        'export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
        'export LD_LIBRARY_PATH="" LD_PRELOAD="" PYTHONDONTWRITEBYTECODE=1',
        'unset PYTHONPATH PYTHONHOME APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
        'export SAI_SOFTWARE_ROOT=' + q(str(ROOT)), 'export SAI_CP2K_IMAGE=' + q(str(image)),
        'export TMPDIR=' + q(str(runtime)), 'mkdir -p "$TMPDIR"',
        'source /etc/profile.d/lmod.sh', 'module purge',
        'module use /opt/modules/modulefiles/devtools /opt/modules/modulefiles/apps',
        'module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto']
    if identity['gpus']:
        lines += ['source /opt/sai_config/mps_mapping.d/' + identity['partition'] + '.bash']
    lines += ['set -u', 'export OMP_NUM_THREADS=1 SLURM_EXPORT_ENV=ALL',
        'scontrol show job -o "$SLURM_JOB_ID" > results/allocation.txt',
        'module -t list > results/modules.txt 2>&1', 'hostname -f > results/node.txt',
        '/usr/bin/python3 -B ' + q(str(task / 'run_reference.py')) + ' execute ' + q(str(task))]
    (task / 'job.sbatch').write_text('\n'.join(lines) + '\n')
    for name in ('runtime.sh', 'container-exec.sh', 'job.sbatch'):
        subprocess.run(['bash', '-n', str(task / name)], check=True)
    hashes = {str(p.relative_to(task)): reference.sha(p) for p in task.rglob('*') if p.is_file()}
    request = dict(schema=1, purpose='independent-libxc-df-repository-reference',
        prepared_at=datetime.now(timezone.utc).isoformat(), identity=identity,
        build_run_id=args.build_run_id, build_request_sha256=reference.sha(build / 'request.json'),
        artifact=str(image), artifact_sha256=manifest['sha256'], controller=str(control),
        controller_hashes={str(p.relative_to(control)): reference.sha(p) for p in control.rglob('*')
                           if p.is_file() and '__pycache__' not in p.parts},
        original_launcher_sha256=reference.sha(control / 'cp2k'), script_hashes=hashes,
        protocol_sha256=PROTOCOL, library=str(library), library_sha256=args.library_sha256,
        inner_argv=inner_argv, outer_argv=['/usr/bin/bash', str(task / 'runtime.sh')],
        resources=dict(account='proj-sai-hpc-software', partition=identity['partition'],
                       nodes=1, tasks=1, gpus=1 if identity['gpus'] else 0,
                       expected_allocated_cpus=CPUS[target], minutes=15),
        scratch=str(runtime), original_science_and_supplement_records_modified=False)
    reference.write_json(task / 'request.json', request)
    for path in task.rglob('*'):
        if path.is_file():
            path.chmod(0o400)
    print(json.dumps(dict(task=str(task), request_sha256=reference.sha(task / 'request.json'),
                         job_script_sha256=hashes['job.sbatch'], resources=request['resources']), sort_keys=True))


def execute(task):
    reference.require(task.resolve() == task and task.parent == ROOT / 'runtime-tests', 'unsafe task')
    request = json.loads((task / 'request.json').read_text())
    control = Path(request['controller'])
    for relative, expected in request['script_hashes'].items():
        reference.require(reference.sha(task / relative) == expected, 'staged input changed: ' + relative)
    for relative, expected in request['controller_hashes'].items():
        reference.require(reference.sha(control / relative) == expected, 'frozen controller changed: ' + relative)
    delivery, _ = modules(control)
    manifest = delivery.load_artifact(ROOT, Path(request['artifact']), software='cp2k',
                                      target=request['identity']['target'])
    reference.require(manifest['identity'] == request['identity'] and manifest['sha256'] == request['artifact_sha256'],
                      'final artifact changed')
    reference.require(os.environ.get('SLURM_JOB_PARTITION') == request['identity']['partition'], 'wrong partition')
    allocation = (task / 'results/allocation.txt').read_text()
    for field, value in [('Account', 'proj-sai-hpc-software'), ('WorkDir', str(task)),
                         ('Partition', request['identity']['partition'])]:
        reference.require(re.search(r'(?:^|\s)' + field + '=' + re.escape(value) + r'(?:\s|$)', allocation),
                          'wrong allocation field: ' + field)
    cpus = re.search(r'(?:^|\s)NumCPUs=(\d+)(?:\s|$)', allocation)
    reference.require(cpus and int(cpus[1]) == request['resources']['expected_allocated_cpus'],
                      'allocation did not receive expected CPUs')
    reference.write_json(task / 'results/execution-started.json',
        dict(started_at=datetime.now(timezone.utc).isoformat(), job_id=os.environ['SLURM_JOB_ID'],
             request_sha256=reference.sha(task / 'request.json'), argv=request['outer_argv']))
    with (task / 'results/candidate.stdout').open('x') as out, (task / 'results/candidate.stderr').open('x') as err:
        result = subprocess.run(request['outer_argv'], cwd=task, stdout=out, stderr=err)
    receipt = dict(returncode=result.returncode, job_id=os.environ['SLURM_JOB_ID'],
                   finished_at=datetime.now(timezone.utc).isoformat(),
                   request_sha256=reference.sha(task / 'request.json'), argv=request['outer_argv'],
                   results_sha256={p.name: reference.sha(p) for p in (task / 'results').iterdir()
                                   if p.is_file() and not p.name.startswith('slurm-')})
    reference.write_json(task / 'results/execution.json', receipt)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--controller', type=Path, required=True)
    prep.add_argument('--build-run-id', required=True)
    prep.add_argument('--run-id', required=True)
    prep.add_argument('--library-relative', required=True)
    prep.add_argument('--library-sha256', required=True)
    execute_parser = commands.add_parser('execute')
    execute_parser.add_argument('task', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        prepare(args)
        return 0
    return execute(args.task)


if __name__ == '__main__':
    raise SystemExit(main())
