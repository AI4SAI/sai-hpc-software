from pathlib import Path
import json
import subprocess

root = Path('/work')
work = root / 'local-fs-f'
rows = []
cases = [
    ('master', 'norecovery', 1),
    ('master', 'inherited_lock', 1),
    ('norecovery', 'norecovery', 0),
    ('inherited-lock', 'inherited_lock', 0),
    ('combined', 'norecovery', 0),
    ('combined', 'inherited_lock', 0),
]
for variant, suite, expected in cases:
    label = variant + '-' + suite
    command = [
        '/usr/bin/python3', '-B', str(root / 'input-e' / ('fuse2fs_' + suite + '.py')),
        '--fuse2fs', str(root / 'drivers-e' / variant / 'fuse2fs'),
        '--work-dir', str(work / label),
    ]
    with (root / 'tmpfs-results-f' / (label + '.log')).open('xb') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=240)
    rows.append(dict(variant=variant, suite=suite, returncode=result.returncode,
                     expected_returncode=expected, passed=result.returncode == expected))
    (root / 'tmpfs-results-f' / 'summary.json').write_text(json.dumps(rows, indent=2) + '\n')
    print(json.dumps(rows[-1]), flush=True)
if not all(row['passed'] for row in rows):
    raise SystemExit(1)
