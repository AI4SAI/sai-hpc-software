#!/usr/bin/env python3
"""Content-pinned site dependencies; extraction is only allowed in the overlay."""
import argparse
import fcntl
import json
import os
import re
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import tempfile
from urllib.request import urlopen
import zipfile
from source_cache import checksum

LOCK = Path(__file__).with_name("abacus_dependency_lock.json")
ARCHIVE_CACHE = "cache/abacus-dependencies"
CACHE_MOUNT = "/input/abacus-updates"


def load_lock():
    lock = json.loads(LOCK.read_text())
    profile = json.loads(LOCK.with_name('abacus_full_profile.json').read_text())
    if profile['enabled']:
        lock['full_features'] = profile
        lock['archives'] += profile['archives']
        lock['required_options'] += profile['required_options']
        lock['required_info'] += profile['required_info']
    return lock


def runtime_binds():
    profile = load_lock().get('full_features')
    return ((profile['ml_root'], profile['ml_root']),) if profile else ()


def json_backend(cmake):
    """Select the declared upstream API, never probe a deprecated cache flag."""
    options = re.findall(r'^\s*option\(\s*(ENABLE_JSON|ENABLE_RAPIDJSON)\b', cmake, re.M)
    if len(options) != 1:
        raise ValueError('expected exactly one declared ABACUS JSON option')
    return {'ENABLE_JSON': 'nlohmann', 'ENABLE_RAPIDJSON': 'rapidjson'}[options[0]]


def hse_case_source(source):
    """Recognize the reviewed H2O HSE case before any expensive compilation.

    Upstream renamed 08_EXX/01_GO_S1_HSE to 08_RI/scf_hse_gamma. Do not
    substitute another hybrid case silently if upstream changes it again.
    """
    source = Path(source)
    candidates = [source / "tests" / name for name in
                  ("08_EXX/01_GO_S1_HSE", "08_RI/scf_hse_gamma")]
    available = [path for path in candidates if path.is_dir()]
    if len(available) != 1:
        raise ValueError("expected exactly one reviewed ABACUS HSE benchmark case")
    case = available[0]
    files = [case / name for name in ("INPUT", "STRU", "KPT", "result.ref")]
    files += [source / "tests/PP_ORB" / name for name in
              ("H_ONCV_PBE-1.0.upf", "O_ONCV_PBE-1.0.upf",
               "H_gga_6au_60Ry_1s.orb", "O_gga_6au_60Ry_1s1p.orb")]
    if any(not path.is_file() or not path.stat().st_size or
           not path.resolve().is_relative_to(source.resolve()) for path in files):
        raise ValueError("HSE benchmark inputs must be complete and stay in the source tree")
    config = {}
    for line in (case / "INPUT").read_text().splitlines():
        fields = re.split(r"#|//", line, maxsplit=1)[0].split()
        if len(fields) >= 2:
            if fields[0] in config:
                raise ValueError("duplicate HSE benchmark input parameter")
            config[fields[0]] = fields[1]
    required = {"calculation": "scf", "dft_functional": "hse", "basis_type": "lcao",
                "gamma_only": "1", "nspin": "1", "suffix": "autotest"}
    if any(config.get(key) != value for key, value in required.items()):
        raise ValueError("reviewed HSE benchmark must remain a spin-unpolarized Gamma SCF case")
    structure = (case / "STRU").read_text().split()
    if not all(path.name in structure for path in files[4:]):
        raise ValueError("HSE benchmark structure changed its reviewed pseudopotentials/orbitals")
    return case


def repair_dftd4_cmake(source):
    """Fix pinned 4.2.0 export templates without disabling OpenMP (CMP0054).

    Quoted variable *names* are literals with CMP0054 NEW, so the upstream
    exports skip find_dependency despite linking the imported OpenMP target.
    Validate all five guards before changing any file; unexpected sources
    require review instead of silently applying an approximate patch.
    """
    source = Path(source)
    repairs = {
        'config/template.cmake': {'OpenMP': 1},
        'subprojects/mctc-lib/config/template.cmake': {'OpenMP': 1, 'JSON': 2},
        'subprojects/multicharge/config/template.cmake': {'OpenMP': 1},
    }
    changes = []
    for relative, flags in repairs.items():
        path = source / relative
        if path.is_symlink() or path.resolve() != path:
            raise ValueError('DFT-D4 CMake template must be a regular, unaliased file')
        updated = path.read_text()
        for flag, expected in flags.items():
            before = f'AND "@PROJECT_NAME@_WITH_{flag}")'
            after = f'AND @PROJECT_NAME@_WITH_{flag})'
            if updated.count(before) != expected or after in updated:
                raise ValueError(f'unexpected DFT-D4 CMake dependency guard: {relative} {flag}')
            updated = updated.replace(before, after)
        changes.append((path, updated))
    # find_dependency also checks <package>_FOUND. The bundled custom LAPACK
    # finder creates LAPACK::LAPACK but forgets its own result variable.
    path = source / 'subprojects/multicharge/config/cmake/Findcustom-lapack.cmake'
    if path.is_symlink() or path.resolve() != path:
        raise ValueError('DFT-D4 LAPACK finder must be a regular, unaliased file')
    updated = path.read_text()
    if updated.count('find_package("LAPACK" REQUIRED)') != 1 or 'custom-lapack_FOUND' in updated:
        raise ValueError('unexpected DFT-D4 custom LAPACK finder')
    updated += ('\n# SAI: report the actual imported target to find_dependency.\n'
                'set(custom-lapack_FOUND FALSE)\n'
                'if(TARGET "LAPACK::LAPACK")\n'
                '  set(custom-lapack_FOUND TRUE)\n'
                'endif()\n')
    changes.append((path, updated))
    for path, updated in changes:
        path.write_text(updated)


def dependency_binds(root):
    """Bind only locked archive directories, never expanded host dependencies."""
    lock = load_lock()
    binds = [(Path(lock["site_archive_root"]), "/input/abacus-dependencies")]
    if any("url" in item for item in lock["archives"]):
        binds.append((Path(root) / ARCHIVE_CACHE, CACHE_MOUNT))
    return tuple(binds)


def cache_archives(cache, lock=None, *, source=None):
    """Cache pinned updates from runner downloads or uploaded, unexpanded files."""
    items = [item for item in (load_lock() if lock is None else lock)["archives"] if "url" in item]
    if not items:
        return
    cache = Path(cache)
    if not cache.is_absolute() or cache.resolve() != cache or cache.is_symlink():
        raise ValueError("dependency cache must be an absolute non-symlink directory")
    cache.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(cache / ".archives.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        for item in items:
            if Path(item["file"]).name != item["file"] or item["file"] in (".", "..", ".archives.lock"):
                raise ValueError("dependency cache filename must be a basename")
            archive = cache / item["file"]
            if archive.exists() or archive.is_symlink():
                if archive.is_symlink() or not archive.is_file() or checksum(archive) != item["sha256"]:
                    raise ValueError(f"changed cached dependency: {item['file']}")
                continue
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=cache, prefix=".download-", delete=False) as output:
                    temporary = Path(output.name)
                    if source is None:
                        incoming = urlopen(item["url"], timeout=120)
                    else:
                        uploaded = Path(source) / item["file"]
                        if not uploaded.is_file() or uploaded.is_symlink() or uploaded.resolve() != uploaded:
                            raise ValueError(f"missing regular uploaded dependency: {item['file']}")
                        incoming = uploaded.open("rb")
                    with incoming:
                        shutil.copyfileobj(incoming, output)
                if checksum(temporary) != item["sha256"]:
                    raise ValueError(f"incoming dependency checksum mismatch: {item['file']}")
                temporary.chmod(0o444)
                os.replace(temporary, archive)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)


def validate_members(files, root, required_files=()):
    """Validate member paths and required nonempty files using one archive index."""
    for name in files:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != root:
            raise ValueError("dependency archive has an unsafe or unexpected path")
    for relative in required_files:
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("required dependency file must stay inside its archive root")
        if not files.get(f"{root}/{path}", False):
            raise ValueError(f"missing nonempty regular dependency file: {root}/{path}")


def extract_archive(archive, destination, root, required_files=()):
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as stream:
            entries = stream.infolist()
            if any(stat.S_ISLNK(entry.external_attr >> 16) for entry in entries):
                raise ValueError("dependency zip links are not allowed")
            validate_members({entry.filename: not entry.is_dir() and entry.file_size > 0 and
                              stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG)
                              for entry in entries}, root, required_files)
            stream.extractall(destination)
            # zipfile deliberately does not restore Unix executable modes.
            # Torch's packaged helpers must remain usable in the exported tree.
            for entry in entries:
                permissions = (entry.external_attr >> 16) & 0o777
                if permissions:
                    (destination / entry.filename).chmod(permissions)
    else:
        with tarfile.open(archive, "r:*") as stream:
            entries = stream.getmembers()
            if any(not (entry.isfile() or entry.isdir()) for entry in entries):
                raise ValueError("dependency tar links and special files are not allowed")
            validate_members({entry.name: entry.isfile() and entry.size > 0 for entry in entries},
                             root, required_files)
            stream.extractall(destination, members=entries)


def unpack(source, destination, lock):
    source, destination = Path(source), Path(destination)
    if not destination.is_absolute() or not destination.is_relative_to("/workspace"):
        raise ValueError("dependency extraction must stay inside /workspace in the build overlay")
    if destination.exists() or destination.is_symlink() or destination.resolve() != destination:
        raise ValueError("dependency destination already exists")
    # Validate every archive before creating the expanded dependency tree.
    archives = [(item, (Path(CACHE_MOUNT) if "url" in item else source) / item["file"])
                for item in lock["archives"]]
    for item, path in archives:
        if not path.is_file() or path.is_symlink() or checksum(path) != item["sha256"]:
            raise ValueError(f"missing or changed pinned dependency: {item['file']}")
    destination.mkdir(parents=True)
    for item, archive in archives:
        extract_archive(archive, destination, item["root"], item.get("required_files", ()))
    (destination / "dependency-lock.json").write_text(json.dumps(lock, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("unpack", "json-backend", "hse-case-source", "runtime-roots", "repair-dftd4-cmake"))
    parser.add_argument("--cmake", default="/workspace/source/CMakeLists.txt")
    parser.add_argument("--source", default="/input/abacus-dependencies")
    parser.add_argument("--destination", default="/workspace/dependencies")
    args = parser.parse_args()
    if args.operation == 'repair-dftd4-cmake':
        source = Path(args.source)
        if not source.is_absolute() or not source.is_relative_to('/workspace'):
            raise ValueError('DFT-D4 repair must stay inside the build overlay')
        repair_dftd4_cmake(source)
    elif args.operation == 'json-backend':
        print(json_backend(Path(args.cmake).read_text()))
    elif args.operation == 'hse-case-source':
        print(hse_case_source(args.source))
    elif args.operation == 'runtime-roots':
        for source, destination in runtime_binds():
            if source != '/opt/apps/conda_env/deepmd-kit-3.1.2' or destination != source:
                raise ValueError('unreviewed ML runtime root')
            print(source)
    else:
        unpack(args.source, args.destination, load_lock())


if __name__ == "__main__":
    main()
