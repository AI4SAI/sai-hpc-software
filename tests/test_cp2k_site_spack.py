from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
from test_cp2k_devtools import provider_fixture
import cp2k_site_spack as site
from cp2k_spack_link import validate_receipt, shell_variables


class SiteSpackTests(unittest.TestCase):
    def entry(self):
        p = provider_fixture()
        libint = next(n for n in p['install']['nodes'] if n['name'] == 'libint')
        return dict(provider=p, native_flags='  -march= znver3',
                    libint_repair=dict(dag_hash=libint['hash'], local_source_patch_applied=True))

    def test_absent_provider_preserves_existing_build_and_active_provider_is_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / 'site.json'
            lock.write_text(json.dumps(dict(schema=1, partitions={})))
            self.assertIsNone(site.selected('16V100', lock=lock))
            entry = self.entry()
            lock.write_text(json.dumps(dict(schema=1, partitions={'16V100': entry})))
            self.assertEqual(site.selected('16V100', lock=lock, files=False), entry)
            with patch.object(site, 'validate_provider', side_effect=ValueError('changed physical file')):
                with self.assertRaisesRegex(ValueError, 'physical'):
                    site.selected('16V100', lock=lock)

    def test_one_library_provider_is_reused_across_application_sources_and_tracks(self):
        entry = self.entry()
        prefixes = ['/opt/apps/cp2k/cp2k-development-sha1/16V100',
                    '/opt/apps/cp2k/cp2k-development-sha2/16V100',
                    '/opt/apps/cp2k/cp2k-release-2026.2/16V100']
        for prefix in prefixes:
            receipt = site.make_receipt(entry, '16V100', prefix)
            roots, runtime = validate_receipt(receipt, '16V100', prefix)
            self.assertEqual(roots, entry['provider']['install']['roots'])
            self.assertEqual(runtime, entry['provider']['install']['runtime_prefixes'])
            self.assertIn('/opt/devtools/libint/', shell_variables(receipt, '16V100', prefix))
            with self.assertRaises(ValueError):
                validate_receipt(receipt, '16V100', prefix.replace('cp2k-', 'cp2k-other-'))
        with self.assertRaises(ValueError):
            site.make_receipt(entry, '4V100', '/opt/apps/cp2k/cp2k-development-test/4V100')

    def test_mixed_shared_receipts_and_unproven_roots_are_rejected(self):
        prefix = '/opt/apps/cp2k/cp2k-development-test/16V100'
        for change in (
            lambda d: d['roots'].update(libint='/opt/devtools/libint/other'),
            lambda d: d.update(shared_provider_sha256='0' * 64),
            lambda d: d['runtime_prefixes'].pop(),
        ):
            receipt = site.make_receipt(self.entry(), '16V100', prefix)
            change(receipt)
            with self.assertRaises(ValueError):
                validate_receipt(receipt, '16V100', prefix)

    def test_cpu_mismatch_writes_no_receipts_and_success_preserves_repair_evidence(self):
        entry = self.entry()
        entry['native_flags'] = '-march= znver3'
        prefix = '/opt/apps/cp2k/cp2k-development-test/16V100'
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with patch.object(site, 'selected', return_value=entry), \
                 patch.object(site.subprocess, 'check_output', return_value='-march= znver4'):
                with self.assertRaisesRegex(ValueError, 'CPU flags'):
                    site.materialize('16V100', prefix, output)
            self.assertEqual(list(output.iterdir()), [])
            with patch.object(site, 'selected', return_value=entry), \
                 patch.object(site.subprocess, 'check_output', return_value='-march= znver3\n'):
                site.materialize('16V100', prefix, output)
                with self.assertRaisesRegex(ValueError, 'already exists'):
                    site.materialize('16V100', prefix, output)
            data = json.loads((output / 'install-success.json').read_text())
            validate_receipt(data, '16V100', prefix)
            self.assertEqual(json.loads((output / 'libint-export-repair.json').read_text()), entry['libint_repair'])


if __name__ == '__main__':
    unittest.main()
