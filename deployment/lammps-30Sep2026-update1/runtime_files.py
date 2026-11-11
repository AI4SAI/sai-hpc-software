"""Standard runtime inventory policy shared with the original LAMMPS release.

Kept self-contained for the administrator handoff; no controller checkout is
required and no workflow metadata is copied into the application prefix.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def plain(path):
    require(path.is_absolute() and '..' not in path.parts, 'noncanonical path: ' + str(path))
    for part in [*reversed(path.parents), path]:
        require(not part.is_symlink(), 'symlink in installation path: ' + str(part))


def pinned(path, expected):
    plain(path)
    require(path.is_file() and path.stat().st_size < 64 * 1024**2, 'missing/oversized metadata: ' + str(path))
    require(digest(path) == expected, 'metadata changed: ' + str(path))
    return json.loads(path.read_text())


def manifest_records(manifest, spec):
    prefix = Path(spec['prefix'])
    require(manifest['entry']['identity'] == spec['identity'], 'native identity changed')
    expected = {}
    for item in manifest['files']:
        absolute = Path('/' + item['path'])
        require(absolute.is_relative_to(prefix) and '..' not in absolute.parts, 'inventory escapes prefix')
        relative = str(absolute.relative_to(prefix))
        require(relative not in expected, 'duplicate inventory path')
        expected[relative] = item
    return expected


def omission(relative):
    parts = Path(relative).parts
    if parts[:2] == ('share', 'sai'):
        return 'workflow metadata and validation material stay in the home deployment package'
    if parts[:1] == ('modulefiles',):
        return 'replaced by the standalone site module'
    if parts[:3] == ('share', 'lammps', 'bench'):
        return 'upstream benchmark inputs'
    if '__pycache__' in parts or Path(relative).suffix in ('.pyc', '.pyo'):
        return 'regenerable Python bytecode cache'
    if relative == '.gitignore':
        return 'virtualenv development metadata'
    if parts[:1] == ('bin',) and len(parts) == 2 and (
            parts[1] in ('Activate.ps1', 'activate', 'activate.csh', 'activate.fish') or
            parts[1] == 'pip' or parts[1].startswith('pip3')):
        return 'environment installation/activation tool; users load the site module'
    if 'site-packages' in parts:
        packages = parts[parts.index('site-packages') + 1:]
        if packages and (packages[0] == 'pip' or
                         packages[0].startswith('pip-') and packages[0].endswith('.dist-info')):
            return 'pip installation tool'
    return None


def runtime_records(manifest, spec):
    """Omit only known non-runtime material; preserve public APIs and all data."""
    original = manifest_records(manifest, spec)
    selected, removed = {}, []
    for name, row in original.items():
        require(row['kind'] in ('file', 'directory', 'symlink'), 'special export entry')
        require(type(row['mode']) is int and 0 <= row['mode'] <= 0o777, 'invalid export mode')
        reason = omission(name)
        if reason:
            if row['kind'] != 'directory':
                removed.append(dict(path=name, bytes=row.get('size', 0), reason=reason))
        elif row['kind'] != 'directory':
            selected[name] = dict(row, path=name)
    for name in list(selected):
        for parent in Path(name).parents:
            key = str(parent)
            require(original.get(key, {}).get('kind') == 'directory', 'missing parent directory')
            selected[key] = dict(original[key], path=key)
    prefix = Path(spec['prefix'])
    # Resolve retained links against the projected inventory, not the source
    # filesystem, to catch links into omitted files before anything is copied.
    for name, row in selected.items():
        if row['kind'] != 'symlink':
            continue
        pending, resolved, links = str(prefix / name).split('/'), [], 0
        while pending:
            part = pending.pop(0)
            if part in ('', '.'):
                continue
            if part == '..':
                if resolved:
                    resolved.pop()
                continue
            resolved.append(part)
            path = Path('/' + '/'.join(resolved))
            if path.is_relative_to(prefix):
                entry = selected.get(str(path.relative_to(prefix)))
                require(entry is not None, 'pruning breaks a runtime link: ' + name)
                if entry['kind'] == 'symlink':
                    links += 1
                    require(links <= 40, 'cyclic runtime link: ' + name)
                    resolved.pop()
                    if entry['target'].startswith('/'):
                        resolved = []
                    pending = entry['target'].split('/') + pending
                elif pending:
                    require(entry['kind'] == 'directory', 'link traverses a file')
        target = Path('/' + '/'.join(resolved))
        roots = [prefix, *map(Path, manifest['entry'].get('external_roots', []))]
        require(any(target.is_relative_to(root) for root in roots), 'link escapes declared runtime roots')
    return sorted(selected.values(), key=lambda row: row['path']), sorted(removed, key=lambda row: row['path'])


def verify_records(root, expected, *, subset=False):
    plain(root)
    seen, pending, files, size = set(), ([root / name for name in expected] if subset else [root]), 0, 0
    while pending:
        path = pending.pop()
        name = str(path.relative_to(root))
        row = expected.get(name)
        require(row is not None, 'unlisted file: ' + str(path))
        info = path.lstat()
        kind = ('symlink' if stat.S_ISLNK(info.st_mode) else 'directory' if stat.S_ISDIR(info.st_mode)
                else 'file' if stat.S_ISREG(info.st_mode) else 'special')
        require(kind == row['kind'], 'file type changed: ' + str(path))
        if kind == 'symlink':
            require(os.readlink(path) == row['target'], 'link target changed: ' + str(path))
        else:
            require(stat.S_IMODE(info.st_mode) == row['mode'], 'permissions changed: ' + str(path))
            if kind == 'directory':
                if not subset:
                    pending.extend(path.iterdir())
            else:
                require(('size' not in row or info.st_size == row['size']) and digest(path) == row['sha256'],
                        'payload changed: ' + str(path))
                files += 1
                size += info.st_size
        seen.add(name)
    require(seen == set(expected), 'missing installed files: ' + str(root))
    return dict(files=files, bytes=size)


def directories(path):
    plain(path)
    for parent in [*reversed(path.parents), path]:
        if not parent.exists():
            parent.mkdir(mode=0o755)
            parent.chmod(0o755)
        require(parent.is_dir(), 'installation ancestor is not a directory')
