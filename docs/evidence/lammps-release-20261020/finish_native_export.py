#!/usr/bin/env python3
"""Finish metadata and SIF export for the compiled 4V100 release, without rebuilding."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

PROJECT = Path('/home/stardust/sai-hpc-software')
ROOT = PROJECT / 'experimental/deepmd-lammps'
SOURCE_RUN = 'md-39403117741-1-2026-10-20-9bf6ae82814537d7'
SOURCE_JOB = '1877799'
RECIPE = '296ed3baa11b0bb5629dfb60e7bf396083367a613719dfd8d21767082fa98097'
DIAGNOSTICS = PROJECT / 'diagnostics/lammps-release-20261020'


def execute(argv):
    return subprocess.check_output(list(map(str, argv)), text=True)


def metadata_script(fixed):
    build = (fixed / 'md_build.sh').read_text()
    clean = 'for name in PATH LD_LIBRARY_PATH PYTHONPATH; do\n'
    clean += build.split('for name in PATH LD_LIBRARY_PATH PYTHONPATH; do\n', 1)[1].split('\ndone\n', 1)[0] + '\ndone\n'
    save = 'for name in PATH LD_LIBRARY_PATH PYTHONPATH PLUMED_KERNEL LAMMPS_POTENTIALS; do\n'
    save += build.split('for name in PATH LD_LIBRARY_PATH PYTHONPATH PLUMED_KERNEL LAMMPS_POTENTIALS; do\n', 1)[1].split('\ndone > ', 1)[0]
    save += '\ndone > "$LAMMPS_PREFIX/share/sai/runtime-env.sh"\n'
    native = build.split('# Package the observed runtime directly with the shared native delivery writer.', 1)[1]
    return '''#!/usr/bin/env bash
set -euo pipefail
export SAI_MD_DELIVERY=$1
mapfile -t settings < <(/usr/bin/python3 - <<'PY'
import json, os, sys
sys.path.insert(0, '/control')
from md_controller import validate_pair
from md_tracking import fingerprint
d = validate_pair(json.loads(os.environ['SAI_MD_DELIVERY']))
assert fingerprint('/control') == d['recipe_sha256']
print(d['plan']['target'])
print(d['identities']['deepmd-kit']['install_prefix'])
print(d['identities']['lammps']['install_prefix'])
PY
)
target=${settings[0]}
export DEEPMD_PREFIX=${settings[1]} LAMMPS_PREFIX=${settings[2]}
export TMPDIR=/workspace/tmp
source /control/md_environment.sh "$target"
source "$LAMMPS_PREFIX/share/sai/runtime-env.sh"
"$DEEPMD_PREFIX/bin/python" /control/md_source_version.py verify-deepmd
"$DEEPMD_PREFIX/bin/python" - <<'PY'
import hashlib, json, os, pathlib, sys
sys.path.insert(0, '/control')
from md_evidence import verify_parity
p = pathlib.Path(os.environ['LAMMPS_PREFIX']) / 'share/sai'
print(verify_parity(json.loads((p/'baseline.json').read_text()), json.loads((p/'candidate.json').read_text())))
hashes = {}
for key in ('DEEPMD_PREFIX', 'LAMMPS_PREFIX'):
    for f in pathlib.Path(os.environ[key]).rglob('*'):
        if f.is_symlink() or not f.is_file(): continue
        with f.open('rb') as stream:
            if stream.read(4) != b'\\x7fELF': continue
            stream.seek(0)
            hashes[str(f)] = hashlib.file_digest(stream, 'sha256').hexdigest()
assert len(hashes) >= 4
pathlib.Path('/workspace/recovery-elf-before.json').write_text(json.dumps(hashes, sort_keys=True)+'\\n')
PY
''' + clean + save + '''
"$DEEPMD_PREFIX/bin/python" - <<'PY'
import hashlib, json, os, pathlib
before=json.loads(pathlib.Path('/workspace/recovery-elf-before.json').read_text())
for name, expected in before.items():
    with open(name, 'rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == expected
record=json.loads(pathlib.Path('/recovery/recovery.json').read_text())
record.update(compiled_elf_unchanged=True, compiled_elf_sha256=before)
for key in ('DEEPMD_PREFIX','LAMMPS_PREFIX'):
    path=pathlib.Path(os.environ[key])/'share/sai/export-recovery.json'
    with path.open('x') as stream: json.dump(record,stream,sort_keys=True); stream.write('\\n')
    path.chmod(0o444)
PY
''' + native + '\necho MD_METADATA_RECOVERY_COMPLETE_NO_RECOMPILATION\n'


def main():
    old, new = (ROOT / 'runs' / name for name in (SOURCE_RUN, SOURCE_RUN + '-export'))
    if (new / 'job.id').exists():
        raise ValueError('recovery already submitted')
    request = json.loads((old / 'request.json').read_text())
    control = Path(request['controller'])
    sys.path.insert(0, str(control))
    import md_controller as md
    from md_tracking import fingerprint
    from source_cache import checksum
    from cp2k_image_driver import wrap_command
    if (request['run_id'] != SOURCE_RUN or request['stage_jobs']['lammps'] != SOURCE_JOB
            or request['delivery']['recipe_sha256'] != RECIPE or fingerprint(control) != RECIPE):
        raise ValueError('compiled source identity or controller changed')
    if execute(['squeue', '-h', '-j', SOURCE_JOB, '-o', '%A']).strip():
        raise ValueError('compiled source job is still active')
    accounting = execute(['sacct', '-X', '-nP', '-j', SOURCE_JOB, '-o', 'JobIDRaw,State,ExitCode'])
    if accounting.strip().split('|')[:3] != [SOURCE_JOB, 'FAILED', '1:0']:
        raise ValueError('unexpected source accounting')
    source = json.loads((new / 'input/source.json').read_text())
    if (source['source'] != str(old) or source['source_job'] != SOURCE_JOB
            or source['request_sha256'] != checksum(old / 'request.json')):
        raise ValueError('clone provenance differs')
    # Reuse the qualified CP2K work-directory inspector on the new clone.
    fixed = DIAGNOSTICS / 'preflight-native-r4/controller'
    sys.path.append(str(fixed))
    from cp2k_overlay_workdir import Inspector, lock
    overlay = new / 'work.ext3'
    source_fd = os.open(old / 'work.ext3', os.O_RDONLY | os.O_NOFOLLOW)
    fd = os.open(overlay, os.O_RDWR | os.O_NOFOLLOW)
    try:
        lock(source_fd, False)
        lock(fd, True)
        if (checksum(old / 'work.ext3') != source['overlay_sha256']
                or checksum(overlay) != source['overlay_sha256']):
            raise ValueError('source or clone checksum changed')
        directory = new / 'results/overlay-preparation'
        directory.mkdir()
        report = dict(source=source, commands=[],
                      workdir_helper_sha256=checksum(fixed / 'cp2k_overlay_workdir.py'))
        inspect = Inspector(fd, directory, report)
        inspect.command(['/usr/sbin/e2fsck', '-pf', '/proc/self/fd/' + str(fd)], allowed=(0, 1))
        checked = inspect.command(['/usr/sbin/e2fsck', '-fn', '/proc/self/fd/' + str(fd)])
        if any(word in checked.stdout + checked.stderr for word in ('Fix?', ' wrong', 'WARNING', 'UNEXPECTED')):
            raise ValueError('clone filesystem is not clean')
        upper, entries, commands = inspect.inventory()
        if commands:
            inspect.debugfs(commands, writable=True)
            os.fsync(fd)
        upper_after, remaining, unused = inspect.inventory()
        if upper_after != upper or remaining or unused:
            raise ValueError('overlay preparation did not preserve upper/empty work')
        checked = inspect.command(['/usr/sbin/e2fsck', '-fn', '/proc/self/fd/' + str(fd)])
        if any(word in checked.stdout + checked.stderr for word in ('Fix?', ' wrong', 'WARNING', 'UNEXPECTED')):
            raise ValueError('prepared clone is not clean')
        report.update(removed_work_entries=entries, prepared_overlay_sha256=checksum(overlay),
                      source_unchanged=checksum(old / 'work.ext3') == source['overlay_sha256'])
        if not report['source_unchanged']:
            raise ValueError('original compiled image changed')
        (new / 'results/overlay-preparation.json').write_text(json.dumps(report, sort_keys=True)+'\n')
    finally:
        os.close(fd)
        os.close(source_fd)
    metadata = new / 'input/finish-metadata.sh'
    metadata.write_text(metadata_script(fixed))
    verify = new / 'input/verify.sh'
    original = (control / 'md_container_entry.sh').read_text()
    if original.count('  verify)\n') != 1:
        raise ValueError('unknown final verifier')
    verify.write_text(original.replace('  verify)\n', '  verify)\n    export TMPDIR=/runtime\n'))
    record = dict(source=source, compiled_recipe_sha256=RECIPE, frozen_controller=str(control),
                  recovery_tool_sha256=checksum(Path(__file__)), metadata_sha256=checksum(metadata),
                  verification_wrapper_sha256=checksum(verify),
                  changes=['normalize /bin and /sbin in saved PATH',
                           'write native manifests with unchanged shared validators',
                           'verify read-only SIF without fakeroot and with home-backed TMPDIR'],
                  acceptance='unchanged source-bound scientific and performance gates required')
    (new / 'input/recovery.json').write_text(json.dumps(record, sort_keys=True)+'\n')
    for path in (metadata, verify, new / 'input/recovery.json'):
        path.chmod(0o444)
    for path in (metadata, verify):
        execute(['bash', '-n', path])
    delivery = request['delivery']
    script = md.render(delivery, new.name, jobs=6, minutes=45, stage='lammps')
    lines = script.splitlines()
    for index, line in enumerate(lines):
        if line == '#SBATCH --gpus-per-node=4': lines[index] = '#SBATCH --gpus-per-node=1'
        elif line == '#SBATCH --qos=rush-gpu': lines[index] = '#SBATCH --qos=flood-1o2gpu'
        elif line.startswith('echo MD_BUILD_RESOURCES '): lines[index] = 'echo MD_RECOVERY_RESOURCES gpus=1 jobs=6'
        elif 'md_container_entry.sh build-lammps ' in line:
            argv = md.container_command(PROJECT / 'containers/base/minimal-v1.sif',
                ['/usr/bin/bash', '/recovery/finish-metadata.sh', json.dumps(delivery, sort_keys=True)],
                overlay=overlay, control=control, jobs=6, gpu=True,
                extra_binds=[*md.site_app_binds('lammps'), (new / 'input', '/recovery')])
            lines[index] = shlex.join(wrap_command(ROOT, new, control, argv))
        elif 'md_container_entry.sh verify ' in line:
            argv = md.container_command(new / 'candidate.sif',
                ['/usr/bin/bash', '/recovery/verify.sh', 'verify', json.dumps(delivery, sort_keys=True)],
                control=control, jobs=6, gpu=True, runtime=new / 'runtime',
                extra_binds=[*md.site_app_binds('lammps'), (new / 'input', '/recovery')])
            lines[index] = shlex.join([arg for arg in argv if arg != '--fakeroot'])
    script = '\n'.join(lines)+'\n'
    if 'md_container_entry.sh build-lammps ' in script or 'cmake --build' in metadata.read_text():
        raise ValueError('recovery would recompile')
    path = new / 'job.sbatch'
    path.write_text(script)
    execute(['bash', '-n', path])
    shutil.copyfile(old / 'job-deepmd.sbatch', new / 'job-deepmd.sbatch')
    shutil.copyfile(old / 'request.json', new / 'source-request.json')
    execute(['sbatch', '--test-only', path])
    job = execute(['sbatch', '--hold', '--parsable', path]).strip().split(';')[0]
    if not job.isdigit(): raise ValueError('ambiguous held submission')
    request.update(run_id=new.name, job_script_sha256=checksum(path), metadata_recovery=record)
    request['stage_jobs']['lammps'] = job
    request['stage_job_script_sha256']['lammps'] = checksum(path)
    (new / 'request.json').write_text(json.dumps(request, sort_keys=True)+'\n')
    (new / 'job.id').write_text(job+'\n')
    execute(['scontrol', 'release', job])
    print(json.dumps(dict(job=job, run=new.name, source=SOURCE_RUN, no_recompilation=True)), flush=True)


if __name__ == '__main__':
    main()
