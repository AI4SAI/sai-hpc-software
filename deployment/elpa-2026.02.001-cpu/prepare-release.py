#!/usr/bin/env python3
"""Extract the accepted CPU SIF and pin the four tested partition selections."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess

from install import NAME, PREFIX, checksum, inventory, require, verify


def write_json(path, value):
    with path.open('x') as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--module-checks', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--payload-from', type=Path,
                        help='Reuse an inventoried payload from the same accepted CPU SIF')
    parser.add_argument('--verify-only', action='store_true', help='Revalidate SIF and raw evidence without extraction')
    args = parser.parse_args()
    require(args.verify_only or args.output is not None, 'Specify --output or --verify-only')
    package = Path(__file__).resolve().parent
    original = package.with_name('elpa-2026.02.001')
    spec = importlib.util.spec_from_file_location('native_results', original/'verify-native-results.py')
    numeric = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(numeric)
    run = args.run.resolve(strict=True)
    proof = run/'work/verify'
    require((run/'accepted.txt').is_file(), 'CPU build not accepted')
    build_job = (run/'job.id').read_text().strip()
    build_accounting = (run/'accounting.txt').read_text().strip().split('|')
    require(len(build_accounting) == 6 and build_accounting[:4] ==
            [build_job, 'DSPRHBM', 'COMPLETED', '0:0'], 'CPU Slurm job did not complete')
    require((proof/'passed.txt').read_text().strip() == 'ELPA_SIF_VERIFIED dsprhbm', 'CPU SIF not verified')
    require((proof/'gpu-kernels.txt').read_text().strip() == 'ELPA_CPU_NO_CUDA_DEPENDENCY',
            'CPU library dependency check is missing')
    require(not re.search(r'lib(?:cuda|cublas|cusolver)',
                         (proof/'ldd.txt').read_text() + (proof/'dynamic.txt').read_text()),
            'CPU library still depends on CUDA')
    require(not re.search(r'is_device_ptr|cudaPointerGetAttributes', (proof/'symbols.txt').read_text()),
            'CPU library retained CUDA pointer dispatch')
    sif = run/'elpa-dsprhbm.sif'
    require(checksum(sif) == (run/'artifact.sha256').read_text().split()[0], 'CPU SIF changed')
    logs = []
    maxima = dict.fromkeys(numeric.ERRORS, 0.0)
    for ranks in (1, 2):
        for solver in (1, 2):
            for complex_value in (0, 1):
                for nbc in ((0, 1) if solver == 2 else (0,)):
                    suffix = '-nbc1' if nbc else ''
                    path = proof/f'r{ranks}-g0-s{solver}-c{complex_value}{suffix}.log'
                    errors = numeric.parse_case(path.read_text(), ranks, 0, solver, complex_value, nbc)
                    logs.append(path)
                    for key in maxima:
                        maxima[key] = max(maxima[key], errors[key])
    require(set(proof.glob('r*-g*-s*-c*.log')) == set(logs), 'Unexpected CPU test inventory')
    require('ELPA_FORTRAN_CONSUMER_PASS' in (proof/'fortran-smoke.log').read_text(), 'Fortran check failed')
    module_hash = checksum(package/NAME)
    gpu_release = json.loads((original/'release.json').read_text())
    previous_module_hash = checksum(original/NAME)
    require(previous_module_hash == gpu_release['module_sha256'], 'Previous auto module changed')
    accounting = {}
    for line in (args.module_checks/'accounting.txt').read_text().splitlines():
        job, partition, state, code, elapsed, node = line.split('|')
        accounting[job] = dict(partition=partition, state=state, exit_code=code, elapsed=elapsed, node=node)
    module_results = {}
    for partition, isa, backend in [('16V100','avx2','nvidia'), ('4V100','avx512','nvidia'),
                                    ('8V100V0','avx2','nvidia'), ('DSPRHBM','avx512','cpu')]:
        folder = args.module_checks/partition
        job = (folder/'job.id').read_text().strip()
        require(accounting[job]['partition'] == partition and
                accounting[job]['state'] == 'COMPLETED' and accounting[job]['exit_code'] == '0:0',
                f'Module job did not complete: {job}')
        require((folder/'module.sha256').read_text().split()[0] == module_hash, 'Untested module')
        require((folder/'passed.txt').read_text().strip() ==
                f'ELPA_PARTITION_MODULE_PASS {partition} {isa} {backend}', 'Module check failed')
        require(f'elpa/{NAME}' in (folder/'modules.txt').read_text().split(), 'Wrong module name loaded')
        if backend == 'nvidia':
            require((folder/'legacy-unload.txt').read_text().strip() == 'ELPA_LEGACY_AUTO_UNLOAD_PASS',
                    'Legacy auto module upgrade/unload check is missing')
        numeric.parse_case((folder/'numeric.log').read_text(), 2, int(backend == 'nvidia'), 2, 1, 1)
        expected = f'/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-{isa}/{backend}/lib/libelpa_openmp.so.19'
        traces = list(folder.glob('loader.*'))
        require(len(traces) == 2, 'Expected loader proof from both MPI ranks')
        for trace in traces:
            require(any('calling init:' in line and line.strip().endswith(expected)
                        for line in trace.read_text().splitlines()), 'Wrong ELPA loaded')
            require(not any('calling init:' in line and '/opt/mellanox/hcoll' in line
                            for line in trace.read_text().splitlines()), 'Legacy HCOLL loaded')
        module_results[partition] = dict(job_id=job, **accounting[job], isa=isa, backend=backend)
    if args.verify_only:
        print(json.dumps(dict(status='accepted', job_id=build_job, numeric_passes=24,
                              module_name=NAME, module_sha256=module_hash,
                              previous_module_sha256=previous_module_hash,
                              max_errors=maxima, partition_module_checks=module_results), indent=2))
        return
    out = args.output
    require(out.is_absolute() and not out.exists(), 'Use a new absolute handoff path')
    out.mkdir(parents=True, mode=0o755)
    for name in ['install.py', 'install.sh', 'README.md', NAME]:
        shutil.copyfile(package/name, out/name)
        (out/name).chmod(0o644)
    if args.payload_from:
        previous = args.payload_from.resolve(strict=True)
        accepted = json.loads((previous/'release.json').read_text())
        require(accepted['sif_sha256'] == checksum(sif) and accepted['prefix'] == str(PREFIX),
                'Existing payload came from a different CPU build')
        require(checksum(previous/'cpu-manifest.json') == accepted['manifest_sha256'] ==
                checksum(package/'cpu-manifest.json'), 'Existing CPU manifest changed')
        verify(previous/'payload/cpu', json.loads((previous/'cpu-manifest.json').read_text()))
        source = previous/'payload/cpu'
    else:
        scratch = run/'work'/f'cpu-handoff-{out.name}'
        scratch.mkdir()
        squash = scratch/'root.squashfs'
        with squash.open('xb') as stream:
            subprocess.run(['/opt/devtools/apptainer/1.4.4/bin/apptainer','sif','dump','1',str(sif)],stdout=stream,check=True)
        extracted = scratch/'root'
        subprocess.run(['unsquashfs','-no-progress','-no-xattrs','-processors','2','-d',str(extracted),str(squash),str(PREFIX).lstrip('/')],check=True)
        source = extracted/str(PREFIX).lstrip('/')
    payload = out/'payload/cpu'
    payload.parent.mkdir()
    shutil.copytree(source, payload, symlinks=True)
    require((payload/'share/sai/backend').read_text().strip() == 'cpu', 'Unexpected backend')
    write_json(out/'cpu-manifest.json', inventory(payload))
    for isa in ('avx2', 'avx512'):
        shutil.copyfile(original/f'{isa}-manifest.json', out/f'{isa}-manifest.json')
    shutil.copytree(proof, out/'evidence/cpu', ignore=shutil.ignore_patterns('smoke','fortran-smoke'))
    shutil.copytree(args.module_checks, out/'evidence/partition-module', ignore=shutil.ignore_patterns('smoke'))
    for path in (out/'evidence').rglob('*'):
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() else 0o644)
    release = dict(version='2026.02.001', prefix=str(PREFIX), module_name=NAME, module_sha256=module_hash,
                   previous_module_sha256=previous_module_hash,
                   sif=str(sif), sif_sha256=checksum(sif), job_id=build_job,
                   manifest_sha256=checksum(out/'cpu-manifest.json'), numeric_passes=24, max_errors=maxima,
                   gpu_prerequisites=gpu_release['builds'], partition_module_checks=module_results,
                   source_sha256=gpu_release['source_sha256'])
    write_json(out/'release.json', release)
    print(out)


if __name__ == '__main__':
    main()
