"""Scheduler-bound QE acceptance through host MPI and the final read-only SIF."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

from remote_controller import TARGETS, safe_name, project_batch_directives, project_qos, private_scratch
from release_contract import PARTITIONS, validate_identity
from delivery_layout import artifact_path, load_artifact, validate_record
from source_cache import checksum
from qe_science import recheck

ROOT = Path(os.environ.get('SAI_SOFTWARE_ROOT', Path.home()/'sai-hpc-software')).resolve()
CONTROL = Path(__file__).resolve().parent


def regular(path):
    if not path.is_file() or path.is_symlink() or path.resolve() != path:
        raise ValueError('missing or redirected QE evidence: ' + str(path))
    return path


def run_dir(run_id):
    task = ROOT/'runtime-tests'/safe_name(run_id)
    if task.resolve() != task:
        raise ValueError('redirected QE acceptance directory')
    return task


def render_job(request, task):
    identity = validate_identity(request['identity'])
    if (identity['software'] != 'qe' or identity['target'] != request['target'] or
            artifact_path(ROOT, identity, request['build_run_id']) != Path(request['artifact'])):
        raise ValueError('QE acceptance identity mismatch')
    target = TARGETS[request['target']]
    gpu = bool(target['gpus'])
    q = shlex.quote
    scratch = ROOT/'runtime'/('qe-' + request['run_id'])
    lines = ['#!/usr/bin/env bash', '#SBATCH --job-name=qe-science-' + request['run_id'],
             '#SBATCH --partition=' + target['partition'], '#SBATCH --qos=' + project_qos(ROOT, request['target']),
             '#SBATCH --nodes=1', '#SBATCH --time=20', '#SBATCH --export=NIL',
             '#SBATCH --output=' + str(task/'results/slurm-%j.log'), *project_batch_directives(ROOT, task)]
    lines += ['#SBATCH --ntasks=2', '#SBATCH --gpus-per-node=2'] if gpu else [
        '#SBATCH --ntasks=2', '#SBATCH --cpus-per-task=2', '#SBATCH --mem=8G']
    lines += ['set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH= LD_PRELOAD=',
              'source /etc/profile.d/lmod.sh', 'export HOME=' + q(str(Path.home())),
              'module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-pgi-cuda12-auto', 'set -u',
              'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
              'export SAI_SOFTWARE_ROOT=' + q(str(ROOT)), 'export SAI_QE_IMAGE=' + q(request['artifact']),
              'export TMPDIR=' + q(str(scratch)),
              'export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher',
              'export OMPI_MCA_shmem_mmap_backing_file_base_dir=/dev/shm OMPI_MCA_shmem_mmap_relocate_backing_file=1',
              'export UCX_WARN_UNUSED_ENV_VARS=n',
              'hostname -f > ' + q(str(task/'results/node.txt'))]
    if gpu:
        lines += ['source /opt/sai_config/mps_mapping.d/' + target['partition'] + '.bash',
                  'nvidia-smi --query-gpu=name,uuid,driver_version --format=csv,noheader > ' + q(str(task/'results/gpu-host.txt'))]
    command = ['python3', str(CONTROL/'qe_science.py'), 'run', str(task/'results/science'), '--launcher', request['launcher']]
    if gpu:
        command += ['--gpu']
    lines += [shlex.join(command), 'echo QE_SCF_PH_PP_HOST_MPI_SIF_ACCEPTED']
    return '\n'.join(lines)+'\n'


def submit(args):
    if args.target not in PARTITIONS:
        raise ValueError('unregistered QE partition')
    task = run_dir(args.run_id)
    if task.exists():
        raise ValueError('QE acceptance run already exists')
    artifact = Path(regular(ROOT/'runs'/safe_name(args.build_run_id)/'artifact.path').read_text().strip())
    record = load_artifact(ROOT, artifact, software='qe', target=args.target)
    if record['identity']['source_version'] != args.version:
        raise ValueError('QE version differs from requested candidate')
    request = dict(vars(args), identity=record['identity'], artifact=str(artifact), artifact_sha256=record['sha256'],
                   launcher=str(CONTROL/'qe'))
    for name, key in (('qe', 'launcher_sha256'), ('qe_acceptance.py', 'controller_sha256'),
                      ('qe_science.py', 'science_sha256'), ('qe_fixture.json', 'fixture_sha256')):
        request[key] = checksum(regular(CONTROL/name))
    (task/'results').mkdir(parents=True)
    private_scratch(ROOT/'runtime'/('qe-'+args.run_id))
    script = task/'job.sbatch'
    script.write_text(render_job(request, task))
    request['job_script_sha256'] = checksum(script)
    (task/'request.json').write_text(json.dumps(request, sort_keys=True)+'\n')
    subprocess.run(['bash', '-n', script], check=True)
    job = subprocess.check_output(['sbatch', '--parsable', script], text=True, cwd=task).strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('invalid QE job ID')
    (task/'job.id').write_text(job+'\n')
    print(job, flush=True)


def accounting(job):
    if not job.isdigit():
        raise ValueError('invalid job ID')
    rows = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', job,
                                    '-o', 'JobIDRaw,State,ExitCode,WorkDir'], text=True)
    return next((line.split('|')[:4] for line in rows.splitlines() if line.split('|')[0] == job), None)


def verify_evidence(task, request):
    if task != run_dir(request['run_id']):
        raise ValueError('QE acceptance run ID changed')
    record = load_artifact(ROOT, Path(request['artifact']), software='qe', target=request['target'])
    if record['identity'] != request['identity'] or record['sha256'] != request['artifact_sha256']:
        raise ValueError('QE accepted image changed')
    for path, key in ((task/'job.sbatch', 'job_script_sha256'), (CONTROL/'qe', 'launcher_sha256'),
                      (CONTROL/'qe_acceptance.py', 'controller_sha256'), (CONTROL/'qe_science.py', 'science_sha256'),
                      (CONTROL/'qe_fixture.json', 'fixture_sha256')):
        if checksum(regular(path)) != request[key]:
            raise ValueError('QE acceptance code/fixture changed: '+key)
    if (task/'job.sbatch').read_text() != render_job(request, task):
        raise ValueError('QE acceptance job no longer matches its request')
    job = regular(task/'job.id').read_text().strip()
    if accounting(job) != [job, 'COMPLETED', '0:0', str(task)]:
        raise ValueError('QE scheduler completion/workdir not verified')
    science = task/'results/science'
    results = recheck(science, gpu=request['target'] != 'dsprhbm')
    files = {}
    for ranks in (1, 2):
        case = science/('mpi'+str(ranks))
        for name in ('scf.in', 'ph.in', 'pp.in', 'Si.pz-vbc.UPF', 'pw.out'):
            files[str((case/name).relative_to(task))] = checksum(regular(case/name))
        for rank in range(ranks):
            trace = regular(case/'ranks'/f'rank-{rank}.tsv')
            fields = trace.read_text().strip().split('\t')
            mpi = '/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-pgi-cuda12-' + request['identity']['dependency_isa']
            if (len(fields) != 6 or fields[1] != str(rank) or fields[2] != request['target'] or
                    fields[3] != request['artifact'] or fields[5] != mpi):
                raise ValueError('QE host MPI rank/ABI evidence mismatch')
            files[str(trace.relative_to(task))] = checksum(trace)
    for name in ('ph.out', 'pp.out', 'rho.cube'):
        path = regular(science/'mpi2'/name)
        files[str(path.relative_to(task))] = checksum(path)
    if request['target'] != 'dsprhbm':
        path = regular(task/'results/gpu-host.txt')
        if 'V100' not in path.read_text():
            raise ValueError('QE V100 allocation metadata missing')
        files['results/gpu-host.txt'] = checksum(path)
    return dict(verified=True, identity=request['identity'], artifact_sha256=request['artifact_sha256'],
                job=job, checks=results, files=files, controller_sha256=request['controller_sha256'],
                job_script_sha256=request['job_script_sha256'])


def validate_manifest(manifest, root=None, control=None):
    global ROOT, CONTROL
    ROOT, CONTROL = Path(root or ROOT), Path(control or CONTROL)
    proof = manifest.get('qe_science', {})
    if proof.get('verified') is not True:
        raise ValueError('QE scientific acceptance is missing')
    task = run_dir(proof['run_id'])
    request = json.loads(regular(task/'request.json').read_text())
    status = json.loads(regular(task/'results/status.json').read_text())
    if (request['identity'] != validate_record(manifest) or proof['identity'] != request['identity'] or
            request['artifact_sha256'] != manifest['sha256'] or request['artifact'] != manifest['artifact'] or
            request['launcher'] != str(CONTROL/'qe') or proof['launcher_sha256'] != request['launcher_sha256'] or
            status != dict(job=proof['job'], state='COMPLETED', exit_code='0:0', verified=True)):
        raise ValueError('QE acceptance proof does not belong to this image')
    evidence = verify_evidence(task, request)
    if (json.loads(regular(task/'results/evidence.json').read_text()) != evidence or
            any(proof.get(key) != value for key, value in evidence.items())):
        raise ValueError('QE raw scientific evidence changed')


def monitor(args):
    task = run_dir(args.run_id)
    job = regular(task/'job.id').read_text().strip()
    deadline = time.monotonic()+args.timeout
    while time.monotonic() < deadline:
        row = accounting(job)
        if row and row[1] not in ('RUNNING', 'PENDING', 'COMPLETING', 'CONFIGURING'):
            status = dict(job=job, state=row[1], exit_code=row[2], verified=False)
            (task/'results/status.json').write_text(json.dumps(status)+'\n')
            if row[1:3] != ['COMPLETED', '0:0']:
                return 1
            request = json.loads(regular(task/'request.json').read_text())
            evidence = verify_evidence(task, request)
            (task/'results/evidence.json').write_text(json.dumps(evidence, sort_keys=True)+'\n')
            artifact = Path(request['artifact'])
            manifest = json.loads(regular(artifact.with_suffix('.json')).read_text())
            manifest['qe_science'] = dict(evidence, run_id=args.run_id, launcher_sha256=request['launcher_sha256'])
            from software_controller import atomic_manifest
            atomic_manifest(artifact, manifest)
            status['verified'] = True
            (task/'results/status.json').write_text(json.dumps(status)+'\n')
            print(json.dumps(evidence), flush=True)
            return 0
        print('QE', job, row[1] if row else 'awaiting accounting', flush=True)
        time.sleep(20)
    raise TimeoutError('QE job remains owned by this run; re-monitor '+job)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    operations = parser.add_subparsers(dest='op', required=True)
    submit_parser = operations.add_parser('submit')
    for name in ('run_id', 'version', 'target'):
        submit_parser.add_argument(name)
    submit_parser.add_argument('--build-run-id', required=True)
    monitor_parser = operations.add_parser('monitor')
    monitor_parser.add_argument('run_id')
    monitor_parser.add_argument('--timeout', type=int, default=7200)
    args = parser.parse_args()
    raise SystemExit({'submit':submit, 'monitor':monitor}[args.op](args) or 0)
