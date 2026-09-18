import hashlib
import json
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
            self.assertNotEqual(sixteen["store"], cpu["store"])
            self.assertTrue(str(sixteen["buildcache"]).endswith("buildcache/16V100"))

    def test_validation_rejects_symlink_and_bad_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = cp2k_spack.cache_roots(root, "16V100")
            for key in ("sources", "buildcache", "store", "config"):
                paths[key].mkdir(parents=True)
            paths["sources"].joinpath("spack.tar.gz").write_bytes(b"bad")
            paths["sources"].joinpath("packages.tar.gz").write_bytes(b"bad")
            with self.assertRaisesRegex(ValueError, "checksum"):
                cp2k_spack.validate_cache(root, "16V100", require_archives=True)

    def test_manifest_records_cpu_gpu_and_external_compiler(self):
        with tempfile.TemporaryDirectory() as directory:
            value = cp2k_spack.config("16V100", Path(directory),
                                      spack_root=Path(directory) / "spack",
                                      package_repo=Path(directory) / "packages")
            self.assertTrue(value["cuda"])
            self.assertEqual(value["compiler"]["cc"], "/opt/devtools/gcc/13.3.0/bin/gcc")
            self.assertEqual(value["spack_commit"], cp2k_spack.SPACK_COMMIT)
            self.assertIn("16V100", value["config"]["install_tree"]["root"])


if __name__ == "__main__":
    unittest.main()
