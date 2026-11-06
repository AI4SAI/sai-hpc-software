#!/usr/bin/env python3
"""Recover only a failed read-only export after a clean dependency build."""
import argparse
import json
from pathlib import Path
import re
import shlex

import cp2k_devtools_stage as stage
from cp2k_image_driver import APPTAINER, module_load_command, wrap_command
from devtools_install import checksum, require
from remote_controller import TARGETS, project_batch_directives, project_qos, safe_name

READONLY_EXPORT = '''import errno, os, shutil, sys
try:
    fd = os.open('/workspace/.sai-readonly-export-probe', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except OSError as error:
    if error.errno != errno.EROFS:
        raise
else:
    os.close(fd)
    raise RuntimeError('Export overlay unexpectedly permits writes')
print('READONLY_EXPORT_WRITE_REJECTED', file=sys.stderr, flush=True)
with open('/workspace/final.squashfs', 'rb') as source:
    shutil.copyfileobj(source, sys.stdout.buffer, 1024 * 1024)
'''


def overlay_stat(path):
    require(path.is_file() and path.resolve() == path, 'Missing or redirected source overlay')
    stat = path.stat()
    return dict(size=stat.st_size, mtime_ns=stat.st_mtime_ns, ctime_ns=stat.st_ctime_ns)


def export_snapshot(project, source_id, overlay_sha256):
    """Retain the failed original; authorize a new export, never its old output."""
    require(bool(re.fullmatch('[0-9a-f]{64}', overlay_sha256)), 'Invalid overlay digest')
    run, request = stage.submitted(project, source_id)
    require(not any(k in request for k in ('source_build', 'source_export')), 'Use the original build')
    job, state = stage.accounting(run)
    require(job == request['job_id'] and state == ['FAILED', '74:0'], 'Not a failed export allocation')
    stage.build_payload_evidence(project, run, request)
    results = run / 'results'
    require(any(stage.read_json(p).get('readonly', False) is False
                for p in results.glob('managed-overlay-*.json')), 'Missing clean writable build receipt')
    require(not (run / 'libraries.sif').exists(), 'Source already has a SIF; use reverification')
    require(not list(results.glob('managed-overlay-failure-*')), 'Managed phase failed')
    failures = sorted(results.glob('driver-cleanup-failure-*.json'))
    require(len(failures) == 1, 'Expected exactly one read-only export failure')
    failure = stage.read_json(failures[0])
    target = TARGETS[stage.PARTITIONS[request['partition']]]
    gpus = 2 if request['partition'] == '8V100V0' else target['gpus']
    jobs = 12 if gpus == 2 else target.get('build_jobs', 8)
    expected = stage.library_container(Path(project) / 'containers/base/minimal-v1.sif',
        ['/usr/bin/cat', '/workspace/final.squashfs'], overlay=str(run / 'work.ext3') + ':ro', jobs=jobs)
    expected = [str(APPTAINER), '--config', str(run / 'runtime/apptainer-fuse2fs.conf'), *expected[1:]]
    require(failure.get('argv') == expected and failure.get('returncode') == 0
            and failure.get('reason') == 'image driver teardown failed after payload success'
            and 'Terminating fuse2fs after timeout' in failure.get('stderr_tail', ''),
            'Failure was not the known read-only export teardown')
    names = [*stage.BUILD_EVIDENCE, *[p.name for p in sorted(results.glob('managed-overlay-*'))]]
    for name in names:
        require((results / name).resolve() == results / name, 'Redirected build evidence')
    return dict(run_id=source_id, job_id=job, partition=request['partition'], state=state,
                request_sha256=checksum(run / 'request.json'), overlay_sha256=overlay_sha256,
                overlay_stat=overlay_stat(run / 'work.ext3'),
                evidence_sha256={name: checksum(results / name) for name in names},
                failure_sha256={p.name: checksum(p) for p in failures})


def render(project, controller, run, source, node):
    project, controller, run = stage.paths(project, controller, run)
    node = safe_name(node)
    require('[' not in node, 'Choose one recovery node')
    partition = source['partition']
    target_name = stage.PARTITIONS[partition]
    gpus = TARGETS[target_name]['gpus']
    original = project / 'runs' / source['run_id'] / 'work.ext3'
    overlay, sif = run / 'work.ext3', run / 'libraries.sif'
    # The copy is sealed before any mount. Both backing FUSE and directory
    # overlay remain read-only; the completed source image is never mounted.
    emit = stage.library_container(project / 'containers/base/minimal-v1.sif',
        ['/usr/bin/python3', '-B', '-c', READONLY_EXPORT], overlay=str(overlay) + ':ro', jobs=2)
    emit = wrap_command(project, run, controller, emit)
    q = shlex.quote
    digest_check = lambda path: shlex.join(['printf', '%s  %s\n', source['overlay_sha256'], str(path)]) + ' | sha256sum --check --status'
    helper = ['/usr/bin/python3', '-B', str(controller / 'cp2k_devtools_reexport.py')]
    lines = ['#!/bin/bash', '#SBATCH --job-name=sai-devtools-export-' + partition,
        '#SBATCH --partition=' + partition, '#SBATCH --qos=' + project_qos(project, target_name),
        '#SBATCH --nodes=1', '#SBATCH --nodelist=' + node, '#SBATCH --ntasks=1',
        '#SBATCH --export=NIL', '#SBATCH --time=60',
        '#SBATCH --output=' + str(run / 'results/slurm-%j.log'), *project_batch_directives(project, run)]
    lines += (['#SBATCH --gpus-per-node=' + str(gpus)] if gpus else
              ['#SBATCH --cpus-per-task=2', '#SBATCH --mem=4G'])
    lines += ['set -eo pipefail', 'export PATH=/usr/bin:/bin LD_LIBRARY_PATH="" LD_PRELOAD=""',
        'source /etc/profile.d/lmod.sh', module_load_command(), 'set -u', 'umask 077',
        'unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH',
        'export TMPDIR=' + q(str(run / 'runtime')), 'export APPTAINER_TMPDIR="$TMPDIR"',
        'export APPTAINER_CACHEDIR=' + q(str(run / 'apptainer-cache')),
        shlex.join([*helper, 'check-source', str(project), run.name]),
        'test ! -e ' + q(str(overlay)),
        shlex.join(['cp', '--sparse=always', '--reflink=auto', '--', str(original), str(overlay)]),
        shlex.join(['chmod', '400', str(overlay)]), digest_check(overlay),
        shlex.join(['/usr/sbin/e2fsck', '-fn', str(overlay)]) + ' > ' + q(str(run / 'results/reexport-fsck.log')) + ' 2>&1',
        shlex.join(emit) + ' > ' + q(str(run / 'libraries.squashfs')),
        shlex.join([*helper, 'check-source', str(project), run.name]),
        shlex.join(['apptainer', 'sif', 'new', str(sif)]),
        shlex.join(['apptainer', 'sif', 'add', '--datatype', '4', '--partfs', '1', '--parttype', '2',
                    '--partarch', '2', '--groupid', '1', str(sif), str(run / 'libraries.squashfs')]),
        shlex.join(stage.verification_command(run, controller, partition)),
        shlex.join(['sha256sum', str(sif)]) + ' > ' + q(str(run / 'results/artifact.sha256')),
        shlex.join(['chmod', '444', str(sif)]),
        shlex.join([*helper, 'record', str(project), run.name]),
        'echo DEVTOOLS_REEXPORT_CONSUMERS_VERIFIED_NOT_CP2K_ACCEPTANCE']
    return '\n'.join(lines) + '\n'


def check_source(project, run, request):
    source = request['source_export']
    require(export_snapshot(project, source['run_id'], source['overlay_sha256']) == source,
            'Original export source or evidence changed')
    for name, digest in source['evidence_sha256'].items():
        require(checksum(run / 'results' / name) == digest, 'Copied build evidence changed')
    return source


def export_receipt(run, source):
    records = []
    for path in (run / 'results').glob('managed-overlay-*.json'):
        if path.name in source['evidence_sha256']:
            continue
        r = stage.read_json(path)
        require(r.get('readonly') is True and r.get('overlay') == str(run / 'work.ext3')
                and r.get('completed') is True and r.get('accepted') is True
                and r.get('forced_stop') is False and r.get('payload_returncode') == 0
                and r.get('unmount_returncode') == 0 and r.get('driver_returncode') == 0,
                'New read-only export did not exit cleanly')
        records.append(path)
    require(len(records) == 1, 'Expected exactly one new managed read-only export')
    return records[0]


def record(project, run, request):
    source = check_source(project, run, request)
    stage.built_evidence(project, run, request)
    receipt = export_receipt(run, source)
    require(checksum(run / 'work.ext3') == source['overlay_sha256'], 'Read-only export changed its overlay')
    names = (receipt.name, 'reexport-fsck.log', 'artifact.sha256', 'sif-consumer/passed.json')
    evidence = {name: checksum(run / 'results' / name) for name in names}
    with (run / 'results/reexport-success.json').open('x') as stream:
        json.dump(dict(source=source, overlay_sha256=source['overlay_sha256'], readonly=True,
                       sif_sha256=checksum(run / 'libraries.sif'), evidence_sha256=evidence), stream, sort_keys=True)
        stream.write('\n')


def validate_reexport(project, run, request):
    source = check_source(project, run, request)
    receipt = export_receipt(run, source)
    record = stage.read_json(run / 'results/reexport-success.json')
    require(record.get('source') == source and record.get('readonly') is True
            and record.get('overlay_sha256') == source['overlay_sha256'], 'Invalid reexport provenance')
    require(record.get('sif_sha256') == checksum(run / 'libraries.sif'), 'Reexport SIF changed')
    expected = {name: checksum(run / 'results' / name) for name in
                (receipt.name, 'reexport-fsck.log', 'artifact.sha256', 'sif-consumer/passed.json')}
    require(record.get('evidence_sha256') == expected, 'Reexport acceptance evidence changed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='op', required=True)
    submit = subs.add_parser('submit')
    for name in ('project', 'controller', 'source_id', 'run_id', 'overlay_sha256', 'node'):
        submit.add_argument(name)
    for op in ('check-source', 'record'):
        check = subs.add_parser(op)
        check.add_argument('project'); check.add_argument('run_id')
    args = parser.parse_args()
    if args.op == 'submit':
        project, controller, run = stage.paths(args.project, args.controller, Path(args.project) / 'runs' / safe_name(args.run_id))
        source = export_snapshot(project, args.source_id, args.overlay_sha256)
        stage.submit_script(project, controller, run, source['partition'],
                            render(project, controller, run, source, args.node), source_export=source)
    else:
        run, request = stage.submitted(args.project, args.run_id)
        if args.op == 'check-source':
            check_source(args.project, run, request)
        else:
            record(args.project, run, request)


if __name__ == '__main__':
    main()
