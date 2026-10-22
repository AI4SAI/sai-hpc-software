#!/usr/bin/env python3
"""Submit and reverify physical ABACUS calculations against accepted SIF results.

Preflight/verify are read-only; submit schedules jobs. All results and scratch
stay in the executing account's home. No command installs or uses a container.
"""
import argparse
from collections import Counter
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

sys.dont_write_bytecode = True
import install as deployment

require, digest = deployment.require, deployment.digest
PACKAGE = Path(__file__).resolve().parent
PARTITIONS = deployment.PARTITIONS
CONTROLS = ('native_checks.py', 'native-rank.sh', 'verify-native.sbatch', 'install.py',
            'release.json', 'runtime-plan.json', 'native-reference.json', deployment.MODULE.name)


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')


def check_files(root, files):
    for name, checksum in files.items():
        require(not Path(name).is_absolute() and '..' not in Path(name).parts, 'unsafe pinned path')
        path = root / name
        deployment.plain(path)
        require(path.is_file() and digest(path) == checksum, 'pinned file changed: ' + str(path))


def energy(text):
    require('#SCF IS CONVERGED#' in text, 'SCF did not converge')
    values = [float(v) for v in re.findall(r'!FINAL_ETOT_IS\s+(\S+)\s+eV', text)]
    require(values and all(math.isfinite(v) for v in values), 'missing/nonfinite SCF energy')
    return values[-1]


def release_reference():
    release = deployment.load_release()
    reference = read(PACKAGE / 'native-reference.json')
    require(reference['schema'] == 1 and reference['release_sha256'] == digest(PACKAGE / 'release.json') and
            set(reference['partitions']) == set(PARTITIONS), 'wrong/incomplete native reference')
    for delivery in release['deliveries']:
        ref = reference['partitions'][delivery['partition']]
        require(all(ref[k] == delivery[k] for k in ('build_run', 'image_sha256', 'publication_sha256')),
                'native reference artifact changed')
        require(ref['tolerance_ev'] == 1e-5 and set(ref['cases']) ==
                ({'pw', 'hse', 'deepks'} if delivery['partition'] == 'DSPRHBM' else
                 {'pw', 'hse', 'deepks', 'cusolvermp', 'nccl'}), 'case/tolerance changed')
    return release, reference


def preflight(project, selection):
    release, reference = release_reference()
    for partition in selection:
        ref = reference['partitions'][partition]
        check_files(project, ref['files'])
        for case in ref['cases'].values():
            raw = energy((project / case['reference_scf']).read_text())
            require(abs(raw - case['candidate_energy_ev']) < 1e-10 and
                    abs(raw - case['energy_ev']) <= ref['tolerance_ev'], 'raw SIF reference differs')
        print(partition + ': accepted inputs and raw reference outputs unchanged', flush=True)
    return release, reference


def input_sources(ref):
    return {'inputs/' + name + '/' + relative: (row['source'], row['sha256'])
            for name, case in ref['cases'].items() for relative, row in case['inputs'].items()}


def home_work_root(value):
    path = Path(value).expanduser().resolve()
    require(path.is_relative_to(Path.home().resolve()), 'work directory must be in the executing account home')
    return path


def freeze(output, project, work_root, partition, reference):
    output.mkdir()
    pins = {}
    for name in CONTROLS:
        source, destination = PACKAGE / name, output / 'control' / name
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, destination)
        pins['control/' + name] = digest(source)
        destination.chmod(0o444)
    for name, (source, checksum) in input_sources(reference['partitions'][partition]).items():
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(project / source, destination)
        pins[name] = checksum
        destination.chmod(0o444)
    check_files(output, pins)
    write(output / 'request.json', dict(schema=1, partition=partition, project=str(project), work_root=str(work_root),
          release_sha256=reference['release_sha256'], native_reference_sha256=digest(PACKAGE / 'native-reference.json'),
          files=pins))
    (output / 'request.json').chmod(0o444)


def submit(selection):
    project = Path(os.environ.get('SAI_SOFTWARE_ROOT', str(deployment.PROJECT))).resolve(strict=True)
    work_root = home_work_root(os.environ.get('SAI_ABACUS_NATIVE_WORK_ROOT', str(Path.home() / 'sai-hpc-software')))
    release, reference = preflight(project, selection)
    selected = dict(release, deliveries=[d for d in release['deliveries'] if d['partition'] in selection])
    deployment.preflight(selected, project, installed=True)
    root = work_root / 'runtime-tests' / ('abacus-beta11-physical-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid()))
    root.mkdir(parents=True)
    for partition in selection:
        freeze(root / partition, project, work_root, partition, reference)
    write(root / 'submission.json', dict(partitions=selection, requests={p: digest(root / p / 'request.json') for p in selection}))
    print('Results directory: ' + str(root), flush=True)
    for partition in selection:
        output = root / partition
        cpu = partition == 'DSPRHBM'
        resources = (['--ntasks=8', '--ntasks-per-node=4', '--cpus-per-task=2', '--qos=rush-cpu'] if cpu else
                     ['--ntasks=2', '--ntasks-per-node=1', '--gpus-per-node=1', '--qos=flood-1o2gpu'])
        command = ['sbatch', '--hold', '--parsable', '--partition=' + partition, *resources,
                   '--job-name=abacus-native-' + partition, '--output=' + str(output / 'slurm-%j.log'),
                   str(output / 'control/verify-native.sbatch'), str(output), str(work_root)]
        job = subprocess.check_output(command, text=True).strip().split(';')[0]
        require(re.fullmatch(r'[1-9][0-9]*', job), 'invalid Slurm job handle')
        (output / 'job.id').write_text(job + '\n')
        with (root / 'jobs.tsv').open('a') as stream:
            stream.write(f'{partition}\t{job}\t{output}\n')
        print(partition + ': job ' + job + ' recorded; releasing hold', flush=True)
        subprocess.run(['scontrol', 'release', job], check=True)
    print('Verify when complete:\npython3 ' + str(PACKAGE / 'native_checks.py') + ' verify ' + str(root))


def context(output):
    request = read(output / 'request.json')
    release, reference = release_reference()
    partition = request['partition']
    require(request['schema'] == 1 and partition in PARTITIONS and
            request['release_sha256'] == reference['release_sha256'] and
            request['native_reference_sha256'] == digest(PACKAGE / 'native-reference.json'), 'frozen request changed')
    ref = reference['partitions'][partition]
    inputs = {name: sha for name, (_, sha) in input_sources(ref).items()}
    require(set(request['files']) == set(inputs) | {'control/' + name for name in CONTROLS} and
            all(request['files'][name] == sha for name, sha in inputs.items()), 'frozen input inventory changed')
    # Verify the copied controller too when revalidating from the public package.
    require(all(request['files']['control/' + name] == digest(PACKAGE / name) for name in CONTROLS),
            'frozen controller differs from the reviewed deployment package')
    check_files(output, request['files'])
    delivery = next(d for d in release['deliveries'] if d['partition'] == partition)
    return request, ref, delivery


def cases(ref):
    return [(name + '-' + mode, name, nodes) for name in ('pw', 'hse', 'deepks')
            for mode, nodes in (('single', 1), ('multi', 2))] + [
                (name + '-multi', name, 2) for name in ('cusolvermp', 'nccl') if name in ref['cases']]


def case_input(data, name, nodes, partition):
    if name == 'pw' and nodes == 1 and partition != 'DSPRHBM':
        # One GPU rank cannot use the two k-point pools in the two-node fixture.
        data = re.sub(rb'^[ \t]*kpar[ \t]+\S+[^\n]*\n?', b'', data, flags=re.M) + b'\nkpar 1\n'
    return data


def command_for(output, hosts, cpu, arguments):
    ranks_per_node = 4 if cpu else 1
    return [shutil.which('mpirun'), '-np', str(len(hosts) * ranks_per_node), '--host', ','.join(hosts),
            '--map-by', 'ppr:4:node:PE=2' if cpu else 'ppr:1:node', '--bind-to', 'core' if cpu else 'none',
            '--report-bindings', 'bash', str(output / 'control/native-rank.sh'), *arguments]


def execute(folder, command, environment):
    start = time.monotonic()
    with (folder / 'stdout.log').open('x') as stream:
        process = subprocess.run(command, cwd=folder, env=environment, stdout=stream,
                                 stderr=subprocess.STDOUT, timeout=600)
    write(folder / 'process.json', dict(command=command, returncode=process.returncode,
          wall_seconds=time.monotonic() - start, job=os.environ['SLURM_JOB_ID']))
    require(process.returncode == 0, 'native process failed: ' + str(folder))


def run(output):
    request, ref, delivery = context(output)
    spec, = delivery['installations']
    partition, prefix = request['partition'], spec['prefix']
    cpu = partition == 'DSPRHBM'
    require(not Path('/.singularity.d').exists(), 'native checks must run on the physical host')
    require(os.environ.get('SLURM_JOB_PARTITION') == os.environ.get('SAI_ABACUS_NATIVE_PARTITION') == partition and
            os.environ.get('SAI_ABACUS_PREFIX') == os.environ.get('ABACUS_ROOT') == prefix,
            'wrong physical module/partition')
    binary = prefix + '/bin/abacus'
    require(shutil.which('abacus') == binary and Path(binary).open('rb').read(4) == b'\x7fELF', 'wrong physical executable')
    isa = spec['identity']['dependency_isa']
    require(shutil.which('mpirun') == '/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-' + isa + '/bin/mpirun',
            'MPI path/ISA changed')
    work_root = home_work_root(request['work_root'])
    require(output.is_relative_to(work_root / 'runtime-tests'), 'result path outside home work tree')
    require((output / 'job.id').read_text().strip() == os.environ['SLURM_JOB_ID'], 'wrong allocation')
    hosts = (output / 'nodes.txt').read_text().split()
    require(len(hosts) == len(set(hosts)) == 2, 'two nodes required')
    for key, name, nodes in [('version', None, 2), *cases(ref)]:
        folder = output / 'results' / key
        (folder / 'ranks').mkdir(parents=True)
        if name:
            for relative in ref['cases'][name]['inputs']:
                data = (output / 'inputs' / name / relative).read_bytes()
                if relative == 'INPUT':
                    data = case_input(data, name, nodes, partition)
                path = folder / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        env = dict(os.environ, SAI_NATIVE_TRACE=str(folder / 'ranks'))
        execute(folder, command_for(output, hosts[:nodes], cpu, ['--info'] if name is None else []), env)
    observed = verify_raw(output)
    write(output / 'native-acceptance.json', dict(observed, files=raw_inventory(output), request_sha256=digest(output / 'request.json')))
    print('ABACUS_PHYSICAL_NATIVE_ACCEPTANCE_PASSED', flush=True)


def check_loader(text, spec):
    require('not found' not in text, 'unresolved dynamic library')
    prefix, identity = spec['prefix'], spec['identity']
    isa, cpu = identity['dependency_isa'], identity['partition'] == 'DSPRHBM'
    expected = {'libmpi.so': '/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-' + isa + '/lib/',
                'libelpa': '/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-' + isa + ('/cpu/lib/' if cpu else '/nvidia/lib/'),
                'libtorch_cpu.so': prefix + '/dependencies/libtorch/lib/',
                'libnep.so': prefix + '/dependencies/nep/lib/'}
    if not cpu:
        expected.update({'libcusolverMp.so': '/opt/devtools/nvidia/mp_libs/lib/',
                         'libcublasmp.so': '/opt/devtools/nvidia/mp_libs/lib/',
                         'libnccl.so': '/opt/devtools/nvidia/nccl_2.29.3_cuda12.9_sai_v2.29.3-1-sai.2/lib/'})
    for library, root in expected.items():
        require(re.search(re.escape(library) + r'\S*\s+=>\s+' + re.escape(root), text), 'wrong loader path: ' + library)


def verify_raw(output):
    request, ref, delivery = context(output)
    spec, = delivery['installations']
    cpu = request['partition'] == 'DSPRHBM'
    prefix, job = spec['prefix'], (output / 'job.id').read_text().strip()
    require(re.fullmatch(r'[1-9][0-9]*', job), 'invalid native job id')
    plan = deployment.runtime_plan(dict(deliveries=[delivery]))[prefix]
    executable = 'bin/abacus'
    indexed = {row['path']: row for row in plan['files']}
    if indexed[executable]['kind'] == 'symlink':
        executable = str(Path('bin') / indexed[executable]['target'])
    checksum = indexed[executable]['sha256']
    hosts = (output / 'nodes.txt').read_text().split()
    require(len(hosts) == len(set(hosts)) == 2, 'missing two-node allocation')
    require((output / 'binary.txt').read_text().strip() == prefix + '/bin/abacus', 'wrong public executable')
    mpi = '/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-' + spec['identity']['dependency_isa']
    require((output / 'mpirun.txt').read_text().strip() == mpi + '/bin/mpirun', 'wrong MPI executable')
    modules = set((output / 'modules.txt').read_text().split())
    require({'abacus/3.11.0-beta11-auto', 'elpa/2026.02.001-2603-gnu-auto',
             'openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto'} <= modules, 'public module/dependencies missing')
    matrix = [('version', None, 2), *cases(ref)]
    require({p.name for p in (output / 'results').iterdir()} == {row[0] for row in matrix}, 'incomplete native case matrix')
    samples = {}
    for key, name, nodes in matrix:
        folder = output / 'results' / key
        process = read(folder / 'process.json')
        require(process['returncode'] == 0 and process['job'] == job and
                math.isfinite(process['wall_seconds']) and process['wall_seconds'] > 0, 'failed/incomplete native process')
        ranks = nodes * (4 if cpu else 1)
        require({p.name for p in (folder / 'ranks').iterdir()} ==
                {f'{stem}-{r}.{suffix}' for r in range(ranks) for stem, suffix in (('rank', 'tsv'), ('ldd', 'txt'))},
                'missing rank/loader evidence')
        actual = Counter()
        for rank in range(ranks):
            row = (folder / f'ranks/rank-{rank}.tsv').read_text().rstrip('\n').split('\t')
            require(len(row) == 8 and row[0] == str(rank) and row[2] == prefix + '/bin/abacus' and
                    row[4] and row[5] == checksum and row[6] == mpi and row[7] == ('2' if cpu else '1'),
                    'rank executable/threads/MPI differs')
            require(cpu or row[3] not in ('', '-1'), 'allocated GPU hidden')
            actual[row[1]] += 1
            check_loader((folder / f'ranks/ldd-{rank}.txt').read_text(), spec)
        require(actual == Counter({host: 4 if cpu else 1 for host in hosts[:nodes]}), 'incorrect MPI placement')
        stdout = (folder / 'stdout.log').read_text(errors='replace')
        if name is None:
            require('3.11.0-beta11' in stdout and 'ABACUS' in stdout.upper(), 'wrong ABACUS version')
            for label in ref['required_info']:
                require(re.search(r'^' + re.escape(label) + r':\s+yes\b', stdout, re.M), 'missing feature: ' + label)
            continue
        for relative in ref['cases'][name]['inputs']:
            expected = (output / 'inputs' / name / relative).read_bytes()
            if relative == 'INPUT':
                expected = case_input(expected, name, nodes, request['partition'])
            require((folder / relative).read_bytes() == expected, 'run input changed')
        scf = (folder / 'OUT.autotest/running_scf.log').read_text()
        value = energy(scf)
        case = ref['cases'][name]
        require(all(abs(value - case[k]) <= ref['tolerance_ev'] for k in ('energy_ev', 'candidate_energy_ev')),
                'native SCF energy differs: ' + key)
        if case['device'] == 'gpu':
            require(re.search(r'GPU.*\(x' + str(ranks) + r'\)', scf), 'GPU execution missing')
        if name == 'cusolvermp':
            require(any(re.search(r'\bcusolverMp(?:Sygvd|Hegvd)\b', line, re.I) and
                        not re.search('bufferSize|ERROR', line, re.I) for line in stdout.splitlines()), 'actual cuSOLVERMp call missing')
        if name == 'nccl':
            require(re.search(r'\b(AllGather|AllReduce|Broadcast): opCount\s+[^\n]*\[nranks=2\]', stdout), 'actual NCCL collective missing')
        samples[key] = dict(energy_ev=value, reference_error_ev=abs(value - case['energy_ev']),
                            nodes=nodes, ranks=ranks, device=case['device'], wall_seconds=process['wall_seconds'])
    for name in ('pw', 'hse', 'deepks'):
        require(abs(samples[name + '-single']['energy_ev'] - samples[name + '-multi']['energy_ev']) <= ref['tolerance_ev'],
                'single/two-node numerical disagreement: ' + name)
    return dict(passed=True, partition=request['partition'], job=job, samples=samples)


def raw_inventory(output):
    paths = [p for p in (output / 'results').rglob('*') if p.is_file()]
    paths += [output / name for name in ('modules.txt', 'allocation.txt', 'nodes.txt', 'binary.txt', 'mpirun.txt', 'job.id')]
    require(all(not p.is_symlink() for p in paths), 'symlink in runtime evidence')
    return {str(p.relative_to(output)): digest(p) for p in sorted(paths)}


def completed_job(job, partition):
    text = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', job, '--format=JobIDRaw,State,ExitCode,Partition'], text=True)
    rows = [line.split('|') for line in text.splitlines() if line.split('|')[0] == job]
    require(len(rows) == 1 and rows[0][1:4] == ['COMPLETED', '0:0', partition], 'Slurm job not COMPLETED 0:0: ' + text.strip())


def verify(root):
    submission = read(root / 'submission.json')
    selection = submission['partitions']
    require(selection and len(selection) == len(set(selection)) and set(selection) <= set(PARTITIONS) and
            set(submission['requests']) == set(selection), 'invalid submission inventory')
    summary = {}
    for partition in selection:
        try:
            output = root / partition
            require(digest(output / 'request.json') == submission['requests'][partition], 'submitted request changed')
            report = read(output / 'native-acceptance.json')
            require(report['request_sha256'] == submission['requests'][partition] and report['files'] == raw_inventory(output),
                    'native evidence changed')
            observed = verify_raw(output)
            require(report == dict(observed, files=report['files'], request_sha256=report['request_sha256']), 'stored acceptance differs')
            require((output / 'module-unloaded.txt').read_text() == 'ABACUS_NATIVE_MODULE_UNLOADED\n', 'module unload did not pass')
            completed_job(observed['job'], partition)
            summary[partition] = dict(passed=True, job=observed['job'], scientific_cases=len(observed['samples']))
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            summary[partition] = dict(passed=False, error=str(error))
    passed = all(row['passed'] for row in summary.values())
    print(json.dumps(dict(passed=passed, partitions=summary), indent=2))
    return 0 if passed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('preflight', 'submit'):
        command = commands.add_parser(name)
        command.add_argument('selection', nargs='?', choices=('all', *PARTITIONS), default='all')
    for name in ('run', 'verify'):
        commands.add_parser(name).add_argument('output', type=Path)
    args = parser.parse_args()
    if args.command in ('preflight', 'submit'):
        selection = list(PARTITIONS) if args.selection == 'all' else [args.selection]
        if args.command == 'preflight':
            preflight(Path(os.environ.get('SAI_SOFTWARE_ROOT', str(deployment.PROJECT))).resolve(strict=True), selection)
            print('ABACUS_NATIVE_REFERENCE_PREFLIGHT_PASSED')
        else:
            submit(selection)
    elif args.command == 'run':
        run(args.output.resolve(strict=True))
    else:
        return verify(args.output.resolve(strict=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit('Native check stopped: ' + str(error)) from error
