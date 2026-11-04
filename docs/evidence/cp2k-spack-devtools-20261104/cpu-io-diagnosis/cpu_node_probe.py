import collections
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

signal.alarm(42)
PROC = Path('/proc')
FUSE = 1025833


def read(path):
    try:
        return Path(path).read_text()
    except OSError as error:
        return {'error': str(error)}


def emit(kind, value):
    print(json.dumps({'kind': kind, 'time': time.time(), 'value': value}), flush=True)


def command(args, timeout=4):
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
        return dict(args=args, code=result.returncode, stdout=result.stdout, stderr=result.stderr)
    except (OSError, subprocess.TimeoutExpired) as error:
        return dict(args=args, error=str(error))


def processes():
    records = []
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            name = (entry / 'comm').read_text().strip()
            if name not in ('cc1plus', 'make', 'fuse2fs', 'as'):
                continue
            args = (entry / 'cmdline').read_bytes().decode().split('\0')
            status = (entry / 'stat').read_text().split()
            records.append(dict(pid=int(entry.name), name=name, ppid=int(status[3]),
                state=status[2], cpu_ticks=int(status[13]) + int(status[14]),
                start_ticks=int(status[21]), wchan=read(entry / 'wchan'),
                affinity=sorted(os.sched_getaffinity(int(entry.name))),
                sources=[a for a in args if a.startswith('src/') and a.endswith('.cc')],
                cgroup=read(entry / 'cgroup')))
        except OSError:
            pass
    return records


def threads():
    result = []
    for entry in (PROC / str(FUSE) / 'task').iterdir():
        try:
            status = (entry / 'stat').read_text().split()
            result.append(dict(tid=int(entry.name), state=status[2],
                cpu_ticks=int(status[13]) + int(status[14]), wchan=read(entry / 'wchan'),
                schedstat=read(entry / 'schedstat'), io=read(entry / 'io'),
                stack=read(entry / 'stack'), syscall=read(entry / 'syscall')))
        except OSError:
            pass
    return result


emit('identity', dict(uid=os.getuid(), hostname=os.uname().nodename,
                     tools={n: shutil.which(n) for n in ('strace', 'perf', 'pidstat', 'iostat', 'nsenter')}))
emit('processes', processes())
emit('driver_threads_initial', threads())
group = read(PROC / str(FUSE) / 'cgroup')
if isinstance(group, str):
    relative = group.strip().split('::')[-1]
    groupdir = Path('/sys/fs/cgroup') / relative.lstrip('/')
    cgroups = []
    for path in (groupdir, *groupdir.parents):
        if not path.is_relative_to('/sys/fs/cgroup'):
            break
        cgroups.append(dict(path=str(path), values={n: read(path / n) for n in (
            'cpu.max', 'cpu.stat', 'cpuset.cpus.effective', 'cpuset.mems.effective',
            'io.max', 'io.stat', 'io.pressure', 'memory.current', 'memory.max',
            'memory.high', 'memory.events', 'memory.stat', 'memory.pressure')}))
    emit('cgroups_initial', cgroups)
emit('mounts', command(['findmnt', '-T', '/org/org-open-sai/proj-sai-hpc-software', '-o', 'TARGET,SOURCE,FSTYPE,OPTIONS']))
emit('mountstats', read('/proc/self/mountstats'))
emit('meminfo', read('/proc/meminfo'))
emit('diskstats_initial', read('/proc/diskstats'))
emit('vmstat_initial', read('/proc/vmstat'))
emit('namespace_access', command(['nsenter', '-t', str(FUSE), '-U', '-m', '--', '/usr/bin/id']))
for name in ('fd', 'ns', 'mountinfo', 'uid_map', 'gid_map', 'io'):
    path = PROC / str(FUSE) / name
    try:
        if name in ('fd', 'ns'):
            output = {x.name: os.readlink(x) for x in path.iterdir()}
        else:
            output = read(path)
    except OSError as error:
        output = str(error)
    emit('driver_' + name, output)
time.sleep(10)
emit('driver_threads_final', threads())
emit('processes_final', processes())
if isinstance(group, str):
    emit('cgroups_final', {str(path): {n: read(path / n) for n in (
        'cpu.stat', 'io.stat', 'io.pressure', 'memory.events', 'memory.pressure')}
        for path in (groupdir, groupdir.parent, groupdir.parent.parent)})
emit('diskstats_final', read('/proc/diskstats'))
emit('vmstat_final', read('/proc/vmstat'))
