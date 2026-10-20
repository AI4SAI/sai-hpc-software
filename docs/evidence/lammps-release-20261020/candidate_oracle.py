#!/usr/bin/env python3
"""Source-oracle diagnostic for the new SIF; does not replace the paired acceptance gate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path('/home/stardust/sai-hpc-software/experimental/deepmd-lammps')
BUILDS = {
    '16V100': 'md-39403084059-1-2026-10-20-5fef92e054bacdaf-export',
    '4V100': 'md-39403117741-1-2026-10-20-9bf6ae82814537d7-export',
}


def context(target):
    build_run = BUILDS[target]
    request = json.loads((ROOT / 'runs' / build_run / 'request.json').read_text())
    control = Path(request['controller'])
    if control.resolve() != control or not control.is_relative_to(ROOT / 'controller'):
        raise ValueError('unexpected frozen controller')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(control))
    from md_publication import build_record
    delivery, image, digest = build_record(build_run)
    task = ROOT / 'runtime-tests' / (build_run + '-candidate-oracle')
    return build_run, control, delivery, image, digest, task


def verify(target):
    build_run, control, delivery, image, digest, task = context(target)
    from md_science import (BACKENDS, TOLERANCES, SOURCE_MODEL, SOURCE_REFERENCE,
                            _load_fixture, _compare_numeric, parse_serialized_reference,
                            parse_lammps_output, verify_plumed_output)
    from source_cache import checksum
    request = json.loads((task / 'request.json').read_text())
    job = (task / 'job.id').read_text().strip()
    if (request['artifact_sha256'] != digest or request['delivery'] != delivery
            or request['job_script_sha256'] != checksum(task / 'job.sbatch')
            or request['entry_sha256'] != checksum(task / 'input/entry.sh')
            or request['verifier_sha256'] != checksum(task / 'input/candidate_oracle.py')):
        raise ValueError('candidate diagnostic changed')
    sha = delivery['plan']['sources']['deepmd-kit']['sha']
    source = {}
    for name in (SOURCE_REFERENCE, SOURCE_MODEL):
        source[name] = subprocess.check_output(['git', '--git-dir', str(ROOT / 'cache/repositories/deepmd-kit'),
                                               'show', sha + ':' + name], text=True)
    reference = parse_serialized_reference(source[SOURCE_REFERENCE])
    resources = dict(nodes=2, ranks=4, gpus_per_node=1, omp_threads=1)
    files, counts = {}, {}
    for backend in BACKENDS:
        fixture = _load_fixture(task / 'case', backend)
        if (fixture['reference'] != reference['reference'] or fixture['tolerances'] != TOLERANCES
                or any(fixture[key] != reference[key] for key in ('coordinates', 'atom_types', 'box'))
                or fixture['source_reference_sha256'] != hashlib.sha256(source[SOURCE_REFERENCE].encode()).hexdigest()
                or fixture['source_model_sha256'] != hashlib.sha256(source[SOURCE_MODEL].encode()).hexdigest()):
            raise ValueError('packaged oracle differs from locked upstream source')
        for engine in ('python', 'lammps'):
            path = task / 'results' / f'candidate-{backend}-{engine}.json'
            rows = json.loads(path.read_text())
            if len(rows) != 4 or [row['warmup'] for row in rows] != [True, False, False, False]:
                raise ValueError('missing warmup or measured candidate trials')
            for index, row in enumerate(rows):
                if (row['implementation'] != 'candidate' or row['backend'] != backend or row['engine'] != engine
                        or row['slurm_job'] != job or row['resources'] != resources
                        or row['reference'] != reference['reference'] or row['tolerances'] != TOLERANCES
                        or row['input_sha256'] != fixture['models'][backend]['input_sha256']):
                    raise ValueError('candidate trial identity differs')
                observed = row['observables']
                if engine == 'lammps':
                    trial = task / 'results' / f'candidate-{backend}-trials' / str(index)
                    raw = {name: (trial / name).read_text() for name in ('stdout.txt', 'result.dump', 'COLVAR')}
                    observed = parse_lammps_output(raw['stdout.txt'], raw['result.dump'])
                    if observed != row['observables']:
                        raise ValueError('raw candidate output differs from report')
                    verify_plumed_output(raw['COLVAR'], reference['distance_angstrom'])
                    hosts = []
                    for rank in range(4):
                        trace = trial / 'sai-ranks' / f'rank-{rank}.tsv'
                        fields = trace.read_text().strip().split('\t')
                        executable = delivery['identities']['lammps']['install_prefix'] + '/bin/lmp'
                        if (len(fields) != 8 or fields[1:3] != [str(rank), '4']
                                or fields[3:7] != [str(image), 'candidate', delivery['plan']['target'], executable]
                                or not fields[7].endswith('-' + delivery['identities']['lammps']['dependency_isa'])):
                            raise ValueError('MPI rank did not use the candidate and intended dependencies')
                        hosts.append(fields[0])
                        files[str(trace.relative_to(task))] = checksum(trace)
                    if len(set(hosts)) != 2 or any(hosts.count(host) != 2 for host in set(hosts)):
                        raise ValueError('candidate did not run two ranks on each of two nodes')
                    for name in raw:
                        files[str((trial / name).relative_to(task))] = checksum(trial / name)
                for name, tolerance in TOLERANCES.items():
                    _compare_numeric(observed[name], reference['reference'][name], **tolerance, path=name)
            files[str(path.relative_to(task))] = checksum(path)
            counts[backend + '/' + engine] = len(rows)
    return dict(candidate_source_oracle_passed=True, full_paired_scientific_gate=False,
                performance_verified=False, published=False, native_runtime_verified=False,
                build_run=build_run, delivery=delivery, artifact=str(image), artifact_sha256=digest,
                job=job, resources=resources, records=counts, files=files)


def submit(target):
    build_run, control, delivery, image, digest, task = context(target)
    from source_cache import checksum
    import md_acceptance_controller as science
    task.mkdir()
    for name in ('case', 'results', 'runtime', 'input'):
        (task / name).mkdir()
    request = dict(run_id=task.name, build_run=build_run, delivery=delivery,
                   sources=delivery['plan']['sources'], version=delivery['plan']['version'],
                   target=delivery['plan']['target'], artifact=str(image), artifact_sha256=digest,
                   nodes=2, ranks=4, diagnostic_only=True)
    verifier = task / 'input/candidate_oracle.py'
    verifier.write_bytes(Path(__file__).read_bytes())
    verifier.chmod(0o444)
    entry = (control / 'md_acceptance.sh').read_text()
    old_control = 'control=$(cd -- "$(dirname -- "$0")" && pwd -P)'
    old_verify = 'python3 "$control/md_science.py" verify "$task/results/"*-python.json "$task/results/"*-lammps.json \\\n  > "$task/results/scientific-summary.json"\necho MD_ALL_BACKENDS_SCIENCE_COMPLETED_NOT_PUBLISHED'
    if entry.count(old_control) != 1 or entry.count(old_verify) != 1:
        raise ValueError('unexpected frozen science entry')
    entry = entry.replace(old_control, 'control=' + shlex.quote(str(control)))
    entry = entry.replace('for implementation in baseline candidate; do', 'for implementation in candidate; do')
    entry = entry.replace(old_verify, shlex.join(['python3', str(verifier), 'verify', target]) +
                          ' > "$task/results/candidate-source-oracle.json"\necho MD_CANDIDATE_SOURCE_ORACLE_COMPLETE_NOT_PUBLISHED')
    (task / 'input/entry.sh').write_text(entry)
    script = science.render(request, task).replace(str(control / 'md_acceptance.sh'), str(task / 'input/entry.sh'))
    (task / 'job.sbatch').write_text(script)
    for path in (task / 'input/entry.sh', task / 'job.sbatch'):
        subprocess.run(['bash', '-n', str(path)], check=True)
    request.update(job_script_sha256=checksum(task / 'job.sbatch'), entry_sha256=checksum(task / 'input/entry.sh'),
                   verifier_sha256=checksum(verifier), diagnostic_helper_sha256=checksum(Path(__file__)))
    (task / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    env = dict(PATH='/usr/bin:/bin', HOME=str(Path.home()), USER='stardust', LOGNAME='stardust',
               SAI_SOFTWARE_ROOT=str(ROOT.parent.parent), PYTHONDONTWRITEBYTECODE='1',
               SBATCH_EXPORT='ALL', SBATCH_QOS='flood-1o2gpu' if target == '16V100' else 'rush-1o2gpu')
    job = subprocess.check_output(['sbatch', '--hold', '--parsable', str(task / 'job.sbatch')], env=env, text=True).strip()
    if not job.isdigit():
        raise ValueError('ambiguous candidate diagnostic handle')
    (task / 'job.id').write_text(job + '\n')
    subprocess.run(['scontrol', 'release', job], check=True)
    print(json.dumps(dict(job=job, run=task.name, diagnostic_only=True)), flush=True)


if __name__ == '__main__':
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('submit', 'verify'))
    parser.add_argument('target', choices=BUILDS)
    args = parser.parse_args()
    if args.operation == 'submit':
        submit(args.target)
    else:
        print(json.dumps(verify(args.target), sort_keys=True))
