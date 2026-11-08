#!/usr/bin/env python3
"""Validate two unpublished build artifacts with frozen release cases.

This is a one-node, one/two-rank regression check, not the production two-node
publication contract and not a scientific runtime performance benchmark.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

PROJECT = Path('/org/org-open-sai/proj-sai-hpc-software')
REFERENCE_ROOT = Path('/home/stardust/sai-hpc-software')
REFERENCE_SHA = 'e5f75411c7891cb4df63bb71410daa6f21e5c91b6ee7bb8f987b41e4e0880478'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, record):
    with Path(path).open('x') as stream:
        json.dump(record, stream, indent=2, sort_keys=True)
        stream.write('\n')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def context(output):
    require(digest(output / 'request.json') == (output / 'request.sha256').read_text().strip(), 'science request changed')
    record = read(output / 'request.json')
    for name, expected in record['files'].items():
        require(digest(output / name) == expected, 'frozen input changed: ' + name)
    return record, read(output / 'reference.json')


def helpers(output):
    sys.path.insert(0, str(output / 'helpers'))
    import md_evidence, md_probe, md_science, gpu_functional_smoke
    return md_evidence, md_probe, md_science, gpu_functional_smoke


def variant_delivery(record, label):
    """Support paired source revisions as well as paired build generators."""
    return record['variants'][label].get('delivery', record.get('delivery'))


def guard(output):
    record, _ = context(output)
    _, _, science, _ = helpers(output)
    science.require_execution_context()
    require(os.environ['SLURM_JOB_ID'] == (output / 'job.id').read_text().strip(), 'wrong job')
    require(os.environ['SLURM_JOB_PARTITION'] == '16V100', 'wrong partition')
    require(os.environ['SLURM_JOB_ACCOUNT'] == 'proj-sai-hpc-software', 'wrong billing account')
    require(os.environ.get('CUDA_VISIBLE_DEVICES') not in (None, '', '-1'), 'GPU hidden')
    variant = record['variants'][os.environ['SAI_LAMMPS_BENCH_VARIANT']]
    mpi = '/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-avx2'
    require(shutil.which('mpirun') == mpi + '/bin/mpirun', 'wrong host MPI')
    delivery = variant_delivery(record, os.environ['SAI_LAMMPS_BENCH_VARIANT'])
    return delivery['identity']['install_prefix'], delivery['deepmd_dependency']['identity']['install_prefix'], variant['image'], mpi


def inventory(output):
    record, _ = context(output)
    _, probe, science, _ = helpers(output)
    science.require_execution_context()
    import torch
    require(torch.cuda.is_available(), 'no GPU available to the installed DeePMD backend')
    prefixes = {variant_delivery(record, label)['identity']['install_prefix']
                for label in record['variants']}
    prefix = Path(os.environ['SAI_LMP_PREFIX'])
    require(str(prefix) in prefixes, 'unrecognized candidate prefix')
    observed = probe.lammps_inventory(prefix)
    built = read(prefix / 'share/sai/candidate.json')['lammps']
    for field in ('version', 'packages', 'styles'):
        require(observed[field] == built[field], 'runtime inventory differs: ' + field)
    write('/work/features.json', dict(lammps=observed, binary_sha256=digest(prefix / 'bin/lmp'),
                                    cuda_capability=list(torch.cuda.get_device_capability()),
                                    image=os.environ['SAI_LMP_IMAGE']))


def run(output):
    record, ref = context(output)
    evidence, _, science, gpu = helpers(output)
    require(ref['tolerances'] == science.TOLERANCES, 'science tolerances changed')
    results = output / 'results'
    results.mkdir()
    observations = {}
    features = {}
    for label, variant in record['variants'].items():
        os.environ['SAI_LAMMPS_BENCH_VARIANT'] = label
        prefix, dep, image, mpi = guard(output)
        require(digest(image) == variant['image_sha256'], 'candidate SIF changed')

        def execute(name, ranks, args, prepare=None, inventory_only=False):
            folder = results / (label + '-' + name)
            folder.mkdir()
            (folder / 'ranks').mkdir()
            if prepare:
                prepare(folder)
            rank_script = ['bash', str(output / 'control/lammps_rank.sh')]
            argv = (rank_script + ['inventory'] if inventory_only else
                    [mpi + '/bin/mpirun', '-np', str(ranks), '--map-by', 'slot', '--bind-to', 'none',
                     '--report-bindings', *rank_script, 'lmp', *args])
            start = time.monotonic()
            with (folder / 'stdout.txt').open('x') as stream:
                try:
                    code = subprocess.run(argv, cwd=folder, stdout=stream, stderr=subprocess.STDOUT, timeout=600).returncode
                except subprocess.TimeoutExpired:
                    code = 124
            write(folder / 'process.json', dict(command=argv, returncode=code, seconds=time.monotonic() - start,
                  job=os.environ['SLURM_JOB_ID'], node=os.uname().nodename, image_sha256=variant['image_sha256']))
            require(code == 0, 'case failed: ' + str(folder))
            if not inventory_only:
                traces = sorted((folder / 'ranks').glob('rank-*.tsv'))
                require(len(traces) == ranks, 'missing rank traces')
                for rank, trace in enumerate(traces):
                    row = trace.read_text().strip().split('\t')
                    require(len(row) == 7 and row[0] == str(rank) and row[2] == prefix + '/bin/lmp'
                            and row[3] not in ('', '-1') and row[6] == image, 'incorrect rank/image/GPU')
            print(folder.name + ': process completed', flush=True)
            return folder

        version = execute('version', 2, ['-h'])
        for loader in (version / 'ranks').glob('ldd-*.txt'):
            text = loader.read_text()
            require('not found' not in text and mpi + '/lib/libmpi.so.40' in text
                    and dep + '/lib/libdeepmd' in text, 'incorrect or unresolved MPI/DeePMD')
        feature_path = execute('inventory', 0, [], inventory_only=True)
        features[label] = read(feature_path / 'features.json')
        require(set(ref['lammps_features']['packages']) <= set(features[label]['lammps']['packages']),
                'an accepted release package is missing')
        for backend in ('tf', 'pt', 'jax'):
            fixture = science._load_fixture(output / 'inputs/science', backend)
            for ranks in (1, 2):
                folder = execute(f'deepmd-{backend}-{ranks}', ranks, ['-in', 'in.' + backend, '-log', 'log.lammps'],
                    lambda dest: science.copy_trial_inputs(output / 'inputs/science', dest, backend, fixture))
                observed = science.parse_lammps_output((folder / 'stdout.txt').read_text(), (folder / 'result.dump').read_text())
                science.verify_plumed_output((folder / 'COLVAR').read_text(), fixture['distance_angstrom'])
                for field, tolerance in ref['tolerances'].items():
                    for expected in (fixture['reference'], ref['science'][backend]['lammps']):
                        evidence._compare_numeric(observed[field], expected[field], **tolerance, path=folder.name + '/' + field)
                observations[folder.name] = observed
        for name, case in ref['gpu'].items():
            for ranks in (1, 2):
                options = (['-pk', 'gpu', str(ranks), 'split', '1.0'] if case['mode'] == 'gpu' else
                           ['-k', 'on', 'g', str(ranks), 't', '1', '-sf', 'kk', '-pk', 'kokkos', 'gpu/aware', 'off'])
                def prepare(dest):
                    for filename in ('data.lmp', 'in.lmp'):
                        shutil.copyfile(output / 'inputs/gpu' / name / filename, dest / filename)
                folder = execute(name + '-' + str(ranks), ranks, [*options, '-in', 'in.lmp', '-log', 'log.lammps'], prepare)
                text = (folder / 'stdout.txt').read_text()
                if case['mode'] == 'gpu':
                    require('(Mixed Precision)' in text and '- Using acceleration for' in text, 'GPU acceleration not active')
                else:
                    require('KOKKOS mode' in text and 'using double precision' in text
                            and ('coulomb' not in name or 'using double precision cuFFT' in text), 'Kokkos/cuFFT not active')
                observed = list(gpu.values(folder))
                evidence._compare_numeric(observed, case['expected'], **ref['gpu_tolerances'], path=folder.name)
                observations[folder.name] = observed
    require(len(features) == 2, 'exactly two candidates required')
    left, right = features.values()
    for field in ('version', 'packages', 'styles'):
        require(left['lammps'][field] == right['lammps'][field], 'paired feature mismatch')
    write(output / 'validation.json', dict(passed=True, scope='one node; one and two ranks; not production publication acceptance',
          job=os.environ['SLURM_JOB_ID'], features=features, observations=observations,
          tolerances=ref['tolerances'], gpu_tolerances=ref['gpu_tolerances'], cases=len(observations),
          process_files={str(path.relative_to(output)): digest(path) for path in results.rglob('*') if path.is_file()}))
    print('PAIRED_BUILD_GPU_SCIENCE_PASSED_NOT_PUBLISHED', flush=True)


def prepare(experiment, reference_file, output):
    require(not output.exists() and output.is_relative_to(PROJECT), 'need a fresh project test directory')
    record = read(experiment / 'request.json')
    control = Path(record['controller'])
    require(digest(reference_file) == REFERENCE_SHA, 'unrecognized frozen reference')
    ref = read(reference_file)['partitions']['16V100']
    for path, checksum in record['controller_files'].items():
        require(digest(control / path) == checksum, 'build controller changed')
    job = (experiment / 'job.id').read_text().strip()
    accounting = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', job, '-o', 'JobIDRaw,State,ExitCode'], text=True)
    rows = [line.strip().rstrip('|').split('|') for line in accounting.splitlines() if line.strip()]
    require(rows == [[job, 'COMPLETED', '0:0']], 'paired build not completed successfully')
    variants = {}
    for row in record['variants']:
        run_dir = Path(row['run'])
        require('LAMMPS_STANDALONE_BUILD_VERIFIED_NOT_PUBLISHED' in (run_dir / f'results/slurm-{job}.log').read_text(), 'missing build verification')
        image = run_dir / 'candidate.sif'
        variants[row['label']] = dict(image=str(image), image_sha256=digest(image),
                                      delivery=row.get('delivery', record.get('delivery')))
    output.mkdir(parents=True)
    pins = {}
    def copy(source, name, expected=None):
        checksum = digest(source)
        require(expected is None or checksum == expected, 'reference changed: ' + str(source))
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        pins[name] = checksum
    copy(__file__, 'control/science.py')
    for source, checksum in ref['tools'].items():
        copy(REFERENCE_ROOT / source, 'helpers/' + Path(source).name, checksum)
    for name, checksum in ref['case_files'].items():
        copy(REFERENCE_ROOT / ref['science_case'] / name, 'inputs/science/' + name, checksum)
    for name, case in ref['gpu'].items():
        for filename in ('data.lmp', 'in.lmp'):
            source = case['source'] + '/' + filename
            copy(REFERENCE_ROOT / source, 'inputs/gpu/' + name + '/' + filename, ref['files'][source])
    write(output / 'reference.json', ref)
    rank = (control / 'lammps_rank.sh').read_text().replace('lammps_acceptance.py', 'science.py')
    (output / 'control/lammps_rank.sh').write_text(rank)
    # Match the production MPI launcher: long source/run labels otherwise
    # exceed Unix-domain socket path limits before the program can start.
    runtime = PROJECT / 'runtime' / ('nb-' + hashlib.sha256(str(output).encode()).hexdigest()[:12])
    require(runtime.resolve() == runtime, 'runtime path was redirected')
    runtime.mkdir(mode=0o700, parents=True)
    script = (control / 'lammps_verify.sbatch').read_text()
    for before, after in (('--nodes=2', '--nodes=1'), ('--ntasks-per-node=1', '--ntasks-per-node=2'),
                           ('--gpus-per-node=1', '--gpus-per-node=2'), ('lammps_acceptance.py', 'science.py'),
                           ('@HOME@', str(REFERENCE_ROOT.parent)), ('@PROJECT@', str(PROJECT)), ('@RUNTIME@', str(runtime))):
        script = script.replace(before, after)
    directives = ('#SBATCH --partition=16V100\n#SBATCH --qos=flood-1o2gpu\n#SBATCH --account=proj-sai-hpc-software\n'
                  '#SBATCH --job-name=lmp-paired-science\n#SBATCH --chdir=' + str(output) + '\n#SBATCH --output=' + str(output / 'slurm-%j.log') + '\n')
    script = script.replace('set -eo pipefail', directives + 'set -eo pipefail', 1)
    (output / 'job.sbatch').write_text(script)
    for name in ('reference.json', 'control/lammps_rank.sh', 'job.sbatch'):
        pins[name] = digest(output / name)
    write(output / 'request.json', dict(schema=1, delivery=record.get('delivery'), variants=variants, files=pins,
          experiment=str(experiment), build_job=job, runtime=str(runtime),
          experiment_request_sha256=digest(experiment / 'request.json')))
    (output / 'request.sha256').write_text(digest(output / 'request.json') + '\n')
    subprocess.run(['bash', '-n', str(output / 'job.sbatch')], check=True)
    command = ['sbatch', '--hold', '--parsable', str(output / 'job.sbatch'), str(output)]
    subprocess.run(['sbatch', '--test-only', *command[3:]], cwd=output, check=True)
    write(output / 'submission-intent.json', dict(command=command))
    job = subprocess.check_output(command, cwd=output, text=True).strip().split(';')[0]
    require(job.isdigit(), 'invalid submission response; inspect queue before retrying')
    (output / 'job.id').write_text(job + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    print(json.dumps(dict(job=job, output=str(output))), flush=True)


if __name__ == '__main__':
    action = sys.argv[1]
    if action == 'prepare':
        prepare(*(Path(value).resolve() for value in sys.argv[2:]))
    elif action == 'run':
        run(Path(sys.argv[2]))
    elif action == 'inventory':
        inventory(Path(sys.argv[2]))
    elif action == 'guard':
        print('\n'.join(guard(Path(sys.argv[2]))))
    else:
        raise ValueError('unknown action')
