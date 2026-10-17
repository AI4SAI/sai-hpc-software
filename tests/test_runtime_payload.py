"""Runtime-only exports omit known fixtures, never arbitrary software data."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import export_native as delivery
from release_contract import make_identity


@unittest.skipUnless(shutil.which('mksquashfs') and shutil.which('unsquashfs'), 'Squashfs tools required')
class RuntimePayloadTests(unittest.TestCase):
    def setUp(self):
        saved = os.umask(0o022)
        self.addCleanup(os.umask, saved)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'rootfs'

    def fixture(self, software='abacus', link=False, runtime_reference=False, script_reference=False):
        identity = make_identity(software, 'release', 'v1', 'a' * 40, 'v1', 'b' * 64, '4v100-avx512')
        self.prefix = identity['install_prefix']
        self.base = self.source / self.prefix.lstrip('/')
        item = {'identity': identity, 'commands': {'run': 'bin/run'}, 'external_roots': [],
                'runtime': {'modules': [], 'prepend': {}, 'set': {}}}
        cases = delivery.NONRUNTIME_CASES[software]
        paths = ['bin/run', 'lib/libkeep.so', 'dependencies/example/lib.so',
                 'share/cp2k/data/BASIS_MOLOPT', 'share/gpumd/src/nep.cu',
                 'share/sai/source-sha', 'share/sai/release-identity.json',
                 'share/sai/unclassified-runtime-data']
        paths += ['share/sai/' + name for name in delivery.NONRUNTIME_COMMON]
        paths += ['share/sai/' + name for name in delivery.NONRUNTIME_HELPERS.get(software, ())]
        paths += ['share/sai/' + name + '/INPUT' for name in cases]
        for relative in paths:
            path = self.base / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture ' + relative + '\n')
            path.chmod(0o755 if relative == 'bin/run' else 0o644)
        if link:
            (self.base / 'required-link').symlink_to('share/sai/CMakeCache.txt')
        if script_reference:
            (self.base / 'bin/run').write_text('#!/bin/sh\nsource "$prefix/share/sai/runtime-env.sh"\n')
        if runtime_reference:
            item['runtime']['set']['ABACUS_ROOT'] = self.prefix + '/share/sai/CMakeCache.txt'
        self.manifest = delivery.write_manifests([item], self.source)
        self.original_manifest = (self.base / delivery.MANIFEST_PATH).read_bytes()
        self.image = self.root / 'source.squashfs'
        subprocess.run(['mksquashfs', str(self.source), str(self.image), '-noappend',
                        '-no-progress', '-processors', '1'], check=True, stdout=subprocess.DEVNULL)

    def export(self, **kwargs):
        return delivery.export_image(self.image, delivery.checksum(self.image), self.root / 'export',
                                     reader_factory=lambda path: delivery.SquashfsReader(path, 0), **kwargs)

    def test_default_omits_fixtures_and_diagnostics_with_truthful_derived_manifest(self):
        self.fixture()
        before = copy.deepcopy(self.manifest)
        receipt = self.export()['installations'][0]
        out = self.root / 'export'
        self.assertEqual(receipt['payload_profile'], 'runtime')
        self.assertEqual(receipt['manifest_origin'], 'derived-runtime-subset')
        self.assertNotEqual(receipt['manifest_sha256'], receipt['source_manifest_sha256'])
        self.assertEqual(delivery.checksum(self.base / delivery.MANIFEST_PATH), receipt['source_manifest_sha256'])
        self.assertEqual(delivery.checksum(out / delivery.MANIFEST_PATH), receipt['manifest_sha256'])
        for relative in receipt['omitted_nonruntime_paths']:
            self.assertFalse((out / relative).exists())
        for relative in ('bin/run', 'lib/libkeep.so', 'dependencies/example/lib.so',
                         'share/cp2k/data/BASIS_MOLOPT', 'share/gpumd/src/nep.cu',
                         'share/sai/source-sha', 'share/sai/release-identity.json',
                         'share/sai/unclassified-runtime-data', delivery.FRAGMENT_PATH):
            self.assertEqual((out / relative).read_bytes(), (self.base / relative).read_bytes())
        derived = json.loads((out / delivery.MANIFEST_PATH).read_text())
        delivery.aggregate_manifests([derived])
        for row in derived['files']:
            path = out / Path(row['path']).relative_to(self.prefix.lstrip('/'))
            self.assertTrue(path.exists())
            if row['kind'] == 'file':
                self.assertEqual(delivery.checksum(path), row['sha256'])
        self.assertEqual(self.manifest, before)
        self.assertEqual((self.base / delivery.MANIFEST_PATH).read_bytes(), self.original_manifest)

    def test_archive_export_requires_explicit_option_and_preserves_original_bytes(self):
        self.fixture()
        receipt = self.export(include_diagnostics=True)['installations'][0]
        self.assertEqual(receipt['payload_profile'], 'complete')
        self.assertEqual(receipt['omitted_nonruntime_paths'], [])
        self.assertEqual((self.root / 'export' / delivery.MANIFEST_PATH).read_bytes(), self.original_manifest)
        self.assertTrue((self.root / 'export/share/sai/gpu-cases/INPUT').is_file())

    def test_remaining_link_to_excluded_data_is_rejected(self):
        self.fixture(link=True)
        with self.assertRaisesRegex(ValueError, 'missing internal|dangling'):
            self.export()
        self.assertFalse((self.root / 'export').exists())

    def test_declared_runtime_dependency_is_not_silently_removed(self):
        self.fixture(runtime_reference=True)
        with self.assertRaisesRegex(ValueError, 'declared runtime path'):
            self.export()

    def test_gpumd_jit_sources_and_unknown_files_survive(self):
        self.fixture('gpumd')
        self.export()
        self.assertTrue((self.root / 'export/share/gpumd/src/nep.cu').is_file())
        self.assertFalse((self.root / 'export/share/sai/cases').exists())

    def test_cp2k_data_survives(self):
        self.fixture('cp2k')
        self.export()
        self.assertTrue((self.root / 'export/share/cp2k/data/BASIS_MOLOPT').is_file())
        self.assertFalse((self.root / 'export/share/sai/smoke-case').exists())

    def test_hardcoded_runtime_script_reference_fails_closed(self):
        self.fixture(script_reference=True)
        with self.assertRaisesRegex(ValueError, 'retained executable script'):
            self.export()
        self.assertFalse((self.root / 'export').exists())


if __name__ == '__main__':
    unittest.main()
