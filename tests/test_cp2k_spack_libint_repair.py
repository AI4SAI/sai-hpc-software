import sys
from pathlib import Path
import unittest
import subprocess
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
from cp2k_spack_libint_repair import ORIGINAL, REPAIRED, repair_export, validate_patch_checkpoint


class LibintExportRepairTests(unittest.TestCase):
    def source(self):
        return ('before\n' + ORIGINAL + '\n\n'
                'tgz:: exportdir $(TOPDIR)/$(EXPORTDIR)/configure\n'
                '\t(cd $(TOPDIR) && $(TAR) -czf $(EXPORTDIR).tgz $(EXPORTDIR) )\n'
                '$(TOPDIR)/$(EXPORTDIR)/configure:: exportdir strip_some_preproc_symb\n'
                '\tautoconf\n')

    def test_only_drops_unused_archive_dependency(self):
        before = self.source()
        after = repair_export(before)
        self.assertEqual(after, before.replace(ORIGINAL, REPAIRED))
        self.assertIn('exportdir strip_some_preproc_symb', after)
        self.assertIn('$(TAR) -czf', after)

    def test_fresh_upstream_and_failed_build_states_are_distinct_and_explicit(self):
        fresh = self.source().replace('$(EXPORTDIR) )', '$(EXPORTDIR) && rm -rf $(EXPORTDIR))')
        fixed = repair_export(fresh, fresh_stage=True)
        self.assertEqual(fixed, fresh.replace(ORIGINAL, REPAIRED))
        # Exactly reproduce pinned Spack build()'s later filter_file operation.
        self.assertEqual(fixed.replace('&& rm -rf $(EXPORTDIR)', ''), repair_export(self.source()))
        for text, mode in ((fresh, False), (self.source(), True),
                           (fresh.replace('rm -rf', 'rm -r'), True)):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'archive implementation'):
                repair_export(text, fresh_stage=mode)

    def test_rejects_unknown_duplicate_or_already_modified_rules(self):
        for text in ('', self.source() + ORIGINAL + '\n',
                     self.source().replace(ORIGINAL, REPAIRED),
                     self.source().replace('$(TAR) -czf', 'tar -cf')):
            with self.subTest(text=text), self.assertRaises(ValueError):
                repair_export(text)

    def test_parallel_export_keeps_prerequisites_without_scheduling_tar(self):
        makefile = ('TOPDIR=make-test-root\nEXPORTDIR=generated\n' + ORIGINAL + '\n'
                    'tgz:: exportdir $(TOPDIR)/$(EXPORTDIR)/configure\n'
                    '\t(cd $(TOPDIR) && $(TAR) -czf $(EXPORTDIR).tgz $(EXPORTDIR) )\n'
                    'exportdir::\n\t@echo GENERATE_REQUIRED\n'
                    '$(TOPDIR)/$(EXPORTDIR)/configure:: exportdir strip_some_preproc_symb\n'
                    '\t@echo CONFIGURE_REQUIRED\n'
                    'strip_some_preproc_symb:: exportdir\n\t@echo PREPROCESS_REQUIRED\n')
        result = subprocess.run(['make', '-n', '-j8', '-f', '-', 'export'],
                                input=repair_export(makefile), text=True, capture_output=True, check=True)
        for required in ('GENERATE_REQUIRED', 'CONFIGURE_REQUIRED', 'PREPROCESS_REQUIRED'):
            self.assertIn(required, result.stdout)
        self.assertNotIn('-czf', result.stdout)

    def test_checkpoint_cannot_trigger_spacks_failed_patch_restage(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            with self.assertRaises(ValueError):
                validate_patch_checkpoint(stage)
            (stage / '.spack_patched').touch()
            validate_patch_checkpoint(stage)
            (stage / '.spack_patch_failed').touch()
            with self.assertRaises(ValueError):
                validate_patch_checkpoint(stage)


if __name__ == '__main__':
    unittest.main()
