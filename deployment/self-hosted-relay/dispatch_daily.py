#!/usr/bin/env python3
"""Request daily Actions work from a local, short-lived systemd timer."""
import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import stat
import sys
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

REPOSITORY = 'AI4SAI/sai-hpc-software'
WORKFLOW = f'/repos/{REPOSITORY}/actions/workflows/daily.yml'
ZONE = ZoneInfo('Asia/Shanghai')
SELECTION = dict(software='all', tracks='development,prerelease,release', targets='all')
VISIBILITY_GRACE = timedelta(minutes=15)


def due_date(now):
    local = now.astimezone(ZONE)
    return (local - timedelta(hours=3)).date().isoformat()


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, sort_keys=True, indent=2) + '\n')
    temporary.replace(path)


class GitHub:
    def __init__(self, token_file):
        info = token_file.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            raise ValueError('token must be a regular owner-only file owned by this account')
        self.token = token_file.read_text().strip()
        if not self.token or any(c.isspace() for c in self.token):
            raise ValueError('empty or invalid dispatch token')

    def request(self, path, data=None):
        request = urllib.request.Request('https://api.github.com' + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'Authorization': 'Bearer ' + self.token,
                     'Accept': 'application/vnd.github+json',
                     'X-GitHub-Api-Version': '2022-11-28',
                     'User-Agent': 'sai-hpc-local-scheduler',
                     'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
        return json.loads(content) if content else None

    def runs(self, now):
        # The workflow has a 24-hour budget. Three days also cover delayed
        # visibility, restart recovery and the previous local scheduling day.
        since = (now - timedelta(days=3)).strftime('%Y-%m-%dT%H:%M:%SZ')
        rows = []
        for page in range(1, 11):
            data = self.request(WORKFLOW + f'/runs?branch=main&per_page=100&page={page}&created=%3E%3D{since}')
            current = data['workflow_runs']
            rows.extend(current)
            if len(current) < 100:
                return rows
        raise RuntimeError('too many recent runs to establish safe dispatch state')

    def dispatch(self, mode, day=''):
        inputs = dict(SELECTION, mode=mode, retry_releases='false')
        if day:
            inputs['schedule_date'] = day
        self.request(WORKFLOW + '/dispatches', dict(ref='main', inputs=inputs))


def plans_at(root):
    return [json.loads(path.read_text()) for path in sorted(root.glob('*/plan.json'))]


def full_daily(plan, day):
    return (plan.get('selection') == SELECTION and not plan.get('retry_releases')
            and (plan.get('schedule_date') or due_date(timestamp(plan['created']))) == day)


def decide(now, plans, runs, receipt):
    day = due_date(now)
    # Do not queue work behind an existing daily/diagnostic job, including a
    # job awaiting runner capacity or Environment approval.
    if any(run['status'] != 'completed' for run in runs):
        return 'busy', ''
    if receipt and now - timestamp(receipt['requested_at']) < VISIBILITY_GRACE:
        return 'awaiting-dispatch-visibility', ''
    if any(plan['state'] == 'active' for plan in plans):
        return 'resume', ''
    title = f'SAI daily {day} / start'
    if (any(full_daily(plan, day) for plan in plans)
            or any(run.get('display_title') == title for run in runs)):
        # A failed daily run is still an attempt. Do not repeatedly spend
        # compute on the same permanent build/scientific failure.
        return 'already-requested', day
    return 'start', day


def tick(root, relay_root, github, now):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / 'dispatch.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = root / 'receipt.json'
        receipt = json.loads(path.read_text()) if path.exists() else None
        action, day = decide(now, plans_at(relay_root), github.runs(now), receipt)
        print(f'SCHEDULE {action} {day}', flush=True)
        if action not in ('start', 'resume'):
            return action
        # Record intent before HTTP: a lost reply must not immediately create
        # a second Actions run. A later start retry uses the SAME logical date;
        # the relay itself deduplicates it before resolving or submitting work.
        record = dict(mode=action, schedule_date=day, requested_at=now.isoformat(), accepted=False)
        save(path, record)
        github.dispatch(action, day)
        record['accepted'] = True
        save(path, record)
        print('DISPATCH_ACCEPTED ' + action + ' ' + day, flush=True)
        return action


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--token-file', type=Path, default=Path.home() / '.config/sai-hpc-scheduler/token')
    parser.add_argument('--state-root', type=Path, default=Path.home() / '.local/state/sai-hpc-scheduler')
    parser.add_argument('--relay-root', type=Path, default=Path.home() / 'ci/state')
    parser.add_argument('--mode', choices=('auto', 'check', 'smoke'), default='auto')
    args = parser.parse_args()
    os.umask(0o077)
    github = GitHub(args.token_file)
    if args.mode == 'check':
        workflow = github.request(WORKFLOW)
        if workflow['state'] != 'active':
            raise ValueError('daily workflow is not active')
        github.runs(datetime.now(timezone.utc))
        print('GitHub workflow and Actions read access verified; smoke tests dispatch permission.')
    elif args.mode == 'smoke':
        github.dispatch('smoke')
        print('SMOKE_DISPATCH_ACCEPTED; inspect the Actions run before claiming execution.')
    else:
        tick(args.state_root, args.relay_root, github, datetime.now(timezone.utc))


if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as error:
        # Never include authorization headers or HTTP bodies in the journal.
        print(f'GitHub HTTP {error.code}; verify repository Actions read/write permission and token expiry.', file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, RuntimeError) as error:
        print(f'Local scheduler failed: {error}', file=sys.stderr)
        sys.exit(1)
