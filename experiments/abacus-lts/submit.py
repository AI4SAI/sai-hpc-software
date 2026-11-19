#!/usr/bin/env python3
"""Submit an isolated LTS build using the normal offline SIF lifecycle.

Run on the SAI login host after the verified source archive is staged.
This does not publish any public module or relax develop's feature contract.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / 'controller'))
from remote_controller import SAI_PROJECT_ROOT, TARGETS, safe_name, safe_sha, project_qos


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--controller-sha', required=True)
    parser.add_argument('--source-record', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--target', choices=[t for t in TARGETS if t != 'a100'], required=True)
    parser.add_argument('--exclude', default='')
    parser.add_argument('--minutes', type=int, default=120)
    parsed = parser.parse_args()
    safe_sha(parsed.source_sha)
    safe_sha(parsed.controller_sha)
    safe_name(parsed.run_id)
    if not parsed.version.startswith('LTS-'):
        parser.error('LTS version label required')
    if parsed.exclude:
        safe_name(parsed.exclude)
    root = SAI_PROJECT_ROOT
    source = json.loads(Path(parsed.source_record).read_text())
    if (source.get('source_sha') != parsed.source_sha or source.get('tree_verified') is not True
            or source.get('source_ref') != 'LTS'):
        raise ValueError('source archive does not match the requested LTS commit')
    safe_name(source['archive'])
    archive = root / 'cache/abacus-dependencies' / source['archive']
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != source['archive_sha256']:
            raise ValueError('source archive checksum mismatch')
    os.environ['SAI_SOFTWARE_ROOT'] = str(root)
    import software_controller as controller
    control = root / 'controller' / parsed.controller_sha / parsed.run_id
    if control.exists() or (root / 'runs' / parsed.run_id).exists():
        raise ValueError('run already exists; inspect it rather than resubmit')
    control.mkdir(parents=True)
    for path in (REPOSITORY / 'controller').iterdir():
        if path.is_file() and path.suffix in ('.py', '.sh', '.json', '.cmake', '.f90'):
            shutil.copyfile(path, control / path.name)
    shutil.copyfile(REPOSITORY / 'controller/abacus_runtime.sh', control / 'abacus')
    for source, destination in [('build.sh', 'abacus_build.sh'), ('container_entry.sh', 'container_entry.sh')]:
        shutil.copyfile(Path(__file__).with_name(source), control / destination)
    lock = control / 'abacus_dependency_lock.json'
    dependencies = json.loads(lock.read_text())
    dependencies['lts_source'] = json.loads(Path(parsed.source_record).read_text())
    lock.write_text(json.dumps(dependencies, indent=2) + '\n')
    manifest = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in control.iterdir() if path.is_file()}
    (control / 'lts-recipe.json').write_text(json.dumps(dict(
        controller_sha=parsed.controller_sha, source_sha=parsed.source_sha,
        source_ref='LTS', files=manifest, publication='disabled: separate LTS pilot'), indent=2) + '\n')
    for path in control.iterdir():
        if path.is_file():
            path.chmod(0o555 if path.name == 'abacus' else 0o444)
    controller.CONTROL = control
    args = SimpleNamespace(software='abacus', run_id=parsed.run_id,
        sha=parsed.source_sha, version=parsed.version, target=parsed.target,
        track='development', source_ref='LTS', jobs=TARGETS[parsed.target].get('build_jobs', 8),
        minutes=parsed.minutes, overlay_mb=16384, resume_run=None, dependencies_run=None)
    from abacus_dependencies import cache_archives, ARCHIVE_CACHE
    cache_archives(root / ARCHIVE_CACHE)
    run = controller.init(args)
    args.identity = controller.request_identity(args)
    script = controller.render_job(args)
    script = script.replace('#SBATCH --qos=' + TARGETS[args.target]['qos'],
                            '#SBATCH --qos=' + project_qos(root, args.target))
    if parsed.exclude:
        script = script.replace('#SBATCH --nodes=1', '#SBATCH --exclude=' + parsed.exclude + '\n#SBATCH --nodes=1')
    if TARGETS[args.target]['gpus']:
        script = script.replace('set -u\n', 'set -u\nexport APPTAINERENV_CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:?}"\n', 1)
    path = run / 'job.sbatch'
    path.write_text(script)
    path.chmod(0o700)
    subprocess.run(['bash', '-n', str(path)], check=True)
    subprocess.run(['sbatch', '--test-only', str(path)], check=True)
    record = dict(vars(args), controller=str(control), contract_schema=controller.CONTRACT_SCHEMA,
                  recipe_sha256=controller.recipe_fingerprint('abacus'),
                  purpose='LTS build pilot; no automatic beta publication', exclude=parsed.exclude)
    (run / 'request.json').write_text(json.dumps(record, indent=2) + '\n')
    result = subprocess.run(['sbatch', '--parsable', str(path)], cwd=run,
                            check=True, text=True, capture_output=True)
    job = result.stdout.strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('invalid sbatch receipt')
    (run / 'job.id').write_text(job + '\n')
    print(json.dumps(dict(job=job, run=str(run), target=args.target,
                         controller=str(control), identity=args.identity)), flush=True)


if __name__ == '__main__':
    main()
