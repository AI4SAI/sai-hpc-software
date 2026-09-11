import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))
import cp2k_benchmark as benchmark
from source_cache import checksum


def science(case, energy=None):
    energy = energy if energy is not None else (benchmark.TBLITE_REFERENCE_HA if case == "ch2o-tblite" else -34.5)
    log = (f"SCF run converged in 12 steps\nENERGY| Total FORCE_EVAL ( QS ) energy [hartree] {energy}\n"
           " CP2K 1 1.0 0.01 0.02 3.0 3.2\nPROGRAM ENDED AT now\n")
    if case == "water-dftd4":
        log += "Dispersion energy: -0.0023\n"
    if case == "water-elpa":
        log += " cp_fm_diag_elpa 1 1.0 0.1 0.1 0.2 0.3\n"
    forces = "ATOMIC FORCES in [a.u.]\n"
    forces += "".join(f" {index} 1 {element} 0.000001 0.000002 0.000003\n"
                      for index, element in enumerate(benchmark.CASES[case]["elements"], 1))
    return log, forces + "SUM OF ATOMIC FORCES 0 0 0\n", "3.5\n"


class BenchmarkTests(unittest.TestCase):
    def test_science_rejects_nan_nonconvergence_wrong_forces_and_timing(self):
        for case in benchmark.CASES:
            log, forces, elapsed = science(case)
            parsed = benchmark.parse_science(log, forces, elapsed, case)
            self.assertEqual(parsed["cp2k_seconds"], 3.2)
            for badlog, badforces, badtime in (
                    (log.replace("SCF run converged", "SCF run NOT converged"), forces, elapsed),
                    (log, forces.replace("0.000001", "NaN"), elapsed),
                    (log.replace("PROGRAM ENDED AT", "missing"), forces, elapsed),
                    (log, forces, "0")):
                with self.subTest(case=case), self.assertRaises(ValueError):
                    benchmark.parse_science(badlog, badforces, badtime, case)
        with self.assertRaises(ValueError):
            benchmark.parse_science(*science("ch2o-tblite", energy=-7.1), "ch2o-tblite")

    def test_performance_and_elpa_are_not_only_startup_probes(self):
        self.assertEqual(len(benchmark.CASES["water64-gpw"]["elements"]), 192)
        self.assertIn("PERIODIC XYZ", benchmark.CASES["water64-gpw"]["input"])
        log, forces, elapsed = science("water64-gpw")
        with self.assertRaisesRegex(ValueError, "too short"):
            benchmark.parse_science(log.replace("3.0 3.2", "0.1 0.2"), forces, elapsed, "water64-gpw")
        log, forces, elapsed = science("water-elpa")
        with self.assertRaisesRegex(ValueError, "ELPA"):
            benchmark.parse_science(log.replace("cp_fm_diag_elpa", "unused"), forces, elapsed, "water-elpa")

    def test_only_explicit_feature_equivalence_and_quip_exception(self):
        candidate = {"flags": ["omp", "libxs", "libxsmm", "elpa"]}
        baseline = {"flags": ["omp", "xsmm", "libgrpp", "quip", "elpa"]}
        with self.assertRaises(ValueError):
            benchmark.feature_comparison(candidate, baseline)
        proof = benchmark.feature_comparison(candidate, baseline,
            {"libgrpp": "builtin_with_source_evidence"},
            {"quip": "user-approved: upstream removed in CP2K 2026"})
        self.assertEqual(set(proof["approved_exceptions"]), {"quip"})
        with self.assertRaises(ValueError):
            benchmark.feature_comparison({"flags": candidate["flags"][:-1]}, baseline,
                {"libgrpp": "builtin_with_source_evidence"},
                {"quip": "user-approved: upstream removed in CP2K 2026"})

    def test_inconsistent_revision_is_not_a_valid_version_proof(self):
        with self.assertRaises(ValueError):
            benchmark.parse_version(" CP2K version 2026.2\n cp2kflags: omp\n Source code revision abc1234\n"
                                    " CP2K version 2026.2\n cp2kflags: omp\n Source code revision def5678\n")

    def test_all_targets_use_same_allocation_warmup_and_three_repeats(self):
        for target in benchmark.BENCHMARK_TARGETS:
            with self.subTest(target=target):
                args = argparse.Namespace(run_id="probe", version="v1", target=target,
                                          artifact="/project/candidate.sif", launcher="/project/cp2k", minutes=60)
                script = benchmark.render_job(args)
                subprocess.run(["bash", "-n"], input=script, check=True, text=True)
                self.assertEqual(script.count("#SBATCH --nodes=2"), 1)
                self.assertNotIn("/tmp/", script)
                self.assertIn("mkdir \"$work\"", script)
                self.assertEqual({r for _, r, _ in benchmark.schedule(target)}, {0, 1, 2, 3})
                for case in benchmark.CASES:
                    self.assertEqual(sum(c == case and k == "candidate" for c, r, k in benchmark.schedule(target)), 4)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".test-work"
        parent.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.control = self.root / "controller"
        self.control.mkdir()
        self.launcher = self.control / "cp2k"
        self.launcher.write_text("#!/bin/bash\n")
        self.launcher.chmod(0o555)
        for field, value in (("ROOT", self.root), ("CONTROL", self.control),
                             ("DATA_HASHES", {name: benchmark.digest(name) for name in benchmark.DATA_HASHES})):
            mock = patch.object(benchmark, field, value)
            mock.start()
            self.addCleanup(mock.stop)
        self.task = self.root / "runtime-tests/probe"
        self.task.mkdir(parents=True)
        image = self.root / "containers/software/cp2k/v1/4v100-avx512/build.sif"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"pinned candidate")
        build = self.root / "runs/build"
        build.mkdir(parents=True)
        (build / "artifact.path").write_text(str(image))
        manifest = {"build_verified": True, "artifact": str(image), "version": "v1", "target": "4v100-avx512",
                    "sha256": checksum(image), "recipe_sha256": "1" * 64, "source_sha": "a" * 40}
        image.with_suffix(".json").write_text(json.dumps(manifest))
        self.request = dict(run_id="probe", version="v1", target="4v100-avx512", build_run_id="build", minutes=60,
            **benchmark.resources("4v100-avx512"), job="123", artifact=str(image), artifact_sha256=checksum(image),
            recipe_sha256="1" * 64, source_sha="a" * 40, launcher=str(self.launcher), launcher_sha256=checksum(self.launcher),
            controller_sha256=checksum(Path(benchmark.__file__)), baseline_module=benchmark.GPU_BASELINE)
        self.write("job.id", "123\n")
        self.write("job.sbatch", benchmark.render_job(argparse.Namespace(**self.request)))
        self.request["job_script_sha256"] = checksum(self.task / "job.sbatch")
        self.write("rank-exec.sh", benchmark.RANK_WRAPPER)
        self.write("results/nodes.txt", "node1\nnode2\n")
        self.write("results/allocation.txt", "JobId=123 Partition=4V100 NumNodes=2 NumTasks=2 AllocTRES=gres/gpu=2\n")
        self.write("results/slurm-123.log", "CP2K_SCIENTIFIC_BENCHMARK_FINISHED\n")
        self.write("results/run-order.tsv", "".join(f"{c}\t{r}\t{k}\n" for c, r, k in benchmark.schedule("4v100-avx512")))
        self.write("results/upstream-feature-changes.json", json.dumps({"source_sha": "a" * 40, "libgrpp": "builtin",
            "quip": "upstream_removed", "approved_parity_exceptions": benchmark.APPROVED_EXCEPTIONS,
            "evidence": {name: "b" * 64 for name in ("src/CMakeLists.txt", "src/libgrpp_integrals.F", "CMakeLists.txt")}}))
        self.request["feature_changes_sha256"] = checksum(self.task / "results/upstream-feature-changes.json")
        for kind in ("candidate", "baseline"):
            folder = f"results/{kind}-version"
            flags = "omp tblite libdftd4 elpa libxs libxsmm offload_cuda dbcsr_acc" if kind == "candidate" else "omp elpa xsmm offload_cuda dbcsr_acc"
            version = "2026.2" if kind == "candidate" else "2026.1"
            self.write(folder + "/version.log", f" CP2K version {version}\n cp2kflags: {flags}\n Source code revision aaaaaaa\n")
            self.runner(folder, kind)
            self.write(folder + "/mpi-version.txt", "Open MPI 5.0.10\n")
            self.write(folder + "/modules.txt", benchmark.GPU_BASELINE if kind == "baseline" else "openmpi\n")
        for case, repetition, kind in benchmark.schedule("4v100-avx512"):
            folder = f"cases/{case}/{repetition}-{kind}"
            self.runner(folder, kind)
            self.write(folder + "/map.txt", "ppr:1:node:pe=1\n")
            self.write(folder + "/input.inp", benchmark.CASES[case]["input"])
            for name in benchmark.fixture_hashes(case).keys() - {"input.inp"}:
                self.write(folder + "/" + name, name)
            for name, content in zip(("cp2k.log", "forces.xyz", "elapsed.txt"), science(case)):
                self.write(folder + "/" + name, content)

    def write(self, name, content):
        path = self.task / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def runner(self, folder, kind):
        binary = str(self.launcher) if kind == "candidate" else "/opt/apps/cp2k/site/bin/cp2k.psmp"
        image = self.request["artifact"] if kind == "candidate" else "system:" + binary
        sha = self.request["launcher_sha256"] if kind == "candidate" else "b" * 64
        self.write(folder + "/binary.txt", binary + "\n")
        self.write(folder + "/binary.sha256", f"{sha}  {binary}\n")
        self.write(folder + "/mpirun.txt", "/opt/devtools/mpi/bin/mpirun\n")
        for rank in range(2):
            self.write(f"{folder}/ranks/rank-{rank}.tsv", f"node{rank+1}\t{rank}\t4v100-avx512\t{image}\t0\t/opt/devtools/mpi-avx512\n")
            self.write(f"{folder}/resources/rank-{rank}.tsv", f"node{rank+1}\t{rank}\t0\t1\t0\t0\t123\n")

    def test_raw_evidence_covers_image_job_all_samples_and_compute_time(self):
        result = benchmark.verify_evidence(self.task, self.request)
        self.assertEqual(result["job"], "123")
        self.assertEqual(result["artifact_sha256"], self.request["artifact_sha256"])
        self.assertEqual(len(result["samples"]), len(benchmark.CASES) * 8)
        self.assertEqual(result["timings"]["water64-gpw"]["measured_repetitions"], 3)

    def test_mutated_input_energy_rank_or_script_invalidates_proof(self):
        changes = {
            "cases/water64-gpw/1-candidate/input.inp": "wrong input",
            "cases/water-gpw/0-candidate/cp2k.log": "PROGRAM ENDED AT but no energy",
            "cases/water-gpw/1-candidate/ranks/rank-1.tsv": "node1\t1\t4v100-avx512\twrong\t0\t/opt/devtools/mpi-avx512\n",
            "job.sbatch": "#!/bin/bash\nexit 0\n",
        }
        for name, bad in changes.items():
            path = self.task / name
            original = path.read_text()
            path.write_text(bad)
            with self.subTest(name=name), self.assertRaises(ValueError):
                benchmark.verify_evidence(self.task, self.request)
            path.write_text(original)

    def test_same_map_string_but_different_actual_affinity_is_rejected(self):
        self.write("cases/water-gpw/1-candidate/resources/rank-1.tsv", "node2\t1\t0\t1\t2\t0\t123\n")
        with self.assertRaisesRegex(ValueError, "bindings changed"):
            benchmark.verify_evidence(self.task, self.request)

    def test_cpu_ranks_cannot_all_share_the_same_two_cpus(self):
        request = dict(self.request, target="dsprhbm", **benchmark.resources("dsprhbm"))
        for rank in range(16):
            host = "node1" if rank < 8 else "node2"
            self.write(f"cpu/ranks/rank-{rank}.tsv", f"{host}\t{rank}\tdsprhbm\timage\t\t/opt/devtools/mpi-avx512\n")
            self.write(f"cpu/resources/rank-{rank}.tsv", f"{host}\t{rank}\t{rank % 8}\t2\t0-1\t\t123\n")
        with self.assertRaisesRegex(ValueError, "overlap"):
            benchmark.verify_rank_evidence(self.task, "cpu", request, ["node1", "node2"], {}, binary="image")

    def test_gpu_reference_from_other_controller_snapshot_is_reverified(self):
        evidence = benchmark.verify_evidence(self.task, self.request)
        self.write("request.json", json.dumps(self.request))
        self.write("results/evidence.json", json.dumps(evidence))
        good = {"job": "123", "verified": True, "state": "COMPLETED", "exit_code": "0:0"}
        self.write("results/status.json", json.dumps(good))
        other_control = self.root / "controller/cpu-snapshot"
        other_control.mkdir()
        (other_control / "cp2k").write_bytes(self.launcher.read_bytes())
        with patch.object(benchmark, "CONTROL", other_control):
            checked, _ = benchmark.accepted_reference("probe")
            self.assertEqual(checked, evidence)
            for bad in (dict(good, job="999"), dict(good, state="FAILED"), dict(good, exit_code="1:0"), {"verified": True}):
                self.write("results/status.json", json.dumps(bad))
                with self.subTest(status=bad), self.assertRaises(ValueError):
                    benchmark.accepted_reference("probe")
            path = self.task / "results/status.json"
            path.unlink()
            path.symlink_to(self.task / "request.json")
            with self.assertRaisesRegex(ValueError, "untrusted"):
                benchmark.accepted_reference("probe")

    def test_gpu_baseline_must_be_the_pinned_gpu_build(self):
        folder = "results/baseline-version/version.log"
        text = (self.task / folder).read_text()
        for bad in (text.replace("2026.1", "2025.1"), text.replace("offload_cuda", "")):
            self.write(folder, bad)
            with self.assertRaises(ValueError):
                benchmark.verify_evidence(self.task, self.request)


if __name__ == "__main__":
    unittest.main()
