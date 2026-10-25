#!/usr/bin/env python3
"""Trusted host controller. External code only runs behind the container policy."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
from remote_controller import TARGETS, safe_name, safe_sha, container_command, site_app_binds, cp2k_mpi_build_binds, project_batch_directives, private_scratch, gpumd_qos
from source_cache import checksum
from module_publication import publish_module, validate_module
from release_contract import make_identity, TRACKS
from delivery_layout import CONTRACT_SCHEMA, artifact_path, catalog_dir, validate_record

ROOT = Path(os.environ.get("SAI_SOFTWARE_ROOT", Path.home() / "sai-hpc-software")).resolve()
CONTROL = Path(__file__).resolve().parent

def contract_files(software):
    common = ["software_controller.py", "remote_controller.py", "source_cache.py", "module_publication.py", "create_rootfs.sh", "environment.sh",
              "release_contract.py", "resolve_source.py", "delivery_layout.py"]
    if software == "abacus":
        return common + ["container_entry.sh", "abacus_build.sh", "runtime_controller.py",
                         "gpu_feature_controller.py", "gpu_feature_runtime.sh", "abacus",
                         "abacus_dependencies.py", "abacus_dependencies.sh",
                         "abacus_full_profile.json", "abacus_full_dependencies.sh", "abacus_site_mpi.sh", "abacus_site_gpu.sh",
                         "abacus_dependency_lock.json", "abacus_features.py", "abacus_benchmark.py",
                         "native_module.py", "export_native.py"]
    if software == "cp2k":
        return common + ["cp2k_container_entry.sh", "cp2k_build.sh", "cp2k_dependencies.sh", "cp2k_libxc.sh", "cp2k_libxs.sh", "cp2k_elpa.sh", "cp2k_elpa_runtime.py", "cp2k_feature_contract.py", "cp2k_site_elpa.json", "cp2k_elpa_abi.f90",
                         "cp2k_Libint2Config.cmake", "cp2k_libxsmmConfig.cmake", "cp2k_cmake_prerequisites.cmake", "cp2k_benchmark.py", "cp2k_acceptance.py", "cp2k_source_fixes.py",
                         "cp2k_regtest.py", "cp2k_regtest_suite.py",
                         "cp2k_spack.py", "cp2k_spack_environment.py", "cp2k_spack_native.py",
                         "cp2k_spack_seed.py", "cp2k_spack_probe.sh", "cp2k_spack_dependencies.sh",
                         "cp2k_spack_libint_repair.py", "cp2k_spack_install_evidence.py",
                         "cp2k_spack_link.py", "cp2k_payload.py", "cp2k_dependency_stage.py", "cp2k_overlay_workdir.py", "cp2k_image_driver.py", "build_capacity.py",
                         "native_module.py", "export_native.py", "cp2k"]
    if software == "gpumd":
        return [name for name in common if name != "environment.sh"] + [
            "gpumd_environment.sh", "gpumd_container_entry.sh", "gpumd_build.sh",
            "gpumd_science.py", "gpumd_deepmd_probe.py", "gpumd_acceptance.py",
            "export_native.py", "native_module.py", "gpumd", "nep", "gnep"]
    raise ValueError("unknown software contract")

def recipe_fingerprint(software, control=None):
    """Hash actual deployed code, not a Git label or an upstream source SHA."""
    control = Path(control or CONTROL)
    digest = hashlib.sha256(f"sai-contract-{CONTRACT_SCHEMA}\n".encode())
    for name in contract_files(software):
        path = control / name
        if name in ("abacus", "cp2k", "gpumd", "nep", "gnep") and not path.exists():
            path = control / f"{'gpumd' if name in ('nep', 'gnep') else name}_runtime.sh"
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing trusted contract file: {name}")
        digest.update(f"{name}\0{checksum(path)}\n".encode())
    return digest.hexdigest()

def request_identity(args):
    return make_identity(args.software, args.track, args.source_ref, args.sha,
                         args.version, recipe_fingerprint(args.software), args.target)

def identity(args):
    print(json.dumps(request_identity(args), sort_keys=True))

def required_acceptance(software, target, track=None):
    if software == "cp2k":
        if target not in ("dsprhbm", "4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
            raise ValueError("CP2K native publication target is not registered")
        from cp2k_regtest import eligible
        return ("cp2k_science", "cp2k_regtest") if eligible(track, target) else ("cp2k_science",)
    if software == "gpumd":
        if target not in ("4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
            raise ValueError("GPUMD publication requires a natively built V100 target")
        return ("gpumd_science",)
    if software != "abacus":
        raise ValueError(f"{software.upper()} publication is disabled: scientific acceptance is not registered")
    if target == "dsprhbm":
        return ("multinode_runtime", "benchmark_pw", "benchmark_hse", "benchmark_deepks")
    if target in ("4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
        return ("multinode_runtime", "gpu_features", "benchmark_pw", "benchmark_hse", "benchmark_deepks")
    raise ValueError("ABACUS publication acceptance is not registered for this target")

def atomic_manifest(artifact, manifest):
    sidecar = artifact.with_suffix(".json")
    temporary = sidecar.with_name(f".{sidecar.name}-{os.getpid()}.tmp")
    temporary.write_text(json.dumps(manifest, sort_keys=True) + "\n")
    os.replace(temporary, sidecar)

def validate_acceptance(manifest):
    """Only proofs tied to this image and the current trusted verifier count."""
    if manifest["software"] == "gpumd":
        from gpumd_acceptance import validate_manifest
        validate_manifest(manifest, ROOT, CONTROL)
        return
    for name in required_acceptance(manifest["software"], manifest["target"], manifest["identity"]["track"]):
        proof = manifest.get(name, {})
        benchmark = name.startswith("benchmark_")
        verifier = ("cp2k_regtest.py" if name == "cp2k_regtest" else
                    "cp2k_acceptance.py" if name == "cp2k_science" else
                    "abacus_benchmark.py" if benchmark else
                    "runtime_controller.py" if name == "multinode_runtime" else "gpu_feature_controller.py")
        if (proof.get("verified") is not True or
                proof.get("artifact_sha256") != manifest["sha256"] or
                proof.get("identity") != manifest["identity"] or
                proof.get("controller_sha256") != checksum(CONTROL / verifier)):
            raise ValueError(f"missing or stale {name} verification")
        task = ROOT / "runtime-tests" / safe_name(proof.get("run_id", ""))
        if task.resolve() != task:
            raise ValueError("untrusted acceptance path")
        for relative in ("request.json", "results/status.json"):
            path = task / relative
            if path.is_symlink() or path.resolve() != path:
                raise ValueError("untrusted acceptance metadata")
        request = json.loads((task / "request.json").read_text())
        status = json.loads((task / "results/status.json").read_text())
        launcher = Path(proof.get("launcher", ""))
        if (status.get("verified") is not True or status.get("state") != "COMPLETED" or
                status.get("exit_code") != "0:0" or status.get("job") != proof.get("job") or
                request.get("artifact") != manifest["artifact"] or
                request.get("artifact_sha256") != manifest["sha256"] or
                request.get("target") != manifest["target"] or
                request.get("identity") != manifest["identity"] or
                request.get("controller_sha256") != proof.get("controller_sha256") or
                request.get("launcher") != str(launcher) or
                request.get("launcher_sha256") != proof.get("launcher_sha256") or
                not launcher.is_file() or launcher.is_symlink() or
                checksum(launcher) != proof.get("launcher_sha256")):
            raise ValueError(f"invalid {name} evidence")
        if name == "multinode_runtime":
            expected_ranks = (request["nodes"] * request["ranks_per_node"] if manifest["target"] == "dsprhbm"
                              else request["nodes"] * request["gpus_per_node"])
            if proof.get("nodes") != 2 or proof.get("ranks") != expected_ranks or expected_ranks < 2:
                raise ValueError("invalid multinode topology")
        elif name == "gpu_features":
            if not all(proof.get(feature) is True for feature in ("nccl_collective", "cusolvermp_eigensolve")):
                raise ValueError("GPU feature execution was not verified")
            if proof.get("runtime_sha256") != checksum(CONTROL / "gpu_feature_runtime.sh"):
                raise ValueError("GPU feature runtime changed")
        elif benchmark:
            if (request.get("packaged_case") != name.removeprefix("benchmark_") or
                    request.get("system_module") != "abacus/v3.9.0.26-sm70-auto" or
                    request.get("warmup", 0) < 1 or request.get("repeats", 0) < 3):
                raise ValueError("benchmark did not cover the required case and baseline")
        evidence_path = task / "results/evidence.json"
        if (not evidence_path.is_file() or evidence_path.is_symlink() or
                evidence_path.resolve() != evidence_path or
                checksum(evidence_path) != proof.get("evidence_sha256")):
            raise ValueError(f"missing or changed {name} scientific evidence")
        if name == "multinode_runtime":
            from runtime_controller import verify_evidence
        elif name == "cp2k_science":
            from cp2k_acceptance import verify_evidence
        elif name == "cp2k_regtest":
            from cp2k_regtest import verify_evidence
        elif name == "gpu_features":
            from gpu_feature_controller import verify_evidence
        else:
            from abacus_benchmark import verify_evidence
        evidence = verify_evidence(task, request)
        if evidence != json.loads(evidence_path.read_text()):
            raise ValueError(f"{name} scientific results changed after verification")
        from delivery_layout import check_performance
        if benchmark:
            from abacus_benchmark import verify_retest
            verify_retest(task, request, evidence)
            timing = evidence['statistics']['rounded_iteration_seconds']
            check_performance(timing['candidate']['median'], timing['system']['median'])
        if (evidence.get("job") != proof.get("job") or
                evidence.get("artifact_sha256") != manifest["sha256"]):
            raise ValueError("acceptance evidence does not match its job or image")
        script_hash = request.get("job_script_sha256", request.get("job_sha256"))
        if proof.get("job_script_sha256", proof.get("job_sha256")) != script_hash:
            raise ValueError("acceptance script proof mismatch")

def validate_candidate(manifest, artifact, *, published=False):
    delivery = validate_record(manifest)
    if (manifest.get("contract_schema") != CONTRACT_SCHEMA or
            artifact != artifact_path(ROOT, delivery, artifact.stem) or
            manifest.get("build_verified") is not True or
            manifest.get("artifact") != str(artifact) or
            not artifact.is_file() or artifact.is_symlink() or artifact.resolve() != artifact or
            checksum(artifact) != manifest.get("sha256") or
            manifest.get("recipe_sha256") != recipe_fingerprint(manifest["software"])):
        raise ValueError("invalid or stale build contract")
    build = task_dir(artifact.stem)
    request = json.loads((build / "request.json").read_text())
    if request.get("contract_schema") != CONTRACT_SCHEMA or validate_record(request) != delivery:
        raise ValueError("build request and artifact identity differ")
    status = json.loads((build / "results/status.json").read_text())
    job = (build / "job.id").read_text().strip()
    if (not job.isdigit() or status.get("job") != job or
            status.get("state") != "COMPLETED" or status.get("exit_code") != "0:0" or
            status.get("verified") is not True or
            request.get("recipe_sha256") != manifest["recipe_sha256"] or
            request.get("sha") != manifest["source_sha"] or
            any(request.get(k) != manifest.get(k) for k in ("software", "version", "target")) or
            (build / "artifact.path").read_text().strip() != str(artifact)):
        raise ValueError("build status or provenance is invalid")
    if published and (manifest.get("verified") is not True or manifest.get("published") is not True):
        raise ValueError("artifact has not passed the publication gate")
    validate_acceptance(manifest)
    if published:
        from export_native import validate_export_record
        validate_export_record(manifest.get('native_export'), manifest['sha256'], [delivery])
        validate_module(manifest, artifact, root=ROOT)

def call(argv, **kw):
    return subprocess.run([str(x) for x in argv], check=True, text=True, **kw)

def task_dir(run_id):
    safe_name(run_id)
    path = ROOT / "runs" / run_id
    if path.resolve() != path:
        raise ValueError("run path must not be a symlink")
    return path

def init(args):
    safe_name(args.software)
    r = task_dir(args.run_id)
    for part in ("input", "results"):
        (r / part).mkdir(parents=True, exist_ok=True)
    for part in ("runtime", "apptainer-cache"):
        private_scratch(r / part)
    return r


def publish_runtime_entry(request, artifact, manifest):
    """Serialize a target's current image/module pointer updates."""
    if artifact.parent.resolve() != artifact.parent:
        raise ValueError('untrusted publication target directory')
    lock_path = artifact.parent / '.publication.lock'
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _publish_runtime_entry(request, artifact, manifest)


def _publish_runtime_entry(request, artifact, manifest):
    """Atomically expose a verified image and its trusted host launcher."""
    validate_candidate(manifest, artifact)
    # Older ABACUS unit fixtures predate the explicit software field.
    software = request.get("software", "abacus")
    if software == "abacus":
        launcher_name = "abacus"
        module_name = "abacus"
        description = "ABACUS"
        module_lines = [
            "module load openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto",
        ]
    elif software == "cp2k":
        launcher_name = "cp2k"
        module_name = "cp2k"
        description = "CP2K"
        module_lines = [
            "module load fftw/3.3.10 saiblas/2603-gnu-auto",
            "module load openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto",
        ]
        if request.get("target") != "dsprhbm":
            module_lines.insert(1, "module load cuda/12.9.1 nvmplibs/26.7-tmp")
    elif software == "gpumd":
        launcher_name = module_name = "gpumd"
        description = "GPUMD"
        module_lines = []
    else:
        raise ValueError("no trusted runtime publisher for this software")
    launcher = Path(request["controller"]) / launcher_name
    if (not launcher.is_file() or launcher.is_symlink() or launcher.resolve() != launcher or
            not launcher.is_relative_to(ROOT / 'controller')):
        raise ValueError("trusted runtime launcher is missing")
    expected_launcher = CONTROL / launcher_name
    if not expected_launcher.exists():
        expected_launcher = CONTROL / f"{launcher_name}_runtime.sh"
    if (checksum(launcher) != checksum(expected_launcher) or
            recipe_fingerprint(software, request["controller"]) != manifest["recipe_sha256"]):
        raise ValueError("publication launcher does not match the build contract")
    for proof_name in required_acceptance(software, request["target"], manifest["identity"]["track"]):
        if proof_name == 'cp2k_regtest':
            continue  # The in-container harness has its own launcher; science tests the user launcher.
        if manifest[proof_name].get("launcher_sha256") != checksum(launcher):
            raise ValueError("publication launcher was not runtime-tested")
    from export_native import export_delivery
    manifest['native_export'] = export_delivery(artifact, manifest['sha256'],
                                                artifact.with_suffix('.native'), [validate_record(manifest)])
    manifest.update(publish_module(ROOT, request, artifact, launcher, module_lines, description))
    target_dir = artifact.parent
    current_tmp = target_dir / f".current-{os.getpid()}.sif"
    current = target_dir / "current.sif"
    current_tmp.symlink_to(artifact.name)
    os.replace(current_tmp, current)

    manifest["runtime_launcher"] = str(launcher)
    manifest["runtime_launcher_sha256"] = checksum(launcher)

def publish(args):
    r = task_dir(args.run_id)
    request = json.loads((r / "request.json").read_text())
    artifact = artifact_path(ROOT, validate_record(request), args.run_id)
    if (r / "artifact.path").read_text().strip() != str(artifact):
        raise ValueError("unexpected candidate path")
    manifest = json.loads(artifact.with_suffix(".json").read_text())
    if (manifest.get("source_sha") != request["sha"] or
            manifest.get("recipe_sha256") != request.get("recipe_sha256")):
        raise ValueError("candidate provenance does not match build request")
    publish_runtime_entry(request, artifact, manifest)
    manifest.update(verified=True, published=True)
    atomic_manifest(artifact, manifest)
    print(f"ACCEPTED_ARTIFACT_PUBLISHED {artifact}", flush=True)

def render_job(args):
    r = task_dir(args.run_id)
    target = TARGETS[args.target]
    sha = safe_sha(args.sha)
    delivery = request_identity(args)
    if getattr(args, "identity", delivery) != delivery:
        raise ValueError("requested identity differs from deployed recipe")
    if args.software not in ("abacus", "cp2k", "gpumd"):
        raise ValueError("no trusted recipe registered for this software")
    if not 1 <= args.jobs <= 16 or not 1 <= args.minutes <= 360:
        raise ValueError("resource request outside controller bounds")
    if not 256 <= args.overlay_mb <= 32768:
        raise ValueError("overlay size outside policy bounds")
    image = ROOT / "containers/base/minimal-v1.sif"
    repo = ROOT / "cache/repositories" / args.software
    overlay = r / "work.ext3"
    squash = r / "final.squashfs"
    sif = r / "result.sif"
    artifact = artifact_path(ROOT, delivery, args.run_id)
    # Host-provided, read-only interpreter; not a binary writable by a prior build.
    entrypoint = "container_entry.sh" if args.software == "abacus" else f"{args.software}_container_entry.sh"
    argv = ["/usr/bin/bash", f"/control/{entrypoint}", "build", args.software, sha, args.version, args.target,
            json.dumps(delivery, sort_keys=True, separators=(",", ":"))]
    extra_binds = ((ROOT / "cache/spack", "/input/spack"),
                   (r / "input/cp2k-updates", "/input/updates"),
                   ("/var/lib/dpkg", "/var/lib/dpkg"),
                   ("/etc/os-release", "/etc/os-release"),
                   ("/etc/alternatives", "/etc/alternatives"),
                   ("/usr/bin", "/bin"),
                   (ROOT / "cache/cp2k-dependencies", "/input/dependencies"),
                   (ROOT / "cache/cp2k-probe", "/input/probe")) if args.software == "cp2k" else ()
    if args.software == "abacus":
        from abacus_dependencies import dependency_binds, runtime_binds
        extra_binds = (*dependency_binds(ROOT), *runtime_binds())
    elif args.software == "gpumd":
        extra_binds = site_app_binds("gpumd")
    def container(phase, final=False):
        cmd = argv.copy()
        cmd[2] = phase
        if args.software == "cp2k":
            cmd = ["/usr/bin/env", f"SAI_BUILD_PARTITION={target['partition']}", *cmd]
        # Final artifacts must not rely on source/dependency staging trees.
        binds = site_app_binds("gpumd") if final and args.software == "gpumd" else () if final else extra_binds
        source_repo = None if final else repo
        if args.software == 'cp2k' and phase != 'build':
            # Metadata/export consume installed files in the overlay. A repack
            # run need not recreate the original source-update/cache mounts.
            binds = tuple((source, destination) for source, destination in binds
                          if not str(destination).startswith('/input/'))
            source_repo = None
        if args.software == "cp2k" and phase == "build" and not final:
            binds = (*binds, *cp2k_mpi_build_binds())
        command = container_command(sif if final else image, cmd, overlay=None if final else overlay,
                                 control=CONTROL, repository=source_repo,
                                 jobs=args.jobs, gpu=bool(target["gpus"]), extra_binds=runtime_binds() if final and args.software == "abacus" else binds,
                                 runtime=r / "runtime" if final and args.software == "cp2k" else None)
        if args.software == 'abacus':
            # The public ELPA selector needs the allocation partition even
            # though the build container deliberately uses --cleanenv.
            command[2:2] = ['--env', 'SLURM_JOB_PARTITION=' + target['partition']]
        if args.software == 'cp2k' and not final:
            command[2:2] = ['--bind', str(r / 'results') + ':/results:rw']
            from cp2k_image_driver import wrap_command
            command = wrap_command(ROOT, r, CONTROL, command)
        return command
    emit = container_command(image, ["/usr/bin/cat", "/workspace/final.squashfs"],
                             overlay=str(overlay) + ":ro", jobs=args.jobs)
    if args.software == 'cp2k':
        from cp2k_image_driver import wrap_command
        emit = wrap_command(ROOT, r, CONTROL, emit)
    q = shlex.quote
    lines = [
        "#!/bin/bash",
        "#SBATCH --job-name=" + ('sai-' + args.software + '-build-' + args.target + '-' + delivery['recipe_sha256'][:12]
                                if args.software in ('abacus', 'cp2k', 'gpumd') else 'sai-software-build'),
        "#SBATCH --dependency=singleton",
        f"#SBATCH --partition={target['partition']}",
        f"#SBATCH --qos={gpumd_qos(ROOT, args.target) if args.software == 'gpumd' else target['qos']}",
        "#SBATCH --nodes=1", "#SBATCH --ntasks=1",
        f"#SBATCH --time={args.minutes}",
        f"#SBATCH --output={r}/results/slurm-%j.log",
        "#SBATCH --export=NIL",
        *project_batch_directives(ROOT, r),
    ]
    if target["gpus"]:
        lines += [f"#SBATCH --gpus-per-node={target['gpus']}"]
    else:
        # SAI GPU partitions assign CPU/memory from GPU count and prohibit
        # overriding these resources. DSPRHBM accepts explicit CPU/memory.
        lines += [f"#SBATCH --cpus-per-task={args.jobs}", "#SBATCH --mem=28G"]
    resume = getattr(args, "resume_run", None)
    prepare = [shlex.join(["apptainer", "overlay", "create", "--fakeroot", "--sparse",
                          "--size", str(args.overlay_mb), str(overlay)]),
               shlex.join(container("build"))]
    dependency_run = getattr(args, 'dependencies_run', None)
    if dependency_run:
        if args.software != 'cp2k' or resume:
            raise ValueError('dependency checkpoint cannot be combined with another recipe or repack')
        prepare = [shlex.join(['/usr/bin/python3', str(CONTROL / 'cp2k_dependency_stage.py'),
                              'clone', str(ROOT), str(CONTROL), safe_name(dependency_run), args.run_id,
                              json.dumps(delivery, sort_keys=True)]), shlex.join(container('build'))]
    if resume:
        old_overlay = task_dir(resume) / "work.ext3"
        digest = getattr(args, 'resume_overlay_sha256', '')
        if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('repack requires the recorded checkpoint checksum')
        prepare = [shlex.join(['cp', '--sparse=always', '--reflink=auto', '--', str(old_overlay), str(overlay)]),
                   shlex.join(['printf', '%s  %s\\n', digest, str(overlay)]) + ' | sha256sum --check --status',
                   shlex.join(['chmod', '600', str(overlay)]), shlex.join(container('metadata'))]
    lines += [
        "set -eo pipefail",
        "export PATH=/usr/bin:/bin",
        'export LD_LIBRARY_PATH="" LD_PRELOAD=""',
        "source /etc/profile.d/lmod.sh",
        # CP2K selects its pinned driver through an explicit --config below.
        # Other writable-image builds use the qualified public driver module.
        ("module load apptainer/1.4.4" if args.software == "cp2k" else
         "module --ignore_cache load apptainer/1.4.4 fuse2fs/1.47.4-sai"),
        "set -u", "umask 077",
        f"export TMPDIR={q(str(r / 'runtime'))} APPTAINER_TMPDIR={q(str(r / 'runtime'))}",
        f"export APPTAINER_CACHEDIR={q(str(r / 'apptainer-cache'))}",
        "unset APPTAINER_BIND APPTAINER_BINDPATH SINGULARITY_BIND SINGULARITY_BINDPATH",
        *([f"source /opt/sai_config/mps_mapping.d/{target['partition']}.bash",
           'export APPTAINERENV_CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:?}"']
          if args.software == "gpumd" else []),
        f"test -s {q(str(image))}",
        *([shlex.join(['/usr/bin/python3', str(CONTROL / 'build_capacity.py'), '--shell-script-stdin',
                      str(ROOT), str(r), str(3 * args.overlay_mb)]) + " <<'SAI_BUILD_BODY'"]
          if args.software == 'cp2k' else []),
        f"test ! -e {q(str(overlay))}",
        shlex.join(["/usr/bin/python3", "-c",
                    f"import sys; sys.path.insert(0, {str(CONTROL)!r}); "
                    f"from remote_controller import require_capacity; require_capacity({str(ROOT)!r}, {args.overlay_mb})"]),
        *prepare,
        shlex.join(container("export")),
        shlex.join(emit) + " > " + q(str(squash)),
        shlex.join(["apptainer", "sif", "new", str(sif)]),
        shlex.join(["apptainer", "sif", "add", "--datatype", "4", "--partfs", "1",
                    "--parttype", "2", "--partarch", "2", "--groupid", "1", str(sif), str(squash)]),
        shlex.join(container("verify", final=True)),
        f"mkdir -p {q(str(artifact.parent))}",
        f"test ! -e {q(str(artifact))}",
        f"chmod 444 {q(str(sif))}",
        f"mv {q(str(sif))} {q(str(artifact))}",
        # Remove only two exact generated files, never recurse over source/image contents.
        f"rm -- {q(str(squash))} {q(str(overlay))}",
        f"printf '%s\\n' {q(str(artifact))} > {q(str(r / 'artifact.path'))}",
        f"sha256sum {q(str(artifact))} > {q(str(r / 'results/artifact.sha256'))}",
        "echo BUILD_AND_ARTIFACT_VERIFIED",
        *(['SAI_BUILD_BODY'] if args.software == 'cp2k' else []),
    ]
    return "\n".join(lines) + "\n"

def submit(args):
    # Do not spend an allocation on a target that cannot pass publication.
    required_acceptance(args.software, args.target)
    if args.jobs is None:
        args.jobs = TARGETS[args.target].get("build_jobs", 8)
    from remote_controller import require_capacity
    require_capacity(ROOT, getattr(args, "overlay_mb", 32768))
    r = init(args)
    if (r / "job.id").exists():
        raise ValueError("run already submitted; choose a fresh run id")
    repo = ROOT / "cache/repositories" / args.software
    call(["git", "--git-dir", repo, "cat-file", "-e", safe_sha(args.sha) + "^{commit}"])
    fingerprint = recipe_fingerprint(args.software)
    args.identity = request_identity(args)
    if getattr(args, 'dependencies_run', None):
        if args.software != 'cp2k' or args.resume_run:
            raise ValueError('dependency checkpoint cannot be combined with another recipe or repack')
        from cp2k_dependency_stage import validate_completed
        validate_completed(ROOT, task_dir(args.dependencies_run), args.identity, CONTROL)
    if args.resume_run:
        old = task_dir(args.resume_run)
        previous = json.loads((old / "request.json").read_text())
        if previous.get("contract_schema") != CONTRACT_SCHEMA or validate_record(previous) != args.identity:
            raise ValueError("cannot resume or relabel a different delivery identity")
        for key in ("software", "sha", "version", "target"):
            if previous[key] != getattr(args, key):
                raise ValueError(f"resume mismatch: {key}")
        if previous.get("recipe_sha256") != fingerprint:
            raise ValueError("cannot relabel an old overlay with a changed build recipe")
        old_job = (old / "job.id").read_text().strip()
        if not old_job.isdigit() or call(["squeue", "-h", "-j", old_job], capture_output=True).stdout.strip():
            raise ValueError("cannot resume an active or invalid job")
        if not (old / "work.ext3").is_file() or (old / "work.ext3").is_symlink():
            raise ValueError("missing file-backed build state")
        args.resume_overlay_sha256 = checksum(old / "work.ext3")
    if args.software == "abacus":
        from abacus_dependencies import ARCHIVE_CACHE, cache_archives
        cache_archives(ROOT / ARCHIVE_CACHE, source=r / "input/abacus-updates")
    script = r / "job.sbatch"
    script.write_text(render_job(args))
    script.chmod(0o700)
    call(["bash", "-n", script])
    result = call(["sbatch", "--parsable", script], capture_output=True, cwd=r)
    job = result.stdout.strip().split(";")[0]
    if not job.isdigit():
        raise ValueError("invalid sbatch response")
    (r / "job.id").write_text(job + "\n")
    (r / "request.json").write_text(json.dumps(dict(vars(args), controller=str(CONTROL),
        recipe_sha256=fingerprint, contract_schema=CONTRACT_SCHEMA), sort_keys=True) + "\n")
    print(job, flush=True)

def monitor(args):
    r = task_dir(args.run_id)
    job = (r / "job.id").read_text().strip()
    if not job.isdigit():
        raise ValueError("invalid job id")
    deadline = time.monotonic() + args.timeout
    previous = None
    accounting_misses = 0
    while time.monotonic() < deadline:
        queue = subprocess.run(["squeue", "-h", "-j", job, "-o", "%T|%R"],
                               check=False, text=True, capture_output=True)
        queued = queue.stdout.strip()
        if queued:
            accounting_misses = 0
            if queued != previous:
                print(f"{job}: {queued}", flush=True)
                previous = queued
        else:
            rows = call(["sacct", "-X", "-n", "-P", "-j", job,
                         "-o", "JobIDRaw,State,ExitCode"], capture_output=True).stdout.splitlines()
            values = next((row.split("|") for row in rows if row.split("|")[0] == job), None)
            if values and values[1] not in ("RUNNING", "PENDING", "COMPLETING"):
                print("|".join(values), flush=True)
                success = values[1] == "COMPLETED" and values[2] == "0:0" and (r / "artifact.path").exists()
                status = {"job": job, "state": values[1], "exit_code": values[2], "verified": False}
                (r / "results/status.json").write_text(json.dumps(status) + "\n")
                if success:
                    request = json.loads((r / "request.json").read_text())
                    if request.get("contract_schema") != CONTRACT_SCHEMA:
                        raise ValueError("legacy build cannot be relabelled as an identity delivery")
                    artifact = Path((r / "artifact.path").read_text().strip())
                    delivery = validate_record(request)
                    expected = artifact_path(ROOT, delivery, args.run_id)
                    if artifact != expected or artifact.resolve() != expected:
                        raise ValueError("unexpected published artifact path")
                    if request.get("recipe_sha256") != recipe_fingerprint(request["software"]):
                        raise ValueError("build controller changed during compilation")
                    manifest = {"software": request["software"], "source_sha": request["sha"],
                                "version": request["version"], "target": request["target"],
                                "artifact": str(artifact), "sha256": checksum(artifact),
                                "controller": request["controller"], "contract_schema": CONTRACT_SCHEMA,
                                "recipe_sha256": request["recipe_sha256"], "identity": delivery,
                                "build_verified": True, "verified": False, "published": False}
                    # A repeated monitor must not erase completed acceptance.
                    sidecar = artifact.with_suffix(".json")
                    if sidecar.is_file() and not sidecar.is_symlink():
                        prior = json.loads(sidecar.read_text())
                        if all(prior.get(k) == manifest[k] for k in ("sha256", "recipe_sha256", "source_sha", "identity")):
                            manifest = prior
                    atomic_manifest(artifact, manifest)
                    status["verified"] = True
                    (r / "results/status.json").write_text(json.dumps(status) + "\n")
                log = r / "results" / f"slurm-{job}.log"
                if log.is_file():
                    print("\n".join(log.read_text(errors="replace").splitlines()[-100:]), flush=True)
                return 0 if success else 1
            accounting_misses += 1
            if accounting_misses > 12:
                raise RuntimeError("Slurm accounting unavailable; job state is unknown")
        time.sleep(args.interval)
    # Do not cancel a job merely because a monitor timed out.
    raise TimeoutError(f"monitor deadline reached; job {job} was not cancelled")

def lookup(args):
    delivery = request_identity(args)
    directory = catalog_dir(ROOT, delivery)
    requested = safe_sha(args.sha)
    for sidecar in sorted(directory.glob("*.json"), reverse=True):
        if sidecar.is_symlink():
            continue
        try:
            data = json.loads(sidecar.read_text())
            artifact = sidecar.with_suffix(".sif")
            if (data.get("identity") != delivery or data.get("software") != args.software or data.get("version") != args.version or
                    data.get("source_sha") != requested or data.get("target") != args.target):
                continue
            validate_candidate(data, artifact, published=True)
        except (ValueError, KeyError, OSError, TypeError):
            continue
        print(json.dumps(data))
        return
    print("{}")

def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="op", required=True)
    a = sub.add_parser("init")
    a.add_argument("software"); a.add_argument("run_id")
    a = sub.add_parser("submit")
    for field in ("software", "run_id", "sha", "version"):
        a.add_argument(field)
    a.add_argument("target", choices=TARGETS)
    a.add_argument("--jobs", type=int, default=None)
    a.add_argument("--minutes", type=int, default=240)
    a.add_argument("--overlay-mb", type=int, default=8192)
    a.add_argument("--dependencies-run", help="consume a verified copy of this exact CP2K dependency checkpoint")
    a.add_argument("--resume-run", help="repack a terminated run's existing overlay; never rebuild source")
    a.add_argument("--track", choices=TRACKS, required=True)
    a.add_argument("--source-ref", required=True)
    a = sub.add_parser("monitor")
    a.add_argument("run_id"); a.add_argument("--timeout", type=int, default=14400)
    a.add_argument("--interval", type=int, default=15)
    a = sub.add_parser("lookup")
    for field in ("software", "version", "target", "sha"):
        a.add_argument(field)
    a.add_argument("--track", choices=TRACKS, required=True)
    a.add_argument("--source-ref", required=True)
    a = sub.add_parser("identity")
    for field in ("software", "sha", "version", "target"):
        a.add_argument(field)
    a.add_argument("--track", choices=TRACKS, required=True)
    a.add_argument("--source-ref", required=True)
    a = sub.add_parser("publish")
    a.add_argument("run_id")
    args = p.parse_args()
    result = {"init": init, "submit": submit, "monitor": monitor, "lookup": lookup, "publish": publish, "identity": identity}[args.op](args)
    return result if isinstance(result, int) else 0

if __name__ == "__main__":
    from delivery_layout import PerformanceRegression
    try:
        raise SystemExit(main())
    except PerformanceRegression as error:
        print(str(error), file=__import__('sys').stderr)
        raise SystemExit(3)
