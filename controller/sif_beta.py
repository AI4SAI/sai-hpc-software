#!/usr/bin/env python3
"""Maintain accepted branch-beta modules in stardust's home directory."""
import argparse
import copy
import fcntl
import json
from pathlib import Path
import re

from module_publication import atomic_text, tcl
from release_contract import PARTITIONS, SOFTWARE
from sif_modules import load_snapshot, render_module, verify_sources, write
from sif_runtime import PROGRAMS, digest, require


def token(value):
    require(bool(re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value)),
            'invalid beta generation')
    return value


def channel(software):
    return SOFTWARE[software]['development_ref'] + '-beta'


def stem(software):
    return 'SAI_SIF_' + software.upper().replace('-', '_')


def render_gateway(home_module):
    # Installed once in /opt. No changing version metadata lives here.
    return '#%Module1.0\n# Maintained by stardust; this gateway does not rotate.\nsource ' + tcl(home_module) + '\n'


def render_channel(root, software):
    key = stem(software) + '_BETA_GENERATION'
    return '\n'.join([
        '#%Module1.0', '# Select once at load; retain that generation through unload.',
        f'set sai_beta_root {tcl(root)}',
        f'if {{[info exists env({key})]}} {{',
        f'    set sai_beta_generation $env({key})',
        '} elseif {[module-info mode remove]} {',
        '    error "missing saved beta generation; refusing to unload a different snapshot"',
        '} else {',
        f'    set sai_beta_file [open [file join $sai_beta_root channels {software}] r]',
        '    set sai_beta_generation [string trim [read $sai_beta_file]]',
        '    close $sai_beta_file', '}',
        'if {![regexp {^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$} $sai_beta_generation]} {error "invalid beta generation"}',
        f'source [file join $sai_beta_root generations $sai_beta_generation modules {software}]',
        f'setenv {key} $sai_beta_generation', ''])


def render_template(software, module_root, *, cpu=False):
    mpi = software in ('cp2k', 'abacus', 'lammps')
    partition, qos = ('DSPRHBM', 'rush-cpu') if cpu else ('16V100', 'flood-1o2gpu')
    lines = ['#!/usr/bin/env bash', '# Copy to your case directory; edit account and resources before sbatch.',
             '#SBATCH --account=YOUR_ACCOUNT', f'#SBATCH --partition={partition}', f'#SBATCH --qos={qos}',
             '#SBATCH --nodes=1', f'#SBATCH --ntasks-per-node={2 if mpi else 1}',
             '#SBATCH --time=01:00:00', '#SBATCH --export=ALL', '#SBATCH --output=slurm-%j.out']
    lines += ['#SBATCH --cpus-per-task=2'] if cpu else ['#SBATCH --gpus-per-node=1']
    lines += ['', 'set -eo pipefail', 'source /etc/profile.d/lmod.sh', 'module purge',
              'module use ' + str(module_root), f'module load {software}/{channel(software)}',
              '', '# Run from a case directory under /home or /org. Inputs must be here or below.',
              'cd "${SLURM_SUBMIT_DIR:?}"', 'umask 077', 'export TMPDIR="$PWD/.sai-sif-$SLURM_JOB_ID"',
              'mkdir -p "$TMPDIR"', 'export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1',
              'export SLURM_EXPORT_ENV=ALL',
              'export OMPI_MCA_plm_slurm_args=--external-launcher',
              'export PRTE_MCA_plm_slurm_args=--external-launcher',
              f'printf "source=%s\\nimage=%s\\ntemplates=%s\\n" "${stem(software)}_SOURCE" "${stem(software)}_IMAGE" "${stem(software)}_TEMPLATES"', '']
    if mpi:
        command = {'cp2k': 'cp2k.psmp -i input.inp -o output.out',
                   'abacus': 'abacus > abacus.out',
                   'lammps': 'lmp -pk gpu 1 split 1.0 -in in.lmp -log log.lammps'}[software]
        lines += ['# The host mpirun starts the SIF command once per rank.',
                  'mpirun -np "${SLURM_NTASKS:?}" --map-by ppr:2:node:PE=1 --bind-to core ' + command]
    elif software == 'deepmd-kit':
        lines += ['# Direct GPU command; use deepmd-python for a Python script in this SIF.',
                  'dp --pt train input.json']
    else:
        lines += ['# GPUMD reads run.in, model.xyz and any referenced potential from this directory.',
                  'gpumd > gpumd.out']
    return '\n'.join(lines) + '\n'


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
    normalized = copy.deepcopy(snapshot)
    for app in normalized['applications'].values():
        for row in app['partitions'].values():
            if 'source_image' in row:
                row['image'] = row.pop('source_image')
    require(normalized == accepted, 'relocated bundle differs from the accepted catalog')
    selected = dict(snapshot, applications={name: snapshot['applications'][name] for name in software})
    verify_sources(selected)
    # Launcher paths are part of the fixed runtime, not the rotating module.
    import shlex
    for name in software:
        for program in PROGRAMS[name]:
            argv = ['/usr/bin/python3', '-I', '-B', str(bundle / 'libexec/sif_runtime.py'),
                    str(bundle / 'catalog.json'), name, program]
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
        require(not (module_root / (name + '-sif') / snapshot['applications'][name]['version']).exists(),
                'fixed module name already exists; preserve its generation')
    for name in software:
        app = snapshot['applications'][name]
        identity = next(iter(app['partitions'].values()))['identity']
        branch = SOFTWARE[name]['development_ref']
        templates = destination / 'templates' / name
        fixed = name + '-sif/' + app['version']
        description = (f"[SAI beta] {name} / {branch}\n"
                       f"Source version: {identity['source_version']}\n"
                       f"Commit: {identity['source_sha']}\n"
                       f"Accepted snapshot: {snapshot['snapshot']} (latest accepted, not necessarily upstream HEAD)\n"
                       f"Partitions: {', '.join(app['partitions'])}\n"
                       f"Fixed module: {fixed}\n"
                       f"Templates: {templates}\n\n"
                       "Copy gpu.sbatch into your case directory and edit YOUR_ACCOUNT and resources.\n"
                       + ("For DSPRHBM use cpu.sbatch.\n" if 'DSPRHBM' in app['partitions'] else '') +
                       f"On the login node: cp {templates}/gpu.sbatch ./run.sbatch; edit it, then sbatch run.sbatch.\n"
                       "Load on a compute node inside sbatch or an srun shell.\n"
                       "Set TMPDIR in /home or /org before host mpirun; keep inputs under the case directory.\n"
                       "SIF commands are wrappers: do not replace them with native /opt executable paths.\n"
                       "ABACUS/CP2K/LAMMPS use host mpirun; GPUMD and dp run directly.\n"
                       "Use deepmd-python for the SIF Python environment.\n"
                       f"For a fixed version: module use {module_root}; module load {fixed}\n")
        extra = {stem(name) + '_VERSION': app['version'], stem(name) + '_BRANCH': branch,
                 stem(name) + '_TEMPLATES': str(templates), stem(name) + '_SNAPSHOT': snapshot['snapshot']}
        notice = (f"[SAI beta] {name}/{branch}; commit={identity['source_sha'][:12]}; "
                  f"snapshot={snapshot['snapshot']}; templates={templates}")
        write(destination / 'modules' / name,
              render_module(bundle, name, app, help_text=description, notice=notice,
                            extra_environment=extra, compute_only=True))
        write(templates / 'gpu.sbatch', render_template(name, module_root))
        if 'DSPRHBM' in app['partitions']:
            write(templates / 'cpu.sbatch', render_template(name, module_root, cpu=True))
    files = {str(path.relative_to(destination)): digest(path)
             for path in destination.rglob('*') if path.is_file()}
    write(destination / 'manifest.json', json.dumps({
        'schema': 1, 'generation': generation, 'bundle': str(bundle),
        'catalog_sha256': digest(bundle / 'catalog.json'), 'runtime_sha256': snapshot['runtime_sha256'],
        'validated_catalog': str(validated_catalog), 'validated_catalog_sha256': digest(validated_catalog),
        'checks_sha256': digest(checks), 'software': software,
        'jobs': {part: report['job'] for part, report in reports.items()}, 'files': files,
    }, indent=2, sort_keys=True) + '\n')
    for name in software:
        home_module = module_root / name / channel(name)
        home_module.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        atomic_text(home_module, render_channel(root, name), immutable=True)
        fixed = module_root / (name + '-sif') / snapshot['applications'][name]['version']
        write(fixed, render_gateway(destination / 'modules' / name))
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
    directory = root / 'channels'
    directory.mkdir(parents=True, exist_ok=True, mode=0o755)
    for name in software:
        atomic_text(directory / name, generation + '\n')
        print(f'{name}/{channel(name)} -> {generation}', flush=True)


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
