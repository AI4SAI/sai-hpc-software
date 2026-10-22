#!/usr/bin/env python3
"""Bounded compile/metadata probe. Sources, objects and temp files stay in image."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

root = Path('/workspace/probe')
out = Path('/results')
workers = 8


def cpu():
    usages = [resource.getrusage(kind) for kind in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN)]
    return sum(u.ru_utime + u.ru_stime for u in usages)


def phase(name, fn):
    start, before = time.monotonic(), cpu()
    fn()
    elapsed, used = time.monotonic() - start, cpu() - before
    result = {'phase': name, 'wall_seconds': elapsed, 'client_cpu_seconds': used,
              'client_average_cores': used / elapsed, 'finished_epoch': time.time()}
    print(json.dumps(result), flush=True)
    with (out / 'phases.jsonl').open('a') as f:
        f.write(json.dumps(result) + '\n')


def parallel(fn, values):
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(fn, values))


def digest_files():
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


if sys.argv[1] == 'verify':
    expected = json.loads((out / 'manifest.json').read_text())
    actual = digest_files()
    assert expected == actual, 'remounted contents differ'
    (out / 'remount-verified.json').write_text(json.dumps({'files': len(actual), 'verified': True}) + '\n')
    print('CLEAN_REMOUNT_VERIFIED', len(actual), flush=True)
    sys.exit(0)

root.mkdir(parents=True, exist_ok=False)
Path('/workspace/tmp').mkdir(exist_ok=True)
os.environ['TMPDIR'] = '/workspace/tmp'
os.environ['PATH'] = '/opt/devtools/gcc/14.3.0/bin:/usr/bin:/bin'
os.environ['LD_LIBRARY_PATH'] = '/opt/devtools/gcc/14.3.0/lib64'
phase('create_64', lambda: parallel(lambda n: (root / ('file-%03d.tmp' % n)).write_bytes(bytes([n]) * 4096), range(64)))
phase('rename_64', lambda: parallel(lambda n: (root / ('file-%03d.tmp' % n)).rename(root / ('file-%03d.dat' % n)), range(64)))

source = '#include <algorithm>\n#include <numeric>\n#include <vector>\nint f{n}(std::vector<int> v) {{ std::sort(v.begin(),v.end()); return std::accumulate(v.begin(),v.end(),{n}); }}\n'
for n in range(32):
    (root / ('unit-%02d.cpp' % n)).write_text(source.format(n=n))


def compile_one(n):
    name = 'unit-%02d' % n
    subprocess.run(['g++', '-O2', '-MMD', '-MF', name + '.Td', '-c', name + '.cpp', '-o', name + '.o'],
                   cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # Automake uses this atomic dependency-file replacement pattern.
    (root / (name + '.Td')).rename(root / (name + '.d'))


phase('compile_32_j8', lambda: parallel(compile_one, range(32)))


def explicit_sync():
    with (root / 'durability-marker').open('wb') as f:
        f.write(b'explicit fsync remains enabled\n')
        f.flush()
        os.fsync(f.fileno())


phase('explicit_fsync', explicit_sync)
(out / 'manifest.json').write_text(json.dumps(digest_files(), sort_keys=True) + '\n')
print('WORKLOAD_COMPLETE', flush=True)
