#!/usr/bin/env python3
"""Paired native/SIF CP2K measurements with physical host input/output storage."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import statistics
import subprocess
import sys
import time

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'controller'))
import cp2k_acceptance as science
from source_cache import checksum

PROJECT = Path('/home/stardust/sai-hpc-software')
CASES = {'water-gpw': 5, 'water64-gpw': 3, 'water64-elpa': 3}
VERSION_REPEATS = 7
PARTITIONS = ['16V100', '8V100V0', 'DSPRHBM', '4V100']
MARKER = 'CP2K_CONTAINER_OVERHEAD_PASSED'


def require(value, message):
    if not value:
        raise ValueError(message)


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def signature(path):
    info = path.stat()
    return [info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def schedule(case, repeats):
    modes = ['native', 'sif', 'checked'] if case == 'version' else ['native', 'sif']
    for mode in modes:
        yield 'warmup', mode
    for repeat in range(repeats):
        order = modes[repeat % 3:] + modes[:repeat % 3] if case == 'version' else modes[::1 if repeat % 2 == 0 else -1]
        for mode in order:
            yield str(repeat), mode


def parse(folder, case, reference):
    text = (folder / 'cp2k.log').read_text()
    elapsed = (folder / 'elapsed.txt').read_text()
    if case == 'version':
        version = science.fixtures.parse_version(text)
        require(version == reference['versions']['candidate'], 'Version/features changed')
        return {'elapsed_seconds': float(elapsed), 'version': version}
    result = science.parse_science(text, (folder / 'forces.xyz').read_text(), elapsed, case)
    result['scf_steps'] = [int(n) for n in re.findall(r'SCF run converged in\s+(\d+) steps', text)]
    result['comparison_to_accepted'] = science.compare_science(result, reference['samples'][case + '/single'])
    return result


def summaries(samples):
    result = {}
    for case, repeats in {'version': VERSION_REPEATS, **CASES}.items():
        modes = ['native', 'sif', 'checked'] if case == 'version' else ['native', 'sif']
        records = {mode: [samples[f'{case}/{r}/{mode}'] for r in range(repeats)] for mode in modes}
        result[case] = {'measured_repetitions': repeats, 'modes': {}}
        for mode, rows in records.items():
            wall = [row['elapsed_seconds'] for row in rows]
            entry = {'wall_median_seconds': statistics.median(wall), 'wall_seconds': wall,
                     'wall_min_seconds': min(wall), 'wall_max_seconds': max(wall)}
            if case != 'version':
                compute = [row['cp2k_seconds'] for row in rows]
                entry.update(cp2k_median_seconds=statistics.median(compute), cp2k_seconds=compute,
                             outside_cp2k_timer_median_seconds=statistics.median(a-b for a,b in zip(wall,compute)))
            if mode != 'native':
                base = records['native']
                deltas = [row['elapsed_seconds'] - baseline['elapsed_seconds'] for row,baseline in zip(rows,base)]
                entry.update(paired_wall_delta_seconds=deltas,
                             paired_wall_delta_median_seconds=statistics.median(deltas),
                             wall_median_ratio_to_native=statistics.median(wall)/statistics.median(r['elapsed_seconds'] for r in base))
                if case != 'version':
                    entry['paired_cp2k_delta_median_seconds'] = statistics.median(
                        row['cp2k_seconds']-baseline['cp2k_seconds'] for row,baseline in zip(rows,base))
                    entry['paired_scf_steps_equal'] = all(row['scf_steps']==baseline['scf_steps'] for row,baseline in zip(rows,base))
            result[case]['modes'][mode] = entry
    return result


def check_probes(root, request):
    probes = {mode:json.loads((root/'probes'/mode/'probe.json').read_text()) for mode in ['native','sif']}
    for mode, probe in probes.items():
        host = json.loads((root/'probes'/mode/'host.json').read_text())
        require(probe['uid'] == probe['euid'] == host['uid'] == request['uid'] != 0, 'Must run as the ordinary submitting user')
        require((probe['cwd_dev'],probe['cwd_inode']) == (host['dev'],host['inode']), 'Work directory is not the bound host filesystem')
        require(probe['binary']['sha256'] == request['binary_sha256'], 'Different CP2K executable bytes')
        for name, digest in science.DATA_HASHES.items():
            require(probe['data'][name]['sha256'] == digest, 'Different CP2K data')
        topology = {}
        for line in (root/'results/topology.txt').read_text().splitlines():
            if not line.startswith('#'):
                cpu,core,socket = map(int,line.split(',')); topology[cpu]=(socket,core)
        require(len({topology[cpu] for cpu in probe['affinity']}) == request['threads'], 'Wrong physical-core affinity')
    a,b = probes['native'],probes['sif']
    require(a['host']==b['host'] and a['affinity']==b['affinity'] and a['environment']==b['environment'], 'Different host, affinity or relevant environment')
    require(a['binary']['dev'] != b['binary']['dev'], 'Container is accidentally reading the physical executable')
    require({name:r['sha256'] for name,r in a['libraries'].items()} ==
            {name:r['sha256'] for name,r in b['libraries'].items()}, 'Different dynamically linked library bytes')
    mountinfo = (root/'probes/sif/mountinfo.txt').read_text()
    require('squashfs' in mountinfo or 'squashfuse' in mountinfo, 'No SIF SquashFS mount recorded')
    return probes


def execute(root):
    request = json.loads((root/'request.json').read_text())
    require(os.getuid()==request['uid']!=0 and os.geteuid()==os.getuid(), 'Unexpected executing user')
    require(os.environ['SLURM_JOB_PARTITION']==request['partition'], 'Wrong partition')
    require(os.environ['SAI_CP2K_PREFIX']==request['prefix'], 'Wrong public module prefix')
    require(int(os.environ['OMP_NUM_THREADS'])==request['threads'], 'Wrong OpenMP threads')
    require(signature(Path(request['image']))==request['image_stat'], 'SIF changed after submission verification')
    for name,digest in request['inputs'].items():
        require(checksum(root/name)==digest, 'Frozen experiment changed: '+name)
    reference = json.loads((root/'reference.json').read_text())
    environment = dict(os.environ, SAI_SOFTWARE_ROOT=str(PROJECT), SAI_CP2K_IMAGE=request['image'],
                       SAI_OVERHEAD_IMAGE=request['image'], SAI_CHECKED_LAUNCHER=str(REPO/'controller/cp2k_runtime.sh'))
    rank = Path(__file__).with_name('rank.sh')
    mpi = ['mpirun','-np','1','--map-by',os.environ['MAP_OPT'],'--report-bindings','bash',str(rank)]
    samples = {}

    def invoke(folder,mode,operation,args):
        command = mpi+[mode,operation,*args]
        write(folder/'command.json',command)
        started = time.monotonic()
        with (folder/'cp2k.log').open('x') as log:
            subprocess.run(command,cwd=folder,env=environment,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=1200)
        (folder/'elapsed.txt').write_text(str(time.monotonic()-started)+'\n')

    for repeat,mode in schedule('version',VERSION_REPEATS):
        key = 'version/'+repeat+'/'+mode
        folder = root/'cases'/key; folder.mkdir(parents=True)
        invoke(folder,mode,'cp2k',['--version'])
        samples[key] = parse(folder,'version',reference)
        print(key, samples[key]['elapsed_seconds'], flush=True)
    # Expensive provenance hashing is excluded from both timing samples.
    for mode in ['native','sif']:
        folder = root/'probes'/mode; folder.mkdir(parents=True)
        info = folder.stat()
        write(folder/'host.json',dict(uid=os.getuid(),dev=info.st_dev,inode=info.st_ino))
        shutil.copyfile(Path(__file__).with_name('probe.py'),folder/'probe.py')
        invoke(folder,mode,'probe',[])
    probes = check_probes(root,request)
    print('MATCHED_EXECUTABLE_LIBRARIES_UID_AFFINITY_AND_HOST_FILESYSTEM',flush=True)
    for case,repeats in CASES.items():
        for repeat,mode in schedule(case,repeats):
            key = case+'/'+repeat+'/'+mode
            folder = root/'cases'/key; folder.mkdir(parents=True)
            shutil.copyfile(root/'fixtures'/(case+'.inp'),folder/'input.inp')
            invoke(folder,mode,'cp2k',['-i','input.inp'])
            samples[key] = parse(folder,case,reference)
            samples[key]['comparison_to_native_warmup'] = science.compare_science(samples[key],samples[case+'/warmup/native'])
            print(key,'wall=',samples[key]['elapsed_seconds'],'cp2k=',samples[key]['cp2k_seconds'],flush=True)
    require(signature(Path(request['image']))==request['image_stat'], 'SIF changed during experiment')
    report = dict(passed=True,request=request,job=os.environ['SLURM_JOB_ID'],samples=samples,
                  summary=summaries(samples),probes=probes,
                  cache_scope='First observed invocation plus warmed repetitions; no cache flush, no cold-cache claim',
                  scope='One rank on one node; native versus Apptainer SIF; same physical host work filesystem')
    report['files'] = {str(p.relative_to(root)):checksum(p) for p in root.rglob('*') if p.is_file() and
                       (p.suffix in ['.json','.txt','.tsv','.inp'] or p.name in ['cp2k.log','forces.xyz'])}
    write(root/'results/evidence.json',report)
    (root/'results/passed.txt').write_text(MARKER+'\n')
    print(MARKER,flush=True)


def submit(partition, name):
    require(partition in PARTITIONS and os.getuid()!=0, 'Ordinary site user and supported partition required')
    package = REPO/'deployment/cp2k-2026.2-shared-elpa'
    release = json.loads((package/'release.json').read_text())
    spec = next(row for row in release['deliveries'] if row['partition']==partition)
    prefix = Path('/opt/apps/cp2k')/('cp2k-release-'+spec['build_id'])/partition
    image = PROJECT/'containers/software/cp2k/release'/spec['build_id']/partition/(spec['run_id']+'.sif')
    require(image.is_file() and not image.is_symlink(), 'Use the immutable SIF filename')
    require(checksum(image)==spec['image_sha256'], 'SIF differs from accepted release')
    receipt_path = prefix/'share/sai/deployment.json'
    require(checksum(receipt_path)==spec['receipt_sha256'], 'Installed receipt differs')
    receipt = json.loads(receipt_path.read_text())
    binary_sha = next(row['sha256'] for row in receipt['files'] if row['path']=='bin/cp2k.psmp')
    require(checksum(prefix/'bin/cp2k.psmp')==binary_sha, 'Installed executable changed')
    sidecar = image.with_suffix('.json')
    require(checksum(sidecar)==spec['sidecar_sha256'], 'Accepted sidecar changed')
    proof = json.loads(sidecar.read_text())['cp2k_science']
    reference = PROJECT/'runtime-tests'/proof['run_id']/'results/evidence.json'
    require(checksum(reference)==proof['evidence_sha256']==spec['science_evidence_sha256'], 'Scientific reference changed')
    require(checksum(REPO/'controller/cp2k_acceptance.py')==proof['controller_sha256'] and
            checksum(REPO/'controller/cp2k_benchmark.py')==proof['helpers_sha256'], 'Scientific parser changed')
    root = PROJECT/'runtime-tests'/('cp2k-container-overhead-'+name+'-'+partition)
    require(root.parent==PROJECT/'runtime-tests' and not root.exists(), 'New home run directory required')
    root.mkdir()
    for directory in ['results','fixtures','control/controller','control/deployment/cp2k-container-overhead']:
        (root/directory).mkdir(parents=True)
    for path in (REPO/'controller').glob('*.py'):
        shutil.copyfile(path,root/'control/controller'/path.name)
    shutil.copyfile(REPO/'controller/cp2k_runtime.sh',root/'control/controller/cp2k_runtime.sh')
    for path in Path(__file__).parent.iterdir():
        if path.is_file(): shutil.copyfile(path,root/'control/deployment/cp2k-container-overhead'/path.name)
    shutil.copyfile(reference,root/'reference.json')
    shutil.copyfile(sidecar,root/'sidecar.json')
    shutil.copyfile(receipt_path,root/'receipt.json')
    shutil.copyfile(package/'release.json',root/'release.json')
    for case in CASES:
        source = reference.parent.parent/'cases'/case/'single/input.inp'
        require(checksum(source)==json.loads(reference.read_text())['input_hashes'][case]['input.inp'], 'Input changed')
        shutil.copyfile(source,root/'fixtures'/(case+'.inp'))
    request = dict(schema=1,partition=partition,identity=receipt['identity'],prefix=str(prefix),
                   image=str(image),image_sha256=spec['image_sha256'],image_stat=signature(image),
                   binary_sha256=binary_sha,reference_sha256=proof['evidence_sha256'],
                   uid=os.getuid(),user=pwd.getpwuid(os.getuid()).pw_name,
                   threads=2 if partition=='DSPRHBM' else 3 if partition=='8V100V0' else 4,
                   cases=CASES,version_repeats=VERSION_REPEATS,
                   image_verification='Full image checksum before submission on login node; stat identity checked on compute node before and after timing',
                   inputs={str(p.relative_to(root)):checksum(p) for p in root.rglob('*') if p.is_file()})
    write(root/'request.json',request)
    script = root/'control/deployment/cp2k-container-overhead/job.sbatch'
    command = ['sbatch','--parsable','--partition='+partition,'--job-name=cp2k-container-io',
               '--output='+str(root/'results/slurm-%j.log')]
    command += ['--qos=rush-cpu','--cpus-per-task=2','--mem=8G'] if partition=='DSPRHBM' else [
        '--qos='+('rush-1o2gpu' if partition=='4V100' else 'flood-1o2gpu'),'--gpus-per-node=1']
    job = subprocess.check_output(command+[str(script),str(root)],text=True).strip().split(';')[0]
    require(job.isdigit(),'Invalid submitted job')
    (root/'job.id').write_text(job+'\n')
    write(root/'submission.json',dict(command=command+[str(script),str(root)],job=job,request_sha256=checksum(root/'request.json')))
    print(json.dumps(dict(partition=partition,job=job,run=str(root))),flush=True)


def validate(root):
    request = json.loads((root/'request.json').read_text())
    recorded = json.loads((root/'results/evidence.json').read_text())
    require(recorded['request']==request and recorded['passed'] is True,'Experiment identity changed')
    for name,digest in {**request['inputs'],**recorded['files']}.items():
        require(checksum(root/name)==digest,'Archived input/output changed: '+name)
    require(checksum(root/'reference.json')==request['reference_sha256'],'Reference changed')
    require((root/'results/passed.txt').read_text().strip()==MARKER,'Missing completion marker')
    job = (root/'job.id').read_text().strip()
    fields = next(line.split('|') for line in (root/'results/accounting.txt').read_text().splitlines() if line.startswith(job+'|'))
    require(fields[1]==request['partition'] and fields[2:4]==['COMPLETED','0:0'],'Job did not complete successfully')
    probes = check_probes(root,request)
    require(probes==recorded['probes'],'Recorded provenance differs')
    reference = json.loads((root/'reference.json').read_text())
    samples = {}
    comparisons = []
    for case,repeats in {'version':VERSION_REPEATS,**CASES}.items():
        for repeat,mode in schedule(case,repeats):
            key = case+'/'+repeat+'/'+mode
            folder = root/'cases'/key
            row = (folder/'rank.tsv').read_text().strip().split('\t')
            require(row[0]==str(request['uid']) and row[1]==fields[5] and row[4]==str(request['threads']), 'Wrong rank user/host/threads')
            if request['partition']=='DSPRHBM': require(row[3]=='','Unexpected CPU GPU assignment')
            else: require(row[3] and ',' not in row[3] and row[3]!='-1','Wrong GPU assignment')
            if case!='version':
                require(checksum(folder/'input.inp')==request['inputs']['fixtures/'+case+'.inp'],'Input differs between routes')
            sample = parse(folder,case,reference)
            if case!='version':
                sample['comparison_to_native_warmup'] = science.compare_science(sample,samples[case+'/warmup/native'])
                comparisons.append(sample['comparison_to_native_warmup'])
            require(sample==recorded['samples'][key], 'Recorded numerical sample differs')
            samples[key]=sample
    require(set(samples)==set(recorded['samples']),'Missing or extra samples')
    summary = summaries(samples)
    require(summary==recorded['summary'],'Recorded timings differ')
    return dict(passed=True,partition=request['partition'],job=job,node=fields[5],state=fields[2],exit_code=fields[3],
                elapsed=fields[4],identity=request['identity'],image_sha256=request['image_sha256'],
                uid=request['uid'],user=request['user'],threads=request['threads'],science_calculations=len(comparisons),
                version_invocations=len(samples)-len(comparisons),summary=summary,
                max_energy_delta_ha=max(r['energy_delta_ha'] for r in comparisons),
                max_force_delta_ha_bohr=max(r['max_force_delta_ha_bohr'] for r in comparisons),
                scope=recorded['scope'],cache_scope=recorded['cache_scope'])


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='op',required=True)
    command = commands.add_parser('submit')
    command.add_argument('--partition',choices=PARTITIONS,required=True)
    command.add_argument('--name',required=True)
    for name in ['execute','verify']:
        command=commands.add_parser(name); command.add_argument('run',type=Path)
    args=parser.parse_args()
    if args.op=='submit': submit(args.partition,args.name)
    elif args.op=='execute': execute(args.run)
    else: print(json.dumps(validate(args.run),indent=2,sort_keys=True))
