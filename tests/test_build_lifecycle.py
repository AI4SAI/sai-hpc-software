"""The build monitor creates candidates; CI publishes only after acceptance."""
import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
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
from delivery_layout import artifact_path
from release_contract import make_identity
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

    def configure(self, software, target="dsprhbm"):
        self.software = software
        # Keep this fixture independent of in-flight GPU controller edits.
        for name in controller.contract_files(software):
            path = self.control / name
            if path.exists():
                path.chmod(0o644)
            path.write_text("# trusted contract fixture: " + name + "\n")
        (self.control / software).chmod(0o555)
        identity = make_identity(software, "development", "develop" if software == "abacus" else "master",
                                 "a" * 40, "v1", controller.recipe_fingerprint(software), target)
        self.request = {"software": software, "version": "v1", "target": target,
                        "sha": "a" * 40, "controller": str(self.control),
                        "identity": identity, "track": identity["track"],
                        "source_ref": identity["source_ref"],
                        "contract_schema": controller.CONTRACT_SCHEMA,
                        "recipe_sha256": controller.recipe_fingerprint(software)}
        self.image = artifact_path(self.root, identity, "build")
        self.image.parent.mkdir(parents=True, exist_ok=True)
        self.image.write_bytes(b"candidate SIF")
        (self.task / "artifact.path").write_text(str(self.image) + "\n")
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
                                                  target=self.request["target"], sha="a" * 40,
                                                  track=self.request["track"],
                                                  source_ref=self.request["source_ref"]))
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

    def test_cp2k_build_success_never_publishes_or_hits_cache_even_with_forged_flags(self):
        for target in ("dsprhbm", "4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
            with self.subTest(target=target):
                self.configure("cp2k", target)
                self.assertEqual(self.monitor(), 0)
                sidecar = self.image.with_suffix(".json")
                manifest = json.loads(sidecar.read_text())
                self.assertTrue(manifest["build_verified"])
                self.assertFalse(manifest["published"])
                self.assertFalse(manifest["verified"])
                with self.assertRaisesRegex(ValueError, "scientific acceptance is not registered"):
                    self.publish()
                manifest.update(verified=True, published=True)
                sidecar.write_text(json.dumps(manifest))
                self.assertEqual(self.lookup(), {})
                with self.assertRaisesRegex(ValueError, "scientific acceptance is not registered"):
                    self.publish()
                self.assertFalse((self.image.parent / "current.sif").exists())
                self.assertFalse((self.image.parent / "current.module").exists())
                self.assertFalse((self.root / "modulefiles").exists())
                forged = sidecar.read_bytes()
                self.assertEqual(self.monitor("FAILED", "1:0"), 1)
                self.assertEqual(sidecar.read_bytes(), forged)
                self.assertEqual(self.lookup(), {})
                with self.assertRaisesRegex(ValueError, "build status or provenance"):
                    self.publish()

    def test_unregistered_software_has_no_empty_acceptance_bypass(self):
        for software in ("cp2k", "gpumd", "deepmd-kit", "lammps", "unknown"):
            with self.subTest(software=software), self.assertRaisesRegex(ValueError, "not registered"):
                controller.required_acceptance(software, "dsprhbm")

    def test_manual_cp2k_candidate_submission_does_not_require_publication_registration(self):
        self.configure("cp2k")
        args = argparse.Namespace(software="cp2k", run_id="manual-candidate", sha="a" * 40,
                                  version="v1", target="dsprhbm", track="development",
                                  source_ref="master", jobs=None, resume_run="")
        with patch.object(controller, "render_job", return_value="#!/bin/bash\nexit 0\n"), \
                patch.object(controller, "call", return_value=subprocess.CompletedProcess([], 0, stdout="101\n")), \
                redirect_stdout(io.StringIO()):
            controller.submit(args)
        request = json.loads((self.root / "runs/manual-candidate/request.json").read_text())
        self.assertEqual(request["software"], "cp2k")
        self.assertEqual(request["identity"]["partition"], "DSPRHBM")
        self.assertEqual((self.root / "runs/manual-candidate/job.id").read_text(), "101\n")
        self.assertFalse((self.root / "modulefiles").exists())


class CiLifecycleTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".test-work"
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def execute(self, *, software="abacus", target="4v100-avx512", fail_monitor=None,
                event="workflow_dispatch", version="v1", track="development",
                prior=None, alter_identity=None, published_path=None, resume=""):
        commands = []
        self.remote_commands = []
        self.uploads = []
        upstream = "a" * 40
        source_ref = "develop" if software == "abacus" else "master"
        identity = make_identity(software, track, source_ref, upstream, version, "c" * 64, target)
        run_id = f"123-1-{track}-{target}-2026-09-13-" + upstream[:12]
        expected_artifact = artifact_path(Path("/home/testuser/sai-hpc-software"), identity, run_id)
        environment = {"SOFTWARE": software, "TARGET": target, "SOURCE_SHA": upstream,
                       "GITHUB_SHA": "b" * 40, "SOFTWARE_VERSION": version,
                       "SOURCE_REF": source_ref, "RELEASE_TRACK": track,
                       "REMOTE_USER": "testuser", "GITHUB_RUN_ID": "123",
                       "GITHUB_RUN_ATTEMPT": "1", "RUNNER_TEMP": str(self.root),
                       "GITHUB_EVENT_NAME": event, "RESUME_RUN": resume}

        def fake_run(argv, **kwargs):
            argv = [str(value) for value in argv]
            stdout = ""
            if argv[0] == "ssh":
                remote = shlex.split(argv[-1])
                if remote[0] == "python3":
                    script, operation = Path(remote[1]).name, remote[2]
                    commands.append((script, operation))
                    self.remote_commands.append((script, remote[2:]))
                    if operation == "monitor" and script == fail_monitor:
                        raise subprocess.CalledProcessError(1, argv)
                    if (script, operation) == ("source_cache.py", "inventory"):
                        stdout = json.dumps({"cache_shas": [upstream]})
                    elif (script, operation) == ("software_controller.py", "identity"):
                        stdout = json.dumps(identity if alter_identity is None else alter_identity(identity))
                    elif (script, operation) == ("software_controller.py", "lookup"):
                        stdout = json.dumps(prior or {})
            elif argv[0] == "scp" and argv[-2].endswith("/artifact.path"):
                Path(argv[-1]).write_text(str(published_path or expected_artifact) + "\n")
            elif argv[0] == "scp":
                self.uploads.append(Path(argv[-2]).name)
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

        with patch.dict(os.environ, environment, clear=True), \
                patch.object(ci, "datetime") as clock, \
                patch.object(ci, "run", side_effect=fake_run), \
                patch.object(ci.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
                redirect_stdout(io.StringIO()):
            clock.now.return_value = datetime(2026, 9, 13, 23, 59, 59, tzinfo=timezone.utc)
            if fail_monitor:
                with self.assertRaises(subprocess.CalledProcessError):
                    ci.main()
            else:
                ci.main()
            clock.now.assert_called_once_with(timezone.utc)
        return [command for command in commands if command[0] != "source_cache.py"]

    def test_gpu_publish_occurs_after_both_acceptance_monitors(self):
        self.assertEqual(self.execute(), [
            ("software_controller.py", "identity"),
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
            ("software_controller.py", "identity"),
            ("software_controller.py", "submit"), ("software_controller.py", "monitor"),
            ("runtime_controller.py", "submit"), ("runtime_controller.py", "monitor"),
            *[("abacus_benchmark.py", op) for _ in range(3) for op in ("prepare", "submit", "monitor")],
            ("software_controller.py", "publish"),
        ])

    def test_benchmarks_use_the_same_canonical_candidate_as_the_build(self):
        self.execute()
        for script, arguments in self.remote_commands:
            if script == "abacus_benchmark.py" and arguments[0] == "prepare":
                self.assertEqual(arguments[arguments.index("--artifact") + 1],
                                 (self.root / "results/artifact.path").read_text().strip())
        self.assertEqual([op for script, op in self.execute(target="dsprhbm") if script == "abacus_benchmark.py"], [
            *[op for _ in range(3) for op in ("prepare", "submit", "monitor")],
        ])

    def test_cp2k_manual_build_is_candidate_only_and_has_no_publication(self):
        for target in ("dsprhbm", "4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
            self.assertEqual(self.execute(software="cp2k", target=target), [
                ("software_controller.py", "identity"),
                ("software_controller.py", "submit"), ("software_controller.py", "monitor"),
            ])
            candidate = json.loads((self.root / "results/candidate-only.json").read_text())
            self.assertFalse(candidate["published"])
            self.assertFalse(candidate["verified"])
            self.assertEqual(candidate["identity"]["target"], target)
            self.assertIn("scientific acceptance is not registered", candidate["reason"])
            self.assertEqual(candidate["artifact"], (self.root / "results/artifact.path").read_text().strip())

    def test_cp2k_schedule_or_nonmanual_event_fails_before_remote_commands(self):
        for event in ("schedule", "push", "pull_request", ""):
            with self.subTest(event=event), self.assertRaisesRegex(ValueError, "manual candidate-only"):
                self.execute(software="cp2k", event=event)
            self.assertEqual(self.remote_commands, [])
            self.assertEqual(self.uploads, [])

    def test_ci_uploads_identity_dependencies_before_requesting_remote_identity(self):
        self.execute()
        self.assertTrue({"release_contract.py", "resolve_source.py", "delivery_layout.py"} <= set(self.uploads))
        self.assertEqual(self.remote_commands[0], ("software_controller.py", [
            "identity", "abacus", "a" * 40, "v1", "4v100-avx512",
            "--track", "development", "--source-ref", "develop"]))
        self.assertEqual(json.loads((self.root / "results/identity.json").read_text())["recipe_sha256"], "c" * 64)

    def test_long_version_is_preserved_but_not_part_of_run_id(self):
        version = "v" * 1000
        self.execute(version=version, track="prerelease", resume="previous-run")
        submit = next(argv for script, argv in self.remote_commands
                      if (script, argv[0]) == ("software_controller.py", "submit"))
        self.assertEqual(submit[2], "123-1-prerelease-4v100-avx512-2026-09-13-" + "a" * 12)
        self.assertLess(len(submit[2]), 100)
        self.assertEqual(submit[4], version)
        self.assertEqual(submit[-6:], ["--track", "prerelease", "--source-ref", "develop",
                                      "--resume-run", "previous-run"])
        runtime = next(argv for script, argv in self.remote_commands
                       if (script, argv[0]) == ("runtime_controller.py", "submit"))
        self.assertEqual(runtime[2:], [version, "4v100-avx512", "--build-run-id", submit[2]])
        gpu = next(argv for script, argv in self.remote_commands
                   if (script, argv[0]) == ("gpu_feature_controller.py", "submit"))
        self.assertEqual(gpu[2:], runtime[2:])
        self.assertLessEqual(len(runtime[1]), 128)
        self.assertLessEqual(len(gpu[1]), 128)

    def test_schedule_identity_and_lookup_use_same_resolved_provenance(self):
        prior = {"artifact": "/trusted/prior.sif"}
        self.assertEqual(self.execute(event="schedule", track="release", prior=prior), [
            ("software_controller.py", "identity"), ("software_controller.py", "lookup")])
        self.assertEqual(self.remote_commands[-1], ("software_controller.py", [
            "lookup", "abacus", "v1", "4v100-avx512", "a" * 40,
            "--track", "release", "--source-ref", "develop"]))
        self.assertEqual((self.root / "results/artifact.path").read_text(), prior["artifact"] + "\n")

    def test_schedule_cache_miss_preserves_identity_flags_for_submission(self):
        self.execute(event="schedule", track="release")
        submit = next(argv for script, argv in self.remote_commands
                      if (script, argv[0]) == ("software_controller.py", "submit"))
        self.assertEqual(submit[-4:], ["--track", "release", "--source-ref", "develop"])

    def test_remote_identity_mismatch_or_forged_path_stops_before_submission(self):
        for field, value in (("software", "cp2k"), ("track", "release"), ("source_ref", "v2"),
                             ("source_sha", "d" * 40), ("source_version", "v2"), ("target", "dsprhbm")):
            def alter(identity):
                arguments = {key: identity[key] for key in (
                    "software", "track", "source_ref", "source_sha", "source_version", "recipe_sha256", "target")}
                arguments[field] = value
                return make_identity(**arguments)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "identity differs"):
                self.execute(alter_identity=alter)
            self.assertFalse(any(argv[0] == "submit" for _, argv in self.remote_commands))
        with self.assertRaises(ValueError):
            self.execute(alter_identity=lambda identity: dict(identity, install_prefix="/forged"))
        self.assertFalse(any(argv[0] == "submit" for _, argv in self.remote_commands))

    def test_published_artifact_must_match_canonical_layout(self):
        with self.assertRaisesRegex(ValueError, "artifact differs"):
            self.execute(published_path="/trusted/wrong-layout.sif")


if __name__ == "__main__":
    unittest.main()
