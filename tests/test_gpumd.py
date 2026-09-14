"""GPUMD fail-closed registration, native target, science and cache contracts."""
import argparse
import copy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))
import gpumd_acceptance as acceptance
import gpumd_science as science
import ci
import software_controller as build
import delivery_layout as layout
import export_native
from release_contract import make_identity, TRACKS
from source_cache import checksum
from remote_controller import TARGETS
import test_build_lifecycle as lifecycle


class GpumdContractTests(unittest.TestCase):
    def test_only_three_native_gpu_targets_are_registered(self):
        self.assertEqual(set(acceptance.GPU_TARGETS), {"4v100-avx512", "16v100-avx2", "8v100v0-avx512"})
        for target in acceptance.GPU_TARGETS:
            self.assertEqual(build.required_acceptance("gpumd", target), ("gpumd_science",))
            self.assertEqual(TARGETS[target]["cuda_arch"], "70")
        for target in ("dsprhbm", "a100", "unknown"):
            with self.assertRaises(ValueError):
                build.required_acceptance("gpumd", target)
        self.assertEqual(TARGETS["8v100v0-avx512"]["cpu_arch"], "skylake-avx512")
        self.assertEqual(TARGETS["8v100v0-avx512"]["dependency_isa"], "avx2")

    def test_all_scientific_build_and_launcher_files_are_hashed(self):
        contract = build.contract_files("gpumd")
        self.assertTrue({"gpumd_environment.sh", "gpumd_build.sh", "gpumd_container_entry.sh",
            "gpumd_science.py", "gpumd_deepmd_probe.py", "gpumd_acceptance.py", "gpumd", "nep", "gnep"} <= set(contract))
        original = build.recipe_fingerprint("gpumd", ROOT / "controller")
        with tempfile.TemporaryDirectory() as temporary:
            control = Path(temporary)
            for name in contract:
                shutil.copyfile(ROOT / "controller" / ("gpumd_runtime.sh" if name in ("gpumd", "nep", "gnep") else name), control / name)
            self.assertEqual(original, build.recipe_fingerprint("gpumd", control))
            for name in ("gpumd_science.py", "gpumd_deepmd_probe.py", "nep"):
                previous = (control / name).read_text()
                (control / name).write_text(previous + "\n# changed\n")
                self.assertNotEqual(original, build.recipe_fingerprint("gpumd", control))
                (control / name).write_text(previous)

    def test_missing_numerical_benchmark_proof_blocks_publication(self):
        with self.assertRaisesRegex(ValueError, "scientific/parity/benchmark"):
            acceptance.validate_manifest({"software": "gpumd", "target": "4v100-avx512"})

    def test_rendered_build_never_uses_host_tmp_or_host_source_tree(self):
        for target in acceptance.GPU_TARGETS:
            args = argparse.Namespace(software="gpumd", target=target, run_id="gpumd-test", sha="a" * 40,
                version="master-2026-09-13", track="development", source_ref="master",
                jobs=6, minutes=120, overlay_mb=8192, resume_run=None)
            script = build.render_job(args)
            subprocess.run(["bash", "-n"], input=script, text=True, check=True)
            self.assertIn("/control/gpumd_container_entry.sh build gpumd", script)
            self.assertIn("/opt/apps:/opt/apps:ro", script)
            self.assertIn("--overlay", script)
            self.assertIn("APPTAINERENV_CUDA_VISIBLE_DEVICES", script)
            self.assertIn(f"/opt/sai_config/mps_mapping.d/{TARGETS[target]['partition']}.bash", script)
            self.assertNotIn("#SBATCH --mem", script)
            self.assertNotIn("#SBATCH --cpus-per-task", script)
            self.assertNotIn("TMPDIR=/tmp", script)

    def test_science_script_uses_single_gpu_and_same_image_baseline(self):
        request = {"target": "8v100v0-avx512", "version": "master-a", "run_id": "gpumd-test-science",
                   "build_run_id": "build",
                   "launcher": "/home/test/sai-hpc-software/controller/gpumd-test/gpumd"}
        request["identity"] = make_identity("gpumd", "development", "master", "a" * 40,
                                             request["version"], "b" * 64, request["target"])
        request["artifact"] = str(layout.artifact_path(acceptance.ROOT, request["identity"], "build"))
        script = acceptance.render_job(request, Path("/home/test/sai-hpc-software/runtime-tests/gpumd-test-science"))
        subprocess.run(["bash", "-n"], input=script, text=True, check=True)
        self.assertIn("#SBATCH --partition=8V100V0", script)
        self.assertIn("#SBATCH --gpus-per-node=1", script)
        self.assertIn("gpumd_science.py run", script)
        self.assertIn('"CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES}"', script)
        self.assertNotIn("TMPDIR=/tmp", script)
        self.assertIn("/results:/work:rw", script)
        self.assertIn(request["identity"]["install_prefix"], script)
        self.assertNotIn("/gpumd/master-a/", script)

    def test_comparison_rejects_nan_wrong_shape_and_excess_error(self):
        self.assertLess(science.compare([[1.0]], [[1.00001]], 0.001, "test"), 0.001)
        for actual in ([[float("nan")]], [[1, 2]], [[3]], []):
            with self.assertRaises(ValueError):
                science.compare(actual, [[1]], 0.001, "test")
        with self.assertRaises(ValueError):
            science.compare([[1, float("nan")]], [[1, 2]], 0.001, "later NaN")

    def test_explicit_native_and_optional_feature_baseline(self):
        environment = (ROOT / "controller/gpumd_environment.sh").read_text()
        recipe = (ROOT / "controller/gpumd_build.sh").read_text()
        self.assertIn("GPUMD_CPU_ARCH=native", environment)
        self.assertIn("GPUMD_EXPECTED_CPU_ARCH=skylake-avx512", environment)
        self.assertIn("module load cuda/12.9.1 deepmd-kit/3.2.0 gcc/13.3.0", environment)
        self.assertNotIn("module load elpa", environment)
        self.assertIn("-DUSE_DEEPMD -DUSE_PLUMED", recipe)
        self.assertIn("GPUMD_SYSTEM_BLAS_PATH=/usr/lib/x86_64-linux-gnu/blas:/usr/lib/x86_64-linux-gnu/lapack", environment)
        self.assertIn('plumed_dependencies=$(ldd "$PLUMED_KERNEL")', environment)
        self.assertIn("-Xlinker=-rpath-link -Xlinker=$GPUMD_SYSTEM_BLAS_PATH", recipe)
        self.assertIn("share/gpumd/src", recipe)
        entry = (ROOT / "controller/gpumd_container_entry.sh").read_text()
        self.assertIn('gpumd_science.py portable "$INSTALL_PREFIX" /workspace/gpumd-portability', entry)

    def test_portability_copy_cannot_escape_the_build_overlay(self):
        with self.assertRaisesRegex(ValueError, "restricted to its overlay path"):
            science.portable_check(Path("/opt/software/gpumd/v1/target"), Path("/tmp/copy"))


class GpumdCiTests(unittest.TestCase):
    setUp = lifecycle.CiLifecycleTests.setUp
    execute = lifecycle.CiLifecycleTests.execute

    def test_each_launcher_upload_occurs_once_before_becoming_readonly(self):
        self.assertEqual(ci.runtime_launchers("gpumd"), ("gpumd", "nep", "gnep"))
        for software in ("gpumd", "abacus", "cp2k"):
            names = ci.runtime_launchers(software)
            self.assertEqual(len(names), len(set(names)))
    def test_gpumd_requires_science_and_benchmark_before_publication(self):
        self.assertEqual(self.execute(software="gpumd"), [
            ("software_controller.py", "identity"),
            ("software_controller.py", "submit"), ("software_controller.py", "monitor"),
            ("gpumd_acceptance.py", "submit"), ("gpumd_acceptance.py", "monitor"),
            ("software_controller.py", "publish")])

    def test_failed_gpumd_science_cannot_publish(self):
        commands = self.execute(software="gpumd", fail_monitor="gpumd_acceptance.py")
        self.assertNotIn(("software_controller.py", "publish"), commands)

    def test_all_native_metadata_helpers_are_uploaded_and_fingerprinted(self):
        self.execute(software="gpumd")
        required = {"gpumd_science.py", "export_native.py", "native_module.py",
                    "release_contract.py", "resolve_source.py", "delivery_layout.py"}
        self.assertTrue(required <= set(self.uploads))
        self.assertTrue(required <= set(build.contract_files("gpumd")))
        self.assertEqual(self.uploads.count("export_native.py"), 1)

    def test_gpumd_run_and_scientific_proof_share_the_dated_utc_run_id(self):
        self.execute(software="gpumd", target="8v100v0-avx512", track="prerelease")
        submitted = next(args for script, args in self.remote_commands
                         if script == "software_controller.py" and args[0] == "submit")
        run_id = "gpumd-123-1-prerelease-8v100v0-avx512-2026-09-13-" + "a" * 12
        self.assertEqual(submitted[2], run_id)
        scientific = next(args for script, args in self.remote_commands
                          if script == "gpumd_acceptance.py" and args[0] == "submit")
        self.assertEqual(scientific[1], run_id + "-science")
        self.assertEqual(scientific[-2:], ["--build-run-id", run_id])
        self.assertLessEqual(len(scientific[1]), 128)

    def test_isolated_uploaded_snapshot_imports_and_resolves_its_own_identity(self):
        self.execute(software="gpumd")
        snapshot = self.root / "uploaded-control"
        snapshot.mkdir()
        for name, source in self.uploaded_files.items():
            shutil.copyfile(source, snapshot / name)
        self.assertTrue(set(build.contract_files("gpumd")) <= self.uploaded_files.keys())
        command = [sys.executable, "-E", "-s"]
        for script in ("software_controller.py", "gpumd_acceptance.py", "gpumd_science.py",
                       "delivery_layout.py", "export_native.py"):
            subprocess.run([*command, snapshot / script, "--help"], cwd=self.root,
                           capture_output=True, text=True, check=True)
        result = subprocess.run([*command, snapshot / "software_controller.py", "identity", "gpumd",
            "a" * 40, "master-2026-09-13", "4v100-avx512", "--track", "development",
            "--source-ref", "master"], cwd=self.root, capture_output=True, text=True, check=True)
        identity = json.loads(result.stdout)
        self.assertEqual(identity["recipe_sha256"], build.recipe_fingerprint("gpumd", snapshot))
        self.assertEqual(identity["partition"], "4V100")
        # Removing one dependency must fail rather than importing the checkout.
        (snapshot / "source_cache.py").unlink()
        missing = subprocess.run([*command, snapshot / "gpumd_science.py", "--help"],
                                 cwd=self.root, capture_output=True, text=True)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("source_cache", missing.stderr)


class GpumdDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        for name, value in (("ROOT", self.root), ("CONTROL", ROOT / "controller")):
            patcher = patch.object(acceptance, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def entry(self, track="development", target="4v100-avx512"):
        identity = make_identity("gpumd", track, "master", "a" * 40,
                                 "master-2026-09-13", "b" * 64, target)
        mpi = "/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-" + identity["dependency_isa"]
        environment = dict(CUDA_HOME=science.CUDA, CUDA_PATH=science.CUDA,
            DEEPMD_ROOT=science.DEEPMD, PLUMED_KERNEL=science.SITE_LIBRARIES["libplumedKernel.so"],
            DP_CUDA_INFER="2", **science.THREADS, PATH=science.GCC + "/bin:" + science.CUDA + "/bin:/usr/bin:/bin",
            LOADEDMODULES=":".join((*science.MODULES, "deepmd-kit/3.2.0")),
            LD_LIBRARY_PATH=":".join((science.DEEPMD + "/lib", science.PLUMED + "/lib",
                                      science.BLAS, science.LAPACK, mpi + "/lib")))
        reports = {name: "libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0xabc)\n"
                   for name in (*science.COMMANDS, *science.SITE_LIBRARIES)}
        reports["libplumedKernel.so"] += "libmpi.so.40 => " + mpi + "/lib/libmpi.so.40 (0xdef)\n"
        entry = science.build_native_entry(identity, identity["install_prefix"], environment, reports)
        return entry, environment, reports

    def candidate(self, identity):
        artifact = layout.artifact_path(self.root, identity, "build")
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"synthetic candidate, no scientific acceptance")
        record = dict(identity=identity, software="gpumd", version=identity["source_version"],
            target=identity["target"], source_sha=identity["source_sha"],
            recipe_sha256=identity["recipe_sha256"], contract_schema=layout.CONTRACT_SCHEMA,
            artifact=str(artifact), sha256=checksum(artifact), build_verified=True)
        artifact.with_suffix(".json").write_text(json.dumps(record))
        return artifact, record

    def test_three_tracks_by_three_partitions_package_one_complete_prefix(self):
        prefixes = set()
        for track in TRACKS:
            for target in acceptance.GPU_TARGETS:
                entry, _, _ = self.entry(track, target)
                identity = entry["identity"]
                prefix = self.root / identity["install_prefix"].lstrip("/")
                for relative in (*entry["commands"].values(), "share/gpumd/src/main_nep/nep_specialized.cu",
                                 "share/gpumd/src/utilities/nep_utilities.cuh", "share/sai/portability/proof.json"):
                    path = prefix / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("# synthetic install fixture\n")
                    path.chmod(0o555 if relative.startswith("bin/") else 0o444)
                manifest = export_native.write_manifests([entry], self.root)
                self.assertEqual(export_native.read_installed_manifests([entry], self.root), manifest)
                self.assertEqual(export_native.inventory([entry], self.root), manifest)
                local = json.loads((prefix / "share/sai/manifest.json").read_text())
                self.assertEqual(local["schema"], 2)
                self.assertEqual(local["entry"]["identity"], identity)
                module = (prefix / "share/sai/native-module.tcl").read_text()
                self.assertIn(identity["install_prefix"], module)
                self.assertNotIn("apptainer", module.lower())
                self.assertNotIn("deepmd-kit/3.2.0", module)
                self.assertEqual(prefix.name, identity["partition"])
                self.assertTrue(all(row["path"].startswith(identity["install_prefix"].lstrip("/"))
                                    for row in local["files"]))
                artifact, record = self.candidate(identity)
                self.assertEqual(layout.load_artifact(self.root, artifact), record)
                with self.assertRaisesRegex(ValueError, "scientific/parity/benchmark"):
                    acceptance.validate_manifest(record, self.root, ROOT / "controller")
                prefixes.add(identity["install_prefix"])
        self.assertEqual(len(prefixes), 9)

    def test_dependency_and_environment_evidence_fail_closed(self):
        entry, environment, reports = self.entry()
        identity = entry["identity"]
        cases = [
            (dict(environment, GPUMD_SRC="/workspace/source/src"), reports),
            (dict(environment, LD_LIBRARY_PATH=environment["LD_LIBRARY_PATH"] + ":/workspace/lib"), reports),
            (dict(environment, LOADEDMODULES=environment["LOADEDMODULES"] + ":lammps/2026"), reports),
            (environment, dict(reports, gpumd="libdeepmd_cc.so => not found\n")),
            (environment, dict(reports, gpumd="libbad.so => /home/user/libbad.so (0xabc)\n")),
            (environment, {key: value for key, value in reports.items() if key != "nep"}),
        ]
        for env, observed in cases:
            with self.subTest(environment=env), self.assertRaises(ValueError):
                science.build_native_entry(identity, identity["install_prefix"], env, observed)
        with self.assertRaises(ValueError):
            science.build_native_entry(identity, identity["install_prefix"], environment, reports, ("gpumd", "nep"))
        altered = copy.deepcopy(identity)
        altered["partition"] = "DSPRHBM"
        with self.assertRaises(ValueError):
            science.build_native_entry(altered, altered["install_prefix"], environment, reports)

    def test_runtime_launcher_reuses_hash_and_rejects_mismatched_partition(self):
        entry, _, _ = self.entry()
        artifact, record = self.candidate(entry["identity"])
        binary = self.root / "test-bin/apptainer"
        binary.parent.mkdir()
        binary.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        binary.chmod(0o755)
        runtime = self.root / "runtime-tests/launcher"
        runtime.mkdir(parents=True)
        environment = dict(os.environ, PATH=str(binary.parent) + ":" + os.environ["PATH"],
            SAI_SOFTWARE_ROOT=str(self.root), SAI_GPUMD_IMAGE=str(artifact), SAI_GPUMD_EXECUTABLE="gpumd",
            SLURM_JOB_ID="123", SLURM_JOB_PARTITION=entry["identity"]["partition"], TMPDIR=str(runtime))
        command = ["bash", str(ROOT / "controller/gpumd_runtime.sh"), "example"]
        for _ in range(2):
            result = subprocess.run(command, env=environment, cwd=self.root, capture_output=True, text=True, check=True)
            self.assertIn(entry["identity"]["install_prefix"] + "/bin/gpumd", result.stdout)
        caches = list((self.root / "runtime/jobs/123").glob("artifact-check-*.json"))
        self.assertEqual(len(caches), 1)
        self.assertEqual(json.loads(caches[0].read_text())["sha256"], record["sha256"])
        environment["SLURM_JOB_PARTITION"] = "16V100"
        self.assertNotEqual(subprocess.run(command, env=environment, capture_output=True).returncode, 0)
        environment["SLURM_JOB_PARTITION"] = entry["identity"]["partition"]
        artifact.write_bytes(b"changed candidate")
        self.assertNotEqual(subprocess.run(command, env=environment, capture_output=True).returncode, 0)

    def test_scientific_request_cannot_relabel_a_candidate(self):
        entry, _, _ = self.entry()
        artifact, _ = self.candidate(entry["identity"])
        request = dict(identity=entry["identity"], target=entry["identity"]["target"],
            version=entry["identity"]["source_version"], artifact=str(artifact), build_run_id="build",
            run_id="science", launcher=str(ROOT / "controller/gpumd_runtime.sh"))
        with patch.object(acceptance, "ROOT", self.root):
            acceptance.render_job(request, self.root / "runtime-tests/science")
            request["identity"] = self.entry(track="release")[0]["identity"]
            with self.assertRaisesRegex(ValueError, "canonical identity"):
                acceptance.render_job(request, self.root / "runtime-tests/science")

    def test_scientific_submission_pins_the_canonical_build_candidate(self):
        entry, _, _ = self.entry(track="prerelease", target="8v100v0-avx512")
        artifact, record = self.candidate(entry["identity"])
        build_task = self.root / "runs/build"
        build_task.mkdir(parents=True)
        (build_task / "artifact.path").write_text(str(artifact) + "\n")
        control = self.root / "controller/fixture"
        control.mkdir(parents=True)
        for name in ("gpumd", "gpumd_acceptance.py", "gpumd_science.py", "gpumd_deepmd_probe.py"):
            source = "gpumd_runtime.sh" if name == "gpumd" else name
            shutil.copyfile(ROOT / "controller" / source, control / name)
        args = argparse.Namespace(run_id="science", build_run_id="build", version=record["version"],
                                  target=record["target"])
        with patch.object(acceptance, "CONTROL", control), \
                patch.object(acceptance.subprocess, "check_output", return_value="123\n") as submit, \
                redirect_stdout(io.StringIO()):
            acceptance.submit(args)
        self.assertEqual(submit.call_args.args[0][0], "sbatch")
        task = self.root / "runtime-tests/science"
        request = json.loads((task / "request.json").read_text())
        self.assertEqual(request["identity"], entry["identity"])
        self.assertEqual(request["artifact_sha256"], record["sha256"])
        self.assertIn(entry["identity"]["install_prefix"], (task / "job.sbatch").read_text())
        self.assertFalse(artifact.parent.joinpath("current.sif").exists())


class GpumdWorkflowTests(unittest.TestCase):
    def test_release_retries_are_explicit_and_disabled_by_default(self):
        workflow = (ROOT / ".github/workflows/gpumd.yml").read_text()
        retry = workflow.split("      retry_releases:\n", 1)[1].split("\n  push:", 1)[0]
        self.assertIn("        type: boolean\n", retry)
        self.assertIn("        default: false", retry.splitlines())
        self.assertIn("          RETRY_RELEASES: ${{ inputs.retry_releases }}", workflow)

    def resolve(self, event="schedule", missing=(), tracks="development", override=""):
        workflow = (ROOT / ".github/workflows/gpumd.yml").read_text()
        snippet = textwrap.dedent(workflow.split("python3 - <<'PY'\n", 1)[1].split("\n          PY", 1)[0])
        def resolver(repository, ref):
            if ref in missing:
                raise ValueError("no " + ref + " available")
            return dict(sha="a" * 40, version="master-2026-09-13" if ref == "master" else ref, ref=ref)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "output"
            environment = dict(GITHUB_EVENT_NAME=event, GITHUB_OUTPUT=str(output), TRACKS=tracks,
                               TARGETS="4v100-avx512", SOURCE_REF=override)
            with patch.dict(os.environ, environment, clear=True), \
                    patch("resolve_source.resolve", side_effect=resolver), redirect_stdout(io.StringIO()):
                exec(compile(snippet, "gpumd.yml", "exec"), {})
            return json.loads(output.read_text().removeprefix("builds="))

    def test_schedule_resolves_three_channels_and_native_partitions(self):
        rows = self.resolve()
        self.assertEqual(len(rows), 9)
        self.assertEqual({row["track"] for row in rows}, set(TRACKS))
        self.assertEqual({row["target"] for row in rows}, set(acceptance.GPU_TARGETS))
        self.assertEqual(len(self.resolve(missing=("latest-prerelease",))), 6)

    def test_explicit_absent_track_and_ambiguous_override_fail(self):
        with self.assertRaises(ValueError):
            self.resolve("workflow_dispatch", missing=("latest-prerelease",), tracks="prerelease")
        with self.assertRaises(ValueError):
            self.resolve("workflow_dispatch", tracks="development,release", override="master")


class GpumdRawEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.report = {"host": "node", "job": "123", "gpu_visible": "0", "conditions": {}}
        for relative in science.required_results():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
        base_xyz = '2\nenergy=1\nCu 0 0 0 0 0 0\nCu 1 0 0 0 0 0\n'
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            if path.name in ("dump.xyz", "gold.xyz", "model.xyz"):
                path.write_text(base_xyz)
            elif path.name in ("run.in", "nep.in"):
                path.write_text("run 10000\n")
            elif path.name.endswith(".out"):
                path.write_text("0 0 0\n")
            elif path.name == "execution.json":
                path.write_text(json.dumps(dict(self.report, environment={}, wall_seconds=12)))
            elif path.name == "run.log":
                path.write_text("Time used for this run = 10 second.\nSpeed of this run = 2000000 atom*step/second.\n")
        for mode in ("candidate", "baseline"):
            for name in ("energy_train.out", "force_train.out", "virial_train.out"):
                for suffix in ("", "-repeat0", "-repeat1", "-repeat2"):
                    for prefix in ("", "gold-"):
                        (self.root / f"prediction-{mode}{suffix}/{prefix}{name}").write_text("0 0 0\n")
        for path in self.root.glob("throughput*/model.xyz"):
            path.write_text("2000\nfixture model\n")
        for mode in ("on", "off"):
            (self.root / f"training-{mode}-candidate/loss.out").write_text("1 0.01\n2 0.02\n")
        (self.root / "training-on-candidate/run.log").write_text("Compile specialized NEP training kernels (sm_70).\n")
        (self.root / "gnep-train-candidate/loss.out").write_text("1 0.01\n2 0.02\n")
        (self.root / "gnep-prediction-candidate/energy_train.out").write_text("0.025 0\n" * 4)
        (self.root / "gnep-prediction-candidate/force_train.out").write_text("0 0 0 0 0 0\n" * 160)
        (self.root / "gnep-static-candidate/dump.xyz").write_text("40\nenergy=1\n" + "Cu 0 0 0 0 0 0\n" * 40)
        (self.root / "plumed-candidate/colvar").write_text("0 1 1\n")
        (self.root / "plumed-candidate/dump.xyz").write_text('2\nenergy=1\nCu 0 0 0 2 0 0\nCu 1 0 0 -2 0 0\n')
        (self.root / "deepmd-input/reference.json").write_text(json.dumps({"energy": 1, "forces": [[0, 0, 0], [0, 0, 0]]}))

    def test_raw_fixture_recomputes_every_scientific_gate(self):
        checks, benchmark = science.recheck_results(self.root, self.report)
        self.assertEqual(checks["plumed-force-feedback"], 0)
        self.assertEqual(benchmark["md-throughput-candidate"]["median_atom_steps_per_second"], 2000000)

    def test_fake_summary_cannot_replace_missing_raw_gold_or_outputs(self):
        self.report["checks"] = {"static-candidate": True, "deepmd-candidate": True}
        for relative in ("static-candidate/gold.xyz", "deepmd-baseline/dump.xyz", "gnep-train-candidate/loss.out"):
            path = self.root / relative
            original = path.read_bytes()
            path.unlink()
            with self.assertRaisesRegex(ValueError, "mandatory GPUMD result"):
                science.recheck_results(self.root, self.report)
            path.write_bytes(original)

    def test_changed_raw_scientific_output_fails_even_with_green_summary(self):
        self.report["checks"] = {"verified": True}
        path = self.root / "static-candidate/dump.xyz"
        path.write_text(path.read_text().replace("energy=1", "energy=2"))
        with self.assertRaisesRegex(ValueError, "static energy"):
            science.recheck_results(self.root, self.report)

    def test_benchmark_nan_timing_and_changed_inputs_fail(self):
        path = self.root / "throughput-candidate-repeat0/execution.json"
        original = path.read_text()
        data = json.loads(original)
        data["wall_seconds"] = float("nan")
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "nonfinite MD"):
            science.recheck_results(self.root, self.report)
        path.write_text(original)
        (self.root / "throughput-candidate-repeat0/run.in").write_text("run 11000\n")
        with self.assertRaisesRegex(ValueError, "input differs"):
            science.recheck_results(self.root, self.report)

    def test_jit_silent_fallback_cannot_be_reported_as_accepted(self):
        path = self.root / "training-on-candidate/run.log"
        path.write_text(path.read_text() + "Warning: NEP training specialization disabled\n")
        with self.assertRaisesRegex(ValueError, "silently fell back"):
            science.recheck_results(self.root, self.report)


if __name__ == "__main__":
    unittest.main()
