import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))
import cp2k_acceptance as acceptance
import cp2k_benchmark as benchmark
from delivery_layout import CONTRACT_SCHEMA, artifact_path
from release_contract import make_identity
from source_cache import checksum


ELEMENTS = {
    "water-gpw": ["O", "O", "H", "H", "H", "H"],
    "water-diag": ["O", "O", "H", "H", "H", "H"],
    "water-elpa": ["O", "O", "H", "H", "H", "H"],
    "water-dftd4": ["O", "O", "H", "H", "H", "H"],
    "ch2o-tblite": ["O", "C", "H", "H"],
    "water64-gpw": ["O", "H", "H"] * 64,
    "water64-elpa": ["O", "H", "H"] * 64,
}
TARGETS = ("dsprhbm", "4v100-avx512", "8v100v0-avx512", "16v100-avx2")
MPI_HOME = "/opt/devtools/openmpi/5.0.10-nvhpc26.3-gnu-cuda12-avx512"


def identity(target="4v100-avx512", **changes):
    fields = dict(software="cp2k", track="development", source_ref="master",
                  source_sha="a" * 40, source_version="v1", recipe_sha256="1" * 64,
                  target=target)
    fields.update(changes)
    return make_identity(**fields)


def request(target="4v100-avx512"):
    record = identity(target)
    return dict(software="cp2k", identity=record, install_prefix=record["install_prefix"],
                version=record["source_version"], target=target, source_sha=record["source_sha"],
                recipe_sha256=record["recipe_sha256"], build_run_id="build", run_id="science",
                artifact=str(artifact_path(acceptance.ROOT, record, "build")),
                launcher=str(acceptance.CONTROL / "cp2k"), minutes=60)


def science(case, energy=None):
    # Independent upstream TBLITE reference; water values are synthetic and
    # exercise consistency checks, not a claim about an absolute GPW energy.
    if energy is None:
        energy = -7.1737299848 if case == "ch2o-tblite" else -34.5
    log = (f"SCF run converged in 12 steps\n"
           f"ENERGY| Total FORCE_EVAL ( QS ) energy [hartree] {energy}\n"
           " CP2K 1 1.0 0.01 0.02 3.0 3.2\nPROGRAM ENDED AT now\n")
    if case == "water-dftd4":
        log += "Dispersion energy: -0.0023\n"
    if case.endswith("-elpa"):
        log += " cp_fm_diag_elpa 1 1.0 0.1 0.1 0.2 0.3\n"
    forces = "ATOMIC FORCES in [a.u.]\n" + "".join(
        f" {index} 1 {element} 0.000001 0.000002 0.000003\n"
        for index, element in enumerate(ELEMENTS[case], 1))
    return log, forces + "SUM OF ATOMIC FORCES 0 0 0\n", "3.5\n"


class AcceptanceTests(unittest.TestCase):
    def test_fixtures_exercise_ot_standard_diagonalization_and_selected_elpa(self):
        self.assertEqual(acceptance.PROOF, "cp2k_science")
        self.assertEqual(set(acceptance.CASES), set(ELEMENTS))
        ot = acceptance.CASES["water-gpw"]["input"]
        self.assertIn("&OT ON", ot)
        for case in ("water-diag", "water-elpa", "water64-elpa"):
            with self.subTest(case=case):
                text = acceptance.CASES[case]["input"]
                self.assertIn("&DIAGONALIZATION", text)
                self.assertIn("ALGORITHM STANDARD", text)
                self.assertNotIn("&OT", text)
                self.assertIn("RUN_TYPE ENERGY_FORCE", text)
        self.assertIn("PREFERRED_DIAG_LIBRARY SL", acceptance.CASES["water-diag"]["input"])
        for case in ("water-elpa", "water64-elpa"):
            elpa = acceptance.CASES[case]["input"]
            self.assertIn("PREFERRED_DIAG_LIBRARY ELPA", elpa)
            self.assertIn("ELPA_NEIGVEC_MIN 1", elpa)
            self.assertIn("ELPA_PRINT T", elpa)
            self.assertIn("THRESHOLD 0", elpa)
            self.assertIn("TIMINGS_LEVEL 1", elpa)
        self.assertIn("TYPE DFTD4", acceptance.CASES["water-dftd4"]["input"])
        self.assertIn("GFN_TYPE TBLITE", acceptance.CASES["ch2o-tblite"]["input"])
        for case in ("water64-gpw", "water64-elpa"):
            large = acceptance.CASES[case]["input"]
            self.assertIn("PERIODIC XYZ", large)
            self.assertIn("ABC 12.4 12.4 12.4", large)

    def test_resource_contract_is_two_nodes_and_two_ranks_on_every_target(self):
        for target in TARGETS:
            with self.subTest(target=target):
                allocation = acceptance.resources(target)
                cpu = target == "dsprhbm"
                cores = 2 if cpu else 3 if target.startswith("8v100") else 4
                self.assertEqual(allocation["nodes"], 2)
                self.assertEqual(allocation["ranks"], 2)
                self.assertEqual(allocation["ranks_per_node"], 1)
                self.assertEqual(allocation["gpus_per_node"], 0 if cpu else 1)
                self.assertEqual(allocation["omp_threads"], 2 if cpu else 1)
                self.assertEqual(allocation["cores_per_rank"], cores)
                self.assertEqual(allocation["allocated_cpus"], 4 if cpu else 4 * cores)
        with self.assertRaises(ValueError):
            acceptance.resources("unregistered-target")

    def test_candidate_only_job_preserves_pinned_identity_and_valid_shell(self):
        subprocess.run(["bash", "-n"], input=acceptance.RANK_WRAPPER, text=True, check=True)
        for target in TARGETS:
            with self.subTest(target=target):
                args = argparse.Namespace(**request(target))
                script = acceptance.render_job(args)
                subprocess.run(["bash", "-n"], input=script, text=True, check=True)
                self.assertIn("#SBATCH --nodes=2", script)
                self.assertIn("#SBATCH --ntasks=2", script)
                self.assertIn("#SBATCH --ntasks-per-node=1", script)
                self.assertIn("#SBATCH --export=NIL", script)
                self.assertIn(args.identity["install_prefix"], script)
                self.assertIn(args.artifact, script)
                self.assertNotIn("baseline", script.lower())
                self.assertNotIn("cp2k/2025.1", script)
                self.assertNotIn("cp2k/2026.1", script)
                for case in ELEMENTS:
                    self.assertIn(f"run_one {case} single 1", script)
                    self.assertIn(f"run_one {case} multi 2", script)
                self.assertIn('module purge\n', script)
                self.assertIn('export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"', script)
                if target == "dsprhbm":
                    self.assertIn("#SBATCH --cpus-per-task=2", script)
                    self.assertNotIn("#SBATCH --gpus-per-node", script)
                else:
                    self.assertIn("#SBATCH --gpus-per-node=1", script)

    def test_job_rejects_relabelled_artifact_identity(self):
        good = request()
        changes = (
            dict(version="other"), dict(target="16v100-avx2"),
            dict(source_sha="b" * 40), dict(recipe_sha256="2" * 64),
            dict(software="abacus"), dict(build_run_id="other-build"),
            dict(identity=identity(track="release")),
            dict(install_prefix="/opt/software/cp2k/v1/4v100-avx512"),
            dict(artifact=str(acceptance.ROOT / "containers/software/cp2k/v1/4v100-avx512/build.sif")),
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                acceptance.render_job(argparse.Namespace(**dict(good, **change)))

    def test_science_accepts_all_seven_cases_and_keeps_energy_force_evidence(self):
        for case in ELEMENTS:
            with self.subTest(case=case):
                parsed = acceptance.parse_science(*science(case), case)
                self.assertEqual(len(parsed["forces_ha_bohr"]), len(ELEMENTS[case]))
                self.assertEqual(parsed["cp2k_seconds"], 3.2)
                self.assertEqual(parsed["elapsed_seconds"], 3.5)
                self.assertEqual(parsed["dispersion_ha"], -0.0023 if case == "water-dftd4" else None)

    def test_science_rejects_incomplete_and_nonfinite_calculations(self):
        for case in ELEMENTS:
            log, forces, elapsed = science(case)
            energy_line = next(line for line in log.splitlines() if line.startswith("ENERGY|"))
            changes = (
                (log.replace("SCF run converged", "SCF run NOT converged"), forces, elapsed),
                (log + "ABORT\n", forces, elapsed),
                (log.replace("PROGRAM ENDED AT", "incomplete"), forces, elapsed),
                (log + energy_line + "\n", forces, elapsed),
                (log.replace(energy_line, energy_line.rsplit(" ", 1)[0] + " NaN"), forces, elapsed),
                (log, forces.replace("0.000001", "NaN"), elapsed),
                (log, forces.replace("1 1 O", "1 1 C", 1), elapsed),
                (log, forces.replace("SUM OF ATOMIC FORCES", "missing"), elapsed),
                (log.replace(" CP2K 1", " other 1"), forces, elapsed),
                (log, forces, "0"), (log, forces, "inf"),
            )
            for changed in changes:
                with self.subTest(case=case, changed=changed), self.assertRaises(ValueError):
                    acceptance.parse_science(*changed, case)

    def test_elpa_requires_positive_dispatch_evidence_with_pinned_selection(self):
        for case in ("water-elpa", "water64-elpa"):
            log, forces, elapsed = science(case)
            changes = (
                log.replace("cp_fm_diag_elpa", "unused_wrapper"),
                log.replace("cp_fm_diag_elpa 1", "cp_fm_diag_elpa 0"),
            )
            for changed in changes:
                with self.subTest(case=case, log=changed), self.assertRaisesRegex(ValueError, "ELPA"):
                    acceptance.parse_science(changed, forces, elapsed, case)
            acceptance.parse_science(log.replace("cp_fm_diag_elpa", "cp_fm_diag_elpa_base"),
                                     forces, elapsed, case)

    def test_large_science_case_reports_short_timings_without_performance_gate(self):
        for case in ("water64-gpw", "water64-elpa"):
            log, forces, _ = science(case)
            parsed = acceptance.parse_science(log.replace("3.0 3.2", "0.1 0.2"), forces, "0.3", case)
            self.assertEqual(parsed["cp2k_seconds"], 0.2)
            self.assertEqual(len(parsed["forces_ha_bohr"]), 192)

    def test_dftd4_and_tblite_require_their_scientific_evidence(self):
        log, forces, elapsed = science("water-dftd4")
        for changed in (log.replace("Dispersion energy:", "unused:"),
                        log.replace("-0.0023", "0"), log.replace("-0.0023", "NaN")):
            with self.subTest(log=changed), self.assertRaises(ValueError):
                acceptance.parse_science(changed, forces, elapsed, "water-dftd4")
        with self.assertRaisesRegex(ValueError, "TBLITE"):
            acceptance.parse_science(*science("ch2o-tblite", energy=-7.1), "ch2o-tblite")

    def test_single_and_multi_rank_results_must_agree_in_energy_and_forces(self):
        for case in ELEMENTS:
            single = acceptance.parse_science(*science(case), case)
            multi = deepcopy(single)
            multi["energy_ha"] += 1e-8
            multi["forces_ha_bohr"][0][0] += 1e-7
            comparison = acceptance.compare_science(multi, single)
            self.assertLess(comparison["energy_delta_ha"], 1e-6)
            self.assertLess(comparison["max_force_delta_ha_bohr"], 1e-5)
            changed_energy = deepcopy(single)
            changed_energy["energy_ha"] += 1e-4
            changed_force = deepcopy(single)
            changed_force["forces_ha_bohr"][0][0] += 1e-3
            for changed in (changed_energy, changed_force):
                with self.subTest(case=case), self.assertRaises(ValueError):
                    acceptance.compare_science(changed, single)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".test-work"
        parent.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.control = self.root / "controller"
        self.control.mkdir()
        self.launcher = self.control / "cp2k"
        self.launcher.write_text("#!/bin/bash\n")
        self.launcher.chmod(0o555)
        data_hashes = {name: hashlib.sha256(name.encode()).hexdigest()
                       for name in ("BASIS_MOLOPT", "GTH_POTENTIALS")}
        for field, value in (("ROOT", self.root), ("CONTROL", self.control),
                             ("DATA_HASHES", data_hashes)):
            mock = patch.object(acceptance, field, value)
            mock.start()
            self.addCleanup(mock.stop)
        self.task = self.root / "runtime-tests/science"
        self.task.mkdir(parents=True)
        self.identity = identity()
        image = artifact_path(self.root, self.identity, "build")
        image.parent.mkdir(parents=True)
        image.write_bytes(b"pinned candidate")
        build = self.root / "runs/build"
        build.mkdir(parents=True)
        (build / "artifact.path").write_text(str(image))
        manifest = dict(build_verified=True, artifact=str(image), version="v1", target="4v100-avx512",
                        sha256=checksum(image), recipe_sha256="1" * 64, source_sha="a" * 40,
                        software="cp2k", identity=self.identity, contract_schema=CONTRACT_SCHEMA)
        image.with_suffix(".json").write_text(json.dumps(manifest))
        self.request = dict(request(), **acceptance.resources("4v100-avx512"),
                            schema=1, purpose="cp2k-candidate-single-multinode-science", job="123",
                            artifact_sha256=checksum(image), launcher_sha256=checksum(self.launcher),
                            controller_sha256=checksum(Path(acceptance.__file__)),
                            helpers_sha256=checksum(Path(benchmark.__file__)),
                            rank_wrapper_sha256=hashlib.sha256(acceptance.RANK_WRAPPER.encode()).hexdigest())
        self.write("job.id", "123\n")
        self.write("job.sbatch", acceptance.render_job(argparse.Namespace(**self.request)))
        self.request["job_script_sha256"] = checksum(self.task / "job.sbatch")
        self.write("request.json", json.dumps(self.request))
        self.write("rank-exec.sh", acceptance.RANK_WRAPPER)
        self.write("results/nodes.txt", "node1\nnode2\n")
        self.write("results/allocation.txt", "JobId=123 Partition=4V100 NumNodes=2 NumTasks=2 "
                   "NumCPUs=16 CPUs/Task=1 AllocTRES=cpu=16,gres/gpu=2\n")
        self.write("results/slurm-123.log", "CP2K_CANDIDATE_SCIENCE_FINISHED\n")
        self.write("results/run-order.tsv", "".join(
            f"{case}\t{mode}\n" for case in acceptance.CASES for mode in ("single", "multi")))
        version = "results/candidate-version"
        self.write(version + "/version.log", " CP2K version 2026.2\n"
                   " cp2kflags: omp libint fftw3 libxc elpa parallel scalapack mpi_f08 cosma "
                   "plumed2 spglib libdftd4 mctc-lib tblite libvori libbqb hdf5 libxs libxsmm "
                   "offload_cuda dbcsr_acc cusolvermp cusolvermp_nccl spla_gemm_offloading\n"
                   " Source code revision aaaaaaa\n")
        self.write(version + "/mpi-version.txt", "Open MPI 5.0.10\n")
        self.write(version + "/modules.txt", "openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto\n")
        self.runner(version, 2)
        for case, spec in acceptance.CASES.items():
            data = {"input.inp": spec["input"]}
            if case.startswith("water"):
                data.update({name: name for name in data_hashes})
            for name, content in data.items():
                self.write(f"fixtures/{case}/{name}", content)
            for mode, ranks in (("single", 1), ("multi", 2)):
                folder = f"cases/{case}/{mode}"
                self.runner(folder, ranks)
                self.write(folder + "/map.txt", "ppr:1:node:pe=4\n")
                for name, content in data.items():
                    self.write(folder + "/" + name, content)
                for name, content in zip(("cp2k.log", "forces.xyz", "elapsed.txt"), science(case)):
                    self.write(folder + "/" + name, content)

    def write(self, relative, content):
        path = self.task / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def runner(self, folder, ranks):
        binary = str(self.launcher)
        self.write(folder + "/binary.txt", binary + "\n")
        self.write(folder + "/binary.sha256", f"{self.request['launcher_sha256']}  {binary}\n")
        self.write(folder + "/mpirun.txt", MPI_HOME + "/bin/mpirun\n")
        topology = "".join(f"{cpu}\t0\t{core}\t{core},{core+16}\n"
                           for core in range(4) for cpu in (core, core + 16))
        for rank in range(ranks):
            self.write(f"{folder}/ranks/rank-{rank}.tsv",
                       f"node{rank+1}\t{rank}\t4v100-avx512\t{self.request['artifact']}\t0\t{MPI_HOME}\n")
            self.write(f"{folder}/resources/rank-{rank}.tsv",
                       f"node{rank+1}\t{rank}\t0\t1\t0-3,16-19\t0\t123\n")
            self.write(f"{folder}/resources/topology-{rank}.tsv", topology)

    def test_complete_candidate_evidence_covers_single_and_multi_node_science(self):
        proof = acceptance.verify_evidence(self.task, self.request)
        self.assertEqual(proof["identity"], self.identity)
        self.assertEqual(proof["artifact_sha256"], self.request["artifact_sha256"])
        self.assertEqual(proof["job"], "123")
        self.assertEqual(len(proof["samples"]), 14)
        self.assertEqual(set(proof["comparisons"]), set(ELEMENTS))
        self.assertEqual(set(proof["solver_comparisons"]), {"single", "multi"})
        self.assertNotIn("baseline_module", proof)

    def test_mutated_science_input_rank_or_launcher_invalidates_proof(self):
        mutations = {
            "cases/water-diag/single/input.inp": "wrong input\n",
            "cases/water-gpw/multi/cp2k.log": science("water-gpw", energy=-34.4)[0],
            "cases/water-elpa/multi/cp2k.log": science("water-elpa")[0].replace("cp_fm_diag_elpa", "unused"),
            "cases/water-gpw/multi/ranks/rank-1.tsv":
                f"node1\t1\t4v100-avx512\t{self.request['artifact']}\t0\t{MPI_HOME}\n",
            "cases/water-gpw/single/binary.sha256": f"{'b' * 64}  {self.launcher}\n",
            "cases/water-gpw/multi/map.txt": "ppr:1:node:pe=1\n",
            "results/allocation.txt": "JobId=123 Partition=4V100 NumNodes=1 NumTasks=2 NumCPUs=16\n",
            "results/run-order.tsv": "water-gpw\tsingle\n",
            "job.id": "999\n",
        }
        for relative, content in mutations.items():
            path = self.task / relative
            original = path.read_text()
            try:
                path.write_text(content)
                with self.subTest(path=relative), self.assertRaises(ValueError):
                    acceptance.verify_evidence(self.task, self.request)
            finally:
                path.write_text(original)

    def test_modified_script_is_rejected_even_when_request_hash_is_updated(self):
        self.write("job.sbatch", "#!/bin/bash\necho CP2K_CANDIDATE_SCIENCE_FINISHED\n")
        changed = dict(self.request, job_script_sha256=checksum(self.task / "job.sbatch"))
        self.write("request.json", json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "job rendering"):
            acceptance.verify_evidence(self.task, changed)

    def test_diagonalization_must_agree_with_ot_even_if_both_rank_counts_agree(self):
        for case in ("water-diag", "water-elpa", "water64-elpa"):
            for mode in ("single", "multi"):
                self.write(f"cases/{case}/{mode}/cp2k.log", science(case, energy=-34.4)[0])
            try:
                with self.subTest(case=case), self.assertRaisesRegex(ValueError, "scientific tolerance"):
                    acceptance.verify_evidence(self.task, self.request)
            finally:
                for mode in ("single", "multi"):
                    self.write(f"cases/{case}/{mode}/cp2k.log", science(case)[0])

    def test_request_cannot_relabel_identity_or_claim_different_resources(self):
        for change in (dict(schema=0), dict(purpose="cp2k_benchmark"), dict(omp_threads=4),
                       dict(source_sha="b" * 40), dict(artifact_sha256="c" * 64),
                       dict(helpers_sha256="d" * 64), dict(rank_wrapper_sha256="e" * 64),
                       dict(baseline_module="cp2k/2026.1-cuda12.9-sm70-auto")):
            changed = dict(self.request, **change)
            self.write("request.json", json.dumps(changed))
            with self.subTest(change=change), self.assertRaises(ValueError):
                acceptance.verify_evidence(self.task, changed)

    def test_symlinked_scientific_evidence_is_rejected(self):
        path = self.task / "cases/water-gpw/single/cp2k.log"
        path.unlink()
        path.symlink_to(self.task / "cases/water-gpw/multi/cp2k.log")
        with self.assertRaises(ValueError):
            acceptance.verify_evidence(self.task, self.request)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".test-work"
        parent.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        control = self.root / "controller"
        control.mkdir()
        launcher = control / "cp2k"
        launcher.write_text("#!/bin/bash\n")
        launcher.chmod(0o555)
        for field, value in (("ROOT", self.root), ("CONTROL", control)):
            mock = patch.object(acceptance, field, value)
            mock.start()
            self.addCleanup(mock.stop)
        self.identity = identity()
        self.image = artifact_path(self.root, self.identity, "build")
        self.image.parent.mkdir(parents=True)
        self.image.write_bytes(b"candidate")
        self.manifest = dict(identity=self.identity, sha256=checksum(self.image))
        self.task = self.root / "runtime-tests/science"

    def test_submit_records_held_job_before_release_and_refuses_second_submission(self):
        args = argparse.Namespace(op="submit", run_id="science", version="v1",
                                  target="4v100-avx512", build_run_id="build", minutes=60)
        events = []

        def command(argv, **kwargs):
            events.append(argv)
            if argv[0] == "sbatch":
                self.assertEqual(argv[:3], ["sbatch", "--hold", "--parsable"])
                stored = json.loads((self.task / "request.json").read_text())
                self.assertNotIn("job", stored)
                self.assertEqual(stored["job_script_sha256"], checksum(self.task / "job.sbatch"))
                return subprocess.CompletedProcess(argv, 0, stdout="123;cluster\n")
            if argv[0] == "scontrol":
                self.assertEqual(argv, ["scontrol", "release", "123"])
                self.assertEqual((self.task / "job.id").read_text(), "123\n")
                self.assertEqual(json.loads((self.task / "request.json").read_text())["job"], "123")
            return subprocess.CompletedProcess(argv, 0, stdout="")

        with patch.object(acceptance, "build_artifact", return_value=(self.image, self.manifest)), \
                patch.object(acceptance, "stage_fixtures"), \
                patch.object(acceptance.subprocess, "run", side_effect=command):
            acceptance.submit(args)
            with self.assertRaises(FileExistsError):
                acceptance.submit(args)
        self.assertEqual(sum(argv[0] == "sbatch" for argv in events), 1)
        self.assertEqual(events[-1], ["scontrol", "release", "123"])

    def test_monitor_seals_only_verified_science_and_clears_stale_proof_on_failure(self):
        (self.task / "results").mkdir(parents=True)
        (self.task / "job.id").write_text("123\n")
        record = dict(request(), job="123", **acceptance.resources("4v100-avx512"))
        record.update({name: "a" * 64 for name in (
            "artifact_sha256", "controller_sha256", "helpers_sha256", "launcher_sha256",
            "job_script_sha256", "rank_wrapper_sha256")})
        (self.task / "request.json").write_text(json.dumps(record))
        manifest_path = self.image.with_suffix(".json")
        manifest_path.write_text(json.dumps(self.manifest))
        args = argparse.Namespace(run_id="science", timeout=60, interval=0)
        verified = {"allocated_cpus": 16, "samples": {"validated": True}}

        def command(argv, **kwargs):
            self.assertIn(argv[0], ("squeue", "sacct"))
            return subprocess.CompletedProcess(argv, 0, stdout="" if argv[0] == "squeue" else
                                               "123|COMPLETED|0:0|\n")

        def validate(task, saved_request):
            self.assertEqual(task, self.task)
            self.assertEqual(saved_request, record)
            self.assertFalse(json.loads((self.task / "results/status.json").read_text())["verified"])
            self.assertNotIn(acceptance.PROOF, json.loads(manifest_path.read_text()))
            return verified

        with patch.object(acceptance.subprocess, "run", side_effect=command), \
                patch.object(acceptance, "request_artifact", side_effect=lambda _: (
                    self.image, json.loads(manifest_path.read_text()))), \
                patch.object(acceptance, "verify_evidence", side_effect=validate) as verifier:
            self.assertEqual(acceptance.monitor(args), 0)
            verifier.assert_called_once()
            status = json.loads((self.task / "results/status.json").read_text())
            proof = json.loads(manifest_path.read_text())[acceptance.PROOF]
            self.assertTrue(status["verified"])
            self.assertTrue(proof["verified"])
            self.assertEqual(proof["evidence_sha256"], checksum(self.task / "results/evidence.json"))
            self.assertEqual(json.loads((self.task / "results/evidence.json").read_text()), verified)
            verifier.side_effect = ValueError("invalid raw science")
            with self.assertRaisesRegex(ValueError, "invalid raw science"):
                acceptance.monitor(args)
        self.assertFalse(json.loads((self.task / "results/status.json").read_text())["verified"])
        self.assertNotIn(acceptance.PROOF, json.loads(manifest_path.read_text()))


if __name__ == "__main__":
    unittest.main()
