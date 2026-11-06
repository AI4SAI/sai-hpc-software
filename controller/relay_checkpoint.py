"""Opt-in cooperative CI checkpoints; Slurm owns the long-running work.

Only the relay sets SAI_RELAY_TASK. Legacy hosted entrypoints keep their
blocking monitors and full artifact collection. A deferred observation is
exit 75, never a successful build or scientific acceptance.
"""
import inspect
import json
import os
from pathlib import Path
import subprocess


class Pending(Exception):
    pass


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def inspect_submission(directory, expected):
    """Recover only a complete, pinned receipt; ambiguous sbatch stays stopped."""
    import hashlib
    import json
    from pathlib import Path
    import re

    directory = Path(directory)
    if directory.resolve() != directory or not directory.is_absolute():
        raise ValueError('redirected relay submission directory')

    def read(name):
        path = directory / name
        if not path.is_file() or path.is_symlink() or path.resolve() != path:
            raise ValueError('incomplete submission; recover Slurm handle before retrying: ' + str(path))
        return path.read_bytes()

    request_bytes = read('request.json')
    request = json.loads(request_bytes)
    if any(request.get(key) != value for key, value in expected.items()):
        raise ValueError('relay submission differs from the frozen delivery')
    script_sha = hashlib.sha256(read('job.sbatch')).hexdigest()
    pinned = request.get('job_script_sha256', request.get('script_sha256'))
    if pinned and script_sha != pinned:
        raise ValueError('relay submission script changed')
    job = str(request['job_id']) if 'job_id' in request else read('job.id').decode().strip()
    if not re.fullmatch('[1-9][0-9]*', job):
        raise ValueError('invalid durable Slurm handle')
    if (directory / 'job.id').exists() and read('job.id').decode().strip() != job:
        raise ValueError('Slurm handle differs from its request')
    return dict(directory=str(directory), job=job, request=request,
                request_sha256=hashlib.sha256(request_bytes).hexdigest(), script_sha256=script_sha)


def inspect_jobs(records, release_held=False):
    """Read queue/accounting without assuming a job still exists in slurmctld."""
    import subprocess

    queue = subprocess.check_output(['squeue', '--me', '-h', '-o', '%i|%T|%r|%Z'], text=True)
    active = {row[0]: row[1:] for line in queue.splitlines() if len(row := line.split('|')) == 4}
    missing = [record['job'] for record in records if record['job'] not in active]
    rows = {}
    if missing:
        accounting = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', ','.join(missing),
                                              '-o', 'JobIDRaw,State,ExitCode,WorkDir'], text=True)
        for line in accounting.splitlines():
            row = line.split('|')
            if row[0] in missing:
                if row[0] in rows:
                    raise ValueError('ambiguous relay accounting')
                rows[row[0]] = row[1:4]
    waiting = []
    states = {'PENDING', 'RUNNING', 'CONFIGURING', 'COMPLETING', 'SUSPENDED', 'REQUEUED', 'RESIZING'}
    for record in records:
        job = record['job']
        row = active.get(job, rows.get(job))
        if row and row[2] != record['directory']:
            raise ValueError('Slurm work directory differs from saved receipt')
        if job in active and row[:2] == ['PENDING', 'JobHeldUser'] and release_held:
            subprocess.run(['scontrol', 'release', job], check=True)
        if not row or job in active or row[0].split('+')[0] in states:
            waiting.append(job)
    return waiting


def remote(ssh, function, *args):
    code = inspect.getsource(function) + '\nimport json, sys\nprint(json.dumps(' + function.__name__ + '(*json.loads(sys.argv[1]))))\n'
    return json.loads(ssh(['python3', '-I', '-B', '-c', code, json.dumps(args)], capture_output=True).stdout)


class Checkpoint:
    def __init__(self):
        value = os.environ.get('SAI_RELAY_TASK')
        self.path = Path(value) if value else None
        self.enabled = self.path is not None
        self.data = json.loads(self.path.read_text()) if self.path and self.path.exists() else {}

    def set(self, key, value):
        self.data[key] = value
        if self.enabled:
            save(self.path, self.data)
        return value

    def freeze(self, key, value):
        return self.data[key] if key in self.data else self.set(key, value)

    def bind(self, value):
        if self.freeze('identity', value) != value:
            raise ValueError('relay checkpoint belongs to another source/controller/workflow')

    def submitted(self, ssh, directory, expected, action):
        """Write intent before sbatch; a lost response must recover a real receipt."""
        if not self.enabled:
            return action()
        key = 'submit:' + directory
        if key not in self.data:
            self.set(key, dict(expected=expected))
            action()
        elif self.data[key]['expected'] != expected:
            raise ValueError('relay submit identity changed')
        record = remote(ssh, inspect_submission, directory, expected)
        prior = self.data[key].get('record')
        if prior and record != prior:
            raise ValueError('recorded relay submission changed')
        self.set(key, dict(expected=expected, record=record))
        return record['request']

    def wait(self, ssh, directory, expected=None):
        if not self.enabled:
            return
        record = remote(ssh, inspect_submission, directory, expected or {})
        self.set('pending', [record])
        if remote(ssh, inspect_jobs, [record], True):
            raise Pending('waiting for Slurm ' + record['job'] + ' in ' + directory)
        self.set('pending', [])


def entry(main):
    try:
        main()
    except Pending as error:
        print('RELAY_PENDING: ' + str(error), flush=True)
        raise SystemExit(75)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        if os.environ.get('SAI_RELAY_TASK') and (isinstance(error, subprocess.TimeoutExpired) or error.returncode == 255):
            print('RELAY_TRANSPORT_RETRY: ' + str(error), flush=True)
            raise SystemExit(76)
        raise
