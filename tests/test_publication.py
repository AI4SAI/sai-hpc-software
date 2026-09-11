"""Recipe-aware cache and publication must fail closed on missing acceptance."""
import argparse
from contextlib import redirect_stdout
import io
import json
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))
import software_controller as controller
import runtime_controller as runtime
import abacus_benchmark as benchmark
from source_cache import checksum


class PublicationTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".test-work"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.control = self.root / "controller/snapshot"
        self.control.mkdir(parents=True)
        for name in controller.contract_files("abacus"):
            (self.control / name).write_text("# trusted " + name + "\n")
        (self.control / "abacus").chmod(0o555)
        self.addCleanup(patch.stopall)
        patch.object(controller, "ROOT", self.root).start()
        patch.object(controller, "CONTROL", self.control).start()
        self.image = self.root / "containers/software/abacus/v1/dsprhbm/build.sif"
        self.image.parent.mkdir(parents=True)
        self.image.write_bytes(b"candidate image")
        self.task = self.root / "runs/build"
        self.task.mkdir(parents=True)
        (self.task / "job.id").write_text("100\n")
        (self.task / "results").mkdir()
        (self.task / "results/status.json").write_text(json.dumps(
            {"job": "100", "state": "COMPLETED", "exit_code": "0:0", "verified": True}))
        self.request = {"software": "abacus", "version": "v1", "target": "dsprhbm",
                        "sha": "a" * 40, "controller": str(self.control),
                        "recipe_sha256": controller.recipe_fingerprint("abacus")}
        (self.task / "request.json").write_text(json.dumps(self.request))
        (self.task / "artifact.path").write_text(str(self.image) + "\n")
        self.manifest = {"software": "abacus", "version": "v1", "target": "dsprhbm",
                         "source_sha": "a" * 40, "artifact": str(self.image),
                         "sha256": checksum(self.image), "build_verified": True,
                         "verified": False, "published": False,
                         "contract_schema": controller.CONTRACT_SCHEMA,
                         "recipe_sha256": self.request["recipe_sha256"]}
        self.save()

    def save(self):
        self.image.with_suffix(".json").write_text(json.dumps(self.manifest))

    def acceptance(self):
        task = self.root / "runtime-tests/acceptance"
        (task / "results").mkdir(parents=True)
        request = {"artifact": str(self.image), "artifact_sha256": checksum(self.image),
                   "target": "dsprhbm", "nodes": 2, "ranks_per_node": 8, "ranks": 16,
                   "cpus_per_task": 2,
                   "gpus_per_node": 0, "launcher": str(self.control / "abacus"),
                   "launcher_sha256": checksum(self.control / "abacus"),
                   "controller_sha256": checksum(self.control / "runtime_controller.py")}
        (task / "job.sbatch").write_text("#!/bin/bash\n# trusted test fixture\n")
        (task / "job.id").write_text("123\n")
        request["job_script_sha256"] = checksum(task / "job.sbatch")
        (task / "results/ranks").mkdir()
        for rank in range(16):
            (task / f"results/ranks/rank-{rank}.tsv").write_text(
                f"node-{rank // 8}\t{rank}\tdsprhbm\t{self.image}\t\t/opt/openmpi-avx512\n")
        (task / "results/slurm-123.log").write_text("MULTINODE_CONTAINER_MPI_VERIFIED\n")
        (task / "results/abacus.log").write_text("SCF completed\n")
        (task / "case/OUT.autotest").mkdir(parents=True)
        (task / "case/OUT.autotest/running_scf.log").write_text(
            "#SCF IS CONVERGED#\n!FINAL_ETOT_IS -4869.7470519303 eV\n")
        (task / "results/final-energy-ev.txt").write_text("-4869.7470519303\n")
        (task / "request.json").write_text(json.dumps(request))
        (task / "results/status.json").write_text(json.dumps(
            {"verified": True, "state": "COMPLETED", "exit_code": "0:0", "job": "123"}))
        evidence = task / "results/evidence.json"
        evidence.write_text(json.dumps(runtime.verify_evidence(task, request), sort_keys=True) + "\n")
        self.manifest["multinode_runtime"] = dict(request, verified=True, run_id="acceptance", job="123",
            evidence_sha256=checksum(evidence))
        for case in ("pw", "hse", "deepks"):
            self.benchmark_acceptance(case)
        self.save()
        return task

    def benchmark_acceptance(self, case):
        args = argparse.Namespace(action="prepare", run_id="paired-" + case, version="v1",
            target="dsprhbm", artifact=str(self.image), launcher=str(self.control / "abacus"),
            system_module=benchmark.SYSTEM_MODULE, case=None, packaged_case=case,
            warmup=1, repeats=3, minutes=30, scf_log="OUT.autotest/running_scf.log",
            allow_cpu_case_on_gpu=True)
        with patch.object(benchmark, "ROOT", self.root), patch.object(benchmark, "CONTROL", self.control / "abacus_benchmark.py"):
            task = benchmark.prepare(args)
        request = json.loads((task / "request.json").read_text())
        (task / "input").mkdir()
        source = "INPUT_PARAMETERS\ncalculation scf\ndevice cpu\nsuffix autotest\n"
        (task / "input/INPUT").write_text(benchmark.transformed_input(source, request))
        (task / "input/STRU").write_text("ATOMIC_SPECIES\nSi 28 Si.upf\n")
        files = benchmark.case_files(task / "input")
        source_files = dict(files, INPUT=hashlib.sha256(source.encode()).hexdigest())
        benchmark.dump(task / "results/input.json", dict(request_sha256=checksum(task / "request.json"),
            artifact_sha256=checksum(self.image), packaged_case=case, source_input=source,
            source_files=source_files, case_files=files, case_device="cpu"))
        (task / "job.id").write_text("456\n")
        (task / "execution.id").write_text("456\n")
        for spec in request["runs"]:
            work = task / "runs" / spec["id"]
            shutil.copytree(task / "input", work)
            (work / "ranks").mkdir()
            (work / "OUT.autotest").mkdir()
            (work / "completed.job").write_text("456\n")
            for name in ("artifact", "launcher"):
                (work / f"{name}.sha256").write_text(request[name + "_sha256"] + "\n")
            (work / "modules.log").write_text(benchmark.MPI_MODULE + "\n" + benchmark.SYSTEM_MODULE + "\n")
            (work / "info.log").write_text("ABACUS v3.9.0.26\n")
            native = "/opt/apps/abacus/system/bin/abacus"
            if spec["arm"] == "system":
                (work / "executable.path").write_text(native + "\n")
                (work / "executable.sha256").write_text("a" * 64 + "\n")
                (work / "ldd.log").write_text("libmpi.so => /opt/devtools/mpi/lib/libmpi.so\n")
            for rank in range(16):
                image = native if spec["arm"] == "system" else str(self.image)
                (work / f"ranks/rank-{rank}.tsv").write_text(
                    f"node-{rank // 8}\t{rank}\tdsprhbm\t{image}\t\t/opt/openmpi-avx512\n")
                local = rank % 8
                cpus = [local * 2, local * 2 + 1]
                benchmark.dump(work / f"ranks/affinity-{rank}.json",
                    dict(schema=1, rank=rank, hostname=f"node-{rank // 8}", local_rank=local,
                         cpus=cpus, topology=[[cpu, 0, cpu] for cpu in cpus],
                         environment=dict(OMP_NUM_THREADS="2", OMP_PROC_BIND="true",
                                          OMP_PLACES="cores", MAP_OPT="ppr:8:node:pe=2")))
            (work / "stdout.log").write_text("OpenMP thread number: 2\nITER ETOT/eV EDIFF/eV DRHO TIME/s\nCG1 -1 0 0 0.1\n")
            (work / "wall-seconds.txt").write_text("1.0\n")
            (work / "OUT.autotest/running_scf.log").write_text("#SCF IS CONVERGED#\n!FINAL_ETOT_IS -1 eV\n")
        evidence = task / "results/evidence.json"
        benchmark.dump(evidence, benchmark.verify_evidence(task, request))
        benchmark.dump(task / "results/status.json", dict(verified=True, state="COMPLETED", exit_code="0:0", job="456"))
        self.manifest["benchmark_" + case] = dict(request, run_id=task.name, verified=True, job="456",
                                                  evidence_sha256=checksum(evidence))
        return task

    def lookup(self):
        output = io.StringIO()
        with redirect_stdout(output):
            controller.lookup(argparse.Namespace(software="abacus", version="v1", target="dsprhbm", sha="a" * 40))
        return json.loads(output.getvalue())

    def publish(self):
        with redirect_stdout(io.StringIO()):
            controller.publish(argparse.Namespace(run_id="build"))

    def test_candidate_cannot_publish_or_hit_cache_without_runtime_acceptance(self):
        with self.assertRaisesRegex(ValueError, "multinode_runtime"):
            self.publish()
        self.assertFalse((self.image.parent / "current.sif").exists())
        self.assertFalse((self.root / "modulefiles").exists())
        self.assertEqual(self.lookup(), {})

    def test_only_accepted_published_candidate_hits_cache(self):
        self.acceptance()
        self.assertEqual(self.lookup(), {})
        self.publish()
        self.assertEqual((self.image.parent / "current.sif").resolve(), self.image)
        self.assertIn("module load openmpi/", self.image.with_suffix('.module').read_text())
        self.assertEqual(self.lookup()["artifact"], str(self.image))

    def test_changed_recipe_or_launcher_invalidates_same_source_cache(self):
        self.acceptance()
        self.publish()
        for name in ("environment.sh", "abacus_build.sh", "abacus", "runtime_controller.py"):
            path = self.control / name
            path.chmod(0o755)
            previous = path.read_bytes()
            path.write_bytes(previous + b"# change\n")
            self.assertEqual(self.lookup(), {}, name)
            with self.assertRaises(ValueError):
                self.publish()
            path.write_bytes(previous)
        self.assertTrue(self.lookup())

    def test_failure_or_wrong_artifact_proof_blocks_publication(self):
        task = self.acceptance()
        for field, bad in (("verified", False), ("artifact_sha256", "0" * 64), ("ranks", 0)):
            original = self.manifest["multinode_runtime"][field]
            self.manifest["multinode_runtime"][field] = bad
            self.save()
            with self.assertRaises(ValueError):
                self.publish()
            self.manifest["multinode_runtime"][field] = original
        self.save()
        (task / "results/status.json").write_text(json.dumps(
            {"verified": False, "state": "FAILED", "exit_code": "1:0", "job": "123"}))
        with self.assertRaises(ValueError):
            self.publish()
        self.assertFalse((self.image.parent / "current.sif").exists())

    def test_wrong_target_or_failed_build_cannot_bypass_acceptance(self):
        for target in ("unknown", "a100", "16v100-avx2"):
            self.manifest["target"] = target
            self.save()
            with self.assertRaises(ValueError):
                self.publish()
        self.manifest["target"] = "dsprhbm"
        self.acceptance()
        (self.task / "results/status.json").write_text(json.dumps(
            {"job": "100", "state": "FAILED", "exit_code": "1:0", "verified": False}))
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual(self.lookup(), {})

    def test_proof_job_must_match_actual_scientific_evidence(self):
        task = self.acceptance()
        self.manifest["multinode_runtime"]["job"] = "999"
        self.save()
        (task / "results/status.json").write_text(json.dumps(
            {"verified": True, "state": "COMPLETED", "exit_code": "0:0", "job": "999"}))
        with self.assertRaisesRegex(ValueError, "job or image"):
            self.publish()

    def test_legacy_verified_flag_does_not_bypass_new_contract(self):
        self.manifest = {"verified": True, "source_sha": "a" * 40, "artifact": str(self.image),
                         "sha256": checksum(self.image), "target": "dsprhbm", "software": "abacus", "version": "v1"}
        self.save()
        self.assertEqual(self.lookup(), {})
        with self.assertRaises(ValueError):
            self.publish()

    def test_corrupt_image_or_missing_evidence_invalidates_cache(self):
        task = self.acceptance()
        self.publish()
        self.image.write_bytes(b"different image")
        self.assertEqual(self.lookup(), {})
        self.image.write_bytes(b"candidate image")
        (task / "results/status.json").unlink()
        self.assertEqual(self.lookup(), {})

    def test_modified_scientific_result_invalidates_previously_accepted_cache(self):
        task = self.acceptance()
        self.publish()
        (task / "results/final-energy-ev.txt").write_text("-1\n")
        self.assertEqual(self.lookup(), {})
        with self.assertRaises(ValueError):
            self.publish()

    def test_gpu_requires_both_scientific_and_feature_acceptance(self):
        self.assertEqual(controller.required_acceptance("abacus", "4v100-avx512"),
                         ("multinode_runtime", "gpu_features", "benchmark_pw", "benchmark_hse", "benchmark_deepks"))
        self.assertEqual(controller.required_acceptance("abacus", "16v100-avx2"),
                         ("multinode_runtime", "gpu_features", "benchmark_pw", "benchmark_hse", "benchmark_deepks"))

    def test_missing_benchmark_and_changed_timing_block_publication_and_cache(self):
        self.acceptance()
        proof = self.manifest.pop("benchmark_hse")
        self.save()
        with self.assertRaisesRegex(ValueError, "benchmark_hse"):
            self.publish()
        self.manifest["benchmark_hse"] = proof
        self.save()
        self.publish()
        self.assertTrue(self.lookup())
        task = self.root / "runtime-tests" / proof["run_id"]
        (task / "runs/m000-candidate/wall-seconds.txt").write_text("nan\n")
        self.assertEqual(self.lookup(), {})
        with self.assertRaises(ValueError):
            self.publish()


if __name__ == "__main__":
    unittest.main()
