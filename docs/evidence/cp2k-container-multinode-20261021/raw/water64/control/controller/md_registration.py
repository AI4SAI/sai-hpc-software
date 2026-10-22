#!/usr/bin/env python3
"""Order DeepMD registration before LAMMPS emits its name-keyed registry.

This is a recorded local integration patch, not an upstream source revision.
The post-configure guard is only a build preflight; runtime parity and scientific
acceptance remain mandatory.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

INCLUDE = 'include(/workspace/deepmd-kit/source/lmp/builtin.cmake)'
STYLE_DIR = 'set(LAMMPS_STYLE_HEADERS_DIR ${CMAKE_CURRENT_BINARY_DIR}/styles)'
GENERATE = 'GenerateStyleSources(${LAMMPS_STYLE_HEADERS_DIR})'
REQUIRED = {
    'compute': {'compute_deepmd_fparam_dedn.h': ('deepmd/fparam/dedn',),
                'compute_deeptensor_atom.h': ('deeptensor/atom',)},
    'fix': {'fix_dplr.h': ('dplr',)},
    'kspace': {'pppm_dplr.h': ('pppm/dplr',)},
    'pair': {'pair_deepmd.h': ('deepmd',), 'pair_deepspin.h': ('deepspin',),
             'pair_dpa4spin.h': ('dpa4spin',),
             'pair_deepmd_kokkos.h': ('deepmd/kk', 'deepmd/kk/device', 'deepmd/kk/host'),
             'pair_dpa4spin_kokkos.h': ('dpa4spin/kk', 'dpa4spin/kk/device', 'dpa4spin/kk/host')},
}


def integrate(text):
    """Idempotent for both legacy header and modern source-based registries."""
    lines = text.splitlines()
    mentions = [line for line in lines if 'source/lmp/builtin.cmake' in line]
    if mentions not in ([], [INCLUDE]):
        raise ValueError('unknown or duplicate DeepMD builtin inclusion')
    if any('GenerateStyleSources' in line for line in lines):
        if lines.count(STYLE_DIR) != 1 or lines.count(GENERATE) != 1:
            raise ValueError('unknown or duplicate LAMMPS registry boundary')
        if lines.index(STYLE_DIR) >= lines.index(GENERATE):
            raise ValueError('LAMMPS style directory must precede source generation')
        if INCLUDE in lines:
            lines.remove(INCLUDE)
        lines.insert(lines.index(STYLE_DIR) + 1, INCLUDE)
        return '\n'.join(lines) + '\n'
    # Older releases consume style_*.h, which builtin.cmake regenerates itself.
    if INCLUDE not in lines:
        return text.rstrip('\n') + '\n' + INCLUDE + '\n'
    return text


def verify(styles):
    """Fail before compilation if any of the 13 required styles is unregistered."""
    styles = Path(styles)
    source_registry = (styles / 'style_compute.cpp').is_file()
    missing = []
    for kind, headers in REQUIRED.items():
        path = styles / f'style_{kind}.{"cpp" if source_registry else "h"}'
        text = path.read_text() if path.is_file() else ''
        for header, names in headers.items():
            if source_registry:
                for name in names:
                    if not re.search(r'\badd_builtin\s*\(\s*"' + re.escape(name) + r'"\s*,', text):
                        missing.append(kind + ':' + name)
            elif not re.search(r'^\s*#include\s+"' + re.escape(header) + '"', text, re.M):
                missing.extend(kind + ':' + name for name in names)
    if missing:
        raise ValueError('missing generated DeepMD registrations: ' + ', '.join(missing))
    return {'registered_styles': sum(len(names) for group in REQUIRED.values() for names in group.values()),
            'registry': 'source' if source_registry else 'header'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('integrate', 'verify'))
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    if args.action == 'integrate':
        before = args.path.read_text()
        after = integrate(before)
        if before != after:
            args.path.write_text(after)
        print('MD_DEEPMD_REGISTRATION_ORDER ' + json.dumps({
            'path': str(args.path), 'changed': before != after,
            'before_sha256': hashlib.sha256(before.encode()).hexdigest(),
            'after_sha256': hashlib.sha256(after.encode()).hexdigest(),
            'local_integration_patch': True}, sort_keys=True))
    else:
        print('MD_DEEPMD_REGISTRATION_VERIFIED ' + json.dumps(verify(args.path), sort_keys=True))


if __name__ == '__main__':
    main()
