#!/usr/bin/env python3
"""Read-only SAI CP2K source/installation audit; writes one new home JSON file.

Run on the login node. This does not mount images, execute installed CP2K,
submit Slurm jobs, or read users' scientific inputs. Installation settings
show supported capabilities, not how frequently users use them.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import tarfile


SOURCE_SHA = "67b5da876dd6a76b8b021d5a04d1c81ba79a4c50"
DBCSR_SHA256 = "d963cac9cb79e04d82bedf0f4febce9cd06470f84c7d5a45f74645d11f8c4875"
SOURCE_PATTERNS = {
    "CMakeLists.txt": r"CP2K_USE_EVERYTHING|CP2K_USE_SPLA|CP2K_ENABLE_(GRID|DBM|PW|ELPA)|CP2K_DBCSR_USE_CPU_ONLY|CP2K_USE_CUSOLVER_MP",
    "src/cp2k_info.F": r"__SPLA|spla_gemm_offloading",
    "src/fm/cp_fm_diag.F": r"cusolver_n_min|FM_DIAG_TYPE_DEFAULT|__CUSOLVERMP",
    "src/fm/cp_fm_elpa.F": r"Prefer GPU|__NO_OFFLOAD_ELPA|ELPA_2STAGE_REAL_NVIDIA_GPU",
    "src/grid/grid_task_list.c": r"config.backend == GRID_BACKEND_AUTO|task_list->backend = GRID_BACKEND_(GPU|CPU)",
    "src/cp_dbcsr_cp2k_link.F": r"ACC%|mm_stack_size|use_comm_thread|n_size_mnk_stacks",
    "src/input_cp2k_global.F": r"PREFERRED_DIAG_LIBRARY|PREFERRED_DGEMM_LIBRARY|ELPA_NEIGVEC_MIN|FFTW_PLAN_TYPE|FFTW_WISDOM_FILE_NAME",
    "src/offload/offload_library.c": r"chosen_device|cudaSetDevice",
    "docs/technologies/accelerators/cuda.md": r".*",
}
DBCSR_PATTERNS = {
    "CMakeLists.txt": r"V100|WITH_GPU_PARAMS|ACC_ARCH_NUMBER",
    "src/core/dbcsr_config.F": r"DBCSR_PARAMETER\((RUN_ON_GPU|ACCDRV|USE_COMM_THREAD|USE_ACC_G2G)|max_kernel_dim",
    "src/acc/libsmm_acc/CMakeLists.txt": r".*",
    "src/acc/libsmm_acc/generate_parameters.py": r"V100|P100|gpu_version|parameters_",
    "docs/guide/2-user-guide/4-gpu/index.md": r".*",
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def excerpt(raw, pattern):
    lines = raw.decode(errors="replace").splitlines()
    selected = set()
    for i, line in enumerate(lines):
        if re.search(pattern, line):
            selected.update(range(max(0, i - 1), min(len(lines), i + 3)))
    return {"sha256": digest(raw), "bytes": len(raw),
            "lines": [{"number": i + 1, "text": lines[i]} for i in sorted(selected)]}


def installed_file(path, pattern):
    return {"path": str(path), **excerpt(path.read_bytes(), pattern)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.home() / "sai-hpc-software")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to(args.root.resolve()):
        parser.error("keep the audit output under the project home")
    git = ["git", "--git-dir=" + str(args.root / "cache/repositories/cp2k")]
    sources = {name: excerpt(subprocess.check_output(git + ["show", SOURCE_SHA + ":" + name]), pattern)
               for name, pattern in SOURCE_PATTERNS.items()}
    archive = args.root / "cache/cp2k-probe/dbcsr-latest.tar.gz"
    archive_hash = digest(archive.read_bytes())
    if archive_hash != DBCSR_SHA256:
        raise ValueError("DBCSR archive differs from the build recipe")
    dbcsr = {}
    parameters = {}
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            name = "/".join(Path(member.name).parts[1:])
            if not member.isfile():
                continue
            if name in DBCSR_PATTERNS:
                dbcsr[name] = excerpt(tar.extractfile(member).read(), DBCSR_PATTERNS[name])
            if "parameters" in name and "V100" in name and name.endswith(".json"):
                raw = tar.extractfile(member).read()
                data = json.loads(raw)
                parameters[name] = {"sha256": digest(raw), "bytes": len(raw),
                                    "entries": len(data), "first_entry": data[:1] if isinstance(data, list) else list(data)[:1]}
    if set(dbcsr) != set(DBCSR_PATTERNS):
        raise ValueError("missing reviewed DBCSR source")
    installations = []
    for version in ("2024.3", "2025.1", "2025.1-avx512", "2025.1_nccl-avx512", "2026.1", "2026.1-avx2"):
        root = Path("/opt/apps/cp2k") / ("cp2k-" + version)
        files = []
        for rel, pattern in (("REVISION", r".*"), ("build_commands.txt", r".*"),
                             ("arch/local.psmp", r"DFLAGS|FCFLAGS|NVCC|^LIBS"),
                             ("arch/local_cuda.psmp", r"DFLAGS|FCFLAGS|NVCC|^LIBS"),
                             ("build/CMakeCache.txt", r"^(CP2K_|CMAKE_(C|CXX|Fortran|CUDA)_(COMPILER|FLAGS))[^:]*:")):
            path = root / rel
            if path.is_file():
                files.append(installed_file(path, pattern))
        installations.append({"version": version, "root": str(root), "present": root.is_dir(), "files": files})
    modules = [installed_file(path, r".*") for path in sorted(Path("/opt/modules/modulefiles/apps/cp2k").glob("*")) if path.is_file()]
    result = {"schema": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
              "host": socket.gethostname(), "purpose": "source-and-installation-capabilities-not-runtime-acceptance",
              "cp2k_source_sha": SOURCE_SHA, "cp2k_sources": sources,
              "dbcsr_archive_sha256": archive_hash, "dbcsr_sources": dbcsr,
              "dbcsr_v100_parameter_tables": parameters, "installations": installations,
              "modules": modules,
              "limitations": ["Installation configuration does not prove current runtime linkage or user demand frequency.",
                              "No tuning speedup is established by this source inspection."]}
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"evidence": str(args.output), "sha256": digest(args.output.read_bytes()),
                      "installation_count": len(installations), "v100_tables": parameters}))


if __name__ == "__main__":
    main()
