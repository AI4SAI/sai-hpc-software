#!/usr/bin/env python3
"""Read-only cached/direct CUDA-library comparison on a selected cluster node."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys


def digest(stream):
    value = hashlib.sha256()
    size = 0
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        value.update(block)
        size += len(block)
    return value.hexdigest(), size


def audit():
    records = []
    for name in ['libnvrtc.so.12', 'libcufft.so.11']:
        path = Path('/opt/devtools/nvidia/cuda-12.9.1/lib64') / name
        info = path.stat()
        row = dict(path=str(path), realpath=str(path.resolve()), inode=info.st_ino,
                   size=info.st_size, mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns)
        with path.open('rb') as stream:
            row['cached_sha256_before'], _ = digest(stream)
        process = subprocess.Popen(['dd', 'if=' + str(path), 'iflag=direct', 'bs=1M', 'status=none'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        row['direct_sha256'], row['direct_bytes'] = digest(process.stdout)
        row['direct_stderr'] = process.stderr.read().decode()
        row['direct_exit_code'] = process.wait()
        with path.open('rb') as stream:
            row['cached_sha256_after'], _ = digest(stream)
        after = path.stat()
        row['stat_unchanged'] = (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) == (
            after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        records.append(row)
    return dict(host=socket.gethostname(), uid=os.getuid(), euid=os.geteuid(), libraries=records)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('node', nargs='?')
    parser.add_argument('--local', action='store_true')
    args = parser.parse_args()
    if args.local:
        result = audit()
    else:
        if not args.node or not all(c.isalnum() or c in '.-' for c in args.node):
            parser.error('a cluster hostname is required')
        source = Path(__file__).read_text()
        command = ['ssh', '-o', 'StrictHostKeyChecking=accept-new', args.node, 'python3', '-', '--local']
        output = subprocess.check_output(command, input=source, text=True, timeout=180)
        result = dict(observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      command=command, probe_sha256=hashlib.sha256(source.encode()).hexdigest(),
                      scope='Ordinary-user cached/direct/cached reads; no library writes or explicit cache eviction',
                      result=json.loads(output))
    print(json.dumps(result, indent=2, sort_keys=True))
