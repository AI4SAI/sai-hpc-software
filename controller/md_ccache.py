#!/usr/bin/env python3
"""Standard ccache with per-build toolchain and include-layout checks.

Ccache checks compilation inputs. Once after CMake configuration, record the
include file names so new headers cannot silently shadow a cached dependency.
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
import time
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
    context = dict(schema=2, target=target, policy='standard-with-layout-v1',
                   tool_sha256=BINARY_SHA256,
                   compilers=compiler_files(os.environ['CC'], os.environ['CXX'], shutil.which('nvcc')),
                   native_flags=subprocess.check_output(
                       [os.environ['CC'], '-march=native', '-mtune=native', '-Q', '--help=target'], text=True),
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
                  stats_log=str(output / 'ccache-stats.log'), ready=False)
    Path(config['temporary']).mkdir(parents=True, exist_ok=True)
    (output / 'ccache-launcher.json').write_text(json.dumps(config, sort_keys=True) + '\n')
    print('LAMMPS_CCACHE_TOOLCHAIN_READY', target, digest(context_file), flush=True)


def cacheable(argv):
    # These forms can hide include paths or inputs from the one-time inventory.
    # Keep the previously unsupported CUDA fatbin/response-file work uncached.
    return '-c' in argv and not any(a.startswith((
        '@', '-Wp,', '-Xpreprocessor', '-include-pch', '-fmodules', '-fplugin',
        '--options-file', '--sysroot', '-isysroot')) for a in argv)


def default_include_dirs():
    """Query actual host compilers once, not nvcc_wrapper for each object."""
    result = set()
    for name, language in (('CC', 'c'), ('CXX', 'c++')):
        probe = subprocess.run([os.environ[name], '-E', '-x', language, '-v', '-'],
                               input='', text=True, capture_output=True, check=True,
                               env=dict(os.environ, LC_ALL='C'))
        begin = '#include <...> search starts here:'
        if begin not in probe.stderr or 'End of search list.' not in probe.stderr:
            raise ValueError('cannot identify compiler include directories')
        block = probe.stderr.split(begin, 1)[1].split('End of search list.', 1)[0]
        result.update(line.strip() for line in block.splitlines() if line.strip())
    result.add(str(Path(shutil.which('nvcc')).resolve().parent.parent / 'include'))
    return result


def include_dirs(commands, defaults=()):
    result = set(defaults)
    options = ('-isystem', '-iquote', '-idirafter', '--include-path', '-I')
    for row in commands:
        argv = row.get('arguments') or shlex.split(row['command'])
        if not cacheable(argv):
            continue
        cwd = Path(row['directory'])
        result.add(str((cwd / row['file']).parent))
        i = 0
        while i < len(argv):
            arg = argv[i]
            for option in options:
                if arg == option:
                    i += 1
                    if i == len(argv):
                        raise ValueError('include option has no directory')
                    value = argv[i]
                elif arg.startswith(option) and len(arg) > len(option):
                    value = arg[len(option):].removeprefix('=')
                else:
                    continue
                for path in value.split(',') if option == '--include-path' else [value]:
                    result.add(str(cwd / path))
                break
            i += 1
    for name in ('CPATH', 'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH'):
        result.update(os.path.abspath(p or '.') for p in os.environ.get(name, '').split(':') if p)
    return result


def include_layout(directories):
    """Hash names and symlink targets, not file contents or timestamps."""
    roots = sorted({os.path.abspath(path) for path in directories})
    pending = [Path(path) for path in roots]
    visited, records = set(), []
    while pending:
        path = pending.pop()
        if path.is_symlink():
            records.append((str(path), 'symlink', os.readlink(path)))
            pending.append(path.resolve())
            continue
        if str(path) in visited:
            continue
        visited.add(str(path))
        if not path.exists():
            records.append((str(path), 'missing'))
        elif path.is_dir():
            records.append((str(path), 'directory'))
            pending.extend(path.iterdir())
        else:
            records.append((str(path), 'file'))
    value = hashlib.sha256(json.dumps(sorted(records), separators=(',', ':')).encode()).hexdigest()
    return dict(roots=roots, entries=len(records), sha256=value)


def finalize(database, output=Path('/runtime')):
    """Enable caching after generated CMake headers and include paths exist."""
    output = Path(output)
    config_path = output / 'ccache-launcher.json'
    config = json.loads(config_path.read_text())
    started = time.monotonic()
    layout = include_layout(include_dirs(json.loads(Path(database).read_text()), default_include_dirs()))
    config.update(ready=True, namespace='standard-layout-v1-' + layout['sha256'])
    config_path.write_text(json.dumps(config, sort_keys=True) + '\n')
    (output / 'ccache-layout.json').write_text(json.dumps(
        dict(layout, seconds=time.monotonic() - started), sort_keys=True, indent=2) + '\n')
    print('LAMMPS_CCACHE_READY', layout['sha256'], layout['entries'], flush=True)


def cache_environment(config, namespace, original=None):
    env = {k: v for k, v in (original if original is not None else os.environ).items()
           if not k.startswith('CCACHE_')}
    env.update(CCACHE_CONFIGPATH='/dev/null', CCACHE_DIR=config['store'],
               CCACHE_TEMPDIR=config['temporary'], CCACHE_STATSLOG=config['stats_log'],
               CCACHE_NAMESPACE=namespace, CCACHE_COMPILERCHECK='content',
               CCACHE_EXTRAFILES=config['context'], CCACHE_SLOPPINESS='',
               CCACHE_DIRECT='true', CCACHE_NODEPEND='true', CCACHE_NOINODECACHE='true',
               CCACHE_MAXSIZE='20GiB', CCACHE_MAXFILES='0', CCACHE_COMPRESS='true')
    return env


def compile_command(config, compiler, argv):
    if not config.get('ready') or not cacheable(argv):
        return subprocess.call([compiler, *argv])
    cached_argv = []
    for arg in argv:
        if Path(compiler).name == 'nvcc_wrapper' and arg in ('-march=native', '-mtune=native'):
            # Preserve the options in ccache's hash and compiler arguments;
            # expanded native CPU settings are checked once in prepare().
            cached_argv.append('--ccache-skip')
        cached_argv.append(arg)
    return subprocess.call([config['tool'], compiler, *cached_argv],
                           env=cache_environment(config, config['namespace']))


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
    elif action == 'finalize':
        finalize(*args)
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
