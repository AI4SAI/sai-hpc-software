#!/usr/bin/env python3
"""Publish only DeePMD after reparsing its completed three-partition checks."""
import json
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / 'controller'))
from sif_modules import install, load_snapshot
from sif_runtime import digest

subprocess.run([sys.executable, str(root / 'verify-site.py'), str(root)], check=True)
checks = json.loads((root / 'validation.json').read_text())
jobs = {report['job'] for report in checks}
accounting = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', ','.join(sorted(jobs)),
    '--format=JobID,State,ExitCode'], text=True)
states = {fields[0]: fields[1:3] for line in accounting.splitlines() if (fields := line.split('|'))[0] in jobs}
assert set(states) == jobs and all(value == ['COMPLETED', '0:0'] for value in states.values()), states
public = Path('/home/stardust/sai-hpc-software')
module_root = public / 'modulefiles/apps'
aliases = sorted(module_root.glob('*/*-beta-later'))
before = {str(path): str(path.readlink()) for path in aliases}
snapshot = load_snapshot(root / 'bundle/catalog.json')
runtime = public / 'sif-beta/runtimes' / ('20261025-' + snapshot['runtime_sha256'][:12])
install(snapshot, runtime)
generation = '20261025-deepmd-ldconfig-r1'
subprocess.run([sys.executable, str(root / 'controller/sif_beta.py'), 'promote',
    '--root', str(public / 'sif-beta'), '--module-root', str(module_root),
    '--generation', generation, '--software', 'deepmd-kit', '--bundle', str(runtime),
    '--validated-catalog', str(root / 'bundle/catalog.json'), '--checks', str(root / 'validation.json')], check=True)
after = {str(path): str(path.readlink()) for path in aliases}
assert [name for name in before if before[name] != after[name]] == [str(module_root / 'deepmd-kit/master-beta-later')]
record = dict(generation=generation, runtime=str(runtime), runtime_sha256=snapshot['runtime_sha256'],
              accounting=states,
              catalog_sha256=digest(runtime / 'catalog.json'), before=before, after=after)
(root / 'publication.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record, indent=2))
