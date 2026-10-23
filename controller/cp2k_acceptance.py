#!/usr/bin/env python3
"""Candidate-only CP2K science acceptance on one and two MPI nodes.

The same sealed image executes seven fixed ENERGY_FORCE cases at both sizes.
Energy/force consistency, actual ELPA execution and rank provenance gate
publication. Timings are reported without a historical performance baseline.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

import cp2k_benchmark as fixtures
from delivery_layout import artifact_path, load_artifact, validate_record
from remote_controller import TARGETS, safe_name, project_batch_directives, private_scratch
from source_cache import checksum

ROOT = Path(os.environ.get('SAI_SOFTWARE_ROOT', Path.home() / 'sai-hpc-software')).resolve()
CONTROL = Path(__file__).resolve().parent
PROOF = 'cp2k_science'
PURPOSE = 'cp2k-candidate-single-multinode-science'
MODES = {'single': 1, 'multi': 2}
TARGET_NAMES = ('dsprhbm', '4v100-avx512', '16v100-avx2', '8v100v0-avx512')
DATA_HASHES = fixtures.DATA_HASHES
FIXTURE_REVISION = fixtures.FIXTURE_REVISION
ENERGY_TOLERANCE_HA = fixtures.ENERGY_TOLERANCE_HA
FORCE_TOLERANCE_HA_BOHR = fixtures.FORCE_TOLERANCE_HA_BOHR
compare_science = fixtures.compare_science
# Reuse the rank affinity/topology recorder; execution always uses the candidate.
RANK_WRAPPER = fixtures.RANK_WRAPPER.split('if [[ "$SAI_BENCH_KIND" == baseline ]]; then')[0] + 'exec "$SAI_BENCH_BINARY" "$@"\n'


def diagonalization_input(elpa=False, large=False):
    text = fixtures.water64_input() if large else fixtures.water_input()
    text = text.replace('  RUN_TYPE ENERGY_FORCE\n',
                        '  RUN_TYPE ENERGY_FORCE\n  PREFERRED_DIAG_LIBRARY SL\n'
                        '  &TIMINGS\n    THRESHOLD 0\n    TIMINGS_LEVEL 1\n  &END TIMINGS\n')
    start = text.index('      &OT ON\n')
    end = text.index('      &END OT\n', start) + len('      &END OT\n')
    text = (text[:start] + '      &DIAGONALIZATION\n        ALGORITHM STANDARD\n'
            '      &END DIAGONALIZATION\n' + text[end:])
    if elpa:
        # The 46-dimensional fixture otherwise falls below ELPA's default 64
        # eigenvector threshold and only exercises its ScaLAPACK fallback.
        return text.replace('  PREFERRED_DIAG_LIBRARY SL\n',
                            '  PREFERRED_DIAG_LIBRARY ELPA\n  ELPA_NEIGVEC_MIN 1\n  ELPA_PRINT T\n')
    return text


CASES = {
    'water-gpw': {'input': fixtures.water_input(), 'parser_case': 'water-gpw'},
    'water-diag': {'input': diagonalization_input(), 'parser_case': 'water-gpw'},
    'water-elpa': {'input': diagonalization_input(True), 'parser_case': 'water-elpa'},
    'water-dftd4': {'input': fixtures.water_input(True), 'parser_case': 'water-dftd4'},
    'ch2o-tblite': {'input': fixtures.TBLITE_INPUT, 'parser_case': 'ch2o-tblite'},
    'water64-gpw': {'input': fixtures.water64_input(), 'parser_case': 'water64-gpw'},
    'water64-elpa': {'input': diagonalization_input(True, True), 'parser_case': 'water64-gpw'},
}


def parse_science(log, forces, elapsed, case):
    if not case.startswith('water64-'):
        # ELPA's positive dispatch timer plus the immutable NEIGVEC_MIN=1
        # input establishes the small fixture's solver path. Do not depend on
        # library-internal timer labels that differ between ELPA releases.
        return fixtures.parse_science(log, forces, elapsed, CASES[case]['parser_case'])
    # The historical water64 parser imposed a two-second measurement floor.
    # Science acceptance accepts every finite positive runtime, so validate
    # this larger fixture directly without that performance-only threshold.
    if ('PROGRAM ENDED AT' not in log or not re.search(r'SCF run converged', log) or
            re.search(r'SCF run NOT converged|\bABORT\b', log, re.I)):
        raise ValueError('CP2K calculation did not finish with converged SCF')
    energies = re.findall(r'ENERGY\|\s+Total FORCE_EVAL.*?energy\s+\[hartree\]\s+(\S+)', log)
    if len(energies) != 1:
        raise ValueError('expected exactly one ENERGY_FORCE energy')
    if forces.count('ATOMIC FORCES in [a.u.]') != 1 or 'SUM OF ATOMIC FORCES' not in forces:
        raise ValueError('missing unique atomic-unit force block')
    rows = re.findall(r'^\s*(\d+)\s+(\d+)\s+([A-Za-z]+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$', forces, re.M)
    elements = fixtures.CASES['water64-gpw']['elements']
    if len(rows) != len(elements) or [row[0] for row in rows] != [str(i + 1) for i in range(len(elements))] or [row[2] for row in rows] != elements:
        raise ValueError('force atom count/order/elements differ from fixture')
    timing = re.findall(r'^\s*CP2K\s+(\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+\d+)?\s*$', log, re.M)
    if len(timing) != 1:
        raise ValueError('missing unique CP2K timing row')
    wall, total = fixtures.number(elapsed.strip()), fixtures.number(timing[0][5])
    if wall <= 0 or total <= 0:
        raise ValueError('timings must be positive')
    if case == 'water64-elpa' and not any(int(value) > 0 for value in re.findall(
            r'^\s*cp_fm_diag_elpa(?:_base)?\s+(\d+)\s+', log, re.M)):
        raise ValueError('ELPA eigensolver path was not executed')
    return dict(energy_ha=fixtures.number(energies[0]),
                forces_ha_bohr=[[fixtures.number(value) for value in row[3:]] for row in rows],
                elapsed_seconds=wall, cp2k_seconds=total, dispersion_ha=None)


def resources(target):
    if target not in TARGET_NAMES:
        raise ValueError('unregistered CP2K science target')
    cpu = target == 'dsprhbm'
    cores = 2 if cpu else 3 if target == '8v100v0-avx512' else 4
    return dict(nodes=2, ranks=2, ranks_per_node=1, gpus_per_node=0 if cpu else 1,
                omp_threads=2 if cpu else 1, cores_per_rank=cores,
                allocated_cpus=2 * cores * (1 if cpu else 2))


def mpi_mapping(target):
    return 'ppr:1:node:pe=' + str(resources(target)['cores_per_rank'])


def run_dir(run_id):
    path = ROOT / 'runtime-tests' / safe_name(run_id)
    if path.resolve() != path:
        raise ValueError('science directory must be canonical')
    return path


def fixture_hashes(case):
    return {'input.inp': fixtures.digest(CASES[case]['input']),
            **(DATA_HASHES if case.startswith('water') else {})}


def schedule():
    return [(case, mode) for case in CASES for mode in MODES]


def build_artifact(build_run_id, version, target):
    reference = ROOT / 'runs' / safe_name(build_run_id) / 'artifact.path'
    artifact = Path(fixtures.checked_read(reference.parent, reference.name, {}).strip())
    manifest = load_artifact(ROOT, artifact, software='cp2k', target=target)
    identity = manifest['identity']
    if identity['source_version'] != version or artifact != artifact_path(ROOT, identity, build_run_id):
        raise ValueError('science build artifact differs from the requested delivery')
    return artifact, manifest


def request_identity(request):
    identity = validate_record(request)
    if (identity['software'] != 'cp2k' or request.get('install_prefix') != identity['install_prefix'] or
            Path(request['artifact']) != artifact_path(ROOT, identity, request['build_run_id'])):
        raise ValueError('science request differs from its delivery identity')
    return identity


def request_artifact(request):
    identity = request_identity(request)
    artifact, manifest = build_artifact(request['build_run_id'], identity['source_version'], identity['target'])
    if manifest['identity'] != identity or manifest['sha256'] != request['artifact_sha256']:
        raise ValueError('science artifact/source/recipe identity changed')
    return artifact, manifest


def render_job(args):
    identity = request_identity(vars(args))
    allocation = resources(args.target)
    if not 5 <= args.minutes <= 120:
        raise ValueError('science wall time outside accepted bounds')
    task, target, q = run_dir(args.run_id), TARGETS[args.target], shlex.quote
    runtime = ROOT / 'runtime' / ('cs-' + hashlib.sha256(str(task).encode()).hexdigest()[:12])
    lines = ['#!/usr/bin/env bash', '#SBATCH --job-name=cp2k-science-' + safe_name(args.run_id),
             '#SBATCH --partition=' + target['partition'], '#SBATCH --qos=' + target['qos'],
             '#SBATCH --nodes=2', '#SBATCH --ntasks=2', '#SBATCH --ntasks-per-node=1',
             '#SBATCH --time=' + str(args.minutes), '#SBATCH --export=NIL',
             '#SBATCH --output=' + str(task / 'results/slurm-%j.log'),
             *project_batch_directives(ROOT, task)]
    lines += (['#SBATCH --cpus-per-task=2'] if not allocation['gpus_per_node'] else ['#SBATCH --gpus-per-node=1'])
    lines += ['set -eo pipefail', 'umask 077', 'export HOME=' + q(str(Path.home())),
        'export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}',
        'export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
        'export LD_LIBRARY_PATH="" LD_PRELOAD=""',
        'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
        'export SAI_SOFTWARE_ROOT=' + q(str(ROOT)),
        'export SAI_CP2K_VERSION=' + q(identity['source_version']),
        'export SAI_CP2K_IMAGE=' + q(str(args.artifact)),
        '# Canonical delivery prefix: ' + identity['install_prefix'],
        'export TMPDIR=' + q(str(runtime)),
        'export APPTAINER_TMPDIR=' + q(str(task / 'apptainer-runtime')),
        'export APPTAINER_CACHEDIR=' + q(str(task / 'apptainer-cache')),
        'mkdir -p "$TMPDIR" "$APPTAINER_TMPDIR" "$APPTAINER_CACHEDIR"',
        'source /etc/profile.d/lmod.sh', 'module purge',
        'export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"',
        'module use /opt/modules/modulefiles/devtools /opt/modules/modulefiles/apps',
        'module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto']
    if allocation['gpus_per_node']:
        lines += ['source /opt/sai_config/mps_mapping.d/' + target['partition'] + '.bash',
                  '[[ "${MAP_OPT,,}" == ' + q(mpi_mapping(args.target)) + ' ]] || exit 2']
    else:
        lines += ['export MAP_OPT=' + q(mpi_mapping(args.target))]
    lines += ['set -u', 'export OMP_NUM_THREADS=' + str(allocation['omp_threads']),
        'export OMP_PLACES=cores OMP_PROC_BIND=close SLURM_EXPORT_ENV=ALL',
        'export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher',
        'export SAI_BENCH_BINARY=' + q(str(args.launcher)),
        'cd ' + q(str(task)),
        'scontrol show job -o "$SLURM_JOB_ID" > results/allocation.txt',
        'scontrol show hostnames "$SLURM_JOB_NODELIST" > results/nodes.txt',
        'mapfile -t hosts < results/nodes.txt', '[[ ${#hosts[@]} -eq 2 && "${hosts[0]}" != "${hosts[1]}" ]]',
        'all_hosts="${hosts[0]},${hosts[1]}"',
        'mkdir -p results/candidate-version/ranks results/candidate-version/resources',
        'export SAI_CP2K_TRACE_DIR=' + q(str(task / 'results/candidate-version/ranks')),
        'export SAI_BENCH_RESOURCES=' + q(str(task / 'results/candidate-version/resources')),
        'readlink -f "$SAI_BENCH_BINARY" > results/candidate-version/binary.txt',
        'sha256sum "$SAI_BENCH_BINARY" > results/candidate-version/binary.sha256',
        'command -v mpirun > results/candidate-version/mpirun.txt',
        'mpirun --version > results/candidate-version/mpi-version.txt',
        'module -t list > results/candidate-version/modules.txt 2>&1',
        'mpirun -np 2 --host "$all_hosts" --map-by "$MAP_OPT" --report-bindings ./rank-exec.sh --version > results/candidate-version/version.log 2>&1',
        'run_one() {', '  local case="$1" mode="$2" ranks="$3" selected="$all_hosts"',
        '  [[ "$mode" != single ]] || selected="${hosts[0]}"',
        '  local work="cases/$case/$mode"', '  mkdir -p "cases/$case"', '  mkdir "$work"',
        '  cp -- "fixtures/$case/"* "$work/"', '  mkdir "$work/ranks" "$work/resources"',
        '  export SAI_CP2K_TRACE_DIR=' + q(str(task)) + '/$work/ranks',
        '  export SAI_BENCH_RESOURCES=' + q(str(task)) + '/$work/resources',
        '  printf "%s\\t%s\\n" "$case" "$mode" >> results/run-order.tsv',
        '  printf "%s\\n" "$MAP_OPT" > "$work/map.txt"',
        '  readlink -f "$SAI_BENCH_BINARY" > "$work/binary.txt"',
        '  sha256sum "$SAI_BENCH_BINARY" > "$work/binary.sha256"',
        '  command -v mpirun > "$work/mpirun.txt"',
        '  ( cd "$work"',
        '    /usr/bin/time -f "%e" -o elapsed.txt mpirun -np "$ranks" --host "$selected" --map-by "$MAP_OPT" --report-bindings '
        + q(str(task / 'rank-exec.sh')) + ' -i input.inp > cp2k.log 2>&1', '  )', '}']
    lines += ['run_one ' + case + ' ' + mode + ' ' + str(MODES[mode]) for case, mode in schedule()]
    return '\n'.join(lines + ['echo CP2K_CANDIDATE_SCIENCE_FINISHED']) + '\n'


def verify_ranks(task, folder, request, hosts, files):
    allocation = resources(request['target'])
    ranks = len(hosts)
    expected = {f'rank-{rank}.tsv' for rank in range(ranks)}
    for name in ('ranks', 'resources'):
        if {path.name for path in (task / folder / name).glob('rank-*.tsv')} != expected:
            raise ValueError('science rank/resource trace set differs from requested node count')
    if {path.name for path in (task / folder / 'resources').glob('topology-*.tsv')} != {
            f'topology-{rank}.tsv' for rank in range(ranks)}:
        raise ValueError('science CPU topology trace set differs from requested ranks')
    read = lambda relative: fixtures.checked_read(task, folder + '/' + relative, files)
    placements, bindings, mpi_roots = Counter(), {}, set()
    for rank in range(ranks):
        trace = read(f'ranks/rank-{rank}.tsv').rstrip('\n').split('\t')
        if len(trace) != 6:
            raise ValueError('malformed CP2K rank trace')
        host, actual_rank, target, image, gpu, mpi = trace
        if (host not in hosts or actual_rank != str(rank) or target != request['target'] or
                image != request['artifact'] or not mpi.startswith('/opt/devtools/openmpi/') or
                not mpi.endswith('-' + TARGETS[target]['dependency_isa'])):
            raise ValueError('rank image/target/MPI/node differs from science request')
        detail = read(f'resources/rank-{rank}.tsv').rstrip('\n').split('\t')
        if (len(detail) != 7 or detail[:3] != [host, str(rank), '0'] or
                detail[3] != str(allocation['omp_threads']) or detail[5:] != [gpu, request['job']]):
            raise ValueError('rank resource evidence is inconsistent')
        if (allocation['gpus_per_node'] and (not gpu or ',' in gpu or gpu == '-1')) or (
                not allocation['gpus_per_node'] and gpu):
            raise ValueError('visible GPU resources differ from science target')
        cpus = fixtures.cpu_list(detail[4])
        topology, _ = fixtures.rank_topology(read(f'resources/topology-{rank}.tsv'), cpus, allocation)
        bindings[str(rank)] = dict(host=host, local_rank=0, cpus=sorted(cpus),
                                  topology={str(cpu): row for cpu, row in topology.items()}, cuda_visible_devices=gpu)
        placements[host] += 1
        mpi_roots.add(mpi)
    if placements != Counter({host: 1 for host in hosts}) or len(mpi_roots) != 1:
        raise ValueError('science ranks did not execute one per requested node')
    return dict(nodes=dict(placements), mpi_home=next(iter(mpi_roots)), bindings=bindings)


def verify_evidence(task, request):
    """Revalidate every raw input, numerical result and launcher/rank receipt."""
    task = Path(task)
    if task != run_dir(request['run_id']) or request.get('schema') != 1 or request.get('purpose') != PURPOSE:
        raise ValueError('science request scope/run path differs')
    if any(key in request for key in ('baseline_module', 'reference_run_id', 'reference_evidence_sha256')):
        raise ValueError('candidate-only science cannot consume historical baseline/reference evidence')
    files = {}
    read = lambda relative: fixtures.checked_read(task, relative, files)
    if json.loads(read('request.json')) != request:
        raise ValueError('stored science request differs')
    identity = request_identity(request)
    request_artifact(request)
    launcher = Path(request['launcher'])
    if (launcher.parent != CONTROL or not launcher.is_relative_to(ROOT / 'controller') or
            not launcher.is_file() or launcher.resolve() != launcher or launcher.is_symlink() or
            checksum(launcher) != request['launcher_sha256'] or
            checksum(CONTROL / 'cp2k') != request['launcher_sha256'] or
            checksum(Path(__file__).resolve()) != request['controller_sha256'] or
            checksum(Path(fixtures.__file__).resolve()) != request['helpers_sha256']):
        raise ValueError('science controller/helpers/launcher changed')
    script = read('job.sbatch')
    if fixtures.digest(script) != request['job_script_sha256'] or script != render_job(argparse.Namespace(**request)):
        raise ValueError('science request no longer matches trusted job rendering')
    wrapper = read('rank-exec.sh')
    if wrapper != RANK_WRAPPER or fixtures.digest(wrapper) != request['rank_wrapper_sha256']:
        raise ValueError('science rank wrapper changed')
    job = read('job.id').strip()
    if not job.isdigit() or request.get('job') != job:
        raise ValueError('science job id is inconsistent')
    allocation = resources(request['target'])
    if any(request.get(key) != value for key, value in allocation.items()):
        raise ValueError('science resources changed')
    hosts = read('results/nodes.txt').splitlines()
    if len(hosts) != 2 or len(set(hosts)) != 2 or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', host) for host in hosts):
        raise ValueError('science allocation does not have exactly two nodes')
    slurm = dict(re.findall(r'(?:^|\s)([A-Za-z][A-Za-z0-9/]*)=([^\s]+)', read('results/allocation.txt')))
    if (slurm.get('JobId') != job or slurm.get('Partition') != identity['partition'] or
            slurm.get('NumNodes') != '2' or slurm.get('NumTasks') != '2'):
        raise ValueError('Slurm allocation differs from science request')
    actual_cpus = int(slurm.get('NumCPUs', '0'))
    allowed_cpus = {allocation['allocated_cpus']} if allocation['gpus_per_node'] else {
        allocation['allocated_cpus'], 2 * allocation['allocated_cpus']}
    if actual_cpus not in allowed_cpus or (not allocation['gpus_per_node'] and slurm.get('CPUs/Task') != '2'):
        raise ValueError('science allocated CPU resources changed')
    gpu_match = re.search(r'(?:^|,)gres/gpu=([0-9]+)(?:,|$)', slurm.get('AllocTRES', ''))
    if (allocation['gpus_per_node'] and (not gpu_match or gpu_match[1] != '2')) or (
            not allocation['gpus_per_node'] and gpu_match and int(gpu_match[1]) != 0):
        raise ValueError('science allocated GPU resources changed')
    if 'CP2K_CANDIDATE_SCIENCE_FINISHED' not in read(f'results/slurm-{job}.log'):
        raise ValueError('science completion marker is absent')
    if read('results/run-order.tsv') != ''.join(f'{case}\t{mode}\n' for case, mode in schedule()):
        raise ValueError('science case/mode execution order changed')
    folder = 'results/candidate-version'
    version = fixtures.parse_version(read(folder + '/version.log'))
    required_flags = {'omp', 'elpa', 'tblite', 'libdftd4'}
    if allocation['gpus_per_node']:
        required_flags |= {'offload_cuda', 'dbcsr_acc'}
    if not required_flags.issubset(version['flags']):
        raise ValueError('candidate lacks required scientific feature flags')
    if not allocation['gpus_per_node'] and {'offload_cuda', 'dbcsr_acc', 'cusolvermp'} & set(version['flags']):
        raise ValueError('CPU science candidate unexpectedly enables GPU offload')
    # The sealed image/source association is validated above. Preserve the
    # upstream version banner as evidence; development versions are not tags.
    mpi = verify_ranks(task, folder, request, hosts, files)
    mpi.update(path=read(folder + '/mpirun.txt').strip(), version=read(folder + '/mpi-version.txt'))
    if mpi['path'] != mpi['mpi_home'] + '/bin/mpirun':
        raise ValueError('recorded MPI executable differs from rank MPI installation')
    if 'openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto' not in read(folder + '/modules.txt'):
        raise ValueError('expected host MPI module was not loaded')
    expected_binary = read(folder + '/binary.sha256')
    if (read(folder + '/binary.txt').strip() != str(launcher) or
            expected_binary.split() != [request['launcher_sha256'], str(launcher)]):
        raise ValueError('science did not execute the pinned candidate launcher')
    samples, placements = {}, {}
    for case, mode in schedule():
        folder = f'cases/{case}/{mode}'
        for name, expected in fixture_hashes(case).items():
            for relative in (f'fixtures/{case}/{name}', f'{folder}/{name}'):
                read(relative)
                if files[relative] != expected:
                    raise ValueError('science input/data differs from pinned fixture')
        if (read(folder + '/binary.txt').strip() != str(launcher) or
                read(folder + '/binary.sha256') != expected_binary or
                read(folder + '/mpirun.txt').strip() != mpi['path'] or
                read(folder + '/map.txt').strip() != mpi_mapping(request['target'])):
            raise ValueError('science executable/MPI/mapping changed between cases')
        proof = verify_ranks(task, folder, request, hosts[:MODES[mode]], files)
        expected_bindings = {rank: binding for rank, binding in mpi['bindings'].items()
                             if binding['host'] in hosts[:MODES[mode]]}
        if proof['mpi_home'] != mpi['mpi_home'] or proof['bindings'] != expected_bindings:
            raise ValueError('science actual CPU/GPU bindings changed between modes/cases')
        placements[f'{case}/{mode}'] = proof
        samples[f'{case}/{mode}'] = parse_science(read(folder + '/cp2k.log'), read(folder + '/forces.xyz'),
                                               read(folder + '/elapsed.txt'), case)
    comparisons = {case: compare_science(samples[case + '/single'], samples[case + '/multi']) for case in CASES}
    solvers = {mode: {case: compare_science(samples['water-gpw/' + mode], samples[case + '/' + mode])
                     for case in ('water-diag', 'water-elpa')} for mode in MODES}
    for mode in MODES:
        solvers[mode]['water64-elpa'] = compare_science(samples['water64-gpw/' + mode],
                                                     samples['water64-elpa/' + mode])
    return dict(schema=1, purpose=PURPOSE, run_id=request['run_id'], job=job, identity=identity,
                artifact_sha256=request['artifact_sha256'], requested_resources=allocation,
                allocated_cpus=actual_cpus, modes=MODES, versions={'candidate': version}, mpi=mpi,
                fixture_revision=FIXTURE_REVISION, input_hashes={case: fixture_hashes(case) for case in CASES},
                energy_tolerance_ha=ENERGY_TOLERANCE_HA, force_tolerance_ha_bohr=FORCE_TOLERANCE_HA_BOHR,
                samples=samples, comparisons=comparisons, solver_comparisons=solvers, placements=placements,
                timings={key: {name: value[name] for name in ('elapsed_seconds', 'cp2k_seconds')}
                         for key, value in samples.items()},
                timing_scope='descriptive timings only; no historical ratio or performance gate',
                files=files)


def stage_fixtures(task):
    data = {}
    for name, expected in DATA_HASHES.items():
        content = subprocess.check_output(['git', '--git-dir=' + str(ROOT / 'cache/repositories/cp2k'),
                                           'show', FIXTURE_REVISION + ':data/' + name])
        if hashlib.sha256(content).hexdigest() != expected:
            raise ValueError('pinned science data checksum mismatch: ' + name)
        data[name] = content
    for case, spec in CASES.items():
        folder = task / 'fixtures' / case
        folder.mkdir(parents=True)
        (folder / 'input.inp').write_text(spec['input'])
        for name in fixture_hashes(case).keys() - {'input.inp'}:
            (folder / name).write_bytes(data[name])


def submit(args):
    allocation = resources(args.target)
    if not 5 <= args.minutes <= 120:
        raise ValueError('science wall time outside accepted bounds')
    artifact, manifest = build_artifact(args.build_run_id, args.version, args.target)
    launcher = CONTROL / 'cp2k'
    if (not launcher.is_file() or launcher.is_symlink() or launcher.resolve() != launcher or
            not os.access(launcher, os.X_OK) or not CONTROL.is_relative_to(ROOT / 'controller')):
        raise ValueError('trusted CP2K launcher is missing')
    task = run_dir(args.run_id)
    # A failed or ambiguous sbatch attempt must never be submitted twice.
    task.parent.mkdir(exist_ok=True)
    task.mkdir(mode=0o700)
    for name in ('results', 'apptainer-runtime', 'apptainer-cache'):
        (task / name).mkdir()
    for name in ('apptainer-runtime', 'apptainer-cache'):
        private_scratch(task / name)
    private_scratch(ROOT / 'runtime' / ('cs-' + hashlib.sha256(str(task).encode()).hexdigest()[:12]))
    stage_fixtures(task)
    identity = manifest['identity']
    request = dict(vars(args), **allocation, schema=1, purpose=PURPOSE, software='cp2k',
                   identity=identity, install_prefix=identity['install_prefix'],
                   source_sha=identity['source_sha'], recipe_sha256=identity['recipe_sha256'],
                   artifact=str(artifact), artifact_sha256=manifest['sha256'], launcher=str(launcher),
                   launcher_sha256=checksum(launcher), controller_sha256=checksum(Path(__file__).resolve()),
                   helpers_sha256=checksum(Path(fixtures.__file__).resolve()),
                   rank_wrapper_sha256=fixtures.digest(RANK_WRAPPER))
    script = task / 'job.sbatch'
    script.write_text(render_job(argparse.Namespace(**request)))
    wrapper = task / 'rank-exec.sh'
    wrapper.write_text(RANK_WRAPPER)
    for path in (script, wrapper):
        path.chmod(0o700)
        subprocess.run(['bash', '-n', str(path)], check=True)
    request['job_script_sha256'] = checksum(script)
    (task / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    response = subprocess.run(['sbatch', '--hold', '--parsable', str(script)], check=True,
                              text=True, capture_output=True, cwd=task)
    job = response.stdout.strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('invalid science sbatch response; inspect held allocation before retry')
    (task / 'job.id').write_text(job + '\n')
    request['job'] = job
    (task / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    print(job, flush=True)


def monitor(args):
    task = run_dir(args.run_id)
    job = fixtures.checked_read(task, 'job.id', {}).strip()
    if not job.isdigit():
        raise ValueError('invalid science job id')
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        queue = subprocess.run(['squeue', '-h', '-j', job, '-o', '%T|%R'], check=True, text=True, capture_output=True)
        if queue.stdout.strip():
            print(job + ': ' + queue.stdout.strip(), flush=True)
        else:
            rows = subprocess.run(['sacct', '-X', '-nP', '-j', job, '-o', 'JobIDRaw,State,ExitCode'],
                                  check=True, text=True, capture_output=True).stdout.splitlines()
            records = [row.split('|') for row in rows if row.split('|')[0] == job]
            if len(records) > 1:
                raise ValueError('ambiguous science job accounting')
            if records and records[0][1] not in ('PENDING', 'RUNNING', 'COMPLETING', 'CONFIGURING'):
                state, exit_code = records[0][1:3]
                status = dict(job=job, state=state, exit_code=exit_code, verified=False)
                status_path = task / 'results/status.json'
                if status_path.resolve() != status_path or status_path.is_symlink():
                    raise ValueError('untrusted science status path')
                status_path.write_text(json.dumps(status, sort_keys=True) + '\n')
                request = json.loads(fixtures.checked_read(task, 'request.json', {}))
                artifact, manifest = request_artifact(request)
                if manifest.get(PROOF, {}).get('run_id') == args.run_id:
                    manifest.pop(PROOF)
                    fixtures.write_manifest(artifact.with_suffix('.json'), manifest)
                if state != 'COMPLETED' or exit_code != '0:0':
                    return 1
                evidence = verify_evidence(task, request)
                path = task / 'results/evidence.json'
                if path.resolve() != path or path.is_symlink():
                    raise ValueError('untrusted science evidence path')
                path.write_text(json.dumps(evidence, sort_keys=True) + '\n')
                manifest[PROOF] = dict(run_id=args.run_id, job=job, verified=True,
                    identity=request['identity'], install_prefix=request['install_prefix'],
                    partition=request['identity']['partition'], **resources(request['target']),
                    actual_allocated_cpus=evidence['allocated_cpus'], evidence_sha256=checksum(path),
                    **{name: request[name] for name in ('artifact_sha256', 'recipe_sha256', 'source_sha',
                        'controller_sha256', 'helpers_sha256', 'launcher', 'launcher_sha256',
                        'job_script_sha256', 'rank_wrapper_sha256')})
                fixtures.write_manifest(artifact.with_suffix('.json'), manifest)
                status['verified'] = True
                status_path.write_text(json.dumps(status, sort_keys=True) + '\n')
                print('|'.join(records[0]), flush=True)
                return 0
        time.sleep(args.interval)
    raise TimeoutError('science deadline reached; the job has not been cancelled: ' + job)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='op', required=True)
    command = commands.add_parser('submit')
    for name in ('run_id', 'version'):
        command.add_argument(name)
    command.add_argument('target', choices=TARGET_NAMES)
    command.add_argument('--build-run-id', required=True)
    command.add_argument('--minutes', type=int, default=60)
    command = commands.add_parser('monitor')
    command.add_argument('run_id')
    command.add_argument('--timeout', type=int, default=14400)
    command.add_argument('--interval', type=int, default=15)
    args = parser.parse_args()
    return {'submit': submit, 'monitor': monitor}[args.op](args) or 0


if __name__ == '__main__':
    raise SystemExit(main())
