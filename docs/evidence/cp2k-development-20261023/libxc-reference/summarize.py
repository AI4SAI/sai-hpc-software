#!/usr/bin/env python3
"""Read-only LibXC evidence summary; never grant CP2K publication/installation.

Run on the login node with the original project reference and smoke directories.
The original built-in/LibXC comparison and FAILED allocation remain unchanged.
Only JSON is written to stdout; no container, calculation or job is launched.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

ROOT = Path('/org/org-open-sai/proj-sai-hpc-software')
SOURCE = 'cfe5e5d87aaf302953f14eb43587bef2411a8fb2'
CONTROL = '10992f818af71543bb39cb81e0f60c9f7ede9535'
RECIPE = 'ae6467ac2934deecf1d610c9d60cb8c5100cf931d2c27aaa66819ba85ca76a79'
PROTOCOL = '270b59212095643bccf398b2cf74e5a09996222d64f830e7ce6f29800adaecf9'
CHECKER = '7a5b39e9edd03a4f053f4389a3481dbaae3cf76e7b5c0a305c4b55b4bc33585a'
RUNNER = '046a3f534b5e10f1cd4e97d3e9617c143265d38a50c03b432e1f775fdd4e97fa'
SMOKE = '87b3ad84ffd18254a60cd2ba737f0a7ba39f5f11f50cec9e21f6c3eeb9913703'
PARSER = 'aefd8b234b1b43f2815a6af5f3502204319a490f4919ad417421d920034a10e3'
TOLERANCES = {'builtin_host': {'energy_ha': 1e-6, 'force_ha_bohr': 1e-5},
              'host_device': {'energy_ha': 1e-8, 'force_ha_bohr': 1e-7}}
KERNEL = r'work_gga_gpu_(?:exc|vxc)_unpol'
SYMBOLS = {'xc_version', 'xc_func_alloc', 'xc_func_init_flags', 'xc_func_get_info',
           'xc_func_info_get_flags', 'xc_func_info_get_number', 'xc_func_info_get_n_ext_params',
           'xc_func_info_get_ext_params_name', 'xc_func_get_ext_params', 'xc_gga_exc_vxc',
           'xc_func_end', 'xc_func_free'}
EVIDENCE = {}


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(path):
    path = Path(path)
    require(path.is_absolute() and path.is_relative_to(ROOT) and path.resolve(strict=True) == path,
            'noncanonical or nonproject evidence path: ' + str(path))
    return path


def sha(path):
    path = canonical(path)
    require(path.is_file() and not path.is_symlink(), 'evidence is not a regular file')
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            value.update(block)
    digest = value.hexdigest()
    key = str(path.relative_to(ROOT))
    require(key not in EVIDENCE or EVIDENCE[key] == digest, 'evidence changed while reading: ' + key)
    EVIDENCE[key] = digest
    return digest


def read(path):
    sha(path)
    return path.read_text()


def load(path):
    return json.loads(read(path))


def verify_hashes(directory, hashes):
    for name, expected in hashes.items():
        require(not Path(name).is_absolute() and '..' not in Path(name).parts, 'unsafe relative evidence path')
        require(sha(directory / name) == expected, 'changed evidence: ' + str(directory / name))


def reference_math(task, request, reference):
    protocol = load(task / 'reference-v1/protocol.json')
    require(sha(task / 'reference-v1/protocol.json') == PROTOCOL == request['protocol_sha256'],
            'reference protocol hash differs')
    require(protocol == reference.protocol_for(task / 'reference-v1'), 'raw reference or selection differs')
    result = load(task / 'results/reference-result.json')
    require(result['protocol'] == protocol and result['protocol_sha256'] == PROTOCOL
            and result['script_sha256'] == CHECKER and result['source_archive_sha256'] == reference.ARCHIVE_SHA256,
            'reference result provenance differs')
    require(result['status'] == 'passed' and result['numerical_reference_verified'] is True
            and result['scientific_verified'] is False
            and result['original_supplement_failure_preserved'] is True, 'reference result scope differs')
    require(result['version'] == [7, 1, 2] and result['backend'] == 'HOST'
            and result['install_prefix'] == request['identity']['install_prefix']
            and result['library'] == request['library'] and result['library_sha256'] == request['library_sha256'],
            'reference used another library')
    require(set(result['symbol_providers']) == SYMBOLS
            and set(result['symbol_providers'].values()) == {request['library']}, 'LibXC symbol provider differs')
    require(set(result['functionals']) == set(reference.SOURCES), 'missing or extra functional results')
    count, evaluations, worst_ratio = 0, 0, Decimal(0)
    for name in reference.SOURCES:
        raw = reference.parse_reference(task / 'reference-v1' / (name + '.data'))
        results = result['functionals'][name]
        require([entry['nspin'] for entry in results] == [2, 1], 'missing spin evaluation')
        for entry in results:
            spin = entry['nspin']
            require(entry['flags'] & (reference.HOST | reference.DEVICE) == reference.HOST
                    and entry['parameters'] == reference.PARAMETERS[name], 'backend or default parameters differ')
            selected = [row for row in raw if reference.selection(row)['unpolarized' if spin == 1 else 'polarized']]
            require(len(entry['records']) == len(selected), 'missing reference points')
            for actual, row in zip(entry['records'], selected):
                values = row['values']
                d = {key: Decimal(value) for key, value in values.items()}
                require(actual['reference_index'] == row['index'] and actual['source_line'] == row['source_line']
                        and actual['inputs'] == {key: values[key] for key in reference.INPUTS}, 'reference point differs')
                expected = [('zk_energy_density', d['zk'], reference.reference_bound(values['zk']))]
                if spin == 2:
                    expected += [(key, d[key], reference.reference_bound(values[key])) for key in reference.OUTPUTS[1:]]
                else:
                    for key, members, divisor in [('vrho', ('vrhoa', 'vrhob'), 2),
                                                  ('vsigma', ('vsigmaaa', 'vsigmaab', 'vsigmabb'), 4)]:
                        expected.append((key, sum(d[k] for k in members) / divisor,
                                         sum(reference.reference_bound(values[k]) for k in members) / divisor))
                require(len(actual['checks']) == len(expected), 'missing scalar checks')
                require(math.isfinite(actual['exc_per_particle'])
                        and actual['checks'][0]['actual'] == float(d['rhoa'] + d['rhob']) * actual['exc_per_particle'],
                        'recorded energy mapping differs')
                for check, (quantity, value, bound) in zip(actual['checks'], expected):
                    rebuilt = reference.comparison(quantity, check['actual'], value, bound)
                    require(check == rebuilt and rebuilt['passed'], 'independent mathematical comparison failed')
                    worst_ratio = max(worst_ratio, Decimal(rebuilt['absolute_error']) / bound)
                    count += 1
                require(actual['passed'] is True, 'reference point status differs from recomputed checks')
                evaluations += 1
            require(entry['passed'] is True, 'spin result status differs from recomputed checks')
    require((evaluations, count) == (54, 258), 'incomplete mathematical reference coverage')
    return dict(evaluations=evaluations, scalar_comparisons=count, max_error_over_bound=str(worst_ratio),
                protocol_sha256=PROTOCOL, library=result['library'], library_sha256=result['library_sha256'],
                version=result['version'], symbol_providers=result['symbol_providers'])


def delta(candidate, reference, bounds):
    require(len(candidate['forces_ha_bohr']) == len(reference['forces_ha_bohr']), 'different atom counts')
    energy = abs(candidate['energy_ha'] - reference['energy_ha'])
    force = max(abs(a - b) for x, y in zip(candidate['forces_ha_bohr'], reference['forces_ha_bohr'])
                for a, b in zip(x, y))
    require(math.isfinite(energy) and math.isfinite(force), 'nonfinite CP2K comparison')
    return dict(energy_delta_ha=energy, max_force_delta_ha_bohr=force, tolerances=bounds,
                within_original_tolerances=energy <= bounds['energy_ha'] and force <= bounds['force_ha_bohr'])


def smoke_results(task, request, reference_request, fixtures, reference):
    gpu = request['identity']['gpus'] > 0
    require(request['modes'] == ['builtin', 'host'] + (['device'] if gpu else [])
            and request['tolerances'] == TOLERANCES and request['kernel_pattern'] == KERNEL,
            'original smoke protocol changed')
    require(request['libxc_archive_sha256'] == reference.ARCHIVE_SHA256
            and reference.ARCHIVE_SHA256 in read(task / 'inspect/libxc-source-archives.sha256'),
            'LibXC source archive differs')
    ldd = read(task / 'inspect/ldd.txt')
    library = reference_request['library']
    prefix = request['identity']['install_prefix']
    expected_library = prefix + ('/dependencies/libxc/lib/libxc.so.15' if gpu else '/lib64/libcp2k.so.2026.2')
    require(library == expected_library and 'not found' not in ldd
            and re.search(r'=>\s+' + re.escape(library) + r'\s+\(', ldd), 'private provider missing from ldd')
    cache = read(task / 'inspect/CMakeCache.txt')
    version = read(task / 'inspect/version.txt')
    require('Source code revision ' + SOURCE[:10] in version, 'executed CP2K source revision differs')
    hashes = read(task / 'inspect/libxc-libraries.sha256')
    if gpu:
        require(re.search(r'^CP2K_ENABLE_LIBXC_GPU:BOOL=ON$', cache, re.M), 'LibXC GPU build flag missing')
        require(re.search(r'^' + reference_request['library_sha256'] + r'\s+' + re.escape(library) + r'$', hashes, re.M),
                'CP2K smoke and mathematical reference library hashes differ')
    else:
        require(not re.search(r'\blibxc(?:f03)?\.so', ldd), 'CPU static provider unexpectedly loads shared LibXC')
    science, loaders = {}, {}
    for mode in request['modes']:
        work = task / mode
        require(read(work / 'cp2k.exit').strip() == '0', 'CP2K calculation failed: ' + mode)
        log = read(work / 'cp2k.log')
        require('FUNCTIONAL| LIBXC compiled against version' not in log, 'LibXC runtime/header version differs')
        if mode != 'builtin':
            require(re.search(r'FUNCTIONAL\| LIBXC version\s+7\.1\.2\s*$', log, re.M), 'executed LibXC version differs')
        science[mode] = fixtures.parse_science(log, read(work / 'forces.xyz'), read(work / 'elapsed.txt'), 'water-gpw')
        lines = [line.strip() for path in sorted(work.glob('loader.*')) for line in read(path).splitlines()
                 if re.search(r'calling init:\s+' + re.escape(library) + r'\s*$', line)]
        require(lines, 'actual private library initialization missing: ' + mode)
        loaders[mode] = sorted(set(lines))
    builtin = delta(science['host'], science['builtin'], TOLERANCES['builtin_host'])
    failed_raw = read(task / 'results/failed.json')
    failed = json.loads(failed_raw)
    require(not builtin['within_original_tolerances'] and failed['status'] == 'failed'
            and failed['job_id'] == read(task / 'job.id').strip()
            and failed['error'] == 'LibXC energy/force tolerance exceeded: '
            + str((builtin['energy_delta_ha'], builtin['max_force_delta_ha_bohr'])),
            'original failed comparison no longer agrees with raw results')
    require(not (task / 'results/accepted.json').exists(), 'original failed supplement has an acceptance receipt')
    host_device, kernels = None, []
    if gpu:
        host_device = delta(science['device'], science['host'], TOLERANCES['host_device'])
        require(host_device['within_original_tolerances'], 'HOST/DEVICE comparison failed original thresholds')
        database = canonical(task / 'device/libxc-device.sqlite')
        sha(database)
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
            connection.execute('PRAGMA query_only=ON')
            rows = connection.execute('''SELECT s.value, COUNT(*), SUM(k.end-k.start)
                FROM CUPTI_ACTIVITY_KIND_KERNEL k JOIN StringIds s ON s.id=k.demangledName
                GROUP BY s.value''').fetchall()
        kernels = [dict(name=name, instances=count, total_gpu_ns=duration) for name, count, duration in rows
                   if re.search(KERNEL, name) and count > 0 and duration > 0]
        require(kernels, 'no positive LibXC-specific CUDA kernels')
    return dict(science=science, private_loader=loaders, host_device=host_device, libxc_cuda_kernels=kernels,
                builtin_host_default_parameterization=dict(comparison=builtin, original_failure=failed,
                    original_failure_json=failed_raw, original_failure_preserved=True,
                    explanation='CP2K built-in PBE and LibXC 7.1.2 use different default PBE/PW92 constants; '
                                'this remains a failed original comparison, characterized separately.'))


def accounting(reference_task, smoke_task, reference_request):
    jobs = [read(task / 'job.id').strip() for task in (reference_task, smoke_task)]
    require(all(re.fullmatch(r'\d+', job) for job in jobs), 'invalid job identity')
    fields = 'JobIDRaw,State,ExitCode,Partition,Account,WorkDir,Elapsed,NodeList,AllocCPUS,AllocTRES'
    argv = ['sacct', '-X', '-nP', '-j', ','.join(jobs), '-o', fields]
    run = subprocess.run(argv, check=True, text=True, capture_output=True, cwd=ROOT)
    rows = [line.split('|') for line in run.stdout.splitlines() if line.strip()]
    require(len(rows) == 2 and {row[0] for row in rows} == set(jobs) and all(len(row) == 10 for row in rows),
            'missing or ambiguous scheduler accounting')
    records = {row[0]: dict(zip(fields.split(','), row)) for row in rows}
    for task, job in zip((reference_task, smoke_task), jobs):
        record = records[job]
        require(record['Partition'] == reference_request['identity']['partition']
                and record['Account'] == 'proj-sai-hpc-software' and record['WorkDir'] == str(task),
                'allocation provenance differs')
    require(records[jobs[0]]['State'] == 'COMPLETED' and records[jobs[0]]['ExitCode'] == '0:0'
            and int(records[jobs[0]]['AllocCPUS']) == reference_request['resources']['expected_allocated_cpus'],
            'independent reference allocation did not complete correctly')
    # The smoke allocation really FAILED its built-in/default-parameter comparison.
    require(records[jobs[1]]['State'] == 'FAILED' and records[jobs[1]]['ExitCode'] == '1:0',
            'original smoke scheduler failure differs')
    return dict(argv=argv, stdout=run.stdout, stderr=run.stderr, records=records)


def summarize(reference_task, smoke_task):
    sys.dont_write_bytecode = True
    os.environ['SAI_SOFTWARE_ROOT'] = str(ROOT)
    for task in (reference_task, smoke_task):
        canonical(task)
        require(task.parent == ROOT / 'runtime-tests', 'requires original project runtime-test directories')
    rr, sr = (load(task / 'request.json') for task in (reference_task, smoke_task))
    for key in ('identity', 'artifact', 'artifact_sha256', 'controller', 'controller_hashes', 'build_run_id'):
        require(rr[key] == sr[key], 'reference and smoke identity differs: ' + key)
    identity = rr['identity']
    require(identity['source_sha'] == SOURCE and identity['recipe_sha256'] == RECIPE
            and identity['software'] == 'cp2k' and identity['track'] == 'development', 'unexpected development SIF')
    control = canonical(Path(rr['controller']))
    base_run_id = '39983165813-1-development-' + identity['target'] + '-2026-10-23-' + SOURCE[:12]
    require(control == ROOT / 'controller' / CONTROL / base_run_id, 'unexpected frozen controller')
    verify_hashes(control, rr['controller_hashes'])
    for task, request in ((reference_task, rr), (smoke_task, sr)):
        verify_hashes(task, request['script_hashes'])
        require(request['original_launcher_sha256'] == sha(control / 'cp2k'), 'original launcher differs')
    require(sha(reference_task / 'libxc_reference.py') == CHECKER
            and sha(reference_task / 'run_reference.py') == RUNNER
            and sha(smoke_task / 'libxc_smoke.py') == SMOKE
            and sha(control / 'cp2k_benchmark.py') == PARSER, 'frozen verifier code differs')
    spec = importlib.util.spec_from_file_location('libxc_reference_frozen', reference_task / 'libxc_reference.py')
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)
    sys.path.insert(0, str(control))
    fixtures = importlib.import_module('cp2k_benchmark')
    delivery = importlib.import_module('delivery_layout')
    build = canonical(ROOT / 'runs' / rr['build_run_id'])
    require(sha(build / 'request.json') == rr['build_request_sha256'], 'original build request changed')
    for request_path in (build / 'request.json', ROOT / 'runs' / base_run_id / 'request.json'):
        build_request = load(request_path)
        require(build_request['controller'] == str(control) and build_request['identity'] == identity,
                'original/recovery build request no longer uses the same frozen controller and identity')
    image = canonical(Path(rr['artifact']))
    require(read(build / 'artifact.path').strip() == str(image)
            and image == delivery.artifact_path(ROOT, identity, rr['build_run_id']), 'wrong final SIF')
    manifest = delivery.load_artifact(ROOT, image, software='cp2k', target=identity['target'])
    require(manifest['identity'] == identity and manifest['sha256'] == rr['artifact_sha256'], 'final SIF differs')
    sha(image.with_suffix('.json'))
    receipt = load(reference_task / 'results/execution.json')
    require(receipt['returncode'] == 0 and receipt['request_sha256'] == sha(reference_task / 'request.json')
            and receipt['job_id'] == read(reference_task / 'job.id').strip()
            and receipt['argv'] == rr['outer_argv'] == ['/usr/bin/bash', str(reference_task / 'runtime.sh')],
            'reference execution receipt differs')
    verify_hashes(reference_task / 'results', receipt['results_sha256'])
    mathematics = reference_math(reference_task, rr, reference)
    smoke = smoke_results(smoke_task, sr, rr, fixtures, reference)
    scheduler = accounting(reference_task, smoke_task, rr)
    for relative in list(EVIDENCE):
        sha(ROOT / relative)
    return dict(schema=1, purpose='read-only-libxc-mathematics-and-backend-summary',
                summarized_at=datetime.now(timezone.utc).isoformat(), identity=identity,
                artifact=str(image), artifact_sha256=manifest['sha256'],
                reference_task=str(reference_task), smoke_task=str(smoke_task),
                libxc_math_verified=True, libxc_backend_verified=True,
                backend_scope='HOST and DEVICE' if identity['gpus'] else 'HOST only; no GPU claim',
                cp2k_science_publication_or_installation_status_granted=False,
                original_supplement_status='failed', original_receipts_modified=False,
                mathematics=mathematics, cp2k_libxc=smoke, accounting=scheduler,
                summary_script_sha256=sha(Path(__file__).resolve()), evidence_sha256=EVIDENCE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference-task', type=Path, required=True)
    parser.add_argument('--smoke-task', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(summarize(args.reference_task, args.smoke_task), sort_keys=True, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
