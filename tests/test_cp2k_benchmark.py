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
from delivery_layout import CONTRACT_SCHEMA, artifact_path
from release_contract import make_identity
from source_cache import checksum


def identity(target="4v100-avx512", **changes):
    fields = dict(software="cp2k", track="development", source_ref="master", source_sha="a" * 40,
                  source_version="v1", recipe_sha256="1" * 64, target=target)
    fields.update(changes)
    return make_identity(**fields)


def request_identity(target="4v100-avx512"):
    record = identity(target)
    return dict(software="cp2k", identity=record, install_prefix=record["install_prefix"],
                version=record["source_version"], target=target, source_sha=record["source_sha"],
                recipe_sha256=record["recipe_sha256"], build_run_id="build",
                artifact=str(artifact_path(benchmark.ROOT, record, "build")))


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
        self.assertIn("THRESHOLD 0\n    TIMINGS_LEVEL 1", benchmark.CASES["water-elpa"]["input"])
        with self.assertRaisesRegex(ValueError, "ELPA"):
            benchmark.parse_science(log.replace("cp_fm_diag_elpa", "unused"), forces, elapsed, "water-elpa")
        with self.assertRaisesRegex(ValueError, "ELPA"):
            benchmark.parse_science(log.replace("cp_fm_diag_elpa 1", "cp_fm_diag_elpa 0"), forces, elapsed, "water-elpa")

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

    def test_only_tblite_backend_syntax_may_differ_between_versions(self):
        self.assertFalse(benchmark.syntax_contract()["same_bytes"])
        for case in benchmark.CASES:
            left, right = benchmark.fixture_hashes(case), benchmark.fixture_hashes(case, "baseline")
            self.assertEqual(left == right, case != "ch2o-tblite")
        with patch.object(benchmark, "TBLITE_BASELINE_INPUT", benchmark.TBLITE_BASELINE_INPUT.replace("GFN2", "GFN1")):
            with self.assertRaises(ValueError):
                benchmark.syntax_contract()

    def test_all_targets_use_same_allocation_warmup_and_three_repeats(self):
        subprocess.run(["bash", "-n"], input=benchmark.RANK_WRAPPER, check=True, text=True)
        for target in benchmark.BENCHMARK_TARGETS:
            with self.subTest(target=target):
                args = argparse.Namespace(run_id="probe", **request_identity(target),
                                          launcher="/project/cp2k", minutes=60)
                script = benchmark.render_job(args)
                subprocess.run(["bash", "-n"], input=script, check=True, text=True)
                self.assertEqual(script.count("#SBATCH --nodes=2"), 1)
                self.assertNotIn("/tmp/", script)
                self.assertIn("mkdir \"$work\"", script)
                self.assertIn(args.identity["install_prefix"], script)
                self.assertIn('module purge\n  export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"', script)
                self.assertNotIn("/opt/software/cp2k/v1/", script)
                self.assertIn(benchmark.mpi_mapping(target), script)
                expected_cores = 2 if target == "dsprhbm" else 3 if target.startswith("8v100") else 4
                allocation = benchmark.resources(target)
                self.assertEqual(allocation["omp_threads"], 2 if target == "dsprhbm" else 1)
                self.assertEqual(allocation["cores_per_rank"], expected_cores)
                if target == "dsprhbm":
                    self.assertIn("#SBATCH --cpus-per-task=2", script)
                    self.assertNotIn("#SBATCH --cpus-per-task=4", script)
                self.assertEqual({r for _, r, _ in benchmark.schedule(target)}, {0, 1, 2, 3})
                for case in benchmark.CASES:
                    self.assertEqual(sum(c == case and k == "candidate" for c, r, k in benchmark.schedule(target)), 4)

    def test_renderer_rejects_relabelled_identity_and_legacy_prefix(self):
        request = dict(request_identity(), run_id="probe", launcher="/project/cp2k", minutes=60)
        changes = [dict(version="other"), dict(target="16v100-avx2"), dict(source_sha="b" * 40),
                   dict(recipe_sha256="2" * 64), dict(software="abacus"),
                   dict(install_prefix="/opt/software/cp2k/v1/4v100-avx512"),
                   dict(artifact=str(benchmark.ROOT / "containers/software/cp2k/v1/4v100-avx512/build.sif")),
                   dict(identity=identity(track="release")), dict(build_run_id="another-build")]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                benchmark.render_job(argparse.Namespace(**dict(request, **change)))

    def test_rank_topology_distinguishes_physical_cores_and_smt(self):
        allocation = benchmark.resources("dsprhbm")
        topology, cores = benchmark.rank_topology("0\t0\t0\t0,16\n16\t0\t0\t0,16\n"
                                                "1\t0\t1\t1,17\n17\t0\t1\t1,17\n",
                                                {0, 1, 16, 17}, allocation)
        self.assertEqual(cores, {(0, 0), (0, 1)})
        self.assertEqual(len(topology), 4)
        # Equal core_id on distinct sockets denotes two real cores.
        _, cores = benchmark.rank_topology("0\t0\t0\t0\n1\t1\t0\t1\n", {0, 1}, allocation)
        self.assertEqual(len(cores), 2)
        for text, cpus in (("0\t0\t0\t0,16\n16\t0\t0\t0,16\n", {0, 16}),
                           ("0\t0\t0\t0,16\n", {0, 1}),
                           ("0\t0\t0\t0,16\n1\t0\t1\t0,1\n", {0, 1})):
            with self.subTest(text=text), self.assertRaises(ValueError):
                benchmark.rank_topology(text, cpus, allocation)

    def test_gpu_topology_requires_exact_site_cores_and_logical_cpus(self):
        for target in benchmark.BENCHMARK_TARGETS - {"dsprhbm"}:
            allocation = benchmark.resources(target)
            count = allocation["cores_per_rank"]
            text = "".join(f"{cpu}\t0\t{core}\t{core},{core+16}\n"
                           for core in range(count) for cpu in (core, core + 16))
            cpus = set(range(count)) | set(range(16, 16 + count))
            benchmark.rank_topology(text, cpus, allocation)
            with self.subTest(target=target), self.assertRaises(ValueError):
                benchmark.rank_topology("\n".join(text.splitlines()[::2]), set(range(count)), allocation)


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
        self.identity = identity()
        image = artifact_path(self.root, self.identity, "build")
        image.parent.mkdir(parents=True)
        image.write_bytes(b"pinned candidate")
        build = self.root / "runs/build"
        build.mkdir(parents=True)
        (build / "artifact.path").write_text(str(image))
        manifest = {"build_verified": True, "artifact": str(image), "version": "v1", "target": "4v100-avx512",
                    "sha256": checksum(image), "recipe_sha256": "1" * 64, "source_sha": "a" * 40,
                    "software": "cp2k", "identity": self.identity, "contract_schema": CONTRACT_SCHEMA}
        image.with_suffix(".json").write_text(json.dumps(manifest))
        self.request = dict(run_id="probe", **request_identity(), minutes=60,
            **benchmark.resources("4v100-avx512"), job="123", artifact_sha256=checksum(image),
            launcher=str(self.launcher), launcher_sha256=checksum(self.launcher),
            controller_sha256=checksum(Path(benchmark.__file__)), baseline_module=benchmark.GPU_BASELINE)
        self.write("job.id", "123\n")
        self.write("job.sbatch", benchmark.render_job(argparse.Namespace(**self.request)))
        self.request["job_script_sha256"] = checksum(self.task / "job.sbatch")
        self.write("rank-exec.sh", benchmark.RANK_WRAPPER)
        self.write("results/nodes.txt", "node1\nnode2\n")
        self.write("results/allocation.txt", "JobId=123 Partition=4V100 NumNodes=2 NumTasks=2 NumCPUs=16 CPUs/Task=1 AllocTRES=cpu=16,gres/gpu=2\n")
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
            self.write(folder + "/map.txt", "ppr:1:node:pe=4\n")
            self.write(folder + "/input.inp", benchmark.case_input(case, kind))
            for name in benchmark.fixture_hashes(case, kind).keys() - {"input.inp"}:
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
            self.write(f"{folder}/resources/rank-{rank}.tsv", f"node{rank+1}\t{rank}\t0\t1\t0-3,16-19\t0\t123\n")
            self.write(f"{folder}/resources/topology-{rank}.tsv", self.topology(range(4)))

    @staticmethod
    def topology(cores, smt=True, socket=0):
        return "".join(f"{cpu}\t{socket}\t{core}\t{core},{core+16}\n"
                       for core in cores for cpu in ((core, core + 16) if smt else (core,)))

    def test_raw_evidence_covers_image_job_all_samples_and_compute_time(self):
        result = benchmark.verify_evidence(self.task, self.request)
        self.assertEqual(result["job"], "123")
        self.assertEqual(result["artifact_sha256"], self.request["artifact_sha256"])
        self.assertEqual(result["identity"], self.identity)
        self.assertEqual(result["install_prefix"], self.identity["install_prefix"])
        self.assertEqual(result["allocated_cpus"], 16)
        self.assertFalse(result["timing_accounting"]["native_performance_claim"])
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
        self.write("cases/water-gpw/1-candidate/resources/rank-1.tsv", "node2\t1\t0\t1\t4-7,20-23\t0\t123\n")
        self.write("cases/water-gpw/1-candidate/resources/topology-1.tsv", self.topology(range(4, 8)))
        with self.assertRaisesRegex(ValueError, "bindings changed"):
            benchmark.verify_evidence(self.task, self.request)

    def test_cpu_ranks_cannot_all_share_the_same_two_cpus(self):
        request = dict(self.request, target="dsprhbm", **benchmark.resources("dsprhbm"))
        for rank in range(16):
            host = "node1" if rank < 8 else "node2"
            self.write(f"cpu/ranks/rank-{rank}.tsv", f"{host}\t{rank}\tdsprhbm\timage\t\t/opt/devtools/mpi-avx512\n")
            self.write(f"cpu/resources/rank-{rank}.tsv", f"{host}\t{rank}\t{rank % 8}\t2\t0-1\t\t123\n")
            self.write(f"cpu/resources/topology-{rank}.tsv", self.topology(range(2), smt=False))
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

    def test_artifact_requires_canonical_identity_and_contract_sidecar(self):
        path = Path(self.request["artifact"]).with_suffix(".json")
        original = json.loads(path.read_text())
        for change in (dict(identity=identity(track="release")), dict(contract_schema=2),
                       dict(source_sha="b" * 40), dict(version="other"),
                       dict(identity=None), dict(build_verified=False)):
            path.write_text(json.dumps(dict(original, **change)))
            with self.subTest(change=change), self.assertRaises(ValueError):
                benchmark.build_artifact("build", "v1", "4v100-avx512")
        path.write_text(json.dumps(original))
        for field, value in (("source_sha", "b" * 40), ("recipe_sha256", "2" * 64),
                             ("install_prefix", "/opt/software/cp2k/v1/4v100-avx512")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                benchmark.verify_evidence(self.task, dict(self.request, **{field: value}))

    def test_resource_misreport_and_wrong_gpu_map_are_rejected(self):
        path = self.task / "results/allocation.txt"
        original = path.read_text()
        path.write_text(original.replace("NumCPUs=16", "NumCPUs=2"))
        with self.assertRaisesRegex(ValueError, "Slurm CPU resources"):
            benchmark.verify_evidence(self.task, self.request)
        path.write_text(original)
        with self.assertRaisesRegex(ValueError, "resources changed"):
            benchmark.verify_evidence(self.task, dict(self.request, omp_threads=4))
        self.write("cases/water-gpw/0-candidate/map.txt", "ppr:1:node:pe=1\n")
        with self.assertRaisesRegex(ValueError, "resource mapping"):
            benchmark.verify_evidence(self.task, self.request)

    def test_disjoint_smt_threads_do_not_authorize_overlapping_physical_cores(self):
        request = dict(self.request, target="dsprhbm", **benchmark.resources("dsprhbm"))
        for rank in range(16):
            host, local = ("node1" if rank < 8 else "node2"), rank % 8
            core = (local // 2) * 2
            cpus = [core, core + 1] if local % 2 == 0 else [core + 16, core + 17]
            self.write(f"cpu/ranks/rank-{rank}.tsv", f"{host}\t{rank}\tdsprhbm\timage\t\t/opt/devtools/mpi-avx512\n")
            self.write(f"cpu/resources/rank-{rank}.tsv", f"{host}\t{rank}\t{local}\t2\t{cpus[0]},{cpus[1]}\t\t123\n")
            self.write(f"cpu/resources/topology-{rank}.tsv", "".join(
                f"{cpu}\t0\t{cpu % 16}\t{cpu % 16},{cpu % 16 + 16}\n" for cpu in cpus))
        with self.assertRaisesRegex(ValueError, "overlap physical CPU cores"):
            benchmark.verify_rank_evidence(self.task, "cpu", request, ["node1", "node2"], {}, binary="image")


if __name__ == "__main__":
    unittest.main()
