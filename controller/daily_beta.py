#!/usr/bin/env python3
"""Revalidate, test and atomically rotate one daily development delivery."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import uuid

import beta_candidates as candidates
import beta_check
import sif_beta
from remote_controller import SAI_PROJECT_ROOT, safe_name
from sif_modules import install, load_snapshot
from sif_runtime import digest, require

PROJECT = SAI_PROJECT_ROOT
PUBLIC = Path('/home/stardust/sai-hpc-software')
ROOT = PUBLIC / 'sif-beta'
MODULES = PUBLIC / 'modulefiles/apps'
ACTIVE = {'PENDING', 'RUNNING', 'CONFIGURING', 'COMPLETING', 'SUSPENDED', 'REQUEUED', 'RESIZING'}


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def current(software):
    link = MODULES / software / sif_beta.channel(software)
    require(link.is_symlink() and link.parent.resolve() == link.parent, 'missing beta gateway target')
    fixed = link.readlink()
    require(fixed.name == str(fixed), 'non-relative beta version pointer')
    match = re.search(r'^set sai_beta_generation "([A-Za-z0-9_.-]+)"$', link.read_text(), re.M)
    require(match is not None, 'unrecognized fixed beta module')
    generation = safe_name(match[1])
    manifest = read(ROOT / 'generations' / generation / 'manifest.json')
    require(manifest['versions'][software]['name'] == str(fixed)
            and manifest['versions'][software]['sha256'] == digest(link.resolve()), 'changed active module')
    return dict(generation=generation, fixed=str(fixed), bundle=manifest['bundle'])


def accounting(job, output, partition):
    require(re.fullmatch('[1-9][0-9]*', job), 'invalid runtime job id')
    result = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', job,
        '-o', 'JobIDRaw,State,ExitCode,Partition,Account,WorkDir,NodeList'], text=True)
    rows = [line.split('|') for line in result.splitlines() if line.split('|')[0] == job]
    require(len(rows) <= 1, 'ambiguous runtime allocation')
    if not rows:
        return None
    row = rows[0]
    require(row[3:6] == [partition, 'proj-sai-hpc-software', str(output)], 'runtime allocation provenance changed')
    return row


def submit(directory, software, partition, bundle, fixtures, exclude=''):
    if not directory.exists():
        staging = directory.with_name('.' + directory.name + '-prepare-' + uuid.uuid4().hex)
        beta_check.prepare(software, partition, bundle, staging, fixtures)
        script = beta_check.render(software, partition, bundle, directory)
        if exclude:
            require(bool(re.fullmatch('[A-Za-z0-9-]+(?:,[A-Za-z0-9-]+)*', exclude)), 'invalid failed-node exclusion')
            script = script.replace('set -eo pipefail\n', '#SBATCH --exclude=' + exclude + '\nset -eo pipefail\n', 1)
        (staging / 'job.sbatch').write_text(script)
        staging.rename(directory)
    receipt = directory / 'submission.json'
    if receipt.exists():
        saved = read(receipt)
        require(digest(directory / 'job.sbatch') == saved['script_sha256'], 'changed submitted runtime script')
        require(digest(directory / 'request.json') == saved['request_sha256'], 'changed runtime check request')
        state = subprocess.check_output(['squeue', '-h', '-j', saved['job'], '-o', '%T|%r'], text=True).strip()
        if state == 'PENDING|JobHeldUser':
            subprocess.run(['scontrol', 'release', saved['job']], check=True)
        return saved
    # Held submission makes the job handle durable before any computation.
    subprocess.run(['sbatch', '--test-only', str(directory / 'job.sbatch')], check=True, capture_output=True)
    job = subprocess.check_output(['sbatch', '--hold', '--parsable', str(directory / 'job.sbatch')], text=True).strip().split(';')[0]
    require(job.isdigit(), 'invalid submitted runtime job')
    saved = dict(job=job, partition=partition, output=str(directory), exclude=exclude,
                 script_sha256=digest(directory / 'job.sbatch'), request_sha256=digest(directory / 'request.json'))
    save(receipt, saved)
    subprocess.run(['scontrol', 'release', job], check=True)
    return saved


def retry_node(directory, accounting_row):
    text = '\n'.join(p.read_text(errors='replace')[-65536:] for p in directory.rglob('*.log'))
    if 'uncorrectable ECC error' in text or ('container creation failed: mount hook function failure:' in text
                                            and 'fuse2fs failed to mount' in text):
        node = accounting_row[6]
        require(bool(re.fullmatch('[A-Za-z0-9-]+', node)), 'expected one failed runtime node')
        return node
    return None


def check_runtime(task, software, bundle, fixtures, timeout=18000):
    snapshot = load_snapshot(bundle / 'catalog.json')
    saved_jobs = task / 'jobs.json'
    if saved_jobs.exists():
        jobs = read(saved_jobs)
        require({job['partition'] for job in jobs} == set(snapshot['applications'][software]['partitions']),
                'saved runtime partition matrix changed')
        for job in jobs:
            require(Path(job['output']).parent == task, 'saved runtime job escaped this task')
            submit(Path(job['output']), software, job['partition'], bundle, fixtures, job['exclude'])
    else:
        jobs = [submit(task / partition, software, partition, bundle, fixtures)
                for partition in snapshot['applications'][software]['partitions']]
    save(task / 'jobs.json', jobs)
    deadline = time.monotonic() + timeout
    finished, failures = {}, {}
    while len(finished) + len(failures) < len(jobs):
        for index, job in enumerate(jobs):
            part = job['partition']
            if part in finished or part in failures:
                continue
            directory = Path(job['output'])
            require(digest(directory / 'request.json') == job['request_sha256'], 'changed runtime check request')
            row = accounting(job['job'], directory, part)
            if row is None or row[1].split('+')[0] in ACTIVE:
                continue
            if row[1:3] == ['COMPLETED', '0:0']:
                try:
                    result = beta_check.verify(directory)
                    require(result == read(directory / 'verification.json'), 'changed runtime numerical evidence')
                    require(result['partition'] == part and result['job'] == job['job'], 'runtime allocation mismatch')
                    finished[part] = result
                except Exception as error:
                    failures[part] = dict(job=job['job'], error=str(error), log=str(directory))
            else:
                node = retry_node(directory, row)
                if node and not job['exclude']:
                    jobs[index] = submit(task / (part + '-retry1'), software, part, bundle, fixtures, node)
                    jobs[index]['previous'] = job
                    save(task / 'jobs.json', jobs)
                    continue
                failures[part] = dict(job=job['job'], state=row[1], exit_code=row[2], log=str(directory))
        save(task / 'runtime-status.json', dict(passed=finished, failed=failures, jobs=jobs))
        if len(finished) + len(failures) == len(jobs):
            break
        require(time.monotonic() < deadline, 'runtime observation deadline; jobs and previous public beta retained')
        print('BETA_RUNTIME_STATUS ' + json.dumps({j['partition']: ('passed' if j['partition'] in finished else
              'failed' if j['partition'] in failures else j['job']) for j in jobs}), flush=True)
        time.sleep(30)
    require(not failures, 'SIF route failed; previous beta retained: ' + json.dumps(failures))
    checks = task / 'validation.json'
    save(checks, list(finished.values()))
    return checks


def execute(software, workflow, attempt, builds, task, fixtures):
    sources = candidates.validate_plan(software, builds)
    request_path = task / 'request.json'
    identity = dict(software=software, workflow=workflow, attempt=attempt, builds=builds)
    if request_path.exists():
        request = read(request_path)
        require(all(request[key] == value for key, value in identity.items()), 'changed daily publication request')
    else:
        request = dict(identity, before=current(software), date=datetime.now(timezone.utc).strftime('%Y%m%d'))
        save(request_path, request)
    before, date = request['before'], request['date']
    generation = safe_name(f'daily-{date}-{workflow}-{attempt}-{software}')
    published = task / 'publication.json'
    if published.exists():
        result = read(published)
        require(result['workflow'] == workflow and result['attempt'] == attempt
                and result['software'] == software, 'publication receipt differs from this request')
        return result
    active = current(software)
    require(active == before or active['generation'] == generation,
            'public beta changed since this request; refusing to replace a newer selection')
    previous = load_snapshot(Path(before['bundle']) / 'catalog.json')
    previous_publication = previous.get('daily_publication')
    if previous_publication:
        require((int(workflow), int(attempt)) >= (int(previous_publication['workflow']), int(previous_publication['attempt'])),
                'a newer workflow already published this beta')
    rows = {}
    for source in sources:
        directory = candidates.locate(software, workflow, attempt, source)
        row = candidates.candidate(software, directory, source)
        rows[row['identity']['partition']] = row
    recipes = {part: row['identity']['recipe_sha256'] for part, row in sorted(rows.items())}
    revision = hashlib.sha256(json.dumps(recipes, sort_keys=True).encode()).hexdigest()[:12]
    app = dict(version=software_version(software, date, sources[0]['source_sha']), partitions=rows, build_revision=revision)
    snapshot = dict(schema=1, snapshot=date, runtime_sha256=digest(Path(__file__).with_name('sif_runtime.py')),
                    applications={software: app}, memory_bootstrap=previous['memory_bootstrap'],
                    daily_publication=dict(workflow=workflow, attempt=attempt))
    # A same-day rerun with the same source, build recipe and runtime is already
    # represented by its accepted fixed module. Do not overwrite that version.
    fixed = MODULES / software / sif_beta.version_name(software, snapshot)
    if fixed.exists() and before['fixed'] == fixed.name:
        result = dict(status='unchanged', software=software, source_sha=sources[0]['source_sha'],
                      workflow=workflow, attempt=attempt, generation=before['generation'], fixed=before['fixed'])
        save(published, result)
        return result
    if fixed.exists():
        require((ROOT / 'generations' / generation).exists(),
                'fixed version already exists but is not current; explicit rollback required')
    if (task / 'candidates.json').exists():
        require(read(task / 'candidates.json') == snapshot, 'candidate evidence changed since this request')
    else:
        save(task / 'candidates.json', snapshot)
    bundle = task / 'bundle'
    if not (bundle / 'catalog.json').exists():
        install(snapshot, bundle, resume=True)
    checked = load_snapshot(bundle / 'catalog.json')
    require(checked['applications'] == snapshot['applications'], 'existing bundle contains different candidates')
    checks = check_runtime(task, software, bundle, fixtures)
    # Re-read each original proof before promotion, including the image hashes.
    for source in sources:
        row = candidates.candidate(software, candidates.locate(software, workflow, attempt, source), source)
        require(row == rows[row['identity']['partition']], 'candidate evidence changed during runtime checks')
    public_bundle = ROOT / 'runtimes' / generation
    if not (public_bundle / 'catalog.json').exists():
        install(checked, public_bundle, copy_images=True, resume=True)
    with (ROOT / '.publication.lock').open('a+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        require(current(software) == before or current(software)['generation'] == generation, 'public beta changed while checks ran; refusing to replace a newer selection')
        sif_beta.prepare(ROOT, MODULES, generation, public_bundle, bundle / 'catalog.json', checks, [software], resume=True)
        sif_beta.select(ROOT, generation, [software])
    after = current(software)
    require(after['generation'] == generation, 'public beta did not select the accepted generation')
    result = dict(status='published', software=software, source_sha=sources[0]['source_sha'], workflow=workflow,
                  attempt=attempt, generation=generation, before=before, after=after,
                  fixed=after['fixed'], checks=str(checks), catalog_sha256=digest(public_bundle / 'catalog.json'))
    save(published, result)
    return result


def software_version(software, date, sha):
    return sif_beta.SOFTWARE[software]['development_ref'] + '-' + date + '-g' + sha[:12]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--software', required=True, choices=tuple(sif_beta.SOFTWARE))
    parser.add_argument('--workflow', required=True)
    parser.add_argument('--attempt', required=True)
    parser.add_argument('--builds', type=Path, required=True)
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--task', type=Path, required=True)
    args = parser.parse_args()
    require(args.task.parent == PROJECT / 'operations/daily-beta' and args.task.resolve() == args.task,
            'daily publication work must stay in the project')
    args.task.mkdir(parents=True, exist_ok=True)
    lock = PROJECT / 'operations/daily-beta' / (args.software + '.lock')
    require(not lock.is_symlink(), 'redirected application lock')
    with lock.open('a+') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            result = execute(args.software, args.workflow, args.attempt, read(args.builds), args.task, args.fixtures)
        except Exception as error:
            try:
                retained = current(args.software)
            except Exception as inspect_error:
                retained = dict(inspection_error=str(inspect_error))
            result = dict(status='failed', software=args.software, workflow=args.workflow, attempt=args.attempt,
                          error=str(error), retained=retained)
            save(args.task / 'status.json', result)
            print(json.dumps(result), flush=True)
            raise
        save(args.task / 'status.json', result)
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
