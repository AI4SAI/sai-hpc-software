#!/usr/bin/env python3
"""Publish a paired MD delivery only after raw science, performance and export gates."""
import argparse
import json
import os
from pathlib import Path
import shlex

from md_controller import PROJECT, ROOT, CONTROL, artifact_path, validate_pair
from md_tracking import fingerprint
from module_publication import atomic_text, render_selector, tcl
from delivery_layout import catalog_dir
from remote_controller import safe_name
from source_cache import checksum
from md_acceptance_recovery import validate as validate_recovery


def canonical(value):
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n'


def regular(path):
    path = Path(path)
    if not path.is_file() or path.is_symlink() or path.resolve() != path:
        raise ValueError('publication requires a regular file without symlink ancestors')
    return path


def read_json(path):
    return json.loads(regular(path).read_text())


def verify_science(run_id):
    from md_acceptance_controller import verify
    return verify(run_id)


def verify_performance(run_id):
    from md_performance_controller import verify
    return verify(run_id)


def export_delivery(image, digest, destination, identities):
    from export_native import export_delivery as export
    return export(image, digest, destination, identities)


def validate_export(record, digest, identities):
    from export_native import validate_export_record
    return validate_export_record(record, digest, identities)


def build_record(build_run, *, controller_recovery=None):
    build_run = safe_name(build_run)
    directory = ROOT / 'runs' / build_run
    request = read_json(directory / 'request.json')
    delivery = validate_pair(request['delivery'])
    build_control = (validate_recovery(controller_recovery, ROOT, CONTROL, build_run, delivery)
                     if controller_recovery is not None else CONTROL)
    status = read_json(directory / 'results/status.json')
    image = regular(artifact_path(ROOT, delivery, build_run))
    sidecar = read_json(image.with_suffix('.json'))
    expected = dict(artifact=str(image), artifact_sha256=checksum(image), delivery=delivery,
                    build_verified=True, state='COMPLETED', exit_code='0:0')
    if (request.get('run_id') != build_run or request.get('controller') != str(build_control)
            or build_control.resolve() != build_control or not build_control.is_relative_to(ROOT / 'controller')
            or delivery['recipe_sha256'] != fingerprint(build_control)
            or any(status.get(key) != value or sidecar.get(key) != value
                   for key, value in expected.items())):
        raise ValueError('MD publication build, image or controller provenance changed')
    hashes = {'deepmd': checksum(regular(directory / 'job-deepmd.sbatch')),
              'lammps': checksum(regular(directory / 'job.sbatch'))}
    if (request.get('stage_job_script_sha256') != hashes
            or request.get('job_script_sha256') != hashes['lammps']):
        raise ValueError('MD publication build scripts changed')
    return delivery, image, expected['artifact_sha256']


def acceptance(build_run, science_run, performance_run):
    if performance_run not in (build_run + '-performance', build_run + '-performance-retest'):
        raise ValueError('MD publication accepts only the original performance attempt or its one named repeat')
    science_request = read_json(ROOT / 'runtime-tests' / safe_name(science_run) / 'request.json')
    recovery = science_request.get('controller_recovery')
    delivery, image, digest = build_record(build_run, controller_recovery=recovery)
    expected = dict(build_run=build_run, artifact=str(image), artifact_sha256=digest,
                    delivery=delivery)
    evidence = {}
    for kind, run_id, verifier in (('science', science_run, verify_science),
                                   ('performance', performance_run, verify_performance)):
        directory = ROOT / 'runtime-tests' / safe_name(run_id)
        request = read_json(directory / 'request.json')
        status = read_json(directory / 'results/status.json')
        if (request.get('run_id') != run_id or any(request.get(key) != value for key, value in expected.items())
                or request.get('controller_recovery') != recovery
                or status.get('state') != 'COMPLETED' or status.get('exit_code') != '0:0'):
            raise ValueError('MD acceptance request differs from the completed paired build')
        if kind == 'science' and (request.get('nodes') != 2 or request.get('ranks') != 4
                                  or status.get('scientific_verified') is not True):
            raise ValueError('MD publication requires completed two-node scientific acceptance')
        result = verifier(run_id)  # Reparse raw evidence; a stored JSON verdict is insufficient.
        if kind == 'performance':
            from md_performance_controller import performance_backends
            backends = performance_backends(request)
            if (result.get('performance_scope', 'all') != request.get('performance_scope', 'all')
                    or (request.get('performance_scope') == 'gpu'
                        and (result.get('performance_backends') != list(backends)
                             or set(result.get('reports', {})) != set(backends)))):
                raise ValueError('MD raw performance evidence differs from its declared scope')
        fields = ('artifact', 'artifact_sha256', 'delivery')
        if (any(result.get(key) != expected[key] for key in fields)
                or result.get('controller_recovery') != recovery
                or (kind == 'science' and result.get('passed') is not True)
                or (kind == 'performance' and (result.get('performance_verified') is not True
                                              or result.get('build_run') != build_run))):
            raise ValueError('MD raw acceptance failed or belongs to another delivery')
        evidence[kind] = dict(run_id=run_id, request_sha256=checksum(directory / 'request.json'),
                              evidence=result)
    return delivery, image, digest, evidence


def launcher_text(delivery, image, program):
    """Every wrapper pins its image, recipe and locked pair independently of env."""
    verifier = ('import sys; sys.path.insert(0, sys.argv[1]); from md_tracking import fingerprint; '
                'sys.exit(0 if fingerprint(sys.argv[1]) == sys.argv[2] else "published MD controller changed")')
    return '\n'.join([
        '#!/usr/bin/env bash', 'set -euo pipefail',
        'unset SAI_MD_IMPLEMENTATION SAI_MD_TEST_CONTROL SAI_MD_PROFILE_LMP SAI_MD_PERFORMANCE_CPU SAI_MD_TRACE_RANKS',
        'export SAI_MD_PUBLISHED=1',
        'export SAI_SOFTWARE_ROOT=' + shlex.quote(str(PROJECT)),
        'export SAI_MD_IMAGE=' + shlex.quote(str(image)),
        'export SAI_MD_DELIVERY=' + shlex.quote(canonical(delivery).strip()),
        shlex.join(['/usr/bin/python3', '-I', '-B', '-c', verifier, str(CONTROL), fingerprint(CONTROL)]),
        shlex.join(['exec', 'bash', str(CONTROL / 'md_runtime.sh'), program]) + ' "$@"', ''])


def module_payload(delivery, image, build_run, software):
    identity = delivery['identities'][software]
    stem = 'SAI_' + software.upper().replace('-', '_')
    peer = 'SAI_' + ('lammps' if software == 'deepmd-kit' else 'deepmd-kit').upper().replace('-', '_')
    directory = catalog_dir(ROOT, identity)
    fragment = directory / (build_run + '.module')
    launcher_dir = ROOT / 'controller/published' / delivery['recipe_sha256'] / build_run / software / 'bin'
    programs = ('dp', 'python') if software == 'deepmd-kit' else ('lmp',)
    wrappers = {str(launcher_dir / program): launcher_text(delivery, image, program) for program in programs}
    content = '\n'.join([
        '# Immutable paired MD image and launcher; science/performance/export accepted.',
        f'if {{[module-info mode load] && [info exists env({peer}_STACK_DIGEST)] && $env({peer}_STACK_DIGEST) ne {tcl(identity["stack_digest"])}}} {{error "loaded MD companion belongs to another locked pair"}}',
        f'if {{[module-info mode load] && [info exists env({peer}_IMAGE)] && $env({peer}_IMAGE) ne {tcl(image)}}} {{error "loaded MD companion uses another accepted image"}}',
        'prepend-path MODULEPATH /opt/modules/modulefiles/devtools',
        'module load apptainer/1.4.4 cuda/12.9.1 openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto',
        f'setenv {stem}_STACK_DIGEST {tcl(identity["stack_digest"])}',
        f'setenv {stem}_IMAGE {tcl(image)}',
        f'setenv {stem}_VERSION {tcl(identity["source_version"])}',
        f'setenv {stem}_PREFIX {tcl(identity["install_prefix"])}',
        f'prepend-path PATH {tcl(launcher_dir)}', ''])
    selector = ROOT / 'modulefiles/apps' / software / identity['track'] / identity['build_id']
    selector_text = render_selector(ROOT, software, identity['track'], identity['build_id'], software.upper())
    return fragment, content, selector, selector_text, wrappers


def prepare_modules(delivery, image, build_run):
    modules = {}
    for software in sorted(delivery['identities']):
        fragment, content, selector, selector_text, wrappers = module_payload(delivery, image, build_run, software)
        for path, text in wrappers.items():
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_text(path, text, immutable=True)
            path.chmod(0o555)
        fragment.parent.mkdir(parents=True, exist_ok=True)
        atomic_text(fragment, content, immutable=True)
        selector.parent.mkdir(parents=True, exist_ok=True)
        atomic_text(selector, selector_text, immutable=True)
        modules[software] = dict(modulefile=str(selector), module_fragment=str(fragment),
                                 module_fragment_sha256=checksum(fragment),
                                 launchers={path: checksum(path) for path in wrappers})
    return modules


def validate_modules(manifest, *, current=False):
    delivery = validate_pair(manifest['delivery'])
    image = Path(manifest['artifact'])
    build_run = safe_name(manifest['build_run'])
    if set(manifest.get('modules', {})) != set(delivery['identities']):
        raise ValueError('paired delivery requires both software modules')
    for software, metadata in manifest['modules'].items():
        fragment, content, selector, selector_text, wrappers = module_payload(delivery, image, build_run, software)
        if (metadata.get('modulefile') != str(selector) or regular(selector).read_text() != selector_text
                or metadata.get('module_fragment') != str(fragment) or regular(fragment).read_text() != content
                or metadata.get('module_fragment_sha256') != checksum(fragment)
                or set(metadata.get('launchers', {})) != set(wrappers)):
            raise ValueError('published MD module changed')
        for path, text in wrappers.items():
            path = regular(path)
            if (path.read_text() != text or not os.access(path, os.X_OK)
                    or checksum(path) != metadata['launchers'][str(path)]):
                raise ValueError('published MD runtime wrapper changed')
        pointer = fragment.parent / 'current.module'
        if current and (not pointer.is_symlink() or pointer.readlink() != Path(fragment.name)):
            raise ValueError('published MD selector does not select the accepted fragment')


def publish(build_run, science_run, performance_run):
    build_run, science_run, performance_run = map(safe_name, (build_run, science_run, performance_run))
    delivery, image, digest, evidence = acceptance(build_run, science_run, performance_run)
    identities = [delivery['identities'][name] for name in sorted(delivery['identities'])]
    destination = ROOT / 'exports' / build_run
    destination.parent.mkdir(parents=True, exist_ok=True)
    native_export = export_delivery(image, digest, destination, identities)
    validate_export(native_export, digest, identities)
    modules = prepare_modules(delivery, image, build_run)
    manifest = dict(schema=1, build_run=build_run, delivery=delivery, artifact=str(image),
                    artifact_sha256=digest, recipe_sha256=delivery['recipe_sha256'],
                    build_verified=True, scientific_verified=True, performance_verified=True,
                    published=True, native_export=native_export, modules=modules, acceptance=evidence,
                    runtime_launcher=str(CONTROL / 'md_runtime.sh'),
                    runtime_launcher_sha256=checksum(regular(CONTROL / 'md_runtime.sh')))
    performance = evidence['performance']['evidence']
    if 'performance_scope' in performance:
        manifest.update(performance_scope=performance['performance_scope'],
                        performance_backends=performance['performance_backends'])
    recovery = evidence['science']['evidence'].get('controller_recovery')
    if recovery is not None:
        manifest.update(controller_recovery=recovery,
                        acceptance_recipe_sha256=recovery['acceptance_recipe_sha256'])
    validate_modules(manifest)
    # Write the immutable receipt before exposing selectors. A retry can finish
    # activation, but cannot replace evidence for an already published run.
    atomic_text(image.with_suffix('.published.json'), canonical(manifest), immutable=True)
    for metadata in modules.values():
        fragment = Path(metadata['module_fragment'])
        temporary = fragment.parent / f'.current-md-module-{os.getpid()}'
        temporary.symlink_to(fragment.name)
        os.replace(temporary, fragment.parent / 'current.module')
    validate_modules(manifest, current=True)
    atomic_text(ROOT / 'runs' / build_run / 'results/publication.json', canonical(manifest), immutable=True)
    return manifest


def verify(build_run):
    build_run = safe_name(build_run)
    receipt = read_json(ROOT / 'runs' / build_run / 'results/publication.json')
    recovery = receipt.get('controller_recovery')
    delivery, image, digest = build_record(build_run, controller_recovery=recovery)
    manifest = read_json(image.with_suffix('.published.json'))
    expected = dict(schema=1, build_run=build_run, delivery=delivery, artifact=str(image),
                    artifact_sha256=digest, recipe_sha256=delivery['recipe_sha256'],
                    build_verified=True, scientific_verified=True, performance_verified=True, published=True)
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError('published MD manifest identity changed')
    if receipt != manifest:
        raise ValueError('MD publication was not completed')
    launcher = regular(CONTROL / 'md_runtime.sh')
    if manifest.get('runtime_launcher') != str(launcher) or manifest.get('runtime_launcher_sha256') != checksum(launcher):
        raise ValueError('published MD runtime launcher changed')
    accepted = manifest['acceptance']
    _, _, _, actual = acceptance(build_run, accepted['science']['run_id'], accepted['performance']['run_id'])
    for key in ('performance_scope', 'performance_backends'):
        if manifest.get(key) != actual['performance']['evidence'].get(key):
            raise ValueError('published MD performance scope changed')
    if (actual != accepted or actual['science']['evidence'].get('controller_recovery') != recovery
            or (recovery is not None and manifest.get('acceptance_recipe_sha256') != fingerprint(CONTROL))):
        raise ValueError('published MD raw acceptance evidence changed')
    validate_export(manifest['native_export'], digest,
                    [delivery['identities'][name] for name in sorted(delivery['identities'])])
    validate_modules(manifest)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    command = sub.add_parser('publish')
    command.add_argument('build_run'); command.add_argument('science_run'); command.add_argument('performance_run')
    command = sub.add_parser('verify'); command.add_argument('build_run')
    args = parser.parse_args()
    if args.operation == 'publish':
        result = publish(args.build_run, args.science_run, args.performance_run)
    else:
        result = verify(args.build_run)
    print(canonical(result), end='')
