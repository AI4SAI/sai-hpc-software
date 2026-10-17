#!/usr/bin/env python3
"""Create 161 durable compile outputs, or read them without changing the image."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path('/workspace/probe')
RESULTS = Path('/results')
EXPECTED_FILES = 161


def digest_files():
    return {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(ROOT.rglob('*')) if path.is_file()}


def verify(label):
    expected = json.loads((RESULTS / 'manifest.json').read_text())
    actual = digest_files()
    if len(actual) != EXPECTED_FILES or actual != expected:
        raise RuntimeError('contents differ from the 161-file workload manifest')
    record = {'label': label, 'files': len(actual), 'verified': True}
    (RESULTS / (label + '.json')).write_text(json.dumps(record) + '\n')
    print(json.dumps(record), flush=True)


def parallel(function, values):
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(function, values))


def run():
    ROOT.mkdir(parents=True, exist_ok=False)
    os.environ['PATH'] = '/opt/devtools/gcc/14.3.0/bin:/usr/bin:/bin'
    os.environ['LD_LIBRARY_PATH'] = '/opt/devtools/gcc/14.3.0/lib64'
    parallel(lambda n: (ROOT / ('file-%03d.tmp' % n)).write_bytes(bytes([n]) * 4096),
             range(64))
    parallel(lambda n: (ROOT / ('file-%03d.tmp' % n)).rename(ROOT / ('file-%03d.dat' % n)),
             range(64))
    source = ('#include <algorithm>\n#include <numeric>\n#include <vector>\n'
              'int f{n}(std::vector<int> v) {{ std::sort(v.begin(),v.end()); '
              'return std::accumulate(v.begin(),v.end(),{n}); }}\n')
    for n in range(32):
        (ROOT / ('unit-%02d.cpp' % n)).write_text(source.format(n=n))

    def compile_one(n):
        name = 'unit-%02d' % n
        subprocess.run(['g++', '-O2', '-MMD', '-MF', name + '.Td', '-c', name + '.cpp',
                        '-o', name + '.o'], cwd=ROOT, check=True)
        (ROOT / (name + '.Td')).rename(ROOT / (name + '.d'))

    parallel(compile_one, range(32))
    with (ROOT / 'durability-marker').open('wb') as stream:
        stream.write(b'explicit fsync remains enabled\n')
        stream.flush()
        os.fsync(stream.fileno())
    manifest = digest_files()
    if len(manifest) != EXPECTED_FILES:
        raise RuntimeError('workload did not produce exactly 161 files')
    (RESULTS / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True) + '\n')
    print(json.dumps({'files': len(manifest), 'explicit_fsync': True}), flush=True)


def main():
    mode = sys.argv[1]
    if mode == 'run':
        run()
    elif mode == 'verify':
        verify(sys.argv[2])
    elif mode == 'hold':
        label = sys.argv[2]
        verify(label + '-before')
        (RESULTS / (label + '-ready')).write_text('ready\n')
        deadline = time.monotonic() + 90
        while not (RESULTS / (label + '-release')).exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('holder release timed out')
            time.sleep(0.1)
        verify(label + '-after')
    else:
        raise ValueError('unknown workload mode: ' + mode)


if __name__ == '__main__':
    main()
