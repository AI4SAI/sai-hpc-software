#!/usr/bin/env python3
"""Refuse runner startup without memory headroom; no resident monitor."""
import argparse
from pathlib import Path


def refusal(meminfo, pressure, required_kib):
    fields = dict(line.split(':', 1) for line in meminfo.splitlines() if ':' in line)
    # Fail closed if the kernel cannot report usable headroom. MemFree alone
    # misses reclaimable cache and is not interchangeable with MemAvailable.
    available = fields['MemAvailable'].split()
    if len(available) != 2 or available[1] != 'kB':
        raise ValueError('invalid MemAvailable')
    available_kib = int(available[0])
    if available_kib < required_kib:
        return f'MemAvailable={available_kib // 1024} MiB, need {required_kib // 1024} MiB'
    rows = dict(line.split(' ', 1) for line in pressure.splitlines() if line)
    some = float(dict(item.split('=', 1) for item in rows['some'].split())['avg10'])
    if not 0 <= some <= 5:
        return f'memory pressure some avg10={some}%, need <=5%'
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runner-mib', type=int, required=True)
    parser.add_argument('--reserve-mib', type=int, required=True)
    args = parser.parse_args()
    if min(args.runner_mib, args.reserve_mib) <= 0:
        parser.error('runner budget and host reserve must be positive')
    try:
        reason = refusal(Path('/proc/meminfo').read_text(), Path('/proc/pressure/memory').read_text(),
                         (args.runner_mib + args.reserve_mib) * 1024)
    except (OSError, KeyError, ValueError) as error:
        reason = f'cannot verify memory headroom: {error}'
    if reason:
        # ExecStartPre failure prevents the listener; systemd retries later.
        print('Runner left stopped: ' + reason, flush=True)
        return 1
    print('Runner startup memory check passed; this does not reserve host memory.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
