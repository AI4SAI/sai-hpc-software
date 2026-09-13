"""ABACUS package metadata remains inside the complete installation folder."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controller"))
import abacus_features as features
import export_native
from export_native import MANIFEST_PATH, inventory, read_installed_manifests, write_manifests
from release_contract import make_identity
from source_cache import checksum


class NativeLayoutTests(unittest.TestCase):
    def test_native_dependencies_match_each_partition_and_reuse_site_modules(self):
        for target in ("dsprhbm", "4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
            with self.subTest(target=target):
                identity = make_identity("abacus", "development", "develop", "a" * 40,
                                         "develop-2026-09-13", "b" * 64, target)
                environment = features.expected_roots(identity)
                modules = features.MODULES + ["nvhpc/26.3-gnu-cuda12-tuned",
                                              "gcc/13.3.0" if target == "dsprhbm" else "nvmplibs/26.7-tmp"]
                ldd = "libmpi.so.40 => " + environment["MPI_HOME"] + "/lib/libmpi.so.40 (0x1234)"
                entry = features.make_entry(identity, environment, modules, ldd)
                self.assertEqual(entry["identity"], identity)
                self.assertIn("elpa/2026.02.001-2603-gnu", entry["runtime"]["modules"])
                self.assertEqual("nvmplibs/26.7-tmp" in entry["runtime"]["modules"], target != "dsprhbm")
                if target == "8v100v0-avx512":
                    self.assertTrue(environment["MPI_HOME"].endswith("-avx2"))
                with self.assertRaisesRegex(ValueError, "not actually loaded"):
                    features.make_entry(identity, environment, modules[:-1], ldd)
                with self.assertRaisesRegex(ValueError, "does not match this partition"):
                    features.make_entry(identity, dict(environment, MPI_HOME="/opt/devtools/openmpi/wrong-isa"), modules, ldd)
                with self.assertRaisesRegex(ValueError, "unrecorded native runtime dependency"):
                    features.make_entry(identity, environment, modules, "libmpi.so => /workspace/build/libmpi.so")
                with self.assertRaisesRegex(ValueError, "unresolved runtime libraries"):
                    features.make_entry(identity, environment, modules, ldd + "\nlibmissing.so => not found")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="abacus-native-layout-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.identity = make_identity("abacus", "development", "develop", "a" * 40,
                                      "test-layout", "b" * 64, "4v100-avx512")
        self.prefix = Path(self.identity["install_prefix"])
        self.base = self.root / str(self.prefix).lstrip("/")
        (self.base / "bin").mkdir(parents=True)
        binary = self.base / "bin/abacus"
        binary.write_text("fixture only\n")
        binary.chmod(0o755)
        self.metadata = self.base / "share/sai"
        self.metadata.mkdir(parents=True)
        self.entry = {"identity": self.identity, "commands": {"abacus": "bin/abacus"},
                      "external_roots": [], "runtime": {"modules": [], "prepend": {}, "set": {}}}
        (self.metadata / "dependency-lock.json").write_text('{"fixture": true}\n')
        for name, value in (("release-identity.json", self.identity), ("native-entry.json", self.entry),
                            ("native-dependencies.json", {"identity": self.identity,
                             "dependency_lock_sha256": checksum(self.metadata / "dependency-lock.json")})):
            (self.metadata / name).write_text(json.dumps(value))

    def test_manifest_modules_and_evidence_all_stay_inside_prefix(self):
        entry = features.installed_entry(self.prefix, self.root)
        write_manifests([entry], root=self.root)
        self.assertEqual(read_installed_manifests([entry], root=self.root),
                         inventory([entry], root=self.root))
        self.assertTrue((self.base / MANIFEST_PATH).is_file())
        self.assertTrue((self.base / "share/sai/native-module.tcl").is_file())
        selector = self.base / "modulefiles/abacus/development" / self.identity["build_id"]
        self.assertTrue(selector.is_file())
        self.assertNotIn(str(self.root), selector.read_text())
        self.assertFalse((self.root / "opt/sai-delivery").exists())
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertTrue(path.is_relative_to(self.base))

    def test_modified_dependency_evidence_or_contents_cannot_pass_verify(self):
        write_manifests([self.entry], root=self.root)
        stored = read_installed_manifests([self.entry], root=self.root)
        (self.base / "bin/abacus").write_text("changed fixture\n")
        self.assertNotEqual(stored, inventory([features.installed_entry(self.prefix, self.root)], root=self.root))
        (self.metadata / "dependency-lock.json").write_text('{"fixture": false}\n')
        with self.assertRaisesRegex(ValueError, "dependency lock differ"):
            features.installed_entry(self.prefix, self.root)

    def test_export_phase_uses_prefix_local_writer(self):
        with patch.object(sys, "argv", ["abacus_features.py", str(self.prefix), "4v100-avx512", "--native-phase", "inventory"]), \
                patch.object(features, "installed_entry", return_value=self.entry) as read, \
                patch.object(export_native, "write_manifests") as write:
            features.main()
        read.assert_called_once_with(self.prefix, Path("/workspace/export"))
        write.assert_called_once_with([self.entry], root=Path("/workspace/export"))

    def test_verify_reads_entry_once_and_uses_shared_inventory(self):
        with patch.object(sys, "argv", ["abacus_features.py", str(self.prefix), "4v100-avx512", "--native-phase", "verify"]), \
                patch.object(features, "installed_entry", return_value=self.entry) as read, \
                patch.object(export_native, "read_installed_manifests", return_value={"fixture": True}) as stored, \
                patch.object(export_native, "inventory", return_value={"fixture": True}) as current:
            features.main()
        read.assert_called_once_with(self.prefix, Path("/"))
        stored.assert_called_once_with([self.entry], root=Path("/"))
        current.assert_called_once_with([self.entry], root=Path("/"))


if __name__ == "__main__":
    unittest.main()
