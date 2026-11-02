"""Runner-side source transfer with a remote lease and resumable verified parts."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import select
import shlex
import subprocess
import time

from source_cache import pack, select_base, sha


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
        repository = Path(temporary) / (Path(cache).name + '-source.git')
        subprocess.run(['git', 'clone', '--bare', repository_url, str(repository)], check=True)
        subprocess.run(['git', '-C', str(repository), 'fetch', '--no-tags', 'origin', commit], check=True)
        base = select_base(repository, commit, inventory['cache_shas'])
        parts = Path(temporary) / (Path(cache).name + '-source-parts')
        manifest = pack(repository, commit, parts, base)
        print('SOURCE_TRANSFER', Path(cache).name, 'base=' + str(base),
              'compressed_bytes=' + str(manifest['compressed_size']), flush=True)
        subprocess.run([*ssh, remote, shlex.join(['mkdir', '-p', destination])], check=True, timeout=90)
        upload_part(parts / 'manifest.json', destination + '/manifest.json', remote, ssh)
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(upload_part, parts / part['name'],
                                       destination + '/' + part['name'], remote, ssh)
                       for part in manifest['parts']]
            for future in futures:
                future.result()
        process.stdin.write(json.dumps({'directory': destination}) + '\n')
        process.stdin.flush()
        if response(process, timeout=900) != {'received': commit}:
            raise ValueError('source receiver did not acknowledge the requested commit')
