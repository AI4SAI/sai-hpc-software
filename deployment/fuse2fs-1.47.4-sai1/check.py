#!/usr/bin/env python3
"""Reuse the qualified filesystem probes and test the standard module selection.

Run inside a compute allocation. --stage tests a home copy before installation;
without it, the actual public prefix/module are checked. All writable data stays
in the caller's home. This does not test abrupt node/power failure recovery.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

import install as deploy
import prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--stage', action='store_true')
    args = parser.parse_args()
    output = args.output.absolute()
    deploy.plain(output)
    deploy.require(output.is_relative_to(Path.home().resolve()) and not output.exists(),
                   'Use a new directory under your home')
    deploy.require(os.environ.get('SLURM_JOB_ID'), 'Use a compute allocation')
    release = deploy.release_plan()
    deploy.verify_tree(deploy.PACKAGE / 'payload' if args.stage else deploy.PREFIX, release['files'])
    output.mkdir(parents=True, mode=0o700)
    control = output / 'control'
    control.mkdir()
    # Top-level files preserve the failed A harness. The accepted C harness
    # handles the exact empty overlay-opacity marker and /usr/bin/false.
    evidence = deploy.PACKAGE.parents[1] / 'docs/evidence/fuse2fs-readonly-20261017/probe-c'
    for name in ('probe.py', 'workload.py'):
        shutil.copyfile(evidence / name, control / name)
    if args.stage:
        prefix = output / 'installation'
        shutil.copytree(deploy.PACKAGE / 'payload', prefix)
        config = prefix / 'etc/apptainer.conf'
        config.chmod(0o644)
        config.write_bytes(prepare.configuration(deploy.SITE_CONFIG.read_bytes(), prefix))
        config.chmod(0o444)
        module_root = output / 'modulefiles'
        module_path = module_root / 'fuse2fs' / deploy.MODULE.name
        module_path.parent.mkdir(parents=True)
        module_path.write_bytes(prepare.module_text(prefix, release['site_config_sha256'], deploy.digest(config)))
    else:
        prefix, module_root = deploy.PREFIX, deploy.MODULE.parent.parent
        deploy.verify_installed(release)
    deploy.require(deploy.digest(prefix / 'bin/fuse2fs') == deploy.BINARY_SHA256, 'Wrong test binary')
    shell = r'''
set -eo pipefail
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
source /etc/profile.d/lmod.sh
module purge
module use /opt/modules/modulefiles/devtools "$1"
module load apptainer/1.4.4
export APPTAINER_CONFIG_FILE=previous-configuration
module --ignore_cache load "$2"
/usr/bin/python3 -c 'import json,os,shutil; print(json.dumps(dict(config=os.environ["APPTAINER_CONFIG_FILE"],root=os.environ["FUSE2FS_ROOT"],path=os.environ["PATH"],driver=shutil.which("fuse2fs"))))'
module unload "$2"
[[ "$APPTAINER_CONFIG_FILE" == previous-configuration ]]
[[ -z ${FUSE2FS_ROOT+x} ]]
'''
    loaded = json.loads(subprocess.check_output(
        ['/bin/bash', '-c', shell, 'module-check', str(module_root), 'fuse2fs/' + deploy.MODULE.name], text=True))
    deploy.require(loaded['config'] == str(prefix / 'etc/apptainer.conf')
                   and loaded['driver'] == str(prefix / 'bin/fuse2fs'), 'Wrong module selection')
    (output / 'module.json').write_text(json.dumps(dict(**loaded, unload_restored=True), indent=2) + '\n')
    spec = importlib.util.spec_from_file_location('qualified_readonly_probe', control / 'probe.py')
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    probe = original.Probe(output)
    probe.candidate = prefix / 'bin/fuse2fs'
    try:
        probe.execute()
        # The original probes intentionally trace the helper through --config.
        # Also exercise the public module's environment-only config selection.
        directory, record = probe.case('module-selected-readonly')
        argv = probe.container(directory, True, probe.workload('verify', 'public-module'))
        index = argv.index('--config')
        del argv[index:index + 2]
        probe.env.update(APPTAINER_CONFIG_FILE=loaded['config'], PATH=loaded['path'])
        before = original.sha256(probe.overlay)
        probe.command(argv, directory, record)
        log = (directory / 'command.log').read_text()
        deploy.require(str(prefix / 'bin/fuse2fs') in log, 'Apptainer did not report the selected helper')
        deploy.require(original.sha256(probe.overlay) == before, 'Module RO mount changed image')
        record.update(passed=True, image_hash_unchanged=True, driver_selection_verified=True)
        probe.record.update(status='passed', stage_only=args.stage, public_installed_verified=not args.stage,
                            module_unload_restored=True, command=shlex.join(argv))
        probe.save()
    except BaseException as error:
        probe.record.update(status='failed', error=str(error))
        probe.save()
        raise
    print(json.dumps(dict(passed=True, output=str(output), stage_only=args.stage,
                          cases=len(probe.record['cases']), binary_sha256=deploy.BINARY_SHA256)))


if __name__ == '__main__':
    main()
