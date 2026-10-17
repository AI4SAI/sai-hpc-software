#!/usr/bin/env python3
"""Create 161 durable compile outputs, or read them without changing the image."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import stat
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
    # fuse-overlayfs represents directory opacity with this empty control file;
    # a kernel overlay lowerdir can expose it. No other extra file is allowed.
    marker_name = '.wh..wh..opq'
    marker_path = ROOT / marker_name
    marker = {'path': marker_name, 'present': False}
    accepted_controls = {}
    try:
        marker_stat = marker_path.lstat()
    except FileNotFoundError:
        pass
    else:
        marker.update(present=True, mode=oct(marker_stat.st_mode),
                      size=marker_stat.st_size, uid=marker_stat.st_uid,
                      gid=marker_stat.st_gid,
                      sha256=actual.get(marker_name))
        if (stat.S_ISREG(marker_stat.st_mode) and marker_stat.st_size == 0 and
                actual.get(marker_name) == hashlib.sha256(b'').hexdigest() and
                marker_name not in expected):
            accepted_controls[marker_name] = actual[marker_name]
    root_xattrs = {}
    xattr_errors = {}
    try:
        names = os.listxattr(ROOT, follow_symlinks=False)
    except OSError as error:
        xattr_errors['listxattr'] = str(error)
    else:
        for name in names:
            try:
                root_xattrs[name] = os.getxattr(ROOT, name, follow_symlinks=False).hex()
            except OSError as error:
                xattr_errors[name] = str(error)
    business = {name: digest for name, digest in actual.items()
                if name not in accepted_controls}
    missing = sorted(set(expected) - set(business))
    unexpected = sorted(set(business) - set(expected))
    changed = {name: {'expected': expected[name], 'actual': business[name]}
               for name in sorted(set(expected) & set(business))
               if expected[name] != business[name]}
    comparison = {'label': label, 'expected_count': len(expected),
                  'actual_count': len(actual), 'business_count': len(business),
                  'missing': missing, 'unexpected': unexpected, 'changed': changed,
                  'accepted_overlay_controls': accepted_controls,
                  'invalid_marker': marker['present'] and marker_name not in accepted_controls,
                  'marker': marker, 'root_xattrs_hex': root_xattrs,
                  'root_xattr_errors': xattr_errors}
    (RESULTS / (label + '-actual-manifest.json')).write_text(
        json.dumps(actual, sort_keys=True, indent=2) + '\n')
    (RESULTS / (label + '-comparison.json')).write_text(
        json.dumps(comparison, sort_keys=True, indent=2) + '\n')
    if (len(expected) != EXPECTED_FILES or len(business) != EXPECTED_FILES or
            missing or unexpected or changed or comparison['invalid_marker']):
        raise RuntimeError('contents differ from the 161-file workload manifest; '
                           'see ' + label + '-comparison.json')
    record = {'label': label, 'files': len(business), 'raw_files': len(actual),
              'accepted_overlay_controls': accepted_controls, 'verified': True}
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
