"""Trusted, allowlisted container policy; no writable host work/install trees."""
import re
from pathlib import Path

TARGETS = {
    "dsprhbm": {
        "partition": "DSPRHBM", "qos": "rush-cpu", "gpus": 0,
        "cuda_arch": "", "cpu_arch": "x86-64-v4",
        "dependency_isa": "avx512",
    },
    "4v100-avx512": {
        "partition": "4V100", "qos": "flood-1o2gpu", "gpus": 1,
        "cuda_arch": "70", "cpu_arch": "znver4",
        "dependency_isa": "avx512",
    },
    "16v100-avx2": {
        "partition": "16V100", "qos": "flood-1o2gpu", "gpus": 1,
        "cuda_arch": "70", "cpu_arch": "znver3",
        "dependency_isa": "avx2",
    },
    "8v100v0-avx512": {
        "partition": "8V100V0", "qos": "flood-1o2gpu", "gpus": 1,
        "cuda_arch": "70", "cpu_arch": "skylake-avx512",
        # Gold 6146 has AVX-512 but no VNNI. Site AVX-512 dependency builds
        # require newer CPUs; the auto modules correctly select AVX2 here.
        "dependency_isa": "avx2", "build_jobs": 6,
    },
    "a100": {
        "partition": "8A100M40", "qos": "rush-1o2gpu", "gpus": 1,
        "cuda_arch": "80", "cpu_arch": "x86-64-v3",
    },
}
DEPENDENCIES = ("/usr", "/lib", "/lib64", "/opt/devtools", "/opt/modules")

def safe_name(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("invalid identifier")
    return value

def safe_sha(value):
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("expected a full Git SHA")
    return value

def layout(home, run_id):
    root = Path(home).resolve() / "sai-hpc-software"
    task = root / "runs" / safe_name(run_id)
    return {"root": root, "run": task, "cache": root / "cache",
            "controller": root / "controller", "input": task / "input",
            "results": task / "results", "runtime": task / "runtime",
            "overlay": task / "work.ext3"}

def container_command(image, command, *, overlay=None, control=None, repository=None,
                      jobs=8, gpu=False, extra_binds=(), runtime=None):
    if not Path(image).is_absolute() or not command:
        raise ValueError("absolute image path and argv required")
    args = ["apptainer", "exec", "--fakeroot", "--cleanenv", "--containall",
            "--no-home", "--no-mount", "bind-paths,home,cwd,tmp,hostfs", "--pwd", "/",
            "--net", "--network", "none"]
    if gpu:
        args += ["--nv"]
    for path in DEPENDENCIES:
        args += ["--bind", f"{path}:{path}:ro"]
    for path in extra_binds:
        if isinstance(path, (tuple, list)):
            source, destination = path
        else:
            source, destination = path, path
        source = Path(source).resolve()
        destination = str(destination)
        if any(c in str(source) for c in ":,\n") or any(c in destination for c in ":,\n"):
            raise ValueError("unsafe bind source")
        args += ["--bind", f"{source}:{destination}:ro"]
    args += ["--bind", "/etc/profile.d/lmod.sh:/etc/profile.d/lmod.sh:ro"]
    if Path("/etc/lmod").is_dir():
        args += ["--bind", "/etc/lmod:/etc/lmod:ro"]
    for source, dest in ((control, "/control"), (repository, "/input/repository")):
        if source is not None:
            source = Path(source).resolve()
            if any(c in str(source) for c in ":,\n"):
                raise ValueError("unsafe bind source")
            args += ["--bind", f"{source}:{dest}:ro"]
    if overlay:
        args += ["--overlay", str(overlay)]
    if runtime is not None:
        runtime = Path(runtime)
        if (not runtime.is_absolute() or runtime.resolve() != runtime or
                runtime.name != "runtime" or runtime.parent.parent.name != "runs" or
                any(c in str(runtime) for c in ":,\n")):
            raise ValueError("runtime bind must be an exact trusted per-run directory")
        args += ["--bind", f"{runtime}:/runtime:rw"]
    args += ["--env", f"BUILD_JOBS={int(jobs)}", "--env",
             "TMPDIR=/runtime" if runtime is not None else "TMPDIR=/workspace/tmp"]
    return args + [str(image)] + list(command)
