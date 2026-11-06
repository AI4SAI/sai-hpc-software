#!/usr/bin/env python3
"""Reuse installed auxiliary libraries only after their CP2K passes science.

The project stores compressed installation trees, never build trees or a live
overlay. A contract excludes the CP2K commit/install prefix but includes its
dependency declarations, patches, native target, compiler and site providers.
Only installed text metadata is relocated when restoring another CP2K prefix.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time

from source_cache import checksum
from input_archive_cache import canonical, locked

SCHEMA = 1
TOKEN = b'@SAI_CP2K_INSTALL_PREFIX@'
STATE = Path('/workspace/auxiliary-cache.json')
CANDIDATE = Path('/auxiliary-cache')
CACHE = Path('/input/auxiliary-cache')
CONTROL = Path(__file__).resolve().parent
RECEIPTS = ('libxc-build/environment.sh', 'libxc-build/source-archives.sha256',
            'libxs-build/source-archives.sha256', 'tblite-build/source-archives.sha256')
SCRIPTS = ('cp2k_auxiliary_cache.py', 'input_archive_cache.py', 'cp2k_build.sh', 'cp2k_libxc.sh',
           'cp2k_libxs.sh', 'cp2k_dependencies.sh', 'cp2k_libxsmmConfig.cmake')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def read(path):
    return json.loads(canonical(path).read_text())


def write(path, value):
    canonical(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def source_contract(source):
    """Source-only changes in CP2K do not invalidate unchanged library inputs."""
    source = Path(source)
    declarations = ('stage3/install_libxc.sh', 'stage4/install_libxs.sh',
                    'stage8/install_tblite.sh', 'stage8/install_dftd4.sh')
    base = source / 'tools/toolchain/scripts'
    files = {name: checksum(base / name) for name in declarations}
    for pattern in ('stage4/libxs-*-jit-handle.patch', 'stage8/*gradient-fixes.patch'):
        files.update({str(p.relative_to(base)): checksum(p) for p in base.glob(pattern)})
    wigner = re.search(r'find_package\(wignernj\s[^)]*\)', (source / 'CMakeLists.txt').read_text())
    return dict(files=files, wignernj=bool(wigner), wignernj_requirement=wigner[0] if wigner else None)


def tool_identity(path):
    path = Path(path)
    require(path.is_file(), 'Missing cache toolchain input: ' + str(path))
    return dict(path=str(path), resolved=str(path.resolve()), sha256=checksum(path))


def contract(source, target, prefix, control=CONTROL):
    from cp2k_spack import GCC_PREFIX
    from cp2k_feature_contract import read_elpa_provider
    installed = read(Path('/results/install-success.json'))
    if installed.get('layout') != 'shared-devtools':
        return None
    sources = source_contract(source)
    tools = {}
    for name in ('gcc', 'g++', 'gfortran'):
        tools[name] = tool_identity(GCC_PREFIX + '/bin/' + name)
    mpi, blas = os.environ['MPI_HOME'], os.environ['OPENBLAS_ROOT']
    for name, path in (('mpifort', mpi + '/bin/mpifort'), ('mpi', mpi + '/lib/libmpi.so'),
                       ('blas', blas + '/lib/libopenblas.so'),
                       ('scalapack', blas + '/lib/libscalapack.so'),
                       ('site-libxc', os.environ['LIBXC_ROOT'] + '/lib/libxc.so')):
        tools[name] = tool_identity(path)
    if target != 'dsprhbm':
        cuda = '/opt/devtools/nvidia/cuda-12.9.1'
        for name in ('bin/nvcc', 'lib64/libcudart.so', 'lib64/libcublas.so'):
            tools['cuda/' + name] = tool_identity(cuda + '/' + name)
    for name in ('cmake',):
        tools[name] = tool_identity(shutil.which(name))
    native = subprocess.check_output([GCC_PREFIX + '/bin/gcc', '-march=native', '-mtune=native',
                                      '-Q', '--help=target'], text=True).strip()
    from cp2k_spack import WIGNERNJ_SHA256
    return dict(schema=SCHEMA, target=target, sources=sources, tools=tools,
                native_flags=native, flags=os.environ['CP2K_NATIVE_FLAGS'],
                recipes={name: checksum(control / name) for name in SCRIPTS},
                provider=installed['shared_provider_sha256'],
                elpa=read_elpa_provider(prefix, target),
                dbcsr='d963cac9cb79e04d82bedf0f4febce9cd06470f84c7d5a45f74645d11f8c4875',
                fypp='ec9d6fd0e54529e7873732be642ea9098e06cc7a1cbe0eb7faee31be6c2267fa',
                wignernj=WIGNERNJ_SHA256 if sources['wignernj'] else None)


def relative(name):
    path = PurePosixPath(name)
    require(str(path) == name and not path.is_absolute() and '..' not in path.parts
            and len(path.parts) >= 2 and path.parts[0] in ('payload', 'receipts'), 'Unsafe cache member')
    return path


def relocated(data, old, new, name):
    if old not in data:
        return data
    # CMake/pkg-config metadata and generated environment assignments are text.
    # Never patch ELF files, static archives, Fortran modules or opaque binaries.
    allowed = Path(name).suffix in ('.cmake', '.pc', '.la', '.sh', '.h')
    require(allowed and b'\0' not in data, 'Nonrelocatable cache input: ' + name)
    data.decode('utf-8')
    return data.replace(old, new)


def pack(prefix, workspace, output, spec, origin):
    prefix, workspace, output = map(canonical, (prefix, workspace, output))
    require(not (output / 'manifest.json').exists(), 'Cache candidate already exists')
    output.mkdir(parents=True, exist_ok=True)
    parts = ['dbcsr', 'libxs', 'tblite']
    if (prefix / 'dependencies/libxc').is_dir():
        parts.append('libxc')
    if spec['sources']['wignernj']:
        parts.append('wignernj')
    paths = []
    for part in parts:
        base = canonical(prefix / 'dependencies' / part)
        require(base.is_dir(), 'Missing installed dependency: ' + part)
        paths += [(p, 'payload/' + str(p.relative_to(prefix))) for p in (base, *sorted(base.rglob('*')))]
    paths += [(workspace / name, 'receipts/' + name) for name in RECEIPTS]
    entries = {}
    start = time.monotonic()
    archive = output / 'payload.tar.gz'
    with tarfile.open(archive, 'x:gz', compresslevel=1) as tar:
        for path, name in paths:
            relative(name)
            mode = path.lstat().st_mode
            entry = dict(mode=stat.S_IMODE(mode) & 0o755)
            info = tarfile.TarInfo(name)
            info.mode = entry['mode']
            if stat.S_ISDIR(mode):
                info.type = tarfile.DIRTYPE
                entry['kind'] = 'directory'
                tar.addfile(info)
            elif stat.S_ISLNK(mode):
                link = os.readlink(path)
                require(not Path(link).is_absolute() and path.resolve().is_relative_to(prefix)
                        and path.exists(), 'Cache link escapes installation: ' + name)
                info.type, info.linkname = tarfile.SYMTYPE, link
                entry.update(kind='symlink', target=link)
                tar.addfile(info)
            else:
                require(stat.S_ISREG(mode), 'Special cache file: ' + name)
                data = relocated(path.read_bytes(), str(prefix).encode(), TOKEN, name)
                info.size = len(data)
                entry.update(kind='file', size=len(data), sha256=hashlib.sha256(data).hexdigest())
                tar.addfile(info, io.BytesIO(data))
            entries[name] = entry
    record = dict(schema=SCHEMA, key=digest(spec), contract=spec, origin=origin,
                  components=parts, entries=entries, archive_sha256=checksum(archive),
                  archive_bytes=archive.stat().st_size, pack_seconds=time.monotonic()-start)
    write(output / 'manifest.json', record)
    return record


def validate_bundle(bundle, spec, *, accepted=True):
    bundle = canonical(bundle)
    record = read(bundle / 'manifest.json')
    require(record['schema'] == SCHEMA and record['contract'] == spec
            and record['key'] == digest(spec), 'Cache identity mismatch')
    archive = canonical(bundle / 'payload.tar.gz')
    require(archive.stat().st_size == record['archive_bytes'] and checksum(archive) == record['archive_sha256'],
            'Cached library archive checksum mismatch')
    if accepted:
        proof = read(bundle / 'accepted.json')
        require(proof.get('science_verified') is True and proof['key'] == record['key']
                and proof['manifest_sha256'] == checksum(bundle / 'manifest.json')
                and proof['archive_sha256'] == record['archive_sha256'], 'Cache has no matching science acceptance')
    return record


def unpack(bundle, prefix, workspace, spec):
    prefix, workspace = canonical(prefix), canonical(workspace)
    record = validate_bundle(bundle, spec)
    for name in record['components']:
        require(name in ('dbcsr', 'libxs', 'tblite', 'libxc', 'wignernj')
                and not (prefix / 'dependencies' / name).exists(), 'Cache restore would overwrite an installation')
    for name in RECEIPTS:
        require(not (workspace / name).exists(), 'Cache restore would overwrite evidence')
    seen, links = set(), []
    # Stage into the current project's overlay, never host /tmp or host /opt.
    with tempfile.TemporaryDirectory(prefix='.auxiliary-restore-', dir=workspace) as stage:
        stage = Path(stage)
        with tarfile.open(bundle / 'payload.tar.gz', 'r|gz') as tar:
            for member in tar:
                name = member.name
                rel = relative(name)
                require(name not in seen and name in record['entries'], 'Unexpected or duplicate cache member')
                seen.add(name)
                expected = record['entries'][name]
                require(member.mode == expected['mode'], 'Cache member permissions changed')
                destination = stage / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                if expected['kind'] == 'directory':
                    require(member.isdir(), 'Cache member type changed')
                    destination.mkdir(exist_ok=True)
                elif expected['kind'] == 'symlink':
                    require(member.issym() and member.linkname == expected['target']
                            and not Path(member.linkname).is_absolute(), 'Cache link changed')
                    links.append((destination, member.linkname))
                    continue
                else:
                    require(member.isfile() and member.size == expected['size'], 'Cache member type/size changed')
                    data = tar.extractfile(member).read()
                    require(hashlib.sha256(data).hexdigest() == expected['sha256'], 'Cache member content changed')
                    data = relocated(data, TOKEN, str(prefix).encode(), name)
                    with destination.open('xb') as stream:
                        stream.write(data)
                destination.chmod(expected['mode'])
        require(seen == set(record['entries']), 'Incomplete cache archive')
        for destination, target in links:
            require((destination.parent / target).resolve().is_relative_to(stage / 'payload'), 'Escaping cache link')
            destination.symlink_to(target)
        (prefix / 'dependencies').mkdir(exist_ok=True)
        for name in record['components']:
            (stage / 'payload/dependencies' / name).rename(prefix / 'dependencies' / name)
        for name in RECEIPTS:
            destination = workspace / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            (stage / 'receipts' / name).rename(destination)
    return record


def prepare(prefix, target):
    started = time.monotonic()
    spec = contract(Path('/workspace/source'), target, prefix)
    state = dict(schema=SCHEMA, prefix=str(prefix), contract=spec, hit=False,
                 timings={}, enabled=spec is not None)
    if spec is not None:
        state['key'] = digest(spec)
        bundle = CACHE / state['key']
        if bundle.exists() or bundle.is_symlink():
            cached = unpack(bundle, prefix, Path('/workspace'), spec)
            state.update(hit=True, cache_archive_sha256=cached['archive_sha256'], origin=cached['origin'])
    state['prepare_seconds'] = time.monotonic() - started
    write(STATE, state)
    print('hit' if state['hit'] else 'miss')


def finish(prefix):
    state = read(STATE)
    require(state['prefix'] == str(prefix), 'Cache prefix changed during build')
    if state['enabled'] and not state['hit']:
        record = pack(prefix, Path('/workspace'), CANDIDATE, state['contract'],
                      dict(source_sha=os.environ['CP2K_SOURCE_SHA'], prefix=str(prefix)))
        state.update(cache_archive_sha256=record['archive_sha256'], pack_seconds=record['pack_seconds'],
                     candidate_manifest_sha256=checksum(CANDIDATE / 'manifest.json'))
    write(STATE, state)
    for destination in (Path('/results/auxiliary-cache.json'), prefix / 'share/sai/auxiliary-cache.json'):
        write(destination, state)
    print('CP2K_AUXILIARY_CACHE ' + json.dumps({k: v for k, v in state.items() if k != 'contract'}, sort_keys=True))


def promote(project, run, artifact):
    """Called after the controller's normal publication/science gates pass."""
    project, run = canonical(project), canonical(run)
    receipt = run / 'results/auxiliary-cache.json'
    if not receipt.exists():
        return  # Historical repacks predate this optional cache.
    state = read(receipt)
    if not state['enabled'] or state['hit']:
        return
    require(run.parent == project / 'runs', 'Cache origin escaped project')
    manifest = read(Path(artifact).with_suffix('.json'))
    proof = manifest.get('cp2k_science', {})
    require(manifest.get('published') is True and manifest.get('verified') is True
            and proof.get('verified') is True and proof.get('artifact_sha256') == manifest['sha256']
            and manifest['identity']['install_prefix'] == state['prefix'], 'Cannot cache unaccepted CP2K dependencies')
    candidate = canonical(run / 'auxiliary-cache')
    record = validate_bundle(candidate, state['contract'], accepted=False)
    require(checksum(candidate / 'manifest.json') == state['candidate_manifest_sha256']
            and record['archive_sha256'] == state['cache_archive_sha256']
            and record['origin'] == dict(source_sha=manifest['source_sha'], prefix=state['prefix']),
            'Cache candidate differs from accepted build')
    root = canonical(project / 'cache/cp2k-auxiliary')
    root.mkdir(parents=True, exist_ok=True)
    target = canonical(root / record['key'])
    with locked(root / (record['key'] + '.lock')):
        if target.exists():
            validate_bundle(target, state['contract'])
            return
        with tempfile.TemporaryDirectory(prefix='.publish-', dir=root) as temporary:
            staged = Path(temporary) / 'entry'
            staged.mkdir()
            for name in ('payload.tar.gz', 'manifest.json'):
                shutil.copyfile(candidate / name, staged / name)
            acceptance = dict(schema=SCHEMA, key=record['key'], science_verified=True,
                              manifest_sha256=checksum(staged / 'manifest.json'),
                              archive_sha256=record['archive_sha256'], build_run=run.name,
                              artifact=str(artifact), artifact_sha256=manifest['sha256'],
                              science_evidence_sha256=proof['evidence_sha256'])
            write(staged / 'accepted.json', acceptance)
            validate_bundle(staged, state['contract'])
            for item in staged.iterdir():
                item.chmod(0o444)
            staged.rename(target)
    write(run / 'results/auxiliary-cache-published.json', acceptance)
    print('CP2K_AUXILIARY_CACHE_PUBLISHED ' + str(target), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('op', choices=('prepare', 'finish', 'time'))
    parser.add_argument('arguments', nargs='+')
    args = parser.parse_args()
    if args.op == 'prepare':
        prepare(Path(args.arguments[0]), args.arguments[1])
    elif args.op == 'finish':
        finish(Path(args.arguments[0]))
    else:
        name, started = args.arguments
        state = read(STATE)
        state['timings'][name] = max(0.0, time.time() - float(started))
        write(STATE, state)
        print('CP2K_DEPENDENCY_SECONDS ' + name + ' ' + str(state['timings'][name]), flush=True)


if __name__ == '__main__':
    main()
