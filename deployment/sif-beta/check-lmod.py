#!/usr/bin/env python3
"""Exercise actual Lmod login rejection and rotation without launching software."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'controller'))
from sif_beta import channel, render_version, render_gateway, stem
from sif_runtime import PROGRAMS, digest, require

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--module-root', required=True, type=Path)
parser.add_argument('--output', required=True, type=Path)
args = parser.parse_args()
require(args.output.is_absolute() and args.output.resolve() == args.output and not args.output.exists(),
        'output must be a new canonical directory')
args.output.mkdir(parents=True, mode=0o755)
environment = {k: v for k, v in os.environ.items() if not k.startswith(('SLURM_', 'SAI_'))}


def run(name, script):
    result = subprocess.run(['bash', '--noprofile', '--norc', '-c',
        'set -eo pipefail\nsource /etc/profile.d/lmod.sh\nmodule purge\n' + script],
        text=True, capture_output=True, env=environment)
    (args.output / (name + '.stdout')).write_text(result.stdout)
    (args.output / (name + '.stderr')).write_text(result.stderr)
    require(result.returncode == 0, f'{name}: Lmod check failed; see {args.output}')
    return result


checks = {}
for software in PROGRAMS:
    name = software + '/' + channel(software)
    result = run(software + '-login', '\n'.join([
        'module use ' + shlex.quote(str(args.module_root)),
        'before_path=$PATH', 'before_modules=${LOADEDMODULES:-}',
        'if module load ' + shlex.quote(name) + '; then echo "unexpected login load" >&2; exit 1; fi',
        '[[ $PATH == "$before_path" && ${LOADEDMODULES:-} == "$before_modules" ]]',
        '[[ -z ${' + stem(software) + '_SOURCE+x} ]]',
        'module help ' + shlex.quote(name), 'module show ' + shlex.quote(name),
    ]))
    require(all(word in result.stderr for word in ('Commit:', 'Templates:', 'Fixed module:', 'Not loaded:')),
            'login rejection must include complete dynamic help')
    checks[software + '_login'] = True

# A private synthetic module exercises Lmod's environment bookkeeping. It does
# not spoof a Slurm job or load production MPI/GPU modules on the login node.
fixture = args.output / 'rotation'
for generation in ('first', 'second'):
    directory = fixture / 'generations' / generation
    (directory / 'modules').mkdir(parents=True)
    (directory / 'bin').mkdir()
    (directory / 'modules/cp2k').write_text('\n'.join([
        '#%Module1.0', 'conflict cp2k',
        'proc ModulesHelp {} {puts stderr "selected ' + generation + '"}',
        'setenv SAI_TEST_BETA_VERSION ' + generation,
        'prepend-path PATH ' + str(directory / 'bin'), '']))
home_modules = fixture / 'home/cp2k'
home_modules.mkdir(parents=True)
versions = {'first': 'master-beta-20261024-aaaaaaaaaaaa', 'second': 'master-beta-20261025-bbbbbbbbbbbb'}
for generation, version in versions.items():
    (home_modules / version).write_text(render_version(fixture, 'cp2k', generation))
pointer = home_modules / 'master-beta-later'
pointer.symlink_to(versions['first'])
gateway = fixture / 'public/cp2k/master-beta-later'
gateway.parent.mkdir(parents=True)
gateway.write_text(render_gateway(pointer))
before = digest(gateway)

def switch_command(generation):
    return 'ln -sfn ' + shlex.quote(versions[generation]) + ' ' + shlex.quote(str(pointer))

for entry in ('public', 'home'):
    pointer.unlink()
    pointer.symlink_to(versions['first'])
    run('rotation-' + entry, '\n'.join([
    'module use ' + shlex.quote(str(fixture / entry)),
    'original=$PATH', 'module load cp2k/master-beta-later',
    '[[ $SAI_TEST_BETA_VERSION == first && $SAI_SIF_CP2K_BETA_GENERATION == first ]]',
    'selected=$PATH', switch_command('second'),
    '[[ $PATH == "$selected" ]]', 'module help cp2k/master-beta-later',
    'module unload cp2k/master-beta-later',
    '[[ $PATH == "$original" && -z ${SAI_TEST_BETA_VERSION+x} && -z ${SAI_SIF_CP2K_BETA_GENERATION+x} ]]',
    'module load cp2k/master-beta-later',
    '[[ $SAI_TEST_BETA_VERSION == second && $SAI_SIF_CP2K_BETA_GENERATION == second ]]',
    switch_command('first'),
    'module unload cp2k/master-beta-later', '[[ $PATH == "$original" ]]',
    'module load cp2k/master-beta-later', '[[ $SAI_TEST_BETA_VERSION == first ]]',
    'module purge', '[[ $PATH == "$original" ]]',
    ]))
require(digest(gateway) == before, 'rotation must not change the public gateway')
checks['public_and_home_symlink_rotation_unload_rollback'] = True
(args.output / 'verification.json').write_text(json.dumps({'passed': True, 'checks': checks}, indent=2) + '\n')
print(json.dumps(checks, sort_keys=True))
