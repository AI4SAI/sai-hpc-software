#!/usr/bin/env python3
"""Allowlist installed runtime references; this is not a copy/relocation test."""
from pathlib import Path
import re
import subprocess
import sys

SITE_ROOTS = ('/opt/devtools', '/opt/apps/conda_env/deepmd-kit-3.2.0',
              '/opt/apps/plumed/plumed-2.10.1', '/usr', '/lib', '/lib64')


def allowed(path, roots):
    path = Path(path)
    return path.is_absolute() and any(path == root or root in path.parents for root in map(Path, roots))


def check_reference(value, roots, *, origin=None, resolve=False):
    if any(x in value for x in ('/workspace', '/control', '/input', '/home/', '/runtime', '.new/')):
        raise ValueError('runtime reference contains build, task or stale prefix: ' + value)
    expanded = value.replace('${ORIGIN}', str(origin or '$ORIGIN')).replace('$ORIGIN', str(origin or '$ORIGIN'))
    if '$' in expanded or not Path(expanded).is_absolute():
        raise ValueError('unresolved or relative runtime search path: ' + value)
    normalized = Path(expanded).resolve(strict=resolve)
    if not allowed(normalized, roots):
        raise ValueError('runtime reference outside canonical/site allowlist: ' + value)
    return normalized


def check_symlink(path, roots):
    path = Path(path)
    raw = str(path.readlink())
    if any(part in raw for part in ('/workspace', '/control', '/input', '/home/', '.new/')):
        raise ValueError('symlink targets stale or untrusted installation')
    try:
        destination = path.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ValueError('broken or cyclic installed symlink') from error
    if not allowed(destination, roots):
        raise ValueError('installed symlink escapes canonical/site roots')
    return destination


def check_dynamic(text, path, roots):
    """Return whether dynamic dependencies exist; static ELF is valid."""
    for needed in re.findall(r'\(NEEDED\).*?\[([^]]+)\]', text):
        if '/' in needed or not re.fullmatch(r'[A-Za-z0-9_.+-]+', needed):
            raise ValueError('DT_NEEDED must be a soname, not a runtime path')
    for value in re.findall(r'\((?:RPATH|RUNPATH)\).*?\[([^]]*)\]', text):
        for entry in value.split(':'):
            if not entry:
                raise ValueError('empty RUNPATH component searches current working directory')
            check_reference(entry, roots, origin=Path(path).parent)
    return '(NEEDED)' in text


def check_metadata(text, roots):
    if any(re.search(re.escape(prefix) + r'(?:/|[\s:;"\']|$)', text)
           for prefix in ('/workspace', '/control', '/input', '/runtime', '/home')):
        raise ValueError('installed operational metadata contains a build/task/home path')
    # ${_IMPORT_PREFIX}/lib is relative to installed CMake metadata. Only
    # literal absolute paths need this allowlist; do not scan URL slashes.
    for value in re.findall(r'(?<![A-Za-z0-9_$}/.])(/[A-Za-z0-9_.$+{}@/-]+)', text):
        check_reference(value, roots)


def audit(prefixes):
    prefixes = list(map(Path, prefixes))
    if len(prefixes) != 2 or any(not str(p).startswith('/opt/software/') for p in prefixes):
        raise ValueError('exactly two canonical installation prefixes are required')
    roots = [*prefixes, *map(Path, SITE_ROOTS)]
    inspected = 0
    for prefix in prefixes:
        if prefix.is_symlink() or prefix.resolve() != prefix:
            raise ValueError('canonical prefix cannot be redirected')
        for path in prefix.rglob('*'):
            if path.is_symlink():
                check_symlink(path, roots)
                continue
            if not path.is_file() or '/share/sai/upstream-tests/' in str(path):
                continue
            with path.open('rb') as stream:
                magic = stream.read(4)
            if magic == b'\x7fELF':
                dynamic = subprocess.check_output(['readelf', '-d', str(path)], text=True)
                if check_dynamic(dynamic, path, roots):
                    ldd = subprocess.check_output(['ldd', str(path)], text=True, stderr=subprocess.STDOUT)
                    if 'not found' in ldd:
                        raise ValueError(f'unresolved installed dependency: {path}\n{ldd}')
                    for resolved in re.findall(r'(?:=>\s+|^\s*)(/[^\s]+)', ldd, re.M):
                        check_reference(resolved, roots, resolve=True)
                inspected += 1
            elif path.suffix in ('.cmake', '.pc') or path.name == 'runtime-env.sh':
                check_metadata(path.read_text(errors='replace'), roots)
    if inspected < 4:
        raise ValueError('incomplete binary runtime-path audit')
    print(f'MD_RUNTIME_PATH_AUDIT_PASSED {inspected}; SAME-PREFIX COPY TEST STILL REQUIRED')


if __name__ == '__main__':
    audit(sys.argv[1:])
