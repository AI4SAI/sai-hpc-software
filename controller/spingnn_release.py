#!/usr/bin/env python3
"""SpinGNN SIF release claims, held Slurm submissions and native deliveries."""
import argparse
import csv
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import re
import stat
import subprocess
import tempfile
import time
import uuid

from release_contract import SOFTWARE, claim_build, make_identity, validate_identity
from remote_controller import SAI_PROJECT_ROOT, project_batch_directives, project_qos, safe_name
from relay_checkpoint import Pending, entry, inspect_submission
from source_cache import checksum
from spingnn_config import hardware, site, target_policy

CONTROL = Path(__file__).resolve().parent
PROJECT = SAI_PROJECT_ROOT
ROOT = PROJECT / 'experimental/spingnn2-release/sif'
FILES = ('spingnn_release.py', 'spingnn_inputs.py', 'spingnn_build.sh', 'spingnn_fixture.py',
         'spingnn_science.py', 'spingnn_config.py', 'spingnn_site.json', 'spingnn_bootstrap.json',
         'spingnn_source.py', 'spingnn_sm70.patch', 'spingnn_sm70.json',
         'release_contract.py', 'resolve_source.py', 'remote_controller.py', 'source_cache.py',
         'source_transport.py', 'relay_checkpoint.py', 'input_archive_cache.py', 'spingnn_runtime.txt',
         'spingnn_sif.py', 'spingnn_accept.sh', 'spingnn_package.py', 'spingnn_install.py',
         'spingnn_sif_entry.sh', 'spingnn_wheels.txt', 'create_rootfs.sh', 'export_native.py',
         'native_module.py', 'module_publication.py', 'delivery_layout.py', 'cp2k_image_driver.py',
         'managed_overlay.py', 'cp2k_overlay_sync.py')
FIELDS = {'total_energy', 'atomic_energy', 'forces', 'spin_forces', 'spin_forces_vert', 'virial'}


def require(value, message):
    if not value:
        raise ValueError(message)


def plain(path):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path and not path.is_symlink(),
            'redirected SpinGNN path: ' + str(path))
    return path


def read(path):
    return json.loads(plain(path).read_text())


def immutable(path, data):
    """Complete and chmod a sibling before one no-overwrite publication."""
    path = plain(path)
    if path.exists():
        require(path.is_file() and path.read_bytes() == data and not path.stat().st_mode & 0o222,
                'immutable SpinGNN file changed: ' + str(path))
        return
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), 0o444)
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            immutable(path, data)
        descriptor = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def save(path, value):
    immutable(path, (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode())


def fingerprint():
    value = hashlib.sha256(b'sai-spingnn2-sif-native-release-v1\n')
    for name in FILES:
        value.update(name.encode() + b'\0' + plain(CONTROL / name).read_bytes())
    return value.hexdigest()


def delivery(source, run_id):
    require(source.get('software') == 'spingnn2' and source.get('repository') == 'Hyyu-Lab/SpinGNN'
            and source.get('track') == 'release'
            and source.get('status') == 'resolved' and source.get('requested_ref') == 'latest-release',
            'SpinGNN requires one exact resolved formal release')
    policy = target_policy(source['target'])
    require(policy['enabled'], 'SpinGNN target has no accepted recipe: ' + policy.get('reason', ''))
    identity = make_identity('spingnn2', 'release', source['source_ref'], source['source_sha'],
                             source['source_version'], fingerprint(), source['target'], installation_run=safe_name(run_id))
    return dict(schema=1, identity=identity)


def validate_delivery(value, run_id):
    require(set(value) == {'schema', 'identity'} and type(value['schema']) is int and value['schema'] == 1,
            'invalid SpinGNN delivery schema')
    identity = qualified_identity(value['identity'])
    require(identity['recipe_sha256'] == fingerprint()
            and identity.get('installation_run') == run_id, 'changed SpinGNN controller or request')
    return value


def qualified_identity(value):
    identity = validate_identity(value)
    require(identity['software'] == 'spingnn2', 'not a SpinGNN identity')
    policy = target_policy(identity['target'])
    require(policy['enabled'],
            'no qualified build/acceptance implementation for this SpinGNN target')
    return identity


def task(run_id):
    return plain(ROOT / 'runs' / safe_name(run_id))


def check_access():
    root = plain(site()['root'])
    require(root == ROOT, 'SpinGNN SIF publication must use the project staging root')
    root.mkdir(parents=True, exist_ok=True)
    require(root.is_dir() and os.access(root, os.W_OK | os.X_OK),
            'daily SSH identity cannot write the configured SpinGNN root')
    return dict(uid=os.getuid(), root=str(root), writable=True)


def public_directory(path):
    root, path = plain(site()['root']), plain(path)
    require(path.is_relative_to(root), 'public directory escapes the deployment root')
    cursor = root
    for component in path.relative_to(root).parts:
        cursor = cursor / component
        if not cursor.exists():
            cursor.mkdir()
            cursor.chmod(0o755)
        mode = cursor.stat().st_mode
        require(cursor.is_dir() and mode & 0o005 == 0o005 and not mode & 0o022,
                'SpinGNN public directory is not readable or is writable by others')
    return path


def bootstrap(identity):
    identity = qualified_identity(identity)
    # Historical native receipts remain evidence, never a SIF qualification.
    return None


def claim(run_id, value, retry=False):
    validate_delivery(value, run_id)
    check_access()
    result = claim_build(ROOT, [value['identity']], run_id, retry=retry, resume=True)
    stamp = task(run_id) / 'input/created.json'
    if not stamp.exists():
        save(stamp, dict(created_ns=time.time_ns()))
    return result


def literal(value):
    return json.dumps(str(value), ensure_ascii=False)


def module_name(identity, run_id):
    # Full source/recipe identities live in the prefix and receipt; the short
    # module name remains readable and changed contents can never replace it.
    return identity['build_id'] + '-' + safe_name(run_id)


def module_text(identity, prefix):
    identity = qualified_identity(identity)
    settings = site()
    policy = target_policy(identity['target'])
    prefix = str(plain(prefix))
    # Same dependency and environment semantics as the qualified first module.
    lines = ['whatis(' + literal('SpinGNN2 ' + identity['source_version'] +
             ': ' + identity['partition'] + ' ' + policy['backend'] +
             ' native inference and lmp_spingnn2; no PyTorch runtime') + ')',
             'help("Native SpinGNN2: load inside a supported Slurm allocation; unload uses the saved identity.")',
             'if mode() ~= "load" and mode() ~= "unload" then return end',
             'if mode() == "load" then',
             '  local job = os.getenv("SLURM_JOB_ID")',
             '  if not job or not job:match("^[1-9]%d*$") then LmodError("load SpinGNN2 inside a Slurm allocation") end',
             '  local partition = os.getenv("SLURM_JOB_PARTITION")',
             '  if partition ~= ' + literal(identity['partition']) + ' then LmodError("SpinGNN2 module does not match this partition") end',
             'else',
             '  if os.getenv("SAI_SPINGNN_LOADED_PREFIX") ~= ' + literal(prefix) +
             ' or os.getenv("SAI_SPINGNN_LOADED_PARTITION") ~= ' + literal(identity['partition']) +
             ' then LmodError("SpinGNN2 unload identity differs from the loaded installation") end',
             '  local added = os.getenv("_SAI_SPINGNN_DEVTOOLS_ADDED")',
             '  if added ~= "0" and added ~= "1" then LmodError("missing SpinGNN2 module-path identity") end',
             'end',
             'conflict("spingnn")', 'conflict("lammps")',
             'local devtools = "/opt/modules/modulefiles/devtools"',
             'local added = os.getenv("_SAI_SPINGNN_DEVTOOLS_ADDED")',
             'if mode() == "load" then',
             '  local paths = ":" .. (os.getenv("MODULEPATH") or "") .. ":"',
             '  added = paths:find(":" .. devtools .. ":", 1, true) and "0" or "1"',
             'end',
             'if added == "1" then prepend_path("MODULEPATH", devtools) end',
             'pushenv("_SAI_SPINGNN_DEVTOOLS_ADDED", added)',
             *['depends_on(' + literal(name) + ')' for name in settings['modules'][policy['backend']][:2]],
             'pushenv("SAI_SPINGNN_LOADED_PREFIX", ' + literal(prefix) + ')',
             'pushenv("SAI_SPINGNN_LOADED_PARTITION", ' + literal(identity['partition']) + ')',
             'prepend_path("PATH", ' + literal(prefix + '/venv/bin') + ')',
             'prepend_path("PATH", ' + literal(prefix + '/bin') + ')',
             'pushenv("SPINGNN2_ROOT", ' + literal(prefix) + ')',
             'pushenv("SPINGNN2_LIBRARY", ' + literal(prefix + '/lib/libspingnn2.so.1') + ')',
             'pushenv("SPINGNN2_TARGET", ' + literal(identity['partition']) + ')',
             'pushenv("SPINGNN2_BACKEND", ' + literal(policy['backend']) + ')',
             'pushenv("SPINGNN2_ARCH", ' + literal('sm' + identity['cuda_arch'] if identity['gpus'] else '') + ')',
             'pushenv("PYTHONNOUSERSITE", "1")', 'pushenv("PYTHONDONTWRITEBYTECODE", "1")', '']
    return '\n'.join(lines)


def selector_text():
    """Load a separately accepted partition and remember it across rotation."""
    settings = site()
    root = Path(settings['root'])
    modules = root / 'modulefiles' / settings['module_family']
    lines = ['whatis("SpinGNN2 accepted native release for the allocated partition")',
             'help("Load inside a supported Slurm allocation; SIF deliveries must first be installed at their recorded /opt path.")',
             'if mode() ~= "load" and mode() ~= "unload" then return end',
             'local base = ' + literal(modules), 'local pointers = ' + literal(root / 'release-targets'),
             'local targets = {']
    lines += ['  [' + literal(target_policy(target)['partition']) + '] = ' + literal(target) + ','
              for target in SOFTWARE['spingnn2']['targets'] if target_policy(target)['enabled']]
    lines += ['}', 'local selected', 'if mode() == "unload" then',
              '  selected = os.getenv("SAI_SPINGNN_RELEASE_FRAGMENT")',
              '  local permitted = false',
              '  for _,target in pairs(targets) do',
              '    local head = base .. "/" .. target .. "/"',
              '    if selected and selected:sub(1,#head) == head and selected:sub(#head+1):match("^[%w_.-]+%.lua$") then permitted = true end',
              '  end', '  if not permitted then LmodError("missing saved SpinGNN partition identity") end',
              'else',
              '  local job = os.getenv("SLURM_JOB_ID")',
              '  if not job or not job:match("^[1-9]%d*$") then LmodError("load SpinGNN2 inside a Slurm allocation") end',
              '  local partition = os.getenv("SLURM_JOB_PARTITION")',
              '  local target = targets[partition]',
              '  if not target then LmodError("unregistered SpinGNN partition: " .. tostring(partition)) end',
              '  local pointer = io.open(pointers .. "/" .. target .. "/current", "r")',
              '  if not pointer then LmodError("no scientifically accepted SpinGNN release for " .. partition) end',
              '  local name = pointer:read("*l"); pointer:close()',
              '  if not name or not name:match("^[%w_.-]+%.lua$") then LmodError("invalid SpinGNN release pointer") end',
              '  selected = base .. "/" .. target .. "/" .. name',
              'end', 'pushenv("SAI_SPINGNN_RELEASE_FRAGMENT", selected)',
              'if not isFile(selected) then LmodError("missing immutable SpinGNN module") end',
              'local fragment, detail = loadfile(selected)',
              'if not fragment then LmodError("cannot read SpinGNN module: " .. tostring(detail)) end',
              'fragment()', '']
    return '\n'.join(lines)


def atomic_text(path, text):
    path = plain(path)
    temporary = path.with_name('.switch-' + uuid.uuid4().hex)
    try:
        immutable(temporary, text.encode())
        temporary.replace(path)
        descriptor = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def site_script(identity, run_id):
    identity = qualified_identity(identity)
    settings = site()
    policy = target_policy(identity['target'])
    values = dict(CC=settings['gcc'] + '/bin/gcc', CXX=settings['gcc'] + '/bin/g++',
                  SPINGNN_GCC=settings['gcc'], SPINGNN_CMAKE=settings['cmake'], SPINGNN_MKL=settings['mkl'],
                  SPINGNN_EXPORT_PYTHON=settings['export_python'], SAI_SPINGNN_SOURCE_SHA=identity['source_sha'],
                  SAI_SPINGNN_VERSION=identity['source_version'], SAI_SPINGNN_MODULE=module_name(identity, run_id),
                  SAI_SPINGNN_BACKEND=policy['backend'], SAI_SPINGNN_PARTITION=identity['partition'],
                  SAI_SPINGNN_CUDA_ARCH=identity['cuda_arch'], SAI_SPINGNN_MPI=policy['mpi_prefix'])
    return '\n'.join(['module load ' + shlex.join(settings['modules'][policy['backend']]),
                      *['export ' + name + '=' + shlex.quote(value) for name, value in values.items()], ''])


def render(run_id, value):
    from spingnn_sif import render_job
    return render_job(run_id, value)




def submit(run_id, value):
    validate_delivery(value, run_id)
    directory = task(run_id)
    require(read(directory / 'input/build-attempt.json')['identities'] == [value['identity']],
            'missing matching SpinGNN release claim')
    request = directory / 'request.json'
    if request.exists():
        return inspect_submission(directory, dict(delivery=value, controller=str(CONTROL)))['request']
    require(not (directory / 'submission-intent.json').exists(),
            'incomplete SpinGNN submission; recover the held Slurm handle before retrying')
    from spingnn_sif import preflight
    preflight()
    sources = read(directory / 'input/spingnn-dependencies/sources.json')
    require(sources['source_sha'] == value['identity']['source_sha'], 'wrong source input')
    (directory / 'evidence').mkdir(exist_ok=True)
    for name in ('runtime', 'results', 'apptainer-cache'):
        (directory / name).mkdir(exist_ok=True)
    modules = directory / 'input/modulefiles' / site()['module_family']
    plain(modules).mkdir(parents=True, exist_ok=True)
    immutable(modules / (module_name(value['identity'], run_id) + '.lua'),
              module_text(value['identity'], value['identity']['install_prefix']).encode())
    immutable(directory / 'input/site.sh', site_script(value['identity'], run_id).encode())
    save(directory / 'input/delivery.json', value)
    immutable(directory / 'job.sbatch', render(run_id, value).encode())
    subprocess.run(['sbatch', '--test-only', str(directory / 'job.sbatch')], check=True, capture_output=True)
    save(directory / 'submission-intent.json', dict(delivery=value, script_sha256=checksum(directory / 'job.sbatch')))
    job = subprocess.check_output(['sbatch', '--hold', '--parsable', str(directory / 'job.sbatch')], text=True).strip().split(';')[0]
    require(job.isdigit() and int(job) > 0, 'invalid Slurm job handle')
    record = dict(schema=1, delivery=value, controller=str(CONTROL), job_id=job,
                  job_script_sha256=checksum(directory / 'job.sbatch'),
                  sources_sha256=checksum(directory / 'input/spingnn-dependencies/sources.json'),
                  created_ns=read(directory / 'input/created.json')['created_ns'])
    save(request, record)
    immutable(directory / 'job.id', (job + '\n').encode())
    subprocess.run(['scontrol', 'release', job], check=True)
    return record


def inventory(prefix):
    prefix = plain(prefix)
    require(prefix.is_dir(), 'missing SpinGNN prefix')
    result = {}
    for path in sorted(prefix.rglob('*')):
        relative = str(path.relative_to(prefix))
        if path.is_symlink():
            resolved = path.resolve(strict=True)
            corresponding_source = (path.is_relative_to(prefix / 'share/source') and
                                    resolved.is_relative_to(plain(site()['root']) / 'source') and resolved.is_file())
            require(resolved.is_relative_to(prefix) or
                    (str(resolved).startswith('/usr/bin/python') and resolved.is_file()) or corresponding_source,
                    'external SpinGNN runtime symlink: ' + str(path))
            if corresponding_source:
                require(resolved.stat().st_mode & 0o004 and not resolved.stat().st_mode & 0o022,
                        'unsafe shared corresponding source permissions')
            result[relative] = dict(link=os.readlink(path), sha256=checksum(resolved) if resolved.is_file() else None)
        elif path.is_file():
            require(path.stat().st_mode & 0o004 and not path.stat().st_mode & 0o022,
                    'SpinGNN runtime file permissions changed')
            result[relative] = dict(sha256=checksum(path), mode=stat.S_IMODE(path.stat().st_mode))
        else:
            require(path.is_dir(), 'special file in SpinGNN installation')
            require(path.stat().st_mode & 0o005 == 0o005 and not path.stat().st_mode & 0o022,
                    'SpinGNN runtime directory permissions changed')
    require('bin/lmp_spingnn2' in result and 'lib/libspingnn2.so.1' in result and 'venv/pyvenv.cfg' in result,
            'incomplete SpinGNN runtime installation')
    return result


def bounded(values, fields, tolerance, inclusive=False):
    require(set(values) == set(fields), 'incomplete SpinGNN numerical result')
    require(all(type(value) in (int, float) and math.isfinite(value) and value >= 0
                and (value <= tolerance if inclusive else value < tolerance)
                for value in values.values()), 'SpinGNN scientific threshold failed')


def gpu_evidence(case, identity, prefix):
    architecture = 'sm_' + identity['cuda_arch']
    require(architecture in (case / 'jit-cubin.txt').read_text(), 'JIT cubin has the wrong GPU architecture')
    kernels = list(csv.reader((case / 'cuda-kernels.csv').read_text().splitlines()))
    # Nsight emits a preamble and multiple CSV tables. Count actual kernel
    # rows, not function names in a compiler or API log.
    require(any(len(row) >= 8 and 'sp2_' in row[-1] and row[2].isdigit() and int(row[2]) > 0
                for row in kernels), 'missing executed SpinGNN CUDA kernels')
    require((case / 'cuda-trace.nsys-rep').stat().st_size > 0, 'missing raw CUDA trace')
    for variant, suffix in (('baseline', ''), ('rotated', '-rotated')):
        maps = (case / ('loaded-libraries' + suffix + '.txt')).read_text()
        require(str(prefix / 'lib/libspingnn2.so') in maps and 'libcuda.so' in maps,
                'CUDA science used an unexpected native runtime')
        log = (case / ('lammps-' + variant) / 'stdout.log').read_text()
        require(re.search(r'KOKKOS mode\b[^\n]*\bis enabled', log) and 'spingnn2/kk' in log
                and re.search(r'will use up to\s+1\s+GPU', log),
                'LAMMPS did not execute its GPU pair style')


def science(case, identity, prefix, job, *, legacy_cpu=False, runtime_prefix=None):
    case, prefix = plain(case), plain(prefix)
    runtime_prefix = plain(runtime_prefix) if runtime_prefix is not None else prefix
    value, fixture = read(case / 'acceptance.json'), read(case / 'fixture.json')
    policy = target_policy(identity['target'])
    require(value.get('environment', {}).get('MPI_HOME') == policy['mpi_prefix'],
            'SpinGNN science used a different MPI installation from the qualified target')
    if legacy_cpu:
        require(identity['target'] == 'dsprhbm' and policy['backend'] == 'cpu', 'legacy evidence is CPU-only')
    else:
        require(value.get('backend') == policy['backend'] and value.get('partition') == identity['partition'],
                'SpinGNN science backend or partition differs from its requested target')
    require(value.get('passed') is True and value.get('job_id') == job and value.get('torch_imported') is False,
            'missing successful allocated SpinGNN scientific evidence')
    require(value['python'] == str(runtime_prefix / 'venv/bin/python')
            and Path(value['spingnn2_file']).is_relative_to(runtime_prefix / 'venv')
            and value['binary_sha256'] == checksum(prefix / 'bin/lmp_spingnn2')
            and value['library_sha256'] == checksum(prefix / 'lib/libspingnn2.so.1'),
            'science did not use the accepted installation bytes')
    require(value['tolerances'] == dict(native_fp64=1e-10, lammps_fp64=1e-11), 'changed scientific tolerances')
    require(len(value['rows']) == 2 and [row['variant'] for row in value['rows']] == ['baseline', 'rotated'],
            'both spin configurations are required')
    for row in value['rows']:
        require(type(row['exitcode']) is int and row['exitcode'] == 0, 'LAMMPS failed')
        bounded(row['native_errors'], FIELDS, 1e-10)
        bounded(row['oracle_errors'], FIELDS, 1e-10)
        bounded(row['lammps_errors'], ('energy_ev', 'force_ev_angstrom', 'spin_force'), 1e-11, inclusive=True)
        require(row['max_force'] > 1e-8 and row['max_spin_force'] > 1e-8, 'trivial spin/force fixture')
    require(math.isfinite(value['spin_rotation_energy_change_ev'])
            and abs(value['spin_rotation_energy_change_ev']) > 1e-8, 'missing spin response')
    require(fixture['source_commit'] == identity['source_sha'] and fixture['trained_potential'] is False
            and fixture['seed'] == 1793, 'fixture identity changed')
    bounded(fixture['oracle_vs_eager_max_abs'], FIELDS, 1e-10)
    require({'model.sp2.json', 'model.sp2.bin', 'graph.npz', 'graph-rotated.npz',
             'reference.npz', 'reference-rotated.npz', 'eager.npz'} <= fixture['hashes'].keys(),
            'incomplete SpinGNN fixture hashes')
    for name, digest in fixture['hashes'].items():
        require(Path(name).name == name and checksum(plain(case / name)) == digest, 'fixture bytes changed')
    require(abs(value['spin_rotation_energy_change_ev'] - fixture['spin_rotation_energy_change_ev']) < 2e-11,
            'spin response differs from the independent reference')
    artifact = read(case / 'artifact.json')
    require(artifact['backend'] == policy['backend'] and artifact['precision'] == 'float64'
            and artifact['gpu_arch'] == ('sm_' + identity['cuda_arch'] if identity['gpus'] else '')
            and artifact['contract_sha256'] == fixture['hashes']['model.sp2.json']
            and Path(artifact['artifact']).name == artifact['artifact']
            and checksum(plain(case / artifact['artifact'])) == artifact['artifact_sha256'],
            'SpinGNN JIT artifact differs from the tested model or backend')
    if identity['gpus']:
        gpu_evidence(case, identity, runtime_prefix)
    return value


def seal(directory):
    raise ValueError("native-only completion cannot qualify a SIF delivery")


def accounting(job, workdir, account='proj-sai-hpc-software', partition='DSPRHBM'):
    output = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', job,
        '-o', 'JobIDRaw,State,ExitCode,Partition,Account,WorkDir'], text=True)
    rows = [line.split('|') for line in output.splitlines() if line.split('|')[0] == job]
    require(len(rows) <= 1, 'ambiguous SpinGNN allocation')
    if not rows:
        raise Pending('SpinGNN allocation is not yet visible in accounting')
    row = rows[0]
    require(row[3:6] == [partition, account, str(workdir)], 'SpinGNN allocation identity changed')
    if row[1] in ('PENDING', 'RUNNING', 'COMPLETING', 'CONFIGURING'):
        raise Pending('SpinGNN Slurm job ' + job + ': ' + row[1])
    require(row[1:3] == ['COMPLETED', '0:0'], 'SpinGNN Slurm job failed: ' + '|'.join(row))
    return row




def verify_bootstrap(boot, identity):
    identity = qualified_identity(identity)
    directory = plain(boot['directory'])
    require(directory.is_relative_to(PROJECT / 'runs') and plain(boot['prefix']).is_relative_to(site()['root']),
            'bootstrap escaped the reviewed project/deployment roots')
    for name, digest in boot['files'].items():
        # Retired build entry points can live in the deployment's preserved
        # before/ snapshot; absolute evidence paths must stay in project runs.
        path = plain(directory / name)
        require(path.is_relative_to(PROJECT / 'runs'), 'bootstrap evidence escaped project runs')
        require(checksum(path) == digest, 'changed first-install evidence: ' + name)
    require(hashlib.sha256(json.dumps(inventory(boot['prefix']), sort_keys=True).encode()).hexdigest()
            == boot['inventory_sha256'], 'first-install runtime changed after qualification')
    accounting(boot['job_id'], boot['workdir'], boot['account'], identity['partition'])
    for allocation in boot.get('extra_allocations', []):
        accounting(allocation['job_id'], allocation['workdir'], boot['account'], identity['partition'])
    science(directory / boot.get('case', 'acceptance'), identity, boot['prefix'], boot['job_id'],
            legacy_cpu=boot.get('legacy_cpu', False))


def adopt(run_id, value):
    validate_delivery(value, run_id)
    raise ValueError('native bootstrap cannot qualify a SIF delivery; build and accept the image')




def publish(run_id):
    from spingnn_sif import publish
    return publish(run_id)




def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('access', 'claim', 'submit', 'seal', 'publish', 'adopt', 'unpack'))
    parser.add_argument('run', nargs='?')
    parser.add_argument('delivery', nargs='?')
    parser.add_argument('--retry', action='store_true')
    args = parser.parse_args()
    if args.operation == 'access':
        result = check_access()
    elif args.operation == 'seal':
        result = seal(Path(args.run))
    elif args.operation == 'unpack':
        from spingnn_inputs import unpack
        directory = plain(args.run)
        value = validate_delivery(read(directory / 'request.json')['delivery'], directory.name)
        result = unpack(directory, value['identity']['source_sha'])
    elif args.operation == 'publish':
        result = publish(args.run)
    else:
        value = json.loads(args.delivery)
        result = (claim(args.run, value, args.retry) if args.operation == 'claim' else
                  adopt(args.run, value) if args.operation == 'adopt' else submit(args.run, value))
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    entry(main)
