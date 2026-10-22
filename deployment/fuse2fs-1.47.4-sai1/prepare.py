#!/usr/bin/env python3
"""Stage the already qualified binary and standard files in the project home."""
import json
from pathlib import Path
import re
import tarfile

import install as deploy

PROJECT = Path('/home/stardust/sai-hpc-software')
SOURCE = PROJECT / 'cache/tools' / ('fuse2fs-' + deploy.BINARY_SHA256) / 'bin/fuse2fs'
ARCHIVE = PROJECT / ('diagnostics/cp2k-release-20261016/'
                     'fuse2fs-efficiency-20261017-a/input/e2fsprogs-45651efd.tar.gz')
ARCHIVE_SHA256 = '3ba72ea2ec624fcbe8c155656623c16da92e07dff4a7ffb981dcadbd9990ccf4'


def configuration(site, prefix):
    # Replace only the helper search path; retain all other site settings.
    original = site.decode()
    text = re.sub(r'^\s*binary path\s*=.*$', '', original, flags=re.MULTILINE)
    return (text.rstrip() + '\n\nbinary path = ' + str(prefix) +
            '/bin:$PATH:/usr/bin:/bin:/usr/sbin:/sbin\n').encode()


def module_text(prefix, site_sha, config_sha):
    text = (deploy.PACKAGE / 'modulefile.in').read_text()
    for key, value in dict(VERSION=deploy.VERSION, PREFIX=str(prefix), BINARY_SHA256=deploy.BINARY_SHA256,
                           SITE_SHA256=site_sha, CONFIG_SHA256=config_sha).items():
        text = text.replace('@' + key + '@', value)
    return text.encode()


def main():
    deploy.plain(SOURCE)
    deploy.require(deploy.digest(SOURCE) == deploy.BINARY_SHA256, 'Qualified driver changed')
    deploy.require(deploy.digest(ARCHIVE) == ARCHIVE_SHA256, 'Source archive changed')
    root = deploy.PACKAGE / 'payload'
    deploy.require(not root.exists(), 'Payload already exists; verify it instead of restaging')
    site = deploy.SITE_CONFIG.read_bytes()
    config = configuration(site, deploy.PREFIX)
    with tarfile.open(ARCHIVE) as archive:
        notice = archive.extractfile('tytso-e2fsprogs-45651ef/NOTICE').read()
        man = archive.extractfile('tytso-e2fsprogs-45651ef/misc/fuse2fs.1.in').read().decode()
    man = man.replace('@E2FSPROGS_MONTH@', 'March').replace('@E2FSPROGS_YEAR@', '2025')
    man = man.replace('@E2FSPROGS_VERSION@', '1.47.4')
    for name, content in {'bin/fuse2fs': SOURCE.read_bytes(), 'etc/apptainer.conf': config,
                          'share/licenses/e2fsprogs/NOTICE': notice,
                          'share/man/man1/fuse2fs.1': man.encode()}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(content)
        path.chmod(deploy.MODES[name])
    for directory in [root, *(p for p in root.rglob('*') if p.is_dir())]:
        directory.chmod(0o755)
    module = module_text(deploy.PREFIX, deploy.hash_bytes(site), deploy.hash_bytes(config))
    with (deploy.PACKAGE / 'modulefile').open('xb') as stream:
        stream.write(module)
    (deploy.PACKAGE / 'modulefile').chmod(0o644)
    release = dict(schema=1, version=deploy.VERSION, upstream_version='1.47.4',
                   prefix=str(deploy.PREFIX), module=str(deploy.MODULE),
                   source_commit='45651efd6db7da8738bc9d8d1f17e99259cabfe4',
                   patched_source_sha256='c63d1fb25510d316836952953e9adddacd3214cd91d6002bf3b16aed350c4ddc',
                   source_archive_sha256=ARCHIVE_SHA256, binary_source=str(SOURCE),
                   site_config_sha256=deploy.hash_bytes(site),
                   module_sha256=deploy.hash_bytes(module),
                   files={name: dict(mode=mode, size=(root/name).stat().st_size,
                                     sha256=deploy.digest(root/name))
                          for name, mode in deploy.MODES.items()})
    with (deploy.PACKAGE / 'release.json').open('x') as stream:
        json.dump(release, stream, indent=2, sort_keys=True)
        stream.write('\n')
    (deploy.PACKAGE / 'release.json').chmod(0o644)
    print(json.dumps(dict(prefix=str(deploy.PREFIX), files=len(deploy.MODES),
                          bytes=sum(row['size'] for row in release['files'].values()))))


if __name__ == '__main__':
    main()
