#!/usr/bin/env python3
"""Install the qualified runtime in a new prefix; never modify the site runtime."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
PREFIX = Path('/opt/devtools/apptainer-sai/1.4.4')
MODULE = Path('/opt/modules/modulefiles/devtools/apptainer/1.4.4-sai.lua')
PROJECT = Path('/org/org-open-sai/proj-sai-hpc-software')


def digest(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('expected a regular file: ' + str(path))
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            h.update(block)
    return h.hexdigest()


def check_file(path, expected):
    if digest(path) != expected:
        raise ValueError('checksum differs: ' + str(path))


def canonical(path):
    if not path.is_absolute() or path.resolve() != path or path.is_symlink():
        raise ValueError('installation path must not contain symlinks: ' + str(path))
    return path


def writable_parent(path):
    parent = next(p for p in path.parents if p.exists())
    canonical(parent)
    return os.access(parent, os.W_OK | os.X_OK)


def verify_tree(prefix, entries):
    canonical(prefix)
    for name, entry in entries.items():
        path = prefix / name
        if entry['type'] == 'symlink':
            if not path.is_symlink() or os.readlink(path) != entry['target']:
                raise ValueError('symlink differs: ' + str(path))
        else:
            canonical(path)
            if path.stat().st_mode & 0o7777 != entry['mode']:
                raise ValueError('permissions differ: ' + str(path))
            if entry['type'] == 'file':
                check_file(path, entry['sha256'])
            elif not path.is_dir():
                raise ValueError('expected directory: ' + str(path))
    expected = set(entries) | {'share/sai', 'share/sai/driver-stop-grace.json'}
    extra = {str(p.relative_to(prefix)) for p in prefix.rglob('*')} - expected
    if extra:
        raise ValueError('unexpected installation entries: ' + ', '.join(sorted(extra)))


def extract_payload(archive, destination, entries):
    # Reject traversal, external links and link parents before extracting anything.
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        names = [m.name for m in members]
        if len(names) != len(set(names)) or set(names) != set(entries):
            raise ValueError('archive entries differ from the payload inventory')
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or '..' in name.parts or str(name) != member.name:
                raise ValueError('unsafe archive path')
            if any(entries.get(str(p), {}).get('type') == 'symlink' for p in name.parents):
                raise ValueError('archive entry has a symlink parent')
            if ((not member.issym() and member.mode & 0o6022) or
                    not (member.isfile() or member.isdir() or member.issym())):
                raise ValueError('unsafe archive type or permissions')
            path = destination / member.name
            if member.issym() and (os.path.isabs(member.linkname) or
                    not (path.parent / member.linkname).resolve().is_relative_to(destination)):
                raise ValueError('archive symlink escapes installation')
        for member in members:
            path = destination / member.name
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
                path.chmod(member.mode)
            elif member.isfile():
                path.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(member) as source, path.open('xb') as target:
                    shutil.copyfileobj(source, target)
                path.chmod(member.mode)
        for member in members:
            if member.issym():
                (destination / member.name).symlink_to(member.linkname)
    verify_tree(destination, entries)


def runtime_check(prefix, work, image):
    """A native launch is required; --version does not exercise AppArmor."""
    work, image = canonical(work), canonical(image)
    # The installing account cannot traverse /org/org-open-sai. Its short native
    # launch probe may use its own home; compilation and full Slurm qualification
    # continue to use the project account and project storage.
    if (not any(work.is_relative_to(root) for root in (PROJECT, Path.home().resolve()))
            or work.exists()):
        raise ValueError('runtime check requires a new directory inside the project or your home')
    work.mkdir(parents=True, mode=0o700)
    for attr in os.listxattr(work):
        if attr in ('system.posix_acl_access', 'system.posix_acl_default'):
            os.removexattr(work, attr)
    work.chmod(0o700)
    env = dict(os.environ, TMPDIR=str(work), APPTAINER_TMPDIR=str(work),
               APPTAINER_CACHEDIR=str(work / 'cache'))
    for name in tuple(env):
        if name.startswith(('APPTAINER_', 'APPTAINERENV_', 'SINGULARITY_', 'SINGULARITYENV_')):
            if name not in ('APPTAINER_TMPDIR', 'APPTAINER_CACHEDIR'):
                del env[name]
    command = [str(prefix / 'bin/apptainer'), 'exec', '--userns', '--cleanenv',
               '--containall', '--no-home', '--no-mount', 'home,cwd,tmp,hostfs,bind-paths',
               '--bind', '/usr,/sbin,/lib,/lib64', str(image), '/usr/bin/true']
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=90)
    record = dict(command=command, returncode=result.returncode,
                  stdout=result.stdout, stderr=result.stderr, node=os.uname().nodename)
    (work / 'runtime-check.json').write_text(json.dumps(record, indent=2) + '\n')
    if result.returncode:
        raise RuntimeError('native runtime launch failed; see ' + str(work / 'runtime-check.json') +
                           '\n' + result.stderr)
    return record


def deploy(action, here=HERE, prefix=PREFIX, module=MODULE, work=None, image=None):
    canonical(prefix)
    canonical(module)
    manifest = json.loads((here / 'manifest.json').read_text())
    check_file(here / 'payload-files.json', manifest['payload_files_sha256'])
    entries = json.loads((here / 'payload-files.json').read_text())
    receipt = prefix / 'share/sai/driver-stop-grace.json'
    module_content = (here / 'module.lua').read_bytes()
    if prefix.exists():
        verify_tree(prefix, entries)
        if json.loads(receipt.read_text()) != manifest:
            raise ValueError('existing installation belongs to a different runtime')
    if module.exists() and module.read_bytes() != module_content:
        raise ValueError('refusing to replace an unrelated module')
    result = dict(prefix=str(prefix), module=str(module), action=action,
                  installed=prefix.exists(), module_active=module.exists(),
                  installation_directory_writable=writable_parent(prefix),
                  module_directory_writable=writable_parent(module),
                  runtime_launch_verified=False)
    if action in ('check', 'install'):
        proof = json.loads((here / 'qualification.json').read_text())
        required = {'plain-small-a', 'plain-small-b', 'plain-build-size', 'delayed-fsync'}
        if (proof.get('verified') is not True or proof.get('starter_sha256') != manifest['starter_sha256']
                or proof.get('binary_sha256') != manifest['site_cli_sha256']
                or {case['label'] for case in proof.get('cases', [])} != required
                or any(case.get('data_verified') is not True or case.get('fsck_returncode') != 0
                       or case.get('cleanup_failure') is not False or case.get('timeout_warning') is not False
                       for case in proof['cases'])):
            raise ValueError('runtime has not passed the complete overlay qualification')
        check_file(here / 'graceful-driver-stop.patch', manifest['patch_sha256'])
        check_file(here / 'payload/apptainer-runtime.tar.gz', manifest['payload_sha256'])
        check_file(here / 'payload/native-check.sif', manifest['native_probe_sha256'])
    if action == 'check':
        return result
    if action == 'install':
        if not result['installation_directory_writable']:
            raise PermissionError('write access is required for the new installation parent')
        if not prefix.parent.exists():
            prefix.parent.mkdir(parents=True, mode=0o755)
            prefix.parent.chmod(0o755)
        canonical(prefix.parent)
        with (prefix.parent / '.install.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if not prefix.exists():
                with tempfile.TemporaryDirectory(dir=prefix.parent, prefix='.stage-') as staging:
                    stage = Path(staging) / 'runtime'
                    stage.mkdir(mode=0o755)
                    stage.chmod(0o755)
                    extract_payload(here / 'payload/apptainer-runtime.tar.gz', stage, entries)
                    (stage / 'share/sai').mkdir(mode=0o755)
                    (stage / 'share/sai').chmod(0o755)
                    shutil.copyfile(here / 'manifest.json', stage / receipt.relative_to(prefix))
                    (stage / receipt.relative_to(prefix)).chmod(0o644)
                    os.rename(stage, prefix)
            verify_tree(prefix, entries)
            if json.loads(receipt.read_text()) != manifest:
                raise ValueError('installation receipt differs')
        return dict(result, installed=True)
    if not prefix.exists():
        raise ValueError('install the independent runtime first')
    if action == 'verify':
        return dict(result, files_verified=True)
    if action in ('runtime-check', 'activate'):
        if work is None:
            raise ValueError('--work is required for a native runtime check')
        if image is None:
            image = here / 'payload/native-check.sif'
        check_file(image, manifest['native_probe_sha256'])
        runtime_check(prefix, work, image)
        result['runtime_launch_verified'] = True
        if action == 'activate':
            if not result['module_directory_writable']:
                raise PermissionError('write access is required for the new module')
            if not module.exists():
                with tempfile.NamedTemporaryFile(dir=module.parent, prefix='.apptainer-module-', delete=False) as stream:
                    temporary = Path(stream.name)
                    try:
                        stream.write(module_content)
                        stream.flush()
                        os.fchmod(stream.fileno(), 0o644)
                        os.fsync(stream.fileno())
                        # An exclusive link publishes complete bytes without
                        # replacing a concurrent administrator's module.
                        os.link(temporary, module)
                    finally:
                        temporary.unlink()
            elif module.read_bytes() != module_content:
                raise ValueError('module changed during activation')
            result['module_active'] = True
        return result
    if action == 'rollback':
        # Retain runtime files and evidence; withdraw only our own module.
        if module.exists():
            module.unlink()
        return dict(result, module_active=False, rolled_back=True)
    raise ValueError('unknown installation action')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    for name in ('install', 'verify', 'runtime-check', 'activate', 'rollback'):
        group.add_argument('--' + name, action='store_const', const=name, dest='action')
    parser.add_argument('--work', type=Path)
    parser.add_argument('--image', type=Path)
    args = parser.parse_args()
    print(json.dumps(deploy(args.action or 'check', work=args.work, image=args.image), indent=2))


if __name__ == '__main__':
    main()
