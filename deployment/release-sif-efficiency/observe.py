#!/usr/bin/env python3
"""Record real rank placement; expensive provenance runs outside timed samples."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


operation, executable, *arguments = sys.argv[1:]
cwd = Path.cwd()
info = cwd.stat()
record = dict(uid=os.getuid(), node=socket.gethostname(), affinity=sorted(os.sched_getaffinity(0)),
              rank=os.environ.get('OMPI_COMM_WORLD_RANK'), cwd=str(cwd),
              cwd_device=info.st_dev, cwd_inode=info.st_ino,
              cuda_visible_devices=os.environ.get('CUDA_VISIBLE_DEVICES'),
              omp_threads=os.environ.get('OMP_NUM_THREADS'), executable=executable)
(cwd / 'rank.json').write_text(json.dumps(record, sort_keys=True) + '\n')
if operation == 'probe':
    binary = Path(executable)
    detail = dict(record, binary=dict(sha256=digest(binary), device=binary.stat().st_dev,
                                    inode=binary.stat().st_ino, resolved=str(binary.resolve())))
    text = subprocess.check_output(['ldd', executable], text=True)
    assert 'not found' not in text
    (cwd / 'ldd.txt').write_text(text)
    libraries = set(re.findall(r'(?:=>\s+|^\s*)(/\S+)\s+\(', text, re.M))
    detail['libraries'] = {p: digest(p) for p in sorted(libraries)}
    selected = json.loads((cwd / 'probe-files.json').read_text())
    detail['files'] = {p: dict(sha256=digest(p), device=Path(p).stat().st_dev,
                             inode=Path(p).stat().st_ino) for p in selected}
    (cwd / 'mountinfo.txt').write_text(Path('/proc/self/mountinfo').read_text())
    (cwd / 'probe.json').write_text(json.dumps(detail, indent=2, sort_keys=True) + '\n')
else:
    assert operation == 'run'
    os.execv(executable, [executable, *arguments])
