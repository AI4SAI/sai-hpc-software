#!/usr/bin/env python3
"""CPU experiment only: preserve CP2K's configured matrix redistribution settings."""
import difflib
import hashlib
import json
from pathlib import Path
import sys

ORIGINAL_SHA256 = 'e3edf28d199b4ba624bd94b0e3e73a8863f6db2a7ffc8e4d8e0017f32f7e8c83'
FIXED_SHA256 = 'd2f091078e805fed2ea8b1be8e387d2a48abeb98408c6e62b280a637f74c6a90'
SOURCE_FILE = 'src/fm/cp_fm_diag_utils.F'


def transform(text):
    old_cleanup = ('      ! Return work to its initial state\n'
                   '      work_redistribute = cp_fm_redistribute_type()')
    old_init = ('      ! Init work\n'
                '      work_redistribute = cp_fm_redistribute_type()\n')
    first_setting = '      work_redistribute%a = a\n'
    for anchor in (old_cleanup, old_init, first_setting):
        if text.count(anchor) != 1:
            raise ValueError('Unrecognized redistribution lifecycle')
    text = text.replace(old_cleanup, '\n'.join(
        '      NULLIFY (work_redistribute%' + field + ')' for field in
        ('group_distribution', 'group_partition', 'blacs_env_new', 'para_env_new')))
    text = text.replace(old_init, '')
    return text.replace(first_setting,
                        '      work_redistribute = cp_fm_redistribute_type()\n' + first_setting)


def repair(source, output):
    raw = Path(source).read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()
    if checksum not in (ORIGINAL_SHA256, FIXED_SHA256):
        raise ValueError('Source is not the pinned CP2K 2026.2 file or its exact repair')
    result = transform(raw.decode()).encode() if checksum == ORIGINAL_SHA256 else raw
    if hashlib.sha256(result).hexdigest() != FIXED_SHA256:
        raise ValueError('Unexpected patched source')
    Path(output).write_bytes(result)
    return dict(source_file=SOURCE_FILE, before_sha256=checksum, after_sha256=FIXED_SHA256,
                upstream_issue='https://github.com/cp2k/cp2k/issues/6074',
                scope='CPU ScaLAPACK companion; public GPU install unchanged',
                diff=''.join(difflib.unified_diff(raw.decode().splitlines(True), result.decode().splitlines(True),
                                                fromfile='a/'+SOURCE_FILE, tofile='b/'+SOURCE_FILE)))


if __name__ == '__main__':
    print(json.dumps(repair(*sys.argv[1:]), indent=2))
