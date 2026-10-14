#!/usr/bin/env python3
"""Render an isolated CPU dependency stage for a frozen CP2K app identity."""
import json
from pathlib import Path
import shlex

from release_contract import validate_identity
from remote_controller import container_command

SEED_SHA256 = '80497b787dc178876e71098840cf465deb63c6c9bec27c9dd5cc0bed3643c337'


def render(project, controller, run, identity):
    project, controller, run = Path(project), Path(controller), Path(run)
    identity = validate_identity(identity)
    if identity['software'] != 'cp2k' or identity['target'] != 'dsprhbm':
        raise ValueError('initial final-prefix dependency stage is scoped to DSPRHBM')
    for path in (project, controller, run):
        if not path.is_absolute() or path.resolve() != path:
            raise ValueError('stage paths must be canonical')
    if not run.is_relative_to(project / 'runs') or not controller.is_relative_to(project / 'controller'):
        raise ValueError('stage paths escaped the project')
    seed = project / 'cache/spack/bootstrap/spack-source-v2.ext3'
    overlay = run / 'work.ext3'
    args = ['/usr/bin/env', 'SAI_BUILD_PARTITION=DSPRHBM', '/bin/bash',
            '/control/cp2k_container_entry.sh', 'dependencies', 'cp2k',
            identity['source_sha'], identity['source_version'], identity['target'],
            json.dumps(identity, sort_keys=True, separators=(',', ':'))]
    command = container_command(project / 'containers/base/minimal-v1.sif', args,
        overlay=overlay, control=controller, jobs=8, gpu=False, extra_binds=[
            (project / 'cache/spack', '/input/spack'),
            ('/var/lib/dpkg', '/var/lib/dpkg'), ('/etc/os-release', '/etc/os-release'),
            ('/etc/alternatives', '/etc/alternatives'), ('/usr/bin', '/bin')])
    command[2:2] = ['--bind', str(run / 'results') + ':/results:rw']
    q = shlex.quote
    lines = ['#!/bin/bash', '#SBATCH --job-name=cp2k-final-dependencies',
             '#SBATCH --partition=DSPRHBM', '#SBATCH --qos=rush-cpu',
             '#SBATCH --nodes=1', '#SBATCH --ntasks=1', '#SBATCH --cpus-per-task=8',
             '#SBATCH --mem=28G', '#SBATCH --time=720', '#SBATCH --export=NIL',
             '#SBATCH --output=' + str(run / 'results/slurm-%j.log'),
             'set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
             'source /etc/profile.d/lmod.sh', 'module load apptainer/1.4.4', 'set -u',
             'umask 077', 'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
             'export TMPDIR=' + q(str(run / 'runtime')), 'export APPTAINER_TMPDIR="$TMPDIR"',
             'export APPTAINER_CACHEDIR=' + q(str(run / 'apptainer-cache')),
             'test ! -e ' + q(str(overlay)),
             shlex.join(['cp', '--sparse=always', '--reflink=auto', '--', str(seed), str(overlay)]),
             shlex.join(['chmod', '600', str(overlay)]),
             shlex.join(['printf', '%s  %s\n', SEED_SHA256, str(overlay)]) + ' | sha256sum --check --status',
             shlex.join(['/usr/sbin/e2fsck', '-pf', str(overlay)]) + ' || test "$?" -eq 1',
             shlex.join(['/usr/sbin/resize2fs', str(overlay), '32G']),
             shlex.join(command), 'echo CP2K_DEPENDENCY_STAGE_COMPLETE_NOT_APPLICATION_ACCEPTANCE']
    return '\n'.join(lines) + '\n'
