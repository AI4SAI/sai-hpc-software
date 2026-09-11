#!/usr/bin/env python3
"""Fail closed on lost ABACUS features and build-only ELF/runtime paths."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def check_features(info, cache, dependencies, target, prefix, lock):
    """The observed union baseline is mandatory; missing optional libs are errors."""
    gpu = target != "dsprhbm"
    labels = lock["required_info"] + (lock["required_gpu_info"] if gpu else [])
    options = lock["required_options"] + (lock["required_gpu_options"] if gpu else [])
    missing = [label for label in labels
               if not re.search(rf"^{re.escape(label)}:\s+yes\b", info, re.MULTILINE)]
    config = dict(re.findall(r"^([^#/:=]+):[^=]+=([^\n]*)$", cache, re.MULTILINE))
    missing += [name for name in options if config.get(name, "").upper() not in ("ON", "1", "TRUE", "YES")]
    if not gpu and config.get("USE_CUDA", "").upper() not in ("OFF", "0", "FALSE", "NO"):
        missing.append("CPU target must disable USE_CUDA")
    if "not found" in dependencies:
        missing.append("unresolved dynamic library")
    expected_libraries = {
        "libelpa": "/opt/devtools/elpa/elpa-2026.02.001-2603-gnu/",
        "libtorch_cpu.so": str(prefix / "dependencies/libtorch/lib") + "/",
        "libnep.so": str(prefix / "dependencies/nep/lib") + "/",
    }
    if gpu:
        expected_libraries.update({"libcusolverMp.so": "/opt/devtools/nvidia/mp_libs/lib/",
                                   "libcublasmp.so": "/opt/devtools/nvidia/mp_libs/lib/",
                                   "libnccl.so": "/opt/devtools/nvidia/nccl_"})
    for library, directory in expected_libraries.items():
        if not re.search(rf"{re.escape(library)}\S*\s+=>\s+{re.escape(directory)}", dependencies):
            missing.append(f"{library} must resolve from {directory}")
    for label, minimum in (("LibRI Support", (2, 1, 1)), ("LibTorch Support", (2, 1, 2))):
        match = re.search(rf"^{label}:.*\(v(\d+)\.(\d+)\.(\d+)", info, re.MULTILINE)
        if not match or tuple(map(int, match.groups())) < minimum:
            missing.append(f"{label} version is older than {minimum}")
    if missing:
        raise ValueError("ABACUS feature parity failed: " + "; ".join(missing))
    return {"required_info": labels, "required_options": options,
            "checked_libraries": expected_libraries,
            "baseline_modules": lock["baseline_modules"],
            "scope": "compile/link presence; not a scientific validation of every optional method"}


def check_dynamic(dynamic, binary, prefix):
    """Inspect actual loader tags, not harmless source paths in debug metadata."""
    result = []
    for tag, value in re.findall(r"\((NEEDED|RPATH|RUNPATH)\).*?\[(.*?)\]", dynamic):
        result.append({"tag": tag, "value": value})
        if tag == "NEEDED":
            if "/" in value:
                raise ValueError(f"absolute or relative path DT_NEEDED in {binary}: {value}")
            continue
        for entry in value.split(":"):
            expanded = entry.replace("${ORIGIN}", str(binary.parent)).replace("$ORIGIN", str(binary.parent))
            path = Path(expanded)
            allowed = (path.is_absolute() and (path.resolve().is_relative_to(prefix) or
                       any(path.resolve().is_relative_to(base) for base in
                           ("/opt/devtools", "/usr", "/lib", "/lib64"))))
            if not entry or "$" in expanded or not allowed:
                raise ValueError(f"build-only or untrusted ELF {tag} in {binary}: {entry}")
    for interpreter in re.findall(r"Requesting program interpreter:\s*([^\]]+)\]", dynamic):
        path = Path(interpreter)
        if not path.is_absolute() or not any(path.resolve().is_relative_to(base) for base in
                                             ("/usr/lib", "/usr/lib64", "/lib", "/lib64")):
            raise ValueError(f"build-only or untrusted ELF interpreter in {binary}: {interpreter}")
        result.append({"tag": "INTERP", "value": interpreter})
    return result


def check_public_mode(path, *, executable=False, ancestor=False):
    """SIF entries are root-owned; fakeroot's access() would hide mode errors."""
    metadata = path.stat()
    required = 0o5 if stat.S_ISDIR(metadata.st_mode) or executable else 0o4
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & required != required or (not ancestor and mode & 0o022):
        raise ValueError(f"installation is not safely readable/executable by ordinary users: {path} mode={mode:o}")


def verify(prefix, target, lock):
    prefix = Path(prefix).resolve()
    binary = prefix / "bin/abacus"
    for parent in prefix.parents:
        check_public_mode(parent, ancestor=True)
    check_public_mode(prefix)
    check_public_mode(binary, executable=True)
    info = subprocess.run([binary, "--info"], check=True, text=True, capture_output=True).stdout
    dependencies = subprocess.run(["ldd", binary], check=True, text=True, capture_output=True).stdout
    cache = (prefix / "share/sai/CMakeCache.txt").read_text()
    features = check_features(info, cache, dependencies, target, prefix, lock)
    runtime_env = (prefix / "share/sai/runtime-env.sh").read_text()
    if re.search(r"/(workspace|control|input)(?:/|\b)|/home/[^\s:;]+/controller/", runtime_env):
        raise ValueError("runtime environment depends on a build-only path")
    elf = {}
    for path in sorted(prefix.rglob("*")):
        if path.is_symlink() and not path.resolve().is_relative_to(prefix):
            raise ValueError(f"installation symlink leaves the exported prefix: {path}")
        check_public_mode(path)
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            if stream.read(4) != b"\x7fELF":
                continue
        dynamic = subprocess.run(["readelf", "-d", path], check=True, text=True, capture_output=True).stdout
        dynamic += subprocess.run(["readelf", "-l", path], check=True, text=True, capture_output=True).stdout
        elf[str(path.relative_to(prefix))] = {"sha256": checksum(path),
                                              "dynamic": check_dynamic(dynamic, path, prefix)}
    return {"schema": 1, "target": target, "prefix": str(prefix), "features": features,
            "binary_sha256": checksum(binary), "runtime_env_sha256": checksum(prefix / "share/sai/runtime-env.sh"),
            "elf": elf, "info": info, "ldd": dependencies,
            "external_runtime_roots": ["/opt/devtools", "/usr", "/lib", "/lib64"],
            "host_tree_status": "loader paths checked; physical /opt deployment and scientific benchmark still required"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prefix", type=Path)
    parser.add_argument("target")
    parser.add_argument("--lock", type=Path, default=Path(__file__).with_name("abacus_dependency_lock.json"))
    args = parser.parse_args()
    print(json.dumps(verify(args.prefix, args.target, json.loads(args.lock.read_text())), sort_keys=True))


if __name__ == "__main__":
    main()
