#!/usr/bin/env python3
"""Exercise a private fuse2fs candidate only on a fresh home-backed overlay."""
import argparse
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import subprocess
import time
import traceback


PROJECT = Path('/home/stardust/sai-hpc-software')
APPTAINER = Path('/opt/devtools/apptainer/1.4.4/bin/apptainer')
CONFIG = Path('/opt/devtools/apptainer/1.4.4/etc/apptainer/apptainer.conf')
BASE_IMAGE = PROJECT / 'containers/base/minimal-v1.sif'
STRACE = Path('/usr/bin/strace')


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def complete_trace_lines(lines):
    """Join strace's interrupted syscalls before counting their return values."""
    pending = {}
    for line in lines:
        match = re.match(r'\s*(?:\[pid\s+(\d+)\]|(\d+))\s+', line)
        pid = (match.group(1) or match.group(2)) if match else 'main'
        if '<unfinished ...>' in line:
            pending[pid] = line.split('<unfinished ...>', 1)[0]
            continue
        resumed = re.search(r'<\.\.\.\s+\w+\s+resumed>', line)
        if resumed:
            line = pending.pop(pid, '') + line[resumed.end():]
        yield line


def stop(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)


class Probe:
    def __init__(self, base):
        self.base = base
        self.runtime = base / 'runtime'
        self.results = base / 'results'
        self.overlay = base / 'work.ext3'
        self.candidate = base / 'driver/fuse2fs'
        self.source = Path(__file__).resolve().with_name('workload.py')
        self.record = {'schema_version': 1, 'status': 'running', 'base': str(base),
                       'node': socket.gethostname(), 'start_epoch': time.time(),
                       'slurm_job_id': os.environ.get('SLURM_JOB_ID'), 'cases': []}
        for path in (self.runtime, self.results, base / 'cases'):
            path.mkdir(mode=0o700, exist_ok=True)
        for name in ('host', 'cache', 'xdg', 'container'):
            (self.runtime / name).mkdir(mode=0o700, exist_ok=True)
        self.env = dict(os.environ, TMPDIR=str(self.runtime / 'host'),
                        TMP=str(self.runtime / 'host'), TEMP=str(self.runtime / 'host'),
                        APPTAINER_TMPDIR=str(self.runtime / 'host'),
                        SINGULARITY_TMPDIR=str(self.runtime / 'host'),
                        APPTAINER_CACHEDIR=str(self.runtime / 'cache'),
                        SINGULARITY_CACHEDIR=str(self.runtime / 'cache'),
                        XDG_RUNTIME_DIR=str(self.runtime / 'xdg'),
                        PYTHONDONTWRITEBYTECODE='1')
        self.save()

    def save(self):
        temporary = self.runtime / 'result.json.pending'
        temporary.write_text(json.dumps(self.record, indent=2) + '\n')
        temporary.replace(self.base / 'result.json')

    def case(self, label):
        directory = self.base / 'cases' / label
        directory.mkdir(mode=0o700)
        record = {'label': label, 'start_epoch': time.time(), 'directory': str(directory)}
        self.record['cases'].append(record)
        self.save()
        return directory, record

    def command(self, argv, directory, record, expected=0, timeout=240):
        record['argv'] = [str(arg) for arg in argv]
        record['expected_returncode'] = expected
        self.save()
        started = time.monotonic()
        with (directory / 'command.log').open('wb') as log:
            process = subprocess.Popen(record['argv'], stdout=log, stderr=subprocess.STDOUT,
                                       env=self.env, start_new_session=True)
            try:
                record['returncode'] = process.wait(timeout=timeout)
            except BaseException:
                stop(process)
                record['returncode'] = process.returncode
                raise
            finally:
                record['wall_seconds'] = time.monotonic() - started
                self.save()
        if expected is not None and record['returncode'] != expected:
            raise RuntimeError('%s exited %s, expected %s; see %s' %
                               (record['label'], record['returncode'], expected,
                                directory / 'command.log'))

    def container(self, directory, readonly, payload):
        binary = directory / 'bin'
        binary.mkdir(mode=0o700)
        wrapper = binary / 'fuse2fs'
        trace = directory / 'driver.trace'
        traced = [str(STRACE), '-f', '-qq', '-ttt', '-T', '-yy', '-s', '256',
                  '-e', 'trace=openat,pwrite64,write,fsync,fdatasync,fcntl,flock',
                  '-o', str(trace), str(self.candidate)]
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.join(traced) + ' "$@"\n')
        wrapper.chmod(0o700)
        config = directory / 'apptainer.conf'
        config.write_text(CONFIG.read_text() + '\nbinary path = ' + str(binary) + ':' +
                          str(self.candidate.parent) + ':$PATH:/usr/bin:/bin:/usr/sbin:/sbin\n')
        argv = [str(APPTAINER), '--debug', '--config', str(config), 'exec', '--fakeroot',
                '--cleanenv', '--containall', '--no-home', '--no-mount',
                'bind-paths,home,cwd,tmp,hostfs', '--pwd', '/', '--net', '--network', 'none']
        for path in ('/usr', '/sbin', '/lib', '/lib64', '/opt/devtools'):
            argv += ['--bind', path + ':' + path + ':ro']
        argv += ['--bind', str(self.source.parent) + ':/probe-input:ro',
                 '--bind', str(self.results) + ':/results:rw',
                 '--bind', str(self.runtime / 'container') + ':/runtime:rw',
                 '--env', 'TMPDIR=/runtime,TMP=/runtime,TEMP=/runtime,PYTHONDONTWRITEBYTECODE=1',
                 '--overlay', str(self.overlay) + (':ro' if readonly else ''), str(BASE_IMAGE)]
        return argv + payload

    def workload(self, mode, label=None):
        args = ['/usr/bin/python3', '-B', '/probe-input/workload.py', mode]
        return args + ([label] if label else [])

    def trace_summary(self, directory, record, readonly):
        trace = directory / 'driver.trace'
        if not trace.is_file() or not trace.stat().st_size:
            raise RuntimeError(record['label'] + ': candidate driver trace is missing')
        lines = trace.read_text(errors='replace').splitlines()
        successful = re.compile(r'\)\s+=\s+([0-9]+)(?:\b|<)')
        summary = {'successful_fsync': 0, 'successful_fdatasync': 0,
                   'readonly_inherited_fd_opens': 0, 'image_successful_writes': 0,
                   'image_bytes_written': 0}
        for line in complete_trace_lines(lines):
            match = successful.search(line)
            if match is None:
                continue
            returned = int(match.group(1))
            if returned == 0 and re.search(r'\bfsync\(', line):
                summary['successful_fsync'] += 1
            if returned == 0 and re.search(r'\bfdatasync\(', line):
                summary['successful_fdatasync'] += 1
            if ('openat(' in line and '"/proc/self/fd/' in line and
                    'O_RDONLY' in line):
                summary['readonly_inherited_fd_opens'] += 1
            if (returned > 0 and re.search(r'\b(?:pwrite64|write)\(', line) and
                    str(self.overlay) in line):
                summary['image_successful_writes'] += 1
                summary['image_bytes_written'] += returned
        record['trace'] = summary
        self.save()
        if readonly and summary['readonly_inherited_fd_opens'] < 1:
            raise RuntimeError(record['label'] + ': no successful O_RDONLY inherited-FD open in trace')
        if readonly and summary['image_successful_writes']:
            raise RuntimeError(record['label'] + ': trace contains successful image writes')
        if record['label'] == 'rw-workload' and summary['successful_fsync'] < 1:
            raise RuntimeError('explicit workload fsync is absent from the driver trace')

    def mount_case(self, label, readonly, payload, expected=0):
        directory, record = self.case(label)
        record.update(readonly=readonly, image_mode=oct(self.overlay.stat().st_mode & 0o777),
                      sha256_before=sha256(self.overlay))
        argv = self.container(directory, readonly, payload)
        try:
            self.command(argv, directory, record, expected=expected)
        finally:
            record['sha256_after'] = sha256(self.overlay)
            record['hash_unchanged'] = record['sha256_before'] == record['sha256_after']
            self.save()
        if readonly and not record['hash_unchanged']:
            raise RuntimeError(label + ': read-only mount changed the full image SHA256')
        self.trace_summary(directory, record, readonly)
        record['passed'] = True
        self.save()

    def fsck(self, label):
        directory, record = self.case(label)
        record['sha256_before'] = sha256(self.overlay)
        try:
            self.command(['/usr/sbin/e2fsck', '-fn', str(self.overlay)], directory, record)
        finally:
            record['sha256_after'] = sha256(self.overlay)
            record['hash_unchanged'] = record['sha256_before'] == record['sha256_after']
            self.save()
        if not record['hash_unchanged']:
            raise RuntimeError(label + ': e2fsck -fn changed the image')
        record['passed'] = True
        self.save()

    def exclusion(self, readonly):
        label = ('ro' if readonly else 'rw') + '-holder'
        directory, record = self.case(label)
        record.update(readonly=readonly, image_mode=oct(self.overlay.stat().st_mode & 0o777),
                      sha256_before=sha256(self.overlay))
        argv = self.container(directory, readonly, self.workload('hold', label))
        record['argv'] = argv
        self.save()
        started = time.monotonic()
        with (directory / 'command.log').open('wb') as log:
            holder = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT,
                                      env=self.env, start_new_session=True)
            try:
                deadline = time.monotonic() + 30
                while not (self.results / (label + '-ready')).exists():
                    if holder.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(label + ': holder did not become ready')
                    time.sleep(0.1)
                record['sha256_while_held'] = sha256(self.overlay)
                if readonly and record['sha256_while_held'] != record['sha256_before']:
                    raise RuntimeError(label + ': image changed before contenders ran')
                with self.overlay.open('r+b') as image_file:
                    try:
                        fcntl.lockf(image_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError as error:
                        if error.errno not in (errno.EACCES, errno.EAGAIN):
                            raise
                        record['posix_writer_lock_rejected'] = True
                        record['posix_writer_errno'] = error.errno
                        (directory / 'posix-writer.log').write_text(
                            'F_SETLK exclusive lock rejected: %s\n' % error)
                    else:
                        fcntl.lockf(image_file.fileno(), fcntl.LOCK_UN)
                        raise RuntimeError(label + ': concurrent POSIX writer lock succeeded')
                contender_dir, contender = self.case(label + '-rw-contender')
                contender['sha256_before'] = sha256(self.overlay)
                contender_argv = self.container(contender_dir, False,
                                                self.workload('verify', label + '-contender'))
                try:
                    self.command(contender_argv, contender_dir, contender, expected=None, timeout=15)
                finally:
                    contender['sha256_after'] = sha256(self.overlay)
                    contender['hash_unchanged'] = (contender['sha256_before'] ==
                                                   contender['sha256_after'])
                    self.save()
                message = (contender_dir / 'command.log').read_text(errors='replace')
                if contender['returncode'] == 0 or 'currently in use' not in message.lower():
                    raise RuntimeError(label + ': second writable mount was not rejected by image lock')
                if readonly and not contender['hash_unchanged']:
                    raise RuntimeError(label + ': rejected writable mount changed RO-held image')
                contender['passed'] = True
                record['second_rw_mount_rejected'] = True
                if holder.poll() is not None:
                    raise RuntimeError(label + ': holder exited before explicit release')
                self.save()
            finally:
                (self.results / (label + '-release')).write_text('release\n')
                try:
                    record['returncode'] = holder.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    stop(holder)
                    record['returncode'] = holder.returncode
                    raise RuntimeError(label + ': holder did not unmount after release')
                finally:
                    record['wall_seconds'] = time.monotonic() - started
                    record['sha256_after'] = sha256(self.overlay)
                    record['hash_unchanged'] = (record['sha256_before'] == record['sha256_after'])
                    self.save()
        if record['returncode'] != 0:
            raise RuntimeError(label + ': holder failed on exit')
        if readonly and not record['hash_unchanged']:
            raise RuntimeError(label + ': read-only holder changed the image')
        self.trace_summary(directory, record, readonly)
        with self.overlay.open('r+b') as image_file:
            fcntl.lockf(image_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.lockf(image_file.fileno(), fcntl.LOCK_UN)
        record['lock_released_after_unmount'] = True
        record['passed'] = True
        self.save()

    def execute(self):
        for path in (self.candidate, self.source, APPTAINER, BASE_IMAGE, CONFIG, STRACE):
            if not path.is_file():
                raise RuntimeError('required input missing: ' + str(path))
        if not os.access(self.candidate, os.X_OK):
            raise RuntimeError('candidate is not executable')
        if self.overlay.exists():
            raise RuntimeError('refusing to reuse an existing overlay')
        if self.base not in self.source.parents:
            raise RuntimeError('deploy probe.py and workload.py below the probe base')
        self.record.update(driver=str(self.candidate), driver_sha256=sha256(self.candidate),
                           probe_sha256=sha256(Path(__file__).resolve()),
                           workload_sha256=sha256(self.source), base_image=str(BASE_IMAGE),
                           overlay=str(self.overlay), overlay_size_mib=256,
                           expected_workload_files=161)
        self.save()
        directory, record = self.case('create')
        self.command([str(APPTAINER), 'overlay', 'create', '--sparse', '--size', '256',
                      '--fakeroot', str(self.overlay)], directory, record)
        self.overlay.chmod(0o600)
        record['sha256_after'] = sha256(self.overlay)
        record['passed'] = True
        self.save()
        self.mount_case('rw-workload', False, self.workload('run'))
        self.fsck('fsck-after-workload')
        self.record['sealed_baseline_sha256'] = sha256(self.overlay)
        self.save()
        # Test RO intent even when DAC allows writes, then test sealed permissions.
        self.mount_case('ro-verify-1', True, self.workload('verify', 'ro-verify-1'))
        self.overlay.chmod(0o400)
        self.mount_case('ro-verify-2-sealed', True, self.workload('verify', 'ro-verify-2-sealed'))
        self.mount_case('ro-false-sealed', True, ['/bin/false'], expected=1)
        # Mode 0600 ensures rejection proves locking, rather than a DAC failure.
        self.overlay.chmod(0o600)
        self.exclusion(True)
        self.exclusion(False)
        self.fsck('fsck-final')
        self.mount_case('ro-final-verify', True, self.workload('verify', 'ro-final-verify'))
        self.record.update(status='passed', finished_epoch=time.time(),
                           final_sha256=sha256(self.overlay))
        self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path, help='fresh diagnostic directory under project home')
    base = parser.parse_args().base.resolve()
    if PROJECT / 'diagnostics' not in base.parents:
        parser.error('base must be under ' + str(PROJECT / 'diagnostics'))
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (base / 'result.json').exists():
        parser.error('result.json already exists; use a new diagnostic directory')
    probe = Probe(base)
    try:
        probe.execute()
    except BaseException as error:
        probe.record.update(status='failed', error=str(error),
                            traceback=traceback.format_exc(), finished_epoch=time.time())
        probe.save()
        print(json.dumps(probe.record), flush=True)
        return 1
    print(json.dumps(probe.record), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
