"""Real CLI coverage and fail-closed evidence checks for distributed GPU tests."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))
import gpu_feature_controller as gpu
from source_cache import checksum


class GpuFeatureTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".test-work"
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.control = self.root / "controller/reviewed/gpu"
        self.control.mkdir(parents=True)
        for name in ("gpu_feature_controller.py", "gpu_feature_runtime.sh",
                     "runtime_controller.py", "remote_controller.py", "source_cache.py"):
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
        self.artifact = self.root / "containers/software/abacus/v1/16v100-avx2/build.sif"
        self.artifact.parent.mkdir(parents=True)
        self.artifact.write_bytes(b"candidate with distributed GPU features")
        self.artifact.with_suffix(".json").write_text(json.dumps({
            "artifact": str(self.artifact), "sha256": checksum(self.artifact),
            "version": "v1", "target": "16v100-avx2", "build_verified": True,
            "verified": False,
        }))
        task = self.root / "runs/build"
        task.mkdir(parents=True)
        (task / "artifact.path").write_text(str(self.artifact))

    def command(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def cli(self, *args, check=True):
        return subprocess.run([sys.executable, self.control / "gpu_feature_controller.py", *args],
                              env=self.env, capture_output=True, text=True, check=check)

    def task(self):
        return self.root / "runtime-tests/gpu"

    def submit(self, *extra):
        return self.cli("submit", "gpu", "v1", "16v100-avx2", "--build-run-id", "build", *extra)

    def request(self):
        return json.loads((self.task() / "request.json").read_text())

    def evidence(self):
        for feature, energy in gpu.ENERGIES.items():
            result = self.task() / "results" / feature
            (result / "ranks").mkdir(parents=True)
            for rank in (0, 1):
                (result / "ranks" / f"rank-{rank}.tsv").write_text(
                    f"node{rank}\t{rank}\t16v100-avx2\t{self.artifact}\t0\t/opt/mpi-avx2\n")
            case = self.task() / "cases" / feature / "OUT.autotest"
            case.mkdir(parents=True)
            (case / "running_scf.log").write_text(
                f"#SCF IS CONVERGED#\n!FINAL_ETOT_IS {energy}\n")
            (result / "abacus.log").write_text(
                "[cuSolverMp] cusolverMpSygvd: executing\n" if feature == "cusolvermp" else
                "node0:1 [0] NCCL TRACE AllGather: opCount 0 count 32 [nranks=2] stream 0\n")

    def manifest(self):
        return json.loads(self.artifact.with_suffix(".json").read_text())

    def monitor(self, **kwargs):
        return self.cli("monitor", "gpu", "--timeout", "1", "--interval", "0", **kwargs)

    def test_cli_submits_pinned_candidate_and_proves_actual_paths_without_publication(self):
        self.assertEqual(self.submit().stdout.strip(), "12345")
        request = self.request()
        self.assertEqual(request["artifact"], str(self.artifact))
        self.assertEqual(request["ranks"], 2)
        self.assertEqual(request["job_script_sha256"], checksum(self.task() / "job.sbatch"))
        script = (self.task() / "job.sbatch").read_text()
        self.assertIn("#SBATCH --partition=16V100", script)
        self.assertIn("#SBATCH --nodes=2", script)
        self.assertIn("#SBATCH --gpus-per-node=1", script)
        self.assertNotIn("#SBATCH --cpus-per-task", script)
        self.assertNotIn("#SBATCH --mem", script)
        self.assertIn(str(self.artifact), script)
        self.assertIn(str(self.control / "abacus"), script)
        self.assertNotIn("current.sif", script)
        self.assertNotIn("module load abacus/", script)
        self.assertNotRegex(script, r"(?:^|[= :])/tmp(?:/|$)")
        self.evidence()
        self.cli("verify", "gpu")
        self.monitor()
        manifest = self.manifest()
        proof = manifest["gpu_features"]
        self.assertTrue(proof["nccl_collective"])
        self.assertEqual(proof["nccl_collectives"], ["AllGather"])
        self.assertTrue(proof["cusolvermp_eigensolve"])
        self.assertEqual(proof["artifact_sha256"], checksum(self.artifact))
        self.assertEqual(proof["controller_sha256"], checksum(self.control / "gpu_feature_controller.py"))
        self.assertEqual(proof["runtime_sha256"], checksum(self.control / "gpu_feature_runtime.sh"))
        self.assertEqual(proof["evidence_sha256"], checksum(self.task() / "results/evidence.json"))
        self.assertEqual(proof["job"], "12345")
        self.assertEqual(proof["files"]["results/nccl/abacus.log"],
                         checksum(self.task() / "results/nccl/abacus.log"))
        self.assertFalse(manifest["verified"])
        self.assertFalse((self.artifact.parent / "current.sif").exists())

    def test_completed_slurm_without_actual_evidence_is_rejected(self):
        self.submit()
        result = self.monitor(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("gpu_features", self.manifest())
        self.assertFalse(json.loads((self.task() / "results/status.json").read_text())["verified"])

    def test_buffer_size_info_and_single_rank_nccl_are_not_evidence(self):
        self.submit()
        self.evidence()
        for feature, values in {
            "cusolvermp": ["CUSOLVERMP ON", "cusolverMpSygvd_bufferSize()", "cusolverMpSygvd: ERROR"],
            "nccl": ["AllReduce: opCount 0 [nranks=1]", "AllGather: opCount 0 [nranks=1]",
                     "NCCL INFO comm init nranks 2"],
        }.items():
            path = self.task() / "results" / feature / "abacus.log"
            original = path.read_text()
            for value in values:
                path.write_text(value)
                with self.assertRaisesRegex(ValueError, "no actual distributed"):
                    gpu.verify_evidence(self.task(), self.request())
            path.write_text(original)

    def test_same_node_missing_gpu_wrong_isa_and_duplicate_rank_rejected(self):
        self.submit()
        self.evidence()
        path = self.task() / "results/nccl/ranks/rank-1.tsv"
        original = path.read_text()
        for bad in (original.replace("node1", "node0"), original.replace("\t0\t/opt", "\t\t/opt"),
                    original.replace("-avx2", "-avx512"), original.replace("\t1\t", "\t0\t")):
            path.write_text(bad)
            with self.assertRaisesRegex(ValueError, "invalid two-node GPU"):
                gpu.verify_evidence(self.task(), self.request())

    def test_missing_convergence_nonfinite_and_wrong_energy_rejected(self):
        self.submit()
        self.evidence()
        path = self.task() / "cases/cusolvermp/OUT.autotest/running_scf.log"
        for bad in ("!FINAL_ETOT_IS -196.6221723701324322", "#SCF IS CONVERGED#\n",
                    "#SCF IS CONVERGED#\n!FINAL_ETOT_IS nan",
                    "#SCF IS CONVERGED#\n!FINAL_ETOT_IS inf",
                    "#SCF IS CONVERGED#\n!FINAL_ETOT_IS -196.0"):
            path.write_text(bad)
            with self.assertRaises(ValueError):
                gpu.verify_evidence(self.task(), self.request())

    def test_changed_runtime_invalidates_prior_proof(self):
        self.submit()
        self.evidence()
        self.monitor()
        path = self.control / "gpu_feature_runtime.sh"
        path.write_text(path.read_text() + "\n# changed\n")
        result = self.monitor(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime_sha256 changed", result.stderr)
        self.assertNotIn("gpu_features", self.manifest())

    def test_changed_submitted_script_rejected(self):
        self.submit()
        self.evidence()
        path = self.task() / "job.sbatch"
        path.write_text(path.read_text() + "\n# changed\n")
        result = self.monitor(check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("job_script_sha256 changed", result.stderr)

    def test_raw_log_symlink_rejected_and_changes_alter_evidence_hashes(self):
        self.submit()
        self.evidence()
        first = gpu.verify_evidence(self.task(), self.request())
        path = self.task() / "results/nccl/abacus.log"
        path.write_text(path.read_text() + "extra runtime line\n")
        second = gpu.verify_evidence(self.task(), self.request())
        self.assertNotEqual(first["files"], second["files"])
        moved = path.with_suffix(".saved")
        path.rename(moved)
        path.symlink_to(moved)
        with self.assertRaisesRegex(ValueError, "untrusted GPU evidence"):
            gpu.verify_evidence(self.task(), self.request())

    def test_failed_slurm_does_not_leave_own_prior_proof(self):
        self.submit()
        self.evidence()
        self.monitor()
        self.command("sacct", "printf '12345|FAILED|1:0|\\n'")
        self.assertEqual(self.monitor(check=False).returncode, 1)
        self.assertNotIn("gpu_features", self.manifest())

    def test_driver_uses_host_mpi_and_only_copies_packaged_inputs(self):
        script = (ROOT / "controller/gpu_feature_runtime.sh").read_text()
        subprocess.run(["bash", "-n"], input=script, text=True, check=True)
        self.assertIn('mpirun -np 2 --map-by "$MAP_OPT" --report-bindings "$launcher"', script)
        self.assertIn('"$prefix/share/sai/gpu-cases/$feature/."', script)
        self.assertIn("NCCL_DEBUG=TRACE", script)
        self.assertIn("CUSOLVERMP_LOG_LEVEL=5", script)
        self.assertNotIn("--network none", script)
        self.assertNotIn("--containall", script)
        self.assertIn("CUSOLVERMP_*", (ROOT / "controller/abacus_runtime.sh").read_text())


if __name__ == "__main__":
    unittest.main()
