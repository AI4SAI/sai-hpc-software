"""Trusted, allowlisted container policy; no writable host work/install trees."""
import os
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
DEPENDENCIES = ("/usr", "/sbin", "/lib", "/lib64", "/opt/devtools", "/opt/modules")
HPCX_ROOT = "/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/comm_libs/12.9/hpcx/hpcx-2.25.1"
HCOLL_BUILD_PREFIX = "/opt/mellanox/hcoll"
SAI_PROJECT_ROOT = Path('/org/org-open-sai/proj-sai-hpc-software')
SAI_PROJECT_ACCOUNT = 'proj-sai-hpc-software'


def project_batch_directives(root, workdir):
    """Select the authorized project account for work physically in its tree."""
    root, workdir = Path(root), Path(workdir)
    if not root.is_relative_to(SAI_PROJECT_ROOT):
        return []
    if '..' in root.parts or '..' in workdir.parts or not workdir.is_relative_to(root):
        raise ValueError('Slurm work directory escapes the project root')
    return ['#SBATCH --account=' + SAI_PROJECT_ACCOUNT, '#SBATCH --chdir=' + str(workdir)]


def gpumd_qos(root, target):
    # Both the project account and the partition must allow this one-GPU QoS.
    # 16V100 disallows rush-1o2gpu; rush-gpu requires four GPUs, so keep flood.
    return ('rush-1o2gpu' if Path(root).is_relative_to(SAI_PROJECT_ROOT)
            and target in ('4v100-avx512', '8v100v0-avx512')
            else TARGETS[target]['qos'])


def private_scratch(path):
    """Keep project ACLs out of Apptainer's root-mapped overlay seed."""
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or path.is_symlink():
        raise ValueError('untrusted scratch directory')
    path.mkdir(parents=True, exist_ok=True)
    # mke2fs copies the seed's ACLs verbatim. Named project users/groups are
    # unmapped in fakeroot and can make overlay upper/work unwritable. Only
    # these private scratch directories lose inherited ACLs; results keep them.
    for name in os.listxattr(path):
        if name in ('system.posix_acl_access', 'system.posix_acl_default'):
            os.removexattr(path, name)
    path.chmod(0o700)

# Observed on SAI 2026-09-20. Never bind all of /opt/apps: it would hide
# the candidate installed at its final prefix in the overlay / SIF.
# Alias destinations (e.g. cp2k-2026.1-avx512) retain their literal host path.
SITE_APPS = {
    "abacus": (),
    "cp2k": (),
    "gpumd": ("/opt/apps/conda_env/deepmd-kit-3.2.0",
              "/opt/apps/plumed/plumed-2.10.1", "/opt/apps/gpumd/GPUMD-5.8"),
    "lammps": ("/opt/apps/conda_env/deepmd-kit-3.2.0",
               "/opt/apps/conda_env/deepmd-kit-3.1.2",
               "/opt/apps/conda_env/VeloxChem/gpu",
               "/opt/apps/plumed/plumed-2.10.1",
               "/opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-nvhpc263-ompi5010-sm70",
               "/opt/apps/lammps/lammps-4Jul2026-deepmd3.2.0-plumed2.10.1-nvhpc263-ompi5010-sm70"),
}
SITE_APPS["deepmd-kit"] = SITE_APPS["lammps"]


def site_app_binds(software):
    """Exact dependency versions only; host directories remain read-only."""
    return tuple((path, path) for path in SITE_APPS[software])


def cp2k_mpi_build_binds():
    """Build-only alias for absolute paths in the site's MPI libtool files.

    /opt/mellanox/hcoll exists on GPU nodes but is absent on DSPRHBM.
    Runtime uses the real HPCX directories through its recorded library paths;
    final SIF verification and launch must not receive this compatibility bind.
    """
    return ((HPCX_ROOT + "/hcoll", HCOLL_BUILD_PREFIX),)


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

def require_capacity(root, overlay_mb):
    """Compatibility hook: the filesystem enforces actual storage limits.

    Shared-filesystem free-space reports do not establish this user's limit.
    Do not reject submissions or reserve estimated future image growth.
    """
    return {'capacity_policy': 'filesystem-enforced'}


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
        if destination.startswith("/opt/apps") and len(Path(destination).parts) <= 4:
            raise ValueError("broad /opt/apps bind would hide final-prefix installations")
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


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Read-only site dependency bind paths")
    parser.add_argument("software", choices=tuple(SITE_APPS))
    parser.add_argument("--mpi-build-binds", action="store_true", help="CP2K build-only MPI source:destination:ro bindings")
    args = parser.parse_args()
    if args.mpi_build_binds:
        if args.software != "cp2k":
            parser.error("MPI aliases are only registered for CP2K")
        for source, destination in cp2k_mpi_build_binds():
            print(f"{source}:{destination}:ro")
    else:
        for source, destination in site_app_binds(args.software):
            print(destination)
