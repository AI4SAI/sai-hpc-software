"""Revalidate exact daily artifacts with each build's immutable controller."""
import json
import os
from pathlib import Path
import re
import subprocess

from release_contract import SOFTWARE, validate_identity
from remote_controller import SAI_PROJECT_ROOT, safe_name, safe_sha, site_app_binds
from sif_runtime import digest, require

PROJECT = SAI_PROJECT_ROOT


def read(path):
    require(path.resolve() == path and path.is_file(), 'missing or redirected evidence: ' + str(path))
    return json.loads(path.read_text())


def run_controller(control, code, *args):
    require(control.resolve() == control and control.is_relative_to(PROJECT), 'untrusted frozen controller')
    result = subprocess.run(['/usr/bin/python3', '-I', '-B', '-c',
        'import sys; sys.path.insert(0, sys.argv[1]); ' + code, str(control), *map(str, args)],
        env=dict(os.environ, SAI_SOFTWARE_ROOT=str(PROJECT)), text=True, capture_output=True)
    require(result.returncode == 0, result.stderr[-4000:] or result.stdout[-4000:])
    return json.loads(result.stdout.splitlines()[-1])


def validate_plan(software, builds):
    require(software in SOFTWARE, 'unknown beta application')
    selected = [row for row in builds if row['track'] == 'development']
    require(len(selected) == len(SOFTWARE[software]['targets'])
            and {row['target'] for row in selected} == set(SOFTWARE[software]['targets']),
            'beta rotation requires the complete development partition matrix')
    require(len({row['source_sha'] for row in selected}) == 1, 'mixed development source SHAs')
    for row in selected:
        safe_sha(row['source_sha'])
        require(row['software'] == software and row['status'] == 'resolved'
                and row['source_ref'] == SOFTWARE[software]['development_ref']
                and row['repository'] == SOFTWARE[software]['repository'], 'invalid beta source plan')
    return selected


def locate(software, workflow, attempt, source):
    require(re.fullmatch(r'[1-9][0-9]*', workflow) and re.fullmatch(r'[1-9][0-9]*', attempt),
            'invalid workflow run/attempt')
    target, sha = source['target'], source['source_sha'][:12]
    if software == 'deepmd-kit':
        root = PROJECT / 'experimental/deepmd-standalone/runs'
        pattern = f'dp-{workflow}-*-development-{target}-{sha}'
    elif software == 'lammps':
        root = PROJECT / 'experimental/deepmd-lammps/runs'
        pattern = f'lmp-{workflow}-*-development-{target}-????????-{sha}'
    else:
        root = PROJECT / 'runs'
        pattern = ('gpumd-' if software == 'gpumd' else '') + f'{workflow}-*-development-{target}-????-??-??-{sha}'
    matches = []
    for path in root.glob(pattern):
        value = path.name.split(workflow + '-', 1)[1].split('-', 1)[0]
        if value.isdigit() and 1 <= int(value) <= int(attempt):
            matches.append((int(value), path))
    highest = max((number for number, _ in matches), default=0)
    matches = [path for number, path in matches if number == highest]
    require(len(matches) == 1 and matches[0].resolve() == matches[0], 'missing or ambiguous daily build: ' + pattern)
    return matches[0]


def build_recovery(software, directory):
    """Follow one recorded, independently accepted rebuild of the same source."""
    proof = directory / 'results/build-recovery.json'
    if software != 'cp2k' or not proof.exists():
        return directory, {}
    record = read(proof)
    original = read(directory / 'request.json')
    require(record.get('schema') == 1 and record.get('source_run') == directory.name
            and record.get('source_request_sha256') == digest(directory / 'request.json'),
            'build recovery does not bind the original request')
    recovered = PROJECT / 'runs' / safe_name(record['run_id'])
    require(recovered != directory and recovered.resolve() == recovered
            and not (recovered / 'results/build-recovery.json').exists(), 'invalid chained build recovery')
    request = read(recovered / 'request.json')
    require(record.get('request_sha256') == digest(recovered / 'request.json'), 'changed recovery request')
    before, after = (validate_identity(value['identity']) for value in (original, request))
    for key in ('software', 'track', 'source_sha', 'source_ref', 'source_version', 'repository',
                'target', 'partition', 'cpu_arch', 'dependency_isa', 'cuda_arch', 'gpus',
                'stack_digest', 'stack_sources'):
        require(before[key] == after[key], 'build recovery changed the selected source or target: ' + key)
    # candidate() still reruns the recovered controller's complete build and
    # scientific verification. This pointer alone never constitutes acceptance.
    return recovered, dict(build_recovery=dict(source_run=directory.name, run_id=recovered.name,
                           evidence=str(proof), evidence_sha256=digest(proof)))


def candidate(software, directory, source):
    directory, extra = build_recovery(software, directory)
    request = read(directory / 'request.json')
    control = Path(request['controller'])
    require('/controller/' in str(control), 'build lacks a frozen controller')
    if software in ('abacus', 'cp2k', 'gpumd'):
        identity = validate_identity(request['identity'])
        from delivery_layout import artifact_path
        image = artifact_path(PROJECT, identity, directory.name)
        proof = image.with_suffix('.json')
        record = run_controller(control,
            'import json; from pathlib import Path; from software_controller import validate_candidate; '
            'p=Path(sys.argv[2]); m=json.loads(p.with_suffix(".json").read_text()); '
            'validate_candidate(m,p); print(json.dumps(m))', image)
        sha = record['sha256']
    elif software == 'lammps':
        proof = PROJECT / 'experimental/deepmd-lammps/runtime-tests' / (directory.name + '-science') / 'verification.json'
        record = run_controller(control,
            'from pathlib import Path; from lammps_acceptance import verify; verify(Path(sys.argv[2]))', proof.parent)
        identity = validate_identity(record['delivery']['identity'])
        extra['installed_deepmd_dependency'] = record['delivery']['deepmd_dependency']
        image, sha = directory / 'candidate.sif', record['artifact_sha256']
    else:
        record = run_controller(control,
            'import deepmd_standalone as d; d.status(sys.argv[2])', directory.name)
        require(record.get('build_verified') is True, 'DeePMD final SIF is not build-verified')
        identity = validate_identity(record['delivery']['identity'])
        image, sha = directory / 'candidate.sif', record['artifact_sha256']
        proof = directory / 'results/status.json'
        if not record.get('scientific_verified'):
            retry = read(directory / 'results/acceptance-recovery.json')
            require(retry['source_run'] == directory.name, 'recovery points at another source build')
            record = run_controller(Path(retry['controller']),
                'import deepmd_acceptance as a; a.status(sys.argv[2])', safe_name(retry['run_id']))
            require(record['source']['artifact_sha256'] == sha
                    and record['source']['source_run'] == directory.name, 'recovery changed the SIF')
            proof = directory.parent / retry['run_id'] / 'results/status.json'
        require(record.get('scientific_verified') is True, 'DeePMD science has not passed')
        environment = directory / 'runtime/runtime-env.sh'
        extra.update(environment=environment.read_text(), environment_sha256=digest(environment))
    for key in ('software', 'target', 'source_sha', 'source_ref', 'source_version', 'track'):
        require(identity[key] == source[key], 'daily candidate differs from the resolved plan: ' + key)
    require(image.resolve() == image and image.is_file() and not image.stat().st_mode & 0o222,
            'daily image is missing, redirected or writable')
    require(digest(image) == sha, 'daily image checksum changed')
    binds = [str(row[0]) for row in site_app_binds(software)
             if not str(row[0]).startswith('/opt/apps/lammps/')] if software in ('gpumd', 'deepmd-kit', 'lammps') else []
    if software == 'abacus':
        binds = ['/opt/apps/conda_env/deepmd-kit-3.1.2']
    if software == 'lammps':
        binds.append(extra['installed_deepmd_dependency']['identity']['install_prefix'])
    if software != 'gpumd':
        extra['mpi_prefix'] = '/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-' + identity['dependency_isa']
    return dict(identity=identity, image=str(image), sha256=sha, binds=list(dict.fromkeys(binds)),
                build_verified=True, scientific_verified=True, evidence=str(proof), evidence_sha256=digest(proof), **extra)
