#!/usr/bin/env python3
"""Package an unchanged site distribution with an independently qualified starter."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tarfile


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('site_prefix', type=Path)
    parser.add_argument('starter', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    site, starter, output = args.site_prefix.resolve(), args.starter.resolve(), args.output.resolve()
    if output.is_relative_to(site):
        raise ValueError('output must be outside the source installation')
    output.mkdir(parents=True, exist_ok=False)
    archive = output / 'apptainer-runtime.tar.gz'
    entries = {}
    with tarfile.open(archive, 'w:gz') as bundle:
        for path in sorted(site.rglob('*')):
            name = str(path.relative_to(site))
            source = starter if name == 'libexec/apptainer/bin/starter' else path
            member = bundle.gettarinfo(str(source), arcname=name)
            if (not member.issym() and member.mode & 0o6022) or name.endswith('/starter-suid'):
                raise ValueError('privileged or writable entry: ' + name)
            member.uid = member.gid = 0
            member.uname = member.gname = 'root'
            if member.isfile():
                entries[name] = dict(type='file', mode=member.mode & 0o7777,
                                     sha256=digest(source), size=member.size)
                with source.open('rb') as stream:
                    bundle.addfile(member, stream)
            elif member.isdir():
                entries[name] = dict(type='directory', mode=member.mode & 0o7777)
                bundle.addfile(member)
            elif member.issym():
                target = (path.parent / member.linkname).resolve()
                if not target.is_relative_to(site):
                    raise ValueError('external symlink: ' + name)
                if os.path.isabs(member.linkname):
                    member.linkname = os.path.relpath(member.linkname, path.parent)
                entries[name] = dict(type='symlink', target=member.linkname)
                bundle.addfile(member)
            else:
                raise ValueError('special file: ' + name)
    inventory = output / 'payload-files.json'
    inventory.write_text(json.dumps(entries, sort_keys=True, indent=2) + '\n')
    print(json.dumps(dict(payload_sha256=digest(archive), payload_files_sha256=digest(inventory)), indent=2))


if __name__ == '__main__':
    main()
