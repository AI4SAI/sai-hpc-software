#!/usr/bin/env python3
"""Reverify completed native jobs and prepare a gated administrator package."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.dont_write_bytecode = True
import install as deployment

PUBLIC = Path('/home/stardust/sai-hpc-software/deployment/lammps-update1-default')
PROJECT = Path('/org/org-open-sai/proj-sai-hpc-software')


def read(path):
    return json.loads(path.read_text())


def save(path, content):
    deployment.plain(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    if path.exists():
        deployment.require(path.read_bytes() == content, 'prepared file differs: ' + str(path))
    else:
        with path.open('xb') as stream:
            stream.write(content)
    path.chmod(0o444)


def save_json(path, value):
    save(path, (json.dumps(value, indent=2, sort_keys=True) + '\n').encode())


def accounting(job, partition, directory):
    deployment.require(job.isdigit() and int(job) > 0, 'invalid job number')
    output = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', job,
        '-o', 'JobIDRaw,State,ExitCode,Partition,Account,WorkDir'], text=True)
    rows = [line.split('|') for line in output.splitlines() if line.split('|')[0] == job]
    deployment.require(len(rows) == 1, 'missing or ambiguous accounting for ' + job)
    row = rows[0]
    deployment.require(row[3:6] == [partition, 'proj-sai-hpc-software', str(directory)], 'job allocation differs')
    if row[1] in ('PENDING', 'RUNNING', 'CONFIGURING', 'COMPLETING', 'SUSPENDED', 'REQUEUED'):
        return row, False
    deployment.require(row[1:3] == ['COMPLETED', '0:0'], 'job failed: ' + '|'.join(row))
    return row, True


def pending_jobs(native, nep):
    pending, records = [], {}
    for rows, kind in ((native, 'lammps'), (nep, 'nepadapters')):
        deployment.require(len(rows) == 3 and {r['partition'] for r in rows} == deployment.PARTITIONS,
                           'incomplete submitted ' + kind + ' matrix')
        for item in rows:
            part, directory = item['partition'], Path(item['directory'])
            deployment.require(directory.is_relative_to(PROJECT / 'operations') and directory.name == part,
                               'unexpected job directory')
            jobs = {'native': item['job']} if kind == 'lammps' else item['jobs']
            if kind == 'nepadapters':
                deployment.require(set(jobs) == {'local', 'distributed'}, 'incomplete NEP jobs')
            for phase, job in jobs.items():
                row, completed = accounting(job, part, directory)
                records.setdefault(kind, {}).setdefault(part, {})[phase] = row
                if not completed:
                    pending.append(f'{kind}/{part}/{phase}={job}:{row[1]}')
    return pending, records


def migrate_home_package(package):
    updates = read(package / 'package-update/manifest.json')
    deployment.require(set(updates) == {'install.py', 'release.json', deployment.LAMMPS.split('/')[1]},
                       'unexpected home package migration')
    # Validate the entire transition before replacing the first helper.
    for name, row in updates.items():
        deployment.checked(package / 'package-update' / name, row['after_sha256'])
        target = deployment.UPDATE1 / name
        deployment.plain(target)
        deployment.require(deployment.digest(target) in (row['before_sha256'], row['after_sha256']),
                           'original update1 package changed: ' + name)
    for name, row in updates.items():
        target = deployment.UPDATE1 / name
        if deployment.digest(target) == row['after_sha256']:
            continue
        stage = target.with_name('.' + name + '.default-help-' + str(os.getpid()))
        with stage.open('xb') as stream:
            stream.write((package / 'package-update' / name).read_bytes())
            stream.flush(); os.fsync(stream.fileno())
        stage.chmod(0o644)
        deployment.require(deployment.digest(target) == row['before_sha256'], 'home package changed during preparation')
        os.replace(stage, target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native-work', type=Path, required=True)
    parser.add_argument('--nep-work', type=Path, required=True)
    parser.add_argument('--controller', type=Path, required=True)
    parser.add_argument('--wait', action='store_true', help='wait for the existing jobs, polling once per minute')
    args = parser.parse_args()
    package = deployment.PACKAGE
    deployment.require(package == PUBLIC, 'prepare the reviewed public home package')
    for path in (args.native_work, args.nep_work, args.controller):
        deployment.plain(path)
        deployment.require(path.is_relative_to(PROJECT / 'operations'), 'preparation inputs must be under project operations')
    if (package / 'manifest.json').exists():
        deployment.preflight(deployment.load_manifest())
        print('DEFAULT_PACKAGE_ALREADY_READY', flush=True)
        return
    native_jobs = read(args.native_work / 'jobs.json')
    nep_jobs = read(args.nep_work / 'jobs.json')
    while True:
        pending, job_records = pending_jobs(native_jobs, nep_jobs)
        if not pending:
            break
        print(datetime.now(timezone.utc).isoformat() + ' waiting: ' + ', '.join(pending), flush=True)
        if not args.wait:
            raise SystemExit('No defaults published: native acceptance is still pending.')
        time.sleep(60)

    # These CLIs recompute scientific results and check final Slurm accounting.
    result = subprocess.check_output(['/usr/bin/python3', '-B', str(deployment.UPDATE1 / 'native_checks.py'),
                                      'verify', str(args.native_work)], text=True)
    native_verification = json.loads(result)
    save_json(package / 'evidence/lammps-verification.json', native_verification)
    nep_reports = {}
    for row in native_jobs:
        part, directory = row['partition'], Path(row['directory'])
        for source, suffix in (('native-acceptance.json', '.json'), ('request.json', '-request.json')):
            save(package / ('evidence/lammps-' + part + suffix), (directory / source).read_bytes())
    for row in nep_jobs:
        part, directory = row['partition'], Path(row['directory'])
        result = subprocess.check_output(['/usr/bin/python3', '-B', str(args.controller / 'nepadapters_native.py'),
                                          'verify', str(directory)], text=True)
        report = json.loads(result)
        nep_reports[part] = report
        save_json(package / ('evidence/nepadapters-' + part + '.json'), report)
        save(package / ('evidence/nepadapters-' + part + '-request.json'), (directory / 'request.json').read_bytes())

    release = read(deployment.UPDATE1 / 'release.json')
    old_module_hash = deployment.digest(Path('/opt/modules/modulefiles/apps') / deployment.LAMMPS)
    plugin_release = read(Path('/home/stardust/sai-hpc-software/deployment/nepadapters-1.0.5/release.json'))
    deployment.require(plugin_release['version'] == '1.0.5'
                       and plugin_release['source_sha'] == '3ceba8f9b69765ac29d9f2f4f1c4f76cee8ecf37', 'wrong NEP release')
    plugin = plugin_release['shared']
    lines = [
        'LAMMPS 30Sep2026 update1 + NEPAdapters 1.0.5',
        'Features: MPI/OpenMP; GPU + KOKKOS CUDA SM70; DeePMD 3.2.0; PLUMED;',
        'nep/cpu; nep/gpu/kk; ordinary NEP4/NEP5.',
        '',
        'LAMMPS release: stable_30Sep2026_update1, 098ffdefa525cabc82272925de02e9884f58c657',
        'NEPAdapters release: v1.0.5, 3ceba8f9b69765ac29d9f2f4f1c4f76cee8ecf37',
        'Installed plugin libraries: /opt/apps/nepadapters/1.0.5-lammps30Sep2026/lib',
        'Full recorded LAMMPS help and partition paths:',
        '  cat /home/stardust/sai-hpc-software/deployment/lammps-30Sep2026-update1/build-info/index.txt',
        'Recorded plugin registration/style inventory (from the original LAMMPS qualification):',
        '  cat /home/stardust/sai-hpc-software/module-info/20261111/nepadapters/1.0.5-lammps30Sep2026/index.txt',
        'The same plugin bytes passed the update1 tests listed below; no plugin was rebuilt.',
        'Numerical validation used the pinned Pb/Te NEP4 model. NEP5 is upstream-supported;',
        'this qualification does not claim coverage of every model family or every NEP5 model.',
        '',
        'The administrator installer makes these the defaults:',
        '  module load lammps -> lammps/30Sep2026_update1-deepmd320-auto',
        '  module load nepadapters -> nepadapters/1.0.5-lammps30Sep2026_update1',
        'Both modules load inside a Slurm allocation. Help/whatis works on login nodes.',
        'NEP3 and CUDA LAMMPS qNEP remain unsupported. Daily beta compatibility is not claimed.',
        '',
        'Acceptance: host MPI, exact installed binaries and plugins; 14 LAMMPS cases per partition;',
        'NEP single GPU, two GPUs on one node and two nodes, energy/forces/virials,',
        '100 GPU MD steps, periodic/mixed/nonperiodic boundaries and module unload.',
    ]
    for row in native_jobs:
        part = row['partition']
        lines.append(f'{part}: LAMMPS job {row["job"]}; NEP jobs ' + ', '.join(nep_reports[part]['jobs'].values()))
    save(package / 'build-info/index.txt', ('\n'.join(lines) + '\n').encode())

    # Only after all raw verification succeeds, migrate stardust's home helper
    # to support the reviewed help update. This still does not modify /opt.
    migrate_home_package(package)
    release = read(deployment.UPDATE1 / 'release.json')
    modules = {}
    for name in deployment.TARGETS:
        source = deployment.UPDATE1 / deployment.LAMMPS.split('/')[1] if name == deployment.LAMMPS else package / 'modulefiles' / name
        modules[name] = dict(sha256=deployment.digest(source))
        if name == deployment.LAMMPS:
            deployment.require(old_module_hash in [release['module_sha256'], *release['previous_module_sha256']],
                               'unreviewed physical LAMMPS module')
            modules[name]['previous_sha256'] = release['previous_module_sha256']
    required = ['install.py', 'runtime_files.py', 'release.json', deployment.LAMMPS.split('/')[1],
                *release['build_info']]
    for row in release['deliveries']:
        required += [row[k] for k in ('manifest', 'acceptance', 'verification', 'dependency_records')]
    manifest = dict(schema=1, ready=True, partitions=sorted(deployment.PARTITIONS), modules=modules,
                    plugin=dict(prefix=plugin['prefix'], files=plugin['files']),
                    native_work=str(args.native_work), nepadapters_work=str(args.nep_work),
                    nepadapters_accounting=job_records['nepadapters'],
                    update1_package_files={name: deployment.digest(deployment.UPDATE1 / name) for name in required},
                    files={str(p.relative_to(package)): deployment.digest(p) for root in ('evidence', 'build-info', 'modulefiles')
                           for p in sorted((package / root).rglob('*')) if p.is_file()})
    deployment.preflight(manifest)
    save_json(package / 'manifest.json', manifest)
    print('DEFAULT_PACKAGE_READY: ' + str(package / 'install.sh'), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        raise SystemExit('Default preparation stopped: ' + str(error)) from error
