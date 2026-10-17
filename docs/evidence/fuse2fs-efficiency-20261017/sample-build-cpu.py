"""Read-only process/cgroup sampling; run by SSH, never as an attached Slurm step."""
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import socket
import sys
import time

job = sys.argv[1]
assert job.isdigit()
ticks = os.sysconf('SC_CLK_TCK')
uid = os.getuid()


def discover_job_cgroups():
    prefixes = set()
    owners = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != uid:
                continue
            env = dict(part.split(b'=', 1) for part in (proc / 'environ').read_bytes().split(b'\0') if b'=' in part)
            if env.get(b'SLURM_JOB_ID') != job.encode():
                continue
            for line in (proc / 'cgroup').read_text().splitlines():
                _, controllers, path = line.split(':', 2)
                if '/step_' in path:
                    prefixes.add((controllers, path.split('/step_', 1)[0]))
                    owners.append({'pid': int(proc.name), 'slurm_job_id': job, 'cgroup': line})
        except OSError:
            continue
    if not prefixes:
        raise RuntimeError('cannot map the Slurm job to its actual cgroup')
    return sorted(prefixes), owners


job_cgroup_prefixes, cgroup_owners = discover_job_cgroups()


def belongs_to_job(cgroup):
    for line in cgroup.splitlines():
        _, controllers, path = line.split(':', 2)
        if any(controllers == kind and (path == prefix or path.startswith(prefix + '/'))
               for kind, prefix in job_cgroup_prefixes):
            return True
    return False


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError as error:
        return 'UNAVAILABLE: ' + str(error)


def mapping(path):
    result = {}
    for line in read(path).splitlines():
        fields = line.replace(':', '').split()
        if len(fields) == 2 and fields[1].isdigit():
            result[fields[0]] = int(fields[1])
    return result


def process(proc):
    raw = (proc / 'stat').read_text()
    head, _, tail = raw.rpartition(')')
    fields = tail.split()
    return {'pid': int(proc.name), 'name': head.split('(', 1)[1],
            'state': fields[0], 'ppid': int(fields[1]),
            'cpu_ticks': int(fields[11]) + int(fields[12]),
            'start_ticks': int(fields[19]), 'threads': int(fields[17])}


def sample():
    started = time.monotonic()
    records = {}
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            if proc.stat().st_uid != uid:
                continue
            cgroup = (proc / 'cgroup').read_text().strip()
            if not belongs_to_job(cgroup):
                continue
            row = process(proc)
            row['cgroup'] = cgroup
            row['io'] = mapping(proc / 'io')
            row['wchan'] = read(proc / 'wchan')
            row['syscall'] = read(proc / 'syscall').split()[:2]
            row['command'] = (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')[:500]
            try:
                row['cwd'] = os.readlink(proc / 'cwd')
            except OSError as error:
                row['cwd'] = str(error)
            status = (proc / 'status').read_text().splitlines()
            row['cpu_allowed_list'] = next((line.split(':', 1)[1].strip()
                for line in status if line.startswith('Cpus_allowed_list:')), '')
            row['thread_states'] = Counter()
            row['blocked_threads'] = []
            for task in (proc / 'task').iterdir():
                try:
                    thread = process(task)
                    row['thread_states'][thread['state']] += 1
                    if thread['state'] == 'D':
                        row['blocked_threads'].append({'tid': thread['pid'],
                            'wchan': read(task / 'wchan'), 'stack': read(task / 'stack').splitlines()[:10]})
                except (OSError, ValueError, IndexError):
                    pass
            if any(name in row['command'] for name in ('fuse2fs', 'fuse-overlayfs', 'make', 'ninja', 'cmake', 'spack install')):
                env = {}
                for part in (proc / 'environ').read_bytes().split(b'\0'):
                    key, sep, value = part.partition(b'=')
                    if sep and key in (b'BUILD_JOBS', b'MAKEFLAGS', b'CMAKE_BUILD_PARALLEL_LEVEL', b'TMPDIR'):
                        env[key.decode()] = value.decode(errors='replace')
                row['build_environment'] = env
            records[str(row['pid'])] = row
        except (OSError, ValueError, IndexError):
            continue
    host_cpu = [int(value) for value in Path('/proc/stat').read_text().splitlines()[0].split()[1:9]]
    return {'monotonic': started, 'duration_seconds': time.monotonic() - started,
            'processes': records, 'host_cpu_ticks': host_cpu,
            'host_pressure': {name: read('/proc/pressure/' + name) for name in ('cpu', 'io', 'memory')},
            'loadavg': read('/proc/loadavg')}


def cgroups(records):
    paths = set()
    for row in records.values():
        for line in row['cgroup'].splitlines():
            _, controllers, relative = line.split(':', 2)
            base = Path('/sys/fs/cgroup')
            if controllers:
                base /= controllers
            path = base / relative.lstrip('/')
            while path.is_relative_to(base) and path != base:
                paths.add(path)
                path = path.parent
    return {str(path): {name: read(path / name) for name in
        ('cpu.max', 'cpu.stat', 'cpu.cfs_quota_us', 'cpu.cfs_period_us',
         'cpuacct.usage', 'cpuset.cpus', 'cpuset.cpus.effective') if (path / name).is_file()}
        for path in sorted(paths)}


initial = sample()
if not initial['processes']:
    raise RuntimeError('no live job processes were observed')
initial_cgroups = cgroups(initial['processes'])
samples = [initial]
for _ in range(2):
    time.sleep(5)
    samples.append(sample())
last = samples[-1]
elapsed = last['monotonic'] - initial['monotonic']
active = []
for pid, row in last['processes'].items():
    previous = initial['processes'].get(pid)
    if previous and previous['start_ticks'] == row['start_ticks']:
        delta = row['cpu_ticks'] - previous['cpu_ticks']
        row['interval_cpu_percent_one_core_100'] = round(delta / ticks / elapsed * 100, 2)
        row['interval_io_bytes'] = {key: value - previous['io'].get(key, value)
            for key, value in row['io'].items() if key in ('read_bytes', 'write_bytes', 'rchar', 'wchar')}
    active.append(row)
active.sort(key=lambda row: row.get('interval_cpu_percent_one_core_100', 0), reverse=True)
host_delta = [after - before for before, after in zip(initial['host_cpu_ticks'], last['host_cpu_ticks'])]
host_total = sum(host_delta)
result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'node': socket.gethostname(),
    'job_id': job, 'sample_seconds': elapsed, 'host_cpu_count': os.cpu_count(),
    'job_cgroup_prefixes': job_cgroup_prefixes, 'cgroup_mapping_evidence': cgroup_owners,
    'host_interval_cpu_percent': {name: round(value / host_total * 100, 2)
        for name, value in zip(('user', 'nice', 'system', 'idle', 'iowait', 'irq', 'softirq', 'steal'), host_delta)},
    'surviving_process_cpu_percent_one_core_100': round(sum(
        row.get('interval_cpu_percent_one_core_100', 0) for row in active), 2),
    'initial_cgroups': initial_cgroups, 'final_cgroups': cgroups(last['processes']),
    'final_processes': active, 'samples': samples}
print(json.dumps(result, indent=2, sort_keys=True))
