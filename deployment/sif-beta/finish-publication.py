#!/usr/bin/env python3
"""Finish a prepared beta publication only after the exact runtime passes its jobs.

Run in a two-GPU Slurm allocation after the final science job. This is an
internal publication driver, not a user template or an /opt payload.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

PACKAGE = Path(__file__).resolve().parent
sys.path.insert(0, str(PACKAGE.parents[1] / 'controller'))
import sif_beta as beta
from sif_runtime import PROGRAMS, digest, require


def completed(job):
    require(re.fullmatch('[0-9]+', str(job)), 'invalid acceptance job')
    rows = subprocess.check_output(['sacct', '-X', '-j', str(job), '-nP',
        '--format=JobIDRaw,State,ExitCode,Account'], text=True).strip().splitlines()
    require(rows == [f'{job}|COMPLETED|0:0|proj-sai-hpc-software'],
            'acceptance job did not complete successfully: ' + str(job))


def publish(plan, output):
    require(os.environ.get('SLURM_JOB_ACCOUNT') == 'proj-sai-hpc-software'
            and os.environ.get('SLURMD_NODENAME')
            and os.environ.get('SLURM_JOB_PARTITION') == '16V100'
            and int(os.environ.get('SLURM_GPUS_ON_NODE', '0')) >= 2,
            'publication requires the two-GPU 16V100 validation allocation')
    require(output.is_absolute() and output.resolve() == output and not output.exists(),
            'publication output must be a new canonical directory')
    candidate, bundle = Path(plan['candidate']), Path(plan['bundle'])
    require(digest(candidate / 'catalog.json') == plan['catalog_sha256']
            and digest(candidate / 'libexec/sif_runtime.py') == plan['runtime_sha256']
            and digest(bundle / 'libexec/sif_runtime.py') == plan['runtime_sha256'],
            'prepared candidate changed')
    reports = []
    for directory in plan['science']:
        report = json.loads((Path(directory) / 'verification.json').read_text())
        completed(report['job'])
        require(report['passed'] and report['automatic_memory'], 'science/memory check failed')
        reports.append(report)
    for directory in plan['concurrency']:
        directory = Path(directory)
        report = json.loads((directory / 'verification.json').read_text())
        teardown = json.loads((directory / 'host-after.json').read_text())
        for job in (report['job'], report['peer']['job'], teardown['observer_job']):
            completed(job)
        require(report['passed'] and report['sentinels_survived']
                and report['runtime_sha256'] == plan['runtime_sha256']
                and report['catalog_sha256'] == plan['catalog_sha256']
                and not teardown['namespace_processes'] and not teardown['host_visible_sentinels'],
                'concurrent epilog/teardown check failed')
    training = json.loads((Path(plan['training']) / 'verification.json').read_text())
    completed(training['job'])
    require(training['passed'] and training['automatic_memory'] and training['steps'] >= 2,
            'single-GPU training check failed')
    root, modules = Path(plan['root']), Path(plan['module_root'])
    output.mkdir(parents=True)
    checks = output / 'validation.json'
    checks.write_text(json.dumps(reports, indent=2, sort_keys=True) + '\n')
    # The normal publisher serializes manual and automated rotations alike.
    import fcntl
    with (root / '.publication.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = {}
        for name in PROGRAMS:
            link = modules / name / beta.channel(name)
            match = re.search(r'^set sai_beta_generation "([A-Za-z0-9_.-]+)"$', link.read_text(), re.M)
            require(match, 'cannot identify previous generation for rollback')
            previous[name] = match[1]
        (output / 'previous.json').write_text(json.dumps(previous, indent=2) + '\n')
        generation = root / 'generations' / plan['generation']
        if generation.exists():
            # A stopped module check may leave a fully prepared immutable
            # generation. Resume only those exact inputs; never overwrite it.
            manifest = json.loads((generation / 'manifest.json').read_text())
            require(manifest['generation'] == plan['generation'] and manifest['bundle'] == str(bundle)
                    and manifest['module_root'] == str(modules)
                    and manifest['catalog_sha256'] == digest(bundle / 'catalog.json')
                    and manifest['runtime_sha256'] == plan['runtime_sha256']
                    and manifest['validated_catalog_sha256'] == plan['catalog_sha256']
                    and manifest['checks_sha256'] == digest(checks), 'prepared generation differs')
            beta.verified_bundle(bundle, candidate / 'catalog.json', checks, list(PROGRAMS))
            for relative, expected in manifest['files'].items():
                require(digest(generation / relative) == expected, 'prepared generation changed')
        else:
            beta.prepare(root, modules, plan['generation'], bundle, candidate / 'catalog.json', checks, list(PROGRAMS))
        snapshot = json.loads((bundle / 'catalog.json').read_text())
        staging = output / 'gateways'
        for name in PROGRAMS:
            gateway = staging / name / beta.channel(name)
            gateway.parent.mkdir(parents=True)
            gateway.write_text(beta.render_gateway(modules / name / beta.version_name(name, snapshot)))
        subprocess.run(['python3', '-B', str(PACKAGE / 'check-lmod.py'), '--module-root', str(staging),
                        '--output', str(output / 'lmod')], check=True)
        module_check = output / 'module-check'
        module_check.mkdir()
        subprocess.run(['bash', str(PACKAGE / 'check.sbatch'), str(staging), str(module_check)], check=True)
        try:
            beta.select(root, plan['generation'], list(PROGRAMS))
            subprocess.run(['python3', '-B', str(PACKAGE / 'check-public.py')], check=True)
        except BaseException:
            for name, generation in previous.items():
                beta.select(root, generation, [name])
            raise
    result = dict(passed=True, generation=plan['generation'], bundle=str(bundle),
                  runtime_sha256=plan['runtime_sha256'], science_jobs=[r['job'] for r in reports],
                  lmod_checked=True, public_payload_checked=True,
                  opt_gateways_installed=all((Path('/opt/modules/modulefiles/apps') / name /
                                             beta.channel(name)).exists() for name in PROGRAMS))
    (output / 'publication.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    publish(json.loads(args.plan.read_text()), args.output)
