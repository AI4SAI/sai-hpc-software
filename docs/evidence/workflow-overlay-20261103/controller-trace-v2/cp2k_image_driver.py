#!/usr/bin/env python3
"""Use the checksum-pinned overlay driver and own writable image teardown.

The adapter preserves Apptainer's inherited OFD image lock and explicit fsync,
and opens explicitly read-only images with a read-only backing descriptor.
Source, patches, rebuild inputs and filesystem tests are recorded in
docs/evidence/fuse2fs-{efficiency,readonly}-20261017. managed_overlay keeps the
writer outside Apptainer's one-second driver deadline. System files are unchanged.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from source_cache import checksum

SOURCE_COMMIT = '45651efd6db7da8738bc9d8d1f17e99259cabfe4'
PATCHED_SOURCE_SHA256 = 'c63d1fb25510d316836952953e9adddacd3214cd91d6002bf3b16aed350c4ddc'
DRIVER_SHA256 = '46c1a8c8f42ab2419ea88072e41d89903c453d93f52485b5baddea5ca238bb10'
APPTAINER = Path('/opt/devtools/apptainer/1.4.4/bin/apptainer')
SITE_CONFIG = Path('/opt/devtools/apptainer/1.4.4/etc/apptainer/apptainer.conf')
INDEPENDENT_PREFIX = Path('/opt/devtools/apptainer-sai/1.4.4')
SITE_CLI_SHA256 = '2ba9ae17c087a0d8a6bbfeaae0b12775bd8724f51d5452c41e951ea853a8e5da'
STARTER_SHA256 = '80cf5306207ea473a4b412a617e8594accebf9bc05413f5cb1f9f8cde7e2b529'


def module_load_command():
    # Writable image lifetime is owned by the workflow, not the stock runtime's
    # one-second driver deadline. No alternate public runtime is required.
    return 'module load apptainer/1.4.4'


def runtime_paths():
    selected = os.environ.get('SAI_HPC_APPTAINER', str(APPTAINER))
    if selected == str(APPTAINER):
        return APPTAINER, SITE_CONFIG
    app = INDEPENDENT_PREFIX / 'bin/apptainer'
    if selected != str(app):
        raise ValueError('unsupported SAI_HPC_APPTAINER; load apptainer/1.4.4-sai')
    for path, expected in ((app, SITE_CLI_SHA256),
                           (INDEPENDENT_PREFIX / 'libexec/apptainer/bin/starter', STARTER_SHA256)):
        canonical(path)
        if path.stat().st_mode & 0o6022 or checksum(path) != expected:
            raise ValueError('independent Apptainer runtime identity differs: ' + str(path))
    return app, INDEPENDENT_PREFIX / 'etc/apptainer/apptainer.conf'


def driver_path(project):
    return Path(project) / 'cache/tools' / ('fuse2fs-' + DRIVER_SHA256) / 'bin/fuse2fs'


def wrap_command(project, run, controller, argv):
    """Wrap an already complete exec argv; bind option positions stay unchanged."""
    if argv[:2] != ['apptainer', 'exec']:
        raise ValueError('CP2K image driver only wraps apptainer exec')
    return ['/usr/bin/python3', str(Path(controller) / 'cp2k_image_driver.py'),
            str(project), str(run), *argv[1:]]


def canonical(path):
    path = Path(path)
    if (not path.is_absolute() or path.resolve() != path or path.is_symlink() or
            any(c in str(path) for c in ':,\n\r$')):
        raise ValueError('untrusted image driver path: ' + str(path))
    return path


def write_once(path, content):
    path = canonical(path)
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise ValueError('image driver configuration or receipt changed: ' + str(path))
        return
    with path.open('xb') as stream:
        stream.write(content)
    path.chmod(0o400)


def validate_receipt(project, record):
    # Dependency receipts are checked outside the job's module environment.
    # Recognize either recorded runtime explicitly; never rewrite its identity.
    app = record.get('apptainer')
    if app not in (str(APPTAINER), str(INDEPENDENT_PREFIX / 'bin/apptainer')):
        raise ValueError('unrecognized Apptainer runtime in image driver provenance')
    if app != str(APPTAINER) and (record.get('apptainer_sha256') != SITE_CLI_SHA256 or
                                  record.get('starter_sha256') != STARTER_SHA256):
        raise ValueError('independent Apptainer runtime provenance differs')
    expected = dict(schema=1, source_commit=SOURCE_COMMIT,
                    patched_source_sha256=PATCHED_SOURCE_SHA256,
                    binary_sha256=DRIVER_SHA256, driver=str(driver_path(project)),
                    apptainer=app)
    digest = record.get('config_sha256', '')
    if (any(record.get(key) != value for key, value in expected.items()) or
            len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)):
        raise ValueError('CP2K image driver provenance differs from the recipe')


def prepare(project, run):
    app, site_config = runtime_paths()
    project, run = canonical(project), canonical(run)
    if run.parent != project / 'runs':
        raise ValueError('image driver run escaped the project')
    for directory in (run / 'runtime', run / 'results'):
        if not canonical(directory).is_dir():
            raise ValueError('image driver requires prepared run directories')
    driver = canonical(driver_path(project))
    if (not driver.is_file() or not os.access(driver, os.X_OK) or
            driver.stat().st_mode & 0o222 or checksum(driver) != DRIVER_SHA256):
        raise ValueError('missing, writable or changed pinned CP2K fuse2fs driver')
    # Apptainer inserts its bundled helper at the literal $PATH position.
    # The private directory must precede it; changing shell PATH is insufficient.
    content = (site_config.read_text() + '\nbinary path = ' + str(driver.parent) +
               ':$PATH:/usr/bin:/bin:/usr/sbin:/sbin\n').encode()
    config = run / 'runtime/apptainer-fuse2fs.conf'
    write_once(config, content)
    record = dict(schema=1, source_commit=SOURCE_COMMIT,
                  patched_source_sha256=PATCHED_SOURCE_SHA256,
                  binary_sha256=DRIVER_SHA256, driver=str(driver),
                  apptainer=str(app), config_sha256=hashlib.sha256(content).hexdigest())
    if app != APPTAINER:
        record.update(apptainer_sha256=SITE_CLI_SHA256, starter_sha256=STARTER_SHA256)
    validate_receipt(project, record)
    # This receipt has no per-run paths, so dependency -> application cloning
    # preserves it verbatim and verifies the same driver/configuration again.
    write_once(run / 'results/image-driver.json',
               (json.dumps(record, sort_keys=True) + '\n').encode())
    return config


def mount_timeout(returncode, stderr):
    # Apptainer emits this before it starts the container payload. Compilation,
    # scientific failures and arbitrary exit 255 are never retried here.
    return returncode == 255 and bool(re.search(
        rb'FATAL:[^\n]*container creation failed: mount hook function failure:[^\n]*fuse2fs failed to mount [^\n]* in 10s', stderr))


def execute(argv, run):
    for attempt in range(3):
        tail = b''
        with subprocess.Popen(argv, stderr=subprocess.PIPE) as process:
            for block in iter(lambda: process.stderr.read1(65536), b''):
                sys.stderr.buffer.write(block)
                sys.stderr.buffer.flush()
                tail = (tail + block)[-65536:]
            code = process.wait()
        forced_stop = bool(re.search(
            rb'Terminating (?:fuse2fs|fuse-overlayfs|squashfuse_ll|squashfuse) after timeout', tail))
        cleanup_failed = b'Cleanup error:' in tail
        if code == 0 and (forced_stop or cleanup_failed):
            # Apptainer can return payload success after killing the writer.
            # Never consume or retry that writable image as a successful phase.
            record = dict(reason='image driver teardown failed after payload success', argv=argv,
                          returncode=code, stderr_tail=tail.decode(errors='replace'))
            (Path(run) / 'results' / f'driver-cleanup-failure-{os.getpid()}.json').write_text(
                json.dumps(record, sort_keys=True) + '\n')
            return 74
        if attempt == 2 or not mount_timeout(code, tail):
            return code
        record = dict(attempt=attempt + 1, reason='fuse2fs mount timeout before payload', argv=argv)
        path = Path(run) / 'results' / f'mount-retry-{os.getpid()}-{attempt + 1}.json'
        path.write_text(json.dumps(record, sort_keys=True) + '\n')
        print('CONTAINER_MOUNT_RETRY ' + str(attempt + 1), file=sys.stderr, flush=True)
        time.sleep(5 * (attempt + 1))


def main():
    from managed_overlay import launch

    project, run, *argv = sys.argv[1:]
    if not argv or argv[0] != 'exec':
        raise ValueError('expected an apptainer exec command')
    config = prepare(project, run)
    print('CP2K_IMAGE_DRIVER sha256=' + DRIVER_SHA256 + ' config=' + str(config),
          file=sys.stderr, flush=True)
    app, _ = runtime_paths()
    return launch(project, run, app, config, argv, execute)


if __name__ == '__main__':
    raise SystemExit(main())
