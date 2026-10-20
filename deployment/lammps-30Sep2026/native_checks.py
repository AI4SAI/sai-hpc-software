#!/usr/bin/env python3
"""Submit and reverify physical LAMMPS checks against the accepted SIF results.

Only submit/run reserve or use compute resources. Preflight and verify are
read-only. No subcommand installs software or invokes a container.
"""
import argparse
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

sys.dont_write_bytecode = True
import install as deployment

require = deployment.require
digest = deployment.digest
PACKAGE = Path(__file__).resolve().parent
PARTITIONS = ('4V100', '16V100', '8V100V0')
BACKENDS = ('tf', 'pt', 'jax')
CONTROLS = ('native_checks.py', 'native-rank.sh', 'verify-native.sbatch', 'install.py',
            'release.json', 'native-reference.json', deployment.MODULE.name)
MODES = {'single': 1, 'multi': 2}


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')


def check_files(root, files):
    for name, checksum in files.items():
        path = root / name
        require(not Path(name).is_absolute() and '..' not in Path(name).parts, 'invalid pinned path')
        deployment.plain(path)
        require(path.is_file() and digest(path) == checksum, 'pinned file changed: ' + str(path))


def release_reference():
    release = deployment.load_release()
    reference = read(PACKAGE / 'native-reference.json')
    require(reference['schema'] == 1 and reference['release_sha256'] == digest(PACKAGE / 'release.json'),
            'native reference is for a different release')
    require(set(reference['partitions']) == set(PARTITIONS), 'incomplete native reference')
    for item in release['deliveries']:
        ref = reference['partitions'][item['partition']]
        require(all(ref[key] == item[key] for key in ('build_run', 'image_sha256', 'publication_sha256')),
                'native reference artifact changed')
        require(ref['tolerances'] == {name: dict(atol=1e-7, rtol=1e-5)
                                     for name in ('energy', 'forces', 'virial')}
                and ref['gpu_tolerances'] == dict(atol=2e-6, rtol=2e-5), 'acceptance tolerance changed')
    return release, reference


def preflight(project, selection):
    release, reference = release_reference()
    for partition in selection:
        ref = reference['partitions'][partition]
        check_files(project, {**ref['tools'], **ref['files']})
        print(partition + ': accepted references and parsers unchanged', flush=True)
    return release, reference


def input_sources(ref):
    """Map every frozen helper/model/input to its accepted source and SHA."""
    sources = {}
    for name, checksum in ref['tools'].items():
        sources['helpers/' + Path(name).name] = (name, checksum)
    for name, checksum in ref['case_files'].items():
        sources['inputs/science/' + name] = (ref['science_case'] + '/' + name, checksum)
    for name, case in ref['gpu'].items():
        for filename in ('data.lmp', 'in.lmp'):
            source = case['source'] + '/' + filename
            sources[f'inputs/gpu/{name}/{filename}'] = (source, ref['files'][source])
    return sources


def freeze(output, project, work_root, partition, reference):
    output.mkdir()
    pins = {}
    for filename in CONTROLS:
        source = PACKAGE / filename
        target = output / 'control' / filename
        target.parent.mkdir(exist_ok=True)
        shutil.copyfile(source, target)
        pins[str(target.relative_to(output))] = digest(source)
        target.chmod(0o444)
    ref = reference['partitions'][partition]
    for name, (source, checksum) in input_sources(ref).items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(project / source, target)
        pins[name] = checksum
        target.chmod(0o444)
    check_files(output, pins)
    write(output / 'request.json', dict(schema=1, partition=partition, project=str(project),
          work_root=str(work_root), release_sha256=reference['release_sha256'],
          native_reference_sha256=digest(PACKAGE / 'native-reference.json'), files=pins))
    (output / 'request.json').chmod(0o444)


def home_work_root(value):
    root = Path(value).expanduser().resolve()
    require(root.is_relative_to(Path.home().resolve()), 'work directory must be in the executing account home')
    return root


def submit(selection):
    project = Path(os.environ.get('SAI_SOFTWARE_ROOT', str(deployment.PROJECT))).resolve(strict=True)
    work_root = home_work_root(os.environ.get('SAI_LAMMPS_NATIVE_WORK_ROOT', str(Path.home() / 'sai-hpc-software')))
    release, reference = preflight(project, selection)
    selected = dict(release, deliveries=[d for d in release['deliveries'] if d['partition'] in selection])
    deployment.preflight(selected, project, installed=True)
    root = work_root / 'runtime-tests' / ('lammps-30Sep2026-physical-' +
                                        datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid()))
    root.mkdir(parents=True)
    # Prepare every partition before submitting anything. All destinations are
    # new; failed snapshots/jobs are retained for diagnosis and never reused.
    for partition in selection:
        freeze(root / partition, project, work_root, partition, reference)
    write(root / 'submission.json', dict(partitions=selection, requests={p: digest(root / p / 'request.json')
                                                                     for p in selection}))
    print('Results directory: ' + str(root), flush=True)
    for partition in selection:
        output = root / partition
        command = ['sbatch', '--hold', '--parsable', '--partition=' + partition,
                   '--qos=' + ('flood-1o2gpu' if partition == '16V100' else 'rush-1o2gpu'),
                   '--job-name=lammps-native-' + partition, '--output=' + str(output / 'slurm-%j.log'),
                   str(output / 'control/verify-native.sbatch'), str(output), str(work_root)]
        job = subprocess.check_output(command, text=True).strip()
        require(re.fullmatch(r'[1-9][0-9]*', job), 'unexpected Slurm job handle: ' + job)
        (output / 'job.id').write_text(job + '\n')
        with (root / 'jobs.tsv').open('a') as stream:
            stream.write(f'{partition}\t{job}\t{output}\n')
        print(partition + ': job ' + job + ' recorded; releasing hold', flush=True)
        subprocess.run(['scontrol', 'release', job], check=True)
    print('Verify when jobs complete:\npython3 ' + str(PACKAGE / 'native_checks.py') + ' verify ' + str(root))


def context(output):
    request = read(output / 'request.json')
    partition = request['partition']
    release, reference = release_reference()
    require(request['schema'] == 1 and partition in PARTITIONS and
            request['release_sha256'] == reference['release_sha256'] and
            request['native_reference_sha256'] == digest(PACKAGE / 'native-reference.json'),
            'frozen request differs from this release/reference')
    ref = reference['partitions'][partition]
    inputs = {name: sha for name, (_, sha) in input_sources(ref).items()}
    require(set(request['files']) == set(inputs) | {'control/' + name for name in CONTROLS},
            'incomplete frozen input/control inventory')
    require(all(request['files'][name] == sha for name, sha in inputs.items()), 'input/helper reference changed')
    require(request['files']['control/release.json'] == request['release_sha256'] and
            request['files']['control/native-reference.json'] == request['native_reference_sha256'],
            'frozen release/reference changed')
    check_files(output, request['files'])
    item = next(d for d in release['deliveries'] if d['partition'] == partition)
    specs = {s['identity']['software']: s for s in item['installations']}
    return request, ref, item, specs


def helpers(output):
    modules = []
    for name in ('md_evidence', 'md_science', 'gpu_functional_smoke', 'md_probe'):
        spec = importlib.util.spec_from_file_location(name, output / 'helpers' / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        modules.append(module)
    return modules


def expected_cases(ref):
    return {'version', 'inventory', *('python-' + b for b in BACKENDS),
            *(f'deepmd-{b}-{m}' for b in BACKENDS for m in MODES),
            *(name + '-' + m for name in ref['gpu'] for m in MODES)}


def check_environment(request, specs):
    require(not Path('/.singularity.d').exists(), 'native validation must run on the physical host')
    prefix = specs['lammps']['prefix']
    require(os.environ.get('SLURM_JOB_PARTITION') == request['partition'] and
            os.environ.get('SAI_LAMMPS_NATIVE_PARTITION') == request['partition'] and
            os.environ.get('SAI_LAMMPS_PREFIX') == prefix, 'wrong partition/native module')
    binary = Path(prefix) / 'bin/lmp'
    require(shutil.which('lmp') == str(binary) and binary.open('rb').read(4) == b'\x7fELF',
            'lmp is not the exact installed ELF executable')
    require(os.environ.get('CUDA_VISIBLE_DEVICES', '') not in ('', '-1'), 'allocated GPU is hidden')
    isa = specs['lammps']['identity']['dependency_isa']
    mpi = '/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-' + isa
    require(shutil.which('mpirun') == mpi + '/bin/mpirun', 'MPI path/ISA differs from accepted build')
    return str(binary), mpi


def command_for(output, hosts, arguments):
    return [shutil.which('mpirun'), '-np', str(len(hosts)), '--host', ','.join(hosts),
            '--map-by', 'ppr:1:node:PE=1', '--bind-to', 'core', '--report-bindings',
            'bash', str(output / 'control/native-rank.sh'), *arguments]


def execute(folder, command, environment=None):
    start = time.monotonic()
    with (folder / 'stdout.txt').open('x') as stream:
        try:
            process = subprocess.run(command, cwd=folder, env=environment, stdout=stream,
                                     stderr=subprocess.STDOUT, timeout=600)
            code = process.returncode
        except subprocess.TimeoutExpired:
            code = 124
    write(folder / 'process.json', dict(command=command, returncode=code,
          seconds=time.monotonic() - start, job=os.environ['SLURM_JOB_ID'],
          node=os.uname().nodename.split('.')[0]))
    require(code == 0, 'native process failed: ' + str(folder))


def run(output):
    request, ref, item, specs = context(output)
    work_root = home_work_root(request['work_root'])
    require(output.is_relative_to(work_root / 'runtime-tests'), 'results must be under the home work tree')
    require(Path(os.environ['TMPDIR']).resolve().is_relative_to(work_root / 'runtime'), 'scratch must be under home')
    require((output / 'job.id').read_text().strip() == os.environ['SLURM_JOB_ID'], 'job handle changed')
    _, science, _, _ = helpers(output)
    science.require_execution_context()
    check_environment(request, specs)
    deployment.preflight(dict(deliveries=[item]), Path(request['project']), installed=True)
    hosts = (output / 'nodes.txt').read_text().split()
    require(len(hosts) == len(set(hosts)) == 2, 'two distinct compute nodes required')
    results = output / 'results'
    results.mkdir()

    def lammps(name, nodes, arguments, prepare=None):
        folder = results / name
        folder.mkdir()
        (folder / 'ranks').mkdir()
        if prepare:
            prepare(folder)
        execute(folder, command_for(output, hosts[:nodes], arguments),
                dict(os.environ, SAI_NATIVE_TRACE=str(folder / 'ranks')))
        print(name + ': process completed', flush=True)

    lammps('version', 2, ['-h'])
    inventory_dir = results / 'inventory'
    inventory_dir.mkdir()
    execute(inventory_dir, [specs['deepmd-kit']['prefix'] + '/bin/python', '-B',
                            str(output / 'control/native_checks.py'), 'inventory', str(output)])
    for backend in BACKENDS:
        folder = results / ('python-' + backend)
        folder.mkdir()
        execute(folder, [specs['deepmd-kit']['prefix'] + '/bin/python', '-B',
                        str(output / 'control/native_checks.py'), 'python-eval', str(output), backend])
        fixture = science._load_fixture(output / 'inputs/science', backend)
        for mode, nodes in MODES.items():
            lammps(f'deepmd-{backend}-{mode}', nodes, ['-in', 'in.' + backend, '-log', 'log.lammps'],
                   lambda dest: science.copy_trial_inputs(output / 'inputs/science', dest, backend, fixture))
    for name, case in ref['gpu'].items():
        options = (['-pk', 'gpu', '1', 'split', '1.0'] if case['mode'] == 'gpu' else
                   ['-k', 'on', 'g', '1', 't', '1', '-sf', 'kk', '-pk', 'kokkos', 'gpu/aware', 'off'])
        def copy_gpu(dest):
            for filename in ('data.lmp', 'in.lmp'):
                shutil.copyfile(output / 'inputs/gpu' / name / filename, dest / filename)
        for mode, nodes in MODES.items():
            lammps(name + '-' + mode, nodes, [*options, '-in', 'in.lmp', '-log', 'log.lammps'], copy_gpu)
    report = verify_raw(output)
    report['files'] = raw_inventory(output)
    report['request_sha256'] = digest(output / 'request.json')
    write(output / 'native-acceptance.json', report)
    print('LAMMPS_PHYSICAL_NATIVE_ACCEPTANCE_PASSED', flush=True)


def python_eval(output, backend):
    _, _, _, specs = context(output)
    _, science, _, _ = helpers(output)
    science.require_execution_context()
    import deepmd
    package = Path(deepmd.__file__).resolve()
    require(package.is_relative_to(Path(specs['deepmd-kit']['prefix'])), 'Python imported another DeePMD installation')
    require(deepmd.__version__ == '3.2.0', 'wrong DeePMD Python version')
    resources = dict(nodes=1, ranks=1, gpus_per_node=1, omp_threads=1)
    records = science.run_python(output / 'inputs/science', backend, 'candidate', resources)
    write(Path.cwd() / 'observations.json', dict(backend=backend, python=sys.executable,
          deepmd_file=str(package), deepmd_version=deepmd.__version__, records=records,
          cuda_visible_devices=os.environ.get('CUDA_VISIBLE_DEVICES')))


def inventory(output):
    _, _, _, specs = context(output)
    _, science, _, probe = helpers(output)
    science.require_execution_context()
    write(Path.cwd() / 'features.json', probe.lammps_inventory(specs['lammps']['prefix']))


def check_ranks(folder, hosts, binary, checksum):
    traces = sorted((folder / 'ranks').glob('rank-*.tsv'))
    require(len(traces) == len(hosts), 'missing/extra MPI rank evidence: ' + str(folder))
    rows = []
    for rank, host in enumerate(hosts):
        row = (folder / 'ranks' / f'rank-{rank}.tsv').read_text().strip().split('\t')
        require(len(row) == 6 and row[:3] == [str(rank), host, binary] and
                row[3] not in ('', '-1') and row[4] and row[5] == checksum,
                'MPI rank/node/binary/GPU differs: ' + str(folder))
        rows.append(row)
    return rows


def compare_science(observed, expected, tolerances, evidence):
    require(set(observed) == set(expected) == {'energy', 'forces', 'virial'}, 'missing scientific observable')
    for field, tolerance in tolerances.items():
        evidence._compare_numeric(observed[field], expected[field], **tolerance, path=field)


def check_features(stdout, expected, evidence, body_styles):
    # The upstream help omits /kk/host and /kk/device aliases. It also has
    # an extra Body section and omits the colon on the Command heading.
    # Compare that displayed view here; inventory() checks all C API names.
    bodies = re.findall(r'(?ms)^\* Body styles:\s*\n([^*]+?)(?=^\*)', stdout)
    require(len(bodies) == 1 and sorted(bodies[0].split()) == body_styles, 'Body style registry changed')
    normalized = re.sub(r'(?ms)^\* Body styles:\s*\n[^*]+?(?=^\*)', '', stdout)
    normalized = re.sub(r'(?m)^\* Command styles\s*$', '* Command styles:', normalized)
    observed = evidence.parse_lammps_help(normalized)
    date = re.match(r'(\d{1,2} [A-Za-z]{3} \d{4})(?:\s|$)', observed['version'])
    require(date is not None and datetime.strptime(date[1], '%d %b %Y').strftime('%Y%m%d') == expected['version'],
            'native LAMMPS version changed')
    displayed = {kind: sorted(v for v in values if not v.endswith(('/kk/host', '/kk/device')))
                 for kind, values in expected['styles'].items()}
    require(sorted(observed['packages']) == sorted(expected['packages']) and
            {name: sorted(values) for name, values in observed['styles'].items()} == displayed,
            'native LAMMPS packages/styles changed')
    observed['styles']['body'] = body_styles
    return observed


def check_inventory(observed, expected, prefix):
    require({key: observed[key] for key in ('version', 'packages', 'styles')} == expected,
            'complete C API inventory differs from the accepted build')
    require(Path(observed['library']).is_relative_to(Path(prefix)) and 'not found' not in observed['ldd'],
            'C API used the wrong LAMMPS library')


def verify_raw(output):
    request, ref, _, specs = context(output)
    evidence, science, gpu, _ = helpers(output)
    results = output / 'results'
    require({p.name for p in results.iterdir() if p.is_dir()} == expected_cases(ref), 'incomplete native case matrix')
    hosts = (output / 'nodes.txt').read_text().split()
    require(len(hosts) == len(set(hosts)) == 2, 'two distinct compute nodes required')
    job = (output / 'job.id').read_text().strip()
    require(re.fullmatch(r'[1-9][0-9]*', job), 'invalid job ID')
    prefix = specs['lammps']['prefix']
    binary = prefix + '/bin/lmp'
    isa = specs['lammps']['identity']['dependency_isa']
    mpi = '/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-' + isa
    require((output / 'binary.txt').read_text().strip() == binary and
            (output / 'mpirun.txt').read_text().strip() == mpi + '/bin/mpirun', 'wrong installed executable/MPI')
    samples, placements = {}, {}
    for name in sorted(expected_cases(ref)):
        folder = results / name
        process = read(folder / 'process.json')
        require(process['returncode'] == 0 and process['job'] == job and process['node'] == hosts[0],
                'native process/job failed: ' + name)
        if name == 'inventory':
            require(process['command'] == [specs['deepmd-kit']['prefix'] + '/bin/python', '-B',
                    str(output / 'control/native_checks.py'), 'inventory', str(output)], 'wrong C API probe command')
            observed = read(folder / 'features.json')
            check_inventory(observed, ref['lammps_features'], prefix)
            continue
        if name.startswith('python-'):
            backend = name.removeprefix('python-')
            observation = read(folder / 'observations.json')
            require(process['command'] == [specs['deepmd-kit']['prefix'] + '/bin/python', '-B',
                    str(output / 'control/native_checks.py'), 'python-eval', str(output), backend], 'wrong Python command')
            require(observation['backend'] == backend and observation['deepmd_version'] == '3.2.0' and
                    observation['python'] == specs['deepmd-kit']['prefix'] + '/bin/python' and
                    Path(observation['deepmd_file']).is_relative_to(Path(specs['deepmd-kit']['prefix'])) and
                    observation['cuda_visible_devices'] not in (None, '', '-1'), 'wrong DeePMD Python/GPU environment')
            fixture = science._load_fixture(output / 'inputs/science', backend)
            records = observation['records']
            require(len(records) == 4 and [r['warmup'] for r in records] == [True, False, False, False],
                    'incomplete Python inference observations')
            for record in records:
                require(record['backend'] == backend and record['engine'] == 'python' and
                        record['slurm_job'] == job and record['node'].split('.')[0] == hosts[0] and
                        record['input_sha256'] == fixture['models'][backend]['input_sha256'], 'Python inference identity changed')
                compare_science(record['observables'], ref['science'][backend]['python'], ref['tolerances'], evidence)
                compare_science(record['observables'], fixture['reference'], ref['tolerances'], evidence)
            samples[name] = [r['observables'] for r in records]
            continue
        count = 2 if name == 'version' else MODES[name.rsplit('-', 1)[1]]
        placements[name] = check_ranks(folder, hosts[:count], binary, ref['binary_sha256'])
        command = process['command']
        expected_start = [mpi + '/bin/mpirun', '-np', str(count), '--host', ','.join(hosts[:count]),
                          '--map-by', 'ppr:1:node:PE=1', '--bind-to', 'core', '--report-bindings',
                          'bash', str(output / 'control/native-rank.sh')]
        require(command[:len(expected_start)] == expected_start, 'native MPI launch changed')
        arguments = command[len(expected_start):]
        stdout = (folder / 'stdout.txt').read_text()
        if name == 'version':
            require(arguments == ['-h'], 'wrong feature probe command')
            features = check_features(stdout, ref['lammps_features'], evidence, ref['help_body_styles'])
            for rank in range(2):
                ldd = (folder / 'ranks' / f'ldd-{rank}.txt').read_text()
                require('not found' not in ldd and '/.singularity.d/' not in ldd and
                        'libmpi.so.40 => ' + mpi + '/lib/libmpi.so.40' in ldd and
                        specs['deepmd-kit']['prefix'] + '/lib/libdeepmd' in ldd,
                        'native loader/ISA/pair differs')
                require('V100' in (folder / 'ranks' / f'gpu-{rank}.txt').read_text(), 'V100 probe missing')
                flags = (folder / 'ranks' / f'cpu-{rank}.txt').read_text().split()
                require(('avx2' if request['partition'] == '16V100' else 'avx512f') in flags, 'CPU lacks required ISA')
            continue
        if name.startswith('deepmd-'):
            backend = name.split('-')[1]
            require(arguments == ['-in', 'in.' + backend, '-log', 'log.lammps'], 'wrong DeePMD input command')
            for relative, checksum in ref['case_files'].items():
                if relative in ('data.lmp', 'plumed.dat', 'in.' + backend) or relative.split('/')[0] == science.BACKENDS[backend]:
                    check_files(folder, {relative: checksum})
            observed = science.parse_lammps_output(stdout, (folder / 'result.dump').read_text())
            science.verify_plumed_output((folder / 'COLVAR').read_text(), ref['distance_angstrom'])
            compare_science(observed, ref['science'][backend]['lammps'], ref['tolerances'], evidence)
            samples[name] = observed
        else:
            case_name = name.rsplit('-', 1)[0]
            case = ref['gpu'][case_name]
            options = (['-pk', 'gpu', '1', 'split', '1.0'] if case['mode'] == 'gpu' else
                       ['-k', 'on', 'g', '1', 't', '1', '-sf', 'kk', '-pk', 'kokkos', 'gpu/aware', 'off'])
            require(arguments == [*options, '-in', 'in.lmp', '-log', 'log.lammps'], 'wrong GPU execution options')
            check_files(folder, {f: ref['files'][case['source'] + '/' + f] for f in ('data.lmp', 'in.lmp')})
            if case['mode'] == 'gpu':
                require('(Mixed Precision)' in stdout and '- Using acceleration for' in stdout, 'GPU offload was not reported')
            else:
                require('KOKKOS mode' in stdout and 'using double precision' in stdout and
                        ('coulomb' not in name or 'using double precision cuFFT' in stdout), 'Kokkos CUDA/cuFFT was not reported')
            observed = list(gpu.values(folder))
            evidence._compare_numeric(observed, case['expected'], **ref['gpu_tolerances'], path=name)
            samples[name] = observed
    for backend in BACKENDS:
        compare_science(samples[f'deepmd-{backend}-multi'], samples[f'deepmd-{backend}-single'], ref['tolerances'], evidence)
    for name in ref['gpu']:
        evidence._compare_numeric(samples[name + '-multi'], samples[name + '-single'], **ref['gpu_tolerances'], path=name)
    return dict(schema=1, passed=True, scope='physical native GPU installation; single/two-node correctness; no speed claim',
                partition=request['partition'], job=job, image_sha256=ref['image_sha256'],
                features=ref['lammps_features'], help_features=features, samples=samples, placements=placements)


def raw_inventory(output):
    paths = [p for p in (output / 'results').rglob('*') if p.is_file()]
    paths += [output / name for name in ('modules.txt', 'allocation.txt', 'nodes.txt', 'binary.txt',
                                         'mpirun.txt', 'mpi-version.txt', 'job.id')]
    return {str(p.relative_to(output)): digest(p) for p in sorted(paths)}


def completed_job(job, partition):
    text = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', job,
                                    '--format=JobIDRaw,State,ExitCode,Partition'], text=True)
    rows = [line.split('|') for line in text.splitlines() if line.split('|')[0] == job]
    require(len(rows) == 1 and rows[0][1:4] == ['COMPLETED', '0:0', partition],
            'Slurm job not COMPLETED 0:0 on the expected partition: ' + text.strip())


def verify(root):
    submission = read(root / 'submission.json')
    selection = submission['partitions']
    require(selection and len(selection) == len(set(selection)) and set(selection) <= set(PARTITIONS) and
            set(submission['requests']) == set(selection), 'invalid submission inventory')
    summary, failed = {}, False
    for partition in selection:
        try:
            output = root / partition
            require(digest(output / 'request.json') == submission['requests'][partition], 'submitted request changed')
            report = read(output / 'native-acceptance.json')
            require(report['request_sha256'] == submission['requests'][partition] and
                    report['files'] == raw_inventory(output), 'raw native acceptance evidence changed')
            observed = verify_raw(output)
            require(report == dict(observed, files=report['files'], request_sha256=report['request_sha256']),
                    'stored acceptance differs from raw revalidation')
            require((output / 'module-unloaded.txt').read_text() == 'LAMMPS_NATIVE_MODULE_UNLOADED\n', 'module unload did not pass')
            completed_job(observed['job'], partition)
            summary[partition] = dict(passed=True, job=observed['job'], scientific_cases=len(observed['samples']))
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            failed = True
            summary[partition] = dict(passed=False, error=str(error))
    print(json.dumps(dict(passed=not failed, partitions=summary), indent=2))
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('preflight', 'submit'):
        command = commands.add_parser(name)
        command.add_argument('selection', nargs='?', choices=('all', *PARTITIONS), default='all')
    for name in ('run', 'verify', 'python-eval', 'inventory'):
        command = commands.add_parser(name)
        command.add_argument('output', type=Path)
        if name == 'python-eval':
            command.add_argument('backend', choices=BACKENDS)
    args = parser.parse_args()
    if args.command in ('preflight', 'submit'):
        selection = list(PARTITIONS) if args.selection == 'all' else [args.selection]
        if args.command == 'preflight':
            preflight(Path(os.environ.get('SAI_SOFTWARE_ROOT', str(deployment.PROJECT))).resolve(strict=True), selection)
            print('LAMMPS_NATIVE_REFERENCE_PREFLIGHT_PASSED')
        else:
            submit(selection)
    elif args.command == 'verify':
        return verify(args.output.resolve(strict=True))
    elif args.command == 'run':
        run(args.output.resolve(strict=True))
    elif args.command == 'inventory':
        inventory(args.output.resolve(strict=True))
    else:
        python_eval(args.output.resolve(strict=True), args.backend)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit('Native check stopped: ' + str(error)) from error
