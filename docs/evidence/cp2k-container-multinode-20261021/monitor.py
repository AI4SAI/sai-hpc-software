#!/usr/bin/env python3
"""Read GPU contexts for only the current benchmark's excluded warmups."""
from concurrent.futures import ThreadPoolExecutor
import datetime
import json
from pathlib import Path
import subprocess

PROJECT = Path('/home/stardust/sai-hpc-software')
OUTPUT = PROJECT/'diagnostics/cp2k-container-multinode-monitor-20261021'
OUTPUT.mkdir(exist_ok=True)
requests = []
run_names = {}
for case in ('water64', 'water216'):
    root = PROJECT/'runtime-tests'/('cp2k-container-multinode-20261021-16V100-2n8g-'+case+'-r2')
    run_names[case] = root.name
    for folder in (root/'cases').glob('*/warmup/*'):
        if (folder/'sample.json').exists():
            continue
        rows = [json.loads(path.read_text()) for path in (folder/'ranks').glob('inside-*.json')]
        if len(rows) == 8:
            for host in sorted({row['host'] for row in rows}):
                requests.append((case, folder.name, host, [row for row in rows if row['host'] == host]))


def collect(item):
    case, mode, host, rows = item
    script = 'import json,os,subprocess,time\nfrom pathlib import Path\nrows=json.loads('+repr(json.dumps(rows))+')\n'
    script += '''processes=[]
for row in rows:
    path=Path('/proc')/str(row['pid'])
    try:
        if path.stat().st_uid != os.getuid(): continue
        processes.append(dict(rank=row['rank'],pid=row['pid'],gpu=row['gpu'],
                              command=(path/'comm').read_text().strip(),
                              status=(path/'status').read_text(),stat=(path/'stat').read_text()))
    except (FileNotFoundError,ProcessLookupError): pass
devices=','.join(row['gpu'] for row in rows)
gpus=subprocess.check_output(['nvidia-smi','-i',devices,'--query-gpu=uuid,utilization.gpu,memory.used,clocks.sm,power.draw','--format=csv,noheader,nounits'],text=True,timeout=15)
clients=subprocess.check_output(['nvidia-smi','-i',devices,'--query-compute-apps=pid,gpu_uuid,used_memory','--format=csv,noheader,nounits'],text=True,timeout=15)
print(json.dumps(dict(uid=os.getuid(),time=time.time(),processes=processes,gpus=gpus,clients=clients)))
'''
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10',
        '-o', 'StrictHostKeyChecking=accept-new', '-o', 'UserKnownHostsFile='+str(OUTPUT/'known_hosts'),
        host, 'python3', '-B', '-'], input=script, text=True, capture_output=True, timeout=50)
    value = dict(case=case, run=run_names[case], mode=mode, host=host, exit_code=result.returncode, stderr=result.stderr)
    if result.returncode == 0:
        value['snapshot'] = json.loads(result.stdout)
    else:
        value['stdout'] = result.stdout
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    (OUTPUT/(case+'-'+mode+'-'+host+'-'+stamp+'.json')).write_text(json.dumps(value, indent=2)+'\n')
    return dict(case=case, mode=mode, host=host, exit_code=result.returncode,
                processes=len(value.get('snapshot', {}).get('processes', [])),
                gpu_snapshot=value.get('snapshot', {}).get('gpus'))


with ThreadPoolExecutor(max_workers=4) as pool:
    for value in pool.map(collect, requests):
        print(json.dumps(value), flush=True)
