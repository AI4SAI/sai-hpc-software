#!/usr/bin/env python3
"""Prevent duplicate ownership of a run without predicting disk capacity.

The filesystem enforces storage limits. Keep the existing entry point for build
launchers, but never reserve shared free space or wait for a capacity estimate.
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys


@contextmanager
def reserve(root, run, budget_mb):
    root, run = Path(root), Path(run)
    if (not root.is_absolute() or root.resolve() != root or
            run.resolve() != run or run.parent != root / 'runs' or not run.is_dir()):
        raise ValueError('build lock requires a canonical build run')
    if not 1 <= budget_mb <= 3 * 32768:
        raise ValueError('invalid build image size')
    path = run / 'capacity.json'
    if path.is_symlink():
        raise ValueError('redirected build lock')
    with path.open('a+') as own:
        fcntl.flock(own, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def record(state):
            own.seek(0)
            own.truncate()
            json.dump({'schema': 1, 'state': state,
                       'capacity_policy': 'filesystem-enforced', 'pid': os.getpid()}, own)
            own.flush()

        record('running')
        print(f'BUILD_CAPACITY_POLICY filesystem-enforced {run.name}', flush=True)
        try:
            yield
        finally:
            record('released')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shell-script-stdin', action='store_true',
                        help='read the whole Bash script before launching noninteractive children')
    parser.add_argument('root', type=Path)
    parser.add_argument('run', type=Path)
    parser.add_argument('budget_mb', type=int, help='legacy launcher image-size argument; no reservation')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if args.shell_script_stdin:
        if command:
            parser.error('shell script mode does not accept a separate command')
        # MPI launchers forward stdin even when their numerical probe never
        # reads it. With bash -s this consumes the remaining export commands.
        # Keep the command source separate from the child process input stream.
        command = ['/bin/bash', '-euc', '-o', 'pipefail', sys.stdin.read()]
    if not command:
        parser.error('build command required')
    with reserve(args.root, args.run, args.budget_mb):
        return subprocess.call(command, **({'stdin': subprocess.DEVNULL}
                                          if args.shell_script_stdin else {}))


if __name__ == '__main__':
    raise SystemExit(main())
