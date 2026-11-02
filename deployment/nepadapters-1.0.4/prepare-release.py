#!/usr/bin/env python3
"""Prepare an administrator package from explicitly selected accepted partitions."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'controller'))
from nepadapters_release import (PARTITIONS, VERSION, SOURCE_SHA, PAYLOAD, SHARED_PREFIX,
                                 build_report, require, runtime_request)
from nepadapters_native import FILES as NATIVE_FILES, PHASES, context as native_context, verify as verify_native
from source_cache import checksum

PACKAGE = Path(__file__).resolve().parent
MODULE = '/opt/modules/modulefiles/apps/nepadapters/1.0.4-lammps30Sep2026'
FILES = PAYLOAD


def completed(job):
    require(str(job).isdigit(), 'invalid Slurm handle')
    text = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', str(job), '-o', 'JobIDRaw,State,ExitCode'], text=True)
    row = next((x.split('|') for x in text.splitlines() if x.split('|')[0] == str(job)), None)
    require(row and row[1:3] == ['COMPLETED', '0:0'], 'Slurm job not successfully completed: ' + str(job))


def modulefile(rows):
    prefixes = {row['prefix'] for row in rows.values()}
    require(len(prefixes) == 1, 'module must use a single shared plugin prefix')
    config = ' '.join(part + ' {' + row['lammps']['prefix'] + '}' for part, row in rows.items())
    return '''#%Module1.0
module-whatis "NEPAdapters 1.0.4; CPU/CUDA Kokkos addon for LAMMPS 30Sep2026"
proc ModulesHelp {} {
    puts stderr "NEPAdapters 1.0.4 (c934e714); LAMMPS 30Sep2026 addon.\nAccepted partitions: @PARTITIONS@. Load inside a Slurm allocation. Supports nep/cpu and nep/gpu/kk.\nGPU execution requires -k on g N -sf kk -pk kokkos neigh full comm device gpu/aware on.\nOrdinary NEP4/NEP5; NEP3 and CUDA LAMMPS qNEP are unsupported.\nThis module is not a plugin for daily LAMMPS beta builds."
}
conflict nepadapters
if {[module-info mode load]} {
    if {![info exists env(SLURM_JOB_ID)] || ![info exists env(SLURM_JOB_PARTITION)]} {
        ModulesHelp
        error "Load NEPAdapters inside a Slurm GPU allocation"
    }
}
if {[llength [info commands depends-on]]} {
    depends-on lammps/30Sep2026-deepmd320-auto
} else {
    prereq lammps/30Sep2026-deepmd320-auto
}
if {[module-info mode load] || [module-info mode remove]} {
    if {[module-info mode remove]} {
        set partition $env(SAI_NEPADAPTERS_PARTITION)
    } else {
        set partition $env(SLURM_JOB_PARTITION)
    }
    set prefix {@PREFIX@}
    set configurations [dict create @CONFIG@]
    if {![dict exists $configurations $partition]} {error "Unsupported NEPAdapters partition: $partition"}
    set lammps [dict get $configurations $partition]
    if {[module-info mode load]} {
        if {![info exists env(SAI_LAMMPS_PREFIX)] || $env(SAI_LAMMPS_PREFIX) ne $lammps} {
            error "NEPAdapters requires its matching LAMMPS 30Sep2026 build"
        }
        foreach name {nepadapterscpuplugin.so nepadaptersgpuplugin.so} {
            if {![file readable $prefix/lib/$name]} {error "Missing NEPAdapters plugin: $prefix/lib/$name"}
        }
        puts stderr "NEPAdapters 1.0.4; LAMMPS 30Sep2026; partition=$partition; styles=nep/cpu,nep/gpu/kk"
    }
    prepend-path LAMMPS_PLUGIN_PATH $prefix/lib
    setenv SAI_NEPADAPTERS_PREFIX $prefix
    setenv SAI_NEPADAPTERS_PARTITION $partition
}
'''.replace('@CONFIG@', config).replace('@PARTITIONS@', ', '.join(rows)).replace('@PREFIX@', next(iter(prefixes)))


def prepare(handoffs, destination, partitions=None, previous=None):
    selected = set(PARTITIONS if partitions is None else partitions)
    require(selected and selected <= set(PARTITIONS), 'invalid selected partitions')
    rows = {}
    for handoff in handoffs:
        build, native = Path(handoff['build']).resolve(), Path(handoff['native']).resolve()
        original = json.loads((build / 'request.json').read_text())
        require(original.get('layout') == 'shared', 'rebuild once with the shared plugin layout')
        plan = native_context(native)
        part = plan['request']['partition']
        request = runtime_request(original, part)
        require(part in PARTITIONS and part not in rows, 'duplicate/unsupported partition')
        require(request['source_sha'] == SOURCE_SHA and request['version'] == VERSION, 'wrong source release')
        completed((build / 'job.id').read_text().strip())
        for phase in PHASES:
            completed((native / (phase + '.id')).read_text().strip())
        accepted = build_report(build)
        require(accepted['accepted'], 'SIF build was not accepted')
        require(plan['build'] == str(build) and plan['request'] == request and
                plan['build_status'] == accepted and not plan['installed'], 'native handoff belongs to a different build')
        proof = verify_native(native)
        require('sif' in proof['phases']['local'], 'shared SIF was not verified on this partition')
        files = {}
        for name in FILES:
            path = build / 'export' / name
            files[name] = dict(sha256=checksum(path), size=path.stat().st_size, mode=0o644)
            if name.startswith('lib/'):
                require(files[name]['sha256'] == proof['plugin_sha256'][Path(name).name], 'native plugin differs from SIF export')
        rows[part] = dict(prefix=request['prefix'], lammps=request['lammps'], files=files, recipe=request['recipe_sha256'],
                         build=str(build), native=str(native), accepted=True, native_multigpu_accepted=True,
                         image=str(build / 'nepadapters.sif'), image_sha256=checksum(build / 'nepadapters.sif'),
                         native_evidence_sha256=checksum(native / 'acceptance.json'))
    require(set(rows) == selected, 'all three V100 partitions (or every explicitly selected partition) must pass')
    first = next(iter(rows.values()))
    require(first['prefix'] == SHARED_PREFIX and
            all(all(row[key] == first[key] for key in ('prefix', 'files', 'build', 'image_sha256'))
                for row in rows.values()), 'all partitions must test the identical shared SIF/plugins')
    previous_hashes = []
    if previous is not None:
        old = json.loads((previous / 'release.json').read_text())
        require(old['version'] == VERSION and old['source_sha'] == SOURCE_SHA and
                old['module'] == MODULE and checksum(previous / 'modulefile') == old['module_sha256'],
                'previous release/module identity differs')
        require(set(old['partitions']) <= set(rows), 'release extension cannot remove a partition')
        for part, row in old['partitions'].items():
            require(rows[part]['lammps'] == row['lammps'], 'shared migration cannot change installed LAMMPS')
            if old.get('schema') == 2:
                require(all(rows[part][key] == row[key] for key in ('prefix', 'files')),
                        'shared release extension cannot replace installed binaries')
        previous_hashes = [old['module_sha256'], *old.get('previous_module_sha256', [])]
    require(not destination.exists(), 'release package already exists')
    destination.mkdir(parents=True, mode=0o755)
    for name in ('install.py', 'install.sh', 'README.md', 'submit-native-checks.sh'):
        shutil.copyfile(PACKAGE / name, destination / name)
    (destination / 'control').mkdir()
    for name in NATIVE_FILES:
        shutil.copyfile(Path(__file__).resolve().parents[2] / 'controller' / name, destination / 'control' / name)
    for name in FILES:
        target = destination / 'payload/shared' / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        shutil.copyfile(Path(first['build']) / 'export' / name, target)
        target.chmod(0o644)
    for part, row in rows.items():
        # Keep administrator checks independent of private build runtime directories.
        shutil.copytree(Path(row['native']) / 'cases', destination / 'cases' / part)
        evidence = destination / 'evidence' / part
        evidence.mkdir(parents=True)
        shutil.copyfile(Path(row['build']) / 'results/artifacts.json', evidence / 'sif.json')
        shutil.copyfile(Path(row['native']) / 'acceptance.json', evidence / 'native.json')
    text = modulefile(rows)
    (destination / 'modulefile').write_text(text)
    release = dict(schema=2, version=VERSION, source_sha=SOURCE_SHA, module=MODULE,
                   module_sha256=hashlib.sha256(text.encode()).hexdigest(), partitions=rows,
                   shared=dict(prefix=SHARED_PREFIX, files=first['files'], image=first['image'],
                               image_sha256=first['image_sha256']),
                   previous_module_sha256=previous_hashes,
                   acceptance_scope='NEP4 Pb/Te energy, forces, virial and 100 GPU MD steps; single GPU, two GPUs and two nodes',
                   checks={str(p.relative_to(destination)): checksum(p)
                           for name in ('control', 'cases', 'evidence')
                           for p in sorted((destination / name).rglob('*')) if p.is_file()})
    (destination / 'release.json').write_text(json.dumps(release, indent=2) + '\n')
    for path in (destination, *destination.rglob('*')):
        path.chmod(0o755 if path.is_dir() else 0o644)
    print(destination)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--handoffs', type=Path, required=True, help='JSON list of build/native directory pairs')
    p.add_argument('--destination', type=Path, required=True)
    p.add_argument('--partitions', default=','.join(PARTITIONS), help='Every selected partition must pass; default all three')
    p.add_argument('--previous', type=Path, help='Accepted earlier package; permit adding partitions to its module')
    args = p.parse_args()
    prepare(json.loads(args.handoffs.read_text()), args.destination.resolve(), args.partitions.split(','), args.previous)
