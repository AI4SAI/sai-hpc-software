"""Regression checks for the paired ABACUS benchmark driver."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "controller"))
import abacus_benchmark as benchmark
from source_cache import checksum


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.addCleanup(patch.stopall)
        patch.object(benchmark, "ROOT", self.root).start()
        self.launcher = self.root / "controller/reviewed/abacus"
        self.launcher.parent.mkdir(parents=True)
        self.launcher.write_text("#!/bin/sh\nexit 0\n")
        self.launcher.chmod(0o755)
        self.case = self.root / "small-case"
        self.case.mkdir()
        (self.case / "INPUT").write_text("INPUT_PARAMETERS\ncalculation scf\ndevice gpu\nsuffix autotest\n")
        (self.case / "STRU").write_text("ATOMIC_SPECIES\nSi 28 Si.upf\n")
        (self.case / "Si.upf").write_text("materialized pseudopotential fixture\n")

    def args(self, target="8v100v0-avx512", **changes):
        artifact = self.root / f"containers/software/abacus/v1/{target}/candidate.sif"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"pinned candidate")
        if benchmark.TARGETS[target]["gpus"] == 0:
            (self.case / "INPUT").write_text("INPUT_PARAMETERS\ncalculation scf\ndevice cpu\n")
        values = dict(action="prepare", run_id="comparison", version="v1", target=target,
                      artifact=str(artifact), launcher=str(self.launcher), system_module=benchmark.SYSTEM_MODULE,
                      case=str(self.case), warmup=1, repeats=3, minutes=30,
                      scf_log="OUT.autotest/running_scf.log")
        values.update(changes)
        return argparse.Namespace(**values)

    def results(self, task):
        request = json.loads((task / "request.json").read_text())
        (task / "job.id").write_text("123\n")
        (task / "execution.id").write_text("123\n")
        for spec in request["runs"]:
            work = task / "runs" / spec["id"]
            shutil.copytree(task / "input", work)
            (work / "ranks").mkdir()
            (work / "OUT.autotest").mkdir()
            (work / "completed.job").write_text("123\n")
            for name in ("artifact", "launcher"):
                (work / f"{name}.sha256").write_text(f"{request[name + '_sha256']}  {request[name]}\n")
            (work / "modules.log").write_text(benchmark.MPI_MODULE + "\n" + benchmark.SYSTEM_MODULE + "\n")
            native = "/opt/software/abacus/system/bin/abacus"
            if spec["arm"] == "system":
                (work / "executable.path").write_text(native + "\n")
                (work / "executable.sha256").write_text("a" * 64 + "  " + native + "\n")
                (work / "info.log").write_text("ABACUS v3.9.0.26\n")
                (work / "ldd.log").write_text("libmpi => /opt/openmpi/lib/libmpi.so\n")
            for rank in range(request["ranks"]):
                executable = native if spec["arm"] == "system" else request["artifact"]
                gpu = "0" if benchmark.TARGETS[request["target"]]["gpus"] else ""
                (work / "ranks" / f"rank-{rank}.tsv").write_text(
                    f"node{rank // request['ranks_per_node']}\t{rank}\t{request['target']}\t"
                    f"{executable}\t{gpu}\t/opt/openmpi-{request['dependency_isa']}\n")
            timing = (4 if spec["arm"] == "system" else 2) if spec["measured"] else 100
            (work / "stdout.log").write_text(
                " ITER ETOT/eV EDIFF/eV DRHO TIME/s\n"
                f" CG1 -4.87155886e+03 0.0 1.5575e+00 {timing / 2}\n"
                f" CG2 -4.87155886e+03 0.0 1.5575e+00 {timing / 2}\n")
            (work / "wall-seconds.txt").write_text(str(timing + 1))
            (work / "OUT.autotest/running_scf.log").write_text(
                "#SCF IS CONVERGED#\n!FINAL_ETOT_IS -4871.55886 eV\n")
        return request

    def test_prepare_does_not_submit_and_pins_real_cpu_and_gpu_resources(self):
        for target in ("8v100v0-avx512", "dsprhbm"):
            with self.subTest(target=target):
                args = self.args(target, run_id=target)
                with patch.object(benchmark.subprocess, "run", wraps=subprocess.run) as run:
                    task = benchmark.prepare(args)
                self.assertEqual([call.args[0][0] for call in run.call_args_list], ["bash"])
                request = benchmark.verify(task)
                script = (task / "job.sbatch").read_text()
                self.assertIn(benchmark.SYSTEM_MODULE, script)
                self.assertIn(benchmark.MPI_MODULE, script)
                self.assertIn("/usr/bin/time -f %e", script)
                self.assertIn('cp -a input/. "$work/"', script)
                self.assertIn(str(task / "mpi-runtime"), script)
                self.assertEqual(len(request["runs"]), 8)
                self.assertEqual([s["arm"] for s in request["runs"]],
                                 ["system", "candidate", "candidate", "system"] * 2)
                if target == "dsprhbm":
                    self.assertEqual((request["ranks"], request["threads"]), (16, 2))
                    self.assertIn("#SBATCH --ntasks-per-node=8", script)
                    self.assertNotIn("#SBATCH --gpus-per-node", script)
                else:
                    self.assertEqual(request["dependency_isa"], "avx2")
                    self.assertIn("#SBATCH --gpus-per-node=1", script)
                    self.assertIn("#SBATCH --ntasks=2", script)

    def test_analysis_excludes_warmup_and_hashes_raw_evidence(self):
        task = benchmark.prepare(self.args())
        self.results(task)
        evidence = benchmark.analyze(task)
        stats = evidence["statistics"]
        self.assertEqual(stats["rounded_iteration_seconds"]["candidate"], dict(median=2, min=2, max=2))
        self.assertEqual(stats["rounded_iteration_seconds"]["candidate_system_ratio"], 0.5)
        self.assertEqual(stats["wall_seconds"]["candidate_system_ratio"], 0.6)
        self.assertEqual(evidence["request_sha256"], checksum(task / "request.json"))
        self.assertEqual((task / "evidence.sha256").read_text().strip(), checksum(task / "evidence.json"))
        self.assertIn("runs/m000-system/OUT.autotest/running_scf.log", evidence["files"])
        (task / "runs/w000-system/wall-seconds.txt").write_text("nan")
        with self.assertRaises(ValueError):
            benchmark.analyze(task)
        self.assertFalse((task / "evidence.json").exists())

    def test_analysis_rejects_incomplete_nonfinite_and_mismatched_evidence(self):
        cases = {
            "missing": ("wall-seconds.txt", None),
            "nan": ("wall-seconds.txt", "nan"),
            "energy": ("OUT.autotest/running_scf.log", "#SCF IS CONVERGED#\n!FINAL_ETOT_IS -1 eV\n"),
            "nan_energy": ("OUT.autotest/running_scf.log", "#SCF IS CONVERGED#\n!FINAL_ETOT_IS NaN eV\n"),
            "unconverged": ("OUT.autotest/running_scf.log", "!FINAL_ETOT_IS -4871.55886 eV\n"),
            "wrong_hash": ("artifact.sha256", "0" * 64),
            "changed_input": ("INPUT", "modified case"),
            "wrong_modules": ("modules.log", "abacus/auto\n"),
            "wrong_image": ("ranks/rank-0.tsv", "node0\t0\t8v100v0-avx512\t/wrong.sif\t0\t/mpi-avx2\n"),
        }
        for name, (relative, value) in cases.items():
            with self.subTest(name=name):
                task = benchmark.prepare(self.args(run_id=name))
                self.results(task)
                path = task / "runs/m000-candidate" / relative
                if value is None:
                    path.unlink()
                else:
                    path.write_text(value)
                with self.assertRaises((ValueError, OSError)):
                    benchmark.analyze(task)

    def test_prepare_rejects_alias_symlink_and_insufficient_repeats(self):
        for overrides in ({"system_module": "abacus/auto"}, {"warmup": 0}, {"repeats": 2},
                          {"warmup": 11}, {"repeats": 31}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                benchmark.prepare(self.args(**overrides))
        (self.case / "linked.upf").symlink_to(self.case / "Si.upf")
        with self.assertRaisesRegex(ValueError, "materialized"):
            benchmark.prepare(self.args())

    def test_parse_real_stdout_last_column_and_reject_nan_iteration(self):
        stdout = "ITER ETOT/eV EDIFF/eV DRHO TIME/s\nCG1 -4.87155886e+03 0.0 1.5575e+00 0.34\n"
        scf = "#SCF IS CONVERGED#\n!FINAL_ETOT_IS -4871.55886 eV\n"
        parsed = benchmark.parse_scf(stdout, scf, "1.50")
        self.assertEqual(parsed["rounded_iteration_seconds"], 0.34)
        with self.assertRaises(ValueError):
            benchmark.parse_scf(stdout.replace("0.34", "NaN"), scf, "1.50")

    def test_cli_prepare_submit_and_analyze_are_separate(self):
        args = self.args()
        env = dict(os.environ, SAI_SOFTWARE_ROOT=str(self.root))
        command = [sys.executable, str(benchmark.CONTROL)]
        subprocess.run(command + ["render", args.run_id, args.version, args.target,
                                  "--artifact", args.artifact, "--launcher", args.launcher,
                                  "--system-module", args.system_module, "--case", args.case],
                       env=env, check=True, capture_output=True, text=True)
        task = benchmark.task_dir(args.run_id)
        self.assertFalse((task / "job.id").exists())
        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir()
        fake_sbatch = fake_bin / "sbatch"
        fake_sbatch.write_text("#!/bin/sh\nprintf '123;test-cluster\\n'\n")
        fake_sbatch.chmod(0o755)
        env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
        submit = subprocess.run(command + ["submit", args.run_id], env=env,
                                check=True, capture_output=True, text=True)
        self.assertEqual(submit.stdout.strip(), "123")
        self.results(task)
        analysis = subprocess.run(command + ["analyze", args.run_id], env=env,
                                  check=True, capture_output=True, text=True)
        self.assertEqual(json.loads(analysis.stdout)["wall_seconds"]["candidate_system_ratio"], 0.6)
        duplicate = subprocess.run(command + ["submit", args.run_id], env=env,
                                   check=False, capture_output=True, text=True)
        self.assertNotEqual(duplicate.returncode, 0)

    def test_verify_rejects_changed_pinned_launcher_and_artifact(self):
        for name in ("launcher", "artifact"):
            args = self.args(run_id=name)
            task = benchmark.prepare(args)
            path = Path(getattr(args, name))
            original = path.read_bytes()
            path.write_bytes(b"changed after prepare")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                benchmark.verify(task)
            path.write_bytes(original)

    def test_cpu_case_on_gpu_requires_opt_in_and_records_actual_device(self):
        args = self.args()
        (self.case / "INPUT").write_text("INPUT_PARAMETERS\ncalculation scf\n")
        with self.assertRaises(ValueError):
            benchmark.prepare(args)
        args.allow_cpu_case_on_gpu = True
        args.system_module = benchmark.SYSTEM_MODULES[1]
        task = benchmark.prepare(args)
        request = self.results(task)
        self.assertEqual(request["case_device"], "cpu")
        self.assertEqual(request["ranks"], 2)
        for spec in request["runs"]:
            (task / "runs" / spec["id"] / "modules.log").write_text(
                benchmark.MPI_MODULE + "\n" + args.system_module + "\n")
        self.assertEqual(benchmark.analyze(task)["case_device"], "cpu")
        (task / "runs/m000-system/ldd.log").write_text("libtorch.so => not found\n")
        with self.assertRaisesRegex(ValueError, "loader"):
            benchmark.analyze(task)
        cpu_args = self.args("dsprhbm", run_id="wrong-device", allow_cpu_case_on_gpu=True)
        (self.case / "INPUT").write_text("INPUT_PARAMETERS\ndevice gpu\n")
        with self.assertRaises(ValueError):
            benchmark.prepare(cpu_args)

    def test_prepare_rejects_preexisting_scf_log_even_with_custom_output_path(self):
        args = self.args(scf_log="logs/scf.log")
        (self.case / "logs").mkdir()
        (self.case / "logs/scf.log").write_text("#SCF IS CONVERGED#\n!FINAL_ETOT_IS -1 eV\n")
        with self.assertRaisesRegex(ValueError, "fresh relative output"):
            benchmark.prepare(args)


if __name__ == "__main__":
    unittest.main()
