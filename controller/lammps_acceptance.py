#!/usr/bin/env python3
"""Single/two-node SIF acceptance for LAMMPS with installed DeePMD.

Reuse frozen inputs and numerical references from the accepted release; no
companion rebuild, installation or publication is performed. New work and
scratch use the selected project root; frozen reference inputs remain read-only.
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

from remote_controller import (SAI_PROJECT_ACCOUNT, SAI_PROJECT_ROOT, private_scratch,
                               project_batch_directives, safe_name)

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parent
PROJECT = Path(os.environ.get('SAI_SOFTWARE_ROOT', str(SAI_PROJECT_ROOT))).resolve()
ROOT = PROJECT / 'experimental/deepmd-lammps'
REFERENCE_ROOT = Path('/home/stardust/sai-hpc-software')
REFERENCE_SHA256 = 'e5f75411c7891cb4df63bb71410daa6f21e5c91b6ee7bb8f987b41e4e0880478'
BACKENDS = ('tf', 'pt', 'jax')
MODES = {'single': 1, 'multi': 2}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(8 * 1024 * 1024):
            value.update(block)
    return value.hexdigest()


def write(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')


def check_files(root, files):
    for name, checksum in files.items():
        path = root / name
        require(not Path(name).is_absolute() and '..' not in Path(name).parts,
                'invalid relative path: ' + name)
        require(path.is_file() and not path.is_symlink() and digest(path) == checksum,
                'pinned file changed: ' + str(path))


def build_status(build):
    """Validate the original recipe using its own frozen controller."""
    require(build.parent == ROOT / 'runs' and build.resolve() == build, 'invalid build directory')
    for name in ('request.json', 'job.sbatch', 'job.id', 'results/status.json'):
        path = build / name
        require(path.is_file() and path.resolve() == path, 'untrusted build metadata: ' + name)
    record = read(build / 'request.json')
    control = Path(record['controller'])
    require(control.is_relative_to(ROOT / 'controller') and control.resolve() == control and control.is_dir(),
            'invalid frozen build controller')
    # Older frozen status entrypoints used squeue -j, which fails once the job
    # ages out of slurmctld. Keep that controller immutable: validate its recipe
    # and dependency with its own helpers, then check durable sacct evidence.
    code = '''import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import md_lammps_standalone as original
record = json.loads((Path(sys.argv[2]) / 'request.json').read_text())
delivery = original.validate_delivery(record['delivery'])
dep, files = original.dependency(delivery['identity']['target'])
original.verify_dependency(dep, files)
print(json.dumps(delivery, sort_keys=True))
'''
    delivery = json.loads(subprocess.check_output(
        ['/usr/bin/python3', '-I', '-B', '-c', code, str(control), str(build)], text=True,
        env=dict(os.environ, SAI_SOFTWARE_ROOT=str(PROJECT))))
    status = read(build / 'results/status.json')
    job = (build / 'job.id').read_text().strip()
    require(re.fullmatch(r'[1-9][0-9]*', job), 'invalid build job handle')
    row = accounting(job)
    require(row is not None and row[1:4] == ['COMPLETED', '0:0', delivery['identity']['partition']],
            'build accounting is not successful')
    check_allocation(row, build)
    require(status['job'] == job and delivery == status['delivery'] and
            digest(build / 'job.sbatch') == record['job_script_sha256'], 'build provenance changed')
    require('LAMMPS_STANDALONE_BUILD_VERIFIED_NOT_PUBLISHED' in
            (build / ('results/slurm-' + job + '.log')).read_text(), 'missing completed-build marker')
    require(status['build_verified'] is True and status['state'] == 'COMPLETED'
            and status['exit_code'] == '0:0' and status['delivery'] == record['delivery'],
            'standalone build is not verified')
    image = build / 'candidate.sif'
    require(status['artifact'] == str(image) and image.resolve() == image and image.is_file(),
            'candidate escaped its build directory')
    require(digest(image) == status['artifact_sha256'], 'candidate image changed')
    return status


def accounting(job):
    require(re.fullmatch(r'[1-9][0-9]*', job), 'invalid Slurm job handle')
    text = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', job,
        '--format=JobIDRaw,State,ExitCode,Partition,Account,WorkDir,Elapsed,NodeList'], text=True)
    rows = [line.split('|') for line in text.splitlines() if line.split('|')[0] == job]
    require(len(rows) <= 1, 'ambiguous Slurm accounting')
    return rows[0] if rows else None


def check_allocation(row, directory):
    if PROJECT.is_relative_to(SAI_PROJECT_ROOT):
        require(row[4:6] == [SAI_PROJECT_ACCOUNT, str(directory)],
                'Slurm project account or working directory differs')


def runtime_path(output):
    # Keep MPI socket paths short even when the workflow run/source label is long.
    return PROJECT / 'runtime' / ('li-' + hashlib.sha256(str(output).encode()).hexdigest()[:12])


def render(output, partition):
    output = Path(output)
    require(output.parent == ROOT / 'runtime-tests' and output.resolve() == output,
            'acceptance output escaped the selected root')
    safe_name(output.name)
    require(partition in ('4V100', '16V100', '8V100V0'), 'unsupported GPU partition')
    script = (PACKAGE / 'lammps_verify.sbatch').read_text()
    directives = ['#SBATCH --partition=' + partition,
                  '#SBATCH --qos=' + ('flood-1o2gpu' if partition == '16V100' else 'rush-1o2gpu'),
                  '#SBATCH --job-name=lmp-science-' + output.name,
                  '#SBATCH --output=' + str(output / 'slurm-%j.log'),
                  *project_batch_directives(PROJECT, output)]
    script = script.replace('set -eo pipefail\n', '\n'.join(directives) + '\nset -eo pipefail\n', 1)
    return script.replace('@PROJECT@', shlex.quote(str(PROJECT))).replace(
        '@HOME@', shlex.quote(str(Path.home()))).replace('@RUNTIME@', shlex.quote(str(runtime_path(output))))


def reference_path(root, relative):
    relative = Path(relative)
    path = Path(root) / relative
    require(not relative.is_absolute() and '..' not in relative.parts and path.resolve() == path,
            'reference input escaped its frozen root')
    return path


def submit(build, reference_file, *, expected_delivery, reference_root=REFERENCE_ROOT, run_id=None):
    status = build_status(build)
    require(status['delivery'] == expected_delivery, 'build differs from the requested source/dependency')
    require(reference_file.resolve() == reference_file and digest(reference_file) == REFERENCE_SHA256,
            'unrecognized accepted reference')
    partition = status['delivery']['identity']['partition']
    reference = read(reference_file)['partitions'][partition]
    output = ROOT / 'runtime-tests' / safe_name(run_id or build.name + '-science')
    require(output.resolve() == output and not output.exists(), 'acceptance task already exists or was redirected')
    script = render(output, partition)
    output.mkdir(parents=True)
    private_scratch(runtime_path(output))
    pins = {}

    def copy(source, name, checksum):
        require(digest(source) == checksum, 'accepted reference changed: ' + str(source))
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        target.chmod(0o444)
        pins[name] = checksum

    for name in ('lammps_acceptance.py', 'lammps_rank.sh', 'lammps_verify.sbatch', 'remote_controller.py'):
        copy(PACKAGE / name, 'control/' + name, digest(PACKAGE / name))
    for name, checksum in reference['tools'].items():
        copy(reference_path(reference_root, name), 'helpers/' + Path(name).name, checksum)
    for name, checksum in reference['case_files'].items():
        copy(reference_path(reference_root, reference['science_case'] + '/' + name), 'inputs/science/' + name, checksum)
    for name, case in reference['gpu'].items():
        for filename in ('data.lmp', 'in.lmp'):
            source = case['source'] + '/' + filename
            copy(reference_path(reference_root, source), 'inputs/gpu/' + name + '/' + filename, reference['files'][source])
    write(output / 'reference.json', reference)
    pins['reference.json'] = digest(output / 'reference.json')
    (output / 'job.sbatch').write_text(script)
    pins['job.sbatch'] = digest(output / 'job.sbatch')
    write(output / 'request.json', dict(schema=1, build=str(build), build_status=status,
          expected_delivery=expected_delivery, reference_root=str(reference_root),
          build_request_sha256=digest(build / 'request.json'), files=pins,
          reference_sha256=digest(reference_file)))
    (output / 'request.sha256').write_text(digest(output / 'request.json') + '\n')
    check_files(output, pins)
    command = ['sbatch', '--hold', '--parsable', str(output / 'job.sbatch'), str(output)]
    subprocess.run(['bash', '-n', str(output / 'job.sbatch')], check=True)
    subprocess.run(['sbatch', '--test-only', *command[3:]], cwd=output, check=True)
    job = subprocess.check_output(command, cwd=output, text=True).strip().split(';')[0]
    require(re.fullmatch(r'[1-9][0-9]*', job), 'unexpected Slurm job handle')
    (output / 'job.id').write_text(job + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    print(json.dumps(dict(job=job, partition=partition, output=str(output))), flush=True)
    return output


def context(output):
    require(digest(output / 'request.json') == (output / 'request.sha256').read_text().strip(),
            'frozen acceptance request changed')
    request = read(output / 'request.json')
    require(request['schema'] == 1, 'unsupported acceptance request')
    require(request['reference_sha256'] == REFERENCE_SHA256 and
            request['expected_delivery'] == request['build_status']['delivery'],
            'acceptance source/dependency/reference binding changed')
    check_files(output, request['files'])
    status = request['build_status']
    identity = status['delivery']['identity']
    dependency = status['delivery']['deepmd_dependency']['identity']
    require(identity['software'] == 'lammps' and identity['stack_sources'] == {}
            and dependency['software'] == 'deepmd-kit' and identity['target'] == dependency['target'],
            'wrong standalone candidate/dependency')
    return request, read(output / 'reference.json'), identity, dependency


def helpers(output):
    sys.path.insert(0, str(output / 'helpers'))
    import md_evidence
    import md_science
    import md_probe
    import gpu_functional_smoke
    return md_evidence, md_science, md_probe, gpu_functional_smoke


def guard(output):
    request, _, identity, dependency = context(output)
    _, science, _, _ = helpers(output)
    science.require_execution_context()
    if PROJECT.is_relative_to(SAI_PROJECT_ROOT):
        require(os.environ.get('SLURM_JOB_ACCOUNT') == SAI_PROJECT_ACCOUNT, 'wrong scientific billing account')
    require(os.environ.get('SLURM_JOB_PARTITION') == identity['partition'], 'wrong GPU partition')
    require(os.environ.get('CUDA_VISIBLE_DEVICES') not in (None, '', '-1'), 'allocated GPU is hidden')
    require((output / 'job.id').read_text().strip() == os.environ['SLURM_JOB_ID'], 'wrong allocation')
    mpi = '/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-' + identity['dependency_isa']
    require(shutil.which('mpirun') == mpi + '/bin/mpirun', 'wrong host MPI/ISA')
    return identity['install_prefix'], dependency['install_prefix'], request['build_status']['artifact'], mpi


def command(output, hosts, arguments):
    # Slurm already constrains each rank to its allocated CPUs. On shared GPU
    # nodes PRTE's extra core binding can select a core outside that mask and
    # abort before starting bash (hwloc_set_cpubind). Keep one rank per node
    # and the inherited allocation; these checks make no timing claim.
    return [shutil.which('mpirun'), '-np', str(len(hosts)), '--host', ','.join(hosts),
            '--map-by', 'ppr:1:node', '--bind-to', 'none', '--report-bindings',
            'bash', str(output / 'control/lammps_rank.sh'), 'lmp', *arguments]


def execute(folder, argv):
    started = time.monotonic()
    with (folder / 'stdout.txt').open('x') as stream:
        try:
            process = subprocess.run(argv, cwd=folder, stdout=stream, stderr=subprocess.STDOUT, timeout=600)
            code = process.returncode
        except subprocess.TimeoutExpired:
            code = 124
    write(folder / 'process.json', dict(command=argv, returncode=code, seconds=time.monotonic() - started,
          job=os.environ['SLURM_JOB_ID'], node=os.uname().nodename.split('.')[0]))
    require(code == 0, 'acceptance process failed: ' + str(folder))


def run(output):
    request, ref, _, _ = context(output)
    guard(output)
    require(build_status(Path(request['build'])) == request['build_status'], 'build status changed')
    _, science, _, _ = helpers(output)
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
        execute(folder, command(output, hosts[:nodes], arguments))
        print(name + ': process completed', flush=True)

    lammps('version', 2, ['-h'])
    folder = results / 'inventory'
    folder.mkdir()
    execute(folder, ['bash', str(output / 'control/lammps_rank.sh'), 'inventory'])
    for backend in BACKENDS:
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
    require(build_status(Path(request['build'])) == request['build_status'], 'build/dependency changed during tests')
    report = verify_raw(output)
    report['files'] = raw_inventory(output)
    write(output / 'acceptance.json', report)
    print('LAMMPS_STANDALONE_GPU_SCIENCE_PASSED_NOT_PUBLISHED', flush=True)


def inventory(output):
    """Executed inside the candidate, using its fixed installed dependency."""
    _, _, identity, dependency = context(output)
    _, science, probe, _ = helpers(output)
    science.require_execution_context()
    import torch
    require(torch.cuda.is_available(), 'site PyTorch cannot use the allocated GPU')
    prefix = identity['install_prefix']
    record = dict(lammps=probe.lammps_inventory(prefix), binary_sha256=digest(Path(prefix) / 'bin/lmp'),
                  installed_manifest=read(Path(prefix) / 'share/sai/manifest.json'),
                  build_inventory=read(Path(prefix) / 'share/sai/candidate.json')['lammps'],
                  torch_cuda=dict(version=torch.__version__, device=torch.cuda.get_device_name(0),
                                  capability=list(torch.cuda.get_device_capability(0))),
                  dependency_prefix=dependency['install_prefix'])
    write(Path('/work/features.json'), record)


def expected_cases(ref):
    return {'version', 'inventory', *(f'deepmd-{b}-{m}' for b in BACKENDS for m in MODES),
            *(name + '-' + m for name in ref['gpu'] for m in MODES)}


def compare_science(observed, expected, tolerance, evidence):
    require(set(observed) == set(expected) == {'energy', 'forces', 'virial'}, 'missing observable')
    for field, limit in tolerance.items():
        evidence._compare_numeric(observed[field], expected[field], **limit, path=field)


def help_version(stdout):
    banners = re.findall(r'^Large-scale Atomic/Molecular Massively Parallel Simulator - '
                         r'(\d{1,2} [A-Za-z]{3} \d{4})([^\n]*)$', stdout, re.M)
    require(len(banners) == 1, 'missing/duplicate LAMMPS help banner')
    date, update = banners[0]
    number = int(datetime.strptime(date, '%d %b %Y').strftime('%Y%m%d'))
    # src/lammps.cpp increments num_ver for Development/Maintenance. This
    # is integer addition, not the following calendar day (Sep 30 -> 20260931).
    if re.match(r'^ - (?:Development|Maintenance)(?: -|\s*$)', update):
        number += 1
    return str(number)


def verify_raw(output):
    request, ref, identity, dependency = context(output)
    evidence, science, _, gpu = helpers(output)
    require(digest(request['build_status']['artifact']) == request['build_status']['artifact_sha256'],
            'candidate image changed')
    require(ref['tolerances'] == science.TOLERANCES and ref['gpu_tolerances'] == dict(atol=2e-6, rtol=2e-5),
            'acceptance tolerances changed')
    results = output / 'results'
    require({p.name for p in results.iterdir() if p.is_dir()} == expected_cases(ref), 'incomplete case matrix')
    hosts = (output / 'nodes.txt').read_text().split()
    require(len(hosts) == len(set(hosts)) == 2, 'two distinct nodes required')
    job = (output / 'job.id').read_text().strip()
    feature_record = read(results / 'inventory/features.json')
    features = feature_record['lammps']
    for field in ('version', 'packages', 'styles'):
        require(features[field] == feature_record['build_inventory'][field], 'candidate inventory changed')
    require(feature_record['installed_manifest']['entry']['identity'] == identity, 'wrong SIF identity')
    require(set(ref['lammps_features']['packages']) <= set(features['packages']), 'release package lost')
    for kind, styles in ref['lammps_features']['styles'].items():
        require(set(styles) <= set(features['styles'][kind]), 'release style lost: ' + kind)
    prefix = identity['install_prefix']
    binary = prefix + '/bin/lmp'
    mpi = '/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-' + identity['dependency_isa']
    require(features['library'].startswith(prefix + '/') and 'not found' not in features['ldd'],
            'wrong/unresolved candidate library')
    require(feature_record['dependency_prefix'] == dependency['install_prefix'] and
            feature_record['torch_cuda']['capability'] == [7, 0], 'wrong installed dependency/GPU')
    samples, placements = {}, {}
    for name in sorted(expected_cases(ref)):
        folder = results / name
        process = read(folder / 'process.json')
        require(process['returncode'] == 0 and process['job'] == job and process['node'] == hosts[0],
                'failed process/allocation: ' + name)
        if name == 'inventory':
            require(process['command'] == ['bash', str(output / 'control/lammps_rank.sh'), 'inventory'],
                    'wrong inventory command')
            continue
        count = 2 if name == 'version' else MODES[name.rsplit('-', 1)[1]]
        require(len(list((folder / 'ranks').glob('rank-*.tsv'))) == count, 'incomplete rank traces')
        placements[name] = []
        for rank, host in enumerate(hosts[:count]):
            row = (folder / 'ranks' / f'rank-{rank}.tsv').read_text().strip().split('\t')
            require(len(row) == 7 and row[:3] == [str(rank), host, binary] and row[3] not in ('', '-1')
                    and row[4] and row[5] == feature_record['binary_sha256'] and
                    row[6] == request['build_status']['artifact'], 'wrong rank/node/binary/GPU/image')
            placements[name].append(row)
            if name == 'version':
                loader = (folder / 'ranks' / f'ldd-{rank}.txt').read_text()
                require('not found' not in loader and mpi + '/lib/libmpi.so.40' in loader and
                        dependency['install_prefix'] + '/lib/libdeepmd' in loader, 'wrong MPI/DeePMD loader')
        start = [mpi + '/bin/mpirun', '-np', str(count), '--host', ','.join(hosts[:count]),
                 '--map-by', 'ppr:1:node', '--bind-to', 'none', '--report-bindings',
                 'bash', str(output / 'control/lammps_rank.sh'), 'lmp']
        require(process['command'][:len(start)] == start, 'wrong MPI launch')
        arguments = process['command'][len(start):]
        stdout = (folder / 'stdout.txt').read_text()
        if name == 'version':
            require(arguments == ['-h'] and help_version(stdout) == features['version'],
                    'help version differs from candidate C API inventory')
            continue
        if name.startswith('deepmd-'):
            backend = name.split('-')[1]
            require(arguments == ['-in', 'in.' + backend, '-log', 'log.lammps'], 'wrong DeePMD command')
            for relative, checksum in ref['case_files'].items():
                if relative in ('data.lmp', 'plumed.dat', 'in.' + backend) or relative.split('/')[0] == science.BACKENDS[backend]:
                    check_files(folder, {relative: checksum})
            fixture = science._load_fixture(output / 'inputs/science', backend)
            observed = science.parse_lammps_output(stdout, (folder / 'result.dump').read_text())
            science.verify_plumed_output((folder / 'COLVAR').read_text(), fixture['distance_angstrom'])
            compare_science(observed, fixture['reference'], ref['tolerances'], evidence)
            compare_science(observed, ref['science'][backend]['lammps'], ref['tolerances'], evidence)
        else:
            case_name = name.rsplit('-', 1)[0]
            case = ref['gpu'][case_name]
            options = (['-pk', 'gpu', '1', 'split', '1.0'] if case['mode'] == 'gpu' else
                       ['-k', 'on', 'g', '1', 't', '1', '-sf', 'kk', '-pk', 'kokkos', 'gpu/aware', 'off'])
            require(arguments == [*options, '-in', 'in.lmp', '-log', 'log.lammps'], 'wrong GPU options')
            check_files(folder, {f: ref['files'][case['source'] + '/' + f] for f in ('data.lmp', 'in.lmp')})
            if case['mode'] == 'gpu':
                require('(Mixed Precision)' in stdout and '- Using acceleration for' in stdout, 'GPU not used')
            else:
                require('KOKKOS mode' in stdout and 'using double precision' in stdout and
                        ('coulomb' not in name or 'using double precision cuFFT' in stdout), 'Kokkos/cuFFT not used')
            observed = list(gpu.values(folder))
            evidence._compare_numeric(observed, case['expected'], **ref['gpu_tolerances'], path=name)
        samples[name] = observed
    for backend in BACKENDS:
        compare_science(samples[f'deepmd-{backend}-multi'], samples[f'deepmd-{backend}-single'], ref['tolerances'], evidence)
    for name in ref['gpu']:
        evidence._compare_numeric(samples[name + '-multi'], samples[name + '-single'], **ref['gpu_tolerances'], path=name)
    return dict(schema=1, passed=True, scientific_verified=True, native_runtime_verified=False, published=False,
                scope='LAMMPS SIF with installed DeePMD; single/two-node GPU correctness; no speed or DeePMD training claim',
                job=job, partition=identity['partition'], delivery=request['build_status']['delivery'],
                artifact=request['build_status']['artifact'], artifact_sha256=request['build_status']['artifact_sha256'],
                request_sha256=digest(output / 'request.json'), features=features,
                samples=samples, placements=placements, torch_cuda=feature_record['torch_cuda'])


def raw_inventory(output):
    paths = [p for p in (output / 'results').rglob('*') if p.is_file()]
    paths += [output / name for name in ('modules.txt', 'allocation.txt', 'nodes.txt', 'job.id')]
    return {str(p.relative_to(output)): digest(p) for p in sorted(paths)}


def verify(output):
    report = read(output / 'acceptance.json')
    job = (output / 'job.id').read_text().strip()
    row = accounting(job)
    require(row is not None and row[1:4] == ['COMPLETED', '0:0', report['partition']],
            'Slurm acceptance job did not complete successfully')
    check_allocation(row, output)
    require(report == dict(verify_raw(output), files=raw_inventory(output)), 'raw acceptance evidence changed')
    request = read(output / 'request.json')
    require(digest(Path(request['build']) / 'request.json') == request['build_request_sha256'], 'build request changed')
    require(build_status(Path(request['build'])) == request['build_status'], 'build/dependency changed after tests')
    result = dict(passed=True, scientific_verified=True, output=str(output), job=job, accounting=row,
          delivery=report['delivery'], request_sha256=digest(output / 'request.json'),
          artifact_sha256=report['artifact_sha256'], acceptance_sha256=digest(output / 'acceptance.json'),
          scientific_cases=len(report['samples']), packages=len(report['features']['packages']),
          styles=sum(len(s) for s in report['features']['styles'].values()),
          native_runtime_verified=False, published=False)
    receipt = output / 'verification.json'
    if receipt.exists():
        require(not receipt.is_symlink() and read(receipt) == result, 'verified acceptance receipt changed')
    else:
        write(receipt, result)
    print(json.dumps(result, sort_keys=True), flush=True)
    return result


def monitor(output, *, timeout=21600, interval=30):
    request, _, identity, _ = context(output)
    job = (output / 'job.id').read_text().strip()
    deadline = time.monotonic() + timeout
    previous = None
    while time.monotonic() < deadline:
        row = accounting(job)
        if row is not None:
            check_allocation(row, output)
            require(row[3] == identity['partition'], 'science job changed partition')
            if row[1] != previous:
                print(job + ': ' + row[1], flush=True)
                previous = row[1]
            if row[1] not in ('PENDING', 'RUNNING', 'CONFIGURING', 'COMPLETING', 'SUSPENDED', 'REQUEUED'):
                require(row[1:3] == ['COMPLETED', '0:0'], 'science job failed: ' + '|'.join(row))
                require((output / 'acceptance.json').is_file(), 'completed job has no scientific evidence')
                return 0
        time.sleep(interval)
    raise TimeoutError('observation deadline; the existing science job remains available for monitoring')


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('submit', 'run', 'guard', 'inventory', 'verify', 'monitor'))
    parser.add_argument('directory', type=Path)
    parser.add_argument('--reference', type=Path)
    parser.add_argument('--reference-root', type=Path, default=REFERENCE_ROOT)
    parser.add_argument('--delivery', type=Path)
    parser.add_argument('--run-id')
    parser.add_argument('--timeout', type=int, default=21600)
    parser.add_argument('--interval', type=int, default=30)
    args = parser.parse_args()
    if args.operation == 'submit':
        require(args.reference is not None and args.delivery is not None,
                'accepted native-reference.json and requested delivery.json are required')
        submit(args.directory.resolve(), args.reference.resolve(), expected_delivery=read(args.delivery),
               reference_root=args.reference_root.resolve(), run_id=args.run_id)
    elif args.operation == 'guard':
        print('\n'.join(guard(args.directory)))
    elif args.operation == 'monitor':
        monitor(args.directory, timeout=args.timeout, interval=args.interval)
    else:
        globals()[args.operation](args.directory)
