import sys
from pathlib import Path
import unittest
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
from cp2k_spack_libint_repair import ORIGINAL, REPAIRED, repair_export


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


if __name__ == '__main__':
    unittest.main()
