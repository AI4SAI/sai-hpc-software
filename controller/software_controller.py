#!/usr/bin/env python3
import argparse, hashlib, json, os, pathlib, shutil, subprocess, sys, time

ROOT = pathlib.Path(os.environ.get('SAI_SOFTWARE_ROOT', pathlib.Path.home()/'sai-hpc-software')).resolve()
def run(*args, **kw): return subprocess.run(args, check=True, text=True, **kw)
def safe(name):
    if not name or pathlib.Path(name).name != name or '..' in pathlib.Path(name).parts: raise ValueError('unsafe name')
def init(args):
    safe(args.software); safe(args.run_id)
    for p in (ROOT/'cache'/'repositories'/args.software, ROOT/'runs'/args.run_id/'input', ROOT/'runs'/args.run_id/'results', ROOT/'runs'/args.run_id/'artifacts', ROOT/'controller'):
        p.mkdir(parents=True, exist_ok=True)
def receive(args):
    safe(args.software); safe(args.run_id); runroot=ROOT/'runs'/args.run_id; cache=ROOT/'cache'/'repositories'/args.software; cache.mkdir(parents=True,exist_ok=True)
    if not (cache/'.git').exists(): run('git','init','--bare',str(cache))
    bundle=runroot/'input'/'source.bundle'; run('git','--git-dir',str(cache),'fetch',str(bundle),f'HEAD:refs/ci/{args.sha}')
    # Source is intentionally never extracted on the host.  The read-only
    # bundle is cloned inside the writable Apptainer image by submit().
def submit(args):
    safe(args.run_id); r=ROOT/'runs'/args.run_id; script=r/'job.sbatch'; command=json.loads(args.command)
    quoted=' '.join(subprocess.list2cmdline([x]) for x in command)
    gpu=f'#SBATCH --gpus-per-node={args.gpus}\n' if args.gpus else ''
    script.write_text(f'''#!/bin/bash\n#SBATCH --job-name={args.software}-{args.run_id}\n#SBATCH --partition={args.partition}\n#SBATCH --qos={args.qos}\n#SBATCH --nodes=1\n#SBATCH --ntasks=1\n{gpu}#SBATCH --time={args.timeout}\n#SBATCH --output={r}/results/slurm-%j.log\nset -eo pipefail\nexport LD_LIBRARY_PATH=\"${{LD_LIBRARY_PATH:-}}\"\nsource /etc/profile.d/lmod.sh\nmodule load apptainer/1.4.4\nset -u\napptainer exec --cleanenv --containall --no-home --writable-tmpfs --bind {r}/input/source.bundle:/input/source.bundle:ro --bind {r}/results:/output/results:rw --bind /opt/devtools:/opt/devtools:ro --bind /usr:/usr:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro --bind /opt:/opt:ro --bind /usr:/usr:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro {args.image} /bin/sh -lc 'export PATH=/opt/devtools/cmake/3.31.6/bin:/usr/bin:/bin; mkdir -p /workspace; git clone /input/source.bundle /workspace/source; cd /workspace/source && exec {quoted}'\n'''); script.chmod(0o700)
    result=subprocess.run(['sbatch','--parsable',str(script)],capture_output=True,text=True)
    if result.returncode: print(result.stderr,file=sys.stderr); raise SystemExit(result.returncode)
    job=result.stdout.strip(); (r/'job.id').write_text(job); print(job)
def monitor(args):
    job=(ROOT/'runs'/args.run_id/'job.id').read_text().strip()
    while subprocess.run(['squeue','-h','-j',job],capture_output=True,text=True).stdout.strip(): time.sleep(15)
    state=run('sacct','-X','-n','-o','State','-j',job,capture_output=True).stdout.strip().splitlines()[0].strip(); print(state); return 0 if state.startswith('COMPLETED') else 1
def submit_v2(args):
    safe(args.software); safe(args.run_id)
    r=ROOT/'runs'/args.run_id; script=r/'job.sbatch'; command=json.loads(args.command)
    quoted=' '.join(subprocess.list2cmdline([x]) for x in command)
    gpu=f'#SBATCH --gpus-per-node={args.gpus}' if args.gpus else ''
    sandbox=r/'container.sandbox'; output=r/'artifacts'/f'{args.software}-{args.run_id}.sif'
    cmd=f"apptainer exec --cleanenv --containall --no-home --writable --bind {r}/input/source.bundle:/input/source.bundle:ro --bind {r}/results:/output/results:rw --bind /opt/devtools:/opt/devtools:ro --bind /usr:/usr:ro --bind /lib:/lib:ro --bind /lib64:/lib64:ro {sandbox} /bin/sh -lc 'export PATH=/opt/devtools/nvidia/cuda-12.9.1/bin:/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-avx512/bin:/opt/devtools/cmake/3.31.6/bin:/usr/bin:/bin; export LD_LIBRARY_PATH=/opt/devtools/nvidia/cuda-12.9.1/lib64:/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-avx512/lib:/opt/devtools/libxc/libxc-7.0.0-avx512/lib:/opt/devtools/fftw/3.3.10/lib:${{LD_LIBRARY_PATH:-}}; mkdir -p /workspace; git clone /input/source.bundle /workspace/source; cd /workspace/source && exec {quoted}'"
    lines=['#!/bin/bash', f'#SBATCH --job-name={args.software}-{args.run_id}', f'#SBATCH --partition={args.partition}', f'#SBATCH --qos={args.qos}', '#SBATCH --nodes=1', '#SBATCH --ntasks=1', gpu, f'#SBATCH --time={args.timeout}', f'#SBATCH --output={r}/results/slurm-%j.log', 'set -eo pipefail', 'source /etc/profile.d/lmod.sh', 'module load apptainer/1.4.4', f'rm -rf {sandbox}', f'apptainer build --fakeroot --sandbox {sandbox} {args.image}', cmd, f'apptainer build --fakeroot {output} {sandbox}', f'rm -rf {sandbox}', f'test -s {output}', f'echo ARTIFACT={output}']
    script.write_text('\n'.join(x for x in lines if x) + '\n'); script.chmod(0o700)
    result=subprocess.run(['sbatch','--parsable',str(script)],capture_output=True,text=True)
    if result.returncode: print(result.stderr,file=sys.stderr); raise SystemExit(result.returncode)
    job=result.stdout.strip(); (r/'job.id').write_text(job); print(job)

def main():
    p=argparse.ArgumentParser(); s=p.add_subparsers(dest='op',required=True); a=s.add_parser('init'); a.add_argument('software'); a.add_argument('run_id'); b=s.add_parser('receive'); b.add_argument('software'); b.add_argument('run_id'); b.add_argument('sha'); c=s.add_parser('submit'); c.add_argument('software'); c.add_argument('run_id'); c.add_argument('image'); c.add_argument('partition'); c.add_argument('gpus',type=int); c.add_argument('timeout',type=int); c.add_argument('command'); c.add_argument('qos'); d=s.add_parser('monitor'); d.add_argument('run_id'); x=p.parse_args(); return {'init':init,'receive':receive,'submit':submit_v2,'monitor':monitor}[x.op](x)
if __name__=='__main__': raise SystemExit(main() or 0)
