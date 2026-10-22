#!/usr/bin/env python3
"""Independently hash and reparse the retained multi-node benchmark evidence."""
import hashlib
import csv
import json
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
RAW = HERE/'raw'


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def special(row):
    root = RAW/row['label']
    job = (root/'job.id').read_text().strip()
    fields = next(line.split('|') for line in (root/'results/accounting.txt').read_text().splitlines()
                  if line.startswith(job+'|'))
    assert job == row['job'] and fields[1] == '16V100' and fields[2].split()[0] == row['state']
    request = json.loads((root/'request.json').read_text())
    assert request['user'] == 'stardust' and request['uid'] != 0
    for name, digest in request['inputs'].items():
        assert checksum(root/name) == digest, name
    result = dict(job=job, state=fields[2], exit_code=fields[3], elapsed=fields[4], nodes=fields[5],
                  excluded_from_measured_statistics=True)
    if row['kind'] == 'inventory-preflight':
        assert 'AssertionError' in (root/'results/inventory.log').read_text()
        assert not (root/'cases').exists()
        result['reason'] = 'GPU inventory count mismatch before any CP2K calculation'
    elif row['kind'] == 'slots-preflight':
        assert 'not enough slots' in (root/'probes/native/cp2k.log').read_text()
        assert not (root/'cases').exists()
        result['reason'] = 'Unqualified MPI host list restricted slots before any CP2K calculation'
    else:
        assert row['kind'] in ('pilot', 'mount-check')
        sys.path.insert(0, str(root/'control/deployment/cp2k-container-overhead'))
        import multinode as multi
        inventory = multi.inventories(root, request)
        probes = multi.check_probes(root, request, inventory)
        case = request['cases'][0]
        reference = json.loads((root/('reference.json' if case == 'water64-gpw' else 'reference216.json')).read_text())
        samples = {}
        modes = ['native'] if row['kind'] == 'pilot' else ['native', 'sif']
        for mode in modes:
            folder = root/'cases'/case/'warmup'/mode
            placements = multi.check_ranks(folder, request, inventory)
            for rank, record in placements.items():
                for key in ('host', 'rank', 'local_rank', 'affinity', 'topology', 'gpu', 'environment'):
                    assert record[key] == probes['placements'][mode][rank][key]
                assert (record['binary_dev'] == probes['placements']['native'][rank]['binary_dev']) == (mode == 'native')
            sample = multi.parse(folder, case, reference); samples[mode] = sample
            sample['comparison_to_native_warmup'] = multi.common.science.compare_science(sample, samples['native'])
            if (folder/'sample.json').exists():
                assert sample == json.loads((folder/'sample.json').read_text())
        result['warmups'] = samples
        if row['kind'] == 'pilot':
            result['reason'] = json.loads((root/'results/stop-reason.json').read_text())['reason']
        else:
            assert 'Rank placement changed between samples: binary_dev' in (root/f'results/slurm-{job}.log').read_text()
            result['reason'] = 'Both warmups finished; overly strict verifier rejected a legitimate new SIF mount device number'
    return result


def timers(root):
    result = {}
    names = {'CP2K', 'write_mo_set_to_restart', 'mp_waitall', 'mp_allreduce', 'mp_alltoall',
             'cp_fm_diag_elpa', 'cp_fm_diag_elpa_base', 'cp_fm_diag_cusolver',
             'cp_fm_diag_cusolvermp', 'ot_scf_mini', 'grid_collocate_task_list',
             'grid_integrate_task_list', 'dbcsr_multiply_generic'}
    for path in sorted((root/'cases').glob('*/*/*/cp2k.log')):
        log = path.read_text()
        assert 'T I M I N G' in log
        log = log[log.rfind('T I M I N G'):]
        rows = re.findall(r'^\s*(\S+)\s+(\d+)\s+[\d.]+\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)(?:\s+\d+)?\s*$', log, re.M)
        result[str(path.parent.relative_to(root/'cases'))] = {
            name: dict(calls_max=int(calls), self_max_seconds=float(self_max), total_max_seconds=float(total_max))
            for name, calls, self_avg, self_max, total_avg, total_max in rows if name in names}
    return result


def scientific_context(root):
    """Check the whole OT iteration count, including the first outer cycle."""
    contexts = {}
    for path in sorted((root/'cases').glob('*/*/*/cp2k.log')):
        log = path.read_text()
        backend = re.search(r'GLOBAL\| Diagonalization library\s+(\S+)', log).group(1)
        outer = [tuple(map(int, row)) for row in re.findall(
            r'outer SCF loop converged in\s+(\d+) iterations or\s+(\d+) steps', log)]
        assert outer, str(path)
        contexts[str(path.parent.relative_to(root/'cases'))] = dict(
            diagonalization_library=backend, outer_convergence=outer,
            electrons=int(re.search(r'Number of electrons:\s+(\d+)', log).group(1)),
            orbital_functions=int(re.search(r'Number of orbital functions:\s+(\d+)', log).group(1)))
    assert len({json.dumps(v, sort_keys=True) for v in contexts.values()}) == 1
    return next(iter(contexts.values()))


manifest = json.loads((RAW/'manifest.json').read_text())
if len(sys.argv) == 2:
    print(json.dumps(special(next(row for row in manifest['runs'] if row['label'] == sys.argv[1])), sort_keys=True))
else:
    hashes = json.loads((RAW/'SHA256.json').read_text())
    assert {str(p.relative_to(RAW)) for p in RAW.rglob('*') if p.is_file()} == set(hashes) | {'SHA256.json'}
    for name, digest in hashes.items():
        assert checksum(RAW/name) == digest, name
    reconstructed = json.loads((RAW/'reconstructed-topology.json').read_text())
    for name, row in reconstructed.items():
        label, filename = name.split('/')
        run = next(item for item in manifest['runs'] if item['label'] == label)
        assert filename == '.nccl_topo_'+run['job']+'.xml'
        assert row['source'] == label+'/results/nccl-topology.xml'
        original = json.loads((RAW/label/'results/evidence.json').read_text())
        assert checksum(RAW/name) == checksum(RAW/row['source']) == row['sha256'] == original['files'][filename]
    results = {}
    for row in manifest['runs']:
        root = RAW/row['label']
        command = ([sys.executable, '-B', str(root/'control/deployment/cp2k-container-overhead/multinode.py'), 'verify', str(root)]
                   if row['kind'] == 'science' else [sys.executable, '-B', __file__, row['label']])
        result = json.loads(subprocess.check_output(command, text=True))
        assert result['job'] == row['job'] and result['state'].split()[0] == row['state']
        if row['kind'] == 'science':
            result['routine_timings'] = timers(root)
            result['scientific_context'] = scientific_context(root)
        results[row['label']] = result
    assert not set(results['water64']['nodes']) & set(results['water216']['nodes'])
    monitoring = []
    for path in sorted((RAW/'monitoring').glob('*.json')):
        value = json.loads(path.read_text())
        entry = dict(file=str(path.relative_to(RAW)), case=value['case'], mode=value['mode'],
                     host=value['host'], exit_code=value['exit_code'])
        if value['exit_code'] == 0:
            run_name = value.get('run', 'cp2k-container-multinode-20261021-16V100-2n8g-'+value['case'])
            source = next(row for row in manifest['runs'] if row['run'] == run_name)
            root = RAW/source['label']
            request = json.loads((root/'request.json').read_text())
            assert value['snapshot']['uid'] == request['uid'] != 0
            folder = root/'cases'/request['cases'][0]/'warmup'/value['mode']
            ranks = [json.loads(p.read_text()) for p in (folder/'ranks').glob('inside-*.json')]
            expected = {r['pid']: r['gpu'] for r in ranks if r['host'] == value['host']}
            actual = {r['pid']: r['gpu'] for r in value['snapshot']['processes'] if r['command'] == 'cp2k.psmp'}
            assert len(expected) == 4 and actual == expected
            clients = {(int(row[0].strip()), row[1].strip()) for row in csv.reader(value['snapshot']['clients'].splitlines())}
            assert set(expected.items()) <= clients
            entry['matched_cp2k_gpu_contexts'] = 4
        monitoring.append(entry)
    print(json.dumps(dict(passed=True, files_hashed=len(hashes), runs=results,
                         reconstructed_topology=reconstructed,
                         monitoring=monitoring,
                         scope='Two independent two-node allocations; eight V100s per workload; raw numerical/resource revalidation'),
                     indent=2, sort_keys=True))
