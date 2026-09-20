#!/usr/bin/env python3
"""Drop only Libint's unused export tarball, not generation or build checks."""
import hashlib
import json
from pathlib import Path

ORIGINAL = 'export:: exportdir $(TOPDIR)/$(EXPORTDIR)/configure tgz'
REPAIRED = 'export:: exportdir $(TOPDIR)/$(EXPORTDIR)/configure'


def repair_export(text):
    lines = text.splitlines(keepends=True)
    indices = [i for i, line in enumerate(lines) if line.rstrip('\n') == ORIGINAL]
    if len(indices) != 1:
        raise ValueError('unexpected Libint export rule; refusing broad rewrite')
    if 'tgz:: exportdir $(TOPDIR)/$(EXPORTDIR)/configure\n' not in text:
        raise ValueError('expected archive rule missing')
    if '\t(cd $(TOPDIR) && $(TAR) -czf $(EXPORTDIR).tgz $(EXPORTDIR) )\n' not in text:
        raise ValueError('unexpected archive implementation')
    lines[indices[0]] = REPAIRED + '\n'
    return ''.join(lines)


def validate_patch_checkpoint(stage):
    # Spack's do_patch() may restage even with --dont-restage when a previous
    # patch failed. Only the diagnosed, successfully patched stage is reusable.
    if not (stage / '.spack_patched').is_file() or (stage / '.spack_patch_failed').exists():
        raise ValueError('expected successfully patched Libint checkpoint')


def main():
    import spack.environment as ev
    env = ev.active_environment()
    if env is None:
        raise ValueError('active locked environment required')
    roots = [s for s in env.concrete_roots() if s.name == 'libint']
    if len(roots) != 1 or not roots[0].satisfies('@2.6.0 +fortran +shared tune=cp2k-lmax-5'):
        raise ValueError('unexpected Libint identity/features')
    spec = roots[0]
    if spec.installed:
        raise ValueError('Libint is already installed; do not modify it')
    stage = Path(spec.package.stage.source_path).resolve(strict=True)
    if not stage.is_relative_to('/workspace/spack-stage'):
        raise ValueError('source escaped the file-backed stage')
    validate_patch_checkpoint(stage)
    path = stage / 'export/Makefile'
    before = path.read_text()
    after = repair_export(before)
    evidence = {
        'purpose': 'remove-unused-source-archive-not-disable-compilation',
        'spec': str(spec), 'dag_hash': spec.dag_hash(), 'file': str(path),
        'before_sha256': hashlib.sha256(before.encode()).hexdigest(),
        'after_sha256': hashlib.sha256(after.encode()).hexdigest(),
        'old_rule': ORIGINAL, 'new_rule': REPAIRED,
        'upstream_version_changed': False, 'features_changed': False,
        'local_source_patch_applied': True, 'binary_cache_publish_allowed': False,
        'diagnosis': 'Spack builds generated/ directly after make export; generated.tgz is not consumed. Both native runs failed exclusively in this redundant tar step.',
    }
    with Path('/results/libint-export-repair.json').open('x') as stream:
        json.dump(evidence, stream, indent=2)
        stream.write('\n')
    path.write_text(after)
    if path.read_text() != after:
        raise ValueError('repair did not persist')
    print('LIBINT_UNUSED_TARBALL_REMOVED: generator/configure/compile/install retained', flush=True)


if __name__ == '__main__':
    main()
