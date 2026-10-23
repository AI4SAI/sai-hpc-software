#!/usr/bin/env python3
"""Read /proc for one existing CP2K batch allocation; never inspect other jobs."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import sys


job = sys.argv[1]
if not job.isdigit() or os.environ.get('SLURM_JOB_ID') != job:
    raise ValueError('diagnostic must run inside the requested allocation')
rows = []
for process in Path('/proc').glob('[0-9]*'):
    try:
        group = (process / 'cgroup').read_text()
        if '/job_' + job + '/step_batch' not in group:
            continue
        stat = (process / 'stat').read_text()
        fields = stat[stat.rfind(')') + 2:].split()
        arguments = (process / 'cmdline').read_bytes().split(b'\0')
        row = dict(pid=int(process.name), ppid=int(fields[1]), state=fields[0],
                   cpu_seconds=(int(fields[11]) + int(fields[12])) / os.sysconf('SC_CLK_TCK'),
                   args=[item.decode(errors='replace')[:240] for item in arguments[:8]],
                   cgroup=group, wchan=(process / 'wchan').read_text(),
                   io=(process / 'io').read_text(), overlay_descriptors=[])
        for descriptor in (process / 'fd').iterdir():
            try:
                path = str(descriptor.readlink())
                if path.endswith('.ext3'):
                    row['overlay_descriptors'].append(dict(path=path,
                        fdinfo=(process / 'fdinfo' / descriptor.name).read_text()))
            except OSError:
                pass
        rows.append(row)
    except (OSError, ValueError, IndexError):
        continue
print(json.dumps(dict(observed_at=datetime.now(timezone.utc).isoformat(),
    job=job, diagnostic_step=os.environ.get('SLURM_STEP_ID'), node=socket.gethostname(),
    workdir=os.getcwd(), processes=sorted(rows, key=lambda row: row['pid'])), sort_keys=True))
