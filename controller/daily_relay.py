#!/usr/bin/env python3
"""One small VPS worker, concurrent Slurm deliveries, durable frozen generations."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

from daily_plan import plan
from release_contract import SOFTWARE, TRACKS, resolve_tracks
from relay_checkpoint import inspect_jobs, remote, save
from remote_controller import safe_name, safe_sha

REPO = Path(__file__).resolve().parents[1]
SNAPSHOT_PATHS = ('controller', 'deployment', '.ci/slurm', 'docs/evidence/sif-lmod-20261024/raw/4V100')
TERMINAL = {'succeeded', 'skipped', 'failed', 'blocked'}


def read(path):
    return json.loads(path.read_text())


def make_plan(software, tracks, targets, resolver=resolve_tracks):
    selected = plan(software, tracks, targets)
    tasks, builds, skipped = [], {}, []
    for name in SOFTWARE:
        parts = selected[name.replace('-', '_')].split(',') if selected[name.replace('-', '_')] else []
        if not parts:
            continue
        sources = resolver(name, tracks.split(','))
        skipped.extend(row for row in sources if row['status'] == 'skipped')
        builds[name] = [dict(row, target=part) for row in sources if row['status'] == 'resolved' for part in parts]
        for source in builds[name]:
            tasks.append(dict(id=f'{name}-{source["track"]}-{source["target"]}', kind='build',
                              software=name, source=source, state='new'))
    # Submit development on every partition first. Releases retain claim-based
    # deduplication; a skipped release never masquerades as accepted science.
    tasks.sort(key=lambda task: TRACKS.index(task['source']['track']))
    for name, rows in builds.items():
        development = [row for row in rows if row['track'] == 'development']
        if {row['target'] for row in development} == set(SOFTWARE[name]['targets']):
            dependencies = [task['id'] for task in tasks if task['software'] == name
                            and task['source']['track'] == 'development']
            tasks.append(dict(id=name + '-beta', kind='beta', software=name, builds=rows,
                              dependencies=dependencies, state='new'))
    return dict(tasks=tasks, skipped_sources=skipped)


def snapshot(destination, sha):
    if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO, text=True).strip() != sha:
        raise ValueError('checkout differs from the workflow controller SHA')
    destination.mkdir()
    archive = subprocess.Popen(['git', 'archive', sha, *SNAPSHOT_PATHS], cwd=REPO, stdout=subprocess.PIPE)
    try:
        subprocess.run(['tar', '-xf', '-', '-C', str(destination)], stdin=archive.stdout, check=True)
    finally:
        archive.stdout.close()
    if archive.wait():
        raise RuntimeError('failed to freeze the trusted controller checkout')
    pins = {}
    for path in destination.rglob('*'):
        if path.is_symlink():
            raise ValueError('snapshot cannot contain symbolic links')
        if path.is_file():
            pins[str(path.relative_to(destination))] = hashlib.sha256(path.read_bytes()).hexdigest()
            path.chmod(path.stat().st_mode & ~0o222)
    return pins


def verify_snapshot(directory, pins):
    for name, digest in pins.items():
        path = directory / name
        if path.resolve() != path or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('frozen relay controller changed: ' + name)


def enqueue(root, args):
    workflow = safe_name(os.environ['GITHUB_RUN_ID'])
    # A rerun keeps the original attempt/source/recipe and reattaches to jobs.
    path = root / workflow / 'plan.json'
    if path.exists():
        return path
    directory = path.parent
    directory.mkdir(exist_ok=True)
    control = directory / 'checkout'
    if control.exists():
        # No plan exists yet, hence no remote task can have been submitted.
        shutil.rmtree(control)
    sha = safe_sha(os.environ['GITHUB_SHA'])
    frozen = make_plan(args.software, args.tracks, args.targets)
    frozen.update(schema=1, workflow=workflow, attempt=os.environ['GITHUB_RUN_ATTEMPT'], controller=sha,
                  user=os.environ['REMOTE_USER'], created=datetime.now(timezone.utc).isoformat(),
                  retry_releases=args.retry_releases, selection=dict(software=args.software, tracks=args.tracks, targets=args.targets),
                  pins=snapshot(control, sha), state='active')
    save(path, frozen)
    return path


def task_environment(generation, task, directory, key):
    env = dict(os.environ, GITHUB_RUN_ID=generation['workflow'], GITHUB_RUN_ATTEMPT=generation['attempt'],
               GITHUB_SHA=generation['controller'], REMOTE_USER=generation['user'],
               RUNNER_TEMP=str(directory), SAI_RELAY_TASK=str(directory / 'checkpoint.json'),
               SAI_RELAY_SSH_KEY=str(key), RETRY_RELEASES=str(generation['retry_releases']).lower(),
               PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
    for name in ('CI_PHASE', 'CP2K_PHASE', 'CI_RUN_ID', 'RESUME_RUN', 'LAMMPS_PHASE',
                 'LAMMPS_HANDOFF', 'BETA_SOURCE_RUN', 'BETA_SOURCE_ATTEMPT'):
        env.pop(name, None)
    env['GITHUB_OUTPUT'] = str(directory / 'outputs')
    env['GITHUB_STEP_SUMMARY'] = str(directory / 'summary.md')
    if task['kind'] == 'beta':
        env.update(BETA_SOFTWARE=task['software'], BETA_BUILDS=json.dumps(task['builds']))
        script = 'beta_ci.py'
    else:
        row = task['source']
        if task['software'] == 'deepmd-kit':
            script = 'deepmd_ci.py'
            env['DEEP_SOFTWARE_SOURCE'] = json.dumps(row)
        elif task['software'] == 'lammps':
            script = 'md_lammps_ci.py'
            env.update(LAMMPS_SOURCE=json.dumps(row), LAMMPS_TARGET=row['target'])
        else:
            script = 'ci.py'
            env.update(SOFTWARE=row['software'], TARGET=row['target'], SOURCE_SHA=row['source_sha'],
                       SOFTWARE_VERSION=row['source_version'], SOURCE_REF=row['source_ref'], RELEASE_TRACK=row['track'])
    return script, env


def advance(path, task, generation, key, run=subprocess.run):
    directory = path.parent / 'tasks' / task['id']
    directory.mkdir(parents=True, exist_ok=True)
    script, env = task_environment(generation, task, directory, key)
    task['state'] = 'running'
    save(path, generation)  # A killed worker re-enters the same checkpoint.
    print(f'RELAY_STEP {generation["workflow"]} {task["id"]}', flush=True)
    log = directory / 'controller.log'
    with log.open('a') as stream:
        result = run([sys.executable, '-u', str(path.parent / 'checkout/controller' / script)],
                     env=env, cwd=path.parent / 'checkout', stdout=stream, stderr=subprocess.STDOUT)
    task['last_exit'] = result.returncode
    if result.returncode == 75:
        task['state'] = 'waiting'
    elif result.returncode in (76, 255):
        task['state'] = 'waiting'
        task['transport_retries'] = task.get('transport_retries', 0) + 1
    elif result.returncode:
        task['state'] = 'failed'
        print(log.read_text(errors='replace')[-12000:], flush=True)
    else:
        task['state'] = 'succeeded'
        for name in ('results/build-attempt.json', 'results/submission.json'):
            receipt = directory / name
            if receipt.exists() and read(receipt).get('build') is False:
                task['state'] = 'skipped'
    save(path, generation)
    print(f'RELAY_RESULT {task["id"]} {task["state"]}', flush=True)
    checkpoint = directory / 'checkpoint.json'
    state = read(checkpoint) if checkpoint.exists() else {}
    if state.get('prepared') or state.get('preparation') or task['state'] in TERMINAL:
        # Only this relay's disposable upload/download scratch. Raw scientific
        # evidence stays on SAI; checkpoints and logs stay on this VPS.
        keep = {'checkpoint.json', 'results', 'beta-results', 'controller.log', 'outputs', 'summary.md'}
        for child in directory.iterdir():
            if child.name not in keep:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()


def ready_tasks(generation, checkpoints, waiting):
    states = {task['id']: task['state'] for task in generation['tasks']}
    for task in generation['tasks']:
        if task['state'] in TERMINAL:
            continue
        deps = [states[name] for name in task.get('dependencies', [])]
        if any(state in ('failed', 'blocked') for state in deps):
            task['state'] = 'blocked'
            continue
        if deps and not all(state == 'succeeded' for state in deps):
            continue
        pending = checkpoints.get(task['id'], {}).get('pending', [])
        if not pending or not any(record['job'] in waiting for record in pending):
            yield task


def connection(key):
    known_hosts = REPO / '.ci/slurm/known_hosts'
    options = ['ssh', '-i', str(key), '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UserKnownHostsFile=' + str(known_hosts), '-o', 'ConnectTimeout=20',
               '-o', 'ServerAliveInterval=20', '-o', 'ServerAliveCountMax=3', '-p', '12022',
               safe_name(os.environ['REMOTE_USER']) + '@c0.sai.ai-4s.com']

    def ssh(argv, **kwargs):
        return subprocess.run([*options, shlex.join(list(map(str, argv)))], text=True, check=True, timeout=180, **kwargs)
    return ssh


def report(paths, summary):
    lines = ['| Generation | Task | State | Slurm job(s) |', '|---|---|---|---|']
    for path in paths:
        generation = read(path)
        for source in generation.get('skipped_sources', []):
            lines.append(f'| {generation["workflow"]} | {source["software"]}-{source["track"]} | '
                         f'skipped: {source["reason"]} | |')
        for task in generation['tasks']:
            checkpoint = path.parent / 'tasks' / task['id'] / 'checkpoint.json'
            data = read(checkpoint) if checkpoint.exists() else {}
            jobs = ', '.join(row['job'] for row in data.get('pending', []))
            lines.append(f'| {generation["workflow"]} | {task["id"]} | {task["state"]} | {jobs} |')
    lines += ['', 'Waiting means observation deferred, not an accepted build. Frozen requests and Slurm jobs are retained.',
              'Full build/scientific evidence remains under `/org/org-open-sai/proj-sai-hpc-software`; the VPS retains metadata and controller logs.']
    summary.write_text('\n'.join(lines) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('start', 'resume', 'smoke'), default='start')
    parser.add_argument('--software', default='all')
    parser.add_argument('--tracks', default=','.join(TRACKS))
    parser.add_argument('--targets', default='all')
    parser.add_argument('--retry-releases', action='store_true')
    parser.add_argument('--budget-seconds', type=int, default=82800)
    args = parser.parse_args()
    root = Path(os.environ['SAI_RELAY_ROOT'])
    root.mkdir(parents=True, exist_ok=True)
    if root.resolve() != root:
        raise ValueError('redirected relay state root')
    key = Path(os.environ['RUNNER_TEMP']) / 'ssh/key'
    ssh = connection(key)
    if args.mode == 'smoke':
        print(json.dumps(dict(host=os.uname().nodename, uid=os.getuid(), root=str(root)), sort_keys=True))
        ssh(['python3', '-c', "import os,shutil; from pathlib import Path; "
             "p=Path('/org/org-open-sai/proj-sai-hpc-software'); "
             "assert p.is_dir() and os.access(p,os.W_OK); assert shutil.which('sbatch'); "
             "print('SAI project access and Slurm commands verified; no allocation submitted')"])
        return
    with (root / '.relay.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        requested = enqueue(root, args) if args.mode == 'start' else None
        paths = sorted(root.glob('*/plan.json'), key=lambda path: int(path.parent.name))
        active = [path for path in paths if read(path)['state'] == 'active']
        if requested and requested not in active:
            active.append(requested)
        deadline = time.monotonic() + args.budget_seconds
        verified = set()
        while active and time.monotonic() < deadline:
            for path in list(active):
                generation = read(path)
                if generation['user'] != os.environ['REMOTE_USER']:
                    raise ValueError('relay account changed during recovery')
                if path not in verified:
                    verify_snapshot(path.parent / 'checkout', generation['pins'])
                    verified.add(path)
                checkpoints = {}
                for task in generation['tasks']:
                    saved = path.parent / 'tasks' / task['id'] / 'checkpoint.json'
                    if saved.exists() and task['state'] not in TERMINAL:
                        checkpoints[task['id']] = read(saved)
                records = [record for state in checkpoints.values() for record in state.get('pending', [])]
                try:
                    waiting = set(remote(ssh, inspect_jobs, records)) if records else set()
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                    print('RELAY_POLL_RETRY ' + str(error), flush=True)
                    continue
                for task in ready_tasks(generation, checkpoints, waiting):
                    if time.monotonic() >= deadline:
                        break
                    advance(path, task, generation, key)
                if all(task['state'] in TERMINAL for task in generation['tasks']):
                    generation['state'] = ('failed' if any(task['state'] in ('failed', 'blocked')
                                                         for task in generation['tasks']) else 'succeeded')
                    active.remove(path)
                save(path, generation)
                print('RELAY_GENERATION ' + generation['workflow'] + ' ' +
                      json.dumps(dict(Counter(task['state'] for task in generation['tasks']))), flush=True)
                # Older generations finish promotion before a newer selection.
                if generation['state'] == 'active':
                    break
            report(paths, Path(os.environ['GITHUB_STEP_SUMMARY']))
            if active:
                time.sleep(min(30, max(0, deadline - time.monotonic())))
        report(paths, Path(os.environ['GITHUB_STEP_SUMMARY']))
        if active:
            raise SystemExit('Relay observation deferred; hourly recovery will reattach to the retained jobs (no cancellation).')
        relevant = [requested] if requested else [path for path in paths if path in verified]
        if any(read(path)['state'] == 'failed' for path in relevant):
            raise SystemExit('One or more deliveries failed; retained logs identify the exact gate.')


if __name__ == '__main__':
    main()
