"""Prepare an exclusively created application clone without overlay work debris.

The sealed source stays read-only. Only the new clone's /work/work entries are
removed; unexpected content or ambiguous tool output fails before publication.
"""
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

WORK = '/work/work'
DEBUGFS = '/usr/sbin/debugfs'
FSCK = '/usr/sbin/e2fsck'
RECEIPT = 'overlay-workdir-preparation.json'
NUMERIC = re.compile(r'[1-9][0-9]{0,19}')
HIDDEN = re.compile(r'\.fuse_hidden[0-9a-f]{16}')
LS_ROW = re.compile(r'/([1-9][0-9]*)/([0-7]{6})/([0-9]+)/([0-9]+)/([^/\x00-\x1f\x7f]+)/([0-9]*)/')
DELETED_ROW = re.compile(r'/0/000000/0/0/[^/\x00-\x1f\x7f]+/0/')


class FileLock(ctypes.Structure):
    _fields_ = [('type', ctypes.c_short), ('whence', ctypes.c_short),
               ('start', ctypes.c_longlong), ('length', ctypes.c_longlong),
               ('pid', ctypes.c_int)]


def lock(fd, writable):
    # OFD locks survive independent open/close and conflict with both OFD and
    # POSIX locks used by the site image drivers. No flock-only fallback.
    request = FileLock(fcntl.F_WRLCK if writable else fcntl.F_RDLCK, os.SEEK_SET, 0, 0, 0)
    fcntl.fcntl(fd, fcntl.F_OFD_SETLK, bytes(request))


def fd_checksum(fd):
    digest = hashlib.sha256()
    offset = 0
    while True:
        data = os.pread(fd, 8 * 1024 * 1024, offset)
        if not data:
            return digest.hexdigest()
        digest.update(data)
        offset += len(data)


def canonical(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError('overlay preparation path is not canonical: ' + str(path))
    return path


def parse_listing(text):
    result = {}
    for line in text.splitlines():
        if line == '':
            continue
        # debugfs prints unused first slots in later directory blocks after
        # unlink. Inode zero is not a live entry; accept only its exact format.
        if DELETED_ROW.fullmatch(line):
            continue
        match = LS_ROW.fullmatch(line)
        if not match:
            raise ValueError('unexpected debugfs directory listing')
        ino, mode, uid, gid, name, size = match.groups()
        if (stat.S_ISDIR(int(mode, 8)) and size != '') or (stat.S_ISREG(int(mode, 8)) and size == ''):
            raise ValueError('unexpected debugfs entry size')
        if name in result:
            raise ValueError('duplicate overlay directory entry')
        result[name] = dict(inode=int(ino), mode=int(mode, 8), uid=int(uid),
                            gid=int(gid), size=int(size) if size else None)
    if not {'.', '..'} <= result.keys():
        raise ValueError('overlay directory listing lacks dot entries')
    if any(not stat.S_ISDIR(result[name]['mode']) for name in ('.', '..')):
        raise ValueError('overlay dot entry is not a directory')
    return result


class Inspector:
    def __init__(self, fd, directory, report):
        self.fd, self.directory, self.report = fd, directory, report
        self.sequence = 0

    def command(self, argv, *, data=None, allowed=(0,)):
        self.sequence += 1
        result = subprocess.run(argv, input=data, capture_output=True, text=True,
                                encoding='utf-8', errors='strict', pass_fds=(self.fd,),
                                env=dict(os.environ, LC_ALL='C', LANG='C'), timeout=600)
        prefix = self.directory / ('%03d' % self.sequence)
        stdout, stderr = prefix.with_suffix('.stdout'), prefix.with_suffix('.stderr')
        stdout.write_text(result.stdout)
        stderr.write_text(result.stderr)
        self.report['commands'].append(dict(argv=argv, returncode=result.returncode,
            stdin=data, stdout=str(stdout), stderr=str(stderr),
            stdout_sha256=hashlib.sha256(result.stdout.encode()).hexdigest(),
            stderr_sha256=hashlib.sha256(result.stderr.encode()).hexdigest()))
        if result.returncode not in allowed:
            raise ValueError('overlay inspection command failed: ' + argv[0])
        return result

    def debugfs(self, commands, *, writable=False):
        argv = [DEBUGFS] + (['-w'] if writable else []) + ['-f', '-', '/proc/self/fd/' + str(self.fd)]
        result = self.command(argv, data='\n'.join(commands) + '\n')
        # debugfs often exits zero on failed commands. Accept only its version
        # banner on stderr, and require one exact echoed command per response.
        if not re.fullmatch(r'debugfs [^\n]+\n?', result.stderr):
            raise ValueError('debugfs reported an error despite its exit status')
        responses = []
        for line in result.stdout.splitlines():
            if line.startswith('debugfs: '):
                index = len(responses)
                if index >= len(commands) or line[len('debugfs: '):].lstrip() != commands[index]:
                    raise ValueError('debugfs command response is ambiguous')
                responses.append([])
            elif responses:
                responses[-1].append(line)
            elif line:
                raise ValueError('unexpected debugfs output')
        if len(responses) != len(commands):
            raise ValueError('missing debugfs command response')
        if writable and any(any(line for line in response) for response in responses):
            raise ValueError('unexpected debugfs mutation output')
        return ['\n'.join(response) for response in responses]

    def listings(self, paths):
        return [parse_listing(text) for text in self.debugfs(['ls -p ' + path for path in paths])]

    def inventory(self):
        root, upper, parent = self.listings(['/', '/upper', '/work'])
        for name, listing in (('upper', upper), ('work', parent)):
            if (name not in root or not stat.S_ISDIR(root[name]['mode']) or
                    listing['.']['inode'] != root[name]['inode'] or
                    listing['..']['inode'] != root['.']['inode']):
                raise ValueError('overlay upper/work roots are redirected')
        if root['upper']['inode'] == root['work']['inode']:
            raise ValueError('overlay upper/work roots are aliased')
        if 'work' not in parent:
            return upper, [], []
        if not stat.S_ISDIR(parent['work']['mode']):
            raise ValueError('overlay work root is not a directory')
        listing = self.listings([WORK])[0]
        if listing['.']['inode'] != parent['work']['inode'] or listing['..']['inode'] != parent['.']['inode']:
            raise ValueError('overlay work root ancestry mismatch')
        directories, files, entries = [], [], []
        for name, item in listing.items():
            if name in ('.', '..'):
                continue
            path = WORK + '/' + name
            if NUMERIC.fullmatch(name) and stat.S_ISDIR(item['mode']):
                directories.append(path)
            elif HIDDEN.fullmatch(name) and stat.S_ISREG(item['mode']):
                files.append(path)
            else:
                raise ValueError('unexpected overlay work entry: ' + path)
            entries.append(dict(path=path, **item))
        if len(entries) > 100000:
            raise ValueError('overlay work inventory exceeds bounded cleanup')
        by_path = {item['path']: item for item in entries}
        for path, contents in zip(directories, self.listings(directories) if directories else []):
            entry = by_path[path]
            if contents['.']['inode'] != entry['inode'] or contents['..']['inode'] != listing['.']['inode']:
                raise ValueError('overlay numeric directory ancestry mismatch')
            if set(contents) - {'.', '..', '.wh..wh..opq'}:
                raise ValueError('unexpected contents in overlay numeric directory')
            if '.wh..wh..opq' in contents:
                item = contents['.wh..wh..opq']
                if not stat.S_ISREG(item['mode']) or item['size'] != 0:
                    raise ValueError('overlay opaque marker is not an empty regular file')
                files.append(path + '/.wh..wh..opq')
                entries.append(dict(path=files[-1], **item))
        if len({item['inode'] for item in entries}) != len(entries):
            raise ValueError('overlay work inode is aliased')
        stats = self.debugfs(['stat ' + item['path'] for item in entries]) if entries else []
        for item, text in zip(entries, stats):
            ino = re.search(r'^Inode: ([0-9]+)\s+Type: (\w+)\s+', text, re.M)
            links = re.search(r'^Links: ([0-9]+)\s+', text, re.M)
            expected_type = 'directory' if stat.S_ISDIR(item['mode']) else 'regular'
            expected_links = 2 if expected_type == 'directory' else 1
            if (not ino or int(ino[1]) != item['inode'] or ino[2] != expected_type or
                    not links or int(links[1]) != expected_links):
                raise ValueError('overlay work inode type or link count is unsafe')
            item['links'] = expected_links
        commands = ['rm ' + path for path in files] + ['rmdir ' + path for path in directories]
        return upper, entries, commands


def prepare_clone(source, target, expected_sha256, results):
    source, target, results = map(canonical, (source, target, results))
    if not re.fullmatch(r'[0-9a-f]{64}', expected_sha256):
        raise ValueError('invalid sealed overlay checksum')
    if target.exists() or target.is_symlink():
        raise ValueError('overlay preparation requires a new clone')
    if not results.is_dir() or (results / RECEIPT).exists():
        raise ValueError('overlay preparation results already exist or are missing')
    directory = results / 'overlay-workdir-preparation'
    directory.mkdir(mode=0o700)
    report = dict(schema=1, status='preparing', source=str(source), target=str(target),
                  source_overlay_sha256=expected_sha256, scope=WORK, commands=[])
    source_fd = target_fd = None
    try:
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_nlink != 1:
            raise ValueError('sealed overlay is not an unaliased regular file')
        if source_stat.st_mode & 0o222:
            raise ValueError('sealed source overlay must have no writable mode bits')
        lock(source_fd, False)
        if fd_checksum(source_fd) != expected_sha256:
            raise ValueError('sealed overlay changed before clone')
        target_fd = os.open(target, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        lock(target_fd, True)
        subprocess.run(['cp', '--sparse=always', '--reflink=auto', '--',
                        '/proc/self/fd/' + str(source_fd), '/proc/self/fd/' + str(target_fd)],
                       pass_fds=(source_fd, target_fd), check=True)
        if fd_checksum(target_fd) != expected_sha256:
            raise ValueError('dependency clone checksum mismatch')
        report['cloned_overlay_sha256'] = expected_sha256
        inspector = Inspector(target_fd, directory, report)
        inspector.command([FSCK, '-fn', '/proc/self/fd/' + str(target_fd)])
        upper, entries, commands = inspector.inventory()
        report.update(removed_entries=entries, cleanup_commands=commands)
        if commands:
            inspector.debugfs(commands, writable=True)
            os.fsync(target_fd)
        upper_after, remaining, unused = inspector.inventory()
        if remaining or unused:
            raise ValueError('overlay work directory is not empty after cleanup')
        if upper_after != upper:
            raise ValueError('overlay upper directory changed during cleanup')
        inspector.command([FSCK, '-fn', '/proc/self/fd/' + str(target_fd)])
        report['prepared_overlay_sha256'] = fd_checksum(target_fd)
        if fd_checksum(source_fd) != expected_sha256:
            raise ValueError('sealed source changed during clone preparation')
        target_stat = os.fstat(target_fd)
        current = target.lstat()
        if ((current.st_ino, current.st_dev) != (target_stat.st_ino, target_stat.st_dev) or
                target_stat.st_nlink != 1 or target_stat.st_size != source_stat.st_size):
            raise ValueError('application clone path changed during preparation')
        if (source.lstat().st_ino, source.lstat().st_dev) != (source_stat.st_ino, source_stat.st_dev):
            raise ValueError('sealed source path changed during preparation')
        report.update(status='prepared', source_unchanged=True, upper_directory_unchanged=True,
                      lock='exclusive-F_OFD_SETLK', fsck_before_and_after='passed',
                      remaining_entries=0)
        return report
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        try:
            with (results / RECEIPT).open('x') as stream:
                json.dump(report, stream, sort_keys=True)
                stream.write('\n')
        finally:
            if target_fd is not None:
                os.close(target_fd)
            if source_fd is not None:
                os.close(source_fd)
