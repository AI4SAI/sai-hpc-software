#!/usr/bin/env python3
"""Prepare, never submit, an isolated same-SIF OT eigensolver comparison.

Run on SAI-stardust with --name pbe-diag-20261018-a. Both variants use the
formal 4V100 artifact and its frozen launcher. The ELPA input adds exactly
GLOBAL PREFERRED_DIAG_LIBRARY ELPA; SPLA and direct generalized diagonalization
are unchanged. One warmup and three measured repetitions per variant alternate
in a single two-node allocation. This cannot replace formal acceptance.
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
import statistics
import subprocess
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path('/home/stardust/sai-hpc-software')
BUILD = '39078998459-1-release-4v100-avx512-2026-10-17-67b5da876dd6'
ORIGINAL = BUILD + '-benchmark'
CONTROLLER = 'c3b65b97f9d95ecb29a453713dfe292af20f6d39'
RECIPE = 'b59c9f26a1846d5a3863c04cefc0f7b746b3078bc48cb0a8138d67084d28e150'
SOURCE = '67b5da876dd6a76b8b021d5a04d1c81ba79a4c50'
ARTIFACT_SHA = '1ec8fdf693f4273f7fa1c52fcc27b4aba858d6077a45ff569f2b421677aa3b00'
LAUNCHER_SHA = '21e5404e8b750c5f1526905ec59fa26e84dd816d36b46148ff310e3aa0b574c0'
REQUEST_SHA = 'adfd95175d170ecffae42950d152af2a46fea7616ae56b680ebcb7ea6a5924ca'
RANK_EXEC_SHA = '243f8e35620faf45b5451e4b57729c3fd17e51c61ff13c8f98dbfa6f47080126'
FIXTURE_HASHES = {
    'input.inp': '567175e994a44502c8afe54c98ec31cb12853f0cba114477596174950974834f',
    'BASIS_MOLOPT': '686dd72e7601ee41d4a173e4c7c3946f1e750f03095090f44d3821c180a81a61',
    'GTH_POTENTIALS': '5a9bfbc3b37a55917ade4fb704b80546d210d843759b94e3915e74acfc3fafb2',
}
SCOPE = 'same-formal-sif-eigensolver-diagnostic-not-formal-acceptance-or-publication'
ORDER = [(0, 'default'), (0, 'elpa'), (1, 'elpa'), (1, 'default'),
         (2, 'default'), (2, 'elpa'), (3, 'elpa'), (3, 'default')]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def regular(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink() and path.resolve() == path,
            'not a canonical regular file: ' + str(path))
    return path


def digest(path):
    value = hashlib.sha256()
    with regular(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def elpa_input(original):
    marker = '  RUN_TYPE ENERGY_FORCE\n'
    require(original.count(marker) == 1 and 'PREFERRED_DIAG_LIBRARY' not in original,
            'unexpected default fixture')
    result = original.replace(marker, marker + '  PREFERRED_DIAG_LIBRARY ELPA\n', 1)
    require(result.replace('  PREFERRED_DIAG_LIBRARY ELPA\n', '', 1) == original,
            'ELPA fixture has an unapproved input difference')
    return result


def render_job(task, lock):
    q = shlex.quote
    temporary = ROOT / 'runtime' / ('pd-' + hashlib.sha256(str(task).encode()).hexdigest()[:8])
    return '\n'.join([
        '#!/usr/bin/env bash', '#SBATCH --job-name=cp2k-same-sif-diag-comparison',
        '#SBATCH --partition=4V100', '#SBATCH --qos=flood-1o2gpu',
        '#SBATCH --nodes=2', '#SBATCH --ntasks=2', '#SBATCH --ntasks-per-node=1',
        '#SBATCH --gpus-per-node=1', '#SBATCH --time=30', '#SBATCH --export=NIL',
        '#SBATCH --output=' + str(task / 'results/slurm-%j.log'),
        'set -euo pipefail', 'umask 077', 'export HOME=/home/stardust',
        'export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}',
        '[[ "$USER" == stardust ]] || exit 2',
        'export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
        'export LD_LIBRARY_PATH="" LD_PRELOAD=""',
        'export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1',
        'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
        'export SAI_SOFTWARE_ROOT=' + q(str(ROOT)),
        'export SAI_CP2K_VERSION=v2026.2 SAI_CP2K_IMAGE=' + q(lock['artifact']),
        'export TMPDIR=' + q(str(temporary)),
        'export APPTAINER_TMPDIR=' + q(str(task / 'apptainer-runtime')),
        'export APPTAINER_CACHEDIR=' + q(str(task / 'apptainer-cache')),
        'mkdir -p "$TMPDIR" "$APPTAINER_TMPDIR" "$APPTAINER_CACHEDIR"',
        'cd ' + q(str(task)), 'sha256sum --check --status prepared.sha256',
        'set +u', 'source /etc/profile.d/lmod.sh', 'module purge',
        'export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"',
        'module use /opt/modules/modulefiles/devtools /opt/modules/modulefiles/apps',
        'module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto',
        'source /opt/sai_config/mps_mapping.d/4V100.bash', 'set -u',
        '[[ "${MAP_OPT,,}" == ppr:1:node:pe=4 ]] || exit 2',
        'export OMP_NUM_THREADS=1 OMP_PLACES=cores OMP_PROC_BIND=close',
        'export SLURM_EXPORT_ENV=ALL',
        'export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher',
        'export SAI_BENCH_KIND=candidate SAI_BENCH_TARGET=4v100-avx512',
        'export SAI_BENCH_BINARY=' + q(lock['launcher']),
        'scontrol show job -o "$SLURM_JOB_ID" > results/allocation.txt',
        'scontrol show hostnames "$SLURM_JOB_NODELIST" > results/nodes.txt',
        'module -t list > results/modules.txt 2>&1',
        'command -v mpirun > results/mpirun.txt',
        'mpirun --version > results/mpi-version.txt',
        'exec python3 input/runner.py --execute ' + q(str(task)), '',
    ])


def prepare(name):
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]{0,39}', name), 'invalid short diagnostic name')
    require(ROOT.resolve() == ROOT, 'noncanonical software root')
    original = ROOT / 'runtime-tests' / ORIGINAL
    request_path = regular(original / 'request.json')
    require(digest(request_path) == REQUEST_SHA, 'original benchmark request changed')
    request = json.loads(request_path.read_text())
    control = ROOT / 'controller' / CONTROLLER / BUILD
    require(control.resolve() == control, 'noncanonical frozen controller')
    sys.path.insert(0, str(control))
    from delivery_layout import load_artifact
    from software_controller import recipe_fingerprint
    from cp2k_benchmark import fixture_hashes, resources
    require(recipe_fingerprint('cp2k', control) == RECIPE, 'frozen recipe changed')
    identity = request['identity']
    require(request['run_id'] == ORIGINAL and request['build_run_id'] == BUILD
            and request['artifact_sha256'] == ARTIFACT_SHA
            and identity['source_sha'] == SOURCE and identity['recipe_sha256'] == RECIPE
            and identity['target'] == '4v100-avx512', 'unexpected original identity')
    require(fixture_hashes('water64-gpw') == FIXTURE_HASHES, 'frozen fixture definition differs')
    allocation = resources('4v100-avx512')
    require(all(request[key] == allocation[key] for key in allocation), 'original resource contract differs')
    launcher = regular(control / 'cp2k')
    require(str(launcher) == request['launcher'] and digest(launcher) == LAUNCHER_SHA,
            'frozen launcher changed')
    artifact = regular(request['artifact'])
    manifest = load_artifact(ROOT, artifact, software='cp2k', target='4v100-avx512')
    require(manifest['sha256'] == ARTIFACT_SHA and manifest['identity'] == identity
            and manifest['controller'] == str(control), 'formal SIF identity mismatch')
    rank_exec = regular(original / 'rank-exec.sh')
    require(digest(rank_exec) == RANK_EXEC_SHA, 'original rank observer changed')
    require(digest(original / 'job.sbatch') == request['job_script_sha256'], 'original job script changed')
    fixture = original / 'fixtures/water64-gpw/candidate'
    for filename, expected in FIXTURE_HASHES.items():
        require(digest(fixture / filename) == expected, 'formal fixture changed: ' + filename)
    default = (fixture / 'input.inp').read_text()
    variant = elpa_input(default)
    paths = [request_path, original / 'job.sbatch', rank_exec]
    paths += [fixture / filename for filename in FIXTURE_HASHES]
    paths += [path for path in sorted(control.rglob('*'))
              if path.is_file() and '__pycache__' not in path.parts]
    task = ROOT / 'runtime-tests' / name
    require(task.resolve() == task and not task.exists(), 'diagnostic already exists or has symlink parents')
    lock = dict(schema=1, scope=SCOPE, prepared_at=datetime.now(timezone.utc).isoformat(),
                original_benchmark=str(original), original_job=request['job'],
                identity=identity, artifact=str(artifact), artifact_sha256=ARTIFACT_SHA,
                controller=str(control), launcher=str(launcher), launcher_sha256=LAUNCHER_SHA,
                resource_contract=allocation, order=ORDER, warmups=1, measured_repetitions=3,
                scientific_input_difference='GLOBAL PREFERRED_DIAG_LIBRARY ELPA only',
                spla_gemm_offload_unchanged=True, direct_generalized_diagonalization_unchanged=True,
                fixture_hashes=FIXTURE_HASHES,
                input_hashes={key: hashlib.sha256(text.encode()).hexdigest()
                              for key, text in [('default', default), ('elpa', variant)]},
                locked_files={str(path): digest(path) for path in paths},
                sidecar_policy='Revalidate artifact identity/full SIF hash; allow formal acceptance metadata updates.')
    script = render_job(task, lock)
    subprocess.run(['bash', '-n'], input=script, text=True, check=True)
    task.mkdir(mode=0o700)
    (task / 'input').mkdir()
    (task / 'results').mkdir()
    (task / 'cases').mkdir()
    for filename in ('BASIS_MOLOPT', 'GTH_POTENTIALS'):
        shutil.copyfile(fixture / filename, task / 'input' / filename)
    (task / 'input/default.inp').write_text(default)
    (task / 'input/elpa.inp').write_text(variant)
    shutil.copyfile(Path(__file__).resolve(), task / 'input/runner.py')
    shutil.copyfile(rank_exec, task / 'rank-exec.sh')
    (task / 'rank-exec.sh').chmod(0o700)
    write_json(task / 'lock.json', lock)
    (task / 'job.sbatch').write_text(script)
    prepared = [task / 'lock.json', task / 'job.sbatch', task / 'rank-exec.sh']
    prepared += sorted((task / 'input').iterdir())
    (task / 'prepared.sha256').write_text(''.join(
        digest(path) + '  ' + str(path.relative_to(task)) + '\n' for path in prepared))
    print(json.dumps(dict(prepared=str(task), scope=SCOPE, submitted=False,
                         artifact_sha256=ARTIFACT_SHA, estimated_minutes='16-20',
                         submit_command='sbatch --parsable ' + shlex.quote(str(task / 'job.sbatch'))), indent=2))


def check_lock(task, lock):
    subprocess.run(['sha256sum', '--check', '--status', 'prepared.sha256'], cwd=task, check=True)
    for filename, expected in lock['locked_files'].items():
        require(digest(filename) == expected, 'locked file changed: ' + filename)
    from delivery_layout import load_artifact
    manifest = load_artifact(ROOT, Path(lock['artifact']), software='cp2k', target='4v100-avx512')
    require(manifest['sha256'] == ARTIFACT_SHA and manifest['identity'] == lock['identity']
            and manifest['controller'] == lock['controller'], 'formal artifact identity changed')


def rank_evidence(task, work, lock, hosts):
    from cp2k_benchmark import cpu_list, rank_topology
    records = []
    for rank in range(2):
        trace = (work / 'ranks' / ('rank-' + str(rank) + '.tsv')).read_text().strip().split('\t')
        detail = (work / 'resources' / ('rank-' + str(rank) + '.tsv')).read_text().strip().split('\t')
        require(len(trace) == 6 and len(detail) == 7, 'invalid rank/resource record')
        # Preserve raw records. Slurm and rank-exec use short names; frozen
        # launcher uses hostname, which is FQDN on these allocated nodes.
        host = trace[0].split('.')[0]
        require(host in hosts and detail[0] == host and trace[1:4] == [str(rank), '4v100-avx512', lock['artifact']],
                'rank artifact/node mapping mismatch')
        require(detail[1:4] == [str(rank), '0', '1'] and detail[5] == trace[4]
                and re.fullmatch(r'\d+', trace[4])
                and detail[6] == os.environ['SLURM_JOB_ID'] and trace[5].endswith('-avx512'),
                'rank resources differ from diagnostic contract')
        topology, cores = rank_topology(
            (work / 'resources' / ('topology-' + str(rank) + '.tsv')).read_text(),
            cpu_list(detail[4]), lock['resource_contract'])
        records.append(dict(host=host, rank=rank, affinity=detail[4], gpu=trace[4],
                            mpi_home=trace[5], topology=topology, physical_cores=len(cores)))
    require({row['host'] for row in records} == set(hosts)
            and len({row['mpi_home'] for row in records}) == 1, 'invalid two-node placement')
    return records


def execute(directory):
    task = Path(directory)
    require(task.resolve() == task and task.parent == ROOT / 'runtime-tests', 'invalid diagnostic directory')
    lock = json.loads(regular(task / 'lock.json').read_text())
    require(lock['scope'] == SCOPE and lock['artifact_sha256'] == ARTIFACT_SHA
            and lock['controller'] == str(ROOT / 'controller' / CONTROLLER / BUILD), 'wrong diagnostic lock')
    require(os.environ.get('SLURM_JOB_PARTITION') == '4V100'
            and os.environ.get('SLURM_JOB_NUM_NODES') == '2'
            and os.environ.get('SLURM_NTASKS') == '2', 'diagnostic needs two nodes and two MPI ranks')
    require(not any((task / 'cases').iterdir()), 'diagnostic cases already exist; never overwrite results')
    sys.path.insert(0, lock['controller'])
    from cp2k_benchmark import parse_science, compare_science, ENERGY_TOLERANCE_HA, FORCE_TOLERANCE_HA_BOHR
    check_lock(task, lock)
    hosts = (task / 'results/nodes.txt').read_text().splitlines()
    require(len(hosts) == len(set(hosts)) == 2, 'expected two distinct allocated nodes')
    report = dict(schema=1, scope=SCOPE, passed=False, job=os.environ['SLURM_JOB_ID'],
                  artifact=lock['artifact'], artifact_sha256=ARTIFACT_SHA, samples={},
                  energy_tolerance_ha=ENERGY_TOLERANCE_HA, force_tolerance_ha_bohr=FORCE_TOLERANCE_HA_BOHR)
    placement = None
    try:
        for repetition, variant in ORDER:
            name = str(repetition) + '-' + variant
            work = task / 'cases' / name
            work.mkdir()
            for child in ('ranks', 'resources'):
                (work / child).mkdir()
            shutil.copyfile(task / 'input' / (variant + '.inp'), work / 'input.inp')
            for filename in ('BASIS_MOLOPT', 'GTH_POTENTIALS'):
                shutil.copyfile(task / 'input' / filename, work / filename)
            env = dict(os.environ, SAI_CP2K_TRACE_DIR=str(work / 'ranks'),
                       SAI_BENCH_RESOURCES=str(work / 'resources'))
            with (task / 'results/run-order.tsv').open('a') as order:
                order.write(str(repetition) + '\t' + variant + '\n')
            started = time.monotonic()
            with (work / 'cp2k.log').open('w') as output:
                process = subprocess.run(['mpirun', '-np', '2', '--map-by', os.environ['MAP_OPT'],
                                          '--report-bindings', str(task / 'rank-exec.sh'), '-i', 'input.inp'],
                                         cwd=work, env=env, stdout=output, stderr=subprocess.STDOUT)
            elapsed = time.monotonic() - started
            (work / 'elapsed.txt').write_text(str(elapsed) + '\n')
            require(process.returncode == 0, name + ' MPI/CP2K exit ' + str(process.returncode))
            log = (work / 'cp2k.log').read_text()
            science = parse_science(log, (work / 'forces.xyz').read_text(), str(elapsed), 'water64-gpw')
            expected_backend = 'ELPA' if variant == 'elpa' else 'cuSOLVER'
            require(re.search(r'^ GLOBAL\| Diagonalization library\s+' + expected_backend + r'\s*$', log, re.M),
                    'unexpected eigensolver for ' + name)
            require(re.search(r'^ GLOBAL\| DGEMM library\s+SPLA\s*$', log, re.M), 'SPLA DGEMM changed')
            require(re.search(r'^ GLOBAL\| Direct generalized diagonalization requested\s+F\s*$', log, re.M),
                    'direct generalized diagonalization changed')
            current = rank_evidence(task, work, lock, hosts)
            require(placement is None or current == placement, 'resource placement changed between variants')
            placement = current
            sample = dict(variant=variant, repetition=repetition, warmup=repetition == 0,
                          science=science, placement=current, input_sha256=digest(work / 'input.inp'),
                          scf_summary=re.findall(r'outer SCF loop converged.*', log),
                          timer_rows=[line for line in log.splitlines()
                                      if re.match(r'^\s*(CP2K|cp_fm_diag_cusolver|cp_fm_diag_elpa(?:_base)?)\s+\d+\s+', line)],
                          log_sha256=digest(work / 'cp2k.log'), forces_sha256=digest(work / 'forces.xyz'))
            require(sample['input_sha256'] == lock['input_hashes'][variant], 'case input changed')
            report['samples'][name] = sample
            write_json(work / 'result.json', sample)
            write_json(task / 'results/diagnostic-result.json', report)
            print(name + ' CP2K_SECONDS ' + str(science['cp2k_seconds']), flush=True)
        check_lock(task, lock)
        report['comparisons'] = {str(repetition): compare_science(
            report['samples'][str(repetition) + '-default']['science'],
            report['samples'][str(repetition) + '-elpa']['science']) for repetition in range(4)}
        report['timings'] = {}
        for clock in ('cp2k_seconds', 'elapsed_seconds'):
            values = {variant: [report['samples'][str(i) + '-' + variant]['science'][clock]
                                for i in range(1, 4)] for variant in ('default', 'elpa')}
            report['timings'][clock] = dict(samples=values,
                medians={variant: statistics.median(items) for variant, items in values.items()},
                default_over_elpa=statistics.median(values['default']) / statistics.median(values['elpa']))
        report.update(passed=True, formal_files_unchanged=True,
                      performance_gate='none; diagnostic reports measured timings without declaring formal acceptance',
                      timing_scope='CP2K internal total versus separate end-to-end MPI/SIF launch time')
    except Exception as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)
    write_json(task / 'results/diagnostic-result.json', report)
    print('DIAG_BACKEND_COMPARISON ' + json.dumps({key: value for key, value in report.items() if key != 'samples'}), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--name', default='pbe-diag-20261018-a')
    parser.add_argument('--execute', metavar='TASK', help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.execute:
        sys.exit(execute(arguments.execute))
    prepare(arguments.name)
