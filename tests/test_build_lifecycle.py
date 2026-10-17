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
import cp2k_spack
import abacus_dependencies as dependencies
from datetime import datetime, timezone
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
        for name in ('export_native.export_delivery', 'export_native.validate_export_record'):
            gate = patch(name, return_value={'fixture': 'export gate'})
            gate.start()
            self.addCleanup(gate.stop)
        self.configure("abacus")

    def configure(self, software):
        self.software = software
        # Keep this fixture independent of in-flight GPU controller edits.
        for name in controller.contract_files(software):
            (self.control / name).write_text("# trusted contract fixture: " + name + "\n")
        (self.control / software).chmod(0o555)
        identity = make_identity(software, "development", "develop" if software == "abacus" else "master",
                                 "a" * 40, "v1", controller.recipe_fingerprint(software), "dsprhbm")
        self.request = {"software": software, "version": "v1", "target": "dsprhbm",
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
                                                  target="dsprhbm", sha="a" * 40,
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

    def test_failed_build_status_invalidates_a_previously_valid_published_sidecar(self):
        # Isolate build-provenance revocation from scientific fixtures. CP2K
        # benchmark validation is covered separately and is mandatory in CI.
        acceptance = patch.object(controller, "validate_acceptance")
        acceptance.start()
        self.addCleanup(acceptance.stop)
        required = patch.object(controller, "required_acceptance", return_value=())
        required.start()
        self.addCleanup(required.stop)
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

    def execute(self, *, software="abacus", target="4v100-avx512", fail_monitor=None,
                event="workflow_dispatch", version="v1", track="development",
                prior=None, alter_identity=None, published_path=None, resume="", retry="false", phase="all", handoff=None, publish_failures=0, persisted_jobs=None):
        commands = []
        publish_remaining = publish_failures
        preflights = {}
        self.remote_commands = []
        self.uploads = []
        self.uploaded_files = {}
        self.transport = []
        upstream = "a" * 40
        source_ref = "develop" if software == "abacus" else "master"
        identity = make_identity(software, track, source_ref, upstream, version, "c" * 64, target)
        run_id = ("gpumd-" if software == "gpumd" else "") + f"123-1-{track}-{target}-2026-09-13-" + upstream[:12]
        expected_artifact = artifact_path(Path("/home/testuser/sai-hpc-software"), identity, run_id)
        environment = {"SOFTWARE": software, "TARGET": target, "SOURCE_SHA": upstream,
                       "GITHUB_SHA": "b" * 40, "SOFTWARE_VERSION": version,
                       "SOURCE_REF": source_ref, "RELEASE_TRACK": track,
                       "REMOTE_USER": "testuser", "GITHUB_RUN_ID": "123",
                       "GITHUB_RUN_ATTEMPT": "1", "RUNNER_TEMP": str(self.root),
                       "GITHUB_EVENT_NAME": event, "RESUME_RUN": resume, "RETRY_RELEASES": retry,
                       "CI_PHASE": phase, "CP2K_PHASE": "", "CI_RUN_ID": handoff or run_id,
                       "GITHUB_OUTPUT": str(self.root / 'github-output')}
        (self.root / 'github-output').write_text('')

        def fake_run(argv, **kwargs):
            nonlocal publish_remaining
            argv = [str(value) for value in argv]
            self.transport.append(argv)
            stdout = ""
            if argv[0] == "ssh":
                remote = shlex.split(argv[-1])
                if remote[0] == 'cat' and remote[1].endswith('/input/ci-handoff.json'):
                    stdout = json.dumps(dict(schema=1, run_id=run_id, workflow_run_id='123',
                                             controller_sha='b' * 40, identity=identity))
                if remote[:2] == ['python3', '-c']:
                    expected = json.loads(remote[6])
                    preflights[expected['run_id']] = expected
                    exists = False
                    if persisted_jobs is not None:
                        path = persisted_jobs / expected['run_id']
                        exists = path.exists()
                        if exists:
                            stored = json.loads((path / 'request.json').read_text())
                            if stored != expected or not (path / 'job.id').read_text().strip().isdigit():
                                raise subprocess.CalledProcessError(1, argv, stderr='existing job differs from immutable handoff')
                    return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(exists), stderr='')
                if remote[0] == "python3":
                    script, operation = Path(remote[1]).name, remote[2]
                    commands.append((script, operation))
                    self.remote_commands.append((script, remote[2:]))
                    if persisted_jobs is not None and operation == 'submit':
                        selected_run = remote[4] if script == 'software_controller.py' else remote[3]
                        if selected_run in preflights:
                            path = persisted_jobs / selected_run
                            if path.exists():
                                raise subprocess.CalledProcessError(1, argv, stderr='run already submitted')
                            path.mkdir(parents=True)
                            (path / 'request.json').write_text(json.dumps(preflights[selected_run]))
                            (path / 'job.id').write_text('12345')
                    if script == 'software_controller.py' and operation == 'publish' and publish_remaining:
                        publish_remaining -= 1
                        raise subprocess.CalledProcessError(3, argv)
                    if operation == "monitor" and script == fail_monitor:
                        raise subprocess.CalledProcessError(1, argv)
                    if (script, operation) == ("source_cache.py", "inventory"):
                        stdout = json.dumps({"cache_shas": [upstream]})
                    elif (script, operation) == ("software_controller.py", "identity"):
                        stdout = json.dumps(identity if alter_identity is None else alter_identity(identity))
                    elif script == "release_contract.py":
                        stdout = json.dumps({"build": not bool(prior), "skipped": prior or []})
            elif argv[0] == "scp" and argv[-2].endswith("/artifact.path"):
                Path(argv[-1]).write_text(str(published_path or expected_artifact) + "\n")
            elif argv[0] == "scp":
                for source in argv[argv.index('-P') + 2:-1]:
                    self.uploads.append(Path(source).name)
                    name = Path(source).name if argv[-1].endswith('/') else Path(argv[-1]).name
                    self.uploaded_files[name] = Path(source)
            return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

        with patch.dict(os.environ, environment, clear=True), \
                patch.object(cp2k_spack, "cache_source_updates", return_value=[
                    self.root / "cp2k-updates/libwignernj-0.8.0.tar.gz",
                    self.root / "cp2k-updates/tblite-0.6.0.tar.xz"]), \
                patch.object(dependencies, "cache_archives") as archives, \
                patch.object(ci, "datetime") as clock, \
                patch.object(ci, "run", side_effect=fake_run), \
                patch.object(ci.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)), \
                redirect_stdout(io.StringIO()):
            clock.now.return_value = datetime(2026, 9, 13, 23, 59, 59, tzinfo=timezone.utc)
            if fail_monitor or publish_failures > 1:
                with self.assertRaises(subprocess.CalledProcessError):
                    ci.main()
            else:
                ci.main()
            clock.now.assert_called_once_with(timezone.utc)
            self.archive_calls = archives.call_args_list
        return [command for command in commands if command[0] != "source_cache.py"]

    def test_stage_handoff_never_reuploads_or_reclaims_release(self):
        calls = self.execute(software='cp2k', phase='dependencies', track='release')
        self.assertEqual(calls[-2:], [('cp2k_dependency_stage.py', 'submit'), ('cp2k_dependency_stage.py', 'monitor')])
        self.assertIn('run_id=', (self.root / 'github-output').read_text())
        calls = self.execute(software='cp2k', phase='application', track='release')
        self.assertEqual(calls, [('software_controller.py', 'identity'),
                                ('software_controller.py', 'submit'), ('software_controller.py', 'monitor')])
        self.assertEqual(self.uploads, [])
        submit = self.remote_commands[1][1]
        self.assertIn('--dependencies-run', submit)
        calls = self.execute(software='cp2k', phase='acceptance', track='release')
        self.assertNotIn(('software_controller.py', 'submit'), calls)
        self.assertNotIn(('cp2k_dependency_stage.py', 'submit'), calls)
        self.assertEqual(self.uploads, [])
        self.assertEqual(calls[-1], ('software_controller.py', 'publish'))

    def test_trusted_controller_uses_one_transfer_before_fingerprinting(self):
        for name, target in (('cp2k', '16v100-avx2'), ('abacus', '4v100-avx512'),
                             ('gpumd', '16v100-avx2')):
            with self.subTest(software=name):
                self.execute(software=name, target=target)
                transfers = [(i, argv) for i, argv in enumerate(self.transport)
                             if argv[0] == 'scp' and '/controller/' in argv[-1]]
                self.assertEqual(len(transfers), 1)
                position, transfer = transfers[0]
                sources = [Path(value) for value in transfer[transfer.index('-P') + 2:-1]]
                self.assertTrue(set(controller.contract_files(name)) <= {path.name for path in sources})
                for executable in ci.runtime_launchers(name):
                    path = next(path for path in sources if path.name == executable)
                    self.assertEqual(path.read_bytes(), (ROOT / 'controller' / (name + '_runtime.sh')).read_bytes())
                identity_position = next(i for i, argv in enumerate(self.transport)
                                         if argv[0] == 'ssh' and 'software_controller.py identity' in argv[-1])
                self.assertLess(position, identity_position)
    def test_cross_workflow_handoff_is_rejected_before_transport(self):
        with self.assertRaisesRegex(ValueError, 'handoff does not belong'):
            self.execute(phase='acceptance', handoff='124-1-development-4v100-avx512-2026-09-13-' + 'a'*12)
        self.assertEqual(self.transport, [])

    def test_performance_repeats_once_on_same_image_then_blocks(self):
        for failures in (1, 2):
            calls = self.execute(publish_failures=failures)
            self.assertEqual(calls.count(('software_controller.py', 'publish')), 2)
            prepares = [args for name, args in self.remote_commands
                        if name == 'abacus_benchmark.py' and args[0] == 'prepare']
            self.assertEqual(len(prepares), 6)
            self.assertTrue(all(args[1].endswith('-retest') for args in prepares[3:]))
            self.assertEqual(len({args[args.index('--artifact')+1] for args in prepares}), 1)
            self.assertEqual((self.root / 'results/artifact.path').exists(), failures == 1)
            (self.root / 'results/artifact.path').unlink(missing_ok=True)

    def test_locked_updates_use_runner_download_and_existing_upload_before_submission(self):
        self.execute()
        self.assertEqual([call.args for call in self.archive_calls], [(self.root / "abacus-updates",)])
        updated = [item["file"] for item in dependencies.load_lock()["archives"] if "url" in item]
        submitted = next(index for index, argv in enumerate(self.transport)
                         if argv[0] == "ssh" and "software_controller.py submit" in argv[-1])
        for name in updated:
            uploads = [index for index, argv in enumerate(self.transport)
                       if argv[0] == "scp" and argv[-2] == str(self.root / "abacus-updates" / name)]
            self.assertEqual(len(uploads), 1)
            self.assertLess(uploads[0], submitted)
            self.assertTrue(self.transport[uploads[0]][-1].endswith("/input/abacus-updates/" + name))
        self.execute(software="cp2k")
        self.assertEqual(self.archive_calls, [])
        self.assertTrue(set(updated).isdisjoint(self.uploads))
        submitted = next(index for index, argv in enumerate(self.transport)
                         if argv[0] == "ssh" and "cp2k_dependency_stage.py submit" in argv[-1])
        for name in ("libwignernj-0.8.0.tar.gz", "tblite-0.6.0.tar.xz"):
            uploads = [index for index, argv in enumerate(self.transport)
                       if argv[0] == "scp" and argv[-2] == str(self.root / "cp2k-updates" / name)]
            self.assertEqual(len(uploads), 1)
            self.assertLess(uploads[0], submitted)
            self.assertTrue(self.transport[uploads[0]][-1].endswith("/input/cp2k-updates/" + name))
        self.execute(event="schedule", track="release", prior=[{"previous_run": "failed"}])
        self.assertEqual(self.archive_calls, [])
        self.execute(event="schedule", prior=[{"previous_run": "development"}])
        self.assertEqual([call.args for call in self.archive_calls], [(self.root / "abacus-updates",)])

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

    def test_cp2k_requires_benchmark_before_publication(self):
        for target in ("dsprhbm", "4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
            self.assertEqual(self.execute(software="cp2k", target=target), [
                ("software_controller.py", "identity"),
                ("cp2k_dependency_stage.py", "submit"), ("cp2k_dependency_stage.py", "monitor"),
                ("software_controller.py", "submit"), ("software_controller.py", "monitor"),
                *([("cp2k_benchmark.py", "wait-reference")] if target == 'dsprhbm' else []),
                ("cp2k_benchmark.py", "submit"), ("cp2k_benchmark.py", "monitor"),
                ("software_controller.py", "publish"),
            ])
        failed = self.execute(software="cp2k", fail_monitor="cp2k_benchmark.py")
        self.assertNotIn(("software_controller.py", "publish"), failed)

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

    def test_unchanged_releases_skip_for_both_events_without_claiming_an_artifact(self):
        for event in ("schedule", "workflow_dispatch"):
            for track in ("release", "prerelease"):
                with self.subTest(event=event, track=track):
                    calls = self.execute(event=event, track=track, prior=[{"previous_run": "failed-build"}])
                    self.assertEqual([script for script, _ in calls],
                                     ["software_controller.py", "release_contract.py"])
                    script, argv = self.remote_commands[-1]
                    claimed = json.loads(argv[2])[0]
                    self.assertEqual(claimed["track"], track)
                    self.assertEqual(claimed["source_sha"], "a" * 40)
                    self.assertNotIn("--retry", argv)
                    self.assertFalse((self.root / "results/artifact.path").exists())
                    self.assertFalse(json.loads((self.root / "results/build-attempt.json").read_text())["build"])

    def test_development_always_builds_for_both_events_even_with_prior_attempt(self):
        for event in ("schedule", "workflow_dispatch"):
            with self.subTest(event=event):
                calls = self.execute(event=event, prior=[{"previous_run": "prior"}])
                self.assertIn(("software_controller.py", "submit"), calls)
                self.assertFalse(any(script == "release_contract.py" or op == "lookup" for script, op in calls))

    def test_release_retry_uses_explicit_input_through_reusable_calls(self):
        for event, retry, resume, expected in (("schedule", "true", "", True),
                                              ("workflow_dispatch", "false", "", False),
                                              ("workflow_dispatch", "true", "", True),
                                              ("workflow_dispatch", "false", "failed", True)):
            with self.subTest(event=event, retry=retry, resume=resume):
                self.execute(event=event, track="release", retry=retry, resume=resume)
                argv = next(argv for script, argv in self.remote_commands if script == "release_contract.py")
                self.assertEqual("--retry" in argv, expected)

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
