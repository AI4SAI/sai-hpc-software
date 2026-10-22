#!/usr/bin/env python3
"""Recover this workflow's compiled overlays using their original frozen recipe."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path('/home/stardust/sai-hpc-software')
COMMIT = 'c1f63becb1502cb324c0fe4816f9ec4187fb04a1'
RECIPE = '38dfba7e3ed7e870c3320f22d2a430b8fae1f3b31c378d65bdd96337d57147cc'
TARGETS = ('16v100-avx2', '4v100-avx512', '8v100v0-avx512', 'dsprhbm')


def name(target):
    return '39293908513-1-release-' + target + '-2026-10-19-67b5da876dd6'


def execute(argv):
    return subprocess.check_output(list(map(str, argv)), text=True)


def isolate_script(script):
    """Keep the frozen commands, but pass their text as an argument, not stdin."""
    lines = script.splitlines()
    starts = [i for i, line in enumerate(lines) if line.endswith(" <<'SAI_BUILD_BODY'")]
    if len(starts) != 1 or lines[-1] != 'SAI_BUILD_BODY':
        raise ValueError('unexpected frozen script wrapper')
    start = starts[0]
    wrapper = shlex.split(lines[start].removesuffix(" <<'SAI_BUILD_BODY'"))
    if wrapper[-5:] != ['--', '/bin/bash', '-seu', '-o', 'pipefail']:
        raise ValueError('unexpected frozen Bash invocation')
    body = '\n'.join(lines[start + 1:-1]) + '\n'
    wrapper = wrapper[:-5] + ['--', '/bin/bash', '-euc', '-o', 'pipefail', body]
    return '\n'.join(lines[:start] + [shlex.join(wrapper) + ' < /dev/null']) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=TARGETS)
    args = parser.parse_args()
    old = ROOT / 'runs' / name(args.target)
    new = old.with_name(old.name + '-export')
    if new.exists():
        raise ValueError('recovery already exists; inspect its receipt before retrying')
    request = json.loads((old / 'request.json').read_text())
    control = ROOT / 'controller' / COMMIT / old.name
    if (request['controller'] != str(control) or request['identity']['recipe_sha256'] != RECIPE
            or request['target'] != args.target or request['software'] != 'cp2k'):
        raise ValueError('not the diagnosed frozen CP2K build')
    sys.path.insert(0, str(control))
    import software_controller as frozen
    from source_cache import checksum
    if frozen.recipe_fingerprint('cp2k') != RECIPE:
        raise ValueError('frozen controller changed')
    job = (old / 'job.id').read_text().strip()
    if not job.isdigit() or job in execute(['squeue', '-h', '-u', 'stardust', '-o', '%A']).split():
        raise ValueError('source job is invalid or active')
    accounting = execute(['sacct', '-X', '-nP', '-j', job, '-o', 'JobIDRaw,State,ExitCode'])
    if accounting.strip().split('|')[:3] != [job, 'COMPLETED', '0:0']:
        raise ValueError('source job is not the diagnosed completed-without-export run')
    if (old / 'artifact.path').exists() or (old / 'result.sif').exists():
        raise ValueError('source already has an artifact; refusing duplicate recovery')
    overlay = old / 'work.ext3'
    if overlay.is_symlink() or not overlay.is_file():
        raise ValueError('missing or redirected source overlay')
    prefix = request['identity']['install_prefix']
    installed = execute(['/usr/sbin/debugfs', '-R',
                         'cat /upper' + prefix + '/share/sai/release-identity.json', overlay])
    native = execute(['/usr/sbin/debugfs', '-R',
                      'cat /upper' + prefix + '/share/sai/native-entry.json', overlay])
    if json.loads(installed) != request['identity'] or json.loads(native)['identity'] != request['identity']:
        raise ValueError('compiled identity or native entry is incomplete')
    # Preserve the completed compiler output; only a new clone is mounted.
    overlay.chmod(0o444)
    overlay_sha = checksum(overlay)
    new.mkdir(mode=0o700)
    for directory in ('input', 'results', 'runtime', 'apptainer-cache'):
        (new / directory).mkdir(mode=0o700)
    # The frozen renderer retains this read-only bind in metadata/export.
    # These phases do not consume source updates, but its mountpoint must exist.
    (new / 'input/cp2k-updates').mkdir(mode=0o700)
    options = argparse.Namespace(**request)
    options.run_id = new.name
    options.resume_run = old.name
    options.resume_overlay_sha256 = overlay_sha
    options.dependencies_run = None
    options.minutes = 60
    original = frozen.render_job(options)
    # Remove only stale overlayfs work entries on the clone, using the same
    # validated helper as dependency -> application handoff. Upper stays intact.
    lines = original.splitlines()
    copy_line = shlex.join(['cp', '--sparse=always', '--reflink=auto', '--', str(overlay), str(new / 'work.ext3')])
    index = lines.index(copy_line)
    if 'sha256sum --check --status' not in lines[index + 1] or not lines[index + 2].startswith('chmod 600 '):
        raise ValueError('unexpected frozen clone sequence')
    preparation = ('import sys; sys.path.insert(0, ' + repr(str(control)) + '); '
                   'from cp2k_overlay_workdir import prepare_clone; '
                   'prepare_clone(*sys.argv[1:])')
    lines[index:index + 3] = [shlex.join(['/usr/bin/python3', '-c', preparation,
        str(overlay), str(new / 'work.ext3'), overlay_sha, str(new / 'results')])]
    script = isolate_script('\n'.join(lines) + '\n')
    if 'cp2k_container_entry.sh build ' in script:
        raise ValueError('recovery must not rebuild the application')
    script_path = new / 'job.sbatch'
    script_path.write_text(script)
    script_path.chmod(0o500)
    request.update(vars(options))
    request.update(recovery_source=old.name, recovery_source_job=job,
        recovery_source_accounting=accounting, recovery_tool_sha256=checksum(Path(__file__)),
        recovery_script_sha256=checksum(script_path),
        recovery_reason='MPI stdin forwarding consumed the heredoc export commands; compilation completed',
        recovery_changes=['isolated shell command source', 'prepared clone overlayfs work directory'],
        compiled_payload_changed=False, scientific_verified=False, published=False)
    (new / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    execute(['bash', '-n', script_path])
    execute(['sbatch', '--test-only', script_path])
    reply = execute(['sbatch', '--hold', '--parsable', script_path]).strip().split(';')[0]
    if not reply.isdigit():
        raise ValueError('ambiguous held submission; inspect scheduler before retrying')
    (new / 'job.id').write_text(reply + '\n')
    request['recovery_job'] = reply
    (new / 'request.json').write_text(json.dumps(request, sort_keys=True) + '\n')
    execute(['scontrol', 'release', reply])
    print(json.dumps(dict(target=args.target, job=reply, run=new.name,
                         source=old.name, overlay_sha256=overlay_sha)), flush=True)


if __name__ == '__main__':
    main()
