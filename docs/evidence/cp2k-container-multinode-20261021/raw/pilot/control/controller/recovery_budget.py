"""Explicit recovery authorization with diagnosed, nonduplicated job receipts."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path

from remote_controller import safe_name

BASE_ID = 'user-20260920-six-additional-attempts'
EXTENSION = {
    'schema': 1,
    'authorization_id': 'user-20260921-abacus-six-extra-attempts',
    'extends': BASE_ID,
    'software': 'abacus',
    'additional_attempts': 6,
    'total_attempt_limit': 12,
    'user_instruction': '我再给你6个额外额度继续推进',
    'counting': 'All newly submitted ABACUS build, probe and acceptance jobs; read-only inspection and local preflight cost no attempt.',
    'automatic_publication': False,
}
EXTENSION_FILE = 'authorizations/abacus-20260921-extra6.json'
SOFTWARE = ('abacus', 'cp2k', 'lammps')
UNLIMITED = {
    'schema': 1,
    'authorization_id': 'user-20260921-unlimited-diagnosed-attempts',
    'supersedes_attempt_limits': [BASE_ID, EXTENSION['authorization_id']],
    'software': list(SOFTWARE),
    'attempt_limit': None,
    'user_instruction': '不限制次数了，每次出问题都要找原因并改正改到可以跑为止',
    'requirements': ['diagnose and correct each failure before retry',
                     'preserve historical receipts and frozen controllers',
                     'fresh final-prefix builds under /opt/apps',
                     'verify actual execution, not compilation alone'],
    'automatic_publication': False,
}
UNLIMITED_FILE = 'authorizations/20260921-unlimited-diagnosed.json'


def regular_path(path):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or path.resolve() != path:
        raise ValueError('recovery paths must be absolute and unaliased')
    return path


def read_json(path):
    return json.loads(regular_path(path).read_text())


def write_new(path, value):
    with regular_path(path).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


@contextmanager
def guard(authority, *, shared=False, software='abacus'):
    if software not in SOFTWARE:
        raise ValueError('unknown recovery software')
    directory = regular_path(Path(authority) / 'attempts' / software)
    # This workflow extends an existing ledger, not a fresh authorization.
    lock = regular_path(directory / 'lock')
    with lock.open('r' if shared else 'a') as stream:
        fcntl.flock(stream, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        yield directory


def policy(authority, software='abacus'):
    if software not in SOFTWARE:
        raise ValueError('unknown recovery software')
    authority = regular_path(authority)
    base = read_json(authority / 'authorization.json')
    if (base.get('authorization_id') != BASE_ID or
            type(base.get('additional_attempt_limit_per_software')) is not int or
            base['additional_attempt_limit_per_software'] != 6):
        raise ValueError('unexpected base recovery authorization')
    extension = authority / EXTENSION_FILE
    limit, ids = 6, [BASE_ID]
    if software == 'abacus' and (extension.exists() or extension.is_symlink()):
        actual = read_json(extension)
        if (actual != EXTENSION or type(actual.get('additional_attempts')) is not int or
                type(actual.get('total_attempt_limit')) is not int):
            raise ValueError('unexpected ABACUS extension authorization')
        limit, ids = 12, [BASE_ID, EXTENSION['authorization_id']]
    override = authority / UNLIMITED_FILE
    if override.exists() or override.is_symlink():
        actual = read_json(override)
        if actual != UNLIMITED or actual.get('attempt_limit') is not None:
            raise ValueError('unexpected unlimited recovery authorization')
        return None, [*ids, UNLIMITED['authorization_id']]
    return limit, ids


def records(directory, software='abacus'):
    result = []
    for number, path in enumerate(sorted(directory.glob('*.json')), 1):
        item = read_json(path)
        if (path.name != f'{number:02d}.json' or type(item.get('number')) is not int or
                item['number'] != number or item.get('software') != software or
                not isinstance(item.get('run_id'), str)):
            raise ValueError('invalid or noncontiguous recovery attempt history')
        safe_name(item['run_id'])
        result.append(item)
    if len({item['run_id'] for item in result}) != len(result):
        raise ValueError('duplicate run in recovery history')
    return result


def status(authority, software='abacus'):
    with guard(authority, shared=True, software=software) as directory:
        limit, ids = policy(authority, software)
        prior = records(directory, software)
        if limit is not None and len(prior) > limit:
            raise ValueError('recorded attempts exceed explicit authorization')
        return dict(software=software, used=len(prior), limit=limit,
                    remaining=None if limit is None else limit-len(prior), authorization_ids=ids)


def register_extension(authority):
    """Call only in response to the explicit September 21 user extension."""
    with guard(authority) as directory:
        limit, _ = policy(authority)
        if limit is None:
            raise ValueError('historical six-attempt extension is superseded')
        if limit == 12:
            return  # Idempotent, but never rewrite the existing grant.
        if len(records(directory)) != 6:
            raise ValueError('extension expects exactly six historical ABACUS attempts')
        path = regular_path(Path(authority) / EXTENSION_FILE)
        path.parent.mkdir(exist_ok=True)
        write_new(path, EXTENSION)


def register_unlimited(authority):
    """Append only the user's explicit unlimited-but-diagnosed authorization."""
    with guard(authority):
        limit, _ = policy(authority)
        if limit is None:
            return
        path = regular_path(Path(authority) / UNLIMITED_FILE)
        path.parent.mkdir(exist_ok=True)
        write_new(path, UNLIMITED)


def claim(authority, run_id, kind, reason, software='abacus'):
    safe_name(run_id)
    if kind not in ('build', 'probe', 'acceptance') or not reason.strip():
        raise ValueError('attempt type and diagnostic reason are required')
    with guard(authority, software=software) as directory:
        limit, ids = policy(authority, software)
        prior = records(directory, software)
        if any(item['run_id'] == run_id for item in prior):
            raise ValueError('duplicate attempt; inspect the existing receipt')
        if limit is not None and len(prior) >= limit:
            raise ValueError('authorized ABACUS attempts exhausted')
        if any(item.get('status') in ('claimed', 'submitted-held') for item in prior):
            raise ValueError('unresolved submission; reconcile its receipt before retrying')
        record = dict(authorization_id=ids[-1], authorization_ids=ids,
                      software=software, run_id=run_id, number=len(prior)+1,
                      limit=limit, status='claimed', kind=kind, reason=reason)
        path = directory / f"{record['number']:02d}.json"
        write_new(path, record)
        return path, record


def receipt(path, record, job, state, run):
    if not job.isdigit() or state not in ('submitted-held', 'released'):
        raise ValueError('invalid scheduler receipt')
    path = regular_path(path)
    authority = path.parents[2]
    with guard(authority, software=record['software']):
        prior = read_json(path)
        if prior != record:
            raise ValueError('attempt receipt changed concurrently')
        if (prior.get('status'), state) not in (('claimed', 'submitted-held'),
                                               ('submitted-held', 'released')):
            raise ValueError('invalid attempt receipt transition')
        if prior.get('job_id', job) != job:
            raise ValueError('cannot replace a recorded job')
        updated = dict(record, job_id=job, status=state, run=str(regular_path(run)))
        temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
        write_new(temporary, updated)
        os.replace(temporary, path)
        record.update(updated)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('status', 'register-abacus-extension', 'register-unlimited'))
    parser.add_argument('authority', type=Path)
    parser.add_argument('--software', choices=SOFTWARE, default='abacus')
    args = parser.parse_args()
    if args.operation == 'register-abacus-extension':
        register_extension(args.authority)
    elif args.operation == 'register-unlimited':
        register_unlimited(args.authority)
    print(json.dumps(status(args.authority, args.software), ensure_ascii=False))


if __name__ == '__main__':
    main()
