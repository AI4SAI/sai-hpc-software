#!/usr/bin/env python3
"""Clone a timed-out CP2K main overlay and retry metadata/export/verify only."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess


def checksum(path):
    h = hashlib.sha256()
    with path.open('rb', buffering=0) as stream:
        while True:
            block = stream.read(16 * 1024 * 1024)
            if not block:
                return h.hexdigest()
            h.update(block)


def run(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('old_run')
    parser.add_argument('new_run')
    args = parser.parse_args()
    root = Path('/home/stardust/sai-hpc-software')
    old = root / 'runs' / args.old_run
    new = root / 'runs' / args.new_run
    if not old.is_dir():
        raise ValueError('old run missing')
    request = json.loads((old / 'request.json').read_text())
    if request.get('phase') != 'main' or request.get('software') != 'cp2k':
        raise ValueError('source is not a CP2K main run')
    old_job = (old / 'job.id').read_text().strip()
    state = run(['sacct', '-X', '-n', '-j', old_job, '-o', 'State'], capture_output=True).stdout.strip()
    if state != 'TIMEOUT':
        raise ValueError('source job must be the diagnosed timeout')
    overlay = old / 'work.ext3'
    source_sha = checksum(overlay)
    identity = request['identity']
    controller = Path(request['controller'])
    prefix = identity['install_prefix']
    new_overlay = new / 'work.ext3'
    if new.exists():
        if (new / 'job.id').exists() or not new_overlay.is_file():
            raise ValueError('existing recovery run is not an unsubmitted copied overlay')
        copied_sha = checksum(new_overlay)
    else:
        new.mkdir(mode=0o700)
        for name in ('input', 'results', 'runtime', 'apptainer-cache'):
            (new / name).mkdir(mode=0o700)
        run(['cp', '--sparse=always', '--reflink=auto', '--', overlay, new_overlay])
        copied_sha = checksum(new_overlay)
    if copied_sha != source_sha:
        raise ValueError('overlay changed during copy')
    image = root / 'containers/base/minimal-v1.sif'
    delivery = json.dumps(identity, separators=(',', ':'))
    common = [
        'apptainer', 'exec', '--fakeroot', '--cleanenv', '--containall', '--no-home',
        '--no-mount', 'bind-paths,home,cwd,tmp,hostfs', '--pwd', '/', '--net', '--network', 'none',
        '--bind', '/usr:/usr:ro', '--bind', '/lib:/lib:ro', '--bind', '/lib64:/lib64:ro',
        '--bind', '/opt/devtools:/opt/devtools:ro', '--bind', '/opt/modules:/opt/modules:ro',
        '--bind', '/var/lib/dpkg:/var/lib/dpkg:ro', '--bind', '/usr/lib/os-release:/etc/os-release:ro',
        '--bind', '/etc/alternatives:/etc/alternatives:ro', '--bind', '/usr/bin:/bin:ro',
        '--bind', '/etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro', '--bind', '/etc/lmod:/etc/lmod:ro',
        '--bind', f'{controller}:/control:ro', '--overlay', str(new_overlay),
        '--bind', f'{new}/results:/results:rw', '--env', 'BUILD_JOBS=8', '--env', 'TMPDIR=/workspace/tmp', str(image)]
    sha = identity['source_sha']
    version = identity['source_version']
    target = identity['target']
    export = common + ['/usr/bin/bash', '/control/cp2k_container_entry.sh', 'export', 'cp2k', sha, version, target, delivery]
    metadata = common + ['/usr/bin/bash', '/control/cp2k_container_entry.sh', 'metadata', 'cp2k', sha, version, target, delivery]
    # Verification runs against the generated SIF, not the build overlay.
    verify = [
        'apptainer', 'exec', '--fakeroot', '--cleanenv', '--containall', '--no-home',
        '--no-mount', 'bind-paths,home,cwd,tmp,hostfs', '--pwd', '/', '--net', '--network', 'none',
        '--bind', '/usr:/usr:ro', '--bind', '/lib:/lib:ro', '--bind', '/lib64:/lib64:ro',
        '--bind', '/opt/devtools:/opt/devtools:ro', '--bind', '/opt/modules:/opt/modules:ro',
        '--bind', '/etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro', '--bind', '/etc/lmod:/etc/lmod:ro',
        '--bind', f'{controller}:/control:ro', '--env', 'BUILD_JOBS=8', '--env', 'TMPDIR=/runtime',
        str(new / 'result.sif'), '/usr/bin/bash', '/control/cp2k_container_entry.sh', 'verify',
        'cp2k', sha, version, target, delivery]
    q = lambda value: shlex.quote(str(value))
    script = '\n'.join([
        '#!/bin/bash', '#SBATCH --partition=DSPRHBM', '#SBATCH --nodelist=d9470n02',
        '#SBATCH --qos=rush-cpu', '#SBATCH --nodes=1', '#SBATCH --ntasks=1',
        '#SBATCH --cpus-per-task=8', '#SBATCH --mem=28G', '#SBATCH --time=720',
        '#SBATCH --job-name=cp2k-export-recovery', f'#SBATCH --output={new}/results/slurm-%j.log',
        '#SBATCH --export=NIL', 'set -eo pipefail',
        'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD="" USER=${USER:-stardust} LOGNAME=${LOGNAME:-stardust}',
        'source /etc/profile.d/lmod.sh', 'module load apptainer/1.4.4', 'set -u',
        f'export TMPDIR={q(new / "runtime")} APPTAINER_TMPDIR={q(new / "runtime")} APPTAINER_CACHEDIR={q(new / "apptainer-cache")}',
        'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
        f'printf "%s  %s\\n" {source_sha} {q(new_overlay)} | sha256sum --check --status',
        shlex.join(metadata),
        shlex.join(export),
        f'test -s {q(new / "final.squashfs")} || true',
        shlex.join(common[:common.index('--overlay')] + ['--overlay', str(new_overlay) + ':ro', '--env', 'BUILD_JOBS=8', '--env', 'TMPDIR=/workspace/tmp', str(image), '/usr/bin/cat', '/workspace/final.squashfs']) + f' > {q(new / "final.squashfs")}',
        f'test -s {q(new / "final.squashfs")}',
        shlex.join(['apptainer', 'sif', 'new', str(new / 'result.sif')]),
        shlex.join(['apptainer', 'sif', 'add', '--datatype', '4', '--partfs', '1', '--parttype', '2', '--partarch', '2', '--groupid', '1', str(new / 'result.sif'), str(new / 'final.squashfs')]),
        shlex.join(verify), 'echo CP2K_EXPORT_RECOVERY_VERIFIED', ''])
    (new / 'job.sbatch').write_text(script)
    run(['bash', '-n', new / 'job.sbatch'])
    run(['sbatch', '--test-only', new / 'job.sbatch'])
    job = run(['sbatch', '--parsable', new / 'job.sbatch'], capture_output=True).stdout.strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('invalid job receipt')
    (new / 'job.id').write_text(job + '\n')
    request.update(run_id=args.new_run, job=job, script_sha256=checksum(new / 'job.sbatch'),
                   recovery_source=args.old_run, cloned_overlay_sha256=source_sha,
                   scientific_verified=False, published=False)
    (new / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    print(json.dumps({'job': job, 'run': str(new), 'overlay_sha256': source_sha}))


if __name__ == '__main__':
    main()
