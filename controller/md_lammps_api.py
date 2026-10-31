#!/usr/bin/env python3
"""Adapt DeePMD's LAMMPS interface to the public Modify lookup API.

Only the checked-out interface sources change. The installed DeePMD package
and its C/C++ libraries remain the pinned dependency. Both the stable release
and current development LAMMPS expose the ID-based accessors.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

FILES = ('pair_base.cpp', 'compute_deepmd_fparam_dedn.cpp')
LEGACY = re.compile(r'\bmodify\s*->\s*(?:find_fix|find_compute|nfix|ncompute|fix|compute)\b')
TTM = '''  const FixTTMDP* ttm_fix = NULL;
  for (int ii = 0; ii < modify->nfix; ii++) {
    if (string(modify->fix[ii]->id) == ttm_fix_id) {
      ttm_fix = dynamic_cast<FixTTMDP*>(modify->fix[ii]);
    }
  }'''
TTM_ACCESSOR = '''  const FixTTMDP* ttm_fix =
      dynamic_cast<FixTTMDP*>(modify->get_fix_by_id(ttm_fix_id));'''


def adapt(text):
    """Keep each original missing-ID error and all subsequent shape checks."""
    counts = {}
    for kind in ('compute', 'fix'):
        # Match the entire old lookup, guard and indexed dereference together.
        # Unknown uses fail below instead of guessing at index/pointer semantics.
        pattern = re.compile(
            r'(?m)^(?P<indent>[ \t]*)int i' + kind
            + r' = modify->find_' + kind + r'\((?P<id>\w+)\);\n'
            + r'(?P=indent)if \(i' + kind + r' < 0\) \{(?P<error>[^{}]*)\n'
            + r'(?P=indent)\}\n\s*(?P=indent)' + kind.title() + r'\* ' + kind
            + r' = modify->' + kind + r'\[i' + kind + r'\];')

        def replace(match):
            indent = match['indent']
            return (indent + kind.title() + '* ' + kind + ' = modify->get_' + kind
                    + '_by_id(' + match['id'] + ');\n' + indent + 'if (!' + kind
                    + ') {' + match['error'] + '\n' + indent + '}')

        text, counts[kind] = pattern.subn(replace, text)
    counts['ttm'] = text.count(TTM)
    text = text.replace(TTM, TTM_ACCESSOR)
    if LEGACY.search(text):
        raise ValueError('unrecognized legacy LAMMPS Modify access; interface needs review')
    return text, counts


def apply(header, interface):
    header, interface = Path(header), Path(interface)
    api = header.read_text()
    for method in ('get_compute_by_id', 'get_fix_by_id'):
        if not re.search(r'\b' + method + r'\s*\(', api):
            raise ValueError('LAMMPS Modify is missing the required accessor: ' + method)
    pending, evidence = {}, {}
    for name in FILES:
        path = interface / name
        before = path.read_text()
        after, counts = adapt(before)
        pending[path] = after
        evidence[name] = dict(changed=before != after, replacements=counts,
            before_sha256=hashlib.sha256(before.encode()).hexdigest(),
            after_sha256=hashlib.sha256(after.encode()).hexdigest())
    # Validate every file first so an unknown second file cannot leave a
    # partially patched checkout.
    for path, after in pending.items():
        if evidence[path.name]['changed']:
            path.write_text(after)
    return dict(local_integration_patch=True, modify_header_sha256=hashlib.sha256(header.read_bytes()).hexdigest(),
                files=evidence)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('header', type=Path)
    parser.add_argument('interface', type=Path)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    record = apply(args.header, args.interface)
    args.receipt.write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')
    print('LAMMPS_DEEPMD_MODIFY_API ' + json.dumps(record, sort_keys=True))
