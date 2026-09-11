"""Per-target, immutable module fragments pin each tested image/launcher pair."""
import os
from pathlib import Path

from remote_controller import TARGETS, safe_name
from source_cache import checksum


def tcl(value):
    value = str(value)
    for old, new in (("\\", "\\\\"), ('"', '\\"'), ("$", "\\$"),
                     ("[", "\\["), ("]", "\\]"), ("\n", "\\n"), ("\r", "\\r")):
        value = value.replace(old, new)
    return '"' + value + '"'


def render_selector(root, software, version, description):
    software, version = safe_name(software), safe_name(version)
    stem = "SAI_" + software.upper()
    catalog = Path(root) / "containers/software" / software / version
    lines = ["#%Module1.0",
             "module-whatis " + tcl(f"{description} {version}: partition-native, accepted SIF and launcher"),
             f"conflict {software}", f"set sai_catalog {tcl(catalog)}",
             'set sai_target ""', 'set sai_run ""',
             f'if {{[module-info mode remove] && [info exists env({stem}_MODULE_TARGET)] && [info exists env({stem}_MODULE_RUN)]}} {{',
             f'    set sai_target $env({stem}_MODULE_TARGET)',
             f'    set sai_run $env({stem}_MODULE_RUN)',
             '} elseif {[module-info mode load] && [info exists env(SLURM_JOB_PARTITION)]} {',
             '    switch -- $env(SLURM_JOB_PARTITION) {']
    for target, profile in TARGETS.items():
        lines.append(f'        {profile["partition"]} {{set sai_target {target}}}')
    lines += ['        default {error "unsupported native software partition"}', '    }',
              '    set sai_current [file join $sai_catalog $sai_target current.module]',
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
              '    if {[lsearch -exact {' + ' '.join(TARGETS) + '} $sai_target] < 0} {error "invalid saved module target"}',
              '    set sai_fragment [file join $sai_catalog $sai_target "$sai_run.module"]',
              '    if {![file isfile $sai_fragment] || [file type $sai_fragment] eq "link"} {error "missing immutable module fragment"}',
              f'    setenv {stem}_MODULE_TARGET $sai_target',
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
    software = safe_name(request.get("software", "abacus"))
    version = safe_name(request["version"])
    safe_name(artifact.stem)
    stem = "SAI_" + software.upper()
    if launcher.resolve() != launcher or not launcher.is_relative_to(Path(root) / 'controller'):
        raise ValueError('untrusted published launcher path')
    fragment = artifact.with_suffix(".module")
    content = '\n'.join([
        '# Immutable, target-specific accepted image and launcher.',
        'prepend-path MODULEPATH /opt/modules/modulefiles/devtools',
        'module load apptainer/1.4.4', *module_lines,
        f'setenv SAI_SOFTWARE_ROOT {tcl(root)}',
        f'setenv {stem}_IMAGE {tcl(artifact)}',
        f'prepend-path PATH {tcl(launcher.parent)}', ''])
    atomic_text(fragment, content, immutable=True)
    module_dir = Path(root) / 'modulefiles/apps' / software
    module_dir.mkdir(parents=True, exist_ok=True)
    module = module_dir / version
    # Identical for every partition: a later GPU build cannot change CPU deps
    # or swap another partition's tested launcher. Existing loaded modules
    # retain their immutable run token, including during unload after publish.
    atomic_text(module, render_selector(root, software, version, description))
    temporary = artifact.parent / f'.current-module-{os.getpid()}'
    temporary.symlink_to(fragment.name)
    os.replace(temporary, artifact.parent / 'current.module')
    return {'modulefile': str(module), 'module_fragment': str(fragment),
            'module_fragment_sha256': checksum(fragment)}


def validate_module(manifest, artifact):
    root, software, version = artifact.parents[5], artifact.parents[2].name, artifact.parents[1].name
    selector = root / 'modulefiles/apps' / software / version
    current = artifact.parent / 'current.module'
    if (manifest.get('modulefile') != str(selector) or not selector.is_file() or
            selector.is_symlink() or selector.resolve() != selector or
            selector.read_text() != render_selector(root, software, version, software.upper()) or
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
