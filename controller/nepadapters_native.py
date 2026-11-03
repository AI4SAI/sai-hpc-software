#!/usr/bin/env python3
"""Validate exported plugins with the physical LAMMPS and host MPI on V100."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess

from nepadapters_release import PARTITIONS, build_report, require, runtime_request
from nepadapters_science import run as science, verify as verify_science
from remote_controller import (container_command, private_scratch, project_batch_directives,
                               project_qos, site_app_binds)
from source_cache import checksum

CONTROL = Path(__file__).resolve().parent
FILES = ('nepadapters_native.py', 'nepadapters_rank.py', 'nepadapters_science.py',
         'nepadapters_release.py', 'remote_controller.py', 'source_cache.py', 'cp2k_image_driver.py', 'managed_overlay.py')
PHASES = ('local', 'distributed')


def submit(build, output, installed=False, partition=None, exclude=None):
    build, output = build.resolve(), output.resolve()
    original = json.loads((build / 'request.json').read_text())
    request = runtime_request(original, partition or original['partition'])
    accepted = build_report(build)
    require(accepted['accepted'], 'SIF build not accepted')
    require((build / 'export/lib/nepadaptersgpuplugin.so').is_file(), 'missing built plugin')
    plugins = Path(request['prefix']) / 'lib' if installed else build / 'export/lib'
    plan = dict(build=str(build), build_status=accepted, request=request, plugins=str(plugins), installed=installed,
                exclude_nodes=exclude,
                files={name: checksum(plugins / name) for name in ('nepadapterscpuplugin.so', 'nepadaptersgpuplugin.so')})
    submit_plan(plan, build / 'runtime/cases', output)


def submit_plan(plan, cases, output):
    require(not output.exists(), 'native validation directory already exists')
    request = plan['request']
    require(output.is_relative_to(Path(request['project'])), 'put acceptance under the project')
    plan['submit_home'] = str(Path.home())
    plan['scratch'] = {phase: str(Path(request['project']) / 'runtime' /
                                 ('nepa-' + hashlib.sha256(str(output).encode()).hexdigest()[:10] + '-' + phase[0]))
                       for phase in PHASES}
    for path in plan['scratch'].values():
        private_scratch(Path(path))
    output.mkdir(parents=True)
    (output / 'control').mkdir()
    (output / 'runtime').mkdir(mode=0o700)
    shutil.copytree(cases, output / 'cases')
    if 'build' in plan and request.get('layout') == 'shared':
        # container_command constrains writable runtime binds to a run directory.
        runtime = output / 'sif/runs/check/runtime'
        private_scratch(runtime)
        shutil.copytree(cases, runtime / 'cases')
        plan['sif_runtime'] = str(runtime)
        (output / 'sif-request.json').write_text(json.dumps(request, indent=2) + '\n')
    for name in FILES:
        shutil.copyfile(CONTROL / name, output / 'control' / name)
    plan['pins'] = {str(p.relative_to(output)): checksum(p) for root in ('control', 'cases')
                    for p in sorted((output / root).rglob('*')) if p.is_file()}
    if 'sif_runtime' in plan:
        plan['pins']['sif-request.json'] = checksum(output / 'sif-request.json')
    (output / 'request.json').write_text(json.dumps(plan, indent=2) + '\n')
    project, part = Path(request['project']), request['partition']
    for phase in PHASES:
        (output / 'runtime' / phase).mkdir(mode=0o700)
        script = output / (phase + '.sbatch')
        script.write_text(render(output, plan, phase))
    for path in (output / 'request.json', *(output / (p + '.sbatch') for p in PHASES)):
        path.with_suffix(path.suffix + '.sha256').write_text(checksum(path) + '\n')
        path.chmod(0o444)
    for name in plan['pins']:
        (output / name).chmod(0o444)
    # Small-job QoS permits two GPUs TOTAL: use separate allocations.
    for phase in PHASES:
        subprocess.run(['sbatch', '--test-only', str(output / (phase + '.sbatch'))], cwd=output, check=True)
    jobs = {}
    for phase in PHASES:
        job = subprocess.check_output(['sbatch', '--parsable', str(output / (phase + '.sbatch'))],
                                      cwd=output, text=True).strip().split(';')[0]
        require(job.isdigit(), 'invalid native Slurm id')
        (output / (phase + '.id')).write_text(job + '\n')
        jobs[phase] = job
    print(json.dumps(dict(output=str(output), jobs=jobs, partition=part)), flush=True)


def render(output, plan, phase):
    project, part = Path(plan['request']['project']), plan['request']['partition']
    excluded = plan.get('exclude_nodes')
    require(not excluded or re.fullmatch(r'[A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)*', excluded),
            'exclude must be a comma-separated list of node names')
    nodes, per_node = (1, 2) if phase == 'local' else (2, 1)
    lines = ['#!/usr/bin/env bash', '#SBATCH --job-name=nepadapters-native-' + part,
             '#SBATCH --partition=' + part, '#SBATCH --qos=' + project_qos(project, PARTITIONS[part]),
             *(['#SBATCH --exclude=' + excluded] if excluded else []),
             *project_batch_directives(project, output), '#SBATCH --nodes=' + str(nodes),
             '#SBATCH --ntasks=2', '#SBATCH --ntasks-per-node=' + str(per_node),
             '#SBATCH --gpus-per-node=' + str(per_node),
             '#SBATCH --time=30', '#SBATCH --export=NIL',
             '#SBATCH --output=' + str(output / 'slurm-%j.log'),
             'set -eo pipefail', 'export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}',
             'export HOME=' + shlex.quote(plan['submit_home']),
             'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
             'source /etc/profile.d/lmod.sh', 'module purge',
             'module use /opt/modules/modulefiles/apps /opt/modules/modulefiles/devtools',
             'module load lammps/30Sep2026-deepmd320-auto',
             *(['module load apptainer/1.4.4'] if 'sif_runtime' in plan else []),
             *(['module load nepadapters/1.0.4-lammps30Sep2026'] if plan['installed'] else []), 'set -u',
             'export TMPDIR=' + shlex.quote(plan['scratch'][phase]),
             'export APPTAINER_TMPDIR="$TMPDIR" APPTAINER_CACHEDIR="$TMPDIR/apptainer-cache"',
             'export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1',
             'export SLURM_EXPORT_ENV=ALL',
             'export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher',
             'scontrol show job -o "$SLURM_JOB_ID" > ' + shlex.quote(str(output / (phase + '-allocation.txt'))),
             'scontrol show hostnames "$SLURM_JOB_NODELIST" > ' + shlex.quote(str(output / (phase + '-nodes.txt'))),
             shlex.join(['/usr/bin/python3', '-B', str(output / 'control/nepadapters_native.py'), 'run',
                         str(output), '--phase', phase])]
    return '\n'.join(lines) + '\n'


def submit_installed(package, output, partition, exclude=None):
    release = json.loads((package / 'release.json').read_text())
    row = release['partitions'][partition]
    from remote_controller import SAI_PROJECT_ROOT
    request = dict(project=str(SAI_PROJECT_ROOT), partition=partition, lammps=row['lammps'])
    payload = release['shared'] if release.get('schema') == 2 else row
    if release.get('schema') == 2:
        request['layout'] = 'shared'
    plan = dict(request=request, installed=True, plugins=payload['prefix'] + '/lib', exclude_nodes=exclude,
                files={Path(k).name: v['sha256'] for k, v in payload['files'].items() if k.startswith('lib/')})
    submit_plan(plan, package / 'cases' / partition, output)


def context(output):
    plan = json.loads((output / 'request.json').read_text())
    for path in (output / 'request.json', *(output / (p + '.sbatch') for p in PHASES)):
        require(checksum(path) == path.with_suffix(path.suffix + '.sha256').read_text().strip(),
                'native request/job script changed')
    for name, sha in plan['pins'].items():
        require(not Path(name).is_absolute() and '..' not in Path(name).parts and
                checksum(output / name) == sha, 'native controls/cases changed')
    if 'build' in plan:
        require(build_report(Path(plan['build'])) == plan['build_status'], 'native SIF input changed')
        original = json.loads((Path(plan['build']) / 'request.json').read_text())
        require(plan['request'] == runtime_request(original, plan['request']['partition']),
                'native dependencies differ from shared build request')
    return plan


def check_ranks(folder, hosts, per_node, lmp, binary_sha):
    proofs = {}
    for backend, inputs in [('cpu', ('in.baseline',)), ('gpu', ('in.baseline', 'in.dynamics'))]:
        for infile in inputs:
            rows = [json.loads(p.read_text()) for p in sorted((folder / backend / 'ranks').glob(infile + '-*.json'))]
            require(len(rows) == len(hosts) * per_node and
                    {r['rank'] for r in rows} == set(range(len(rows))), 'incomplete MPI rank evidence')
            for host in hosts:
                selected = [r for r in rows if r['host'] == host]
                require(len(selected) == per_node and len({r['gpu_uuid'] for r in selected}) == per_node and
                        {r['local_rank'] for r in selected} == set(range(per_node)), 'wrong MPI node/device mapping')
            for row in rows:
                require(row['binary'] == lmp and row['binary_sha256'] == binary_sha and row['sm'] == 70
                        and row['gpu_uuid'].startswith('GPU-'), 'wrong MPI binary/GPU identity')
            proofs[backend + '/' + infile] = rows
    return proofs


def selections(output, phase):
    hosts = (output / (phase + '-nodes.txt')).read_text().split()
    require(len(hosts) == len(set(hosts)) == (1 if phase == 'local' else 2), 'wrong native node allocation')
    return [('single', hosts, 1), ('multi-gpu', hosts, 2)] if phase == 'local' else [('multi-node', hosts, 1)]


def mpi_binding(output, plan, phase, capture=False):
    """Check that LAMMPS and both plugins resolve the same site MPI library."""
    isa = plan['request']['lammps']['identity']['dependency_isa']
    mpi = Path('/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-' + isa)
    library = (mpi / 'lib/libmpi.so.40').resolve(strict=True)
    paths = {'lmp': Path(plan['request']['lammps']['prefix']) / 'bin/lmp',
             **{name: Path(plan['plugins']) / name for name in plan['files']}}
    for name, binary in paths.items():
        report = output / (phase + '-' + name + '.ldd')
        if capture:
            report.write_text(subprocess.check_output(['ldd', str(binary)], text=True))
        text = report.read_text()
        match = re.search(r'\blibmpi\.so\.40\s+=>\s+(\S+)', text)
        require('not found' not in text and match and Path(match[1]).resolve() == library,
                'LAMMPS/plugin MPI dependency mismatch: ' + name)
    launcher = str(mpi / 'bin/mpirun')
    if capture:
        require(shutil.which('mpirun') == launcher, 'MPI launcher differs from LAMMPS environment')
    return dict(launcher=launcher, library=str(library), library_sha256=checksum(library),
                reports={name: checksum(output / (phase + '-' + name + '.ldd')) for name in paths})


def verify_sif(output, plan, execute=False):
    runtime, request = Path(plan['sif_runtime']), plan['request']
    if execute:
        build = Path(plan['build'])
        binds = [*site_app_binds('lammps'), ('/etc/alternatives', '/etc/alternatives'),
                 (request['lammps']['prefix'], request['lammps']['prefix']),
                 (request['deepmd']['prefix'], request['deepmd']['prefix']),
                 (output / 'sif-request.json', '/input/request.json')]
        argv = container_command(build / 'nepadapters.sif',
            ['/usr/bin/bash', '/control/nepadapters_build.sh', request['target'], 'verify'],
            control=build / 'control', jobs=1, gpu=True, runtime=runtime, extra_binds=binds)
        subprocess.run([x for x in argv if x != '--fakeroot'], check=True)
    proof = verify_science(runtime / 'cases', runtime / 'science-sif')
    require(proof['plugin_sha256'] == plan['files'] and
            proof['plugins'] == request['prefix'] + '/lib' and
            proof['lmp'] == request['lammps']['prefix'] + '/bin/lmp', 'shared SIF tested different plugins/LAMMPS')
    return proof


def verify_phase(output, phase):
    plan = context(output)
    record = json.loads((output / ('acceptance-' + phase + '.json')).read_text())
    job = (output / (phase + '.id')).read_text().strip()
    require(record['passed'] is True and record['job'] == job and
            record['partition'] == plan['request']['partition'] and record['plugin_sha256'] == plan['files'] and
            record['request_sha256'] == checksum(output / 'request.json') and
            record['installed'] == plan['installed'], 'native receipt identity mismatch')
    cases = selections(output, phase)
    require(record['nodes'] == cases[0][1] and set(record['proofs']) == {c[0] for c in cases}, 'incomplete native cases')
    lmp = plan['request']['lammps']['prefix'] + '/bin/lmp'
    for name, selected, per_node in cases:
        case = verify_science(output / 'cases', output / name)
        require(case == record['proofs'][name] and case['lmp'] == lmp and
                case['plugin_sha256'] == plan['files'], 'native numerical proof differs')
        ranks = check_ranks(output / name, selected, per_node, lmp, plan['request']['lammps']['files']['bin/lmp'])
        require(ranks == record['ranks'][name], 'MPI rank evidence changed')
    for name, sha in plan['files'].items():
        require(checksum(Path(plan['plugins']) / name) == sha, 'native plugin changed')
    if plan['request'].get('layout') == 'shared':
        require(record['mpi'] == mpi_binding(output, plan, phase), 'MPI evidence changed')
    if phase == 'local' and 'sif_runtime' in plan:
        require(record['sif'] == verify_sif(output, plan), 'shared SIF evidence changed')
    return record


def verify(output):
    records = {phase: verify_phase(output, phase) for phase in PHASES}
    result = dict(schema=1, passed=True, partition=records['local']['partition'],
                  installed=records['local']['installed'], jobs={p: r['job'] for p, r in records.items()},
                  nodes=records['distributed']['nodes'], phases=records,
                  plugin_sha256=records['local']['plugin_sha256'])
    path = output / 'acceptance.json'
    if path.exists():
        require(json.loads(path.read_text()) == result, 'native aggregate receipt changed')
    else:
        path.write_text(json.dumps(result, indent=2) + '\n')
    return result


def run(output, phase):
    plan = context(output)
    request, plugins = plan['request'], Path(plan['plugins'])
    cases = selections(output, phase)
    hosts = cases[0][1]
    require(os.environ.get('SLURM_JOB_PARTITION') == request['partition'], 'wrong partition')
    lmp = Path(request['lammps']['prefix']) / 'bin/lmp'
    require(shutil.which('lmp') == str(lmp), 'module selected a different LAMMPS')
    for name, sha in request['lammps']['files'].items():
        require(checksum(Path(request['lammps']['prefix']) / name) == sha, 'LAMMPS bytes changed')
    for name, sha in plan['files'].items():
        require(checksum(plugins / name) == sha, 'plugin bytes changed')
    extra = {}
    if request.get('layout') == 'shared':
        extra['mpi'] = mpi_binding(output, plan, phase, capture=True)
    if phase == 'local' and 'sif_runtime' in plan:
        extra['sif'] = verify_sif(output, plan, execute=True)
    proofs, ranks = {}, {}
    for name, selected, per_node in cases:
        # As in lammps_acceptance.command(), PRTE core binding can select a
        # core outside Slurm's remote-node cpuset and abort before exec.
        # The distributed correctness check keeps one rank per node inside
        # that allocation; local two-rank checks retain separate core binding.
        mapping = f'ppr:{per_node}:node' + (':PE=1' if phase == 'local' else '')
        launcher = [shutil.which('mpirun'), '-np', str(len(selected) * per_node),
                    '--host', ','.join(f'{host}:{per_node}' for host in selected),
                    '--map-by', mapping,
                    '--bind-to', 'core' if phase == 'local' else 'none', '--report-bindings',
                    '/usr/bin/python3', '-B', str(output / 'control/nepadapters_rank.py'), str(per_node)]
        # Every rank sees exactly its selected V100, including on the second node.
        proofs[name] = science(output / 'cases', lmp, plugins, output / name, launcher, 1)
        ranks[name] = check_ranks(output / name, selected, per_node, str(lmp), request['lammps']['files']['bin/lmp'])
    record = dict(schema=1, passed=True, installed=plan['installed'], partition=request['partition'],
                  nodes=hosts, job=os.environ['SLURM_JOB_ID'], proofs=proofs, ranks=ranks,
                  request_sha256=checksum(output / 'request.json'), plugin_sha256=plan['files'], **extra)
    (output / ('acceptance-' + phase + '.json')).write_text(json.dumps(record, indent=2) + '\n')
    verify_phase(output, phase)
    print('NEPADAPTERS_NATIVE_' + phase.upper() + '_PASSED', flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('submit')
    s.add_argument('build', type=Path)
    s.add_argument('output', type=Path)
    s.add_argument('--installed', action='store_true')
    s.add_argument('--partition', choices=PARTITIONS)
    s.add_argument('--exclude', help='Comma-separated node names to exclude from both allocations')
    s = sub.add_parser('run')
    s.add_argument('output', type=Path)
    s.add_argument('--phase', choices=PHASES, required=True)
    s = sub.add_parser('verify')
    s.add_argument('output', type=Path)
    s = sub.add_parser('installed')
    s.add_argument('package', type=Path)
    s.add_argument('output', type=Path)
    s.add_argument('--partition', choices=PARTITIONS, required=True)
    s.add_argument('--exclude', help='Comma-separated node names to exclude from both allocations')
    args = p.parse_args()
    if args.command == 'submit':
        submit(args.build, args.output, args.installed, args.partition, args.exclude)
    elif args.command == 'installed':
        submit_installed(args.package.resolve(), args.output.resolve(), args.partition, args.exclude)
    elif args.command == 'run':
        run(args.output, args.phase)
    else:
        from nepadapters_release import require
        for phase in PHASES:
            job = (args.output / (phase + '.id')).read_text().strip()
            require(job.isdigit(), 'invalid native Slurm id')
            accounting = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', job,
                                                  '-o', 'JobIDRaw,State,ExitCode'], text=True)
            row = next((s.split('|') for s in accounting.splitlines() if s.split('|')[0] == job), None)
            if not row or row[1] in ('PENDING', 'RUNNING', 'CONFIGURING', 'COMPLETING', 'SUSPENDED', 'REQUEUED'):
                raise SystemExit(3)
            require(row[1:3] == ['COMPLETED', '0:0'], 'native Slurm job not completed successfully')
        print(json.dumps(verify(args.output)))
