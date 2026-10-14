"""Final-prefix builds, exact dependency mounts, and legacy provenance."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))
from release_contract import APP_STEMS, SOFTWARE, TRACKS, make_identity, validate_identity
from native_module import render_native_fragment, render_native_selector, validate_native_entry
from remote_controller import container_command, site_app_binds
import export_native


def identity(software="abacus", target="4v100-avx512", track="release", schema=2):
    return make_identity(software, track, "v1", "a" * 40, "v1", "b" * 64,
                         target, layout_schema=schema)


def entry(record):
    return dict(identity=record, commands={"probe": "bin/probe"}, external_roots=[],
                runtime={"modules": ["openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto"],
                         "prepend": {}, "set": {}})


class SiteLayoutTests(unittest.TestCase):
    def test_all_targets_are_flat_version_siblings_with_partition_children(self):
        for software, profile in SOFTWARE.items():
            for track in TRACKS:
                for target in profile["targets"]:
                    row = identity(software, target, track)
                    prefix = Path(row["install_prefix"])
                    self.assertEqual(row["schema"], 2)
                    self.assertEqual(prefix.parent.parent, Path("/opt/apps") / software)
                    self.assertEqual(prefix.name, row["partition"])
                    self.assertEqual(prefix.parent.name,
                                     f"{APP_STEMS.get(software, software)}-{track}-{row['build_id']}")
                    self.assertEqual(validate_identity(row), row)
                    module = render_native_selector(row)
                    self.assertIn(str(prefix.parent), module)
                    self.assertNotIn("/opt/software", module)
                    self.assertIn("SLURM_JOB_PARTITION", module)
                    self.assertIn("openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto",
                                  render_native_fragment(entry(row)))

    def test_old_identity_stays_old_and_relabelling_is_rejected(self):
        old, new = identity(schema=1), identity()
        self.assertEqual(validate_identity(old), old)
        self.assertTrue(old["install_prefix"].startswith("/opt/software/"))
        self.assertIn(old["install_prefix"], render_native_fragment(entry(old)))
        for row, prefix in ((old, new["install_prefix"]), (new, old["install_prefix"])):
            with self.assertRaises(ValueError):
                validate_identity(dict(row, install_prefix=prefix))
        with self.assertRaises(ValueError):
            validate_identity(dict(old, schema=2))

    def test_exact_site_binds_never_mask_any_new_installation(self):
        for software, profile in SOFTWARE.items():
            binds = site_app_binds(software)
            command = container_command("/tmp/base.sif", ["/bin/true"], extra_binds=binds)
            self.assertNotIn("/opt/apps:/opt/apps:ro", command)
            for source, destination in binds:
                self.assertEqual(source, destination)
                self.assertGreaterEqual(len(Path(destination).parts), 5)
                self.assertIn(":" + destination + ":ro", " ".join(command))
                for target in profile["targets"]:
                    self.assertFalse(Path(identity(software, target)["install_prefix"])
                                     .is_relative_to(destination))
        for destination in ("/opt/apps", "/opt/apps/abacus", "/opt/apps/cp2k"):
            with self.assertRaisesRegex(ValueError, "hide"):
                container_command("/tmp/base.sif", ["/bin/true"],
                                  extra_binds=[(destination, destination)])

    def test_external_dependency_declarations_cannot_authorize_other_builds(self):
        row = entry(identity())
        for path in ("/opt/apps/abacus", identity(target="16v100-avx2")["install_prefix"],
                     identity()["install_prefix"] + "/lib"):
            changed = copy.deepcopy(row)
            changed["external_roots"] = [path]
            with self.assertRaises(ValueError):
                validate_native_entry(changed)

    def test_ci_uploads_the_final_prefix_dependency_builder(self):
        self.assertIn('"cp2k_dependencies.sh"', (ROOT / "controller/ci.py").read_text())
        self.assertIn('"cp2k_dependencies.sh"', (ROOT / "controller/software_controller.py").read_text())

    def test_recipes_package_only_the_validated_prefix(self):
        for name in ("container_entry.sh", "cp2k_container_entry.sh", "gpumd_container_entry.sh"):
            path = ROOT / "controller" / name
            if not path.exists():
                continue
            script = path.read_text()
            if name == "cp2k_container_entry.sh":
                self.assertIn('python3 /control/cp2k_payload.py "$INSTALL_PREFIX" /workspace/export', script)
            else:
                self.assertIn('cp -a "$INSTALL_PREFIX" "/workspace/export$INSTALL_PREFIX"', script)
            self.assertNotIn("cp -a /opt/apps ", script)
            self.assertNotIn("cp -a /opt/software ", script)
            subprocess.run(["bash", "-n", str(path)], check=True)
        abacus = (ROOT / "controller/abacus_build.sh").read_text()
        cp2k = (ROOT / "controller/cp2k_build.sh").read_text()
        self.assertIn('-DCMAKE_INSTALL_PREFIX="$INSTALL_PREFIX"', abacus)
        self.assertIn('-DCMAKE_INSTALL_PREFIX="$prefix"', cp2k)
        self.assertIn('tblite_root="$prefix/dependencies/tblite"', cp2k)


@unittest.skipUnless(shutil.which("mksquashfs") and shutil.which("unsquashfs"),
                     "requires real Squashfs tools")
class SamePrefixExportTests(unittest.TestCase):
    def test_legacy_and_apps_manifests_export_without_rewriting_paths(self):
        for schema in (1, 2):
            with self.subTest(schema=schema), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                row = identity(schema=schema)
                prefix = root / "rootfs" / row["install_prefix"].lstrip("/")
                (prefix / "bin").mkdir(parents=True)
                program = prefix / "bin/probe"
                # Simulate an absolute Python/venv shebang: export must preserve
                # it, not silently rewrite it to the administrator's staging home.
                program.write_text("#!" + row["install_prefix"] + "/bin/python\n")
                program.chmod(0o755)
                export_native.write_manifests([entry(row)], root=root / "rootfs")
                image = root / "image.squashfs"
                subprocess.run(["mksquashfs", str(root / "rootfs"), str(image),
                                "-noappend", "-processors", "1", "-no-progress"],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                reader = export_native.SquashfsReader(image, 0)
                manifest = reader.manifest()
                self.assertEqual(manifest["entries"][0]["identity"], row)
                output = root / "export"
                receipt = export_native.export_image(
                    image, hashlib.sha256(image.read_bytes()).hexdigest(), output,
                    reader_factory=lambda image: export_native.SquashfsReader(image, 0))
                self.assertEqual(receipt["expected_install_prefixes"], [row["install_prefix"]])
                self.assertEqual((output / "bin/probe").read_bytes(), program.read_bytes())
                self.assertIn(row["install_prefix"], (output / export_native.FRAGMENT_PATH).read_text())
                self.assertEqual(json.loads((output / export_native.MANIFEST_PATH).read_text())["entry"]["identity"], row)


if __name__ == "__main__":
    unittest.main()
