import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controller"))
import cp2k_spack


class CP2KSpackTests(unittest.TestCase):
    def test_partition_roots_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sixteen = cp2k_spack.cache_roots(root, "16V100")
            cpu = cp2k_spack.cache_roots(root, "DSPRHBM")
            self.assertNotEqual(sixteen["buildcache"], cpu["buildcache"])
            self.assertTrue(str(sixteen["buildcache"]).endswith("buildcache/16V100"))

    def test_validation_rejects_bad_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = cp2k_spack.cache_roots(root, "16V100")
            for key in ("sources", "buildcache"):
                paths[key].mkdir(parents=True)
            paths["sources"].joinpath("spack.tar.gz").write_bytes(b"bad")
            paths["sources"].joinpath("packages.tar.gz").write_bytes(b"bad")
            with self.assertRaisesRegex(ValueError, "checksum"):
                cp2k_spack.validate_cache(root, "16V100", require_archives=True)

    def test_manifest_records_cpu_gpu_and_external_compiler(self):
        with tempfile.TemporaryDirectory() as directory:
            value = cp2k_spack.config("16V100", Path(directory),
                                      install_prefix=Path('/opt/software/cp2k/development/test/16V100'))
            self.assertTrue(value["cuda"])
            self.assertEqual(value["compiler"]["cc"], "/opt/devtools/gcc/13.3.0/bin/gcc")
            self.assertEqual(value["spack_commit"], cp2k_spack.SPACK_COMMIT)
            settings = value['container']['config']
            self.assertEqual(settings['install_tree']['root'],
                             '/opt/software/cp2k/development/test/16V100/dependencies/spack')
            self.assertEqual(settings['build_stage'], ['/workspace/spack-stage'])
            self.assertFalse(value['container']['bootstrap']['enable'])
            self.assertEqual(value['status'], 'preparation-only-not-concretized')

    def test_rejects_symlinked_cache_parents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'real').mkdir()
            (root / 'buildcache').symlink_to(root / 'real', target_is_directory=True)
            with self.assertRaises(ValueError):
                cp2k_spack.validate_cache(root, '16V100')
            with self.assertRaises(ValueError):
                cp2k_spack.cache_roots(root / 'buildcache', '16V100')

    def test_store_cannot_be_on_host_or_another_partition(self):
        with tempfile.TemporaryDirectory() as directory:
            for prefix in (directory, '/opt/software/cp2k/development/test/DSPRHBM',
                           '/opt/apps/cp2k/old', '/opt/software/cp2k/development/../16V100'):
                with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                    cp2k_spack.config('16V100', Path(directory), install_prefix=Path(prefix))

    def test_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'plan.json'
            cp2k_spack.write_config(output, {'status': 'preparation-only'})
            with self.assertRaises(FileExistsError):
                cp2k_spack.write_config(output, {})


if __name__ == "__main__":
    unittest.main()
