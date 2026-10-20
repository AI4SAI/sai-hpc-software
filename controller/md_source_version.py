#!/usr/bin/env python3
"""Restore resolved release names after commit-only transport into an overlay."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess


def git(repository, *args):
    return subprocess.check_output(
        ['git', '-C', str(repository), '-c', 'core.hooksPath=/dev/null', *args],
        text=True).strip()


def restore_release_tag(repository, source):
    """Only name the exact commit already resolved from an upstream release tag.

    The bare-cache transport contains commit objects, not upstream tag objects.
    This local lightweight tag recovers version discovery without claiming to
    reproduce the upstream annotated tag or changing the source tree.
    """
    repository = Path(repository)
    if git(repository, 'rev-parse', 'HEAD') != source['sha']:
        raise ValueError('checkout differs from the locked source SHA')
    if source['track'] == 'development':
        return {'changed': False, 'track': 'development', 'sha': source['sha']}
    if source['track'] not in ('release', 'prerelease'):
        raise ValueError('unknown source channel')
    tag = source['ref']
    ref = 'refs/tags/' + tag
    git(repository, 'check-ref-format', ref)
    found = subprocess.run(['git', '-C', str(repository), 'show-ref', '--verify', '--quiet', ref])
    if found.returncode not in (0, 1):
        raise ValueError('cannot inspect checkout release tag')
    if found.returncode == 0:
        if git(repository, 'rev-parse', ref + '^{commit}') != source['sha']:
            raise ValueError('existing release tag differs from the locked source SHA')
    else:
        git(repository, '-c', 'tag.gpgSign=false', 'tag', '--no-sign', '--', tag, source['sha'])
    if git(repository, 'describe', '--tags', '--exact-match', 'HEAD') != tag:
        raise ValueError('ambiguous checkout release version')
    return {'changed': found.returncode == 1, 'track': source['track'],
            'sha': source['sha'], 'local_lightweight_tag': tag}


def verify_deepmd_version(source, observed):
    if source['track'] == 'development':
        return {'track': 'development', 'observed': observed}
    # The build environment already requires packaging. Compare normalized
    # Python versions so an upstream vX.Y.Z/rc tag has its standard spelling.
    from packaging.version import Version
    expected = str(Version(source['ref']))
    if Version(observed) != Version(expected):
        raise ValueError(f'DeePMD release version mismatch: expected {expected}, found {observed}')
    return {'track': source['track'], 'expected': expected, 'observed': observed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('restore', 'verify-deepmd'))
    args = parser.parse_args()
    from md_controller import validate_pair
    delivery = validate_pair(json.loads(os.environ['SAI_MD_DELIVERY']))
    sources = delivery['plan']['sources']
    if args.action == 'restore':
        for name, source in sources.items():
            print('MD_UPSTREAM_RELEASE_TAG ' + json.dumps(
                dict(software=name, **restore_release_tag(Path('/workspace') / name, source)), sort_keys=True))
    else:
        print('MD_DEEPMD_VERSION ' + json.dumps(verify_deepmd_version(
            sources['deepmd-kit'], importlib.metadata.version('deepmd-kit')), sort_keys=True))


if __name__ == '__main__':
    main()
