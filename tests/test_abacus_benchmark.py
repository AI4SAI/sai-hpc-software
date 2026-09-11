"""Regression checks for the paired ABACUS benchmark driver."""

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
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
        benchmark.dump(artifact.with_suffix(".json"), dict(artifact=str(artifact), sha256=checksum(artifact),
                       version="v1", target=target, build_verified=True))
        if benchmark.TARGETS[target]["gpus"] == 0:
            (self.case / "INPUT").write_text("INPUT_PARAMETERS\ncalculation scf\ndevice cpu\n")
        values = dict(action="prepare", run_id="comparison", version="v1", target=target,
                      artifact=str(artifact), launcher=str(self.launcher), system_module=benchmark.SYSTEM_MODULE,
                      case=str(self.case), packaged_case=None, warmup=1, repeats=3, minutes=30,
                      scf_log="OUT.autotest/running_scf.log")
        values.update(changes)
        return argparse.Namespace(**values)

    def affinity(self, request, rank, pe=None):
        if pe is None:
            pe = request["threads"] if request["target"] == "dsprhbm" else 4
        local = rank % request["ranks_per_node"]
        cpus = list(range(local * pe, (local + 1) * pe))
        return dict(schema=1, rank=rank, hostname=f"node{rank // request['ranks_per_node']}",
                    local_rank=local, cpus=cpus, topology=[[cpu, 0, cpu] for cpu in cpus],
                    environment=dict(OMP_NUM_THREADS=str(request["threads"]), OMP_PROC_BIND="true",
                                     OMP_PLACES="cores", MAP_OPT=f"ppr:{request['ranks_per_node']}:node:pe={pe}"))

    def results(self, task):
        request = json.loads((task / "request.json").read_text())
        if not (task / "results/input.json").exists():
            self.materialize(task)
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
            (work / "info.log").write_text("ABACUS v3.9.0.26\n")
            if spec["arm"] == "system":
                (work / "executable.path").write_text(native + "\n")
                (work / "executable.sha256").write_text("a" * 64 + "  " + native + "\n")
                (work / "ldd.log").write_text("libmpi => /opt/openmpi/lib/libmpi.so\n")
            for rank in range(request["ranks"]):
                executable = native if spec["arm"] == "system" else request["artifact"]
                gpu = "0" if benchmark.TARGETS[request["target"]]["gpus"] else ""
                (work / "ranks" / f"rank-{rank}.tsv").write_text(
                    f"node{rank // request['ranks_per_node']}\t{rank}\t{request['target']}\t"
                    f"{executable}\t{gpu}\t/opt/openmpi-{request['dependency_isa']}\n")
                benchmark.dump(work / "ranks" / f"affinity-{rank}.json", self.affinity(request, rank))
            timing = (4 if spec["arm"] == "system" else 2) if spec["measured"] else 100
            (work / "stdout.log").write_text(
                f" OpenMP thread number: {request['threads']}\n"
                " ITER ETOT/eV EDIFF/eV DRHO TIME/s\n"
                f" CG1 -4.87155886e+03 0.0 1.5575e+00 {timing / 2}\n"
                f" CG2 -4.87155886e+03 0.0 1.5575e+00 {timing / 2}\n")
            (work / "wall-seconds.txt").write_text(str(timing + 1))
            (work / "OUT.autotest/running_scf.log").write_text(
                "#SCF IS CONVERGED#\n!FINAL_ETOT_IS -4871.55886 eV\n")
        return request

    def materialize(self, task):
        def fake_copy(argv, **kwargs):
            self.assertEqual(argv[0], "apptainer")
            self.assertIn("/share/sai/benchmark-cases/", argv[-2])
            for bind in ("/usr:/usr:ro", "/lib:/lib:ro", "/lib64:/lib64:ro"):
                self.assertIn(bind, argv)
            shutil.copytree(self.case, task / "input", dirs_exist_ok=True)
            return subprocess.CompletedProcess(argv, 0)
        with patch.object(benchmark.subprocess, "run", side_effect=fake_copy):
            benchmark.materialize(task)

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
                self.assertIn('--map-by "$MAP_OPT" --bind-to core --report-bindings', script)
                self.assertIn("export OMP_PROC_BIND=true OMP_PLACES=cores", script)
                self.assertIn("affinity-", script)
                self.assertIn("os.sched_getaffinity(0)", script)
                self.assertIn('cp -a input/. "$work/"', script)
                self.assertIn(str(task / "mpi-runtime"), script)
                self.assertIn("#SBATCH --export=NIL", script)
                self.assertIn(f"export HOME={Path.home()}", script)
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
        self.assertEqual((task / "results/evidence.sha256").read_text().strip(), checksum(task / "results/evidence.json"))
        self.assertIn("runs/m000-system/OUT.autotest/running_scf.log", evidence["files"])
        self.assertIn("runs/m000-system/ranks/affinity-0.json", evidence["files"])
        self.assertEqual(evidence["runs"][0]["reported_threads"], 1)
        self.assertEqual(evidence["runs"][0]["affinity"][0]["cpus"], [0, 1, 2, 3])
        (task / "runs/w000-system/wall-seconds.txt").write_text("nan")
        with self.assertRaises(ValueError):
            benchmark.analyze(task)
        self.assertFalse((task / "results/evidence.json").exists())

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
            "no_version": ("info.log", "ABACUS unknown build\n"),
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

    def test_analysis_rejects_missing_or_invalid_affinity_identity_and_openmp(self):
        cases = {
            "missing": None,
            "schema": lambda trace: trace.update(schema=2),
            "boolean_schema": lambda trace: trace.update(schema=True),
            "rank": lambda trace: trace.update(rank=1),
            "boolean_rank": lambda trace: trace.update(rank=False),
            "hostname": lambda trace: trace.update(hostname="another-node"),
            "local_rank": lambda trace: trace.update(local_rank=1),
            "negative_local_rank": lambda trace: trace.update(local_rank=-1),
            "boolean_local_rank": lambda trace: trace.update(local_rank=False),
            "threads": lambda trace: trace["environment"].update(OMP_NUM_THREADS="2"),
            "missing_threads": lambda trace: trace["environment"].pop("OMP_NUM_THREADS"),
            "unbound_omp": lambda trace: trace["environment"].update(OMP_PROC_BIND="false"),
            "omp_places": lambda trace: trace["environment"].update(OMP_PLACES="threads"),
            "missing_mapping": lambda trace: trace["environment"].pop("MAP_OPT"),
            "non_string_mapping": lambda trace: trace["environment"].update(MAP_OPT=4),
            "wrong_ranks_per_node": lambda trace: trace["environment"].update(MAP_OPT="ppr:2:node:pe=4"),
            "non_object_environment": lambda trace: trace.update(environment=[]),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                task = benchmark.prepare(self.args(run_id="affinity-" + name))
                self.results(task)
                path = task / "runs/m000-candidate/ranks/affinity-0.json"
                if mutate is None:
                    path.unlink()
                else:
                    trace = json.loads(path.read_text())
                    mutate(trace)
                    benchmark.dump(path, trace)
                with self.assertRaisesRegex(ValueError, "rank traces|affinity"):
                    benchmark.analyze(task)

    def test_analysis_rejects_non_object_affinity_json(self):
        task = benchmark.prepare(self.args())
        self.results(task)
        path = task / "runs/m000-candidate/ranks/affinity-0.json"
        for malformed in ([], None, 1, "not an object"):
            with self.subTest(malformed=malformed):
                benchmark.dump(path, malformed)
                with self.assertRaisesRegex(ValueError, "affinity evidence must be an object"):
                    benchmark.analyze(task)

    def test_analysis_rejects_missing_or_wrong_self_reported_threads(self):
        for name, replacement in (("missing", ""), ("wrong", "OpenMP thread number: 2"),
                                  ("mixed", "OpenMP thread number: 1\nOpenMP thread number: 2"),
                                  ("prefixed", "expected OpenMP thread number: 1"),
                                  ("suffixed", "OpenMP thread number: 1 is requested")):
            with self.subTest(name=name):
                task = benchmark.prepare(self.args(run_id="reported-" + name))
                self.results(task)
                path = task / "runs/m000-candidate/stdout.log"
                path.write_text(path.read_text().replace("OpenMP thread number: 1", replacement))
                with self.assertRaisesRegex(ValueError, "reported OpenMP thread count"):
                    benchmark.analyze(task)

    def test_analysis_rejects_wrong_physical_core_counts_and_incomplete_topology(self):
        cases = {
            "too_few": lambda trace: (trace["cpus"].pop(), trace["topology"].pop()),
            "unbound": lambda trace: (trace["cpus"].append(4), trace["topology"].append([4, 0, 4])),
            "smt_not_physical_cores": lambda trace: trace["topology"][3].__setitem__(2, 2),
            "missing_topology": lambda trace: trace["topology"].pop(),
            "wrong_topology_cpu": lambda trace: trace["topology"][3].__setitem__(0, 8),
            "duplicate_cpu": lambda trace: trace["cpus"].append(3),
            "mapping_zero_cores": lambda trace: trace["environment"].update(MAP_OPT="ppr:1:node:pe=0"),
        }
        for name, mutate in cases.items():
            with self.subTest(name=name):
                task = benchmark.prepare(self.args(run_id="cores-" + name))
                self.results(task)
                path = task / "runs/m000-candidate/ranks/affinity-0.json"
                trace = json.loads(path.read_text())
                mutate(trace)
                benchmark.dump(path, trace)
                with self.assertRaisesRegex(ValueError, "affinity"):
                    benchmark.analyze(task)

    def test_analysis_rejects_smt_sibling_overlap_and_repeated_local_rank(self):
        for name in ("smt_overlap", "logical_overlap", "local_rank", "cpu_pe_exceeds_threads"):
            with self.subTest(name=name):
                task = benchmark.prepare(self.args("dsprhbm", run_id=name))
                self.results(task)
                path = task / "runs/m000-candidate/ranks/affinity-1.json"
                trace = json.loads(path.read_text())
                if name == "smt_overlap":
                    # Logical CPUs 2/3 are distinct from rank 0's 0/1, but
                    # they are SMT siblings on the same two physical cores.
                    trace["topology"] = [[2, 0, 0], [3, 0, 1]]
                elif name == "logical_overlap":
                    # Reusing logical CPUs cannot pass by claiming different
                    # physical-core IDs in the second rank's JSON.
                    trace["cpus"] = [0, 1]
                    trace["topology"] = [[0, 0, 2], [1, 0, 3]]
                elif name == "local_rank":
                    trace["local_rank"] = 0
                else:
                    trace["environment"]["MAP_OPT"] = "ppr:8:node:pe=4"
                benchmark.dump(path, trace)
                with self.assertRaisesRegex(ValueError, "affinity"):
                    benchmark.analyze(task)

    def test_analysis_rejects_core_swaps_between_arms_or_only_in_warmup(self):
        for name in ("candidate_arm", "warmup_only", "one_measured_run"):
            with self.subTest(name=name):
                task = benchmark.prepare(self.args("dsprhbm", run_id=name))
                request = self.results(task)
                for spec in request["runs"]:
                    change = (spec["arm"] == "candidate" if name == "candidate_arm" else
                              not spec["measured"] if name == "warmup_only" else
                              spec["id"] == "m001-candidate")
                    if not change:
                        continue
                    ranks = task / "runs" / spec["id"] / "ranks"
                    first = json.loads((ranks / "affinity-0.json").read_text())
                    second = json.loads((ranks / "affinity-1.json").read_text())
                    # The node still uses the same cores without overlap;
                    # only the host/local-rank ownership has changed.
                    for key in ("cpus", "topology"):
                        first[key], second[key] = second[key], first[key]
                    benchmark.dump(ranks / "affinity-0.json", first)
                    benchmark.dump(ranks / "affinity-1.json", second)
                with self.assertRaisesRegex(ValueError, "changed between benchmark arms/runs"):
                    benchmark.analyze(task)

    def test_gpu_four_physical_cores_with_one_openmp_thread_and_smt_is_valid(self):
        task = benchmark.prepare(self.args())
        request = self.results(task)
        for spec in request["runs"]:
            for rank in range(request["ranks"]):
                path = task / "runs" / spec["id"] / "ranks" / f"affinity-{rank}.json"
                trace = json.loads(path.read_text())
                self.assertEqual(trace["environment"]["OMP_NUM_THREADS"], "1")
                self.assertEqual(trace["environment"]["MAP_OPT"], "ppr:1:node:pe=4")
                trace["cpus"] = list(range(8))
                trace["topology"] = [[cpu, 0, cpu % 4] for cpu in trace["cpus"]]
                benchmark.dump(path, trace)
        evidence = benchmark.analyze(task)
        self.assertEqual(evidence["runs"][0]["reported_threads"], 1)
        self.assertEqual(len(evidence["runs"][0]["affinity"][0]["cpus"]), 8)

    @unittest.skipUnless(hasattr(os, "sched_getaffinity") and Path("/sys/devices/system/cpu").is_dir(),
                         "Linux CPU affinity and sysfs are required")
    def test_inline_affinity_probe_reads_real_sysfs_and_execs_program(self):
        traces = self.root / "live-rank-traces"
        traces.mkdir()
        env = dict(os.environ, OMPI_COMM_WORLD_RANK="0", OMPI_COMM_WORLD_LOCAL_RANK="0",
                   SAI_ABACUS_TRACE_DIR=str(traces), OMP_NUM_THREADS="1", OMP_PROC_BIND="true",
                   OMP_PLACES="cores", MAP_OPT="ppr:1:node:pe=4")
        completed = subprocess.run([sys.executable, "-c", benchmark.RANK_AFFINITY_PROBE, "/bin/true"],
                                   env=env, check=True, capture_output=True, text=True)
        self.assertEqual(completed.stdout, "")
        trace = json.loads((traces / "affinity-0.json").read_text())
        self.assertEqual((trace["schema"], trace["rank"], trace["local_rank"]), (1, 0, 0))
        self.assertEqual(trace["hostname"], socket.gethostname())
        self.assertEqual(trace["cpus"], sorted(os.sched_getaffinity(0)))
        expected_topology = []
        for cpu in trace["cpus"]:
            topology = Path("/sys/devices/system/cpu") / f"cpu{cpu}" / "topology"
            expected_topology.append([cpu, int((topology / "physical_package_id").read_text()),
                                      int((topology / "core_id").read_text())])
        self.assertEqual(trace["topology"], expected_topology)
        self.assertEqual(trace["environment"], {key: env[key] for key in
                         ("OMP_NUM_THREADS", "OMP_PROC_BIND", "OMP_PLACES", "MAP_OPT")})
        # A nonzero executable exit status must propagate through the wrapper.
        env["OMPI_COMM_WORLD_RANK"] = "1"
        failed = subprocess.run([sys.executable, "-c", benchmark.RANK_AFFINITY_PROBE, "/bin/false"],
                                env=env, check=False, capture_output=True, text=True)
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(json.loads((traces / "affinity-1.json").read_text())["rank"], 1)

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

    def test_packaged_cases_are_deferred_and_preserve_request_and_transform_hashes(self):
        for target, case, device in (("dsprhbm", "pw", "cpu"), ("8v100v0-avx512", "pw", "gpu"),
                                     ("8v100v0-avx512", "hse", "cpu"), ("8v100v0-avx512", "deepks", "cpu")):
            with self.subTest(target=target, case=case):
                args = self.args(target, run_id=target + case, case=None, packaged_case=case)
                (self.case / "INPUT").write_text("INPUT_PARAMETERS\ncalculation scf\nsuffix autotest\n" +
                                                ("device gpu\n" if case == "pw" else ""))
                with patch.object(benchmark.subprocess, "run", wraps=subprocess.run) as run:
                    task = benchmark.prepare(args)
                self.assertEqual([call.args[0][0] for call in run.call_args_list], ["bash"])
                self.assertFalse((task / "input").exists())
                before = checksum(task / "request.json")
                request = self.results(task)
                self.assertEqual(before, checksum(task / "request.json"))
                self.assertEqual(request["case_device"], device)
                self.assertIsNone(request["case_files"])
                metadata = json.loads((task / "results/input.json").read_text())
                self.assertEqual(metadata["case_device"], device)
                self.assertEqual(metadata["case_files"]["INPUT"], checksum(task / "input/INPUT"))
                self.assertEqual(benchmark.analyze(task)["benchmark_case"], case)
                if target == "dsprhbm":
                    self.assertNotEqual(metadata["source_files"]["INPUT"], metadata["case_files"]["INPUT"])
                if case != "pw":
                    self.assertTrue(request["allow_cpu_case_on_gpu"])

    def test_verify_evidence_is_read_only_and_independent_of_import_location(self):
        task = benchmark.prepare(self.args())
        request = self.results(task)
        evidence = benchmark.analyze(task)
        before = {str(p): checksum(p) for p in task.rglob("*") if p.is_file()}
        with patch.object(benchmark, "CONTROL", self.root / "another-controller.py"):
            self.assertEqual(benchmark.verify_evidence(task, request), evidence)
        self.assertEqual(before, {str(p): checksum(p) for p in task.rglob("*") if p.is_file()})

    def test_monitor_attaches_proof_then_revokes_it_on_failed_raw_evidence(self):
        args = self.args(case=None, packaged_case="pw")
        task = benchmark.prepare(args)
        request = self.results(task)
        monitor = argparse.Namespace(run_id=args.run_id, timeout=1, interval=0)

        def scheduler(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0 if argv[0] == "sacct" else 1,
                                               "123|COMPLETED|0:0|\n" if argv[0] == "sacct" else "", "")
        with patch.object(benchmark.subprocess, "run", side_effect=scheduler):
            self.assertEqual(benchmark.monitor(monitor), 0)
        sidecar = Path(request["artifact"]).with_suffix(".json")
        manifest = json.loads(sidecar.read_text())
        proof = manifest["benchmark_pw"]
        self.assertEqual(proof["run_id"], "benchmark-" + args.run_id)
        self.assertEqual(proof["evidence_sha256"], checksum(task / "results/evidence.json"))
        self.assertTrue(json.loads((task / "results/status.json").read_text())["verified"])
        manifest["benchmark_hse"] = {"run_id": "another-case", "verified": True}
        benchmark.dump(sidecar, manifest)
        (task / "runs/m000-system/wall-seconds.txt").write_text("NaN")
        with patch.object(benchmark.subprocess, "run", side_effect=scheduler), self.assertRaises(ValueError):
            benchmark.monitor(monitor)
        manifest = json.loads(sidecar.read_text())
        self.assertNotIn("benchmark_pw", manifest)
        self.assertIn("benchmark_hse", manifest)
        self.assertFalse(json.loads((task / "results/status.json").read_text())["verified"])

    def test_monitor_rejects_failed_scheduler_and_missing_case_metadata(self):
        task = benchmark.prepare(self.args(case=None, packaged_case="pw"))
        self.results(task)
        args = argparse.Namespace(run_id="comparison", timeout=1, interval=0)
        failure = [subprocess.CompletedProcess([], 1, "", ""),
                   subprocess.CompletedProcess([], 0, "123|FAILED|1:0|\n", "")]
        with patch.object(benchmark.subprocess, "run", side_effect=failure):
            self.assertEqual(benchmark.monitor(args), 1)
        self.assertFalse(json.loads((task / "results/status.json").read_text())["verified"])
        (task / "results/input.json").unlink()
        with self.assertRaises((ValueError, OSError)):
            benchmark.analyze(task)


if __name__ == "__main__":
    unittest.main()
