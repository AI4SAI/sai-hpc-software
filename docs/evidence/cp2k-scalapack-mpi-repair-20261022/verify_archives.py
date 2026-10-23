#!/usr/bin/env python3
"""Reparse archived MPI/physics/timing evidence without cluster access.

This verifies recorded execution evidence, not the current live installations.
The cluster verifier separately checked the actual executable/library hashes.
"""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import re
import statistics
import sys
import tarfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO/'deployment/cp2k-water-scaling'))
import run as water
spec = importlib.util.spec_from_file_location('mpi_retest', REPO/'deployment/cp2k-water-scaling/retest-mpi.py')
retest = importlib.util.module_from_spec(spec); spec.loader.exec_module(retest)


def verify(archive, work):
    checksum = water.digest(archive)
    root = work/archive.name.removesuffix('.tar.gz')
    if not root.exists():
        root.mkdir(parents=True)
        with tarfile.open(archive) as stream:
            stream.extractall(root, filter='data')
        (root/'archive.sha256').write_text(checksum)
    assert (root/'archive.sha256').read_text() == checksum
    request = json.loads((root/'request.json').read_text())
    proof = json.loads((root/'results/verified.json').read_text())
    assert proof['accepted'] and not proof['scheduler_pending']
    assert proof['request_sha256'] == water.digest(root/'request.json')
    job = (root/'job.id').read_text().strip()
    assert (root/'results/accounting.txt').read_text().strip().split('|')[:3] == [job,'COMPLETED','0:0']
    for name,value in {**request['inputs'], **proof['files']}.items():
        assert water.digest(root/name) == value, name
    assert {str(p.relative_to(root)) for p in (root/'cases').rglob('*') if p.is_file()} == set(proof['files'])
    for name,value in water.fixtures.DATA_HASHES.items():
        assert water.digest(root/'fixtures'/name) == value
    samples, peers, traces = {}, {}, {}
    for name,variant,tracing,shape,ranks,threads in retest.schedule(request):
        case=root/'cases'/name
        assert (case/'input.inp').read_text() == water.input_text('cpu',shape)
        records=water.verify_ranks(case,'cpu',request['gpus'],ranks,4*request['gpus']//ranks)
        assert all(r['omp_threads']==threads and r['binary']==str(retest.binaries(request)[variant]) for r in records)
        resources=[json.loads(row) for row in (case/'resources.jsonl').read_text().splitlines()]
        pids={r['pid'] for r in records}
        assert not any(p['pid'] in pids for row in resources for p in row['gpu_processes'])
        water.fixtures.CASES['scaling']=dict(elements=['O','H','H']*math.prod(shape))
        log=(case/'cp2k.log').read_text()
        sample=water.fixtures.parse_science(log,(case/'forces.xyz').read_text(),(case/'elapsed.txt').read_text(),'scaling')
        sample['syevd_seconds']=water.timers(log,'cpu')['cp_fm_syevd']['seconds']
        sample['scf_steps']=[int(x) for x in re.findall(r'SCF run converged in\s+(\d+) steps',log)]
        samples[name]=sample
        peers.setdefault(tuple(shape),[]).append(sample)
        if tracing:
            traces[name]=retest.trace_proof(case,ranks,23*math.prod(shape),max(1,ranks//2) if variant=='before' else ranks)
        else:
            assert not list(case.glob('scalapack-*.jsonl'))
    assert traces==proof['mpi']
    comparisons=[water.fixtures.compare_science(sample,group[0]) for group in peers.values() for sample in group]
    medians={v:statistics.median(samples[f'pair-{i}-{v}']['cp2k_seconds'] for i in (1,2,3)) for v in ('before','after')}
    solver={v:statistics.median(samples[f'pair-{i}-{v}']['syevd_seconds'] for i in (1,2,3)) for v in ('before','after')}
    assert medians==proof['median_seconds'] and solver==proof['syevd_median_seconds']
    assert medians['before']/medians['after']==proof['before_over_after']
    return dict(archive=archive.name,archive_sha256=checksum,job=job,partition=request['partition'],
        physical_cores=request['physical_cores'],timing_waters=math.prod(request['shape']),cases_verified=len(samples),
        median_seconds=medians,syevd_median_seconds=solver,before_over_after=proof['before_over_after'],
        total_time_reduction_percent=100*(1-medians['after']/medians['before']),
        syevd_time_reduction_percent=100*(1-solver['after']/solver['before']),
        same_size_max_energy_delta_ha=max(x['energy_delta_ha'] for x in comparisons),
        same_size_max_force_delta_ha_bohr=max(x['max_force_delta_ha_bohr'] for x in comparisons),mpi=traces,
        measured_seconds={v:[samples[f'pair-{i}-{v}']['cp2k_seconds'] for i in (1,2,3)] for v in ('before','after')},
        measured_scf_steps={v:[samples[f'pair-{i}-{v}']['scf_steps'] for i in (1,2,3)] for v in ('before','after')})


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    results=[verify(p,args.work) for p in sorted((HERE/'raw').glob('*.tar.gz'))]
    assert results
    water.write_json(args.output,dict(verified=True,scope='archived execution evidence',runs=results))
    for result in results:
        print(json.dumps({k:v for k,v in result.items() if k!='mpi'}))
