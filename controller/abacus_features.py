#!/usr/bin/env python3
"""Fail closed on lost ABACUS features and build-only ELF/runtime paths."""
import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
from release_contract import validate_identity
from source_cache import checksum


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
    identity = validate_identity(json.loads((prefix / "share/sai/release-identity.json").read_text()))
    if identity["software"] != "abacus" or identity["target"] != target or identity["install_prefix"] != str(prefix):
        raise ValueError("feature verification prefix/target differs from canonical identity")
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
    return {"schema": 1, "identity": identity, "target": target, "prefix": str(prefix), "features": features,
            "binary_sha256": checksum(binary), "runtime_env_sha256": checksum(prefix / "share/sai/runtime-env.sh"),
            "elf": elf, "info": info, "ldd": dependencies,
            "external_runtime_roots": ["/opt/devtools", "/usr", "/lib", "/lib64"],
            "host_tree_status": "loader paths checked; physical /opt deployment and scientific benchmark still required"}


MODULE_ROOT = Path("/opt/modules/modulefiles/devtools")
MODULES = ["openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto", "fftw/3.3.10",
           "libxc/7.0.0-auto", "saiblas/2603-gnu-auto", "elpa/2026.02.001-2603-gnu",
           "cuda/12.9.1"]
SDK = "/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3"
NCCL = "/opt/devtools/nvidia/nccl_2.29.3_cuda12.9_sai_v2.29.3-1-sai.2"


def expected_roots(identity):
    isa = identity["dependency_isa"]
    return {"MPI_HOME": f"/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-{isa}",
            "OPAL_PREFIX": f"/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-{isa}",
            "OPENBLAS_ROOT": f"/opt/devtools/saiblas/2603-gnu-{isa}",
            "LIBXC_ROOT": f"/opt/devtools/libxc/libxc-7.0.0-{isa}",
            "FFTW_ROOT": "/opt/devtools/fftw/fftw-3.3.10",
            "ELPA_ROOT": "/opt/devtools/elpa/elpa-2026.02.001-2603-gnu/nvidia",
            "CUDA_HOME": "/opt/devtools/nvidia/cuda-12.9.1",
            "NVHPC_ROOT": SDK, "NCCL_ROOT": NCCL}


def make_entry(identity, environment, loaded_modules, ldd):
    from native_module import validate_native_entry
    identity = validate_identity(identity)
    if identity["software"] != "abacus":
        raise ValueError("native ABACUS metadata requires an ABACUS identity")
    prefix = identity["install_prefix"]
    roots = expected_roots(identity)
    for key, expected in roots.items():
        if environment.get(key) != expected:
            raise ValueError(f"native dependency {key} does not match this partition: {environment.get(key)}")
    modules = MODULES + (["gcc/13.3.0"] if identity["target"] == "dsprhbm" else ["nvmplibs/26.7-tmp"])
    if not set(modules + ["nvhpc/26.3-gnu-cuda12-tuned"]) <= set(loaded_modules):
        raise ValueError("native dependencies were not actually loaded during this build")
    external = sorted(set(roots.values()) | {str(MODULE_ROOT)})
    external += (["/opt/devtools/gcc/13.3.0"] if identity["target"] == "dsprhbm" else
                 ["/opt/devtools/nvidia/mp_libs"])
    if "not found" in ldd:
        raise ValueError("native metadata cannot record unresolved runtime libraries")
    resolved = re.findall(r"=>\s+(/\S+)", ldd)
    if not resolved:
        raise ValueError("native metadata requires actual ldd dependency paths")
    for path in resolved:
        if not any(Path(path).is_relative_to(root) for root in [prefix, *external, "/usr", "/lib", "/lib64"]):
            raise ValueError(f"unrecorded native runtime dependency: {path}")
    entry = dict(identity=identity, commands={"abacus": "bin/abacus"}, external_roots=external,
                 runtime=dict(modules=modules,
                              prepend={"MODULEPATH": [str(MODULE_ROOT)], "PATH": [prefix + "/bin"],
                                       "LD_LIBRARY_PATH": [prefix + "/dependencies/libtorch/lib", prefix + "/dependencies/nep/lib"]},
                              set={"ABACUS_ROOT": prefix}))
    return validate_native_entry(entry)


def generate(prefix):
    prefix = Path(prefix)
    metadata = prefix / "share/sai"
    identity = validate_identity(json.loads((metadata / "release-identity.json").read_text()))
    if str(prefix) != identity["install_prefix"]:
        raise ValueError("native metadata must be generated at its canonical build prefix")
    # Modules are trusted site inputs, not inferred from a preinstalled ABACUS.
    loaded = (metadata / "modules.txt").read_text().splitlines()
    ldd = subprocess.check_output(["ldd", str(prefix / "bin/abacus")], text=True)
    entry = make_entry(identity, os.environ, loaded, ldd)
    module_files = entry["runtime"]["modules"] + ["nvhpc/26.3-gnu-cuda12-tuned"]
    module_hashes = {name: checksum(MODULE_ROOT / name) for name in module_files}
    evidence = dict(identity=identity, modulefile_sha256=module_hashes,
                    resolved_roots=expected_roots(identity), ldd=ldd,
                    dependency_lock_sha256=checksum(metadata / "dependency-lock.json"),
                    auto_module_status="site files checked; exact ISA roots verified during build; native Lmod load/unload on allocation pending",
                    performance_status="native scientific and same-resource performance acceptance pending")
    (metadata / "native-entry.json").write_text(json.dumps(entry, sort_keys=True, indent=2) + "\n")
    (metadata / "native-dependencies.json").write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n")
    return entry


def installed_entry(prefix, root):
    from native_module import validate_native_entry
    root, prefix = Path(root), Path(prefix)
    metadata = root / str(prefix).lstrip("/") / "share/sai"
    entry = validate_native_entry(json.loads((metadata / "native-entry.json").read_text()))
    identity = validate_identity(json.loads((metadata / "release-identity.json").read_text()))
    evidence = json.loads((metadata / "native-dependencies.json").read_text())
    if (entry["identity"] != identity or entry["identity"]["install_prefix"] != str(prefix) or
            evidence["identity"] != identity or evidence["dependency_lock_sha256"] != checksum(metadata / "dependency-lock.json")):
        raise ValueError("native entry, source identity and dependency lock differ")
    return entry


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("prefix", type=Path)
    parser.add_argument("target")
    parser.add_argument("--native-phase", choices=("metadata", "inventory", "verify"),
                        help="package native delivery using the shared module/manifest implementation")
    parser.add_argument("--lock", type=Path, default=Path(__file__).with_name("abacus_dependency_lock.json"))
    args = parser.parse_args()
    if args.native_phase:
        # Only build-time packaging needs the exporter; the installed feature
        # checker remains usable with its small, already-packaged dependencies.
        from export_native import inventory, read_installed_manifests, write_manifests
        if args.native_phase == "metadata":
            generate(args.prefix)
        else:
            root = Path("/workspace/export") if args.native_phase == "inventory" else Path("/")
            entry = installed_entry(args.prefix, root)
            if args.native_phase == "inventory":
                write_manifests([entry], root=root)
            elif read_installed_manifests([entry], root=root) != inventory([entry], root=root):
                raise ValueError("final SIF native inventory differs from its installed files")
            else:
                print("ABACUS_NATIVE_INVENTORY_VERIFIED")
        return
    print(json.dumps(verify(args.prefix, args.target, json.loads(args.lock.read_text())), sort_keys=True))


if __name__ == "__main__":
    main()
