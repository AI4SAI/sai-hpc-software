#!/usr/bin/env python3
"""Paired two-node, four-GPU-per-node native/SIF OT measurements."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time

sys.dont_write_bytecode = True
import run as common

REPEATS = 5
CASES = ('water64-gpw', 'water216-ot')
MARKER = 'CP2K_MULTINODE_CONTAINER_OVERHEAD_PASSED'
require, write, checksum = common.require, common.write, common.checksum


def check_ranks(folder, request, inventory):
    expected = {f'inside-{rank}.json' for rank in range(8)}
    require({p.name for p in (folder/'ranks').glob('inside-*.json')} == expected, 'Missing or extra MPI ranks')
    records, per_host, cores = {}, {}, {}
    for rank in range(8):
        a = json.loads((folder/f'ranks/host-{rank}.json').read_text())
        b = json.loads((folder/f'ranks/inside-{rank}.json').read_text())
        require(a == {key: b[key] for key in a}, 'Host/container rank context differs')
        require(b['uid'] == b['euid'] == request['uid'] != 0 and b['rank'] == rank and b['size'] == 8,
                'Wrong MPI rank or ordinary user')
        host, local = b['host'], b['local_rank']
        require(host in inventory and 0 <= local < 4, 'Wrong rank host/local rank')
        require(b['gpu'] == inventory[host]['devices'][local]['uuid'], 'Wrong allocated GPU mapping')
        require(b['binary'] == request['prefix']+'/bin/cp2k.psmp', 'Wrong executable prefix')
        require(b['environment']['OMP_NUM_THREADS'] == '4' and
                b['environment']['OPENBLAS_NUM_THREADS'] == b['environment']['MKL_NUM_THREADS'] == '1',
                'Wrong OpenMP/BLAS threads')
        actual = {tuple(pair) for pair in b['topology'].values()}
        require(set(map(str, b['affinity'])) == set(b['topology']) and len(actual) == 4,
                'Wrong physical-core affinity')
        require(not cores.setdefault(host, set()) & actual, 'Overlapping physical cores')
        cores[host].update(actual)
        per_host.setdefault(host, []).append((local, b['gpu']))
        records[str(rank)] = b
    require(set(per_host) == set(inventory) and len(per_host) == 2, 'Wrong node distribution')
    for rows in per_host.values():
        require(sorted(local for local, gpu in rows) == list(range(4)) and
                len({gpu for local, gpu in rows}) == 4, 'Duplicate rank/GPU mapping')
    return records


def check_probes(root, request, inventory):
    placements = {mode: check_ranks(root/'probes'/mode, request, inventory) for mode in ('native', 'sif')}
    libraries = {}
    for rank in range(8):
        probes = {mode: json.loads((root/f'probes/{mode}/ranks/probe-{rank}.json').read_text())
                  for mode in ('native', 'sif')}
        a, b = probes['native'], probes['sif']
        for key in ('host', 'rank', 'local_rank', 'affinity', 'topology', 'gpu', 'environment'):
            require(a[key] == b[key], 'Native/SIF probe placement differs: '+key)
        require(a['binary_dev'] != b['binary_dev'], 'SIF accidentally reads physical executable')
        for value in probes.values():
            require(value['binary_sha256'] == request['binary_sha256'], 'Executable bytes changed')
            require(len(value['devices']) == 1 and value['devices'][0]['uuid'] == value['gpu'],
                    'CUDA driver does not see exactly the selected GPU')
        if a['local_rank'] == 0:
            for value in probes.values():
                require(value['data'] == common.science.DATA_HASHES, 'Scientific data bytes changed')
            digests = lambda probe: {name: row['sha256'] for name, row in probe['libraries'].items()}
            require(digests(a) == digests(b), 'Native/SIF dynamically linked libraries differ')
            libraries[a['host']] = digests(a)
            mount = (root/f'probes/sif/ranks/mountinfo-{rank}.txt').read_text()
            require('squashfs' in mount or 'squashfuse' in mount, 'Missing actual SIF mount')
    require(len(libraries) == 2 and len({json.dumps(v, sort_keys=True) for v in libraries.values()}) == 1,
            'Shared library bytes differ between allocated nodes')
    return dict(placements=placements, libraries=libraries)


def parse(folder, case, reference):
    if case == 'water64-gpw':
        result = common.parse(folder, case, reference)
    else:
        path = common.REPO/'deployment/cp2k-diagonalization/run.py'
        spec = importlib.util.spec_from_file_location('multinode_ot_parser', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = module.parse_sample(folder, 'ot-elpa', 6)
        result['comparison_to_accepted'] = common.science.compare_science(
            result, reference['samples']['warmup-ot-elpa'])
    log = (folder/'cp2k.log').read_text()
    require(re.search(r'Total number of message passing processes\s+8\b', log), 'CP2K did not use eight ranks')
    require(re.search(r'Number of threads for this process\s+4\b', log), 'CP2K did not use four threads')
    times = [(folder/f'ranks/time-{rank}.txt').read_text().split() for rank in range(8)]
    require(all(len(row) == 4 and float(row[0]) > 0 for row in times), 'Missing rank timing')
    result['rank_wall_seconds'] = [float(row[0]) for row in times]
    result['max_rank_wall_seconds'] = max(result['rank_wall_seconds'])
    return result


def summaries(samples, cases=CASES):
    result = {}
    for case in cases:
        entry = {}
        for metric in ('elapsed_seconds', 'cp2k_seconds', 'max_rank_wall_seconds'):
            values = {mode: [samples[f'{case}/{i}/{mode}'][metric] for i in range(REPEATS)]
                      for mode in ('native', 'sif')}
            deltas = [b-a for a, b in zip(values['native'], values['sif'])]
            medians = {mode: statistics.median(rows) for mode, rows in values.items()}
            entry[metric] = dict(values=values, medians=medians,
                ratio_of_medians=medians['sif']/medians['native'], paired_deltas=deltas,
                paired_delta_median=statistics.median(deltas), paired_delta_mean=statistics.mean(deltas))
        result[case] = entry
    return result


def same_placement(actual, baseline):
    for rank, record in actual.items():
        for key in ('host', 'rank', 'local_rank', 'affinity', 'topology', 'gpu', 'environment'):
            require(record[key] == baseline[rank][key], 'Rank placement changed between samples: '+key)


def check_binary_origin(actual, native, mode):
    # Every apptainer exec may allocate a different SquashFS device number.
    for rank, record in actual.items():
        physical = record['binary_dev'] == native[rank]['binary_dev']
        require(physical == (mode == 'native'), 'Executable is on the wrong backing filesystem')


def inventories(root, request):
    hosts = (root/'results/nodes.txt').read_text().split()
    require(len(hosts) == len(set(hosts)) == 2, 'Expected exactly two allocated nodes')
    result = {host: json.loads((root/'inventory'/(host+'.json')).read_text()) for host in hosts}
    for host, row in result.items():
        require(row['host'] == host and row['uid'] == row['euid'] == request['uid'] != 0, 'Wrong inventory user/host')
        require(len(row['devices']) == len({v['uuid'] for v in row['devices']}) == 4, 'Expected four allocated GPUs per node')
    require(len({d['uuid'] for row in result.values() for d in row['devices']}) == 8, 'GPU UUIDs repeat across nodes')
    return result


def execute(root):
    request = json.loads((root/'request.json').read_text())
    cases = request['cases']
    require(cases and set(cases) <= set(CASES), 'Unsupported workload selection')
    require(os.getuid() == os.geteuid() == request['uid'] != 0, 'Unexpected executing user')
    require(os.environ['SLURM_JOB_PARTITION'] == '16V100' and os.environ['SAI_CP2K_PREFIX'] == request['prefix'],
            'Wrong partition or public module')
    require(common.signature(Path(request['image'])) == request['image_stat'], 'SIF changed')
    for name, digest in request['inputs'].items():
        require(checksum(root/name) == digest, 'Frozen experiment changed: '+name)
    inventory = inventories(root, request)
    environment = dict(os.environ, SAI_MULTI_IMAGE=request['image'], SAI_MULTI_PREFIX=request['prefix'],
                       SAI_MULTI_INVENTORY=str(root/'inventory'))
    # Use Slurm's slots; an unqualified --host list would cap each node at one.
    mpi = ['mpirun', '-np', '8', '--map-by', 'ppr:4:node:pe=4',
           '--bind-to', 'core', '--report-bindings']
    references = {case: json.loads((root/name).read_text()) for case, name in
                  [('water64-gpw', 'reference.json'), ('water216-ot', 'reference216.json')]}

    def invoke(folder, mode, operation, args, input_file=None):
        folder.mkdir(parents=True)
        (folder/'ranks').mkdir()
        if input_file is not None:
            shutil.copyfile(input_file, folder/'input.inp')
        shutil.copyfile(Path(__file__).with_name('multinode_rank.py'), folder/'entry.py')
        command = mpi + ['/usr/bin/python3', str(folder/'entry.py'), 'host', mode, operation, *args]
        write(folder/'command.json', command)
        print('START', str(folder.relative_to(root)), flush=True)
        started = time.monotonic()
        with (folder/'cp2k.log').open('x') as log:
            subprocess.run(command, cwd=folder, env=environment, stdout=log,
                           stderr=subprocess.STDOUT, check=True, timeout=1800)
        (folder/'elapsed.txt').write_text(str(time.monotonic()-started)+'\n')

    for mode in ('native', 'sif'):
        invoke(root/'probes'/mode, mode, 'probe', [])
    probes = check_probes(root, request, inventory)
    print('MATCHED_TWO_NODES_EIGHT_GPUS_EXECUTABLE_LIBRARIES_AND_HOST_FILESYSTEM', flush=True)
    samples, placements = {}, {}
    for case in cases:
        for repeat, mode in common.schedule(case, REPEATS):
            key = f'{case}/{repeat}/{mode}'
            folder = root/'cases'/key
            invoke(folder, mode, 'cp2k', ['-i', 'input.inp'], root/'fixtures'/(case+'.inp'))
            placements[key] = check_ranks(folder, request, inventory)
            same_placement(placements[key], probes['placements'][mode])
            check_binary_origin(placements[key], probes['placements']['native'], mode)
            sample = parse(folder, case, references[case])
            samples[key] = sample
            sample['comparison_to_native_warmup'] = common.science.compare_science(sample, samples[f'{case}/warmup/native'])
            require(sample['scf_steps'] == samples[f'{case}/warmup/native']['scf_steps'], 'SCF work differs between routes')
            write(folder/'sample.json', sample)
            print('DONE', key, 'wall=', sample['elapsed_seconds'], 'cp2k=', sample['cp2k_seconds'], flush=True)
    require(common.signature(Path(request['image'])) == request['image_stat'], 'SIF changed during experiment')
    result = dict(passed=True, request=request, inventory=inventory, probes=probes,
                  samples=samples, placements=placements, summary=summaries(samples, cases))
    result['files'] = {str(p.relative_to(root)): checksum(p) for p in root.rglob('*') if p.is_file() and
        (p.suffix in ('.json', '.txt', '.inp', '.py', '.xml') or p.name in ('cp2k.log', 'forces.xyz'))}
    write(root/'results/evidence.json', result)
    (root/'results/passed.txt').write_text(MARKER+'\n')
    print(MARKER, flush=True)


def submit(parent, ot_parent, name, cases=CASES, exclude=None):
    request = json.loads((parent/'request.json').read_text())
    require(request['partition'] == '16V100' and os.getuid() == request['uid'] != 0, 'Use the accepted ordinary-user 16V100 parent')
    require((parent/'results/passed.txt').read_text().strip() == common.MARKER, 'Parent benchmark did not pass')
    require(checksum(Path(request['image'])) == request['image_sha256'], 'SIF checksum changed')
    require(checksum(Path(request['prefix'])/'bin/cp2k.psmp') == request['binary_sha256'], 'Physical executable changed')
    require(checksum(parent/'reference.json') == request['reference_sha256'], 'Accepted reference changed')
    ot_request = json.loads((ot_parent/'request.json').read_text())
    ot_result = json.loads((ot_parent/'results/evidence.json').read_text())
    require(ot_request['artifact_sha256'] == request['image_sha256'] and
            ot_request['identity'] == request['identity'] and ot_result['request'] == ot_request and
            (ot_parent/'results/passed.txt').read_text().strip() == 'CP2K_OT_BACKEND_COMPARISON_PASS',
            '216-water release/reference differs')
    require(checksum(ot_parent/'fixtures/ot-elpa.inp') == ot_request['inputs']['fixtures/ot-elpa.inp'], '216-water input changed')
    require(checksum(parent/'fixtures/water64-gpw.inp') == request['inputs']['fixtures/water64-gpw.inp'], '64-water input changed')
    root = common.PROJECT/'runtime-tests'/('cp2k-container-multinode-'+name)
    require(root.parent == common.PROJECT/'runtime-tests' and not root.exists(), 'New home run directory required')
    root.mkdir()
    shutil.copytree(parent/'control', root/'control', ignore=shutil.ignore_patterns('__pycache__'))
    destination = root/'control/deployment/cp2k-container-overhead'
    for path in Path(__file__).parent.glob('*'):
        if path.is_file():
            shutil.copyfile(path, destination/path.name)
    tuning = root/'control/deployment/cp2k-diagonalization'
    tuning.mkdir()
    shutil.copyfile(ot_parent/'control/deployment/cp2k-diagonalization/run.py', tuning/'run.py')
    for directory in ('results', 'fixtures', 'inventory'):
        (root/directory).mkdir()
    for filename in ('reference.json', 'receipt.json', 'release.json', 'sidecar.json'):
        shutil.copyfile(parent/filename, root/filename)
    shutil.copyfile(ot_parent/'results/evidence.json', root/'reference216.json')
    shutil.copyfile(ot_parent/'request.json', root/'reference216-request.json')
    shutil.copyfile(parent/'fixtures/water64-gpw.inp', root/'fixtures/water64-gpw.inp')
    shutil.copyfile(ot_parent/'fixtures/ot-elpa.inp', root/'fixtures/water216-ot.inp')
    request.update(profile='two-node-four-gpus-ot', parent_run=str(parent), ot_parent_run=str(ot_parent),
                   nodes=2, ranks=8, ranks_per_node=4, gpus_per_node=4, threads=4,
                   cases=list(cases), paired_repeats=REPEATS, image_stat=common.signature(Path(request['image'])),
                   scope='Two nodes, four MPI ranks/GPUs per node, four physical cores per rank; physical host home I/O',
                   inputs={str(p.relative_to(root)): checksum(p) for p in root.rglob('*') if p.is_file()})
    request.pop('version_repeats', None)
    write(root/'request.json', request)
    command = ['sbatch', '--parsable', '--partition=16V100', '--qos=flood-gpu',
               '--job-name=cp2k-sif-2n8g-'+('both' if len(cases) == 2 else cases[0]),
               '--output='+str(root/'results/slurm-%j.log')]
    if exclude:
        require(re.fullmatch(r'16v100n\d+(?:,16v100n\d+)*', exclude), 'Invalid excluded 16V100 node list')
        command.append('--exclude='+exclude)
    command += [str(destination/'multinode.sbatch'), str(root)]
    job = subprocess.check_output(command, text=True).strip().split(';')[0]
    require(job.isdigit(), 'Invalid submitted job')
    (root/'job.id').write_text(job+'\n')
    write(root/'submission.json', dict(command=command, job=job, request_sha256=checksum(root/'request.json')))
    print(json.dumps(dict(job=job, run=str(root))), flush=True)


def verify(root):
    request = json.loads((root/'request.json').read_text())
    cases = request['cases']
    require(cases and set(cases) <= set(CASES), 'Unsupported workload selection')
    recorded = json.loads((root/'results/evidence.json').read_text())
    require(recorded['passed'] and recorded['request'] == request, 'Changed request or incomplete experiment')
    require((root/'results/passed.txt').read_text().strip() == MARKER, 'Missing completion marker')
    for name, digest in {**request['inputs'], **recorded['files']}.items():
        require(checksum(root/name) == digest, 'Archived evidence changed: '+name)
    job = (root/'job.id').read_text().strip()
    row = next(line.split('|') for line in (root/'results/accounting.txt').read_text().splitlines() if line.startswith(job+'|'))
    require(row[1:4] == ['16V100', 'COMPLETED', '0:0'], 'Job did not complete successfully')
    inventory = inventories(root, request)
    require(inventory == recorded['inventory'], 'Changed node/GPU inventory')
    require(check_probes(root, request, inventory) == recorded['probes'], 'Changed provenance')
    references = {case: json.loads((root/name).read_text()) for case, name in
                  [('water64-gpw', 'reference.json'), ('water216-ot', 'reference216.json')]}
    samples = {}
    for case in cases:
        for repeat, mode in common.schedule(case, REPEATS):
            key = f'{case}/{repeat}/{mode}'
            folder = root/'cases'/key
            require(checksum(folder/'input.inp') == request['inputs'][f'fixtures/{case}.inp'], 'Different scientific input')
            require(check_ranks(folder, request, inventory) == recorded['placements'][key], 'Changed rank placement')
            same_placement(recorded['placements'][key], recorded['probes']['placements'][mode])
            check_binary_origin(recorded['placements'][key], recorded['probes']['placements']['native'], mode)
            sample = parse(folder, case, references[case]); samples[key] = sample
            sample['comparison_to_native_warmup'] = common.science.compare_science(sample, samples[f'{case}/warmup/native'])
            require(sample['scf_steps'] == samples[f'{case}/warmup/native']['scf_steps'], 'Different SCF work')
            require(sample == recorded['samples'][key], 'Raw scientific/timing reparse differs')
    require(set(samples) == set(recorded['samples']) and summaries(samples, cases) == recorded['summary'], 'Changed timing summary')
    return dict(passed=True, job=job, partition=row[1], state=row[2], exit_code=row[3], elapsed=row[4],
                nodes=sorted(inventory), ranks=8, gpus_per_node=4, uid=request['uid'], user=request['user'],
                science_calculations=len(samples), summary=summaries(samples, cases),
                max_energy_delta_ha=max(v['comparison_to_native_warmup']['energy_delta_ha'] for v in samples.values()),
                max_force_delta_ha_bohr=max(v['comparison_to_native_warmup']['max_force_delta_ha_bohr'] for v in samples.values()))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('op', choices=['submit', 'execute', 'verify'])
    parser.add_argument('run', type=Path)
    parser.add_argument('--ot-parent', type=Path)
    parser.add_argument('--name')
    parser.add_argument('--case', choices=CASES)
    parser.add_argument('--exclude', help='Comma-separated 16V100 nodes used by another benchmark')
    args = parser.parse_args()
    if args.op == 'submit':
        require(args.ot_parent is not None and args.name, 'Submission requires both reference parents and a unique name')
        submit(args.run, args.ot_parent, args.name, (args.case,) if args.case else CASES, args.exclude)
    elif args.op == 'execute':
        execute(args.run)
    else:
        print(json.dumps(verify(args.run), indent=2, sort_keys=True))
