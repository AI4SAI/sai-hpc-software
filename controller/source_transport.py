"""Runner-side source transfer with a remote lease and resumable verified parts."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from source_cache import complete, git, pack, select_base, sha


class SourceNetworkError(RuntimeError):
    """Source identity is retained while a transient network failure is retried."""


def network_error(message):
    message = message.lower()
    return any(marker in message for marker in (
        'could not resolve host', 'could not resolve proxy', 'failed to connect to',
        'could not connect to server', 'connection timed out', 'operation timed out',
        'connection reset by peer', 'recv failure:', 'send failure:',
        'gnutls recv error (-110)', 'gnutls recv error (-54)',
        'the tls connection was non-properly terminated',
        'http/2 stream', 'the requested url returned error: 502',
        'the requested url returned error: 503', 'the requested url returned error: 504'))


def fetch_source(repository, repository_url, commit, *, run=subprocess.run, pause=time.sleep):
    argv = ['git', '-C', str(repository), '-c', 'pack.threads=1', '-c', 'pack.windowMemory=4m',
            '-c', 'pack.packSizeLimit=128m', '-c', 'fetch.fsckObjects=true', '-c', 'gc.auto=0',
            '-c', 'http.lowSpeedLimit=1', '-c', 'http.lowSpeedTime=60',
            'fetch', '--no-tags', repository_url, commit + ':refs/cache/' + commit]
    environment = dict(os.environ, LC_ALL='C', GIT_TERMINAL_PROMPT='0')
    for attempt in range(3):
        # Stream progress to disk rather than accumulating an unbounded log on
        # the relay. Only the final diagnostic is needed for classification.
        with tempfile.TemporaryFile() as log:
            try:
                result = run(argv, stderr=log, env=environment, timeout=900)
                code = result.returncode
            except subprocess.TimeoutExpired:
                code = None
            log.seek(0)
            while block := log.read(65536):
                sys.stderr.write(block.decode('utf-8', errors='replace'))
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - 65536))
            message = log.read().decode('utf-8', errors='replace')
        if code == 0:
            return
        if code is not None and not network_error(message):
            raise subprocess.CalledProcessError(code, argv, stderr=message)
        if attempt < 2:
            print(f'SOURCE_NETWORK_RETRY {commit} attempt={attempt + 1}', flush=True)
            pause(5 * (attempt + 1))
    raise SourceNetworkError('Git source transfer deferred after three network failures: ' + commit)


@contextmanager
def source_repository(repository_url, commit, temporary, name):
    cache = os.environ.get('SAI_SOURCE_CACHE')
    if not cache:
        repository = Path(temporary) / (name + '-source.git')
        subprocess.run(['git', 'init', '--bare', str(repository)], check=True, capture_output=True)
        fetch_source(repository, repository_url, commit)
        yield repository
        return
    root = Path(cache)
    root.mkdir(parents=True, exist_ok=True)
    if root.resolve() != root:
        raise ValueError('redirected relay source cache')
    # The one-worker relay serializes source preparation. Keep full ancestry:
    # a shallow clone would invalidate the verified Git bundle contract.
    with (root / '.cache.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        repository = root / (hashlib.sha256(repository_url.encode()).hexdigest() + '.git')
        if not repository.exists():
            subprocess.run(['git', 'init', '--bare', str(repository)], check=True, capture_output=True)
        if repository.resolve() != repository:
            raise ValueError('redirected bare source repository')
        for setting, value in (('pack.threads', '1'), ('pack.windowMemory', '4m'),
                               ('core.deltaBaseCacheLimit', '4m'), ('gc.auto', '0')):
            subprocess.run(['git', '-C', str(repository), 'config', setting, value], check=True)
        if git(repository, 'rev-parse', '--is-shallow-repository') == 'false' and complete(repository, commit):
            print('RELAY_SOURCE_CACHE_HIT', commit, flush=True)
        else:
            fetch_source(repository, repository_url, commit)
        os.utime(repository, None)
        try:
            yield repository
        finally:
            # Cache contents are reproducible Git objects, not build evidence.
            # Bound the cache on the 10 GB relay disk, evicting oldest first.
            entries = sorted(root.glob('*.git'), key=lambda path: path.stat().st_mtime)
            sizes = {path: sum(item.stat().st_size for item in path.rglob('*') if item.is_file()) for path in entries}
            total = sum(sizes.values())
            for path in entries:
                if total <= 1536 * 1024**2:
                    break
                if path.resolve() != path or len(path.stem) != 64:
                    raise ValueError('unrecognized relay cache entry')
                shutil.rmtree(path)
                total -= sizes[path]


def response(process, timeout=3600):
    if not select.select([process.stdout], [], [], timeout)[0]:
        raise TimeoutError('source transfer lease/receiver timed out')
    line = process.stdout.readline()
    if not line:
        raise RuntimeError('source transfer session closed before acknowledgement')
    return json.loads(line)


@contextmanager
def transfer_lease(argv):
    process = subprocess.Popen(list(map(str, argv)), stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True)
    try:
        yield process, response(process)
    finally:
        process.stdin.close()
        try:
            code = process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise RuntimeError('source transfer session failed to close')
        finally:
            process.stdout.close()
        if code:
            raise subprocess.CalledProcessError(code, argv)


def upload_part(path, destination, remote, ssh, run=subprocess.run, pause=time.sleep):
    # Unlike scp retries this reuses a partial destination as the delta basis.
    # --checksum also repairs equal-sized corrupt leftovers; the receiver then
    # checks every part and both reconstructed bundle digests independently.
    argv = ['rsync', '--partial', '--checksum', '--protect-args', '--timeout=120',
            '-e', shlex.join(list(map(str, ssh))), str(path), remote + ':' + destination]
    for attempt in range(3):
        try:
            return run(argv, check=True, timeout=900)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise
            pause(5 * (attempt + 1))


def ensure_source(*, remote, options, port, controller, cache, commit,
                  repository_url, temporary, destination):
    """Called on an initial cache miss; recheck while holding the source lease."""
    commit = sha(commit)
    ssh = ['ssh', *options, '-p', str(port)]
    command = ['python3', str(Path(controller) / 'source_cache.py'),
               'transfer-session', cache, commit]
    with transfer_lease([*ssh, remote, shlex.join(command)]) as (process, inventory):
        if commit in inventory['cache_shas']:
            print('SOURCE_CACHE_HIT_AFTER_WAIT', commit, flush=True)
            return
        with source_repository(repository_url, commit, temporary, Path(cache).name) as repository:
            base = select_base(repository, commit, inventory['cache_shas'])
            parts = Path(tempfile.mkdtemp(prefix=Path(cache).name + '-source-parts-', dir=temporary))
            manifest = pack(repository, commit, parts, base)
        print('SOURCE_TRANSFER', Path(cache).name, 'base=' + str(base),
              'compressed_bytes=' + str(manifest['compressed_size']), flush=True)
        subprocess.run([*ssh, remote, shlex.join(['mkdir', '-p', destination])], check=True, timeout=90)
        upload_part(parts / 'manifest.json', destination + '/manifest.json', remote, ssh)
        # Each stream owns rsync and SSH processes. Keep the small relay's
        # aggregate process footprint bounded during source-cache misses.
        workers = 1 if os.environ.get('SAI_SOURCE_CACHE') else 4
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(upload_part, parts / part['name'],
                                       destination + '/' + part['name'], remote, ssh)
                       for part in manifest['parts']]
            for future in futures:
                future.result()
        process.stdin.write(json.dumps({'directory': destination}) + '\n')
        process.stdin.flush()
        if response(process, timeout=900) != {'received': commit}:
            raise ValueError('source receiver did not acknowledge the requested commit')
