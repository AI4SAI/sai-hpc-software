"""Identity continuity is mandatory from the first compile prefix to runtime."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "controller"))
import delivery_layout as layout
import software_controller as controller
from release_contract import make_identity
from source_cache import checksum


class DeliveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def identity(self, software="abacus", track="development", target="4v100-avx512", version="v1"):
        return make_identity(software, track, "develop" if software == "abacus" else "master",
                             "a" * 40, version, "b" * 64, target)

    def candidate(self, identity):
        artifact = layout.artifact_path(self.root, identity, "build")
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"synthetic artifact, never submitted or exported")
        record = dict(identity=identity, software=identity["software"], version=identity["source_version"],
                      target=identity["target"], source_sha=identity["source_sha"],
                      recipe_sha256=identity["recipe_sha256"], contract_schema=layout.CONTRACT_SCHEMA,
                      artifact=str(artifact), sha256=checksum(artifact), build_verified=True)
        artifact.with_suffix(".json").write_text(json.dumps(record))
        return artifact, record

    def test_channels_and_real_partitions_are_distinct_catalogs(self):
        paths = set()
        for software in ("abacus", "cp2k"):
            for track in ("development", "prerelease", "release"):
                for target in ("dsprhbm", "4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
                    identity = self.identity(software, track, target)
                    artifact, record = self.candidate(identity)
                    self.assertEqual(artifact.parent.name, identity["partition"])
                    self.assertEqual(layout.load_artifact(self.root, artifact), record)
                    self.assertIn(f"/{software}/{track}/{identity['build_id']}/", str(artifact))
                    paths.add(artifact)
        self.assertEqual(len(paths), 24)

    def test_legacy_relabelled_and_corrupt_sidecars_fail_closed(self):
        artifact, record = self.candidate(self.identity())
        for field, value in (("identity", None), ("contract_schema", 2), ("build_verified", False),
                             ("source_sha", "c" * 40), ("version", "v2"),
                             ("recipe_sha256", "c" * 64), ("target", "16v100-avx2"),
                             ("sha256", "0" * 64), ("artifact", "/wrong.sif")):
            with self.subTest(field=field):
                artifact.with_suffix(".json").write_text(json.dumps(dict(record, **{field: value})))
                with self.assertRaises(ValueError):
                    layout.load_artifact(self.root, artifact)
        for track in ("release", "prerelease"):
            altered = dict(record, identity=self.identity(track=track))
            artifact.with_suffix(".json").write_text(json.dumps(altered))
            with self.assertRaises(ValueError):
                layout.load_artifact(self.root, artifact)

    def test_first_build_command_uses_identity_not_old_version_target_prefix(self):
        for software in ("abacus", "cp2k"):
            args = argparse.Namespace(software=software, run_id="build", sha="a" * 40,
                                      version="v1", target="4v100-avx512", track="development",
                                      source_ref="develop" if software == "abacus" else "master",
                                      jobs=8, minutes=30, overlay_mb=8192)
            with patch.object(controller, "ROOT", self.root), patch.object(controller, "recipe_fingerprint", return_value="b" * 64):
                script = controller.render_job(args)
            identity = self.identity(software)
            self.assertIn(identity["install_prefix"], script)
            self.assertIn(str(layout.artifact_path(self.root, identity, "build")), script)
            self.assertNotIn(f"/opt/software/{software}/v1/4v100-avx512", script)
            subprocess.run(["bash", "-n"], input=script, text=True, check=True)
            args.identity = self.identity(software, track="release")
            with patch.object(controller, "ROOT", self.root), patch.object(controller, "recipe_fingerprint", return_value="b" * 64):
                with self.assertRaisesRegex(ValueError, "identity"):
                    controller.render_job(args)

    def test_container_prefix_and_installed_identity_are_recomputed(self):
        identity = self.identity()
        installed = self.root / "identity.json"
        installed.write_text(json.dumps(identity))
        command = [sys.executable, str(REPO / "controller/delivery_layout.py"), "prefix", json.dumps(identity),
                   "abacus", "a" * 40, "v1", "4v100-avx512", "--installed", str(installed)]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), identity["install_prefix"])
        installed.write_text(json.dumps(self.identity(track="release")))
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        for entry in ("container_entry.sh", "cp2k_container_entry.sh"):
            script = (REPO / "controller" / entry).read_text()
            self.assertIn('delivery_layout.py prefix "$delivery"', script)
            self.assertIn('share/sai/release-identity.json', script)
            self.assertIn('umask 022', script)
        cp2k = (REPO / "controller/cp2k_container_entry.sh").read_text()
        self.assertLess(cp2k.index("mkdir -p /workspace/export/opt/software"), cp2k.index("cp -a /opt/software/cp2k"))

    def test_launcher_uses_validated_manifest_prefix_and_rejects_wrong_partition(self):
        identity = self.identity()
        artifact, _ = self.candidate(identity)
        command = [sys.executable, str(REPO / "controller/delivery_layout.py"), "runtime",
                   str(self.root), str(artifact), "abacus", "4v100-avx512"]
        self.assertEqual(subprocess.run(command, check=True, text=True, capture_output=True).stdout.strip(), identity["install_prefix"])
        command[-1] = "16v100-avx2"
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        for software in ("abacus", "cp2k"):
            launcher = (REPO / f"controller/{software}_runtime.sh").read_text()
            self.assertIn('delivery_layout.py" runtime', launcher)
            self.assertNotIn('prefix="/opt/software/', launcher)
            self.assertNotIn('image="$catalog/current.sif"', launcher)

    def test_cp2k_skylake_native_cpu_uses_site_avx2_dependencies(self):
        recipe = (REPO / "controller/cp2k_build.sh").read_text()
        self.assertIn("8v100v0-avx512)", recipe)
        self.assertIn("-march=native -mtune=native", recipe)
        self.assertIn("Gold 6146", recipe)
        self.assertIn('[[ "${OPAL_PREFIX:?}" == *-avx2 ]]', recipe)
        self.assertIn('[[ "${OPENBLAS_ROOT:?}" == *-avx2 ]]', recipe)

    def test_resume_never_relabels_a_legacy_or_other_channel_overlay(self):
        old = self.root / "runs/old"
        old.mkdir(parents=True)
        args = argparse.Namespace(software="abacus", run_id="new", sha="a" * 40, version="v1",
                                  target="4v100-avx512", track="development", source_ref="develop",
                                  jobs=8, minutes=30, overlay_mb=8192, resume_run="old")
        for previous in ({"software": "abacus", "sha": "a" * 40, "version": "v1", "target": args.target},
                         dict(software="abacus", sha="a" * 40, version="v1", target=args.target,
                              recipe_sha256="b" * 64, contract_schema=layout.CONTRACT_SCHEMA,
                              identity=self.identity(track="release")),
                         dict(software="abacus", sha="a" * 40, version="v1", target=args.target,
                              recipe_sha256="b" * 64, contract_schema=2, identity=self.identity())):
            (old / "request.json").write_text(json.dumps(previous))
            with patch.object(controller, "ROOT", self.root), patch.object(controller, "recipe_fingerprint", return_value="b" * 64), \
                    patch.object(controller, "call") as call:
                with self.assertRaises(ValueError):
                    controller.submit(args)
            self.assertEqual([str(item.args[0][0]) for item in call.call_args_list], ["git"])
            self.assertFalse((self.root / "runs/new/job.sbatch").exists())


if __name__ == "__main__":
    unittest.main()
