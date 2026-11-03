"""Compare stock supervision and workflow supervision on the same allocation."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path('/org/org-open-sai/proj-sai-hpc-software')
WORK = Path(__file__).resolve().parent
CONTROL = WORK / 'controller'
APP = '/opt/devtools/apptainer/1.4.4/bin/apptainer'
BASE = ROOT / 'containers/base/minimal-v1.sif'
sys.path.insert(0, str(CONTROL))
from remote_controller import container_command, private_scratch

job = os.environ['SLURM_JOB_ID']
summary = WORK / ('benchmark-' + job + '.json')
record = {'job': job, 'node': socket.gethostname(), 'cases': [],
          'partition': os.environ['SLURM_JOB_PARTITION'],
          'cpus': os.environ.get('SLURM_CPUS_ON_NODE'),
          'payload_sha256': hashlib.sha256((WORK / 'payload.py').read_bytes()).hexdigest(),
          'controller_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in CONTROL.glob('*.py')}}
baseline = ('import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);'
            'from cp2k_image_driver import prepare,execute,runtime_paths;'
            'project,run=sys.argv[2:4];config=prepare(project,run);app,_=runtime_paths();'
            'raise SystemExit(execute([str(app),"--config",str(config),*sys.argv[4:]],Path(run)))')
# Both mechanisms get one warmup. Alternate order in three measured pairs.
order = [('direct', True), ('managed', True), ('direct', False), ('managed', False),
         ('managed', False), ('direct', False), ('direct', False), ('managed', False)]
for index, (mode, warmup) in enumerate(order):
    run = ROOT / 'runs' / f'overlay-efficiency-{job}-{index}-{mode}'
    run.mkdir(mode=0o700)
    for name in ('runtime', 'results', 'apptainer-cache'):
        private_scratch(run / name)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(('APPTAINER_', 'SINGULARITY_'))}
    env.pop('SAI_HPC_APPTAINER', None)
    env.update(TMPDIR=str(run / 'runtime'), APPTAINER_TMPDIR=str(run / 'runtime'),
               APPTAINER_CACHEDIR=str(run / 'apptainer-cache'))
    subprocess.run([APP, 'overlay', 'create', '--fakeroot', '--sparse', '--size', '1024',
                    str(run / 'work.ext3')], check=True, env=env)
    argv = container_command(str(BASE), ['/usr/bin/python3', '-B', '/bench/payload.py'],
                             overlay=str(run / 'work.ext3'), jobs=8, gpu=True,
                             extra_binds=[(WORK, '/bench')])
    argv[2:2] = ['--bind', str(run / 'results') + ':/results:rw',
                 '--env', 'LD_LIBRARY_PATH=/opt/devtools/gcc/14.3.0/lib64']
    if mode == 'managed':
        command = ['/usr/bin/python3', '-B', str(CONTROL / 'cp2k_image_driver.py'),
                   str(ROOT), str(run), *argv[1:]]
    else:
        command = ['/usr/bin/python3', '-B', '-c', baseline, str(CONTROL),
                   str(ROOT), str(run), *argv[1:]]
    t = time.monotonic()
    with (run / 'results/write.log').open('w') as stream:
        rc = subprocess.run(command, env=env, stdout=stream, stderr=subprocess.STDOUT,
                            timeout=300).returncode
    wall = time.monotonic() - t
    logs = (run / 'results/write.log').read_text()
    item = {'index': index, 'mode': mode, 'warmup': warmup, 'run': str(run),
            'returncode': rc, 'total_seconds': wall,
            'cleanup_warning': 'after timeout' in logs or 'Cleanup error:' in logs}
    payload = run / 'results/payload.json'
    if payload.exists():
        item['payload'] = json.loads(payload.read_text())
        item['outside_payload_seconds'] = wall - item['payload']['payload_seconds']
    item['receipts'] = [json.loads(p.read_text())
                        for p in (run / 'results').glob('managed-overlay-*.json')]
    with (run / 'results/fsck.log').open('w') as stream:
        item['fsck'] = subprocess.run(['/usr/sbin/e2fsck', '-fn', str(run / 'work.ext3')],
            env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=120).returncode
    if rc == 0 and item['fsck'] == 0 and not item['cleanup_warning']:
        verify = ('from pathlib import Path;import subprocess;'
                  'r=Path("/workspace/efficiency/data");'
                  'assert all((r/str(n)).read_bytes()==bytes([n%256])*4096 '
                  'for n in range(4096) if n%3);'
                  'assert all(not (r/str(n)).exists() for n in range(0,4096,3));'
                  'assert subprocess.check_output(["/workspace/efficiency/sum"],'
                  'text=True).strip()=="2080";print("REMOUNT_VERIFIED")')
        args = container_command(str(BASE), ['/usr/bin/python3', '-B', '-c', verify],
                                  overlay=str(run / 'work.ext3') + ':ro', jobs=8)
        with (run / 'results/verify.log').open('w') as stream:
            item['verify_returncode'] = subprocess.run(['/usr/bin/python3', '-B',
                str(CONTROL / 'cp2k_image_driver.py'), str(ROOT), str(run), *args[1:]],
                env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=120).returncode
    item['accepted'] = (rc == 0 and item['fsck'] == 0 and not item['cleanup_warning']
                        and item.get('verify_returncode') == 0 and 'payload' in item
                        and (mode == 'direct' or (len(item['receipts']) == 1
                             and item['receipts'][0].get('accepted') is True
                             and item['receipts'][0].get('forced_stop') is False)))
    record['cases'].append(item)
    summary.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(item), flush=True)
    if not item['accepted']:
        print(logs[-6000:], flush=True)
        raise SystemExit(1)
record['passed'] = all(case['accepted'] for case in record['cases'])
summary.write_text(json.dumps(record, indent=2) + '\n')
