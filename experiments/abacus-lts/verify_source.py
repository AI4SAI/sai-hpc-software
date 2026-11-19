#!/usr/bin/env python3
"""Check a GitHub source archive against every blob in its pinned Git tree."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile


def verify(archive, tree, commit):
    if tree.get('truncated') is not False or tree['sha'] != commit['tree']['sha']:
        raise ValueError('complete tree for the exact commit required')
    expected = {row['path']: row for row in tree['tree'] if row['type'] == 'blob'}
    observed = set()
    with tarfile.open(archive, 'r:gz') as stream:
        root = None
        for member in stream:
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or not path.parts:
                raise ValueError('unsafe archive path')
            if root is None:
                root = path.parts[0]
            if path.parts[0] != root:
                raise ValueError('multiple archive roots')
            if member.isdir():
                continue
            relative = str(PurePosixPath(*path.parts[1:]))
            if relative not in expected or relative in observed:
                raise ValueError('unexpected/duplicate archive file: ' + relative)
            row = expected[relative]
            if member.issym() and row['mode'] == '120000':
                contents = member.linkname.encode()
            elif member.isfile() and row['mode'] in ('100644', '100755'):
                if bool(member.mode & 0o111) != (row['mode'] == '100755'):
                    raise ValueError('executable mode differs: ' + relative)
                contents = stream.extractfile(member).read()
            else:
                raise ValueError('archive type differs: ' + relative)
            blob = hashlib.sha1(b'blob ' + str(len(contents)).encode() + b'\0' + contents).hexdigest()
            if blob != row['sha']:
                raise ValueError('Git blob mismatch: ' + relative)
            observed.add(relative)
    if observed != set(expected):
        raise ValueError('archive omits tracked files: ' + ', '.join(sorted(set(expected) - observed)[:10]))
    with Path(archive).open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(source_sha=commit['sha'], source_ref='LTS', repository='deepmodeling/abacus-develop',
                source_tree=tree['sha'], archive=Path(archive).name, archive_sha256=checksum,
                tree_verified=True, verified_blobs=len(observed))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--tree', required=True, type=Path)
    parser.add_argument('--commit', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    record = verify(args.archive, json.loads(args.tree.read_text()), json.loads(args.commit.read_text()))
    args.output.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record))
