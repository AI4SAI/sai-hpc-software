#!/usr/bin/env python3
"""Maintain accepted branch-beta modules in stardust's home directory."""
import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import re

from module_publication import tcl
from release_contract import PARTITIONS, SOFTWARE
from sif_modules import load_snapshot, render_module, verify_sources, write
from sif_runtime import MPI_LAUNCHERS, PROGRAMS, digest, require


def token(value):
    require(bool(re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value)),
            'invalid beta generation')
    return value


def channel(software):
    return SOFTWARE[software]['development_ref'] + '-beta-later'


def version_name(software, snapshot):
    identity = next(iter(snapshot['applications'][software]['partitions'].values()))['identity']
    version = f"{SOFTWARE[software]['development_ref']}-beta-{snapshot['snapshot']}-{identity['source_sha'][:12]}"
    if snapshot.get('runtime_sha256'):
        version += '-r' + snapshot['runtime_sha256'][:12]
    return version


def stem(software):
    return 'SAI_SIF_' + software.upper().replace('-', '_')


def render_gateway(home_module):
    # Installed once in /opt. No changing version metadata lives here.
    return '#%Module1.0\n# Maintained by stardust; this gateway does not rotate.\nsource ' + tcl(home_module) + '\n'


def render_version(root, software, generation):
    key = stem(software) + '_BETA_GENERATION'
    return '\n'.join([
        '#%Module1.0', '# Keep the loaded generation when the later symlink changes.',
        f'set sai_beta_root {tcl(root)}',
        f'set sai_beta_generation {tcl(token(generation))}',
        f'if {{[info exists env({key})]}} {{',
        f'    set sai_beta_generation $env({key})',
        '} elseif {[module-info mode remove]} {',
        '    error "missing saved beta generation; refusing to unload a different snapshot"',
        '}',
        'if {![regexp {^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$} $sai_beta_generation]} {error "invalid beta generation"}',
        f'source [file join $sai_beta_root generations $sai_beta_generation modules {software}]',
        f'setenv {key} $sai_beta_generation', ''])


def render_help(software, snapshot, module_root, checks=None):
    app = snapshot['applications'][software]
    identity = next(iter(app['partitions'].values()))['identity']
    fixed = software + '/' + version_name(software, snapshot)
    command = {
        'cp2k': 'cp2k.psmp -i input.inp -o output.out',
        'abacus': 'abacus > abacus.out',
        'lammps': 'lmp -pk gpu $SLURM_GPUS_ON_NODE split 1.0 -in in.lmp -log log.lammps',
        'deepmd-kit': 'dp --pt train input.json',
        'gpumd': 'gpumd > gpumd.out',
    }[software]
    launch = ('Use host mpirun with the resource and mapping settings in your existing job script.\n'
              if software in ('cp2k', 'abacus', 'lammps') else 'Run directly in the case directory.\n')
    if software == 'lammps':
        launch += 'The input must select GPU styles, such as lj/cut/gpu.\n'
    elif software == 'deepmd-kit':
        ddp = [((checks or {}).get(partition, {}).get(software, {}).get('pytorch_ddp', {}))
               for partition in app['partitions']]
        if ddp and all(report.get('passed') is True
                       and set(report.get('cases', {})) == {'se_e2_a', 'dpa2'}
                       and all(case.get('passed') is True for case in report['cases'].values())
                       for report in ddp):
            launch += ('PyTorch training and restart accepted on one node with two GPUs for se_e2_a and DPA2 on all listed partitions.\n'
                       'For two allocated GPUs: deepmd-python -m torch.distributed.run --standalone --nproc_per_node=2 -m deepmd --pt train input.json\n'
                       'Multi-node training and more than two GPUs have not been accepted. TensorFlow/JAX are CPU-only.\n')
        else:
            launch += 'Single-GPU PyTorch training was checked on 16V100; multi-GPU training is not accepted for this snapshot.\n'
        launch += 'Use deepmd-python for the SIF Python environment.\n'
    return (f"[SAI beta] {software} / {SOFTWARE[software]['development_ref']}\n"
            f"Source version: {identity['source_version']}\n"
            f"Commit: {identity['source_sha']}\n"
            f"Accepted snapshot: {snapshot['snapshot']} (latest accepted, not necessarily upstream HEAD)\n"
            f"Partitions: {', '.join(app['partitions'])}\n"
            f"Fixed module: {fixed}\n"
            "System sbatch examples: /opt/sbatch_examples\n\n"
            "Load on a compute node inside your existing sbatch job or srun shell:\n"
            f"  module load {software}/{channel(software)}\n"
            f"Command: {command}\n" + launch +
            "Commands enter the read-only SIF automatically; no extraction is needed.\n"
            "Runtime temporary files use private node memory automatically; no TMPDIR setup is needed.\n"
            f"For a fixed version: module use {module_root}; module load {fixed}\n")


def verified_bundle(bundle, validated_catalog, checks, software):
    """Bind relocated public images to the exact catalog that passed SIF/MPI checks."""
    snapshot = load_snapshot(bundle / 'catalog.json')
    accepted = load_snapshot(validated_catalog)
    reports = json.loads(checks.read_text())
    require(isinstance(reports, list) and len({r['partition'] for r in reports}) == len(reports),
            'duplicate or invalid partition checks')
    by_partition = {r['partition']: r for r in reports}
    require(digest(bundle / 'libexec/sif_runtime.py') == snapshot['runtime_sha256'], 'changed bundle runtime')
    for name in software:
        app = snapshot['applications'][name]
        require(set(app['partitions']) == {PARTITIONS[t] for t in SOFTWARE[name]['targets']},
                f'{name}: complete partition matrix is required for beta')
        for partition, row in app['partitions'].items():
            identity = row['identity']
            require(identity['track'] == 'development'
                    and identity['source_ref'] == SOFTWARE[name]['development_ref'],
                    'beta source must match the displayed development branch')
            report = by_partition.get(partition, {})
            require(report.get('passed') is True and isinstance(report.get(name), dict)
                    and report.get('catalog_sha256') == digest(validated_catalog)
                    and report.get('runtime_sha256') == snapshot['runtime_sha256'],
                    f'{name}/{partition}: missing matching SIF execution acceptance')
            if 'memory_bootstrap' in snapshot:
                require(report.get('automatic_memory') is True,
                        f'{name}/{partition}: automatic memory runtime was not accepted')
    normalized = copy.deepcopy(snapshot)
    # Normalize relocation only against the exact accepted path, preserving
    # any earlier provenance already present in the validated catalog.
    if 'memory_bootstrap' in normalized:
        row = normalized['memory_bootstrap']
        if row.get('source_image') == accepted['memory_bootstrap']['image']:
            row['image'] = row.pop('source_image')
            if 'source_image' in accepted['memory_bootstrap']:
                row['source_image'] = accepted['memory_bootstrap']['source_image']
    for name, app in normalized['applications'].items():
        for partition, row in app['partitions'].items():
            original = accepted['applications'][name]['partitions'][partition]
            if row.get('source_image') == original['image']:
                row['image'] = row.pop('source_image')
                if 'source_image' in original:
                    row['source_image'] = original['source_image']
    require(normalized == accepted, 'relocated bundle differs from the accepted catalog')
    selected = dict(snapshot, applications={name: snapshot['applications'][name] for name in software})
    verify_sources(selected)
    # Launcher paths are part of the fixed runtime, not the rotating module.
    import shlex
    launchers = {name: PROGRAMS[name] for name in software}
    launchers['mpi'] = MPI_LAUNCHERS
    for name, programs in launchers.items():
        for program in programs:
            argv = ['/usr/bin/python3', '-I', '-B', str(bundle / 'libexec/sif_runtime.py'),
                    str(bundle / 'catalog.json'), '_mpi' if name == 'mpi' else name, program]
            expected = '#!/usr/bin/env bash\nexec ' + shlex.join(argv) + ' "$@"\n'
            require((bundle / 'bin' / name / program).read_text() == expected,
                    'launcher differs from the fixed bundle')
    return snapshot, by_partition


def prepare(root, module_root, generation, bundle, validated_catalog, checks, software):
    generation = token(generation)
    for path in (root, module_root, bundle, validated_catalog, checks):
        require(path.is_absolute() and path.resolve() == path, 'beta paths must be absolute and canonical')
    snapshot, reports = verified_bundle(bundle, validated_catalog, checks, software)
    destination = root / 'generations' / generation
    require(not destination.exists(), 'generation already exists; select it for rollback instead')
    # Preflight all immutable names before creating or selecting anything.
    for name in software:
        require(not (module_root / name / version_name(name, snapshot)).exists(),
                'fixed module name already exists; preserve its generation')
    for name in software:
        app = snapshot['applications'][name]
        identity = next(iter(app['partitions'].values()))['identity']
        branch = SOFTWARE[name]['development_ref']
        description = render_help(name, snapshot, module_root, reports)
        extra = {stem(name) + '_VERSION': version_name(name, snapshot), stem(name) + '_BRANCH': branch,
                 stem(name) + '_SNAPSHOT': snapshot['snapshot']}
        notice = (f"[SAI beta] {name}/{branch}; commit={identity['source_sha'][:12]}; "
                  f"snapshot={snapshot['snapshot']}")
        write(destination / 'modules' / name,
              render_module(bundle, name, dict(app, version=version_name(name, snapshot)), help_text=description, notice=notice,
                            extra_environment=extra, compute_only=True))
    versions = {}
    for name in software:
        version = version_name(name, snapshot)
        module = module_root / name / version
        write(module, render_version(root, name, generation))
        versions[name] = {'name': version, 'sha256': digest(module)}
    write(destination / 'acceptance.json', json.dumps(list(reports.values()), indent=2, sort_keys=True) + '\n')
    files = {str(path.relative_to(destination)): digest(path)
             for path in destination.rglob('*') if path.is_file()}
    write(destination / 'manifest.json', json.dumps({
        'schema': 2, 'generation': generation, 'bundle': str(bundle),
        'module_root': str(module_root), 'versions': versions,
        'catalog_sha256': digest(bundle / 'catalog.json'), 'runtime_sha256': snapshot['runtime_sha256'],
        'validated_catalog': str(validated_catalog), 'validated_catalog_sha256': digest(validated_catalog),
        'checks_sha256': digest(checks), 'software': software,
        'jobs': {part: report['job'] for part, report in reports.items()}, 'files': files,
    }, indent=2, sort_keys=True) + '\n')
    for path in destination.rglob('*'):
        if path.is_dir():
            path.chmod(0o755)
    destination.chmod(0o755)
    return destination


def select(root, generation, software):
    """Atomic per-application promotion/rollback; old loaded jobs keep their token."""
    require(root.is_absolute() and root.resolve() == root, 'beta root must be canonical')
    destination = root / 'generations' / token(generation)
    manifest = json.loads((destination / 'manifest.json').read_text())
    require(manifest['generation'] == generation and set(software) <= set(manifest['software']),
            'generation does not provide these applications')
    for path, expected in manifest['files'].items():
        require((destination / path).resolve().is_relative_to(destination)
                and digest(destination / path) == expected, 'changed generation file')
    require(manifest.get('schema') == 2, 'generation predates the versioned beta symlink layout')
    module_root = Path(manifest['module_root'])
    require(module_root.is_absolute() and module_root.resolve() == module_root, 'redirected module root')
    # Preflight every application before switching any link.
    for name in software:
        version = token(manifest['versions'][name]['name'])
        module = module_root / name / version
        require(module.resolve() == module and digest(module) == manifest['versions'][name]['sha256'],
                'changed fixed version module')
        link = module.parent / channel(name)
        require(link.parent.resolve() == link.parent and (not link.exists() or link.is_symlink()),
                'later entry must be a symlink, not a regular module')
    for name in software:
        version = manifest['versions'][name]['name']
        link = module_root / name / channel(name)
        temporary = link.with_name(f'.{link.name}-{os.getpid()}')
        temporary.symlink_to(version)
        os.replace(temporary, link)
        print(f'{name}/{channel(name)} -> {version}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('promote', 'select'))
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--module-root', type=Path)
    parser.add_argument('--generation', required=True)
    parser.add_argument('--software', choices=tuple(SOFTWARE), nargs='+', default=list(SOFTWARE))
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--validated-catalog', type=Path)
    parser.add_argument('--checks', type=Path)
    args = parser.parse_args()
    require(args.root.is_absolute() and args.root.resolve() == args.root, 'beta root must be canonical')
    args.root.mkdir(parents=True, exist_ok=True, mode=0o755)
    lock = args.root / '.publication.lock'
    require(not lock.is_symlink(), 'redirected beta publication lock')
    with lock.open('a+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        if args.action == 'promote':
            require(all((args.module_root, args.bundle, args.validated_catalog, args.checks)),
                    'promote needs --module-root, --bundle, --validated-catalog and --checks')
            prepare(args.root, args.module_root, args.generation, args.bundle,
                    args.validated_catalog, args.checks, args.software)
        select(args.root, args.generation, args.software)


if __name__ == '__main__':
    main()
