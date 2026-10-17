"""Read completed probe/source evidence on SAI; write a new home diagnostic."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys
import tarfile

project = Path('/home/stardust/sai-hpc-software')
output = Path(sys.argv[1])
assert output.parent == project / 'diagnostics/cp2k-release-20261016'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return dict(path=str(path), sha256=sha(path), text=path.read_text())


probes = []
for label in ('a', 'b'):
    root = project / ('runs/cp2k-spla-probe-20261017-' + label)
    job = (root / 'job.id').read_text().strip()
    assert job.isdigit()
    probe = dict(run=str(root), job_id=job, inputs=read(root / 'input.sha256'),
        accounting=subprocess.check_output(['sacct', '-X', '-nP', '-j', job, '-o',
            'JobIDRaw,Partition,State,Start,Elapsed,AllocCPUS,NodeList,ExitCode'], text=True).strip(),
        log=read(root / 'results' / ('slurm-' + job + '.log')))
    if label == 'b':
        probe['measurements'] = []
        for threads in (1, 4):
            path = root / 'results' / ('spla-threads-%d.json' % threads)
            data = json.loads(path.read_text())
            assert data['passed']
            probe['measurements'].append(dict(raw=read(path), data=data,
                medians=[dict(n=case['n'], cpu_seconds=statistics.median(case['cpu_seconds']),
                    gpu_seconds=statistics.median(case['gpu_seconds']),
                    cpu_over_gpu=statistics.median(case['cpu_seconds']) / statistics.median(case['gpu_seconds']))
                    for case in data['cases']]))
        probe['cmake_reproducer'] = read(root / 'results/openmp-repro.json')
        probe['cmake_fsck'] = read(root / 'results/openmp-fsck.log')
    probes.append(probe)

archive = project / 'cache/spack/sources/spla/spla-1.6.1.tar.gz'
source = dict(path=str(archive), sha256=sha(archive), excerpts={})
with tarfile.open(archive) as tar:
    for suffix in ('CMakeLists.txt', 'src/CMakeLists.txt', 'src/gpu_util/gpu_blas_api.hpp',
                   'src/gemm/gemm_gpu.cpp'):
        member = tar.getmember('spla-1.6.1/' + suffix)
        source['excerpts'][suffix] = tar.extractfile(member).read().decode()
revision = '67b5da876dd6a76b8b021d5a04d1c81ba79a4c50'
cp2k = dict(revision=revision, excerpts={})
for path, lo, hi in (('src/mp2_ri_gpw.F', 125, 145), ('src/rpa_exchange.F', 550, 565),
                     ('src/local_gemm_api.F', 90, 225), ('src/input_cp2k_global.F', 160, 190)):
    content = subprocess.check_output(['git', '--git-dir=' + str(project / 'cache/repositories/cp2k'),
                                      'show', revision + ':' + path], text=True)
    cp2k['excerpts'][path] = dict(first_line=lo, text='\n'.join(content.splitlines()[lo-1:hi]))
failed = project / 'runs/39032306213-1-release-4v100-avx512-2026-10-17-67b5da876dd6/results/slurm-1831311.log'
lines = failed.read_text().splitlines()
indices = sorted({j for i, line in enumerate(lines) if 'could not be removed' in line
                  for j in range(max(0, i-6), min(len(lines), i+8))})
record = dict(checked_at=datetime.now(timezone.utc).isoformat(), probes=probes,
    spla_source=source, cp2k_source=cp2k, failed_application=dict(log=str(failed), sha256=sha(failed),
        excerpt='\n'.join(lines[i] for i in indices), fsck=read(project /
            'diagnostics/cp2k-release-20261016/4v100-1831311-fsck-readonly-20261017.log')),
    image_integrity_after=read(project / 'diagnostics/cp2k-release-20261016/spla-sealed-image-after-20261017.json'),
    limitations=['Dependency microbenchmark, not CP2K scientific or whole-application acceptance.',
        'CMake fresh-overlay reproducer passed; original link.txt failure cause remains unresolved.',
        'Probe a found a separate private-driver read-only mount failure; probe b uses stock for read-only.',
        'The supposedly read-only probe changed the dependency image hash; its original seal must be rejected.',
        'The original seal, historical controllers and shared /opt installation were not changed.'])
with output.open('x') as stream:
    stream.write(json.dumps(record, indent=2, sort_keys=True) + '\n')
print(json.dumps(dict(output=str(output), sha256=sha(output))))
