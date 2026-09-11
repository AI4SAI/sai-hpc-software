#!/usr/bin/env python3
"""Audit installed ELF RUNPATH and operational metadata, not debug provenance.

The installed /opt prefixes can be copied as-is to physical storage. System
dependencies keep their original read-only /opt paths; arbitrary prefix moves
are not promised by this audit.
"""
from pathlib import Path
import subprocess
import sys
from md_evidence import audit_runtime_metadata


def audit(prefixes):
    inspected = 0
    for prefix in map(Path, prefixes):
        if not str(prefix).startswith('/opt/software/'):
            raise ValueError('not a canonical installation prefix')
        for path in prefix.rglob('*'):
            if path.is_symlink():
                audit_runtime_metadata(str(path.readlink()))
                continue
            if not path.is_file() or '/share/sai/upstream-tests/' in str(path):
                continue
            with path.open('rb') as stream:
                magic = stream.read(4)
            if magic == b'\x7fELF':
                dynamic = subprocess.check_output(['readelf', '-d', str(path)], text=True)
                audit_runtime_metadata(dynamic)
                ldd = subprocess.check_output(['ldd', str(path)], text=True, stderr=subprocess.STDOUT)
                if 'not found' in ldd:
                    raise ValueError(f'unresolved installed dependency: {path}\n{ldd}')
                audit_runtime_metadata(ldd)
                inspected += 1
            elif path.suffix in ('.cmake', '.pc') or path.name == 'runtime-env.sh':
                audit_runtime_metadata(path.read_text(errors='replace'))
    if inspected < 4:
        raise ValueError('incomplete binary relocation audit')
    print(f'MD_RUNTIME_PATH_AUDIT_PASSED {inspected}')


if __name__ == '__main__':
    audit(sys.argv[1:])
