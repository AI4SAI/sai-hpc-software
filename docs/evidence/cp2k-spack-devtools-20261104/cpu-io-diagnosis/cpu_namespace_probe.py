import collections
import json
import os
from pathlib import Path
import re
import signal
import time

signal.alarm(35)


def emit(kind, value):
    print(json.dumps({'kind': kind, 'time': time.time(), 'value': value}), flush=True)


def read(path):
    try:
        return Path(path).read_text()
    except OSError as error:
        return {'error': str(error)}


emit('identity', dict(uid=os.getuid(), namespace=os.readlink('/proc/self/ns/user')))
make = Path('/proc/1103548')
emit('make_args', (make / 'cmdline').read_bytes().decode().split('\0'))
try:
    env = dict(p.split(b'=', 1) for p in (make / 'environ').read_bytes().split(b'\0') if b'=' in p)
    emit('build_environment', {key.decode(): env.get(key, b'').decode() for key in (
        b'BUILD_JOBS', b'MAKEFLAGS', b'TMPDIR', b'CXXFLAGS', b'CXX')})
except OSError as error:
    emit('build_environment_error', str(error))
base = make / 'cwd'
for rel in ('../../spack-build-out.txt', '../spack-build-out.txt', 'Makefile'):
    path = base / rel
    try:
        with path.open('rb') as stream:
            size = path.stat().st_size
            stream.seek(max(0, size - 6000))
            tail = stream.read().decode(errors='replace')
        emit('file_tail', dict(path=rel, size=size, tail=tail))
    except OSError as error:
        emit('file_error', dict(path=rel, error=str(error)))
for rel in ('src', 'src/.libs'):
    started = time.monotonic()
    try:
        counts = collections.Counter(Path(x.name).suffix for x in os.scandir(base / rel))
        emit('file_counts', dict(path=rel, counts=counts, seconds=time.monotonic() - started))
    except OSError as error:
        emit('directory_error', dict(path=rel, error=str(error)))
for pid in (1025833, 1026103, 1103548):
    proc = Path('/proc') / str(pid)
    emit('process', dict(pid=pid, io=read(proc/'io'), syscall=read(proc/'syscall'),
                         namespace=os.readlink(proc/'ns/user'), mountinfo=read(proc/'mountinfo')))
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit():
        continue
    try:
        if (proc/'comm').read_text().strip() != 'cc1plus':
            continue
        if 's391M6J6DHDP00' not in (proc/'cgroup').read_text():
            continue
        fds = {}
        for path in (proc/'fd').iterdir():
            try:
                fds[path.name] = os.readlink(path)
            except OSError:
                pass
        emit('compiler', dict(pid=int(proc.name), syscall=read(proc/'syscall'),
                             io=read(proc/'io'), fds=fds, wchan=read(proc/'wchan')))
    except OSError:
        pass
