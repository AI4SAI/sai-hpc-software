"""Read-only snapshot of November 5 development publications; run on SAI."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


root = Path('/org/org-open-sai/proj-sai-hpc-software')
home = Path('/home/stardust/sai-hpc-software')
applications = {}
for software, branch, count in [('abacus', 'develop', 4), ('cp2k', 'master', 4),
                               ('qe', 'develop', 4), ('lammps', 'develop', 3),
                               ('deepmd-kit', 'master', 3), ('gpumd', 'master', 3)]:
    relative = Path(software) / (branch + '-beta-later')
    alias = home / 'modulefiles/apps' / relative
    fixed = str(alias.readlink())
    assert Path(fixed).name == fixed
    matches = []
    for task in (root / 'operations/daily-beta').glob('42003384294-1-' + software + '-*'):
        status = task / 'status.json'
        if not status.exists():
            continue
        record = json.loads(status.read_text())
        if record.get('status') == 'published' and record.get('fixed') == fixed:
            matches.append((task, record))
    assert len(matches) == 1, (software, fixed, len(matches))
    task, record = matches[0]
    checks = json.loads((task / 'validation.json').read_text())
    candidates = json.loads((task / 'candidates.json').read_text())['applications'][software]['partitions']
    assert len(checks) == len(candidates) == count
    assert {row['partition'] for row in checks} == set(candidates)
    assert all(row['passed'] is True for row in checks)
    assert all(row['build_verified'] and row['scientific_verified'] for row in candidates.values())
    assert {row['identity']['source_sha'] for row in candidates.values()} == {record['source_sha']}
    for row in candidates.values():
        assert digest(Path(row['evidence'])) == row['evidence_sha256']
    assert digest(Path(record['after']['bundle']) / 'catalog.json') == record['catalog_sha256']
    job_ids = {row['job'] for row in checks}
    accounting = subprocess.run(['sacct', '-X', '-n', '-P', '-j', ','.join(sorted(job_ids)),
        '-o', 'JobID,State,ExitCode,Elapsed,NodeList,Account'], text=True, capture_output=True, check=True).stdout
    rows = [line.split('|') for line in accounting.splitlines() if line.strip()]
    assert len(rows) == count and {row[0] for row in rows} == job_ids
    assert all(row[1:3] == ['COMPLETED', '0:0'] and row[5] == 'proj-sai-hpc-software' for row in rows)
    assert str(alias.readlink()) == fixed
    gateway = Path('/opt/modules/modulefiles/apps') / relative
    applications[software] = dict(alias=str(alias), fixed=fixed,
        fixed_module_sha256=digest(alias.parent / fixed),
        public_gateway=str(gateway), public_gateway_present=gateway.is_file(),
        public_gateway_content=gateway.read_text() if gateway.is_file() else None,
        task=str(task), publication=record, checks=checks, candidates=candidates,
        accounting=accounting,
        evidence_sha256={name: digest(task / name)
                         for name in ('status.json', 'publication.json', 'validation.json', 'candidates.json')})

gateway_check = subprocess.run(['bash', str(home / 'deployment/sif-beta/install.sh'), '--check',
    '/opt/modules/modulefiles/apps', 'qe'], text=True, capture_output=True, check=True)
total = sum(len(item['checks']) for item in applications.values())
assert total == 21
print(json.dumps(dict(observed_at=datetime.now(timezone.utc).isoformat(),
    source_workflow='42003384294', applications=applications,
    accepted_partition_checks=total,
    qe_gateway_check=dict(exit_code=0, stdout=gateway_check.stdout, stderr=gateway_check.stderr)),
    indent=2, sort_keys=True))
