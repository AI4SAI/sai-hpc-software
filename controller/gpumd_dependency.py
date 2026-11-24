#!/usr/bin/env python3
"""Freeze one accepted DeePMD SIF for a GPUMD request; never install on the host."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

from export_native import MANIFEST_PATH, SquashfsReader, canonical, read_installed_manifests, inventory
from release_contract import validate_identity
from source_cache import checksum
from remote_controller import site_app_binds

LOCK = 'gpumd-deepmd-lock.json'
API_FILES = ('include/deepmd/DeepPot.h', 'include/deepmd/c_api.h',
             'include/deepmd/canonical_graph.h', 'lib/libdeepmd_cc.so', 'lib/libdeepmd_c.so')


def regular(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or not path.is_file():
        raise ValueError('missing or redirected DeePMD dependency file: ' + str(path))
    return path


def validate(lock, target):
    if not isinstance(lock, dict) or set(lock) != {
            'schema', 'identity', 'image', 'sha256', 'binds', 'acceptance',
            'manifest_sha256', 'entry', 'api_files'} or lock['schema'] != 1:
        raise ValueError('invalid GPUMD DeePMD dependency lock')
    identity = validate_identity(lock['identity'])
    if identity['software'] != 'deepmd-kit' or identity['target'] != target or identity['stack_digest']:
        raise ValueError('GPUMD DeePMD dependency target/architecture mismatch')
    if lock['entry']['identity'] != identity:
        raise ValueError('DeePMD manifest identity differs from dependency')
    from native_module import validate_native_entry
    validate_native_entry(lock['entry'])
    for value in (lock['sha256'], lock['manifest_sha256'], *lock['api_files'].values()):
        if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
            raise ValueError('DeePMD dependency requires exact SHA-256 values')
    if set(lock['api_files']) != set(API_FILES):
        raise ValueError('incomplete DeePMD header/library evidence')
    image = Path(lock['image'])
    if not image.is_absolute() or '..' in image.parts or any(c in str(image) for c in ':,\n'):
        raise ValueError('invalid DeePMD dependency image path')
    accepted = lock['acceptance']
    if (accepted.get('build_verified') is not True or accepted.get('scientific_verified') is not True
            or not re.fullmatch('[0-9a-f]{64}', accepted.get('evidence_sha256', ''))):
        raise ValueError('DeePMD dependency lacks scientific acceptance')
    permitted = {str(path[0]) for path in site_app_binds('gpumd')}
    if not isinstance(lock['binds'], list) or not set(lock['binds']) <= permitted:
        raise ValueError('DeePMD dependency requires unregistered site bindings')
    return lock


def read(control, target, *, required=False):
    path = Path(control) / LOCK
    if not path.exists() and not path.is_symlink():
        if required:
            raise ValueError('GPUMD requires a frozen, accepted DeePMD dependency; run dependency selection first')
        return None  # Old requests keep their original controller/environment.
    return validate(json.loads(regular(path).read_text()), target)


def verify_image(lock, target):
    validate(lock, target)
    image = regular(lock['image'])
    if image.stat().st_mode & 0o222 or checksum(image) != lock['sha256']:
        raise ValueError('locked DeePMD SIF is writable or its checksum changed')
    return lock


def require_api(headers):
    version = re.search(r'^#define DP_C_API_VERSION (\d+)\s*$', headers['c_api.h'], re.M)
    if (not version or int(version[1]) < 31 or
            not all(token in headers['DeepPot.h'] for token in (
                'has_long_range()', 'DP_CanonicalIndexWidth index_width',
                'const std::int64_t* d_source_order', 'const double* box',
                'const DP_HaloExchange* halo')) or
            'canonical_index_width' not in headers['canonical_graph.h']):
        raise ValueError('incompatible DeePMD canonical API: GPUMD needs index-width, int64 source order, '
                         'halo and long-range cell support (site 3.2.0/API 30 is unsupported)')
    return int(version[1])


def from_candidate(record, target, reader_factory=SquashfsReader.from_sif):
    identity = validate_identity(record['identity'])
    if identity['software'] != 'deepmd-kit' or identity['target'] != target:
        raise ValueError('accepted DeePMD candidate has the wrong target')
    reader = reader_factory(record['image'])
    manifest, data = reader.read_manifest(identity['install_prefix'].lstrip('/') + '/' + MANIFEST_PATH)
    if manifest['entry']['identity'] != identity:
        raise ValueError('accepted SIF contains a different DeePMD identity')
    files = {row['path']: row for row in manifest['files']}
    api_files, headers = {}, {}
    for name in API_FILES:
        path = identity['install_prefix'].lstrip('/') + '/' + name
        row = files.get(path, {})
        if row.get('kind') != 'file':
            raise ValueError('DeePMD SIF lacks a regular required header/library: ' + name)
        api_files[name] = row['sha256']
        if name.endswith('.h'):
            content = subprocess.check_output(reader.command([path]))
            if hashlib.sha256(content).hexdigest() != row['sha256']:
                raise ValueError('DeePMD header differs from its accepted manifest')
            headers[Path(name).name] = content.decode()
    require_api(headers)
    result = dict(schema=1, identity=identity, image=record['image'], sha256=record['sha256'],
                  binds=record['binds'], entry=manifest['entry'], api_files=api_files,
                  manifest_sha256=hashlib.sha256(data).hexdigest(),
                  acceptance={key: record[key] for key in (
                      'build_verified', 'scientific_verified', 'evidence', 'evidence_sha256')})
    return verify_image(result, target)


def resolve(project, target):
    # Reuse the same revalidation as daily beta publication, including accepted
    # recovery runs and the original build's immutable controller. Never infer
    # acceptance from a successful build, directory name or mutable module alias.
    from beta_candidates import candidate
    rows = []
    for path in (Path(project) / 'experimental/deepmd-standalone/runs').glob('dp-*/results/status.json'):
        record = json.loads(regular(path).read_text())
        identity = record.get('delivery', {}).get('identity', {})
        if (identity.get('target') == target and identity.get('track') == 'development'
                and record.get('build_verified') is True and
                (record.get('scientific_verified') is True or
                 (path.parent / 'acceptance-recovery.json').is_file())):
            rows.append((regular(path.parent.parent / 'request.json').stat().st_mtime_ns, str(path), identity))
    if not rows:
        raise ValueError('no accepted DeePMD delivery for GPUMD target ' + target)
    # Select once. A broken or incompatible newest accepted delivery is an
    # explicit failure, never an unnoticed downgrade to an older interface.
    _, path, identity = max(rows)
    return from_candidate(candidate('deepmd-kit', Path(path).parent.parent, identity), target)


def select(project, target, control):
    control = Path(control)
    if not control.is_absolute() or control.resolve() != control or not control.is_dir():
        raise ValueError('missing or redirected GPUMD controller directory')
    # The controller directory belongs to this request. Serialize the initial
    # selection, and keep the same dependency on every relay retry.
    mutex_path = control / '.gpumd-deepmd-selection.lock'
    if mutex_path.exists() or mutex_path.is_symlink():
        regular(mutex_path)
    descriptor = os.open(mutex_path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as mutex:
        fcntl.flock(mutex, fcntl.LOCK_EX)
        locked = read(control, target)
        if locked is None:
            locked = resolve(project, target)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=control,
                        prefix='.' + LOCK + '.', delete=False) as out:
                    temporary = Path(out.name)
                    out.write(canonical(locked))
                    out.flush()
                    os.fchmod(out.fileno(), 0o444)
                    os.fsync(out.fileno())
                # Publish only complete, closed, read-only bytes. Like the
                # immutable controller upload, link is atomic and refuses to
                # replace any intervening file or symlink at the final path.
                os.link(temporary, control / LOCK)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        return verify_image(locked, target)


def installed(lock):
    """Verify the dependency in the build base/final SIF, without modifying it."""
    prefix = Path(lock['identity']['install_prefix'])
    if checksum(regular(prefix / MANIFEST_PATH)) != lock['manifest_sha256']:
        raise ValueError('bundled DeePMD manifest differs from locked SIF')
    entries = [lock['entry']]
    if read_installed_manifests(entries) != inventory(entries):
        raise ValueError('bundled DeePMD payload differs from accepted inventory')
    return prefix


def environment(lock):
    prefix = installed(lock)
    runtime = lock['entry']['runtime']
    lines = []
    for key in ('PATH', 'LD_LIBRARY_PATH', 'PYTHONPATH'):
        values = runtime['prepend'].get(key, [])
        # The dependency's Python/backend selection precedes site frameworks;
        # keep GPUMD's separately selected compiler at the head of PATH.
        if key == 'PATH':
            values = [str(prefix / 'bin')]
        if values:
            lines.append('export ' + key + '=' + shlex.quote(':'.join(values)) +
                         '"${' + key + ':+:$' + key + '}"')
    for key, value in dict(runtime['set'], DEEPMD_ROOT=str(prefix),
                           DEEPMD_PYTHON=str(prefix / 'bin/python')).items():
        lines.append('export ' + key + '=' + shlex.quote(value))
    return '\n'.join(lines) + '\n'


def declaration(lock):
    return dict(identity=lock['identity'], manifest_sha256=lock['manifest_sha256'],
                image_sha256=lock['sha256'])


def verify_link(report, prefix):
    if 'not found' in report:
        raise ValueError('GPUMD DeePMD library dependencies are unresolved')
    for library in ('libdeepmd_cc.so', 'libdeepmd_c.so'):
        matches = re.findall(r'^\s*' + re.escape(library) + r' => (\S+) ', report, re.M)
        if library == 'libdeepmd_c.so' and not matches:
            continue  # The C++ API probe can be linked with --as-needed.
        if matches != [str(Path(prefix) / 'lib' / library)]:
            raise ValueError('GPUMD loaded a different DeePMD library: ' + library)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('op', choices=('select', 'verify-image', 'environment', 'prefix'))
    parser.add_argument('project')
    parser.add_argument('target')
    parser.add_argument('control')
    args = parser.parse_args()
    lock = select(args.project, args.target, args.control) if args.op == 'select' else read(
        args.control, args.target, required=True)
    if args.op == 'verify-image':
        verify_image(lock, args.target)
    elif args.op == 'environment':
        print(environment(lock), end='')
    elif args.op == 'prefix':
        print(lock['identity']['install_prefix'])
    else:
        print(json.dumps(lock, sort_keys=True))


if __name__ == '__main__':
    main()
