#!/usr/bin/env python3
"""Record the real UID, affinity, filesystem and loaded ELF dependencies."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

prefix = Path(sys.argv[1])
binary = prefix / 'bin/cp2k.psmp'


def file_record(path):
    info = path.stat()
    return dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                dev=info.st_dev, inode=info.st_ino, uid=info.st_uid)


ldd = subprocess.check_output(['ldd', str(binary)], text=True)
assert 'not found' not in ldd
Path('ldd.txt').write_text(ldd)
libraries = {name: dict(path=path, **file_record(Path(path)))
             for name, path in re.findall(r'^\s*(\S+)\s+=>\s+(/\S+)', ldd, re.M)}
cwd = Path.cwd().stat()
record = dict(uid=os.getuid(), euid=os.geteuid(), host=socket.gethostname(),
              affinity=sorted(os.sched_getaffinity(0)), cwd=str(Path.cwd()),
              cwd_dev=cwd.st_dev, cwd_inode=cwd.st_ino, binary=file_record(binary),
              libraries=libraries,
              data={name:file_record(prefix/'share/cp2k/data'/name)
                    for name in ['BASIS_MOLOPT', 'GTH_POTENTIALS']},
              environment={name:os.environ.get(name) for name in
                  ['OMP_NUM_THREADS','OMP_PLACES','OMP_PROC_BIND','OPENBLAS_NUM_THREADS',
                   'MKL_NUM_THREADS','CUDA_VISIBLE_DEVICES','CP2K_DATA_DIR','TMPDIR','MPI_HOME']})
Path('mountinfo.txt').write_text(Path('/proc/self/mountinfo').read_text())
Path('probe.json').write_text(json.dumps(record, indent=2, sort_keys=True)+'\n')
