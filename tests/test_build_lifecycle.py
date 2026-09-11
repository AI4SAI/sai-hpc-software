"""The build monitor creates candidates; CI publishes only after acceptance."""
import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))
import ci
import software_controller as controller


class BuildMonitorLifecycleTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".test-work"
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.control = self.root / "controller/reviewed/build"
        self.control.mkdir(parents=True)
        for field, value in (("ROOT", self.root), ("CONTROL", self.control)):
            patcher = patch.object(controller, field, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.task = self.root / "runs/build"
        (self.task / "results").mkdir(parents=True)
        (self.task / "job.id").write_text("100\n")
        self.configure("abacus")

    def configure(self, software):
        self.software = software
        # Keep this fixture independent of in-flight GPU controller edits.
        for name in controller.contract_files(software):
            (self.control / name).write_text("# trusted contract fixture: " + name + "\n")
        (self.control / software).chmod(0o555)
        self.image = self.root / f"containers/software/{software}/v1/dsprhbm/build.sif"
        self.image.parent.mkdir(parents=True, exist_ok=True)
        self.image.write_bytes(b"candidate SIF")
        (self.task / "artifact.path").write_text(str(self.image) + "\n")
        self.request = {"software": software, "version": "v1", "target": "dsprhbm",
                        "sha": "a" * 40, "controller": str(self.control),
                        "contract_schema": controller.CONTRACT_SCHEMA,
                        "recipe_sha256": controller.recipe_fingerprint(software)}
        self.save_request()

    def save_request(self):
        (self.task / "request.json").write_text(json.dumps(self.request))

    def monitor(self, state="COMPLETED", exit_code="0:0"):
        queue = subprocess.CompletedProcess(["squeue"], 1, stdout="", stderr="")
        accounting = subprocess.CompletedProcess(
            ["sacct"], 0, stdout=f"100|{state}|{exit_code}|\n", stderr="")
        with patch.object(controller.subprocess, "run", return_value=queue), \
                patch.object(controller, "call", return_value=accounting), \
                redirect_stdout(io.StringIO()):
            return controller.monitor(argparse.Namespace(run_id="build", timeout=1, interval=0))

    def lookup(self):
        output = io.StringIO()
        with redirect_stdout(output):
            controller.lookup(argparse.Namespace(software=self.software, version="v1",
                                                  target="dsprhbm", sha="a" * 40))
        return json.loads(output.getvalue())

    def publish(self):
        with redirect_stdout(io.StringIO()):
            controller.publish(argparse.Namespace(run_id="build"))

    def test_completed_build_creates_candidate_without_changing_existing_publication(self):
        old_image = self.image.parent / "previous.sif"
        old_image.write_bytes(b"known good image")
        current = self.image.parent / "current.sif"
        current.symlink_to(old_image.name)
        module = self.root / "modulefiles/apps/abacus/v1"
        module.parent.mkdir(parents=True)
        module.write_text("# previous trusted module\n")
        self.assertEqual(self.monitor(), 0)
        manifest = json.loads(self.image.with_suffix(".json").read_text())
        self.assertIs(manifest["build_verified"], True)
        self.assertIs(manifest["verified"], False)
        self.assertIs(manifest["published"], False)
        self.assertEqual(manifest["recipe_sha256"], self.request["recipe_sha256"])
        self.assertEqual(current.resolve(), old_image)
        self.assertEqual(module.read_text(), "# previous trusted module\n")
        self.assertNotIn("modulefile", manifest)
        self.assertEqual(self.lookup(), {})

    def test_path_or_recipe_validation_exception_leaves_build_status_false(self):
        for problem in ("path", "recipe"):
            with self.subTest(problem=problem):
                (self.task / "artifact.path").write_text(str(self.image) + "\n")
                self.request["recipe_sha256"] = controller.recipe_fingerprint(self.software)
                self.save_request()
                (self.task / "results/status.json").write_text(json.dumps({"verified": True}))
                if problem == "path":
                    (self.task / "artifact.path").write_text(str(self.image.parent / "wrong.sif"))
                else:
                    self.request["recipe_sha256"] = "0" * 64
                    self.save_request()
                with self.assertRaises(ValueError):
                    self.monitor()
                status = json.loads((self.task / "results/status.json").read_text())
                self.assertEqual(status, {"job": "100", "state": "COMPLETED",
                                          "exit_code": "0:0", "verified": False})
                self.assertFalse(self.image.with_suffix(".json").exists())
                self.assertFalse((self.image.parent / "current.sif").exists())
                self.assertFalse((self.root / "modulefiles").exists())

    def test_failed_build_status_invalidates_a_previously_valid_published_sidecar(self):
        # CP2K shares the lifecycle gate and has no ABACUS-specific acceptance;
        # make the sidecar genuinely publishable before testing its revocation.
        self.configure("cp2k")
        self.assertEqual(self.monitor(), 0)
        self.publish()
        self.assertEqual(self.lookup()["artifact"], str(self.image))
        sidecar = self.image.with_suffix(".json")
        published = sidecar.read_bytes()
        self.assertEqual(self.monitor("FAILED", "1:0"), 1)
        self.assertEqual(sidecar.read_bytes(), published)
        self.assertEqual(self.lookup(), {})
        with self.assertRaisesRegex(ValueError, "build status or provenance"):
            self.publish()


class CiLifecycleTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".test-work"
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def execute(self, *, software="abacus", target="4v100-avx512", fail_monitor=None):
        commands = []
        upstream = "a" * 40
        environment = {"SOFTWARE": software, "TARGET": target, "SOURCE_SHA": upstream,
                       "GITHUB_SHA": "b" * 40, "SOFTWARE_VERSION": "v1",
                       "REMOTE_USER": "testuser", "GITHUB_RUN_ID": "123",
                       "GITHUB_RUN_ATTEMPT": "1", "RUNNER_TEMP": str(self.root),
                       "GITHUB_EVENT_NAME": "workflow_dispatch"}

        def fake_run(argv, **kwargs):
            argv = [str(value) for value in argv]
            stdout = ""
            if argv[0] == "ssh":
                remote = shlex.split(argv[-1])
                if remote[0] == "python3":
                    script, operation = Path(remote[1]).name, remote[2]
                    commands.append((script, operation))
                    if operation == "monitor" and script == fail_monitor:
                        raise subprocess.CalledProcessError(1, argv)
                    if (script, operation) == ("source_cache.py", "inventory"):
                        stdout = json.dumps({"cache_shas": [upstream]})
            elif argv[0] == "scp" and argv[-2].endswith("/artifact.path"):
                Path(argv[-1]).write_text("/trusted/candidate.sif\n")
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

        with patch.dict(os.environ, environment, clear=True), \
                patch.object(ci, "run", side_effect=fake_run), \
                patch.object(ci.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
                redirect_stdout(io.StringIO()):
            if fail_monitor:
                with self.assertRaises(subprocess.CalledProcessError):
                    ci.main()
            else:
                ci.main()
        return [command for command in commands if command[0] != "source_cache.py"]

    def test_gpu_publish_occurs_after_both_acceptance_monitors(self):
        self.assertEqual(self.execute(), [
            ("software_controller.py", "submit"), ("software_controller.py", "monitor"),
            ("runtime_controller.py", "submit"), ("runtime_controller.py", "monitor"),
            ("gpu_feature_controller.py", "submit"), ("gpu_feature_controller.py", "monitor"),
            *[("abacus_benchmark.py", op) for _ in range(3) for op in ("prepare", "submit", "monitor")],
            ("software_controller.py", "publish"),
        ])
        self.assertEqual(self.execute(target="8v100v0-avx512"), self.execute())

    def test_failed_build_or_either_acceptance_monitor_prevents_publication(self):
        for script in ("software_controller.py", "runtime_controller.py", "gpu_feature_controller.py", "abacus_benchmark.py"):
            with self.subTest(script=script):
                commands = self.execute(fail_monitor=script)
                self.assertEqual(commands[-1], (script, "monitor"))
                self.assertNotIn(("software_controller.py", "publish"), commands)

    def test_cpu_runs_automatic_multinode_acceptance_before_publication(self):
        self.assertEqual(self.execute(target="dsprhbm"), [
            ("software_controller.py", "submit"), ("software_controller.py", "monitor"),
            ("runtime_controller.py", "submit"), ("runtime_controller.py", "monitor"),
            *[("abacus_benchmark.py", op) for _ in range(3) for op in ("prepare", "submit", "monitor")],
            ("software_controller.py", "publish"),
        ])

    def test_cp2k_uses_its_build_contract_without_abacus_acceptance(self):
        self.assertEqual(self.execute(software="cp2k", target="dsprhbm"), [
            ("software_controller.py", "submit"), ("software_controller.py", "monitor"),
            ("software_controller.py", "publish"),
        ])


if __name__ == "__main__":
    unittest.main()
