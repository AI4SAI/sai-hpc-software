#!/usr/bin/env python3
"""Run an accepted SIF with the site's host MPI and caller-owned scratch.

This file is deliberately standalone: a public runtime needs neither the build
checkout nor writable access to its image/catalog directory.
"""
import fcntl
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


def file_identity(path):
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and path.resolve() == path,
            'image must be a regular file without symlink ancestors')
    require(not info.st_mode & 0o222, 'accepted image must be read-only')
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def verify_image(image, expected, cache):
    """One full read per image/job/node; other ranks share a locked stat cache."""
    require(re.fullmatch('[0-9a-f]{64}', expected), 'invalid image SHA256')
    before = file_identity(image)
    key = hashlib.sha256((str(image) + expected).encode()).hexdigest()
    lock = cache / (key + '.lock')
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and not info.st_mode & 0o077, 'unsafe image-check cache')
        fcntl.flock(stream, fcntl.LOCK_EX)
        expected_record = {'path': str(image), 'sha256': expected, 'stat': before}
        try:
            cached = json.load(stream)
        except (ValueError, OSError):
            cached = None
        if cached == expected_record:
            return 'cached'
        require(digest(image) == expected, 'SIF checksum differs from accepted image')
        require(file_identity(image) == before, 'SIF changed during verification')
        stream.seek(0)
        json.dump(expected_record, stream, sort_keys=True)
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
    # Do not allow a working directory to cover a SIF application or system tree.
    for path in (work, host_tmp):
        require(str(path).startswith(('/home/', '/org/')) and path.resolve() == path,
                'working directory and TMPDIR must be real home/project directories')
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
    # All disk-backed MPI session files and container scratch still use TMPDIR.
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
    manifest_path, software, program, *arguments = sys.argv[1:] if argv is None else argv
    manifest_path = Path(manifest_path).resolve(strict=True)
    manifest = json.loads(manifest_path.read_text())
    environment = dict(os.environ)
    _, row = selection(manifest, software, program, environment)
    job = environment.get('SLURM_JOB_ID', '')
    require(re.fullmatch('[0-9]+', job), 'SIF programs require a Slurm allocation')
    host_tmp = bind_path(environment.get('TMPDIR', ''))
    require(str(host_tmp).startswith(('/home/', '/org/')) and host_tmp.is_dir()
            and host_tmp.resolve() == host_tmp, 'set TMPDIR to an existing home/project job directory before mpirun')
    cache = private_directory(host_tmp / ('sai-sif-' + str(os.getuid())))
    node = socket.gethostname().split('.')[0]
    require(re.fullmatch('[A-Za-z0-9_.-]+', node), 'invalid node name')
    restart = environment.get('SLURM_RESTART_COUNT', '0')
    require(re.fullmatch('[0-9]+', restart), 'invalid Slurm restart count')
    cache = private_directory(cache / (job + '-' + restart + '-' + node))
    private_directory(cache / 'config')
    checked = verify_image(Path(row['image']), row['sha256'], cache)
    rank = environment.get('OMPI_COMM_WORLD_RANK', environment.get('PMIX_RANK', 'serial'))
    require(re.fullmatch('[0-9]+|serial', rank), 'invalid MPI rank')
    scratch = private_directory(cache / ('rank-' + rank))
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
        (trace / f'{software}-{node}-{rank}-{os.getpid()}.json').write_text(json.dumps(record, indent=2) + '\n')
    os.execve(args[0], args, child)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError) as error:
        sys.exit('SIF runtime: ' + str(error))
