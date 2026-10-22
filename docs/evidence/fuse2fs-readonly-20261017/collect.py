#!/usr/bin/env python3
"""Read-only collection of the isolated read-only driver validation."""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

base = Path(sys.argv[1]).resolve()
assert str(base).startswith('/home/stardust/sai-hpc-software/diagnostics/')
job = (base / 'job.id').read_text().strip()
assert job.isdecimal()


def evidence(path, include_text=True):
    data = path.read_bytes()
    record = dict(path=str(path), bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    if include_text:
        record['text'] = data.decode(errors='replace')
    return record


record = dict(
    checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    run=str(base), job_id=job,
    source_commit='45651efd6db7da8738bc9d8d1f17e99259cabfe4',
    pristine_source_sha256='ab3794d86b12a8227b4a87823349e974df06f39c42971dcf2983ddf57cf82696',
    patched_source_sha256='c63d1fb25510d316836952953e9adddacd3214cd91d6002bf3b16aed350c4ddc',
    accounting=subprocess.check_output(
        ['sacct', '-j', job, '--noheader', '--parsable2',
         '--format=JobIDRaw,Partition,State,Start,Elapsed,AllocCPUS,NodeList,ExitCode'], text=True),
    files={}, limitations=[
        'Only newly created disposable images are mounted for validation.',
        'Historical sealed dependency image hash mismatch remains invalid; it was not resealed.',
        'Original CMake link.txt EISDIR cause is unresolved by this driver read-only fix.',
    ])
for path in sorted(base.rglob('*')):
    if not path.is_file() or path.is_symlink():
        continue
    relative = path.relative_to(base)
    if path.suffix in ('.ext3', '.sif') or 'cache' in relative.parts or 'runtime' in relative.parts:
        continue
    if path.name == 'fuse2fs':
        record['driver'] = evidence(path, False)
    elif path.suffix == '.trace':
        record['files'][str(relative)] = evidence(path, False)
    elif path.stat().st_size <= 512 * 1024:
        record['files'][str(relative)] = evidence(path)
print(json.dumps(record, indent=2, sort_keys=True))
