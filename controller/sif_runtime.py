#!/usr/bin/env python3
"""Run an accepted SIF with host MPI and private node-memory scratch.

This file is deliberately standalone: a public runtime needs neither the build
checkout nor writable access to its image/catalog directory.
"""
import fcntl
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import sys


PROGRAMS = {'abacus': ('abacus',), 'cp2k': ('cp2k', 'cp2k.psmp'),
            'lammps': ('lmp',), 'deepmd-kit': ('dp', 'deepmd-python'),
            'gpumd': ('gpumd', 'nep', 'gnep')}
MPI_PROGRAMS = ('abacus', 'cp2k', 'lammps')
MPI_LAUNCHERS = ('mpirun', 'mpiexec', 'prted')
ENV_PREFIXES = ('SLURM_', 'OMPI_', 'OPAL_', 'PMIX_', 'PMI_', 'PRTE_', 'UCX_',
                'NCCL_', 'CUSOLVERMP_', 'CUDA_', 'FI_', 'OMP_', 'DP_', 'TF_')
ENV_NAMES = ('NVIDIA_VISIBLE_DEVICES', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def bind_path(value):
    path = Path(value)
    require(path.is_absolute() and not any(c in str(path) for c in ':,\n\r'),
            'bind paths must be absolute without commas, colons or newlines')
    return path


def private_directory(path):
    """Mode 0700 also masks named users in an inherited project access ACL."""
    path = Path(path)
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and not info.st_mode & 0o077 and path.resolve() == path,
            'runtime cache must be a private directory owned by this user')
    return path


def memory_root(environment):
    job = environment.get('SLURM_JOB_ID', '')
    restart = environment.get('SLURM_RESTART_COUNT', '0')
    require(re.fullmatch('[0-9]+', job) and re.fullmatch('[0-9]+', restart),
            'SIF programs require a Slurm allocation')
    return Path(f'/dev/shm/sai-{os.getuid()}-{job}-{restart}')


def memory_scope(environment):
    root = memory_root(environment)
    marker = root / 'scope.json'
    try:
        info = marker.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and not info.st_mode & 0o077 and marker.resolve() == marker,
                'unsafe memory namespace marker')
        record = json.loads(marker.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if record != dict(schema=1, job=environment['SLURM_JOB_ID'],
                      mount=os.readlink('/proc/self/ns/mnt'),
                      user=os.readlink('/proc/self/ns/user')):
        return None
    return record


def memory_environment(environment):
    environment = dict(environment)
    root = memory_root(environment)
    private_directory(root / 'mpi')
    environment.pop('PRTE_MCA_prte_local_tmpdir_base', None)
    environment.pop('PRTE_MCA_prte_remote_tmpdir_base', None)
    environment.update(TMPDIR=str(root), TMP=str(root), TEMP=str(root),
                       PRTE_MCA_prte_tmpdir_base=str(root / 'mpi'))
    return environment


def enter_memory(arguments):
    """Called only after unshare; no mounts or elevated capabilities escape it."""
    old_mount, old_user, *original = arguments
    require(old_mount != os.readlink('/proc/self/ns/mnt')
            and old_user != os.readlink('/proc/self/ns/user'),
            'memory setup requires new mount and user namespaces')
    uid_map = Path('/proc/self/uid_map').read_text().split()
    require(uid_map == [str(os.getuid()), str(os.getuid()), '1'],
            'memory setup must preserve the caller UID')
    libc = ctypes.CDLL(None, use_errno=True)

    def mount(source, destination, filesystem, flags, data=None):
        result = libc.mount(os.fsencode(source), os.fsencode(destination),
                            filesystem, ctypes.c_ulong(flags), data)
        if result:
            raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()), str(destination))

    # The enclosing mount namespace has private propagation. The site epilog
    # sees the original /dev/shm, never these job-local objects. Kernel teardown
    # frees the mounts even when Slurm kills the whole process tree.
    mount('sai-sif-memory', '/dev/shm', b'tmpfs', 2 | 4, b'mode=1777')
    root = private_directory(memory_root(os.environ))
    for destination, name in (('/tmp', 'tmp'), ('/var/tmp', 'var-tmp')):
        source = private_directory(root / name)
        mount(source, destination, None, 4096)  # MS_BIND
    marker = root / 'scope.json'
    with marker.open('x') as stream:
        json.dump(dict(schema=1, job=os.environ['SLURM_JOB_ID'],
                       mount=os.readlink('/proc/self/ns/mnt'),
                       user=os.readlink('/proc/self/ns/user')), stream)
    marker.chmod(0o400)
    # --keep-caps is needed only to mount inside this unprivileged namespace.
    # Drop ambient, inheritable, permitted and effective capabilities before
    # executing either the real host MPI or Apptainer.
    require(libc.prctl(47, 4, 0, 0, 0) == 0, 'cannot clear ambient capabilities')
    header = (ctypes.c_uint32 * 2)(0x20080522, 0)
    data = (ctypes.c_uint32 * 6)()
    require(libc.capset(header, data) == 0, 'cannot drop namespace capabilities')
    command = ['/usr/bin/python3', '-I', '-B', str(Path(__file__).resolve()), *original]
    os.execve(command[0], command, memory_environment(os.environ))


def unshare_memory(arguments, environment):
    command = ['/usr/bin/unshare', '--user', '--map-current-user', '--keep-caps',
               '--mount', '--propagation', 'private', '/usr/bin/python3', '-I', '-B',
               str(Path(__file__).resolve()), '--memory', os.readlink('/proc/self/ns/mnt'),
               os.readlink('/proc/self/ns/user'), *arguments]
    os.execve(command[0], command, environment)


def apparmor_restricts_userns():
    policy = Path('/proc/sys/kernel/apparmor_restrict_unprivileged_userns')
    return policy.exists() and policy.read_text().strip() == '1'


def bootstrap_memory(arguments, environment):
    """Use the site's supported Apptainer profile on restricted kernels.

    The tiny image contains only mountpoints. MPI, Python and libraries are
    the physical host files, not another application's runtime dependency.
    """
    manifest = json.loads(Path(arguments[0]).read_text())
    bootstrap = manifest['memory_bootstrap']
    image = bind_path(bootstrap['image'])
    require(file_identity(image) and digest(image) == bootstrap['sha256'],
            'memory bootstrap differs from the accepted image')
    apptainer = shutil.which('apptainer', path=environment.get('PATH'))
    require(apptainer, 'load apptainer/1.4.4 before running a SIF command')
    # /run/lock is a small tmpfs unaffected by the site's per-user epilog.
    # Only bootstrap configuration lives here; application scratch never does.
    cache = node_cache()
    for name in ('bootstrap-config', 'bootstrap-cache', 'bootstrap-tmp'):
        private_directory(cache / name)
    child = {k: v for k, v in environment.items()
             if not k.startswith(('APPTAINER', 'SINGULARITY'))}
    child.update(APPTAINER_CONFIGDIR=str(cache / 'bootstrap-config'),
                 APPTAINER_CACHEDIR=str(cache / 'bootstrap-cache'),
                 APPTAINER_TMPDIR=str(cache / 'bootstrap-tmp'))
    for key in ('PATH', 'LD_LIBRARY_PATH', 'LD_PRELOAD'):
        if key in environment:
            child['APPTAINERENV_' + key] = environment[key]
    command = [apptainer, 'exec', '--userns', '--contain', '--no-home', '--no-eval',
               '--no-mount', 'bind-paths,home,cwd', '--pwd', str(Path.cwd())]
    # Keep the host MPI's RDMA and GPU devices while retaining private shm.
    if environment.get('SLURM_JOB_PARTITION') != 'DSPRHBM':
        command.append('--nv')
    for name in ('/usr', '/lib', '/lib64', '/etc', '/opt', '/org', '/home', '/run',
                 '/dev/fuse', '/dev/infiniband', '/var/lib/sss/pipes/nss', '/var/run/nscd'):
        if Path(name).exists():
            command += ['--bind', name]
    command += [str(image), '/usr/bin/python3', '-I', '-B', str(Path(__file__).resolve()),
                '--bootstrap', os.readlink('/proc/self/ns/mnt'),
                os.readlink('/proc/self/ns/user'),
                json.dumps({k: environment.get(k, '') for k in ('PATH', 'LD_LIBRARY_PATH', 'LD_PRELOAD')}),
                *arguments]
    os.execve(command[0], command, child)


def ensure_memory(arguments, environment):
    if memory_scope(environment) is None:
        require('OMPI_COMM_WORLD_RANK' not in environment,
                'use the mpirun/mpiexec entry supplied by the SIF module')
        if apparmor_restricts_userns():
            bootstrap_memory(arguments, environment)
        else:
            unshare_memory(arguments, environment)
    return memory_environment(environment)


def node_cache():
    """A bounded checksum index in node RAM; never put software/cache data here."""
    base = Path('/run/lock')
    info = ctypes.create_string_buffer(256)
    libc = ctypes.CDLL(None, use_errno=True)
    require(libc.statfs(os.fsencode(base), info) == 0
            and ctypes.c_long.from_buffer(info).value == 0x01021994,
            'the node checksum cache requires tmpfs at /run/lock')
    return private_directory(base / ('sai-sif-' + str(os.getuid())))


def file_identity(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and path.resolve() == path,
            'image must be a regular file without symlink ancestors')
    require(not info.st_mode & 0o222, 'accepted image must be read-only')
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def verify_image(image, expected, cache, *, job=''):
    """One full read per image/job/node; other ranks share a locked stat cache."""
    require(re.fullmatch('[0-9a-f]{64}', expected), 'invalid image SHA256')
    before = file_identity(image)
    key = hashlib.sha256((job + str(image) + expected).encode()).hexdigest()
    lock = cache / 'images.json'
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and not info.st_mode & 0o077, 'unsafe image-check cache')
        fcntl.flock(stream, fcntl.LOCK_EX)
        expected_record = {'path': str(image), 'sha256': expected, 'stat': before}
        try:
            records = json.load(stream)
        except (ValueError, OSError):
            records = {}
        if not isinstance(records, dict):
            records = {}
        if records.get(key) == expected_record:
            return 'cached'
        require(digest(image) == expected, 'SIF checksum differs from accepted image')
        require(file_identity(image) == before, 'SIF changed during verification')
        records.pop(key, None)
        records[key] = expected_record
        # One small file per UID/node, bounded across jobs and source updates.
        records = dict(list(records.items())[-64:])
        stream.seek(0)
        json.dump(records, stream)
        stream.truncate()
        stream.flush()
        return 'hashed'


def selection(manifest, software, program, environment):
    require(manifest.get('schema') == 1 and software in PROGRAMS
            and program in PROGRAMS[software], 'unknown SIF runtime command')
    app = manifest['applications'][software]
    partition = environment.get('SLURM_JOB_PARTITION')
    require(partition in app['partitions'], 'load/run this module in a supported Slurm partition')
    row = app['partitions'][partition]
    require(row['identity']['software'] == software
            and row['identity']['partition'] == partition,
            'SIF identity differs from selected software/partition')
    expected_mpi = row.get('mpi_prefix')
    if expected_mpi:
        require(environment.get('MPI_HOME') == expected_mpi,
                'host MPI differs from the accepted ABI; load the SIF module inside this allocation')
    return app, row


def command(manifest, manifest_path, software, program, arguments, environment, work, scratch):
    """Return exec argv/env; no nested mpirun, extraction, overlay or fakeroot."""
    _, row = selection(manifest, software, program, environment)
    image = bind_path(row['image'])
    prefix = row['identity']['install_prefix']
    executable = {'cp2k': 'cp2k.psmp', 'deepmd-python': 'python'}.get(program, program)
    runtime_env = row.get('environment_file', prefix + '/share/sai/runtime-env.sh')
    work, scratch = bind_path(work), bind_path(scratch)
    host_tmp = bind_path(environment['TMPDIR'])
    # Work stays on the user's project; all runtime state is namespace-local RAM.
    require(str(work).startswith(('/home/', '/org/')) and work.resolve() == work,
            'working directory must be a real home/project directory')
    require(host_tmp == memory_root(environment) and host_tmp.resolve() == host_tmp,
            'runtime TMPDIR must be the private memory directory')
    apptainer = shutil.which('apptainer', path=environment.get('PATH'))
    require(apptainer, 'load apptainer/1.4.4 before running a SIF command')
    argv = [apptainer, 'exec', '--cleanenv', '--no-eval', '--no-home',
            '--no-mount', 'bind-paths,home,cwd,tmp,hostfs', '--pwd', str(work)]
    if software in MPI_PROGRAMS and 'OMPI_COMM_WORLD_RANK' in environment:
        # This process execs all the way from PRRTE to Apptainer, preserving
        # its parent PID. Sibling ranks on each node therefore share the
        # unprivileged user namespace and can use UCX POSIX/CMA transports.
        argv.append('--sharens')
    if row['identity']['gpus']:
        argv.append('--nv')
    binds = [(p, p, 'ro') for p in ('/usr', '/lib', '/lib64', '/opt/devtools', *row['binds'])]
    binds += [(str(work), str(work), 'rw'), (str(host_tmp), str(host_tmp), 'rw'),
              (str(scratch), '/tmp', 'rw'), (str(scratch), '/var/tmp', 'rw'),
              ('/dev/shm', '/dev/shm', 'rw')]
    if 'environment_file' in row:
        source = Path(manifest_path).parent / runtime_env
        require(source.resolve() == source and digest(source) == row['environment_sha256'],
                'accepted runtime environment changed')
        runtime_env = '/sai-runtime-env.sh'
        binds.append((str(source), runtime_env, 'ro'))
    topology = environment.get('NCCL_TOPO_FILE')
    if topology:
        require(Path(topology).is_file(), 'missing NCCL topology file')
        bind_path(topology)
        binds.append((topology, topology, 'ro'))
    for source, destination, mode in binds:
        bind_path(source)
        bind_path(destination)
        require(source != '/opt/apps' and not Path(prefix).is_relative_to(source),
                'bind would hide the application stored in SIF')
        argv += ['--bind', f'{source}:{destination}:{mode}']
    # The host launcher has already established MPI placement and transports.
    # APPTAINERENV + --no-eval preserves comma-separated CUDA IDs/UCX settings
    # and literal shell metacharacters, unlike Apptainer's --env comma map.
    child = {k: v for k, v in environment.items()
             if not k.startswith(('APPTAINER', 'SINGULARITY'))}
    forwarded = {k: v for k, v in environment.items()
                 if k.startswith(ENV_PREFIXES) or k in ENV_NAMES}
    forwarded.update(TMPDIR=str(host_tmp), TMP=str(scratch), TEMP=str(scratch),
                     XDG_CACHE_HOME=str(scratch / 'cache'), CUDA_CACHE_PATH=str(scratch / 'cuda'),
                     PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
    # Communication buffers belong in RAM, not in project Lustre files.
    # MPI session files and container scratch also live in private tmpfs.
    forwarded.setdefault('OMPI_MCA_shmem_mmap_backing_file_base_dir', '/dev/shm')
    forwarded.setdefault('OMPI_MCA_shmem_mmap_relocate_backing_file', '1')
    for key, value in forwarded.items():
        child['APPTAINERENV_' + key] = value
    child.update(APPTAINER_TMPDIR=str(scratch), APPTAINER_CACHEDIR=str(scratch / 'cache'),
                 APPTAINER_CONFIGDIR=str(scratch.parent / 'config'))
    argv += [str(image), '/usr/bin/bash', '--noprofile', '--norc', '-c',
             'set -eo pipefail; driver=${LD_LIBRARY_PATH:-}; source "$1"; shift; '
             'export LD_LIBRARY_PATH="$driver${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"; exec "$@"',
             'bash', runtime_env, prefix + '/bin/' + executable, *arguments]
    return argv, child


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[0] == '--bootstrap':
        old_mount, old_user, preserved, *original = argv[1:]
        require(old_mount != os.readlink('/proc/self/ns/mnt')
                and old_user != os.readlink('/proc/self/ns/user'),
                'Apptainer bootstrap did not create private namespaces')
        restored = json.loads(preserved)
        require(set(restored) == {'PATH', 'LD_LIBRARY_PATH', 'LD_PRELOAD'}
                and all(isinstance(value, str) for value in restored.values()),
                'invalid host launcher environment')
        return unshare_memory(original, dict(os.environ, **restored))
    if argv[0] == '--memory':
        return enter_memory(argv[1:])
    manifest_path, software, program, *arguments = argv
    manifest_path = Path(manifest_path).resolve(strict=True)
    manifest = json.loads(manifest_path.read_text())
    environment = dict(os.environ)
    if software == '_mpi':
        require(program in MPI_LAUNCHERS, 'unknown host MPI launcher')
        prefixes = {row.get('mpi_prefix') for app in manifest['applications'].values()
                    for partition, row in app['partitions'].items()
                    if partition == environment.get('SLURM_JOB_PARTITION') and row.get('mpi_prefix')}
        require(environment.get('MPI_HOME') in prefixes, 'host MPI differs from the accepted ABI')
        environment = ensure_memory(argv, environment)
        environment['PRTE_MCA_prte_launch_agent'] = str(manifest_path.parent / 'bin/mpi/prted')
        executable = Path(environment['MPI_HOME']) / 'bin' / program
        os.execve(str(executable), [str(executable), *arguments], environment)
    _, row = selection(manifest, software, program, environment)
    environment = ensure_memory(argv, environment)
    job = environment.get('SLURM_JOB_ID', '')
    require(re.fullmatch('[0-9]+', job), 'SIF programs require a Slurm allocation')
    host_tmp = memory_root(environment)
    cache = private_directory(host_tmp / 'runtime')
    node = socket.gethostname().split('.')[0]
    require(re.fullmatch('[A-Za-z0-9_.-]+', node), 'invalid node name')
    restart = environment.get('SLURM_RESTART_COUNT', '0')
    require(re.fullmatch('[0-9]+', restart), 'invalid Slurm restart count')
    private_directory(cache / 'config')
    checked = verify_image(Path(row['image']), row['sha256'], node_cache(), job=job + '-' + restart)
    rank = environment.get('OMPI_COMM_WORLD_RANK', environment.get('PMIX_RANK', 'serial'))
    require(re.fullmatch('[0-9]+|serial', rank), 'invalid MPI rank')
    scratch = private_directory(cache / ('rank-' + rank + '-' + str(os.getpid())))
    for directory in ('cache', 'cuda'):
        private_directory(scratch / directory)
    args, child = command(manifest, manifest_path, software, program, arguments,
                          environment, Path.cwd(), scratch)
    if environment.get('SAI_SIF_TRACE_DIR'):
        trace = bind_path(environment['SAI_SIF_TRACE_DIR'])
        require(trace.is_dir() and trace.resolve() == trace, 'invalid runtime trace directory')
        record = dict(software=software, program=program, rank=rank, node=node,
                      image=row['image'], sha256=row['sha256'], verification=checked,
                      mpi=environment.get('MPI_HOME'), affinity=sorted(os.sched_getaffinity(0)),
                      cuda_visible_devices=environment.get('CUDA_VISIBLE_DEVICES'),
                      command=args, job=job)
        record.update(memory_scope=memory_scope(environment), memory_root=str(host_tmp), uid=os.getuid())
        (trace / f'{software}-{node}-{rank}-{os.getpid()}.json').write_text(json.dumps(record, indent=2) + '\n')
    os.execve(args[0], args, child)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        sys.exit('SIF runtime: ' + str(error))
