#!/usr/bin/env python3
"""Run the accepted native export at its canonical prefix without the HCOLL alias.

Apptainer supplies a private mount namespace around an empty home-directory
sandbox. Every executable/system library comes from the host or the exported
native installation; no CP2K SIF is executed. This is not a physical /opt install.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path('/home/stardust/sai-hpc-software')
RUN = '39094062144-1-release-16v100-avx2-2026-10-18-67b5da876dd6'
TASK = ROOT / 'runtime-tests/hcoll-native-alias-free-16v100-20261018'
SANDBOX = ROOT / 'diagnostics/cp2k-release-20261016/hcoll-native-alias-free/host-runtime-namespace'
MPI_TMP = ROOT / 'runtime/hc-native-16v'
HPCX = '/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/hpcx/hpcx-2.25.1'
CASES = ('water-gpw', 'water-diag', 'water-elpa')


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def prepare():
    require(not TASK.exists(), 'diagnostic task already exists')
    request = json.loads((ROOT / 'runs' / RUN / 'request.json').read_text())
    artifact = Path((ROOT / 'runs' / RUN / 'artifact.path').read_text().strip())
    manifest = json.loads(artifact.with_suffix('.json').read_text())
    require(manifest['verified'] and manifest['published'], 'artifact is not accepted')
    native = Path(manifest['native_export']['directory'])
    entry = json.loads((native / 'share/sai/native-entry.json').read_text())
    identity = manifest['identity']
    require(entry['identity'] == identity, 'native identity differs')
    require('/opt/mellanox' not in json.dumps(entry), 'native environment retains alias')
    prefix = identity['install_prefix']
    require(identity['partition'] == '16V100' and prefix.startswith('/opt/apps/cp2k/'), 'unexpected prefix')
    control = Path(manifest['controller'])
    require(str(control) == request['controller'], 'frozen controller differs')
    sys.path.insert(0, str(control))
    import cp2k_acceptance as acceptance
    for folder in (TASK / 'input', TASK / 'results', TASK / 'apptainer-runtime', TASK / 'apptainer-cache'):
        folder.mkdir(parents=True, exist_ok=False)
    for folder in (SANDBOX / prefix.lstrip('/'), SANDBOX / 'opt/sai_config', SANDBOX / 'diag', SANDBOX / 'work',
                   SANDBOX / str(MPI_TMP).lstrip('/')):
        folder.mkdir(parents=True, exist_ok=True)
    # Prevent harmless Apptainer passwd/group/resolver warnings in the skeleton.
    for name in ('passwd', 'group', 'resolv.conf'):
        (SANDBOX / 'etc' / name).touch(exist_ok=True)
    for case in CASES:
        folder = TASK / 'results' / case
        folder.mkdir()
        (folder / 'input.inp').write_text(acceptance.CASES[case]['input'])
    write_json(TASK / 'lock.json', dict(prepared_at=datetime.now(timezone.utc).isoformat(),
        artifact=str(artifact), artifact_sha256=manifest['sha256'], native=str(native),
        native_export=manifest['native_export'], identity=identity, controller=str(control),
        sandbox=str(SANDBOX), physical_opt_modified=False, cp2k_sif_executed=False,
        scope='native-export execution in a private mount namespace at the canonical prefix; host runtime binds; HCOLL alias absent; not physical installation acceptance'))
    q = shlex.quote
    body = '\n'.join([
        '#!/usr/bin/env bash', 'set -eo pipefail',
        'test ! -e /opt/mellanox/hcoll/lib/libhcoll.so.1',
        'printf "HCOLL_ALIAS_ABSENT\\n" > /work/alias-absent.txt',
        'source /etc/profile.d/lmod.sh', 'module purge',
        'module use /opt/modules/modulefiles/devtools ' + q(prefix + '/modulefiles'),
        'module load ' + q('cp2k/release/' + identity['build_id']), 'set -u',
        '[[ "$(command -v cp2k.psmp)" == ' + q(prefix + '/bin/cp2k.psmp') + ' ]]',
        '[[ "$CP2K_DATA_DIR" == ' + q(prefix + '/share/cp2k/data') + ' ]]',
        'printf "%s\\n%s\\n" "$(command -v cp2k.psmp)" "$CP2K_DATA_DIR" > /work/canonical-paths.txt',
        'printf "%s\\n" "$LD_LIBRARY_PATH" > /work/ld-library-path.txt',
        'module -t list > /work/modules.txt 2>&1',
        'if [[ "${HCOLL_NATIVE_PROBE:-0}" == 1 ]]; then',
        '  ldd ' + q(prefix + '/bin/cp2k.psmp') + ' > /work/ldd.txt',
        '  exec cp2k.psmp --version', 'fi',
        'export LD_DEBUG=libs LD_DEBUG_OUTPUT=/work/loader',
        'exec cp2k.psmp "$@"', '',
    ])
    (TASK / 'input/native-rank.sh').write_text(body)
    wrapper = ['#!/usr/bin/env bash', 'set -euo pipefail',
        'args=(apptainer exec --cleanenv --no-home --no-mount bind-paths,home,cwd,tmp,hostfs --pwd /work --nv)']
    for path in ('/usr', '/bin', '/sbin', '/lib', '/lib64', '/etc', '/opt/devtools', '/opt/modules', '/opt/sai_config'):
        wrapper.append('args+=(--bind ' + q(path + ':' + path + ':ro') + ')')
    for source, destination, mode in ((native, prefix, 'ro'), (TASK / 'input', '/diag', 'ro'),
                                      (MPI_TMP, str(MPI_TMP), 'rw')):
        wrapper.append('args+=(--bind ' + q(str(source) + ':' + destination + ':' + mode) + ')')
    wrapper += ['args+=(--bind "$(pwd -P):/work:rw" --env ' + q('TMPDIR=' + str(MPI_TMP)) + ')',
        'args+=(--env "HCOLL_NATIVE_PROBE=${HCOLL_NATIVE_PROBE:-0}")',
        'while IFS= read -r name; do',
        '  case "$name" in SLURM_*|OMPI_*|OPAL_*|PMIX_*|PMI_*|PRTE_*|UCX_*|NCCL_*|CUDA_*|FI_*|OMP_*)',
        '    args+=(--env "$name=${!name}") ;; esac', 'done < <(compgen -e)',
        'exec "${args[@]}" ' + q(str(SANDBOX)) + ' /usr/bin/bash /diag/native-rank.sh "$@"', '']
    (TASK / 'native-exec.sh').write_text('\n'.join(wrapper))
    (TASK / 'native-exec.sh').chmod(0o700)
    script = '\n'.join([
        '#!/usr/bin/env bash', '#SBATCH --job-name=cp2k-native-no-hcoll-alias',
        '#SBATCH --partition=16V100', '#SBATCH --qos=flood-1o2gpu', '#SBATCH --nodes=1',
        '#SBATCH --ntasks=1', '#SBATCH --gpus-per-node=1', '#SBATCH --time=15', '#SBATCH --export=NIL',
        '#SBATCH --output=' + str(TASK / 'results/slurm-%j.log'), 'set -eo pipefail', 'umask 077',
        'export HOME=/home/stardust USER=stardust LOGNAME=stardust',
        'export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
        'export LD_LIBRARY_PATH="" LD_PRELOAD="" PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1',
        'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
        'export TMPDIR=' + q(str(MPI_TMP)),
        'export APPTAINER_TMPDIR=' + q(str(TASK / 'apptainer-runtime')),
        'export APPTAINER_CACHEDIR=' + q(str(TASK / 'apptainer-cache')),
        'mkdir -p "$TMPDIR"', 'source /etc/profile.d/lmod.sh', 'module purge',
        'export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"',
        'module use /opt/modules/modulefiles/devtools /opt/modules/modulefiles/apps',
        'module load apptainer/1.4.4 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto',
        'source /opt/sai_config/mps_mapping.d/16V100.bash',
        'export OMP_NUM_THREADS=1 OMP_PLACES=cores OMP_PROC_BIND=close SLURM_EXPORT_ENV=ALL',
        'export OMPI_MCA_plm_slurm_args=--external-launcher PRTE_MCA_plm_slurm_args=--external-launcher',
        'set -u', 'cd ' + q(str(TASK)),
        'scontrol show job -o "$SLURM_JOB_ID" > results/allocation.txt',
        'exec python3 input/runner.py execute', '',
    ])
    (TASK / 'job.sbatch').write_text(script)
    (TASK / 'input/runner.py').write_bytes(Path(__file__).read_bytes())
    for name in ('job.sbatch', 'native-exec.sh', 'input/native-rank.sh'):
        subprocess.run(['bash', '-n', str(TASK / name)], check=True)
    output = subprocess.check_output(['sbatch', '--parsable', str(TASK / 'job.sbatch')], text=True).strip()
    require(re.fullmatch(r'[0-9]+', output) is not None, 'unexpected sbatch output: ' + output)
    (TASK / 'job.id').write_text(output + '\n')
    print(json.dumps(dict(task=str(TASK), job=output, identity=identity, native=str(native)), indent=2))


def execute():
    lock = json.loads((TASK / 'lock.json').read_text())
    sys.path.insert(0, lock['controller'])
    import cp2k_acceptance as acceptance
    from export_native import validate_export_record
    report = dict(scope=lock['scope'], slurm_job_id=os.environ['SLURM_JOB_ID'],
        collected_at=datetime.now(timezone.utc).isoformat(), identity=lock['identity'],
        artifact_sha256=lock['artifact_sha256'], native=lock['native'],
        physical_opt_modified=False, cp2k_sif_executed=False, passed=False, cases={})
    try:
        require(os.environ['SLURM_JOB_PARTITION'] == '16V100', 'wrong partition')
        require(digest(Path(lock['artifact'])) == lock['artifact_sha256'], 'sealed SIF changed')
        validate_export_record(lock['native_export'], lock['artifact_sha256'], [lock['identity']])
        probe = TASK / 'results/probe'
        probe.mkdir()
        def launch(work, arguments, env=None):
            with (work / 'cp2k.log').open('w') as out:
                started = time.monotonic()
                process = subprocess.run(['mpirun', '-np', '1', '--map-by', os.environ['MAP_OPT'],
                    '--report-bindings', str(TASK / 'native-exec.sh'), *arguments],
                    cwd=work, stdout=out, stderr=subprocess.STDOUT, env=env)
            require(process.returncode == 0, 'native MPI process failed: ' + str(work))
            return time.monotonic() - started
        launch(probe, [], dict(os.environ, HCOLL_NATIVE_PROBE='1'))
        linkage = (probe / 'ldd.txt').read_text()
        require('not found' not in linkage and '/opt/mellanox' not in linkage, 'unresolved or aliased native linkage')
        for library in ('libhcoll.so.1', 'libocoms.so.0'):
            require(library + ' => ' + HPCX + '/hcoll/lib/' + library in linkage, 'wrong native library source: ' + library)
        report['hcoll_linkage'] = [line.strip() for line in linkage.splitlines() if any(lib in line for lib in ('libhcoll', 'libocoms', 'libsharp'))]
        require('CP2K version 2026.2' in (probe / 'cp2k.log').read_text(), 'wrong native version')
        baseline = ROOT / 'runtime-tests' / (RUN + '-science') / 'cases'
        for case in CASES:
            folder = TASK / 'results' / case
            elapsed = launch(folder, ['-i', 'input.inp'])
            science = acceptance.parse_science((folder / 'cp2k.log').read_text(),
                (folder / 'forces.xyz').read_text(), str(elapsed), case)
            previous = baseline / case / 'single'
            reference = acceptance.parse_science((previous / 'cp2k.log').read_text(),
                (previous / 'forces.xyz').read_text(), (previous / 'elapsed.txt').read_text(), case)
            loaded = sorted(set(path for trace in folder.glob('loader.*')
                                for path in re.findall(r'calling init:\s*(\S+)', trace.read_text(errors='replace'))))
            require(any(path == HPCX + '/hcoll/lib/libhcoll.so.1' for path in loaded), 'HCOLL load trace missing')
            require(all('/opt/mellanox' not in path for path in loaded), 'loader used HCOLL alias')
            require((folder / 'alias-absent.txt').read_text().strip() == 'HCOLL_ALIAS_ABSENT', 'alias was visible')
            require(not (folder / 'BASIS_MOLOPT').exists() and not (folder / 'GTH_POTENTIALS').exists(), 'local data obscures canonical data lookup')
            report['cases'][case] = dict(science=science,
                comparison_with_accepted_sif=acceptance.compare_science(reference, science),
                actual_hcoll_loads=[p for p in loaded if any(v in p for v in ('hcoll', 'ocoms', 'sharp'))],
                alias_absent=True, installed_data_used=True)
            print('NATIVE_CASE_PASSED ' + case, flush=True)
        validate_export_record(lock['native_export'], lock['artifact_sha256'], [lock['identity']])
        require(digest(Path(lock['artifact'])) == lock['artifact_sha256'], 'sealed SIF changed during diagnostic')
        report.update(passed=True, native_export_unchanged=True, sealed_sif_unchanged=True)
    except Exception as exc:
        report['error'] = type(exc).__name__ + ': ' + str(exc)
    write_json(TASK / 'results/native-alias-free.json', report)
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'execute':
        sys.exit(execute())
    prepare()
