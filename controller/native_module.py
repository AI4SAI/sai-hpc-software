"""Generate native Tcl modules for immutable, canonical-prefix installations.

These modules execute the extracted installation directly. They do not invoke
Apptainer, source a shell environment, or claim arbitrary relocation. Site
dependencies stay external and are selected by exact, recorded module names.

An entry contains ``identity`` (release_contract schema 1), nonempty ``commands``
mapping command names to relative ``bin/...`` files, ``external_roots`` (explicit
site dependency prefixes), and ``runtime`` with ``modules``, ``prepend``, ``set``.
Unknown entry/runtime fields and environment settings fail closed. Other entries
in a paired delivery can authorize same-partition canonical prefixes via
allowed_prefixes. Supplying other partitions never authorizes their libraries.
Filesystem checks belong to extraction and module load, not code generation.
"""
import os
from pathlib import Path, PurePosixPath
import re
import stat

from release_contract import (MAX_BUILD_ID, SOFTWARE, TRACKS, allowed_partitions,
                              validate_identity)


_PREPEND = frozenset(("PATH", "LD_LIBRARY_PATH", "LIBRARY_PATH", "CPATH",
                      "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "PKG_CONFIG_PATH",
                      "CMAKE_PREFIX_PATH", "PYTHONPATH", "MANPATH", "MODULEPATH"))
_PATH_SETTINGS = frozenset((
    "CP2K_DATA_DIR", "CP2K_ROOT", "ABACUS_ROOT", "DEEPMD_ROOT", "DEEPMD_DIR",
    "GPUMD_ROOT", "GPUMD_SRC", "LAMMPS_ROOT", "LAMMPS_POTENTIALS", "PLUMED_ROOT", "PLUMED_KERNEL",
    "PLUMED_INCLUDEDIR", "PLUMED_HTMLDIR", "PLUMED_TCLLIBPATH", "TORCH_ROOT",
    "Torch_DIR", "TENSORFLOW_ROOT", "CUDA_HOME", "CUDA_ROOT", "CUDA_PATH",
    "CUDAToolkit_ROOT", "CUDACXX", "NCCL_ROOT", "CUSOLVERMP_ROOT", "ELPA_ROOT",
))
_THREAD_SETTINGS = frozenset((
    "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "BLIS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS", "TF_NUM_INTRAOP_THREADS", "TF_NUM_INTEROP_THREADS",
    "DP_INTRA_OP_PARALLELISM_THREADS", "DP_INTER_OP_PARALLELISM_THREADS",
))
_ENUM_SETTINGS = {
    "OMP_PROC_BIND": ("false", "true", "master", "primary", "close", "spread"),
    "OMP_PLACES": ("threads", "cores", "sockets", "ll_caches", "numa_domains"),
    "OMP_DYNAMIC": ("false", "true", "FALSE", "TRUE"),
    "MKL_DYNAMIC": ("false", "true", "FALSE", "TRUE"),
    "CUDA_DEVICE_ORDER": ("PCI_BUS_ID", "FASTEST_FIRST"),
    "CUDA_MODULE_LOADING": ("LAZY", "EAGER"),
    "CUDA_CACHE_DISABLE": ("0", "1"),
    "DP_CUDA_INFER": ("0", "1", "2", "false", "true"),
    "DP_JIT": ("0", "1"),
    "DP_INTERFACE_PREC": ("high", "low"),
    "DP_BACKEND": ("tensorflow", "pytorch", "jax", "paddle"),
    "TF_CPP_MIN_LOG_LEVEL": ("0", "1", "2", "3"),
    "TF_FORCE_GPU_ALLOW_GROWTH": ("false", "true"),
    "TF_ENABLE_ONEDNN_OPTS": ("0", "1"),
    "TF_DETERMINISTIC_OPS": ("0", "1"),
    "TORCH_ALLOW_TF32_CUBLAS_OVERRIDE": ("0", "1"),
    "PYTORCH_NVML_BASED_CUDA_CHECK": ("0", "1"),
}


def tcl(value):
    """One literal Tcl argument, with no variable/command/backslash expansion."""
    if not isinstance(value, str) or "\0" in value:
        raise ValueError("Tcl argument must be a NUL-free string")
    for old, new in (("\\", "\\\\"), ('"', '\\"'), ("$", "\\$"),
                     ("[", "\\["), ("]", "\\]"), ("\n", "\\n"),
                     ("\r", "\\r"), ("\t", "\\t")):
        value = value.replace(old, new)
    return '"' + value + '"'


def _absolute(value):
    if (not isinstance(value, str) or not value.startswith("/") or
            value.startswith("//") or ":" in value or "\\" in value or
            any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("runtime path must be an absolute, literal POSIX path")
    path = PurePosixPath(value)
    if str(path) != value or ".." in path.parts:
        raise ValueError("runtime path must be canonical without traversal")
    return path


def _install_prefix(value):
    path = _absolute(value)
    parts = path.parts
    if (len(parts) != 7 or parts[1:3] != ("opt", "software") or
            parts[3] not in SOFTWARE or parts[4] not in TRACKS or
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", parts[5]) or
            len(parts[5]) > MAX_BUILD_ID or parts[6] not in allowed_partitions(parts[3])):
        raise ValueError("allowed_prefixes must be canonical partitioned install prefixes")
    return path


def _external_root(value):
    path = _absolute(value)
    if (any(path.is_relative_to(root) for root in ("/usr", "/lib", "/lib64")) or
            (len(path.parts) >= 4 and path.parts[1:3] in (
                ("opt", "devtools"), ("opt", "apps"), ("opt", "modules")))):
        return path
    raise ValueError("external root must identify an explicit site dependency prefix")


def _sequence(value, field):
    if not isinstance(value, (list, tuple)) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a sequence of strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{field} contains duplicates")
    return list(value)


def validate_native_entry(entry, *, allowed_prefixes=()):
    """Return a fresh validated entry; never read or execute its shell metadata."""
    if not isinstance(entry, dict) or set(entry) != {"identity", "commands", "runtime", "external_roots"}:
        raise ValueError("native entry requires identity, commands, runtime and external_roots")
    identity = validate_identity(entry["identity"])
    prefix = _install_prefix(identity["install_prefix"])
    other_prefixes = [_install_prefix(value) for value in _sequence(allowed_prefixes, "allowed_prefixes")]
    other_prefixes = [path for path in other_prefixes if path.name == identity["partition"]]
    external = _sequence(entry["external_roots"], "external_roots")
    external_paths = [_external_root(value) for value in external]
    roots = [prefix, *other_prefixes, *external_paths,
             PurePosixPath("/usr"), PurePosixPath("/lib"), PurePosixPath("/lib64")]

    def runtime_path(value):
        path = _absolute(value)
        if not any(path.is_relative_to(root) for root in roots):
            raise ValueError("runtime path is outside this delivery and recorded site dependencies")
        return str(path)

    commands = entry["commands"]
    if not isinstance(commands, dict) or not commands:
        raise ValueError("commands must map at least one command to its relative executable")
    clean_commands = {}
    for name, value in commands.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*", name) or
                not isinstance(value, str) or not re.fullmatch(r"bin/(?:[A-Za-z0-9_.+-]+/)*[A-Za-z0-9_.+-]+", value) or
                any(part in (".", "..") for part in value.split("/"))):
            raise ValueError("commands must use safe names and relative bin executable paths")
        if PurePosixPath(value).name != name:
            raise ValueError("native commands must name their executable basename; aliases need packaged wrappers")
        clean_commands[name] = value

    runtime = entry["runtime"]
    if not isinstance(runtime, dict) or set(runtime) != {"modules", "prepend", "set"}:
        raise ValueError("runtime requires exactly modules, prepend and set")
    modules = _sequence(runtime["modules"], "runtime.modules")
    for value in modules:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]*(?:/[A-Za-z0-9][A-Za-z0-9_.+-]*)+", value):
            raise ValueError("dependency module must be an exact safe name/version")
        if value.split("/", 1)[0] in SOFTWARE or value.split("/", 1)[0] == "apptainer":
            raise ValueError("native dependencies cannot load the delivered software or a container launcher")
    prepend = runtime["prepend"]
    if not isinstance(prepend, dict) or any(name not in _PREPEND for name in prepend):
        raise ValueError("unapproved runtime prepend variable")
    clean_prepend = {}
    for name, values in prepend.items():
        clean_prepend[name] = [runtime_path(value) for value in _sequence(values, f"runtime.prepend.{name}")]
        if name == "MODULEPATH" and any(
                not any(PurePosixPath(value).is_relative_to(root) for root in external_paths)
                for value in clean_prepend[name]):
            # A delivered module directory could shadow a recorded site module
            # with arbitrary payload Tcl despite a safe dependency name/version.
            raise ValueError("MODULEPATH must use explicitly recorded external site roots, never delivered code")
    settings = runtime["set"]
    if not isinstance(settings, dict):
        raise ValueError("runtime.set must be an object")
    clean_settings = {}
    for name, value in settings.items():
        if not isinstance(value, str) or len(value) > 4096:
            raise ValueError("runtime setting must be a bounded string")
        if name in _PATH_SETTINGS:
            value = runtime_path(value)
        elif name in _THREAD_SETTINGS:
            if not re.fullmatch(r"[1-9][0-9]{0,5}", value) or int(value) > 65536:
                raise ValueError("runtime thread setting must be an integer from 1 to 65536")
        elif name in _ENUM_SETTINGS:
            if value not in _ENUM_SETTINGS[name]:
                raise ValueError("unsupported runtime scalar setting")
        elif name == "DP_INFER_BATCH_SIZE":
            if not re.fullmatch(r"(?:auto(?::[1-9][0-9]{0,8})?|[1-9][0-9]{0,8})", value):
                raise ValueError("unsupported DeepMD inference batch size")
        else:
            raise ValueError("unapproved runtime set variable")
        clean_settings[name] = value
    return {"identity": identity, "commands": clean_commands, "external_roots": external,
            "runtime": {"modules": modules, "prepend": clean_prepend, "set": clean_settings}}


def _stem(software):
    return "SAI_" + software.upper().replace("-", "_")


def _regular_tree(path, *, directory=False):
    """Tcl checks reject missing paths and symlinks in every native path component."""
    kind = "isdirectory" if directory else "isfile"
    return [
        f"set sai_native_checked_path {path}",
        f'if {{![file {kind} $sai_native_checked_path]}} {{error "missing canonical native installation; deploy at the expected /opt/software prefix"}}',
        'while {$sai_native_checked_path ne "/"} {',
        '    if {[file type $sai_native_checked_path] eq "link"} {error "native installation path must not contain symlinks"}',
        '    set sai_native_checked_path [file dirname $sai_native_checked_path]',
        '}',
    ]


def _allocation_checks():
    return [
        'if {![info exists env(SLURM_JOB_ID)] || ![regexp {^[1-9][0-9]*$} $env(SLURM_JOB_ID)] || ![info exists env(SLURM_JOB_PARTITION)]} {',
        '    error "load this native software module inside a Slurm allocation"',
        '}',
    ]


def render_native_fragment(entry, *, allowed_prefixes=()):
    """Render share/sai/native-module.tcl for the entry's true /opt prefix."""
    entry = validate_native_entry(entry, allowed_prefixes=allowed_prefixes)
    identity, runtime = entry["identity"], entry["runtime"]
    prefix, partition = identity["install_prefix"], identity["partition"]
    stem = _stem(identity["software"])
    lines = ["#%Module1.0", "# Native installation; site modules/dependencies remain external.",
             "# Staging is not relocation: deploy at the exact expected prefix below.",
             "module-whatis " + tcl(f'{identity["software"]} {identity["track"]} {identity["build_id"]}; native {partition}; expected prefix {prefix}; external site dependencies'),
             'if {[module-info mode load] || [module-info mode remove]} {',
             '    if {[module-info mode load]} {',
             *('        ' + line for line in _allocation_checks()),
             f'        if {{$env(SLURM_JOB_PARTITION) ne {tcl(partition)}}} {{error "native installation does not match this Slurm partition"}}',
             f'        if {{[info exists env({stem}_NATIVE_PARTITION)] && $env({stem}_NATIVE_PARTITION) ne {tcl(partition)}}} {{error "another native partition is already loaded"}}',
             f'        if {{[info exists env({stem}_PREFIX)] && $env({stem}_PREFIX) ne {tcl(prefix)}}} {{error "another native build is already loaded"}}',
             '    } else {',
             f'        if {{![info exists env({stem}_NATIVE_PARTITION)] || $env({stem}_NATIVE_PARTITION) ne {tcl(partition)} || ![info exists env({stem}_PREFIX)] || $env({stem}_PREFIX) ne {tcl(prefix)}}} {{error "native unload identity differs from the loaded installation"}}',
             '    }',
             *('    ' + line for line in _regular_tree(tcl(prefix), directory=True)),
             f'    conflict {tcl(identity["software"])}']
    for value in reversed(runtime["prepend"].get("MODULEPATH", [])):
        lines.append(f'    prepend-path MODULEPATH {tcl(value)}')
    for value in runtime["modules"]:
        lines += ['    if {[llength [info commands depends-on]]} {',
                  f'        depends-on {tcl(value)}', '    } else {',
                  f'        module load {tcl(value)}', '    }']
    for name, values in runtime["prepend"].items():
        if name != "MODULEPATH":
            for value in reversed(values):
                lines.append(f'    prepend-path {name} {tcl(value)}')
    for name, value in runtime["set"].items():
        lines.append(f'    setenv {name} {tcl(value)}')
    # App-owned paths go after dependency operations, hence precede them in env.
    command_dirs = list(dict.fromkeys(["bin", *(str(PurePosixPath(value).parent)
                                              for value in entry["commands"].values())]))
    for relative in reversed(command_dirs):
        lines.append(f'    prepend-path PATH {tcl(prefix + "/" + relative)}')
    for name, relatives in (("LD_LIBRARY_PATH", ("lib", "lib64")),
                            ("LIBRARY_PATH", ("lib", "lib64")),
                            ("CPATH", ("include",)),
                            ("PKG_CONFIG_PATH", ("lib/pkgconfig", "lib64/pkgconfig")),
                            ("MANPATH", ("share/man",))):
        for relative in reversed(relatives):
            value = tcl(prefix + "/" + relative)
            lines.append(f'    if {{[file isdirectory {value}]}} {{prepend-path {name} {value}}}')
    lines += [f'    prepend-path CMAKE_PREFIX_PATH {tcl(prefix)}',
              f'    setenv {stem}_PREFIX {tcl(prefix)}',
              f'    setenv {stem}_NATIVE_PARTITION {tcl(partition)}', '}', '']
    return "\n".join(lines)


def render_native_selector(identity):
    """Render modulefiles/<software>/<track>/<build_id>, shared by partitions."""
    identity = validate_identity(identity)
    software, track, build_id = (identity[name] for name in ("software", "track", "build_id"))
    stem = _stem(software)
    prefix_base = f"/opt/software/{software}/{track}/{build_id}"
    description = f"{software} {track} {build_id}: native Slurm-partition selection; expected prefix {prefix_base}/<PARTITION>; dependencies external; staging is not relocation"
    lines = ["#%Module1.0", "module-whatis " + tcl(description),
             "proc ModulesHelp {} {", "    puts stderr " + tcl(description), "}",
             'set sai_native_partition ""',
             'if {[module-info mode load]} {',
             *('    ' + line for line in _allocation_checks()),
             '    set sai_native_partition $env(SLURM_JOB_PARTITION)',
             '} elseif {[module-info mode remove]} {',
             f'    if {{![info exists env({stem}_NATIVE_PARTITION)]}} {{error "missing saved native partition for unload"}}',
             f'    set sai_native_partition $env({stem}_NATIVE_PARTITION)', '}',
             'if {$sai_native_partition ne ""} {',
             '    if {[lsearch -exact [list ' + ' '.join(tcl(partition) for partition in allowed_partitions(software)) + '] $sai_native_partition] < 0} {error "unsupported native software partition"}',
             f'    set sai_native_fragment [file join {tcl(prefix_base)} $sai_native_partition share sai native-module.tcl]',
             *('    ' + line for line in _regular_tree('$sai_native_fragment')),
             '    source $sai_native_fragment', '}', '']
    return "\n".join(lines)


def package_native_modules(entry, root=Path("/"), *, allowed_prefixes=()):
    """Add self-contained native modules beneath an already-installed prefix.

    ``root`` may be a build rootfs or an extraction staging root; it never
    changes the modules' expected absolute /opt paths. Both returned Paths
    (``fragment`` and ``selector``) are inside the one installation directory.
    Existing equal regular files are idempotent; conflicting contents, hard
    links and symlink ancestors are rejected, without replacing existing data.
    No directory outside the existing installation prefix is created.
    """
    entry = validate_native_entry(entry, allowed_prefixes=allowed_prefixes)
    identity = entry["identity"]
    root = Path(root)
    if not root.is_absolute() or ".." in root.parts:
        raise ValueError("native package root must be absolute without traversal")
    prefix = root / identity["install_prefix"].lstrip("/")
    relative = {
        "fragment": Path("share/sai/native-module.tcl"),
        "selector": Path("modulefiles") / identity["software"] / identity["track"] / identity["build_id"],
    }
    contents = {
        "fragment": render_native_fragment(entry, allowed_prefixes=allowed_prefixes).encode("utf-8"),
        "selector": render_native_selector(identity).encode("utf-8"),
    }
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

    def directory(start_fd, parts, *, create=False):
        descriptor = os.dup(start_fd)
        try:
            for part in parts:
                created = False
                if create:
                    try:
                        os.mkdir(part, mode=0o755, dir_fd=descriptor)
                        created = True
                    except FileExistsError:
                        pass
                next_descriptor = os.open(part, directory_flags, dir_fd=descriptor)
                if created:
                    # Installation modules must remain traversable even when
                    # the build process uses a private default umask (0077).
                    # Never alter permissions on a pre-existing directory.
                    os.fchmod(next_descriptor, 0o755)
                os.close(descriptor)
                descriptor = next_descriptor
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def existing(parent_fd, name, content, *, normalize_mode=False):
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=parent_fd)
        except FileNotFoundError:
            return False
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    info.st_size != len(content)):
                raise ValueError("native packaged module already exists with different or unsafe contents")
            with os.fdopen(os.dup(descriptor), "rb") as stream:
                if stream.read(len(content) + 1) != content:
                    raise ValueError("native packaged module already exists with different or unsafe contents")
            if normalize_mode:
                os.fchmod(descriptor, 0o444)
        finally:
            os.close(descriptor)
        return True

    descriptors = []
    try:
        filesystem_fd = os.open("/", directory_flags)
        descriptors.append(filesystem_fd)
        prefix_fd = directory(filesystem_fd, prefix.parts[1:])
        descriptors.append(prefix_fd)
        parents = {}
        # Preflight both destinations before creating either module file.
        for name, path in relative.items():
            parent_fd = directory(prefix_fd, path.parent.parts, create=True)
            descriptors.append(parent_fd)
            parents[name] = parent_fd
            existing(parent_fd, path.name, contents[name])
        for name, path in relative.items():
            parent_fd, content = parents[name], contents[name]
            try:
                descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     mode=0o444, dir_fd=parent_fd)
            except FileExistsError:
                if not existing(parent_fd, path.name, content, normalize_mode=True):
                    raise ValueError("native packaged module changed during packaging")
            else:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(content)
                    output.flush()
                    os.fchmod(output.fileno(), 0o444)
    except OSError as error:
        raise ValueError("native package prefix and module parents must be regular directories without symlinks") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
    return {name: prefix / path for name, path in relative.items()}
