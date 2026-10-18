"""CP2K publication consumes fresh candidate science, never paired timing proof."""
import argparse
from contextlib import redirect_stdout
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))
import software_controller as controller
from delivery_layout import artifact_path
from release_contract import make_identity
from source_cache import checksum


class CP2KSciencePublicationTests(unittest.TestCase):
    def setUp(self):
        # Publication checks real Unix paths, modes and symlinks; avoid DrvFS.
        self.temp = tempfile.TemporaryDirectory(prefix="cp2k-science-publication-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.control = self.root / "controller/snapshot"
        self.control.mkdir(parents=True)
        for name in controller.contract_files("cp2k"):
            (self.control / name).write_text("# trusted fixture: " + name + "\n")
        (self.control / "cp2k").chmod(0o555)
        self.start_patch(patch.object(controller, "ROOT", self.root))
        self.start_patch(patch.object(controller, "CONTROL", self.control))
        # Raw scientific parsing has its own tests. Here the return value is
        # independent of the saved evidence, so the gate must compare both.
        self.verify = self.start_patch(patch("cp2k_acceptance.verify_evidence"))
        self.export = self.start_patch(patch("export_native.export_delivery",
                                            return_value={"fixture": "native export"}))
        self.start_patch(patch("export_native.validate_export_record"))
        self.performance = self.start_patch(patch("delivery_layout.check_performance",
                                                 side_effect=AssertionError("paired timing gate invoked")))
        self.identity = make_identity("cp2k", "release", "v2026.2", "a" * 40,
                                      "v2026.2", controller.recipe_fingerprint("cp2k"), "dsprhbm")
        self.image = artifact_path(self.root, self.identity, "build")
        self.image.parent.mkdir(parents=True)
        self.image.write_bytes(b"candidate CP2K image")
        self.build = self.root / "runs/build"
        (self.build / "results").mkdir(parents=True)
        self.write_json(self.build / "request.json", {
            "software": "cp2k", "version": "v2026.2", "target": "dsprhbm",
            "identity": self.identity, "track": "release", "source_ref": "v2026.2",
            "contract_schema": controller.CONTRACT_SCHEMA, "sha": "a" * 40,
            "controller": str(self.control), "recipe_sha256": self.identity["recipe_sha256"],
        })
        (self.build / "job.id").write_text("100\n")
        (self.build / "artifact.path").write_text(str(self.image) + "\n")
        self.write_json(self.build / "results/status.json", {
            "job": "100", "state": "COMPLETED", "exit_code": "0:0", "verified": True,
        })
        self.manifest = {
            "software": "cp2k", "version": "v2026.2", "target": "dsprhbm",
            "identity": self.identity, "source_sha": "a" * 40, "artifact": str(self.image),
            "sha256": checksum(self.image), "build_verified": True,
            "verified": False, "published": False, "contract_schema": controller.CONTRACT_SCHEMA,
            "recipe_sha256": self.identity["recipe_sha256"],
        }
        self.task = self.root / "runtime-tests/science"
        (self.task / "results").mkdir(parents=True)
        (self.task / "job.sbatch").write_text("#!/bin/bash\n# candidate scientific acceptance\n")
        (self.task / "job.id").write_text("123\n")
        self.request = {
            "artifact": str(self.image), "artifact_sha256": checksum(self.image),
            "identity": self.identity, "version": "v2026.2", "target": "dsprhbm",
            "build_run_id": "build", "launcher": str(self.control / "cp2k"),
            "launcher_sha256": checksum(self.control / "cp2k"),
            "controller_sha256": checksum(self.control / "cp2k_acceptance.py"),
            "job_script_sha256": checksum(self.task / "job.sbatch"),
        }
        self.write_json(self.task / "request.json", self.request)
        self.status = {"job": "123", "state": "COMPLETED", "exit_code": "0:0", "verified": True}
        self.write_json(self.task / "results/status.json", self.status)
        self.evidence = {
            "job": "123", "artifact_sha256": checksum(self.image), "scientific_acceptance": True,
            "cases": {"pbe": {"energy_hartree": -17.25}},
        }
        self.proof = dict(self.request, run_id="science", job="123", verified=True)
        self.manifest["cp2k_science"] = self.proof
        self.save_evidence()

    def start_patch(self, patcher):
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    @staticmethod
    def write_json(path, value):
        path.write_text(json.dumps(value, sort_keys=True) + "\n")

    def save(self):
        self.write_json(self.image.with_suffix(".json"), self.manifest)

    def save_evidence(self):
        path = self.task / "results/evidence.json"
        self.write_json(path, self.evidence)
        self.proof["evidence_sha256"] = checksum(path)
        self.verify.return_value = copy.deepcopy(self.evidence)
        self.save()

    def publish(self):
        with redirect_stdout(io.StringIO()):
            controller.publish(argparse.Namespace(run_id="build"))

    def assert_unpublished(self):
        self.assertFalse((self.image.parent / "current.sif").exists())
        self.assertFalse((self.image.parent / "current.module").exists())
        self.assertFalse((self.root / "modulefiles").exists())
        self.assertFalse(json.loads(self.image.with_suffix(".json").read_text())["published"])
        self.export.assert_not_called()

    def test_legacy_paired_benchmark_proof_cannot_publish_new_candidate(self):
        self.manifest["cp2k_benchmark"] = self.manifest.pop("cp2k_science")
        self.save()
        with self.assertRaisesRegex(ValueError, "cp2k_science"):
            self.publish()
        self.verify.assert_not_called()
        self.assert_unpublished()

    def test_candidate_science_publishes_without_legacy_baseline(self):
        self.assertNotIn("cp2k_benchmark", self.manifest)
        self.assertNotIn("statistics", self.evidence)
        self.publish()
        self.verify.assert_called_once_with(self.task, self.request)
        self.performance.assert_not_called()
        self.export.assert_called_once()
        self.assertEqual((self.image.parent / "current.sif").resolve(), self.image)
        published = json.loads(self.image.with_suffix(".json").read_text())
        self.assertTrue(published["verified"])
        self.assertTrue(published["published"])
        self.assertTrue(Path(published["modulefile"]).is_file())

    def test_large_recorded_timing_does_not_trigger_old_performance_gate(self):
        self.evidence["statistics"] = {
            "wall_seconds": {"candidate": {"median": 10**12}},
            "rounded_iteration_seconds": {"candidate": {"median": 10**12},
                                          "system": {"median": 1.0}},
        }
        self.manifest["cp2k_benchmark"] = {"verified": False, "reason": "legacy baseline unavailable"}
        self.save_evidence()
        self.publish()
        self.verify.assert_called_once_with(self.task, self.request)
        self.performance.assert_not_called()

    def test_failed_raw_science_recomputation_blocks_publication(self):
        self.verify.side_effect = ValueError("raw CP2K SCF did not converge")
        with self.assertRaisesRegex(ValueError, "did not converge"):
            self.publish()
        self.verify.assert_called_once_with(self.task, self.request)
        self.assert_unpublished()

    def test_changed_recomputed_science_blocks_publication(self):
        self.verify.return_value["cases"]["pbe"]["energy_hartree"] = -1.0
        with self.assertRaisesRegex(ValueError, "scientific results changed"):
            self.publish()
        self.verify.assert_called_once_with(self.task, self.request)
        self.assert_unpublished()

    def test_stale_proof_artifact_identity_or_verifier_is_rejected(self):
        for field, bad in (("verified", False), ("artifact_sha256", "0" * 64),
                           ("identity", dict(self.identity, source_sha="b" * 40)),
                           ("controller_sha256", "0" * 64)):
            with self.subTest(field=field):
                original = self.proof[field]
                self.proof[field] = bad
                self.save()
                with self.assertRaisesRegex(ValueError, "missing or stale cp2k_science"):
                    self.publish()
                self.assert_unpublished()
                self.proof[field] = original
        self.verify.assert_not_called()

    def test_request_must_match_pinned_science_proof(self):
        for field, bad in (("artifact", str(self.image.with_name("other.sif"))),
                           ("artifact_sha256", "0" * 64), ("target", "16v100-avx2"),
                           ("identity", dict(self.identity, source_sha="b" * 40)),
                           ("controller_sha256", "0" * 64),
                           ("launcher", str(self.control / "other-launcher")),
                           ("launcher_sha256", "0" * 64)):
            with self.subTest(field=field):
                self.write_json(self.task / "request.json", dict(self.request, **{field: bad}))
                with self.assertRaisesRegex(ValueError, "invalid cp2k_science evidence"):
                    self.publish()
                self.assert_unpublished()
        self.verify.assert_not_called()

    def test_job_must_have_successful_matching_slurm_status(self):
        for field, bad in (("verified", False), ("state", "FAILED"),
                           ("exit_code", "1:0"), ("job", "999")):
            with self.subTest(field=field):
                self.write_json(self.task / "results/status.json", dict(self.status, **{field: bad}))
                with self.assertRaisesRegex(ValueError, "invalid cp2k_science evidence"):
                    self.publish()
                self.assert_unpublished()
        self.verify.assert_not_called()

    def test_recomputed_evidence_must_match_job_and_image(self):
        for field, bad in (("job", "999"), ("artifact_sha256", "0" * 64)):
            with self.subTest(field=field):
                original = self.evidence[field]
                self.evidence[field] = bad
                self.save_evidence()
                with self.assertRaisesRegex(ValueError, "job or image"):
                    self.publish()
                self.assert_unpublished()
                self.evidence[field] = original

    def test_script_proof_must_match_scientific_request(self):
        self.proof["job_script_sha256"] = "0" * 64
        self.save()
        with self.assertRaisesRegex(ValueError, "script proof mismatch"):
            self.publish()
        self.verify.assert_called_once_with(self.task, self.request)
        self.assert_unpublished()

    def test_scientific_evidence_digest_must_match_proof(self):
        self.evidence["cases"]["pbe"]["energy_hartree"] = -1.0
        self.write_json(self.task / "results/evidence.json", self.evidence)
        with self.assertRaisesRegex(ValueError, "missing or changed cp2k_science"):
            self.publish()
        self.verify.assert_not_called()
        self.assert_unpublished()

    def test_changed_launcher_cannot_reuse_scientific_proof(self):
        launcher = self.control / "cp2k"
        launcher.chmod(0o755)
        launcher.write_text("#!/bin/bash\n# changed executable\n")
        with self.assertRaisesRegex(ValueError, "invalid cp2k_science evidence"):
            controller.validate_acceptance(self.manifest)
        self.verify.assert_not_called()
        self.assert_unpublished()

    def test_changed_image_cannot_reuse_scientific_proof(self):
        self.image.write_bytes(b"another CP2K image")
        with self.assertRaisesRegex(ValueError, "invalid or stale build contract"):
            self.publish()
        self.verify.assert_not_called()
        self.assert_unpublished()

    def test_published_candidate_still_recomputes_scientific_results(self):
        self.publish()
        published = json.loads(self.image.with_suffix(".json").read_text())
        self.verify.reset_mock()
        self.verify.side_effect = ValueError("raw scientific output changed")
        with self.assertRaisesRegex(ValueError, "raw scientific output changed"):
            controller.validate_candidate(published, self.image, published=True)
        self.verify.assert_called_once_with(self.task, self.request)
        self.performance.assert_not_called()


if __name__ == "__main__":
    unittest.main()
