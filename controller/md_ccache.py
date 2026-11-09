#!/usr/bin/env python3
"""Strict, project-backed compiler caching for the standalone LAMMPS build.

Every invocation asks the actual compiler for its current dependencies and
hashes their bytes (including system headers). This also detects new headers
which would shadow an old include. Only unchanged inputs may reuse an object.
No build directories, link results or acceptance results are cached.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

VERSION = '4.14.3'
ARCHIVE = 'ccache-4.14.3-linux-x86_64-musl-static.tar.xz'
ARCHIVE_SHA256 = 'd553fac006ed93a37342cee9228cf4e13dd66690fa1a8075c54c7bace9b72ae6'
BINARY_SHA256 = 'd8c63ad7be5029fdbea0e326255a07a1618de8dca12809cee0219dbedc74e4de'
URL = 'https://github.com/ccache/ccache/releases/download/v' + VERSION + '/' + ARCHIVE
TARGETS = ('16v100-avx2', '4v100-avx512', '8v100v0-avx512')


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def canonical(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or any(c in str(path) for c in ':,\n\r'):
        raise ValueError('compiler cache path must be canonical and absolute')
    return path


def tool_path(root):
    return canonical(root) / 'cache/tools' / ('ccache-' + VERSION) / 'ccache'


def store_path(root, target):
    if target not in TARGETS:
        raise ValueError('unsupported compiler cache target')
    return canonical(root) / 'cache/compiler/lammps' / target


def verify_tool(path):
    path = canonical(path)
    if not path.is_file() or digest(path) != BINARY_SHA256:
        raise ValueError('ccache executable differs from the pinned upstream release')
    return path


def download_tool(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'ccache'
    if path.exists():
        return verify_tool(path.resolve())
    archive = directory / ARCHIVE
    with urllib.request.urlopen(URL, timeout=120) as response, archive.open('wb') as out:
        shutil.copyfileobj(response, out)
    if digest(archive) != ARCHIVE_SHA256:
        raise ValueError('ccache release archive checksum differs')
    with tarfile.open(archive) as source:
        member = source.getmember(ARCHIVE.removesuffix('.tar.xz') + '/ccache')
        if not member.isfile():
            raise ValueError('ccache archive has no regular executable')
        data = source.extractfile(member).read()
    if hashlib.sha256(data).hexdigest() != BINARY_SHA256:
        raise ValueError('ccache archive executable checksum differs')
    path.write_bytes(data)
    path.chmod(0o755)
    return path


def install_tool(root, source):
    source = verify_tool(Path(source).resolve())
    target = tool_path(root)
    canonical(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return verify_tool(target)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as out:
        staging = Path(out.name)
        with source.open('rb') as incoming:
            shutil.copyfileobj(incoming, out)
    try:
        verify_tool(staging)
        staging.chmod(0o555)
        os.replace(staging, target)
    finally:
        staging.unlink(missing_ok=True)
    return target


def prepare_store(root, target):
    verify_tool(tool_path(root))
    directory = canonical(store_path(root, target))
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise ValueError('compiler cache must be private to the build account')
    return directory


def compiler_files(cc, cxx, nvcc):
    """Hash drivers and their actual code generators, not just version strings."""
    paths = {Path(p).resolve() for p in (cc, cxx, nvcc)}
    for compiler, names in ((cc, ('cc1', 'as', 'lto1')), (cxx, ('cc1plus',))):
        for name in names:
            value = subprocess.check_output([compiler, '-print-prog-name=' + name], text=True).strip()
            path = Path(value if '/' in value else shutil.which(value) or value)
            if not path.is_file():
                raise ValueError('compiler component not found: ' + value)
            paths.add(path.resolve())
    cuda = Path(nvcc).resolve().parent.parent
    # CUDA also ships debuggers/profilers with optional Python/GUI libraries.
    # Those are not compilation inputs and need not be runnable in the SIF.
    for name in ('nvcc', 'nvcc.profile', 'ptxas', 'fatbinary', 'nvlink', 'cudafe++', 'bin2c'):
        path = cuda / 'bin' / name
        if path.is_file():
            paths.add(path.resolve())
    for folder in ('nvvm/bin', 'nvvm/lib64', 'nvvm/libdevice'):
        paths.update(p.resolve() for p in (cuda / folder).rglob('*') if p.is_file())
    # A changed shared implementation of a driver also invalidates the cache.
    for path in sorted(paths):
        with path.open('rb') as stream:
            if stream.read(4) != b'\x7fELF':
                continue
        result = subprocess.run(['ldd', str(path)], text=True, capture_output=True)
        if 'not found' in result.stdout:
            raise ValueError('compiler shared library missing: ' + str(path))
        for line in result.stdout.splitlines():
            match = re.search(r'(?:=>\s+|^\s*)(/\S+)', line)
            if match:
                paths.add(Path(match[1]).resolve())
    return {str(path): digest(path) for path in sorted(paths)}


def prepare(target, output=Path('/runtime')):
    if target not in TARGETS:
        raise ValueError('unsupported compiler cache target')
    verify_tool(Path('/ccache-tool/ccache'))
    output = Path(output)
    context = dict(schema=1, target=target, policy='raw-dependencies-v1',
                   tool_sha256=BINARY_SHA256,
                   compilers=compiler_files(os.environ['CC'], os.environ['CXX'], shutil.which('nvcc')),
                   native_flags=subprocess.check_output(
                       [os.environ['CC'], '-march=native', '-Q', '--help=target'], text=True),
                   compiler_specs={name: subprocess.check_output(
                       [os.environ[name], '-dumpspecs'], text=True) for name in ('CC', 'CXX')},
                   environment={k: os.environ.get(k, '') for k in (
                       'NVCC_WRAPPER_DEFAULT_COMPILER', 'CUDAHOSTCXX', 'CUDA_ROOT',
                       'NVCC_PREPEND_FLAGS', 'NVCC_APPEND_FLAGS', 'GCC_EXEC_PREFIX',
                       'COMPILER_PATH', 'LIBRARY_PATH', 'LD_LIBRARY_PATH', 'PATH')})
    context_file = output / 'ccache-context.json'
    context_file.write_text(json.dumps(context, sort_keys=True, indent=2) + '\n')
    config = dict(tool='/ccache-tool/ccache', store='/ccache-store',
                  context=str(context_file), temporary='/workspace/tmp/ccache',
                  stats_log=str(output / 'ccache-stats.log'))
    Path(config['temporary']).mkdir(parents=True, exist_ok=True)
    (output / 'ccache-launcher.json').write_text(json.dumps(config, sort_keys=True) + '\n')
    print('LAMMPS_CCACHE_READY', target, digest(context_file), flush=True)


def dependency_arguments(argv, depfile):
    """Keep preprocessing options; remove only compilation/output options.

    Unrecognized dependency/precompiled-header/response-file modes bypass the
    cache. CMake's GCC/NVCC commands use separate -o/-MF/-MT arguments.
    """
    if '-c' not in argv or any(a.startswith(('@', '-Wp,', '-Xpreprocessor', '-include-pch',
                                           '-fmodules', '-fplugin', '--options-file')) for a in argv):
        return None
    output = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ('-o', '-MF', '-MT', '-MQ', '--output-file', '--dependency-output', '--dependency-target-name'):
            if i + 1 == len(argv):
                return None
            i += 2
            continue
        if arg in ('-c', '-MD', '-MMD', '-MP', '--compile', '--generate-dependencies-with-compile',
                   '--generate-nonsystem-dependencies-with-compile'):
            i += 1
            continue
        if arg.startswith(('-MF', '-MT', '-MQ', '-o', '--output-file=', '--dependency-output=')):
            return None
        output.append(arg)
        i += 1
    return [*output, '-M', '-MF', str(depfile), '-MT', 'sai_ccache_dependencies']


def dependencies(text):
    text = text.replace('\\\n', '')
    if not text.startswith('sai_ccache_dependencies:'):
        raise ValueError('unrecognized compiler dependency output')
    # Make escaping differs from shell quoting: quotes are literal; backslash
    # escapes spaces/# and $$ denotes a literal dollar. No shell is executed.
    lexer = shlex.shlex(text.split(':', 1)[1], posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ''
    lexer.quotes = ''
    result = sorted({word.replace('$$', '$') for word in lexer})
    if not result or any(not Path(p).is_file() for p in result):
        raise ValueError('missing or invalid compiler dependency')
    return result


def cache_environment(config, namespace, original=None):
    env = {k: v for k, v in (original if original is not None else os.environ).items()
           if not k.startswith('CCACHE_')}
    env.update(CCACHE_CONFIGPATH='/dev/null', CCACHE_DIR=config['store'],
               CCACHE_TEMPDIR=config['temporary'], CCACHE_STATSLOG=config['stats_log'],
               CCACHE_NAMESPACE=namespace, CCACHE_COMPILERCHECK='content',
               CCACHE_EXTRAFILES=config['context'], CCACHE_SLOPPINESS='',
               CCACHE_DIRECT='true', CCACHE_DEPEND='true', CCACHE_NOINODECACHE='true',
               CCACHE_MAXSIZE='20GiB', CCACHE_MAXFILES='0', CCACHE_COMPRESS='true')
    return env


def compile_command(config, compiler, argv):
    """Fail open to a real compilation when an input cannot be proven reusable."""
    with tempfile.TemporaryDirectory(prefix='dependencies-', dir=config['temporary']) as folder:
        depfile = Path(folder) / 'inputs.d'
        probe = dependency_arguments(argv, depfile)
        if probe is None:
            return subprocess.call([compiler, *argv])
        result = subprocess.run([compiler, *probe], capture_output=True)
        if result.returncode != 0 or not depfile.is_file():
            return subprocess.call([compiler, *argv])
        try:
            records = [(p, digest(p)) for p in dependencies(depfile.read_text())]
        except (OSError, ValueError):
            return subprocess.call([compiler, *argv])
        # Raw bytes participate even when preprocessing would erase the change
        # (comments, inactive #if branches), and system headers are included.
        namespace = hashlib.sha256(json.dumps(records, separators=(',', ':')).encode()).hexdigest()
        return subprocess.call([config['tool'], compiler, *argv],
                               env=cache_environment(config, namespace))


def statistics(output=Path('/runtime')):
    output = Path(output)
    config = json.loads((output / 'ccache-launcher.json').read_text())
    result = subprocess.check_output([config['tool'], '--print-log-stats', '--format=json'],
                                     env=cache_environment(config, ''), text=True)
    (output / 'ccache-statistics.json').write_text(result)
    print('LAMMPS_CCACHE_STATISTICS', result, flush=True)


def main():
    action, *args = sys.argv[1:]
    if action == 'compile':
        config, compiler, *argv = args
        return compile_command(json.loads(Path(config).read_text()), compiler, argv)
    if action == 'prepare':
        prepare(*args)
    elif action == 'statistics':
        statistics(*args)
    elif action == 'tool-status':
        path = tool_path(args[0])
        print(json.dumps(dict(ready=path.is_file() and digest(path) == BINARY_SHA256, path=str(path))))
    elif action == 'install-tool':
        print(install_tool(*args))
    else:
        raise ValueError('unknown compiler cache action')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
