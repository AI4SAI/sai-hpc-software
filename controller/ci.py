#!/usr/bin/env python3
"""GitHub-side orchestration; remote commands always come from this repository."""
from datetime import datetime, timezone
import inspect
import json
import os
import re
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from remote_controller import safe_name, safe_sha, SAI_PROJECT_ROOT
from delivery_layout import artifact_path
from release_contract import SOFTWARE, TRACKS, validate_identity
from source_transport import ensure_source
from relay_checkpoint import Checkpoint, entry, remote as remote_call

def run(argv, **kwargs):
    try:
        return subprocess.run(list(map(str, argv)), check=True, text=True, **kwargs)
    except subprocess.CalledProcessError as error:
        # Machine-readable stdout stays captured; failed remote diagnostics do not.
        detail = (error.stderr or error.stdout or '').strip()
        if detail:
            print(detail, file=__import__('sys').stderr, flush=True)
            results = Path(os.environ['RUNNER_TEMP']) / 'results'
            results.mkdir(parents=True, exist_ok=True)
            with (results / 'command-errors.log').open('a') as stream:
                stream.write(detail + '\n')
        raise


def runtime_launchers(software):
    if software == "qe":
        from sif_runtime import PROGRAMS
        return ("qe", *PROGRAMS["qe"])
    return ("gpumd", "nep", "gnep") if software == "gpumd" else (software,)


def install_controller(staging, directory, manifest, executables):
    """Commit verified files without rewriting a prior immutable launcher.

    Sent verbatim to the SSH host. Interrupted uploads only affect staging;
    interrupted commits can be repeated after checking every existing file.
    """
    import hashlib
    import os
    from pathlib import Path

    staging, directory = Path(staging), Path(directory)
    for path in (staging, directory):
        if not path.is_absolute() or path.resolve() != path or not path.is_dir():
            raise ValueError('redirected controller upload directory')
    if staging.parent != directory.parent or not staging.name.startswith(directory.name + '.upload-'):
        raise ValueError('controller upload is outside its run')
    if not set(executables) <= set(manifest):
        raise ValueError('unknown controller launcher')
    for name, digest in manifest.items():
        if Path(name).name != name or name in ('.', '..'):
            raise ValueError('invalid controller filename')
        source, target = staging / name, directory / name
        if source.is_symlink() or not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise ValueError('incomplete controller upload: ' + name)
        if target.exists() or target.is_symlink():
            if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise ValueError('existing controller differs from the frozen source: ' + name)
    for name in manifest:
        source, target = staging / name, directory / name
        source.chmod(0o555 if name in executables else 0o444)
        try:
            os.link(source, target)
        except FileExistsError:
            if target.is_symlink() or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != manifest[name]:
                raise ValueError('controller changed during upload: ' + name)
            target.chmod(0o555 if name in executables else 0o444)
        source.unlink()
    staging.rmdir()
    return True


def remote_project_root(software, user):
    # New runs use the funded project. Existing home-backed continuations keep
    # their original controller snapshot, including its original root policy.
    if software not in SOFTWARE:
        raise ValueError('unknown software project')
    safe_name(user)
    return str(SAI_PROJECT_ROOT)


def remote_controller_command(root, control, script, *args):
    return ['env', 'SAI_SOFTWARE_ROOT=' + root, 'python3', f'{control}/{script}', *args]


def stage_archives(python, upload, root, directory, archives):
    """Cache lookup is remote; only verified cache misses cross the SSH link."""
    for archive, digest in archives:
        name = Path(archive).name
        arguments = [root, directory, name, digest]
        hit = json.loads(python('input_archive_cache.py', 'stage', *arguments,
                                capture_output=True).stdout)
        if type(hit) is not bool:
            raise ValueError('invalid dependency archive cache response')
        if hit:
            print(f'ARCHIVE_CACHE_HIT {name} {digest}: zero archive upload', flush=True)
            continue
        temporary = f'{directory}/.upload-{uuid.uuid4().hex}-{name}'
        upload(archive, temporary)
        committed = json.loads(python('input_archive_cache.py', 'commit', *arguments,
                                      '--upload', temporary, capture_output=True).stdout)
        if committed is not True:
            raise ValueError('dependency archive upload was not committed')


def inspect_existing_job(root, control, directory, expected, script):
    """Read-only continuation preflight, also sent verbatim to the SSH host.

    An incomplete submission is ambiguous: never guess that it is safe to
    resubmit without its recorded Slurm handle. Monitors reverify raw evidence.
    """
    import argparse
    import json
    import os
    from pathlib import Path
    import sys

    root, control, directory = map(Path, (root, control, directory))
    for path in (root, control, directory):
        if not path.is_absolute() or path.resolve() != path or path.is_symlink():
            raise ValueError('untrusted CI continuation path')
    if (not control.is_relative_to(root / 'controller') or
            directory.parent not in (root / 'runs', root / 'runtime-tests')):
        raise ValueError('CI continuation escaped its immutable project')
    if not directory.exists():
        return False
    names = ('request.json', 'job.id', 'job.sbatch')
    present = [(directory / name).exists() or (directory / name).is_symlink() for name in names]
    if script == 'software_controller.py' and not any(present):
        # Dependencies/source staging create this directory before application submission.
        if any(path.name not in ('input', 'results', 'runtime', 'apptainer-cache')
               for path in directory.iterdir()):
            raise ValueError('ambiguous application submission state')
        return False
    if not all(present):
        raise ValueError('incomplete previous submission; recover its Slurm handle before continuing')
    for name in names:
        path = directory / name
        if not path.is_file() or path.is_symlink() or path.resolve() != path:
            raise ValueError('untrusted CI continuation metadata')
    request = json.loads((directory / 'request.json').read_text())
    if any(request.get(key) != value for key, value in expected.items()):
        raise ValueError('existing job differs from the immutable CI handoff')
    job = (directory / 'job.id').read_text().strip()
    if not job.isdigit() or ('job' in request and request['job'] != job):
        raise ValueError('existing Slurm handle differs from its request')
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(control))
    os.environ['SAI_SOFTWARE_ROOT'] = str(root)
    from source_cache import checksum
    from software_controller import recipe_fingerprint
    identity = expected['identity']
    if recipe_fingerprint(identity['software'], control) != identity['recipe_sha256']:
        raise ValueError('existing job recipe changed')
    if script == 'software_controller.py':
        from software_controller import render_job
        from delivery_layout import CONTRACT_SCHEMA, validate_record
        if request.get('contract_schema') != CONTRACT_SCHEMA or validate_record(request) != identity:
            raise ValueError('existing build identity changed')
        if (directory / 'job.sbatch').read_text() != render_job(argparse.Namespace(**request)):
            raise ValueError('existing build script changed')
    else:
        from delivery_layout import load_artifact
        record = load_artifact(root, expected['artifact'], software=identity['software'], target=identity['target'])
        if record['identity'] != identity or request.get('artifact_sha256') != record['sha256']:
            raise ValueError('existing acceptance image changed')
        pins = [(directory / 'job.sbatch', 'job_script_sha256'),
                (control / identity['software'], 'launcher_sha256'),
                (control / script, 'controller_sha256')]
        if request.get('launcher') != str(control / identity['software']):
            raise ValueError('existing acceptance launcher differs')
        if script == 'gpu_feature_controller.py':
            pins.append((control / 'gpu_feature_runtime.sh', 'runtime_sha256'))
        elif script == 'gpumd_acceptance.py':
            pins += [(control / 'gpumd_science.py', 'science_sha256'),
                     (control / 'gpumd_deepmd_probe.py', 'deepmd_probe_sha256')]
        elif script == 'qe_acceptance.py':
            pins += [(control / 'qe_science.py', 'science_sha256'), (control / 'qe_fixture.json', 'fixture_sha256')]
        elif script == 'cp2k_acceptance.py':
            pins += [(control / 'cp2k_benchmark.py', 'helpers_sha256'),
                     (directory / 'rank-exec.sh', 'rank_wrapper_sha256')]
        for path, key in pins:
            if (not path.is_file() or path.is_symlink() or path.resolve() != path or
                    checksum(path) != request.get(key)):
                raise ValueError('existing acceptance pin changed: ' + key)
    return True


def existing_job(ssh, root, control, directory, expected, script):
    code = inspect.getsource(inspect_existing_job) + "\nimport json, sys\nprint(json.dumps(inspect_existing_job(*sys.argv[1:4], json.loads(sys.argv[4]), sys.argv[5])))\n"
    result = ssh(['python3', '-c', code, root, control, directory,
                  json.dumps(expected, sort_keys=True), script], capture_output=True)
    found = json.loads(result.stdout)
    if type(found) is not bool:
        raise ValueError('invalid continuation preflight response')
    return found


def inspect_multinode_mapping_failure(root, directory):
    """Allow one fresh allocation only when PRTE failed before starting ranks."""
    import hashlib
    import json
    from pathlib import Path
    import re
    import subprocess

    root, task = Path(root), Path(directory)
    if (root.resolve() != root or task.resolve() != task or
            task.parent != root / 'runtime-tests' or not task.name.endswith('-multinode')):
        raise ValueError('untrusted multi-node retry path')

    def read(relative):
        path = task / relative
        if not path.is_file() or path.is_symlink() or path.resolve() != path:
            raise ValueError('missing or redirected mapping failure evidence: ' + relative)
        return path.read_bytes()

    request_bytes = read('request.json')
    request = json.loads(request_bytes)
    if (request.get('target') != 'dsprhbm' or
            request.get('identity', {}).get('software') != 'abacus' or
            (request.get('nodes'), request.get('ranks_per_node'), request.get('cpus_per_task'),
             request.get('gpus_per_node')) != (2, 4, 2, 0)):
        return False
    job = read('job.id').decode().strip()
    if not job.isdigit():
        raise ValueError('invalid mapping failure job')
    status = json.loads(read('results/status.json'))
    if status != dict(job=job, state='FAILED', exit_code='254:0', verified=False):
        return False
    rows = subprocess.check_output(['sacct', '-X', '-n', '-P', '-j', job,
                                   '-o', 'JobIDRaw,State,ExitCode,WorkDir'], text=True)
    matching = [line.split('|')[:4] for line in rows.splitlines() if line.split('|')[0] == job]
    if matching != [[job, 'FAILED', '254:0', str(task)]]:
        return False
    log = read('results/abacus.log')
    if not re.search(rb'Your job failed to map because the resulting process placement\s+'
                     rb'would cause the process to be bound to CPUs in more than one\s+package:', log):
        return False
    ranks = task / 'results/ranks'
    if (ranks.is_symlink() or (ranks.exists() and (not ranks.is_dir() or any(ranks.iterdir()))) or
            (task / 'case/OUT.autotest').exists() or (task / 'results/final-energy-ev.txt').exists()):
        return False
    receipt = dict(schema=1, source_run=task.name, retry_run=task.name + '-retry1', job=job,
                   reason='MPI package mapping failed before rank startup',
                   request_sha256=hashlib.sha256(request_bytes).hexdigest(),
                   log_sha256=hashlib.sha256(log).hexdigest(),
                   artifact_sha256=request['artifact_sha256'])
    path = task / 'results/mapping-retry.json'
    content = (json.dumps(receipt, sort_keys=True) + '\n').encode()
    if path.exists() or path.is_symlink():
        if read('results/mapping-retry.json') != content:
            raise ValueError('original mapping failure changed after retry authorization')
    else:
        with path.open('xb') as stream:
            stream.write(content)
    return True


def multinode_retry_allowed(ssh, root, control, directory, expected):
    code = (inspect.getsource(inspect_existing_job) + '\n' +
            inspect.getsource(inspect_multinode_mapping_failure) +
            '\nimport json, sys\n'
            'present = inspect_existing_job(*sys.argv[1:4], json.loads(sys.argv[4]), "runtime_controller.py")\n'
            'print(json.dumps(present and inspect_multinode_mapping_failure(sys.argv[1], sys.argv[3])))\n')
    result = ssh(['python3', '-c', code, root, control, directory,
                  json.dumps(expected, sort_keys=True)], capture_output=True)
    allowed = json.loads(result.stdout)
    if type(allowed) is not bool:
        raise ValueError('invalid mapping retry response')
    return allowed


def accept_multinode(python, exists, allow_retry, run_id, version, target):
    runtime_run = safe_name(run_id + '-multinode')
    for suffix in ('', '-retry1'):
        selected = safe_name(runtime_run + suffix)
        if not exists('runtime_controller.py', selected):
            python('runtime_controller.py', 'submit', selected, version, target,
                   '--build-run-id', run_id)
        try:
            python('runtime_controller.py', 'monitor', selected)
            return
        except subprocess.CalledProcessError:
            if suffix or target != 'dsprhbm' or not allow_retry(runtime_run):
                raise
            print('MULTINODE_MAPPING_RETRY: fresh allocation, same SIF and resources', flush=True)


def main():
    relay = Checkpoint()
    software = os.environ.get("SOFTWARE", "abacus")
    if software not in ("abacus", "cp2k", "gpumd", "qe"):
        raise ValueError("unknown software recipe")
    phase = os.environ.get("CP2K_PHASE") or os.environ.get("CI_PHASE", "all")
    if phase not in ("all", "build", "dependencies", "application", "acceptance"):
        raise ValueError("unknown software CI phase")
    if phase in ("dependencies", "application") and software != "cp2k":
        raise ValueError("dependency handoff is only registered for CP2K")
    continuing = phase in ("application", "acceptance") or bool(relay.data.get("prepared"))
    target = os.environ["TARGET"]
    if target not in SOFTWARE[software]['targets']:
        raise ValueError("unknown target")
    upstream = safe_sha(os.environ["SOURCE_SHA"])
    control_sha = safe_sha(os.environ["GITHUB_SHA"])
    version = os.environ["SOFTWARE_VERSION"]
    track = os.environ["RELEASE_TRACK"]
    if track not in TRACKS:
        raise ValueError("unknown release track")
    source_ref = os.environ["SOURCE_REF"]
    provenance_flags = ["--track", track, "--source-ref", source_ref]
    user = safe_name(os.environ["REMOTE_USER"])
    run_prefix = "gpumd-" if software == "gpumd" else ""
    run_id = safe_name(run_prefix + "-".join((os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"],
                                 track, target, datetime.now(timezone.utc).date().isoformat(), upstream[:12])))
    if relay.enabled:
        run_id = relay.freeze("run_id", run_id)
        relay.bind(dict(software=software, target=target, upstream=upstream, controller=control_sha,
                        version=version, track=track, source_ref=source_ref,
                        workflow=os.environ["GITHUB_RUN_ID"], attempt=os.environ["GITHUB_RUN_ATTEMPT"]))
        if relay.data.get("skipped"):
            print("UNCHANGED_RELEASE_SKIPPED: frozen claim", flush=True)
            return
    if continuing:
        run_id = safe_name(relay.data["run_id"] if relay.enabled else os.environ['CI_RUN_ID'])
        pattern = (re.escape(run_prefix + os.environ['GITHUB_RUN_ID']) + r'-[0-9]+-'
                   + re.escape(track + '-' + target) + r'-\d{4}-\d{2}-\d{2}-' + re.escape(upstream[:12]))
        if not re.fullmatch(pattern, run_id):
            raise ValueError('CI handoff does not belong to this workflow/source/track/target')
    if software == "abacus":
        safe_name("benchmark-" + run_id + "-deepks")
    elif software == "gpumd":
        safe_name(run_id + "-science")
    temporary = Path(os.environ["RUNNER_TEMP"])
    key = Path(os.environ.get("SAI_RELAY_SSH_KEY", str(temporary / "ssh/key")))
    known_hosts = Path(__file__).resolve().parents[1] / ".ci/slurm/known_hosts"
    options = ["-i", str(key), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", f"UserKnownHostsFile={known_hosts}", "-o", "ConnectTimeout=20",
               "-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=6"]
    remote = f"{user}@c0.sai.ai-4s.com"
    root = remote_project_root(software, user)
    # One deployment per run: concurrent matrix uploads cannot truncate code
    # already being read by another running container.
    control = f"{root}/controller/{control_sha}/{run_id}"
    task = f"{root}/runs/{run_id}"
    cache = f"{root}/cache/repositories/{software}"
    def ssh(argv, **kw):
        return run(["ssh", *options, "-p", "12022", remote, shlex.join(argv)], **kw)
    def python(script, *args, **kw):
        if relay.enabled and args and args[0] == 'monitor':
            if script == 'cp2k_dependency_stage.py':
                directory = f'{root}/runs/{args[2]}'
            elif script == 'software_controller.py':
                directory = f'{root}/runs/{args[1]}'
            else:
                prefix = 'benchmark-' if script == 'abacus_benchmark.py' else ''
                directory = f'{root}/runtime-tests/{prefix}{args[1]}'
            relay.wait(ssh, directory)
        return ssh(remote_controller_command(root, control, script, *args), **kw)
    def upload(source, destination):
        sources = list(source) if isinstance(source, (list, tuple)) else [source]
        for attempt in range(3):
            try:
                return run(["scp", "-q", *options, "-P", "12022", *sources,
                            f"{remote}:{destination}"], timeout=1800)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                if attempt == 2:
                    raise
                print(f"retrying upload of {len(sources)} file(s)", flush=True)
                time.sleep(3)
    if not continuing:
        ssh(["mkdir", "-p", control, f"{task}/input", f"{task}/results"])
        parent = Path(__file__).resolve().parent
        common = ["software_controller.py", "remote_controller.py", "source_cache.py", "module_publication.py",
                  "runtime_controller.py", "create_rootfs.sh", "release_contract.py", "resolve_source.py",
                  "delivery_layout.py", "input_archive_cache.py"]
        recipe = (["container_entry.sh", "environment.sh", "abacus_build.sh",
                   "cp2k_image_driver.py", "managed_overlay.py", "cp2k_overlay_sync.py",
                   "gpu_feature_controller.py", "gpu_feature_runtime.sh",
                   "abacus_dependencies.py", "abacus_dependencies.sh",
                   "abacus_full_profile.json", "abacus_full_dependencies.sh", "abacus_site_mpi.sh", "abacus_site_gpu.sh",
                   "abacus_dependency_lock.json", "abacus_cusolvermp_case.json", "abacus_features.py", "abacus_benchmark.py",
                   "native_module.py", "export_native.py"]
                  if software == "abacus" else
                  ["cp2k_container_entry.sh", "environment.sh", "cp2k_build.sh", "cp2k_dependencies.sh", "cp2k_auxiliary_cache.py", "cp2k_libxc.sh", "cp2k_libxs.sh", "cp2k_elpa.sh", "cp2k_elpa_runtime.py", "cp2k_feature_contract.py", "cp2k_site_elpa.json", "cp2k_elpa_abi.f90",
                   "cp2k_Libint2Config.cmake", "cp2k_libxsmmConfig.cmake", "cp2k_cmake_prerequisites.cmake", "cp2k_benchmark.py", "cp2k_acceptance.py", "cp2k_source_fixes.py",
                   "cp2k_spack.py", "cp2k_spack_store.py", "cp2k_spack_environment.py", "cp2k_spack_native.py",
                   "cp2k_site_spack.py", "cp2k_site_spack.json", "cp2k_devtools.py", "devtools_install.py",
                   "cp2k_spack_seed.py", "cp2k_spack_probe.sh", "cp2k_spack_dependencies.sh", "cp2k_overlay_sync.py",
                   "cp2k_spack_libint_repair.py", "cp2k_spack_install_evidence.py",
                   "cp2k_spack_link.py", "cp2k_payload.py", "cp2k_dependency_stage.py", "cp2k_overlay_workdir.py", "cp2k_image_driver.py", "managed_overlay.py", "build_capacity.py",
                   "native_module.py", "export_native.py"])
        if software == "gpumd":
            recipe = ["gpumd_container_entry.sh", "gpumd_environment.sh", "gpumd_build.sh",
                      "gpumd_science.py", "gpumd_reference_cases.json", "gpumd_deepmd_probe.py", "gpumd_acceptance.py",
                      "export_native.py", "native_module.py", "cp2k_image_driver.py",
                      "managed_overlay.py", "cp2k_overlay_sync.py"]
        if software == "qe":
            from software_controller import contract_files
            recipe = [name for name in contract_files("qe") if name != "qe"]
        # One SFTP connection avoids paying the site's SSH handshake latency
        # for every trusted file. All files still arrive before fingerprinting
        # or submission, inside this run's own controller directory.
        staged = Path(tempfile.mkdtemp(prefix='controller-upload-', dir=temporary))
        for name in dict.fromkeys(common + recipe):
            shutil.copyfile(parent / name, staged / name)
        for executable in runtime_launchers(software):
            shutil.copyfile(parent / f"{software}_runtime.sh", staged / executable)
        upload_directory = control + '.upload-' + uuid.uuid4().hex
        ssh(['mkdir', upload_directory])
        files = sorted(staged.iterdir())
        upload(files, upload_directory + '/')
        from hashlib import sha256
        manifest = {path.name: sha256(path.read_bytes()).hexdigest() for path in files}
        if remote_call(ssh, install_controller, upload_directory, control, manifest, runtime_launchers(software)) is not True:
            raise ValueError('controller upload was not committed')
    results = temporary / "results"
    results.mkdir(exist_ok=True)
    # The uploaded, immutable remote recipe owns the fingerprint. Never invent
    # an identity from the runner's checkout or from a mutable channel label.
    identity = validate_identity(json.loads(python("software_controller.py", "identity", software,
        upstream, version, target, *provenance_flags, capture_output=True).stdout))
    expected_fields = {"software": software, "track": track, "source_ref": source_ref,
                       "source_sha": upstream, "source_version": version, "target": target}
    if any(identity[name] != value for name, value in expected_fields.items()):
        raise ValueError("remote identity differs from the resolved source")
    expected_artifact = artifact_path(Path(root), identity, run_id)
    (results / "identity.json").write_text(json.dumps(identity, sort_keys=True) + "\n")
    # Development always rebuilds. Other channels are attempted once per
    # upstream version, even after failure, unless retry is explicitly selected.
    if not continuing and track != "development":
        retry = (["--retry"] if (os.environ.get("RETRY_RELEASES") == "true" or os.environ.get("RESUME_RUN")) else [])
        claim = json.loads(python("release_contract.py", root, run_id, json.dumps([identity]),
                                  *retry, *(["--resume"] if relay.enabled else []), capture_output=True).stdout)
        (results / "build-attempt.json").write_text(json.dumps(claim, sort_keys=True) + "\n")
        if not claim["build"]:
            relay.set("skipped", True)
            print("UNCHANGED_RELEASE_SKIPPED " + json.dumps(claim["skipped"]), flush=True)
            return
    marker = dict(schema=1, run_id=run_id, workflow_run_id=os.environ['GITHUB_RUN_ID'],
                  controller_sha=control_sha, identity=identity)
    if continuing:
        observed = json.loads(ssh(['cat', task + '/input/ci-handoff.json'], capture_output=True).stdout)
        if observed != marker:
            raise ValueError('immutable CI handoff identity changed')
    else:
        handoff_path = results / 'ci-handoff.json'
        handoff_path.write_text(json.dumps(marker, sort_keys=True) + '\n')
        upload(handoff_path, task + '/input/ci-handoff.json')
        inventory = json.loads(python("source_cache.py", "inventory", cache, "--commit", upstream, capture_output=True).stdout)
        if upstream in inventory["cache_shas"]:
            print(f"CACHE_HIT {upstream}: zero source upload", flush=True)
        else:
            repository = ("https://github.com/deepmodeling/abacus-develop.git"
                          if software == "abacus" else "https://github.com/cp2k/cp2k.git")
            if software == "gpumd":
                repository = "https://github.com/brucefan1983/GPUMD.git"
            if software == "qe":
                repository = "https://github.com/QEF/q-e.git"
            ensure_source(remote=remote, options=options, port=12022, controller=control,
                          cache=cache, commit=upstream, repository_url=repository,
                          temporary=temporary, destination=task + '/input')
        # Fail before scheduling if the manually provisioned minimal base is missing.
        ssh(["test", "-s", f"{root}/containers/base/minimal-v1.sif"])
        if software == "abacus":
            from abacus_dependencies import cache_archives, load_lock
            updates = temporary / "abacus-updates"
            cache_archives(updates)
            stage_archives(python, upload, root, f'{task}/input/abacus-updates',
                           [(updates / item['file'], item['sha256'])
                            for item in load_lock()['archives'] if 'url' in item])
        if software == "qe":
            from qe_dependencies import cache_archives
            archives = cache_archives(temporary / "qe-dependencies", upstream)
            stage_archives(python, upload, root, f'{task}/input/qe-dependencies', archives)
        if software == "cp2k":
            import cp2k_spack
            archives = cp2k_spack.cache_source_updates(temporary / 'cp2k-updates')
            pins = {getattr(cp2k_spack, item + '_ARCHIVE'): getattr(cp2k_spack, item + '_SHA256')
                    for item in ('WIGNERNJ', 'TBLITE_RELEASE', 'TBLITE_DEVELOPMENT', 'ELPA', 'LIBXC', 'LIBXS')}
            stage_archives(python, upload, root, f'{task}/input/cp2k-updates',
                           [(archive, pins[archive.name]) for archive in archives])
    relay.set("prepared", True)
    def emit_run_id():
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
                stream.write(f'run_id={run_id}\n')
    resume = os.environ.get("RESUME_RUN", "")
    extras = ["--resume-run", safe_name(resume)] if resume else []
    if software == 'qe':
        extras += ['--minutes', '120', '--overlay-mb', '16384']
    if software == 'gpumd':
        # Project builds take 2-3 minutes with the qualified image driver.
        # A four-hour request prevented backfill on the busy 4V100 partition.
        extras += ['--minutes', '30']
    dependency_run = safe_name(run_id + '-dependencies')
    shared_dependencies = False
    try:
        if software == 'cp2k' and phase != 'acceptance' and not resume:
            if relay.enabled and 'shared_dependencies' in relay.data:
                shared_dependencies = relay.data['shared_dependencies']
            else:
                shared_dependencies = json.loads(python('cp2k_site_spack.py', 'available', identity['partition'],
                                                       capture_output=True).stdout)['available']
                relay.set('shared_dependencies', shared_dependencies)
            if shared_dependencies:
                print('CP2K_SHARED_DEVTOOLS: skipping the Spack build allocation', flush=True)
            elif phase != 'application':
                # The verified CPU dependency pilot took 5h45m; GPU QOS is capped at 4h.
                resources = ['--overlay-mb', '16384']
                if target == 'dsprhbm':
                    resources += ['--minutes', '720']
                relay.submitted(ssh, f'{root}/runs/{dependency_run}', dict(identity=identity, controller=control),
                    lambda: python('cp2k_dependency_stage.py', 'submit', root, control, dependency_run,
                                   json.dumps(identity, sort_keys=True), *resources))
                observation = '21000' if target == 'dsprhbm' else '19800'
                python('cp2k_dependency_stage.py', 'monitor', root, dependency_run, '--timeout', observation)
            if phase == 'dependencies':
                emit_run_id()
                return
            extras += ['--overlay-mb', '16384']
            if not shared_dependencies:
                extras += ['--dependencies-run', dependency_run]
        elif phase == 'dependencies':
            # A user-requested repack consumes its original checkpoint in the next job.
            emit_run_id()
            return
        if phase != 'acceptance':
            expected_build = dict(run_id=run_id, identity=identity, controller=control,
                                  software=software, sha=upstream, version=version, target=target,
                                  track=track, source_ref=source_ref, recipe_sha256=identity['recipe_sha256'],
                                  resume_run=resume or None,
                                  dependencies_run=dependency_run if software == 'cp2k' and not resume and not shared_dependencies else None)
            submitted = (continuing or relay.enabled) and existing_job(ssh, root, control, task, expected_build, 'software_controller.py')
            if not submitted:
                python("software_controller.py", "submit", software, run_id, upstream, version, target,
                       *provenance_flags, *extras)
            python("software_controller.py", "monitor", run_id, "--timeout", "19800")
            if phase in ('build', 'application'):
                emit_run_id()
                return
        def acceptance_exists(script, acceptance_run, **extra):
            expected = dict(run_id=acceptance_run, version=version, target=target,
                            identity=identity, artifact=str(expected_artifact), **extra)
            if script != 'abacus_benchmark.py':
                expected['build_run_id'] = run_id
            directory_name = ('benchmark-' if script == 'abacus_benchmark.py' else '') + acceptance_run
            return existing_job(ssh, root, control, f'{root}/runtime-tests/{directory_name}', expected, script)

        if software == "abacus" and target in ("dsprhbm", "4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
            def allow_mapping_retry(runtime_run):
                expected = dict(run_id=runtime_run, build_run_id=run_id, version=version,
                                target=target, identity=identity, artifact=str(expected_artifact))
                return multinode_retry_allowed(ssh, root, control, f'{root}/runtime-tests/{runtime_run}', expected)
            accept_multinode(python, acceptance_exists, allow_mapping_retry, run_id, version, target)
        if software == "abacus" and target in ("4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
            feature_run = safe_name(run_id + "-gpu-features")
            if not acceptance_exists('gpu_feature_controller.py', feature_run):
                python("gpu_feature_controller.py", "submit", feature_run, version, target,
                       "--build-run-id", run_id)
            python("gpu_feature_controller.py", "monitor", feature_run)
        for repeat_suffix in ('', '-retest'):
            try:
                if software == "abacus":
                    artifact = str(expected_artifact)
                    for case in ("pw", "hse", "deepks"):
                        benchmark_run = safe_name(run_id + "-" + case + repeat_suffix)
                        if not acceptance_exists('abacus_benchmark.py', benchmark_run, packaged_case=case,
                                                 system_module='abacus/v3.9.0.26-sm70-auto'):
                            python("abacus_benchmark.py", "prepare", benchmark_run, version, target,
                                   "--artifact", artifact, "--launcher", f"{control}/abacus",
                                   "--system-module", "abacus/v3.9.0.26-sm70-auto", "--packaged-case", case,
                                   "--allow-cpu-case-on-gpu",
                                   # HSE runs both versions eight times; the CPU allocation
                                   # reached 20m02s on 2026-11-10, including normal cleanup.
                                   *(['--minutes', '45' if case == 'hse' else '20']
                                     if target == 'dsprhbm' else []))
                            python("abacus_benchmark.py", "submit", benchmark_run)
                        python("abacus_benchmark.py", "monitor", benchmark_run)
                if software == "cp2k":
                    scientific_run = safe_name(run_id + "-science")
                    if not acceptance_exists('cp2k_acceptance.py', scientific_run):
                        python("cp2k_acceptance.py", "submit", scientific_run, version, target,
                               "--build-run-id", run_id)
                    python("cp2k_acceptance.py", "monitor", scientific_run)
                if software == "qe":
                    scientific_run = safe_name(run_id + "-science")
                    if not acceptance_exists('qe_acceptance.py', scientific_run):
                        python("qe_acceptance.py", "submit", scientific_run, version, target,
                               "--build-run-id", run_id)
                    python("qe_acceptance.py", "monitor", scientific_run)
                if software == "gpumd":
                    scientific_run = safe_name(run_id + "-science" + repeat_suffix)
                    if not acceptance_exists('gpumd_acceptance.py', scientific_run):
                        python("gpumd_acceptance.py", "submit", scientific_run, version, target,
                               "--build-run-id", run_id)
                    python("gpumd_acceptance.py", "monitor", scientific_run)
                python("software_controller.py", "publish", run_id)
                break
            except subprocess.CalledProcessError as error:
                if software == 'cp2k' or error.returncode != 3 or repeat_suffix:
                    raise
                print('PERFORMANCE_RETEST: repeating on the same artifact before publication', flush=True)
        run(["scp", "-q", *options, "-P", "12022", f"{remote}:{task}/artifact.path", results / "artifact.path"])
        if (results / "artifact.path").read_text().strip() != str(expected_artifact):
            raise ValueError("published artifact differs from the delivery identity layout")
        print((results / "artifact.path").read_text(), flush=True)
    finally:
        if relay.enabled:
            (results / 'remote-evidence.json').write_text(json.dumps(dict(
                build=task, acceptance=f'{root}/runtime-tests', run_id=run_id,
                controller=control, identity=identity), indent=2) + '\n')
        else:
            # Only logs/metadata travel back; the single SIF stays in the SAI catalog.
            subprocess.run(["scp", "-q", *options, "-P", "12022", "-r",
                            f"{remote}:{task}/results/.", str(results)], check=False)
            if software == 'cp2k':
                dependency_results = results / 'dependencies'
                dependency_results.mkdir(exist_ok=True)
                subprocess.run(['scp', '-q', *options, '-P', '12022', '-r',
                                f'{remote}:{root}/runs/{dependency_run}/results/.',
                                str(dependency_results)], check=False)
            if software == "qe":
                scientific_results = results / "qe-science"
                scientific_results.mkdir(exist_ok=True)
                scientific_task = f"{root}/runtime-tests/{run_id}-science"
                for name in ("results", "request.json", "job.sbatch", "job.id"):
                    subprocess.run(["scp", "-q", *options, "-P", "12022", "-r",
                                    f"{remote}:{scientific_task}/{name}", str(scientific_results)], check=False)
            for suffix in ("", "-retest"):
                if software == "cp2k" and not suffix:
                    scientific_results = results / "cp2k-science"
                    scientific_results.mkdir(exist_ok=True)
                    subprocess.run(["scp", "-q", *options, "-P", "12022", "-r",
                                    f"{remote}:{root}/runtime-tests/{run_id}-science/results/.",
                                    str(scientific_results)], check=False)
                    # Preserve raw scientific inputs/logs/forces as well as summaries;
                    # never transfer SIFs or build/install trees back to the runner.
                    scientific_task = f"{root}/runtime-tests/{run_id}-science"
                    for name in ("cases", "fixtures", "request.json", "job.sbatch", "job.id", "rank-exec.sh"):
                        subprocess.run(["scp", "-q", *options, "-P", "12022", "-r",
                                        f"{remote}:{scientific_task}/{name}", str(scientific_results)], check=False)
                if software == "gpumd":
                    scientific_results = results / ("gpumd-science" + suffix)
                    scientific_results.mkdir(exist_ok=True)
                    scientific_run = safe_name(run_id + "-science" + suffix)
                    subprocess.run(["scp", "-q", *options, "-P", "12022", "-r",
                                    f"{remote}:{root}/runtime-tests/{scientific_run}/results/.",
                                    str(scientific_results)], check=False)
            if software == "abacus" and target in ("dsprhbm", "4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
                for suffix in ('', '-retry1'):
                    runtime_results = results / ('runtime' + suffix)
                    runtime_results.mkdir(exist_ok=True)
                    runtime_run = safe_name(run_id + '-multinode' + suffix)
                    subprocess.run(["scp", "-q", *options, "-P", "12022", "-r",
                                    f"{remote}:{root}/runtime-tests/{runtime_run}/results/.",
                                    str(runtime_results)], check=False)
            if software == "abacus" and target in ("4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
                feature_results = results / "gpu-features"
                feature_results.mkdir(exist_ok=True)
                feature_run = safe_name(run_id + "-gpu-features")
                subprocess.run(["scp", "-q", *options, "-P", "12022", "-r",
                                f"{remote}:{root}/runtime-tests/{feature_run}/results/.",
                                str(feature_results)], check=False)
            for suffix in ("", "-retest"):
                if software == "abacus":
                    for case in ("pw", "hse", "deepks"):
                        benchmark_results = results / f"benchmark-{case}{suffix}"
                        benchmark_results.mkdir(exist_ok=True)
                        benchmark_run = safe_name("benchmark-" + run_id + "-" + case + suffix)
                        subprocess.run(["scp", "-q", *options, "-P", "12022", "-r",
                                        f"{remote}:{root}/runtime-tests/{benchmark_run}/results/.",
                                        str(benchmark_results)], check=False)

if __name__ == "__main__":
    entry(main)
