#!/usr/bin/env python3
"""Drop only Libint's unused export tarball, not generation or build checks."""
import hashlib
import argparse
import json
from pathlib import Path

ORIGINAL = 'export:: exportdir $(TOPDIR)/$(EXPORTDIR)/configure tgz'
REPAIRED = 'export:: exportdir $(TOPDIR)/$(EXPORTDIR)/configure'


def repair_export(text, *, fresh_stage=False):
    lines = text.splitlines(keepends=True)
    indices = [i for i, line in enumerate(lines) if line.rstrip('\n') == ORIGINAL]
    if len(indices) != 1:
        raise ValueError('unexpected Libint export rule; refusing broad rewrite')
    if 'tgz:: exportdir $(TOPDIR)/$(EXPORTDIR)/configure\n' not in text:
        raise ValueError('expected archive rule missing')
    # The pinned Spack build() removes the export-directory deletion just
    # before make export. A do_patch()-only fresh source still has the exact
    # original upstream command. Select the expected state explicitly.
    cleanup = '&& rm -rf $(EXPORTDIR)' if fresh_stage else ''
    archive = '\t(cd $(TOPDIR) && $(TAR) -czf $(EXPORTDIR).tgz $(EXPORTDIR) ' + cleanup + ')\n'
    if text.count(archive) != 1:
        raise ValueError('unexpected archive implementation')
    lines[indices[0]] = REPAIRED + '\n'
    return ''.join(lines)


def validate_patch_checkpoint(stage):
    # Spack's do_patch() may restage even with --dont-restage when a previous
    # patch failed. Only the diagnosed, successfully patched stage is reusable.
    if not (stage / '.spack_patched').is_file() or (stage / '.spack_patch_failed').exists():
        raise ValueError('expected successfully patched Libint checkpoint')


def main(fresh_stage=False):
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
    if fresh_stage:
        # Fetch/expand/apply the pinned package patches before our extra patch.
        # Installation must retain this stage with --dont-restage afterwards.
        spec.package.do_patch()
    stage = Path(spec.package.stage.source_path).resolve(strict=True)
    if not stage.is_relative_to('/workspace/spack-stage'):
        raise ValueError('source escaped the file-backed stage')
    validate_patch_checkpoint(stage)
    path = stage / 'export/Makefile'
    before = path.read_text()
    after = repair_export(before, fresh_stage=fresh_stage)
    evidence = {
        'purpose': 'remove-unused-source-archive-not-disable-compilation',
        'spec': str(spec), 'dag_hash': spec.dag_hash(), 'file': str(path),
        'before_sha256': hashlib.sha256(before.encode()).hexdigest(),
        'after_sha256': hashlib.sha256(after.encode()).hexdigest(),
        'old_rule': ORIGINAL, 'new_rule': REPAIRED,
        'checkpoint': 'fresh-do-patch' if fresh_stage else 'failed-build-after-spack-filter',
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fresh-stage', action='store_true')
    main(parser.parse_args().fresh_stage)
