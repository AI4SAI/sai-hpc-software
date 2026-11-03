"""Own writable-image teardown while using the installed Apptainer unchanged.

A short outer invocation provides the site's permitted root-mapped namespace.
The existing fuse2fs mounts the image there. The original build command uses
that mount as a directory overlay, with its original isolation and bind policy.
After the build exits, unmount and wait for fuse2fs before returning to the host.
Only mountpoints and receipts are outside the image; no build tree is extracted.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

MOUNT_TIMEOUT = 60
EXIT_TIMEOUT = 120
VALUE_OPTIONS = {'--bind', '--env', '--overlay', '--no-mount', '--pwd', '--network'}
FLAG_OPTIONS = {'--fakeroot', '--ignore-fakeroot-command', '--cleanenv', '--containall',
                '--no-home', '--net', '--nv', '--userns'}


def inspect_command(argv, run):
    """Parse only the trusted build policy, never options in the payload argv."""
    if not argv or argv[0] != 'exec':
        raise ValueError('managed overlay requires apptainer exec')
    overlays, binds = [], []
    i = 1
    while i < len(argv) and argv[i].startswith('-'):
        option = argv[i]
        if option in FLAG_OPTIONS:
            i += 1
            continue
        if option not in VALUE_OPTIONS or i + 1 == len(argv):
            raise ValueError('unsupported managed overlay option: ' + option)
        value = argv[i + 1]
        if option == '--overlay':
            overlays.append((i + 1, value))
        elif option == '--bind':
            binds.extend(value.split(','))
        i += 2
    if i + 1 >= len(argv) or not Path(argv[i]).is_absolute():
        raise ValueError('managed overlay requires an image and payload')
    writable = [(index, value) for index, value in overlays if not value.endswith(':ro')]
    if not writable:
        return None
    expected = str(Path(run) / 'work.ext3')
    if len(overlays) != 1 or len(writable) != 1 or writable[0][1] != expected:
        raise ValueError('managed overlay requires the exact per-run work.ext3')
    return dict(index=writable[0][0], overlay=expected, image=argv[i], binds=binds)


def clean_environment(env):
    # A nested invocation must not inherit the outer invocation's mount list,
    # fakeroot preload, or session metadata. Keep explicit APPTAINERENV_* values.
    result = {k: v for k, v in env.items()
              if not k.startswith(('APPTAINER_', 'SINGULARITY_')) and k != 'LD_PRELOAD'}
    for name in ('APPTAINER_TMPDIR', 'APPTAINER_CACHEDIR'):
        if name in env:
            result[name] = env[name]
    return result


def launch(project, run, app, config, argv, execute):
    from cp2k_image_driver import canonical, driver_path
    from remote_controller import private_scratch

    policy = inspect_command(argv, run)
    if policy is None:
        return execute([str(app), '--config', str(config), *argv], run)
    overlay = canonical(policy['overlay'])
    if not overlay.is_file():
        raise ValueError('missing writable image')
    run, project = Path(run), Path(project)
    token = uuid.uuid4().hex
    session = run / 'runtime' / ('overlay-session-' + token)
    session.mkdir(mode=0o700)
    private_scratch(session)
    for name in ('mount', 'tmp', 'cache'):
        (session / name).mkdir(mode=0o700)
    env = clean_environment(os.environ)
    env.update(TMPDIR=str(session/'tmp'), APPTAINER_TMPDIR=str(session/'tmp'),
               APPTAINER_CACHEDIR=str(session/'cache'))
    request = dict(schema=1, project=str(project), run=str(run), app=str(app),
                   config=str(config), driver=str(driver_path(project)), argv=argv,
                   policy=policy, token=token,
                   forwarded_env={k: v for k, v in env.items()
                                  if k.startswith(('APPTAINERENV_', 'SINGULARITYENV_'))})
    request_path = session / 'request.json'
    request_path.write_text(json.dumps(request, sort_keys=True) + '\n')
    request_path.chmod(0o400)

    # Bind source paths at their original names so the inner command can apply
    # exactly its existing source:destination:mode policy. Host installations
    # remain read-only in both layers.
    # The inner --nv resolves host driver libraries through ldconfig -p. Its
    # lookup runs in this outer namespace, so carry the matching host cache.
    binds = {path: 'ro' for path in ('/usr', '/sbin', '/lib', '/lib64', '/opt/devtools',
             '/etc/ld.so.cache',
             '/etc/passwd', '/etc/group', '/etc/subuid', '/etc/subgid', '/etc/nsswitch.conf')
             if Path(path).exists()}
    binds.update({str(project): 'rw', str(Path(__file__).resolve().parent): 'ro',
                  policy['image']: 'ro'})
    for bind in policy['binds']:
        parts = bind.split(':')
        if not 1 <= len(parts) <= 3 or not Path(parts[0]).is_absolute():
            raise ValueError('invalid build bind source')
        mode = parts[2] if len(parts) == 3 else 'rw'
        if mode not in ('ro', 'rw'):
            raise ValueError('unsupported build bind mode')
        binds[parts[0]] = mode
    command = [str(app), '--config', str(config), 'exec', '--fakeroot',
               '--ignore-fakeroot-command', '--no-home', '--no-mount',
               'bind-paths,home,cwd,tmp,hostfs', '--pwd', '/']
    # Do not pass --nv here: the build's original --nv performs GPU setup in
    # the inner container. The outer layer retains the host device view.
    for source, mode in binds.items():
        command += ['--bind', source + ':' + source + ':' + mode]
    command += [policy['image'], '/usr/bin/python3', '-B', str(Path(__file__).resolve()),
                str(request_path)]
    # execute preserves binary stdout (including extracted squashfs streams)
    # and rejects Apptainer cleanup errors in either layer.
    saved = os.environ.copy()
    try:
        os.environ.clear()
        os.environ.update(env)
        status = execute(command, run)
        if status == 0:
            # The stock runtime has historically returned zero after a driver
            # failure. Require the manager's completed lifecycle evidence too.
            receipt = run / 'results' / ('managed-overlay-' + token + '.json')
            try:
                record = json.loads(receipt.read_text())
                valid = (record.get('completed') is True and record.get('accepted') is True
                         and record.get('forced_stop') is False
                         and record.get('payload_returncode') == 0
                         and record.get('driver_returncode') == 0
                         and record.get('unmount_returncode') == 0
                         and record.get('overlay') == str(overlay)
                         and record.get('apptainer') == str(app))
            except (OSError, ValueError):
                valid = False
            if not valid:
                print('MANAGED_OVERLAY_INCOMPLETE ' + str(receipt), file=sys.stderr, flush=True)
                return 74
        return status
    finally:
        os.environ.clear()
        os.environ.update(saved)


def mounted(path):
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        point = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4])
        if point == str(path):
            return True
    return False


def stop_driver(driver, mount, record):
    """Normal unmount first; any escalation makes this phase unsuccessful."""
    started = time.monotonic()
    try:
        if mounted(mount):
            result = subprocess.run(['/usr/bin/umount', str(mount)], capture_output=True,
                                    text=True, timeout=MOUNT_TIMEOUT)
            record['unmount_returncode'] = result.returncode
            record['unmount_stderr'] = result.stderr
        else:
            record['unmount_returncode'] = None
        record['driver_returncode'] = driver.wait(timeout=EXIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        record['driver_timeout'] = True
    finally:
        if driver.poll() is None:
            record['forced_stop'] = True
            driver.terminate()
            try:
                driver.wait(timeout=5)
            except subprocess.TimeoutExpired:
                driver.kill()
                driver.wait()
        record['teardown_seconds'] = time.monotonic() - started
    return (record.get('unmount_returncode') == 0 and
            record.get('driver_returncode') == 0 and not record.get('forced_stop'))


def inside(request_path):
    from cp2k_image_driver import (canonical, checksum, DRIVER_SHA256, driver_path,
                                   execute, prepare, runtime_paths)

    request_path = canonical(request_path)
    request = json.loads(request_path.read_text())
    project, run = canonical(request['project']), canonical(request['run'])
    session = run / 'runtime' / ('overlay-session-' + request['token'])
    if (not re.fullmatch('[0-9a-f]{32}', request['token']) or
            request_path != session / 'request.json' or run.parent != project / 'runs'):
        raise ValueError('managed overlay request escaped its run')
    app, _ = runtime_paths()
    config = prepare(project, run)
    policy = inspect_command(request['argv'], run)
    driver_pathname = canonical(driver_path(project))
    if (request.get('schema') != 1 or request['app'] != str(app) or
            request['config'] != str(config) or policy != request['policy'] or
            request['driver'] != str(driver_pathname) or checksum(driver_pathname) != DRIVER_SHA256):
        raise ValueError('managed overlay identity changed')
    mount = canonical(session / 'mount')
    overlay = canonical(policy['overlay'])
    env = clean_environment(os.environ)
    env.update(request['forwarded_env'])
    env.update(TMPDIR=str(session/'tmp'), APPTAINER_TMPDIR=str(session/'tmp'),
               APPTAINER_CACHEDIR=str(session/'cache'))
    os.environ.clear()
    os.environ.update(env)
    receipt = run / 'results' / ('managed-overlay-' + request['token'] + '.json')
    log = run / 'results' / ('managed-overlay-' + request['token'] + '.log')
    record = dict(schema=1, mechanism='workflow-owned-fuse2fs', overlay=str(overlay),
                  apptainer=str(app), driver_sha256=DRIVER_SHA256, completed=False,
                  accepted=False, forced_stop=False, started=time.time())
    receipt.write_text(json.dumps(record, sort_keys=True) + '\n')
    status = 74
    with log.open('xb') as stream:
        command = [str(driver_pathname), '-f', '-o', 'fakeroot', str(overlay), str(mount)]
        driver = subprocess.Popen(command, env=env, stdout=stream, stderr=subprocess.STDOUT)
        record['driver_pid'] = driver.pid
        try:
            deadline = time.monotonic() + MOUNT_TIMEOUT
            while not mounted(mount):
                if driver.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('fuse2fs did not mount; see ' + str(log))
                time.sleep(0.1)
            record['mounted'] = True
            receipt.write_text(json.dumps(record, sort_keys=True) + '\n')
            payload = request['argv'].copy()
            payload[policy['index']] = str(mount)
            status = execute([str(app), '--config', str(config), *payload], run)
            record['payload_returncode'] = status
        except Exception as error:
            record['error'] = str(error)
            print('MANAGED_OVERLAY_FAILED ' + str(error), file=sys.stderr, flush=True)
            status = 74
        finally:
            clean = stop_driver(driver, mount, record)
            if not clean:
                status = 74
            record.update(completed=True, accepted=status == 0, finished=time.time())
            receipt.write_text(json.dumps(record, sort_keys=True) + '\n')
            print('MANAGED_OVERLAY_EXIT ' + str(receipt) + ' status=' + str(status),
                  file=sys.stderr, flush=True)
    return status


if __name__ == '__main__':
    raise SystemExit(inside(Path(sys.argv[1])))
