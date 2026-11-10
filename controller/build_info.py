"""Publish static, artifact-bound program help for login-node readers."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess


COMMANDS = {
    'abacus': [['abacus', '--info']],
    'cp2k': [['cp2k.psmp', '--version']],
    'lammps': [['lmp', '-h']],
    'deepmd-kit': [['dp', '--version'], ['dp', '--help']],
    # Neither PWSCF nor GPUMD has a standalone help/version switch.
    # Preserve their actual accepted run (including compiler/backend banner).
    'qe': [],
    'gpumd': [],
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture(output):
    request = json.loads((output / 'request.json').read_text())
    catalog = Path(request['catalog'])
    if digest(catalog) != request['catalog_sha256']:
        raise ValueError('build information catalog changed')
    software, partition = request['software'], request['partition']
    row = json.loads(catalog.read_text())['applications'][software]['partitions'][partition]
    environment = dict(os.environ)
    # Documentation must not add singleton traces to the numerical MPI gate.
    environment.pop('SAI_SIF_TRACE_DIR', None)
    records = []
    for argv in COMMANDS[software]:
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, env=environment,
                                cwd=output, timeout=120)
        if result.returncode:
            raise ValueError('build information command failed: ' + shlex.join(argv) + '\n' + result.stdout)
        records.append(dict(command=argv, output=result.stdout, returncode=0))
    if software in ('gpumd', 'qe'):
        records.append(dict(source='case/stdout.txt', output=(output / 'case/stdout.txt').read_text()))
    value = dict(schema=1, identity=row['identity'], image_sha256=row['sha256'],
                 catalog_sha256=request['catalog_sha256'],
                 job=(output / 'job.id').read_text().strip(), records=records)
    path = output / 'build-info.json'
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    return path


def verify(path, row, catalog_sha256, job):
    value = json.loads(path.read_text())
    if (value.get('schema') != 1 or value['identity'] != row['identity']
            or value['image_sha256'] != row['sha256']
            or value['catalog_sha256'] != catalog_sha256 or value['job'] != job):
        raise ValueError('static build information belongs to another artifact/allocation')
    commands = [r.get('command') for r in value['records'] if 'command' in r]
    if commands != COMMANDS[row['identity']['software']]:
        raise ValueError('missing full standard help/version commands')
    if not value['records'] or any(not r['output'].strip() or r.get('returncode', 0) for r in value['records']):
        raise ValueError('empty or failed build information')
    if row['identity']['software'] in ('gpumd', 'qe') and value['records'][0].get('source') != 'case/stdout.txt':
        raise ValueError('this program requires its accepted calculation output')
    return value


def render(value):
    lines = ['SAI static build information (no module load required)',
             json.dumps({k: v for k, v in value.items() if k != 'records'}, indent=2, sort_keys=True)]
    for record in value['records']:
        lines.extend(['', '$ ' + shlex.join(record['command']) if 'command' in record
                      else 'Recorded output: ' + record['source'], record['output']])
    return '\n'.join(lines) + '\n'


def qe_configuration(row):
    """Read the saved CMake configuration, without starting the container."""
    from export_native import SquashfsReader, MANIFEST_PATH
    reader = SquashfsReader.from_sif(Path(row['image']))
    prefix = row['identity']['install_prefix'].lstrip('/')
    manifest, _ = reader.read_manifest(prefix + '/' + MANIFEST_PATH)
    if manifest['entry']['identity'] != row['identity']:
        raise ValueError('QE configuration identity changed')
    inventory = {r['path']: r for r in manifest['files']}
    result = []
    for name in ('configure-command.txt', 'CMakeCache.txt'):
        path = prefix + '/share/sai/' + name
        record = inventory[path]
        data = subprocess.check_output(reader.command([path]), timeout=60)
        if record['kind'] != 'file' or len(data) != record['size'] or hashlib.sha256(data).hexdigest() != record['sha256']:
            raise ValueError('QE saved configuration differs from its manifest')
        result.append(dict(source=path, sha256=record['sha256'], output=data.decode()))
    return result


def publish(root, generation, software, snapshot, reports, write):
    """Require every partition's captured output before making beta visible."""
    destination = root / 'build-info' / generation / software
    index = ['Static build information for ' + software, 'Read these files directly; no module load or allocation is needed.', '']
    files = {}
    for partition, row in snapshot['applications'][software]['partitions'].items():
        report = reports[partition]
        proof = report.get('build_info')
        if not proof:
            raise ValueError('missing static build information for ' + software + '/' + partition)
        source = Path(proof['path'])
        if digest(source) != proof['sha256']:
            raise ValueError('static build information changed after acceptance')
        value = verify(source, row, report['catalog_sha256'], report['job'])
        if software == 'qe':
            value['records'].extend(qe_configuration(row))
        path = destination / (partition + '.txt')
        write(path, render(value))
        files[str(path.relative_to(root))] = digest(path)
        index += [partition + ': cat ' + str(path)]
    path = destination / 'index.txt'
    write(path, '\n'.join(index) + '\n')
    files[str(path.relative_to(root))] = digest(path)
    for parent in (destination, destination.parent, destination.parent.parent):
        parent.chmod(0o755)
    return files


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    capture(parser.parse_args().output)
