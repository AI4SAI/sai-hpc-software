"""Exercise the real runtime CLI, including target defaults and proof metadata."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))
import runtime_controller as runtime
from delivery_layout import CONTRACT_SCHEMA, artifact_path
from release_contract import make_identity
from source_cache import checksum


class RuntimeCliTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".test-work"
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.control = self.root / "controller/reviewed/test"
        self.control.mkdir(parents=True)
        for name in ("runtime_controller.py", "remote_controller.py", "source_cache.py",
                     "release_contract.py", "resolve_source.py", "delivery_layout.py"):
            shutil.copy2(ROOT / "controller" / name, self.control / name)
        shutil.copy2(ROOT / "controller/abacus_runtime.sh", self.control / "abacus")
        (self.control / "abacus").chmod(0o555)
        self.bin = self.root / "test-bin"
        self.bin.mkdir()
        self.command("sbatch", "printf '12345\\n'")
        self.command("squeue", "exit 1")
        self.command("sacct", "printf '12345|COMPLETED|0:0|\\n'")
        self.env = dict(os.environ, SAI_SOFTWARE_ROOT=str(self.root),
                        PATH=str(self.bin) + os.pathsep + os.environ["PATH"])
        patcher = patch.object(runtime, "ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def command(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def candidate(self, target, *, legacy=False, build_verified=True):
        self.identity = make_identity("abacus", "development", "develop", "a" * 40,
                                      "v1", "b" * 64, target)
        artifact = (self.root / f"containers/software/abacus/v1/{target}/build.sif" if legacy else
                    artifact_path(self.root, self.identity, "build"))
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"test candidate SIF")
        manifest = {"software": "abacus", "artifact": str(artifact), "sha256": checksum(artifact),
                    "version": "v1", "target": target, "verified": legacy}
        request = {"software": "abacus", "version": "v1", "target": target, "sha": "a" * 40,
                   "controller": str(self.control), "recipe_sha256": "b" * 64}
        if not legacy:
            manifest.update(build_verified=build_verified, contract_schema=CONTRACT_SCHEMA,
                            source_sha="a" * 40, recipe_sha256="b" * 64, identity=self.identity)
            request.update(identity=self.identity, contract_schema=CONTRACT_SCHEMA,
                           track=self.identity["track"], source_ref=self.identity["source_ref"])
        artifact.with_suffix(".json").write_text(json.dumps(manifest))
        task = self.root / "runs/build"
        task.mkdir(parents=True, exist_ok=True)
        (task / "artifact.path").write_text(str(artifact) + "\n")
        (task / "request.json").write_text(json.dumps(request))
        return artifact

    def cli(self, *args, check=True):
        return subprocess.run([sys.executable, self.control / "runtime_controller.py", *args],
                              env=self.env, capture_output=True, text=True, check=check)

    def submit(self, target, *extra):
        return self.cli("submit", "runtime", "v1", target, "--build-run-id", "build", *extra)

    def task(self):
        return self.root / "runtime-tests/runtime"

    def scientific_results(self):
        """Simulate job output, separately from the fake scheduler's exit status."""
        request = json.loads((self.task() / "request.json").read_text())
        self.assertEqual(request["identity"], self.identity)
        results = self.task() / "results"
        (results / "ranks").mkdir(exist_ok=True)
        mpi_isa = runtime.TARGETS[request["target"]]["dependency_isa"]
        for rank in range(request["ranks"]):
            host = f"node-{rank // request['ranks_per_node']}"
            gpu = "0" if request["gpus_per_node"] else ""
            (results / f"ranks/rank-{rank}.tsv").write_text(
                f"{host}\t{rank}\t{request['target']}\t{request['artifact']}\t{gpu}"
                f"\t/opt/openmpi-{mpi_isa}\n")
        (results / "slurm-12345.log").write_text("MULTINODE_CONTAINER_MPI_VERIFIED\n")
        (results / "abacus.log").write_text("ABACUS calculation completed\n")
        case = self.task() / "case/OUT.autotest"
        case.mkdir(parents=True, exist_ok=True)
        gpu_summary = f"GPU devices (x{request['ranks']})\n" if request["gpus_per_node"] else ""
        (case / "running_scf.log").write_text(
            gpu_summary + "#SCF IS CONVERGED#\n!FINAL_ETOT_IS -4869.7470519303 eV\n")
        (results / "final-energy-ev.txt").write_text("-4869.7470519303\n")

    def test_cpu_cli_defaults_accept_unpublished_candidate_and_record_actual_ranks(self):
        artifact = self.candidate("dsprhbm")
        self.assertEqual(self.submit("dsprhbm").stdout.strip(), "12345")
        self.assertFalse((artifact.parent / "current.sif").exists())
        self.assertFalse((self.root / "modulefiles").exists())
        request = json.loads((self.task() / "request.json").read_text())
        self.assertEqual(request["gpus_per_node"], 0)
        self.assertEqual(request["ranks_per_node"], 8)
        self.assertEqual(request["cpus_per_task"], 2)
        self.assertEqual(request["ranks"], 16)
        script = (self.task() / "job.sbatch").read_text()
        self.assertIn("#SBATCH --partition=DSPRHBM", script)
        self.assertIn("#SBATCH --ntasks=16", script)
        self.assertIn("#SBATCH --cpus-per-task=2", script)
        self.assertNotIn("#SBATCH --gpus-per-node", script)
        self.assertIn("module load apptainer/1.4.4 openmpi/", script)
        self.assertIn("export SAI_ABACUS_VERSION=v1", script)
        self.assertNotIn("module load abacus/", script)
        self.assertNotIn("command -v mpirun apptainer abacus", script)
        self.assertIn('NF != 6 || $6 == ""', script)
        self.assertNotIn('$5 == ""', script)
        self.assertIn(str(artifact), script)
        self.assertIn(self.identity["install_prefix"], script)
        self.assertIn(str(self.control / "abacus"), script)
        self.assertEqual(request["job_script_sha256"], checksum(self.task() / "job.sbatch"))
        self.scientific_results()
        self.cli("monitor", "runtime", "--timeout", "1", "--interval", "0")
        manifest = json.loads(artifact.with_suffix(".json").read_text())
        proof = manifest["multinode_runtime"]
        self.assertEqual(proof["identity"], self.identity)
        self.assertEqual(proof["ranks"], 16)
        self.assertEqual(proof["gpus_per_node"], 0)
        self.assertEqual(proof["artifact_sha256"], checksum(artifact))
        self.assertEqual(proof["controller_sha256"], checksum(self.control / "runtime_controller.py"))
        self.assertEqual(proof["launcher_sha256"], checksum(self.control / "abacus"))
        evidence_path = self.task() / "results/evidence.json"
        self.assertEqual(proof["evidence_sha256"], checksum(evidence_path))
        evidence = json.loads(evidence_path.read_text())
        self.assertEqual(evidence, runtime.verify_evidence(self.task(), request))
        self.assertEqual(evidence["nodes"], {"node-0": 8, "node-1": 8})
        self.assertFalse(manifest["verified"])
        self.assertFalse((artifact.parent / "current.sif").exists())

    def test_gpu_cli_defaults_still_use_one_rank_and_gpu_per_node(self):
        self.candidate("16v100-avx2")
        self.submit("16v100-avx2")
        request = json.loads((self.task() / "request.json").read_text())
        self.assertEqual((request["gpus_per_node"], request["ranks_per_node"], request["ranks"]),
                         (1, 1, 2))
        script = (self.task() / "job.sbatch").read_text()
        self.assertIn("#SBATCH --gpus-per-node=1", script)
        self.assertIn('NF != 6 || $5 == "" || $6 == ""', script)
        self.assertIn("nvidia-smi -L", script)
        self.scientific_results()
        self.cli("monitor", "runtime", "--timeout", "1", "--interval", "0")

    def test_cpu_cli_rejects_explicit_gpu_request(self):
        self.candidate("dsprhbm")
        result = self.cli(
            "submit", "runtime", "v1", "dsprhbm", "--build-run-id", "build",
            "--gpus-per-node", "1", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime resources outside acceptance bounds", result.stderr)
        self.assertFalse(self.task().exists())

    def test_skylake_gpu_target_uses_avx2_site_mpi_not_zen4_avx512(self):
        self.candidate("8v100v0-avx512")
        self.submit("8v100v0-avx512")
        script = (self.task() / "job.sbatch").read_text()
        self.assertIn("#SBATCH --partition=8V100V0", script)
        self.assertIn("$6 !~ /-avx2$/", script)
        self.scientific_results()
        self.cli("monitor", "runtime", "--timeout", "1", "--interval", "0")

    def test_legacy_or_explicit_failed_build_cannot_receive_new_acceptance(self):
        for options in ({"legacy": True}, {"build_verified": False}):
            with self.subTest(options=options):
                self.candidate("dsprhbm", **options)
                with self.assertRaises(ValueError):
                    runtime.build_artifact("build", "v1", "dsprhbm")

    def test_build_request_must_have_the_exact_new_identity(self):
        self.candidate("dsprhbm")
        path = self.root / "runs/build/request.json"
        original = json.loads(path.read_text())
        for kind in ("missing", "track", "schema", "sha"):
            altered = json.loads(json.dumps(original))
            if kind == "missing":
                altered.pop("identity")
            elif kind == "track":
                altered["identity"] = make_identity("abacus", "release", "develop", "a" * 40,
                                                     "v1", "b" * 64, "dsprhbm")
                altered["track"] = "release"
            elif kind == "schema":
                altered["contract_schema"] = CONTRACT_SCHEMA - 1
            else:
                altered["sha"] = "c" * 40
            path.write_text(json.dumps(altered))
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                runtime.build_artifact("build", "v1", "dsprhbm")

    def test_evidence_cannot_relabel_a_pinned_artifact_as_another_channel(self):
        artifact = self.candidate("dsprhbm")
        self.submit("dsprhbm")
        self.scientific_results()
        request = json.loads((self.task() / "request.json").read_text())
        request["identity"] = make_identity("abacus", "release", "develop", "a" * 40,
                                             "v1", "b" * 64, "dsprhbm")
        with self.assertRaises(ValueError):
            runtime.verify_evidence(self.task(), request)
        request["identity"] = self.identity
        sidecar = artifact.with_suffix(".json")
        original = json.loads(sidecar.read_text())
        for field, value in (("contract_schema", CONTRACT_SCHEMA - 1), ("build_verified", False)):
            sidecar.write_text(json.dumps(dict(original, **{field: value})))
            with self.subTest(field=field), self.assertRaises(ValueError):
                runtime.verify_evidence(self.task(), request)

    def test_failed_monitor_clears_only_its_own_prior_proof(self):
        artifact = self.candidate("dsprhbm")
        self.submit("dsprhbm")
        self.scientific_results()
        self.cli("monitor", "runtime", "--timeout", "1", "--interval", "0")
        self.command("sacct", "printf '12345|FAILED|1:0|\\n'")
        self.assertEqual(self.cli("monitor", "runtime", check=False).returncode, 1)
        manifest = json.loads(artifact.with_suffix(".json").read_text())
        self.assertNotIn("multinode_runtime", manifest)
        manifest["multinode_runtime"] = {"run_id": "other-run", "verified": True}
        artifact.with_suffix(".json").write_text(json.dumps(manifest))
        self.cli("monitor", "runtime", check=False)
        self.assertEqual(json.loads(artifact.with_suffix(".json").read_text())[
            "multinode_runtime"]["run_id"], "other-run")

    def test_repeated_monitor_rejects_changed_controller_and_clears_stale_proof(self):
        artifact = self.candidate("dsprhbm")
        self.submit("dsprhbm")
        self.scientific_results()
        self.cli("monitor", "runtime", "--timeout", "1", "--interval", "0")
        controller = self.control / "runtime_controller.py"
        controller.write_text(controller.read_text() + "\n# changed since submit\n")
        result = self.cli("monitor", "runtime", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("acceptance controller changed", result.stderr)
        self.assertNotIn("multinode_runtime", json.loads(artifact.with_suffix(".json").read_text()))
        self.assertFalse(json.loads((self.task() / "results/status.json").read_text())["verified"])

    def test_zero_exit_without_scientific_results_does_not_verify(self):
        artifact = self.candidate("dsprhbm")
        self.submit("dsprhbm")
        result = self.cli("monitor", "runtime", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rank files do not match", result.stderr)
        self.assertNotIn("multinode_runtime", json.loads(artifact.with_suffix(".json").read_text()))
        self.assertFalse(json.loads((self.task() / "results/status.json").read_text())["verified"])

    def test_monitor_rejects_wrong_energy_and_mismatched_image(self):
        artifact = self.candidate("dsprhbm")
        self.submit("dsprhbm")
        for bad_energy in ("-4869.7", "nan", "inf", "1e999"):
            with self.subTest(energy=bad_energy):
                self.scientific_results()
                (self.task() / "case/OUT.autotest/running_scf.log").write_text(
                    f"#SCF IS CONVERGED#\n!FINAL_ETOT_IS {bad_energy}\n")
                result = self.cli("monitor", "runtime", check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("scientific tolerance", result.stderr)
        self.scientific_results()
        trace = self.task() / "results/ranks/rank-0.tsv"
        trace.write_text(trace.read_text().replace(str(artifact), str(artifact.parent / "wrong.sif")))
        result = self.cli("monitor", "runtime", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pinned allocation", result.stderr)
        self.assertNotIn("multinode_runtime", json.loads(artifact.with_suffix(".json").read_text()))

    def test_evidence_rejects_duplicate_rank_wrong_node_distribution_and_missing_marker(self):
        self.candidate("dsprhbm")
        self.submit("dsprhbm")
        request = json.loads((self.task() / "request.json").read_text())
        for kind in ("duplicate", "node", "mpi", "marker", "convergence", "script"):
            with self.subTest(kind=kind):
                self.scientific_results()
                trace = self.task() / "results/ranks/rank-0.tsv"
                if kind == "duplicate":
                    trace.write_text(trace.read_text().replace("\t0\t", "\t1\t"))
                elif kind == "node":
                    trace.write_text(trace.read_text().replace("node-0", "node-1"))
                elif kind == "mpi":
                    trace.write_text(trace.read_text().replace("-avx512", "-avx2"))
                elif kind == "marker":
                    (self.task() / "results/slurm-12345.log").write_text("exit 0\n")
                elif kind == "convergence":
                    (self.task() / "case/OUT.autotest/running_scf.log").write_text(
                        "!FINAL_ETOT_IS -4869.7470519303\n")
                elif kind == "script":
                    script = self.task() / "job.sbatch"
                    script.write_text(script.read_text() + "\n# modified\n")
                with self.assertRaises(ValueError):
                    runtime.verify_evidence(self.task(), request)

    def test_cpu_trace_validation_allows_empty_cuda_but_requires_mpi_isa(self):
        artifact = self.candidate("dsprhbm")
        args = argparse.Namespace(run_id="cpu", version="v1", target="dsprhbm",
                                  nodes=2, gpus_per_node=None, minutes=30,
                                  artifact=str(artifact), identity=self.identity)
        with patch.object(runtime, "ROOT", self.root):
            script = runtime.render_job(args)
        check = next(shlex.split(line)[3] for line in script.splitlines()
                     if "'NF != 6" in line)
        good = "node\t0\tdsprhbm\timage.sif\t\t/opt/openmpi-avx512\n"
        bad = "node\t0\tdsprhbm\timage.sif\t\t\n"
        for value, status in ((good, 0), (bad, 1)):
            result = subprocess.run(["awk", "-F", "\t", check], input=value, text=True)
            self.assertEqual(result.returncode, status)

    def test_energy_gate_rejects_missing_nonfinite_and_wrong_energy(self):
        artifact = self.candidate("dsprhbm")
        args = argparse.Namespace(run_id="cpu", version="v1", target="dsprhbm",
                                  nodes=2, gpus_per_node=None, minutes=30,
                                  artifact=str(artifact), identity=self.identity)
        with patch.object(runtime, "ROOT", self.root):
            script = runtime.render_job(args)
        command = next(line for line in script.splitlines() if line.startswith("awk -v actual="))
        for value, success in (("-4869.7470519303", True), ("", False), ("nan", False),
                               ("inf", False), ("1e999", False), ("-4869.7", False)):
            result = subprocess.run(["bash", "-c", command], env=dict(os.environ, actual=value))
            self.assertEqual(result.returncode == 0, success, value)


if __name__ == "__main__":
    unittest.main()
