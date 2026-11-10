#!/usr/bin/env python3
"""Connect failed imports, cache evictions, retries, and the exporting rank."""
import json
from pathlib import Path
import re
import sys

directory = Path(sys.argv[1])
exports = {}
records = {}
pattern = re.compile(r'^(\d+) (open|close|export) result=(\d+) address=(0x[0-9a-f]+).*?context=(\S+) device=(-?\d+).*?handle=([0-9a-f]*)$')
for path in sorted(directory.glob('ipc-*.log')):
    events = []
    previous = None
    for number, line in enumerate(path.read_text().splitlines(), 1):
        match = pattern.match(line)
        if match:
            sequence, op, result, address, context, device, handle = match.groups()
            previous = dict(sequence=int(sequence), operation=op, result=int(result), address=int(address, 16),
                            context=context, device=int(device), handle=handle, file=path.name, line=number)
            events.append(previous)
        elif line.startswith('  exported_range ') and previous is not None:
            base, size, ident = re.search(r'base=(0x[0-9a-f]+) size=(\d+) buffer_id=(\d+)', line).groups()
            previous['allocation'] = dict(base=int(base, 16), size=int(size), buffer_id=int(ident))
            exports[previous['handle']] = previous
    records[path.name] = events

failures = []
for filename, events in records.items():
    active = {}
    for index, event in enumerate(events):
        if event['operation'] == 'open' and event['result'] == 0:
            active[event['address']] = event
        elif event['operation'] == 'close' and event['result'] == 0:
            active.pop(event['address'], None)
        elif event['operation'] == 'open' and event['result'] != 0:
            retry = next((row for row in events[index + 1:] if row['operation'] == 'open' and row['handle'] == event['handle']), None)
            closes = [row for row in events[index + 1:] if row['operation'] == 'close'
                      and (retry is None or row['sequence'] < retry['sequence'])]
            old = [active[row['address']] for row in closes if row['address'] in active]
            incoming = exports.get(event['handle'])
            retired = [exports.get(row['handle']) for row in old]
            result = dict(error=event, retry=retry, intervening_closes=closes, incoming_export=incoming,
                          retired_exports=retired,
                          same_handle_already_open=any(row['handle'] == event['handle'] for row in active.values()),
                          recovered=retry is not None and retry['result'] == 0)
            if incoming and all(retired):
                new = incoming['allocation']
                result['overlap_checks'] = [dict(
                    old=ex['allocation'], new=new,
                    overlaps=max(new['base'], ex['allocation']['base']) < min(new['base'] + new['size'], ex['allocation']['base'] + ex['allocation']['size']),
                    new_start_below_old=new['base'] < ex['allocation']['base'],
                    different_buffer_id=new['buffer_id'] != ex['allocation']['buffer_id'],
                    different_handle=incoming['handle'] != ex['handle']) for ex in retired]
            failures.append(result)

summary = dict(directory=str(directory), files=len(records), failures=len(failures),
               all_recovered=bool(failures) and all(row['recovered'] for row in failures),
               same_handle_reimports=sum(row['same_handle_already_open'] for row in failures),
               trace=failures)
print(json.dumps(summary, indent=2))
