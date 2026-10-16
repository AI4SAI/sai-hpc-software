#!/usr/bin/env python3
"""GitHub-side orchestration; remote commands always come from this repository."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import inspect
import json
import os
import re
from pathlib import Path
import shlex
import subprocess
import sys
import time
from remote_controller import safe_name, safe_sha
from delivery_layout import artifact_path
from release_contract import SOFTWARE, TRACKS, validate_identity
from source_cache import pack

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
    return ("gpumd", "nep", "gnep") if software == "gpumd" else (software,)


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


def main():
    software = os.environ.get("SOFTWARE", "abacus")
    if software not in ("abacus", "cp2k", "gpumd"):
        raise ValueError("unknown software recipe")
    phase = os.environ.get("CP2K_PHASE") or os.environ.get("CI_PHASE", "all")
    if phase not in ("all", "build", "dependencies", "application", "acceptance"):
        raise ValueError("unknown software CI phase")
    if phase in ("dependencies", "application") and software != "cp2k":
        raise ValueError("dependency handoff is only registered for CP2K")
    continuing = phase in ("application", "acceptance")
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
    if continuing:
        run_id = safe_name(os.environ['CI_RUN_ID'])
        pattern = (re.escape(run_prefix + os.environ['GITHUB_RUN_ID']) + r'-[0-9]+-'
                   + re.escape(track + '-' + target) + r'-\d{4}-\d{2}-\d{2}-' + re.escape(upstream[:12]))
        if not re.fullmatch(pattern, run_id):
            raise ValueError('CI handoff does not belong to this workflow/source/track/target')
    if software == "abacus":
        safe_name("benchmark-" + run_id + "-deepks")
    elif software == "gpumd":
        safe_name(run_id + "-science")
    temporary = Path(os.environ["RUNNER_TEMP"])
    key = temporary / "ssh/key"
    known_hosts = Path(__file__).resolve().parents[1] / ".ci/slurm/known_hosts"
    options = ["-i", str(key), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
               "-o", f"UserKnownHostsFile={known_hosts}", "-o", "ConnectTimeout=20",
               "-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=6"]
    remote = f"{user}@c0.sai.ai-4s.com"
    root = f"/home/{user}/sai-hpc-software"
    # One deployment per run: concurrent matrix uploads cannot truncate code
    # already being read by another running container.
    control = f"{root}/controller/{control_sha}/{run_id}"
    task = f"{root}/runs/{run_id}"
    cache = f"{root}/cache/repositories/{software}"
    def ssh(argv, **kw):
        return run(["ssh", *options, "-p", "12022", remote, shlex.join(argv)], **kw)
    def python(script, *args, **kw):
        return ssh(["python3", f"{control}/{script}", *args], **kw)
    def upload(source, destination):
        for attempt in range(3):
            try:
                return run(["scp", "-q", *options, "-P", "12022", source,
                            f"{remote}:{destination}"], timeout=1800)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                if attempt == 2:
                    raise
                print(f"retrying part {Path(source).name}", flush=True)
                time.sleep(3)
    if not continuing:
        ssh(["mkdir", "-p", control, f"{task}/input", f"{task}/results"])
        parent = Path(__file__).resolve().parent
        common = ["software_controller.py", "remote_controller.py", "source_cache.py", "module_publication.py",
                  "runtime_controller.py", "create_rootfs.sh", "release_contract.py", "resolve_source.py",
                  "delivery_layout.py"]
        recipe = (["container_entry.sh", "environment.sh", "abacus_build.sh",
                   "gpu_feature_controller.py", "gpu_feature_runtime.sh",
                   "abacus_dependencies.py", "abacus_dependencies.sh",
                   "abacus_full_profile.json", "abacus_full_dependencies.sh", "abacus_site_mpi.sh", "abacus_site_gpu.sh",
                   "abacus_dependency_lock.json", "abacus_features.py", "abacus_benchmark.py",
                   "native_module.py", "export_native.py"]
                  if software == "abacus" else
                  ["cp2k_container_entry.sh", "environment.sh", "cp2k_build.sh", "cp2k_dependencies.sh", "cp2k_feature_contract.py",
                   "cp2k_Libint2Config.cmake", "cp2k_libxsmmConfig.cmake", "cp2k_benchmark.py",
                   "cp2k_spack.py", "cp2k_spack_environment.py", "cp2k_spack_native.py",
                   "cp2k_spack_seed.py", "cp2k_spack_probe.sh", "cp2k_spack_dependencies.sh",
                   "cp2k_spack_libint_repair.py", "cp2k_spack_install_evidence.py",
                   "cp2k_spack_link.py", "cp2k_payload.py", "cp2k_dependency_stage.py",
                   "native_module.py", "export_native.py"])
        if software == "gpumd":
            recipe = ["gpumd_container_entry.sh", "gpumd_environment.sh", "gpumd_build.sh",
                      "gpumd_science.py", "gpumd_deepmd_probe.py", "gpumd_acceptance.py",
                      "export_native.py", "native_module.py"]
        for name in common + recipe:
            upload(parent / name, f"{control}/{name}")
        for executable in runtime_launchers(software):
            upload(parent / f"{software}_runtime.sh", f"{control}/{executable}")
            ssh(["chmod", "0555", f"{control}/{executable}"])
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
                                  *retry, capture_output=True).stdout)
        (results / "build-attempt.json").write_text(json.dumps(claim, sort_keys=True) + "\n")
        if not claim["build"]:
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
            repo = temporary / "source.git"
            repository = ("https://github.com/deepmodeling/abacus-develop.git"
                          if software == "abacus" else "https://github.com/cp2k/cp2k.git")
            if software == "gpumd":
                repository = "https://github.com/brucefan1983/GPUMD.git"
            run(["git", "clone", "--bare", repository, repo])
            run(["git", "-C", repo, "fetch", "--no-tags", "origin", upstream])
            base = None
            for candidate in inventory["cache_shas"]:
                result = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", candidate, upstream],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if result.returncode == 0 and candidate != upstream:
                    base = candidate
                    break
            parts = temporary / "parts"
            manifest = pack(repo, upstream, parts, base)
            print(f"SOURCE_TRANSFER base={base} compressed_bytes={manifest['compressed_size']} parts=8", flush=True)
            upload(parts / "manifest.json", f"{task}/input/manifest.json")
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(upload, parts / f"source.part.{i:02d}",
                                           f"{task}/input/source.part.{i:02d}") for i in range(8)]
                for future in futures:
                    future.result()
            python("source_cache.py", "receive", cache, f"{task}/input")
        # Fail before scheduling if the manually provisioned minimal base is missing.
        ssh(["test", "-s", f"{root}/containers/base/minimal-v1.sif"])
        if software == "abacus":
            from abacus_dependencies import cache_archives, load_lock
            updates = temporary / "abacus-updates"
            cache_archives(updates)
            ssh(["mkdir", "-p", f"{task}/input/abacus-updates"])
            for item in load_lock()["archives"]:
                if "url" in item:
                    upload(updates / item["file"], f"{task}/input/abacus-updates/{item['file']}")
        if software == "cp2k":
            from cp2k_spack import cache_source_updates
            archive = cache_source_updates(temporary / 'cp2k-updates')
            ssh(['mkdir', '-p', f'{task}/input/cp2k-updates'])
            upload(archive, f'{task}/input/cp2k-updates/{archive.name}')
    def emit_run_id():
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
                stream.write(f'run_id={run_id}\n')
    resume = os.environ.get("RESUME_RUN", "")
    extras = ["--resume-run", safe_name(resume)] if resume else []
    dependency_run = safe_name(run_id + '-dependencies')
    try:
        if software == 'cp2k' and phase != 'acceptance' and not resume:
            if phase != 'application':
                # The verified CPU dependency pilot took 5h45m; GPU QOS is capped at 4h.
                resources = ['--minutes', '720'] if target == 'dsprhbm' else []
                python('cp2k_dependency_stage.py', 'submit', root, control, dependency_run,
                       json.dumps(identity, sort_keys=True), *resources)
                observation = '21000' if target == 'dsprhbm' else '19800'
                python('cp2k_dependency_stage.py', 'monitor', root, dependency_run, '--timeout', observation)
            if phase == 'dependencies':
                emit_run_id()
                return
            extras += ['--dependencies-run', dependency_run, '--overlay-mb', '32768']
        elif phase == 'dependencies':
            # A user-requested repack consumes its original checkpoint in the next job.
            emit_run_id()
            return
        if phase != 'acceptance':
            expected_build = dict(run_id=run_id, identity=identity, controller=control,
                                  software=software, sha=upstream, version=version, target=target,
                                  track=track, source_ref=source_ref, recipe_sha256=identity['recipe_sha256'],
                                  resume_run=resume or None,
                                  dependencies_run=dependency_run if software == 'cp2k' and not resume else None)
            submitted = continuing and existing_job(ssh, root, control, task, expected_build, 'software_controller.py')
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
            runtime_run = safe_name(run_id + "-multinode")
            if not acceptance_exists('runtime_controller.py', runtime_run):
                python("runtime_controller.py", "submit", runtime_run, version, target,
                       "--build-run-id", run_id)
            python("runtime_controller.py", "monitor", runtime_run)
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
                                   "--allow-cpu-case-on-gpu")
                            python("abacus_benchmark.py", "submit", benchmark_run)
                        python("abacus_benchmark.py", "monitor", benchmark_run)
                if software == "cp2k":
                    benchmark_run = safe_name(run_id + "-benchmark" + repeat_suffix)
                    reference = os.environ.get("CP2K_REFERENCE_RUN", "")
                    reference_args = ["--reference-run-id", safe_name(reference)] if reference and target == "dsprhbm" else []
                    expected_reference = {'reference_run_id': reference} if reference_args else {}
                    if not acceptance_exists('cp2k_benchmark.py', benchmark_run, **expected_reference):
                        python("cp2k_benchmark.py", "submit", benchmark_run, version, target,
                               "--build-run-id", run_id, *reference_args)
                    python("cp2k_benchmark.py", "monitor", benchmark_run)
                if software == "gpumd":
                    scientific_run = safe_name(run_id + "-science" + repeat_suffix)
                    if not acceptance_exists('gpumd_acceptance.py', scientific_run):
                        python("gpumd_acceptance.py", "submit", scientific_run, version, target,
                               "--build-run-id", run_id)
                    python("gpumd_acceptance.py", "monitor", scientific_run)
                python("software_controller.py", "publish", run_id)
                break
            except subprocess.CalledProcessError as error:
                if error.returncode != 3 or repeat_suffix:
                    raise
                print('PERFORMANCE_RETEST: repeating on the same artifact before publication', flush=True)
        run(["scp", "-q", *options, "-P", "12022", f"{remote}:{task}/artifact.path", results / "artifact.path"])
        if (results / "artifact.path").read_text().strip() != str(expected_artifact):
            raise ValueError("published artifact differs from the delivery identity layout")
        print((results / "artifact.path").read_text(), flush=True)
    finally:
        # Only logs/metadata travel back; the single SIF stays in the SAI catalog.
        subprocess.run(["scp", "-q", *options, "-P", "12022", "-r",
                        f"{remote}:{task}/results/.", str(results)], check=False)
        if software == 'cp2k':
            dependency_results = results / 'dependencies'
            dependency_results.mkdir(exist_ok=True)
            subprocess.run(['scp', '-q', *options, '-P', '12022', '-r',
                            f'{remote}:{root}/runs/{dependency_run}/results/.',
                            str(dependency_results)], check=False)
        for suffix in ("", "-retest"):
            if software == "cp2k":
                benchmark_results = results / ("benchmark" + suffix)
                benchmark_results.mkdir(exist_ok=True)
                subprocess.run(["scp", "-q", *options, "-P", "12022", "-r",
                                f"{remote}:{root}/runtime-tests/{run_id}-benchmark{suffix}/results/.",
                                str(benchmark_results)], check=False)
                # Preserve raw scientific inputs/logs/forces as well as summaries;
                # never transfer SIFs or build/install trees back to the runner.
                benchmark_task = f"{root}/runtime-tests/{run_id}-benchmark{suffix}"
                for name in ("cases", "fixtures", "request.json", "job.sbatch", "job.id", "rank-exec.sh"):
                    subprocess.run(["scp", "-q", *options, "-P", "12022", "-r",
                                    f"{remote}:{benchmark_task}/{name}", str(benchmark_results)], check=False)
            if software == "gpumd":
                scientific_results = results / ("gpumd-science" + suffix)
                scientific_results.mkdir(exist_ok=True)
                scientific_run = safe_name(run_id + "-science" + suffix)
                subprocess.run(["scp", "-q", *options, "-P", "12022", "-r",
                                f"{remote}:{root}/runtime-tests/{scientific_run}/results/.",
                                str(scientific_results)], check=False)
        if software == "abacus" and target in ("dsprhbm", "4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
            runtime_results = results / "runtime"
            runtime_results.mkdir(exist_ok=True)
            runtime_run = safe_name(run_id + "-multinode")
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
    main()
