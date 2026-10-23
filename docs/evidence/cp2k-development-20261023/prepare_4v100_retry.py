#!/usr/bin/env python3
"""One fresh application attempt with the original sealed dependencies."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path('/org/org-open-sai/proj-sai-hpc-software')
OLD_ID = '39983165813-1-development-4v100-avx512-2026-10-23-cfe5e5d87aaf'
NEW_ID = OLD_ID + '-mount-retry1'
CONTROL = ROOT / 'controller/10992f818af71543bb39cb81e0f60c9f7ede9535' / OLD_ID
SOURCE_SHA = 'cfe5e5d87aaf302953f14eb43587bef2411a8fb2'
RECIPE_SHA = 'ae6467ac2934deecf1d610c9d60cb8c5100cf931d2c27aaa66819ba85ca76a79'


def digest(path):
    path = Path(path)
    if not path.is_file() or path.resolve() != path or path.is_symlink():
        raise ValueError('input must be a canonical regular file: ' + str(path))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n')


def main():
    old, new = ROOT / 'runs' / OLD_ID, ROOT / 'runs' / NEW_ID
    request = json.loads((old / 'request.json').read_text())
    identity = request['identity']
    if (identity['source_sha'] != SOURCE_SHA or identity['recipe_sha256'] != RECIPE_SHA
            or request['controller'] != str(CONTROL) or request['target'] != '4v100-avx512'
            or request['dependencies_run'] != OLD_ID + '-dependencies'):
        raise ValueError('original source/recipe/checkpoint differs')
    state = subprocess.check_output(['sacct', '-X', '-nP', '-j', '1914241',
                                     '-o', 'JobIDRaw,State,ExitCode'], text=True).strip()
    if state != '1914241|FAILED|255:0':
        raise ValueError('original failed allocation differs: ' + state)
    log = (old / 'results/slurm-1914241.log').read_text()
    if ('CP2K_EXACT_DEPENDENCY_CHECKPOINT_CLONED' not in log or
            'fuse2fs failed to mount' not in log or 'in 10s' not in log):
        raise ValueError('not the reviewed pre-build mount timeout')
    if (old / 'artifact.path').exists():
        raise ValueError('original attempt unexpectedly has an artifact')
    preparation = json.loads((old / 'results/overlay-workdir-preparation.json').read_text())
    if preparation['status'] != 'prepared' or not preparation['source_unchanged']:
        raise ValueError('original clone did not finish its integrity checks')
    updates = old / 'input/cp2k-updates'
    archives = {path.name: digest(path) for path in updates.iterdir()}
    tracked = [old / name for name in ('request.json', 'job.id', 'job.sbatch',
               'results/slurm-1914241.log', 'results/overlay-workdir-preparation.json')]
    deps = ROOT / 'runs' / request['dependencies_run']
    tracked += [deps / name for name in ('request.json', 'job.id', 'job.sbatch', 'results/dependency-stage.json')]
    tracked += list(updates.iterdir())
    control_hashes = {str(path.relative_to(CONTROL)): digest(path)
                      for path in CONTROL.rglob('*') if path.is_file() and '__pycache__' not in path.parts}
    tracked += [CONTROL / name for name in control_hashes]
    observed = {str(path): digest(path) for path in tracked}
    env = {key: value for key, value in os.environ.items() if not key.startswith('PYTHON')}
    env.update(SAI_SOFTWARE_ROOT=str(ROOT), PYTHONDONTWRITEBYTECODE='1')
    cli = ['/usr/bin/python3', '-B', '-s', str(CONTROL / 'software_controller.py')]
    computed = subprocess.check_output([*cli, 'identity', 'cp2k', SOURCE_SHA,
        identity['source_version'], '4v100-avx512', '--track', 'development', '--source-ref', 'master'],
        env=env, text=True)
    if json.loads(computed) != identity:
        raise ValueError('frozen controller no longer computes the original identity')
    new.mkdir(mode=0o700)
    subprocess.run([*cli, 'init', 'cp2k', NEW_ID], env=env, check=True)
    destination = new / 'input/cp2k-updates'
    destination.mkdir()
    for name, expected in archives.items():
        with (updates / name).open('rb') as incoming, (destination / name).open('xb') as outgoing:
            shutil.copyfileobj(incoming, outgoing)
        if digest(destination / name) != expected:
            raise ValueError('copied archive mismatch: ' + name)
    argv = [*cli, 'submit', 'cp2k', NEW_ID, SOURCE_SHA, identity['source_version'], '4v100-avx512',
        '--track', 'development', '--source-ref', 'master', '--dependencies-run', request['dependencies_run'],
        '--overlay-mb', str(request['overlay_mb']), '--jobs', str(request['jobs']),
        '--minutes', str(request['minutes'])]
    plan = dict(purpose=__doc__, prepared_at=datetime.now(timezone.utc).isoformat(),
        original_run=OLD_ID, original_job='1914241', new_run=NEW_ID, identity=identity,
        controller=str(CONTROL), dependencies_run=request['dependencies_run'],
        controller_file_sha256=control_hashes, recovery_script_sha256=digest(Path(__file__).resolve()),
        input_archives=archives, original_small_file_sha256=observed, argv=argv,
        dependency_hash_verification='original frozen submit and original compute-node clone',
        original_failed_overlay_reused=False, mount_timeout_changed=False,
        original_workflow_success_claimed=False)
    write_json(new / 'input/retry-plan.json', plan)
    if any(digest(Path(name)) != expected for name, expected in observed.items()):
        raise ValueError('original inputs changed before retry')
    write_json(new / 'input/submission-attempt.json', dict(started_at=datetime.now(timezone.utc).isoformat(),
        argv=argv, rule='one submission only; inspect retained run on any interruption'))
    with (new / 'results/submit.stdout').open('x') as out, (new / 'results/submit.stderr').open('x') as err:
        result = subprocess.run(argv, cwd=ROOT, env=env, stdout=out, stderr=err)
    receipt = dict(returncode=result.returncode, run_id=NEW_ID,
                   original_small_files_unchanged=all(digest(Path(name)) == value for name, value in observed.items()))
    if result.returncode == 0:
        receipt['job_id'] = (new / 'job.id').read_text().strip()
        fresh = json.loads((new / 'request.json').read_text())
        if fresh['identity'] != identity or fresh['dependencies_run'] != request['dependencies_run']:
            raise ValueError('submitted retry identity/checkpoint differs')
        receipt['request_sha256'] = digest(new / 'request.json')
        receipt['job_script_sha256'] = digest(new / 'job.sbatch')
    write_json(new / 'results/retry-submitted.json', receipt)
    print(json.dumps(receipt, sort_keys=True), flush=True)
    if result.returncode or not receipt['original_small_files_unchanged']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
