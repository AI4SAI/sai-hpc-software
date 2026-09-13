"""Per-target, immutable module fragments pin each tested image/launcher pair."""
import os
from pathlib import Path

from delivery_layout import artifact_path, catalog_dir, validate_record
from release_contract import MAX_BUILD_ID, PARTITIONS, SOFTWARE, TRACKS
from remote_controller import safe_name
from source_cache import checksum


def tcl(value):
    value = str(value)
    for old, new in (("\\", "\\\\"), ('"', '\\"'), ("$", "\\$"),
                     ("[", "\\["), ("]", "\\]"), ("\n", "\\n"), ("\r", "\\r")):
        value = value.replace(old, new)
    return '"' + value + '"'


def render_selector(root, software, track, build_id, description):
    """Select one accepted real-partition fragment within an immutable identity."""
    if software not in SOFTWARE or track not in TRACKS:
        raise ValueError("unregistered module software or track")
    safe_name(build_id)
    if len(build_id) > MAX_BUILD_ID:
        raise ValueError("module build_id exceeds delivery contract")
    root = Path(root)
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError("module root must be absolute without symlink ancestors")
    stem = "SAI_" + software.upper().replace("-", "_")
    catalog = root / "containers/software" / software / track / build_id
    targets = SOFTWARE[software]["targets"]
    lines = ["#%Module1.0",
             "module-whatis " + tcl(f"{description} {track}/{build_id}: partition-native, accepted SIF and launcher"),
             f"conflict {software}", f"set sai_catalog {tcl(catalog)}",
             'set sai_target ""', 'set sai_partition ""', 'set sai_run ""',
             'if {[module-info mode remove]} {',
             f'    if {{![info exists env({stem}_MODULE_TARGET)] || ![info exists env({stem}_MODULE_PARTITION)] || ![info exists env({stem}_MODULE_RUN)] || ![info exists env({stem}_MODULE_TRACK)] || ![info exists env({stem}_MODULE_BUILD_ID)]}} {{error "missing saved immutable module identity"}}',
             f'    if {{$env({stem}_MODULE_TRACK) ne {tcl(track)} || $env({stem}_MODULE_BUILD_ID) ne {tcl(build_id)}}} {{error "unload selector differs from the loaded channel/build"}}',
             f'    set sai_target $env({stem}_MODULE_TARGET)',
             f'    set sai_partition $env({stem}_MODULE_PARTITION)',
             f'    set sai_run $env({stem}_MODULE_RUN)',
             '} elseif {[module-info mode load] && [info exists env(SLURM_JOB_PARTITION)]} {',
             f'    if {{[info exists env({stem}_MODULE_TRACK)] && $env({stem}_MODULE_TRACK) ne {tcl(track)}}} {{error "another module release channel is already loaded"}}',
             f'    if {{[info exists env({stem}_MODULE_BUILD_ID)] && $env({stem}_MODULE_BUILD_ID) ne {tcl(build_id)}}} {{error "another immutable module build is already loaded"}}',
             f'    if {{[info exists env({stem}_MODULE_PARTITION)] && $env({stem}_MODULE_PARTITION) ne $env(SLURM_JOB_PARTITION)}} {{error "another module partition is already loaded"}}',
             '    switch -- $env(SLURM_JOB_PARTITION) {']
    for target in targets:
        lines.append(f'        {PARTITIONS[target]} {{set sai_target {target}; set sai_partition {PARTITIONS[target]}}}')
    lines += ['        default {error "unsupported native software partition"}', '    }',
              '    set sai_current [file join $sai_catalog $sai_partition current.module]',
              '    if {![file exists $sai_current] || [file type $sai_current] ne "link"} {',
              '        error "no accepted image/launcher module for this partition"', '    }',
              '    set sai_link [file readlink $sai_current]',
              r'    if {![regexp {^[A-Za-z0-9][A-Za-z0-9_.-]*\.module$} $sai_link]} {',
              '        error "invalid accepted module fragment"', '    }',
              '    set sai_run [file rootname $sai_link]',
              '} elseif {[module-info mode load]} {',
              '    error "load this native software module inside the Slurm allocation"', '}',
              'if {$sai_target ne "" && $sai_run ne ""} {',
              '    if {![regexp {^[A-Za-z0-9][A-Za-z0-9_.-]*$} $sai_run]} {error "invalid saved module run"}',
              '    set sai_partitions [dict create ' + ' '.join(f'{target} {PARTITIONS[target]}' for target in targets) + ']',
              '    if {![dict exists $sai_partitions $sai_target] || [dict get $sai_partitions $sai_target] ne $sai_partition} {error "invalid saved module target/partition"}',
              '    set sai_fragment [file join $sai_catalog $sai_partition "$sai_run.module"]',
              '    if {![file isfile $sai_fragment] || [file type $sai_fragment] eq "link"} {error "missing immutable module fragment"}',
              '    set sai_checked_path [file dirname $sai_fragment]',
              '    while {$sai_checked_path ne "/"} {',
              '        if {[file type $sai_checked_path] eq "link"} {error "immutable module path contains a symlink"}',
              '        set sai_checked_path [file dirname $sai_checked_path]',
              '    }',
              f'    setenv {stem}_MODULE_TARGET $sai_target',
              f'    setenv {stem}_MODULE_PARTITION $sai_partition',
              f'    setenv {stem}_MODULE_TRACK {tcl(track)}',
              f'    setenv {stem}_MODULE_BUILD_ID {tcl(build_id)}',
              f'    setenv {stem}_MODULE_RUN $sai_run',
              '    source $sai_fragment', '}', '']
    return '\n'.join(lines)


def atomic_text(path, content, *, immutable=False):
    if path.is_symlink() or path.parent.resolve() != path.parent:
        raise ValueError("untrusted module publication path")
    if immutable and path.exists():
        if path.read_text() != content:
            raise ValueError("immutable module fragment changed")
        return
    temporary = path.with_name(f".{path.name}-{os.getpid()}.tmp")
    temporary.write_text(content)
    temporary.chmod(0o444)
    os.replace(temporary, path)


def publish_module(root, request, artifact, launcher, module_lines, description):
    identity = validate_record(request)
    software, version = identity["software"], identity["source_version"]
    root, artifact, launcher = Path(root), Path(artifact), Path(launcher)
    if (artifact != artifact_path(root, identity, artifact.stem) or
            not artifact.is_file() or artifact.is_symlink() or artifact.resolve() != artifact):
        raise ValueError("published artifact differs from the canonical delivery identity")
    stem = "SAI_" + software.upper().replace("-", "_")
    if (not launcher.is_file() or launcher.is_symlink() or launcher.resolve() != launcher or
            not launcher.is_relative_to(root / 'controller')):
        raise ValueError('untrusted published launcher path')
    fragment = artifact.with_suffix(".module")
    content = '\n'.join([
        '# Immutable, target-specific accepted image and launcher.',
        'prepend-path MODULEPATH /opt/modules/modulefiles/devtools',
        'module load apptainer/1.4.4', *module_lines,
        f'setenv SAI_SOFTWARE_ROOT {tcl(root)}',
        f'setenv {stem}_IMAGE {tcl(artifact)}',
        f'setenv {stem}_VERSION {tcl(version)}',
        f'setenv {stem}_PREFIX {tcl(identity["install_prefix"])}',
        f'prepend-path PATH {tcl(launcher.parent)}', ''])
    atomic_text(fragment, content, immutable=True)
    module_dir = root / 'modulefiles/apps' / software / identity["track"]
    module_dir.mkdir(parents=True, exist_ok=True)
    module = module_dir / identity["build_id"]
    # Identical for every partition: a later GPU build cannot change CPU deps
    # or swap another partition's tested launcher. Existing loaded modules
    # retain their immutable run token, including during unload after publish.
    atomic_text(module, render_selector(root, software, identity["track"], identity["build_id"], description))
    temporary = artifact.parent / f'.current-module-{os.getpid()}'
    temporary.symlink_to(fragment.name)
    os.replace(temporary, artifact.parent / 'current.module')
    return {'modulefile': str(module), 'module_fragment': str(fragment),
            'module_fragment_sha256': checksum(fragment)}


def validate_module(manifest, artifact, *, root):
    identity = validate_record(manifest)
    root, artifact = Path(root), Path(artifact)
    software = identity["software"]
    if (artifact != artifact_path(root, identity, artifact.stem) or
            not artifact.is_file() or artifact.is_symlink() or artifact.resolve() != artifact):
        raise ValueError("published module artifact differs from its delivery identity")
    selector = root / 'modulefiles/apps' / software / identity["track"] / identity["build_id"]
    current = artifact.parent / 'current.module'
    if (manifest.get('modulefile') != str(selector) or not selector.is_file() or
            selector.is_symlink() or selector.resolve() != selector or
            selector.read_text() != render_selector(root, software, identity["track"], identity["build_id"], software.upper()) or
            not current.is_symlink() or current.readlink() != Path(artifact.with_suffix('.module').name)):
        raise ValueError('published module selector is missing, changed, or no longer current')
    fragment = artifact.with_suffix('.module')
    if (manifest.get('module_fragment') != str(fragment) or
            not fragment.is_file() or fragment.is_symlink() or fragment.resolve() != fragment or
            checksum(fragment) != manifest.get('module_fragment_sha256')):
        raise ValueError('published module fragment is missing or changed')
    launcher = Path(manifest.get('runtime_launcher', ''))
    if (not launcher.is_file() or launcher.is_symlink() or launcher.resolve() != launcher or
            not launcher.is_relative_to(root / 'controller') or
            checksum(launcher) != manifest.get('runtime_launcher_sha256')):
        raise ValueError('published module launcher is missing or changed')
