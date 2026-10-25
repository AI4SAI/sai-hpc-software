"""Numerical checks of one application's real SIF/Lmod launch route."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil

from sif_runtime import digest, require


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def prepare(software, partition, bundle, output, fixtures):
    require(not output.exists(), 'new runtime check directory required')
    snapshot = read(bundle / 'catalog.json')
    row = snapshot['applications'][software]['partitions'][partition]
    output.mkdir(parents=True)
    for name in ('tmp', 'trace', 'control'):
        (output / name).mkdir(mode=0o700)
    shutil.copytree(fixtures / software, output / 'case', copy_function=shutil.copyfile)
    control = Path(__file__).resolve().parent
    if software == 'deepmd-kit':
        for name in ('deepmd_training_check.py', 'deepmd_training_input.json', 'deepmd_training_dpa2.json'):
            shutil.copyfile(control / name, output / 'control' / name)
        script = (output / 'case/check.py').read_text().replace("os.environ['SAI_CHECK_PREFIX']", repr(row['identity']['install_prefix']))
        (output / 'case/check.py').write_text(script)
        (output / 'training').mkdir()
    elif software == 'abacus' and partition == 'DSPRHBM':
        file = output / 'case/INPUT'
        file.write_text(re.sub(r'(?m)^device\s+gpu\s*$', 'device cpu', file.read_text()))
    request = dict(software=software, partition=partition, catalog=str(bundle / 'catalog.json'),
                   catalog_sha256=digest(bundle / 'catalog.json'), runtime_sha256=snapshot['runtime_sha256'],
                   reference=read(fixtures / 'references.json')[software],
                   ranks=2 if software in ('abacus', 'cp2k', 'lammps') else 1,
                   files={str(p.relative_to(output)): digest(p) for p in output.rglob('*') if p.is_file()})
    write(output / 'request.json', request)
    return request


def render(software, partition, bundle, output):
    from remote_controller import TARGETS, gpumd_qos, SAI_PROJECT_ROOT
    from release_contract import PARTITIONS
    target = next(k for k, v in PARTITIONS.items() if v == partition)
    app = read(bundle / 'catalog.json')['applications'][software]
    prefix = app['partitions'][partition]['identity']['install_prefix']
    mpi = software in ('abacus', 'cp2k', 'lammps')
    q = shlex.quote
    lines = ['#!/usr/bin/env bash', '#SBATCH --job-name=beta-' + software + '-' + partition,
             '#SBATCH --account=proj-sai-hpc-software', '#SBATCH --chdir=' + str(output),
             '#SBATCH --partition=' + partition,
             '#SBATCH --qos=' + (TARGETS[target]['qos'] if partition == 'DSPRHBM' else gpumd_qos(SAI_PROJECT_ROOT, target)),
             '#SBATCH --nodes=1', '#SBATCH --ntasks=' + ('2' if mpi else '1'),
             *(['#SBATCH --cpus-per-task=1', '#SBATCH --mem=28G'] if partition == 'DSPRHBM' else
               ['#SBATCH --gpus-per-node=' + ('1' if software == 'gpumd' else '2')]),
             '#SBATCH --time=30', '#SBATCH --output=' + str(output / 'slurm-%j.log'), '#SBATCH --export=NIL',
             'set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD="" PYTHONPATH=""',
             # NIL drops HOME; site AVX2 MPI needs the actual account home even
             # though runtime scratch remains in private memory/project storage.
             'export HOME=' + q(str(Path.home())),
             'export USER=${SLURM_JOB_USER:?} LOGNAME=${SLURM_JOB_USER:?}',
             'source /etc/profile.d/lmod.sh', 'module purge', 'module use ' + q(str(bundle / 'modulefiles')),
             'module load ' + q(software + '-sif/' + app['version']),
             'export SLURM_EXPORT_ENV=ALL OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1',
             'export DP_INTRA_OP_PARALLELISM_THREADS=1 DP_INTER_OP_PARALLELISM_THREADS=1',
             'export TF_NUM_INTRAOP_THREADS=1 TF_NUM_INTEROP_THREADS=1',
             'export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher',
             'export SAI_SIF_TRACE_DIR=' + q(str(output / 'trace')),
             'printf "%s\\n" "$SLURM_JOB_ID" > job.id', 'module -t list > modules.txt 2>&1',
             'cd case']
    launch = 'mpirun -np 2 --map-by ppr:2:node:PE=1 --bind-to core '
    commands = {'cp2k': launch + 'cp2k.psmp -i input.inp', 'abacus': launch + 'abacus',
                'lammps': launch + 'lmp -pk gpu 1 split 1.0 -in in.lmp -log log.lammps', 'gpumd': 'gpumd',
                'deepmd-kit': 'deepmd-python check.py'}
    lines += ['/usr/bin/time -f %e -o elapsed.txt ' + commands[software] + ' > stdout.txt 2>&1']
    if software == 'deepmd-kit':
        runner = ("import os,sys,runpy; os.chdir('training'); sys.argv="
                  + repr(['../control/deepmd_training_check.py', prefix, '../case/fixture.json'])
                  + "; runpy.run_path(sys.argv[0], run_name='__main__')")
        # Bind the complete case tree by launching from its parent; the SIF
        # exposes only the current work directory, never arbitrary siblings.
        lines += ['cd ..', 'deepmd-python -c ' + q(runner) + ' > training/training.log 2>&1']
    lines += ['cd ' + q(str(output)), 'python3 -B ' + q(str(Path(__file__).resolve())) + ' verify ' + q(str(output))]
    return '\n'.join(lines) + '\n'


def training_report(output, job, partition, prefix):
    summary = read(output / 'training/summary.json')
    require(summary['passed'] is True and summary['job'] == job
            and summary['partition'] == partition and summary['installation'] == prefix
            and set(summary['cases']) == {'se_e2_a', 'dpa2'}, 'missing current two-GPU training evidence')
    for name, case in summary['cases'].items():
        require(case['passed'] is True, 'failed DDP descriptor')
        directory = output / 'training' / name
        for stage, final in (('training', 8), ('restart', 12)):
            report = case[stage]
            curve = directory / ('lcurve.out' if stage == 'training' else 'restart-lcurve.out')
            values = [[float(v) for v in line.split()] for line in curve.read_text().splitlines()
                      if line.strip() and not line.startswith('#')]
            require(report['passed'] is True and report['returncode'] == 0
                    and values == report['learning_curve'] and values[-1][0] == final
                    and all(math.isfinite(v) for row in values for v in row), 'invalid training learning curve')
            require(digest(directory / report['checkpoint']['name']) == report['checkpoint']['sha256'], 'changed checkpoint')
            ranks = [read(directory / (('initial-' if stage == 'training' else '') + f'rank-{i}.json')) for i in range(2)]
            require(ranks == report['ranks'] and {r['cuda_current_device'] for r in ranks} == {0, 1}
                    and all(r['world_size'] == 2 and r['gradient_calls'] > 0 and r['gradients_finite']
                            and r['initial_parameters_sha256'] != r['final_parameters_sha256'] for r in ranks)
                    and ranks[0]['final_parameters_sha256'] == ranks[1]['final_parameters_sha256'], 'invalid DDP rank evidence')
    return summary


def verify(output):
    request = read(output / 'request.json')
    catalog = Path(request['catalog'])
    require(digest(catalog) == request['catalog_sha256']
            and digest(catalog.parent / 'libexec/sif_runtime.py') == request['runtime_sha256'], 'changed test runtime/catalog')
    for name, expected in request['files'].items():
        require(digest(output / name) == expected, 'changed numerical fixture: ' + name)
    software, partition = request['software'], request['partition']
    row = read(catalog)['applications'][software]['partitions'][partition]
    work, reference = output / 'case', request['reference']
    text = (work / 'stdout.txt').read_text()
    require(not re.search(r'UCX\s+ERROR|unable to create shared memory|performance degradation', text, re.I), 'MPI transport failure')
    job = (output / 'job.id').read_text().strip()
    if software == 'cp2k':
        from cp2k_benchmark import parse_science, compare_science
        observed = parse_science(text, (work / 'forces.xyz').read_text(), (work / 'elapsed.txt').read_text(), 'water-gpw')
        result = compare_science(reference, observed)
    elif software == 'abacus':
        from abacus_benchmark import parse_scf
        result = parse_scf(text, (work / 'OUT.autotest/running_scf.log').read_text(), (work / 'elapsed.txt').read_text())
        error = abs(result['energy_ev'] - reference['energy_ev'])
        require(math.isfinite(error) and error < 1e-5, 'ABACUS route energy changed')
        result['energy_error_ev'] = error
    elif software == 'lammps':
        require('(Mixed Precision)' in text and '- Using acceleration for' in text, 'LAMMPS GPU not used')
        [line] = re.findall(r'^SAI_GPU_OBS (.+)$', text, re.M)
        values = [float(v) for v in line.split()]
        dump = (work / 'result.dump').read_text().split('ITEM: ATOMS id fx fy fz\n')[1]
        values.extend(float(v) for line in dump.splitlines() for v in line.split()[1:])
        expected = [v for group in reference for v in group]
        require(len(values) == len(expected) and all(math.isfinite(a) and abs(a-b) <= 2e-6 + 2e-5 * abs(b)
                for a, b in zip(values, expected)), 'LAMMPS GPU observables changed')
        result = dict(max_error=max(abs(a-b) for a, b in zip(values, expected)))
    elif software == 'gpumd':
        from gpumd_science import xyz, compare
        e, f = xyz(work / 'dump.xyz'); ge, gf = xyz(work / 'gold.xyz')
        result = dict(energy_error=compare([[e]], [[ge]], 1e-3, 'GPUMD energy'), force_error=compare(f, gf, 1e-3, 'GPUMD forces'))
    else:
        result = dict(inference=read(work / 'result.json'), pytorch_ddp=training_report(output, job, partition, row['identity']['install_prefix']))
        require(result['inference']['passed'] is True and result['inference']['device'] == 'cuda:0'
                and result['inference']['cuda_bytes'] > 0
                and result['inference']['package'].startswith(row['identity']['install_prefix'] + '/'), 'invalid GPU inference')
    traces = [read(p) for p in (output / 'trace').glob('*.json')]
    require(traces and all(t['software'] == software and t['job'] == job and t['sha256'] == row['sha256']
            and t['memory_scope']['job'] == job for t in traces), 'wrong SIF/allocation/private memory')
    if request['ranks'] == 2:
        require({t['rank'] for t in traces} == {'0', '1'} and all(t['mpi'] == row['mpi_prefix'] for t in traces),
                'missing host MPI rank or wrong MPI')
    report = dict(passed=True, automatic_memory=True, partition=partition, job=job,
                  catalog_sha256=request['catalog_sha256'], runtime_sha256=request['runtime_sha256'], **{software: result})
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['verify'])
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    write(args.output / 'verification.json', verify(args.output))
