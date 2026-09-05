#!/usr/bin/env python3
import argparse, hashlib, json, os, pathlib, shutil, subprocess, sys, time

ROOT = pathlib.Path(os.environ.get('SAI_SOFTWARE_ROOT', pathlib.Path.home()/'sai-hpc-software')).resolve()
def run(*args, **kw): return subprocess.run(args, check=True, text=True, **kw)
def safe(name):
    if not name or pathlib.Path(name).name != name or '..' in pathlib.Path(name).parts: raise ValueError('unsafe name')
def init(args):
    safe(args.software); safe(args.run_id)
    for p in (ROOT/'cache'/'repositories'/args.software, ROOT/'runs'/args.run_id/'input', ROOT/'runs'/args.run_id/'source', ROOT/'runs'/args.run_id/'build', ROOT/'runs'/args.run_id/'install', ROOT/'runs'/args.run_id/'results', ROOT/'runs'/args.run_id/'tmp', ROOT/'controller'):
        p.mkdir(parents=True, exist_ok=True)
def receive(args):
    safe(args.software); safe(args.run_id); runroot=ROOT/'runs'/args.run_id; cache=ROOT/'cache'/'repositories'/args.software; cache.mkdir(parents=True,exist_ok=True)
    if not (cache/'.git').exists(): run('git','init','--bare',str(cache))
    bundle=runroot/'input'/'source.bundle'; run('git','--git-dir',str(cache),'fetch',str(bundle),f'HEAD:refs/ci/{args.sha}')
    runroot.joinpath('source').mkdir(exist_ok=True); run('git','--git-dir',str(cache),'archive',args.sha,'-o',str(runroot/'source.tar')); run('tar','-xf',str(runroot/'source.tar'),'-C',str(runroot/'source')); (runroot/'source.tar').unlink()
def submit(args):
    safe(args.run_id); r=ROOT/'runs'/args.run_id; script=r/'job.sbatch'; command=json.loads(args.command)
    quoted=' '.join(subprocess.list2cmdline([x]) for x in command)
    gpu=f'#SBATCH --gpus-per-node={args.gpus}\n' if args.gpus else ''
    script.write_text(f'''#!/bin/bash\n#SBATCH --job-name={args.software}-{args.run_id}\n#SBATCH --partition={args.partition}\n#SBATCH --qos={args.qos}\n#SBATCH --nodes=1\n#SBATCH --ntasks=1\n{gpu}#SBATCH --time={args.timeout}\n#SBATCH --output={r}/results/slurm-%j.log\nset -eo pipefail\nexport LD_LIBRARY_PATH=\"${{LD_LIBRARY_PATH:-}}\"\nsource /etc/profile.d/lmod.sh\nmodule load apptainer/1.4.4\nset -u\napptainer exec --cleanenv --containall --no-home --bind {r}/source:/workspace/source:ro --bind {r}/build:/workspace/build:rw --bind {r}/install:/workspace/install:rw --bind {r}/results:/workspace/results:rw --bind {r}/tmp:/tmp:rw --bind /opt:/opt:ro --bind /usr:/usr:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro {args.image} /bin/sh -lc 'cd /workspace/source && exec {quoted}'\n'''); script.chmod(0o700)
    result=subprocess.run(['sbatch','--parsable',str(script)],capture_output=True,text=True)
    if result.returncode: print(result.stderr,file=sys.stderr); raise SystemExit(result.returncode)
    job=result.stdout.strip(); (r/'job.id').write_text(job); print(job)
def monitor(args):
    job=(ROOT/'runs'/args.run_id/'job.id').read_text().strip()
    while subprocess.run(['squeue','-h','-j',job],capture_output=True,text=True).stdout.strip(): time.sleep(15)
    state=run('sacct','-X','-n','-o','State','-j',job,capture_output=True).stdout.strip().splitlines()[0].strip(); print(state); return 0 if state.startswith('COMPLETED') else 1
def main():
    p=argparse.ArgumentParser(); s=p.add_subparsers(dest='op',required=True); a=s.add_parser('init'); a.add_argument('software'); a.add_argument('run_id'); b=s.add_parser('receive'); b.add_argument('software'); b.add_argument('run_id'); b.add_argument('sha'); c=s.add_parser('submit'); c.add_argument('software'); c.add_argument('run_id'); c.add_argument('image'); c.add_argument('partition'); c.add_argument('gpus',type=int); c.add_argument('timeout',type=int); c.add_argument('command'); c.add_argument('qos'); d=s.add_parser('monitor'); d.add_argument('run_id'); x=p.parse_args(); return {'init':init,'receive':receive,'submit':submit,'monitor':monitor}[x.op](x)
if __name__=='__main__': raise SystemExit(main() or 0)
