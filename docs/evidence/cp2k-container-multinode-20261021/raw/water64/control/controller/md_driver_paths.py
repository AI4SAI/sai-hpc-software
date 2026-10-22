#!/usr/bin/env python3
"""Replace Apptainer's transient library directory with verified native paths.

Only byte-identical libraries beneath the read-only host /usr mount may replace
injected files. This is loader-path preparation, not GPU/scientific acceptance.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sys

INJECTED = '/.singularity.d/libs'
# Observed OpenSAI Ubuntu library layout. Do not infer paths from the login
# node's driver version: this check runs afresh on each allocated build node.
NATIVE_DIRS = ('/usr/lib/x86_64-linux-gnu', '/usr/lib/x86_64-linux-gnu/vdpau',
               '/usr/lib64', '/usr/lib')
REQUIRED = ('libcuda.so.1', 'libnvidia-ml.so.1', 'libnvidia-ptxjitcompiler.so.1')
FAKEROOT_FILES = ('fakeroot', 'faked', 'libfakeroot.so')


def normalize(library_path, *, root=Path('/')):
    """Preserve search order; refuse unknown, absent or different replacements."""
    root = Path(root)
    components = [part for part in library_path.split(':') if part]
    for part in components:
        if not part.startswith('/') or '..' in Path(part).parts or '\n' in part:
            raise ValueError('invalid library search directory: ' + part)
        if '.singularity.d' in part and part != INJECTED:
            raise ValueError('unrecognized container library path: ' + part)
    if INJECTED not in components:
        return ':'.join(components), {'changed': False, 'libraries': []}

    def local(path):
        return root / path.lstrip('/')

    digests = {}

    def digest(path, *, elf=True):
        resolved = path.resolve(strict=True)
        if resolved not in digests:
            if not resolved.is_file():
                raise ValueError('driver library is not a regular file: ' + str(path))
            with resolved.open('rb') as stream:
                if elf and stream.read(4) != b'\x7fELF':
                    raise ValueError('driver library is not ELF: ' + str(path))
                stream.seek(0)
                digests[resolved] = hashlib.file_digest(stream, 'sha256').hexdigest()
        return digests[resolved]

    files = sorted(local(INJECTED).iterdir())
    if not set(REQUIRED) <= {path.name for path in files}:
        raise ValueError('injected directory lacks required NVIDIA driver libraries')
    helpers = [path for path in files if path.name in FAKEROOT_FILES]
    omitted = []
    if helpers:
        if {path.name for path in helpers} != set(FAKEROOT_FILES):
            raise ValueError('incomplete injected fakeroot helper group')
        # Apptainer 1.4.4 mounts these three non-driver files into the same
        # directory as --nv libraries. Verify the exact host helper group;
        # they are build machinery, not native application dependencies.
        for variant in ('sysv', 'tcp'):
            originals = (f'/usr/bin/fakeroot-{variant}', f'/usr/bin/faked-{variant}',
                         f'/usr/lib/x86_64-linux-gnu/libfakeroot/libfakeroot-{variant}.so')
            if all(local(original).is_file() and
                   digest(local(INJECTED) / name, elf=False) == digest(local(original), elf=False)
                   for name, original in zip(FAKEROOT_FILES, originals)):
                omitted = [{'name': name, 'host_source': original,
                            'sha256': digest(local(original), elf=False)}
                           for name, original in zip(FAKEROOT_FILES, originals)]
                break
        if not omitted:
            raise ValueError('injected fakeroot helpers differ from supported host group')
    replacements, evidence = [], []
    for injected in files:
        name = injected.name
        if name in FAKEROOT_FILES:
            continue
        if not re.fullmatch(r'[A-Za-z0-9_.+-]+\.so(?:\.[A-Za-z0-9_.+-]+)?', name):
            raise ValueError('unexpected injected library entry: ' + name)
        expected = digest(injected)
        candidates = [local(directory) / name for directory in NATIVE_DIRS]
        candidate = next((path for path in candidates if path.exists()), None)
        if candidate is None:
            raise ValueError('no native replacement for injected library: ' + name)
        real = candidate.resolve(strict=True)
        if not real.is_relative_to(local('/usr').resolve(strict=True)):
            raise ValueError('native driver replacement escapes host /usr: ' + str(candidate))
        if digest(candidate) != expected:
            raise ValueError('native/injected driver bytes differ: ' + name)
        directory = '/' + str(candidate.parent.relative_to(root))
        if directory not in replacements:
            replacements.append(directory)
        evidence.append({'name': name, 'native': directory + '/' + name, 'sha256': expected})

    result = list(dict.fromkeys(value for part in components
                               for value in (replacements if part == INJECTED else [part])))
    # Several native directories may be necessary. Their combined ordering must
    # not select another DSO of the same name, nor displace earlier module paths.
    for item in evidence:
        def first(paths):
            return next(local(path) / item['name'] for path in paths
                        if (local(path) / item['name']).exists())
        if digest(first(components)) != digest(first(result)):
            raise ValueError('native driver replacement changes library precedence: ' + item['name'])
    return ':'.join(result), {'changed': True, 'replacement_directories': replacements,
                              'libraries': evidence, 'omitted_build_helpers': omitted}


def main():
    value, evidence = normalize(os.environ.get('LD_LIBRARY_PATH', ''))
    print('MD_NATIVE_DRIVER_PATHS ' + json.dumps(evidence, sort_keys=True), file=sys.stderr)
    print(value)


if __name__ == '__main__':
    main()
