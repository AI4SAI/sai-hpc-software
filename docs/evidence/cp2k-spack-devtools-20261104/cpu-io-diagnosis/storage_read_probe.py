"""Bounded read-only Lustre comparison; never reads the active CPU image."""
import json
import os
from pathlib import Path
import random
import signal
import socket
import subprocess
import time

signal.alarm(300)
root = Path('/org/org-open-sai/proj-sai-hpc-software')
output = Path(__file__).resolve().parent / ('samples-' + socket.gethostname() + '.jsonl')
paths = [root / ('runs/spack-devtools-20261104-b-' + part) / 'work.ext3'
         for part in ('16V100', '4V100', '8V100V0')]
with output.open('x') as stream:
    def emit(kind, **data):
        stream.write(json.dumps(dict(kind=kind, time=time.time(), **data)) + '\n')
        stream.flush()

    emit('identity', host=socket.gethostname(), job=os.environ.get('SLURM_JOB_ID'),
         rank=os.environ.get('SLURM_PROCID'), affinity=sorted(os.sched_getaffinity(0)),
         active_cpu_image_accessed=False)
    for path in paths:
        try:
            result = subprocess.run(['lfs', 'getstripe', str(path)], text=True,
                                    capture_output=True, timeout=8)
            emit('stripe', path=str(path), returncode=result.returncode,
                 stdout=result.stdout, stderr=result.stderr)
        except subprocess.TimeoutExpired:
            emit('stripe_timeout', path=str(path))
        started = time.monotonic()
        fd = os.open(path, os.O_RDONLY)
        emit('open', path=str(path), seconds=time.monotonic()-started)
        try:
            size = os.fstat(fd).st_size
            rng = random.Random(20261104)
            offsets = [rng.randrange(0, min(size, 4 * 1024**3) // 4096) * 4096 for _ in range(24)]
            for round_index in range(2):
                for offset in offsets:
                    started = time.monotonic()
                    data = os.pread(fd, 4096, offset)
                    emit('pread', path=str(path), round=round_index, offset=offset,
                         bytes=len(data), nonzero=any(data), seconds=time.monotonic()-started)
        finally:
            os.close(fd)
    emit('complete')
print(output, flush=True)
