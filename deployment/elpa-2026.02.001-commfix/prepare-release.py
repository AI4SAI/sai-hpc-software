#!/usr/bin/env python3
"""Export three repaired SIFs only after successful four-partition acceptance."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

from install import NAME, PREFIXES, checksum, inventory, require

PACKAGE = Path(__file__).resolve().parent
BUILD = PACKAGE.with_name('elpa-2026.02.001')
PREVIOUS = PACKAGE.with_name('elpa-2026.02.001-cpu')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


numeric = load('elpa_numeric', BUILD/'verify-native-results.py')
lifecycle = load('elpa_lifecycle', BUILD/'verify-lifecycle.py')


def write_json(path, value):
    with path.open('x') as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True)+'\n')


def verify_results(folder, target):
    gpu_modes = (0,) if target == 'dsprhbm' else (0, 1)
    require((folder/'passed.txt').read_text().strip() == f'ELPA_SIF_VERIFIED {target}', 'Missing SIF acceptance')
    require((folder/'lifecycle-passed.txt').read_text().strip() ==
            f'ELPA_LIFECYCLE_PASS {target} objects=32', 'Missing lifecycle acceptance')
    require('ELPA_FORTRAN_CONSUMER_PASS' in (folder/'fortran-smoke.log').read_text(), 'Missing Fortran acceptance')
    for name in ('ldd.txt', 'consumer-ldd.txt', 'dynamic.txt'):
        text = (folder/name).read_text()
        require('not found' not in text and '/opt/mellanox/hcoll' not in text, 'Unresolved or legacy runtime library')
    require(str(PREFIXES[target])+'/lib/libelpa_openmp.so.19' in (folder/'consumer-ldd.txt').read_text(),
            'Consumer loaded another ELPA prefix')
    if target == 'dsprhbm':
        require('ELPA_CPU_NO_CUDA_DEPENDENCY' in (folder/'gpu-kernels.txt').read_text(), 'CPU CUDA check missing')
        require('cudaPointerGetAttributes' not in (folder/'symbols.txt').read_text(), 'CPU pointer dispatch remains')
    logs = set()
    maxima = dict.fromkeys(numeric.ERRORS, 0.0)
    traces = []
    for ranks in (1, 2):
        for gpu in gpu_modes:
            for solver in (1, 2):
                for complex_value in (0, 1):
                    for nbc in ((0, 1) if solver == 2 else (0,)):
                        suffix = '-nbc1' if nbc else ''
                        path = folder/f'r{ranks}-g{gpu}-s{solver}-c{complex_value}{suffix}.log'
                        errors = numeric.parse_case(path.read_text(), ranks, gpu, solver, complex_value, nbc)
                        logs.add(path)
                        for key in maxima:
                            maxima[key] = max(maxima[key], errors[key])
            traces.append(lifecycle.validate((folder/f'lifecycle-r{ranks}-g{gpu}.log').read_text(), ranks, gpu, 32))
    require(set(folder.glob('r*-g*-s*-c*.log')) == logs, 'Unexpected numerical case inventory')
    return dict(numerical_solves=2*len(logs), max_errors=maxima, lifecycle=traces)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for target in PREFIXES:
        parser.add_argument('--'+target, type=Path, required=True)
    parser.add_argument('--checks', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    runs = {key: getattr(args, key).resolve(strict=True) for key in PREFIXES}
    partitions = {'16V100': 'avx2', '4V100': 'avx512', '8V100V0': 'avx2', 'DSPRHBM': 'dsprhbm'}
    checks = {key: (args.checks/key).resolve(strict=True) for key in partitions}
    ids = [(run/'job.id').read_text().strip() for run in [*runs.values(), *checks.values()]]
    # A short check may run as a Slurm step in our own existing allocation.
    # Keep the step's completion/exit status and its parent partition identity.
    query_ids = sorted(set(ids) | {job.split('.')[0] for job in ids})
    raw_accounting = subprocess.check_output(['sacct', '-n', '-P', '-j', ','.join(query_ids),
        '--format=JobIDRaw,Partition,State,ExitCode,Elapsed,NodeList'], text=True)
    accounting = {}
    for line in raw_accounting.splitlines():
        job, partition, state, code, elapsed, node = line.split('|')
        accounting[job] = dict(partition=partition, state=state, exit_code=code, elapsed=elapsed, node=node)
    for job, record in accounting.items():
        if '.' in job and not record['partition']:
            record['partition'] = accounting[job.split('.')[0]]['partition']
    for job in ids:
        require(accounting[job]['state'] == 'COMPLETED' and accounting[job]['exit_code'] == '0:0',
                f'Job not completed successfully: {job}')
    old = json.loads((PREVIOUS/'release.json').read_text())
    release = dict(version='2026.02.001', revision='commfix-1',
        upstream_commit='20be982db53a671b9aac866a726aebe153a766e6',
        source_sha256=old['source_sha256'], module_name=NAME, module_sha256=checksum(PACKAGE/NAME),
        communicator_patch_sha256=checksum(BUILD/'mpi-communicator.patch'),
        compatibility_patch_sha256=checksum(BUILD/'cuda-aware-cpu.patch'), builds={}, partition_checks={})
    require(release['module_sha256'] == old['module_sha256'], 'Partition module unexpectedly changed')
    build_proofs = {}
    for target, run in runs.items():
        require((run/'accepted.txt').read_text().startswith(f'ELPA_BUILD_AND_CHECKS_PASSED {target} '), 'Build acceptance missing')
        for line in (run/'inputs.sha256').read_text().splitlines():
            digest, filename = line.split(maxsplit=1)
            path = Path(filename)
            require(path.is_relative_to(run) and checksum(path) == digest, 'Frozen build input changed')
        sif = run/f'elpa-{target}.sif'
        digest = checksum(sif)
        require(digest == (run/'artifact.sha256').read_text().split()[0], 'Final SIF changed')
        build_proofs[target] = verify_results(run/'work/verify', target)
        release['builds'][target] = dict(prefix=str(PREFIXES[target]), sif=str(sif), sif_sha256=digest,
            job_id=(run/'job.id').read_text().strip(), **build_proofs[target])
    for partition, target in partitions.items():
        run = checks[partition]
        job = (run/'job.id').read_text().strip()
        require(accounting[job]['partition'] == partition, 'Wrong validation partition')
        require((run/'accepted.txt').read_text().strip() == f'ELPA_REPAIR_PARTITION_PASS {partition}', 'Partition incomplete')
        require((run/'artifact.sha256').read_text().split()[0] == release['builds'][target]['sif_sha256'], 'Partition used another SIF')
        for line in (run/'controls.sha256').read_text().splitlines():
            digest, name = line.split(maxsplit=1)
            require(Path(name).name == name and checksum(run/'control'/name) == digest, 'Partition control changed')
        proof = run/'work/module'
        isa, backend = ('avx512', 'cpu') if target == 'dsprhbm' else (target, 'nvidia')
        require((proof/'passed.txt').read_text().strip() == f'ELPA_PARTITION_MODULE_PASS {partition} {isa} {backend}',
                'Partition module check failed')
        require((proof/'module.sha256').read_text().split()[0] == release['module_sha256'], 'Wrong module tested')
        numeric.parse_case((proof/'numeric.log').read_text(), 2, int(backend == 'nvidia'), 2, 1, 1)
        loaders = list(proof.glob('loader.*'))
        require(len(loaders) == 2, 'Loader evidence missing for an MPI rank')
        for path in loaders:
            require(any('calling init:' in line and line.strip().endswith(str(PREFIXES[target])+'/lib/libelpa_openmp.so.19')
                        for line in path.read_text().splitlines()), 'Another ELPA was actually loaded')
        release['partition_checks'][partition] = dict(job_id=job, **accounting[job], **verify_results(run/'work/verify', target))
    out = args.output
    require(out.is_absolute() and not out.exists(), 'Use a new absolute deployment directory')
    out.mkdir(parents=True, mode=0o755)
    for name in ('install.py', 'install.sh', 'README.md', NAME):
        shutil.copyfile(PACKAGE/name, out/name)
    for target, run in runs.items():
        sif = run/f'elpa-{target}.sif'
        scratch = run/'work'/('handoff-'+out.name)
        scratch.mkdir()
        squash = scratch/'root.squashfs'
        with squash.open('xb') as stream:
            subprocess.run(['/opt/devtools/apptainer/1.4.4/bin/apptainer', 'sif', 'dump', '1', str(sif)], stdout=stream, check=True)
        extracted = scratch/'root'
        prefix = str(PREFIXES[target]).lstrip('/')
        subprocess.run(['unsquashfs', '-no-progress', '-no-xattrs', '-processors', '2', '-d', str(extracted), str(squash), prefix], check=True)
        payload = out/'payload'/target
        payload.parent.mkdir(exist_ok=True)
        shutil.copytree(extracted/prefix, payload, symlinks=True)
        require(checksum(payload/'share/sai/mpi-communicator.patch') == release['communicator_patch_sha256'], 'Wrong repair patch in SIF')
        require(checksum(payload/'share/sai/cuda-aware-cpu.patch') == release['compatibility_patch_sha256'], 'Changed compatibility patch')
        fix = json.loads((payload/'share/sai/mpi-communicator-fix.json').read_text())
        require(fix['upstream_commit'] == release['upstream_commit'] and fix['patch_sha256'] == release['communicator_patch_sha256']
                and fix['patched_sha256'] == 'af18210afa4781c58144941d1f5fef6b4807afdf4eac57bc5e6b7fd6463c9741', 'Missing repaired source provenance')
        write_json(out/f'{target}-manifest.json', inventory(payload))
        previous_name = 'cpu-manifest.json' if target == 'dsprhbm' else f'{target}-manifest.json'
        old_digest = old['manifest_sha256'] if target == 'dsprhbm' else old['gpu_prerequisites'][target]['manifest_sha256']
        require(checksum(PREVIOUS/previous_name) == old_digest, 'Previous manifest changed')
        shutil.copyfile(PREVIOUS/previous_name, out/f'{target}-previous-manifest.json')
        release['builds'][target].update(manifest_sha256=checksum(out/f'{target}-manifest.json'), previous_manifest_sha256=old_digest)
        proof = out/'evidence/build'/target
        shutil.copytree(run/'work/verify', proof, ignore=shutil.ignore_patterns('smoke', 'fortran-smoke', 'comm-trace.so'))
        for name in ('job.id', 'accepted.txt', 'inputs.sha256', 'artifact.sha256'):
            shutil.copyfile(run/name, proof/name)
    for partition, run in checks.items():
        proof = out/'evidence/partitions'/partition
        shutil.copytree(run/'work', proof, ignore=shutil.ignore_patterns('smoke', 'fortran-smoke', 'comm-trace.so'))
        shutil.copytree(run/'control', proof/'control')
        for name in ('job.id', 'accepted.txt', 'controls.sha256', 'artifact.sha256', 'hostname.txt', 'allocation.txt'):
            shutil.copyfile(run/name, proof/name)
    (out/'evidence/accounting.txt').write_text(raw_accounting)
    for path in out.rglob('*'):
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() else (path.stat().st_mode & 0o755 if path.is_relative_to(out/'payload') else 0o644))
    write_json(out/'release.json', release)
    print(out)


if __name__ == '__main__':
    main()
