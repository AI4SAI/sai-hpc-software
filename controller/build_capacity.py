#!/usr/bin/env python3
"""Reserve remaining disk growth across concurrent native build allocations."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time

RESERVE_BYTES = 4 * 2**30
OUTPUTS = ('work.ext3', 'final.squashfs', 'result.sif')


def allocated(run):
    return sum(path.stat().st_blocks * 512 for name in OUTPUTS
               if (path := run / name).exists())


def unmanaged_growth(root, own):
    """Budget already-running allocations from controllers predating this lock."""
    candidates = []
    for handle in (root / 'runs').glob('*/job.id'):
        run = handle.parent
        if run == own.parent:
            continue
        reservation = run / 'capacity.json'
        if reservation.exists():
            with reservation.open('r+') as stream:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
        request = run / 'request.json'
        if handle.is_symlink() or request.is_symlink():
            raise ValueError('redirected build resource request')
        if request.is_file():
            candidates.append((handle.read_text().strip(), run, request))
    if not candidates:
        return 0
    active = set(subprocess.check_output(
        ['squeue', '--noheader', '--me', '--states=RUNNING,COMPLETING,CONFIGURING', '--format=%A'],
        text=True).split())
    pending = 0
    for job, run, request in candidates:
        if job not in active:
            continue
        record = json.loads(request.read_text())
        overlay_mb = record.get('overlay_mb', 32768)
        if not isinstance(overlay_mb, int) or not 1 <= overlay_mb <= 32768:
            raise ValueError('invalid existing build space request')
        dependency = record.get('purpose') == 'cp2k-final-prefix-dependencies-not-application-acceptance'
        budget = overlay_mb * 2**20 * (1 if dependency else 3)
        pending += max(0, budget - allocated(run))
    return pending


def pending_growth(root, own):
    pending = 0
    for path in (root / 'runs').glob('*/capacity.json'):
        if path == own:
            continue
        if path.is_symlink():
            raise ValueError('redirected capacity reservation')
        with path.open('r+') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                # A new waiter may hold its file before its first record is
                # written. Reservation writes themselves hold the global lock.
                content = stream.read()
                record = json.loads(content) if content else {'state': 'waiting'}
                if record['state'] == 'reserved':
                    pending += max(0, record['budget_bytes'] - allocated(path.parent))
            # Unlocked records belong to exited jobs and reserve no space.
    return pending


@contextmanager
def reserve(root, run, budget_mb, *, timeout=1800, interval=15):
    root, run = Path(root), Path(run)
    if (not root.is_absolute() or root.resolve() != root or
            run.resolve() != run or run.parent != root / 'runs' or not run.is_dir()):
        raise ValueError('capacity reservation requires a canonical build run')
    if not 1 <= budget_mb <= 3 * 32768 or timeout < 0 or interval <= 0:
        raise ValueError('invalid capacity reservation bounds')
    path, coordination = run / 'capacity.json', root / '.build-capacity.lock'
    if path.is_symlink() or coordination.is_symlink():
        raise ValueError('redirected capacity lock')
    budget = budget_mb * 2**20
    deadline = time.monotonic() + timeout
    with path.open('a+') as own:
        fcntl.flock(own, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with coordination.open('a+') as lock:
            while True:
                with_locked = False
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    disk = os.statvfs(root)
                    available = disk.f_bavail * disk.f_frsize
                    pending = pending_growth(root, path) + unmanaged_growth(root, path)
                    required = max(0, budget - allocated(run)) + RESERVE_BYTES
                    with_locked = available - pending >= required
                    own.seek(0)
                    own.truncate()
                    json.dump({'schema': 1, 'state': 'reserved' if with_locked else 'waiting',
                               'budget_bytes': budget, 'pid': os.getpid()}, own)
                    own.flush()
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
                if with_locked:
                    print(f'BUILD_CAPACITY_RESERVED {budget_mb} MiB {run.name}', flush=True)
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError('build capacity deadline reached; existing artifacts preserved')
                print(f'BUILD_CAPACITY_WAIT available={available} pending={pending} required={required}', flush=True)
                time.sleep(min(interval, max(0, deadline - time.monotonic())))
            try:
                yield
            finally:
                own.seek(0)
                own.truncate()
                json.dump({'schema': 1, 'state': 'released', 'budget_bytes': budget}, own)
                own.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('run', type=Path)
    parser.add_argument('budget_mb', type=int)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('build command required')
    with reserve(args.root, args.run, args.budget_mb):
        return subprocess.call(command)


if __name__ == '__main__':
    raise SystemExit(main())
