"""Explicit private/application and independent devtools Spack layouts."""
import hashlib
from pathlib import Path, PurePosixPath
import re

DEVTOOLS = '/opt/devtools'


def patch_id():
    # This post-concretization source repair is not covered by Spack's DAG hash.
    return hashlib.sha256(Path(__file__).with_name('cp2k_spack_libint_repair.py').read_bytes()).hexdigest()[:12]


def validate_layout(partition, prefix, layout='private'):
    from cp2k_spack import TARGETS, validated_prefix
    if layout == 'private':
        return validated_prefix(partition, prefix)
    if layout != 'devtools' or partition not in TARGETS or str(prefix) != DEVTOOLS:
        raise ValueError('independent dependencies require the exact /opt/devtools store')
    return DEVTOOLS


def store_config():
    from cp2k_spack import GCC_VERSION
    return {'root': DEVTOOLS, 'projections': {
        'all': '{name}/{version}-gcc' + GCC_VERSION + '-{architecture.target}-{hash}',
        'libint': '{name}/{version}-gcc' + GCC_VERSION + '-{architecture.target}-{hash}-p' + patch_id(),
    }}


def validate_package_prefix(path, name, version, target, dag_hash):
    from cp2k_spack import GCC_VERSION
    for value in (name, version, target):
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value):
            raise ValueError('invalid devtools package identity')
    if not re.fullmatch(r'[a-z2-7]{32}', dag_hash):
        raise ValueError('invalid Spack DAG hash')
    suffix = '-p' + patch_id() if name == 'libint' else ''
    expected = f'{DEVTOOLS}/{name}/{version}-gcc{GCC_VERSION}-{target}-{dag_hash}{suffix}'
    if path != expected or str(PurePosixPath(path)) != path:
        raise ValueError('devtools package path differs from its concrete identity')
    return path
