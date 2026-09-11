#!/usr/bin/env python3
"""Run the representative fixed-geometry LAMMPS benchmark in a Slurm allocation.

This is a synthetic force-evaluation workload, not an equilibrated trajectory.
TF/JAX comparisons explicitly hide CUDA devices in BOTH implementations. PT
requires a real CUDA kernel trace produced by this runner: no command-line flag
or imported boolean can attest device execution. Missing trace evidence retains
scientific/timing artifacts but fails acceptance and never emits a speedup.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

from md_evidence import _compare_numeric
from md_performance import ATOMS, calibrate, parse_engine_time, verify_performance
from md_science import (BACKENDS, NKTV2P, STRESS_ORDER, TOLERANCES, _flat,
                        _load_fixture, copy_trial_inputs, file_digest,
                        require_execution_context, verify_plumed_output)

REPLICAS = 7 ** 3


def replicated_reference(fixture):
    """LAMMPS replicate assigns ID = original ID + replica * original max ID."""
    source = fixture["reference"]
    if (len(source["forces"]) != 6 or any(len(row) != 3 for row in source["forces"])
            or len(source["virial"]) != 9):
        raise ValueError("performance requires the six-atom force/virial oracle")
    for value in source.values():
        _flat(value)
    return {"energy": source["energy"] * REPLICAS,
            "forces": [list(source["forces"][i % 6]) for i in range(ATOMS)],
            "virial": [value * REPLICAS for value in source["virial"]]}


def parse_large_lammps_output(stdout, dump_text):
    """Validate one full 2058-atom frame, sorting IDs before checking ALL forces."""
    markers = re.findall(r"^SAI_ENERGY = (\S+)\s*$", stdout, re.M)
    if len(markers) != 1:
        raise ValueError("missing or duplicate LAMMPS energy marker")
    lines = dump_text.strip().splitlines()
    if (len(lines) != ATOMS + 9 or lines[0] != "ITEM: TIMESTEP"
            or not re.fullmatch(r"[0-9]+", lines[1])
            or lines[2] != "ITEM: NUMBER OF ATOMS" or lines[3] != str(ATOMS)
            or lines[4] != "ITEM: BOX BOUNDS pp pp pp"):
        raise ValueError("expected exactly one replicated 2058-atom dump frame")
    for line in lines[5:8]:
        bounds = list(map(float, line.split()))
        if len(bounds) != 2 or bounds != [0.0, 91.0]:
            raise ValueError("replicated geometry must have a 91 Angstrom cubic box")
    columns = ["id", "fx", "fy", "fz"] + [f"c_sai_virial[{j}]" for j in range(1, 10)]
    if lines[8].split() != ["ITEM:", "ATOMS", *columns]:
        raise ValueError("LAMMPS dump columns do not match the performance contract")
    rows = {}
    for line in lines[9:]:
        fields = line.split()
        if len(fields) != len(columns) or not re.fullmatch(r"[0-9]+", fields[0]):
            raise ValueError("invalid LAMMPS dump row")
        atom_id = int(fields[0])
        if atom_id in rows:
            raise ValueError("duplicate LAMMPS atom ID")
        rows[atom_id] = list(map(float, fields[1:]))
        _flat(rows[atom_id])
    if set(rows) != set(range(1, ATOMS + 1)):
        raise ValueError("missing or out-of-range LAMMPS atom IDs")
    virial = [0.0] * 9
    for column, tensor_index in enumerate(STRESS_ORDER):
        virial[tensor_index] = -math.fsum(row[column + 3] for row in rows.values()) / NKTV2P
    result = {"energy": float(markers[0]), "forces": [rows[i][:3] for i in range(1, ATOMS + 1)],
              "virial": virial}
    for value in result.values():
        _flat(value)
    return result


def verify_large_science(stdout, dump_text, colvar_text, fixture):
    observed = parse_large_lammps_output(stdout, dump_text)
    reference = replicated_reference(fixture)
    # Use the reviewed tolerances, never accept inflated tolerances from a file.
    for name, tolerance in TOLERANCES.items():
        _compare_numeric(observed[name], reference[name], **tolerance, path=name)
    plumed = verify_plumed_output(colvar_text, fixture["distance_angstrom"])
    return {"passed": True, "observables": observed, "reference": reference,
            "tolerances": TOLERANCES, "plumed": plumed}


def parse_nsight_kernel_trace(csv_text):
    """Parse raw nsys cuda_gpu_trace CSV; memcpy events are NOT kernel proof.

    Accept the two Nsight grid-header spellings. The report must contain a
    positive-duration kernel with positive launch dimensions and device/name.
    This pure parser does not attest provenance: Runner collects the report
    itself from its immediately preceding profiled LAMMPS invocation.
    """
    reader = csv.reader(io.StringIO(csv_text))
    header = None
    events = []
    for fields in reader:
        if header is None:
            normalized = [field.strip() for field in fields]
            if {"Start (ns)", "Duration (ns)", "Name", "Device"}.issubset(normalized):
                header = normalized
                if len(set(header)) != len(header):
                    raise ValueError("duplicate Nsight CSV columns")
            continue
        if not fields or not any(field.strip() for field in fields):
            continue
        if len(fields) != len(header):
            raise ValueError("malformed Nsight CUDA trace row")
        row = dict(zip(header, fields))
        grid = [row.get(short, row.get(long, "")).strip()
                for short, long in (("GrdX", "Grid X"), ("GrdY", "Grid Y"), ("GrdZ", "Grid Z"))]
        if not all(grid):
            continue  # CUDA memory transfers have no grid dimensions.
        try:
            dimensions = [int(value.replace(",", "")) for value in grid]
            start = float(row["Start (ns)"].replace(",", ""))
            duration = float(row["Duration (ns)"].replace(",", ""))
        except ValueError as error:
            raise ValueError("invalid Nsight kernel timing or dimensions") from error
        if (any(value < 1 for value in dimensions) or not math.isfinite(start) or start < 0
                or not math.isfinite(duration) or duration <= 0):
            raise ValueError("invalid Nsight kernel timing or dimensions")
        name, device = row["Name"].strip(), row["Device"].strip()
        if not name or not device or re.search(r"memcpy|memset|memory copy", name, re.I):
            raise ValueError("CUDA transfer cannot establish GPU kernel execution")
        events.append({"name": name, "device": device, "start_ns": start, "duration_ns": duration})
    if not events:
        raise ValueError("no real GPU kernel events in Nsight CUDA trace")
    devices = sorted({event["device"] for event in events})
    if len(devices) != 1:
        raise ValueError("representative benchmark requires a single execution GPU")
    return {"kernel_events": len(events), "devices": devices,
            "kernel_names": sorted({event["name"] for event in events}),
            "total_kernel_ns": math.fsum(event["duration_ns"] for event in events)}


def _write_json(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")


def _fixture_digest(case, backend):
    _load_fixture(case, backend)
    hashes = [file_digest(case / name) for name in
              ("fixture.json", BACKENDS[backend], "data.lmp", "plumed.dat", f"in.{backend}")]
    return hashlib.sha256("\n".join(hashes).encode()).hexdigest()


class Runner:
    """Every subprocess that can execute LAMMPS is below the allocation guard."""

    def __init__(self, case_dir, backend, resources, launcher, output_dir, *, nsys=None, timeout=1800):
        require_execution_context()
        if backend not in BACKENDS or not isinstance(resources, dict) or not resources:
            raise ValueError("backend and explicit allocation resources are required")
        if type(resources.get("ranks")) is not int or resources["ranks"] != 1:
            raise ValueError("this host-launcher representative runner requires ranks=1")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("positive subprocess timeout required")
        self.case = Path(case_dir).resolve()
        self.backend, self.resources, self.timeout = backend, dict(resources), timeout
        if self.resources.get('threads_per_rank', 1) != 1:
            raise ValueError('this representative runner requires one thread per rank')
        self.cpu = min(os.sched_getaffinity(0))
        topology = Path(f'/sys/devices/system/cpu/cpu{self.cpu}/topology')
        self.physical_cores = [[int((topology / 'physical_package_id').read_text()),
                                int((topology / 'core_id').read_text())]]
        self.binding = None
        self.launcher = Path(launcher).resolve(strict=True)
        if not self.launcher.is_file():
            raise ValueError("host launcher must be a regular file")
        self.launcher_sha256 = file_digest(self.launcher)
        self.fixture = _load_fixture(self.case, backend)
        replicated_reference(self.fixture)
        self.fixture_sha256 = _fixture_digest(self.case, backend)
        self.node = os.uname().nodename
        self.output = Path(output_dir).resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.nsys = str(Path(nsys).resolve(strict=True)) if nsys else None
        self.calibration_records = []
        self.device_proofs = {}

    def _device(self, trial, command, env, result, *, profile=False):
        if self.backend in ("tf", "jax"):
            if env.get("CUDA_VISIBLE_DEVICES") != "" or result.returncode != 0:
                raise ValueError("CPU execution policy was not enforced")
            return {"kind": "cpu", "backend": self.backend, "device_verified": True,
                    "model": f"CPU on {self.node}", "evidence": {
                        "policy": "CUDA_VISIBLE_DEVICES empty in both launcher/container invocations",
                        "command": command, "stdout_sha256": file_digest(trial / "stdout.txt"),
                        "returncode": result.returncode}}
        if not profile and env.get('SAI_MD_IMPLEMENTATION') in self.device_proofs:
            proof = self.device_proofs[env['SAI_MD_IMPLEMENTATION']]
            evidence = proof['evidence']
            if (file_digest(self.output / evidence['trial'] / 'cuda-trace.nsys-rep') != evidence['report_sha256']
                    or file_digest(self.output / evidence['trial'] / 'cuda-kernels.csv') != evidence['csv_sha256']):
                raise ValueError('separate device trace evidence changed')
            return dict(proof, evidence=dict(evidence, measured_stdout_sha256=file_digest(trial / 'stdout.txt'),
                        policy='separate same-input/implementation trace; benchmark intervals are unprofiled'))
        device = {"kind": "gpu", "backend": self.backend, "device_verified": False,
                  "evidence": {"reason": "no runner-collected Nsight CUDA kernel trace"}}
        if not profile or not self.nsys:
            return device
        report = trial / "cuda-trace.nsys-rep"
        if not report.is_file() or report.is_symlink():
            device["evidence"]["reason"] = "Nsight did not produce a regular CUDA trace report"
            return device
        stats_command = [self.nsys, "stats", "--report", "cuda_gpu_trace", "--format", "csv",
                         "--timeunit", "ns", str(report)]
        stats = subprocess.run(stats_command, cwd=trial, env=env, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=self.timeout)
        (trial / "cuda-kernels.csv").write_text(stats.stdout)
        (trial / "nsys-stats.stderr.txt").write_text(stats.stderr)
        try:
            stats.check_returncode()
            parsed = parse_nsight_kernel_trace(stats.stdout)
        except (ValueError, subprocess.CalledProcessError) as error:
            device["evidence"]["reason"] = str(error)
            return device
        return {"kind": "gpu", "backend": self.backend, "device_verified": True,
                "model": parsed["devices"][0], "evidence": {
                    "collector": "runner-owned nsys profile + cuda_gpu_trace; not imported attestation",
                    "trial": trial.name,
                    "profile_command": command, "stats_command": stats_command, "trace": parsed,
                    "report_sha256": file_digest(report),
                    "csv_sha256": file_digest(trial / "cuda-kernels.csv")}}

    def measure(self, implementation, name, steps, input_text, *, warmup=False, profile=False):
        require_execution_context()
        if implementation not in ("baseline", "candidate") or not re.fullmatch(r"[a-z0-9-]+", name):
            raise ValueError("invalid implementation or trial name")
        if (_fixture_digest(self.case, self.backend) != self.fixture_sha256
                or file_digest(self.launcher) != self.launcher_sha256):
            raise ValueError("scientific fixture or launcher changed during performance measurement")
        trial = self.output / name
        trial.mkdir()
        copy_trial_inputs(self.case, trial, self.backend, self.fixture)
        (trial / "in.performance").write_text(input_text)
        command = ["taskset", "--cpu-list", str(self.cpu), "bash", str(self.launcher),
                   "lmp", "-log", "log.lammps", "-in", "in.performance"]
        env = os.environ.copy()
        # A caller's profiling toggle must not silently contaminate timed runs.
        env.pop('SAI_MD_PROFILE_LMP', None)
        env["SAI_MD_IMPLEMENTATION"] = implementation
        env['SAI_MD_PERFORMANCE_CPU'] = str(self.cpu)
        for name in ('OMP_NUM_THREADS', 'DP_INTRA_OP_PARALLELISM_THREADS', 'DP_INTER_OP_PARALLELISM_THREADS',
                     'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'TF_NUM_INTRAOP_THREADS', 'TF_NUM_INTEROP_THREADS'):
            env[name] = '1'
        if self.backend in ("tf", "jax"):
            env["CUDA_VISIBLE_DEVICES"] = ""
        if profile and (self.backend != 'pt' or not self.nsys):
            raise ValueError('profiling requires the PT backend and explicit Nsight executable')
        if profile:
            # The launcher starts Nsight INSIDE the SIF, after cleanenv. A host
            # profiler around apptainer would lose its CUDA injection variables.
            if not self.nsys.startswith('/opt/devtools/'):
                raise ValueError('profiler must be a read-only site /opt/devtools executable')
            env['SAI_MD_PROFILE_LMP'] = self.nsys
        _write_json(trial / "execution.json", {"command": command, "implementation": implementation,
                    "slurm_job": env.get("SLURM_JOB_ID", ""), "node": self.node,
                    "resources": self.resources, "CUDA_VISIBLE_DEVICES": env.get("CUDA_VISIBLE_DEVICES"),
                    "launcher_sha256": file_digest(self.launcher)})
        start = time.perf_counter()
        try:
            result = subprocess.run(command, cwd=trial, env=env, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=self.timeout)
        except subprocess.TimeoutExpired as error:
            raw = error.stdout or ""
            (trial / "stdout.txt").write_text(raw.decode(errors="replace") if isinstance(raw, bytes) else raw)
            _write_json(trial / "failure.json", {"reason": "process timeout", "wall_seconds": time.perf_counter() - start})
            raise
        wall = time.perf_counter() - start
        (trial / "stdout.txt").write_text(result.stdout)
        _write_json(trial / "process.json", {"returncode": result.returncode, "wall_seconds": wall})
        result.check_returncode()
        binding = json.loads((trial / 'performance-binding.json').read_text())
        if (binding.get('logical_cpus') != [self.cpu] or binding.get('node') != self.node
                or binding.get('physical_cores') != self.physical_cores
                or binding.get('job') != env.get('SLURM_JOB_ID')
                or any(binding.get(key) != '1' for key in ('omp_threads', 'dp_intra_threads', 'dp_inter_threads'))):
            raise ValueError('actual final-container benchmark binding/allocation differs')
        fixed_binding = {key: binding[key] for key in ('logical_cpus', 'physical_cores', 'omp_threads',
                                                       'dp_intra_threads', 'dp_inter_threads', 'node', 'job')}
        if self.binding is None:
            self.binding = fixed_binding
        elif self.binding != fixed_binding:
            raise ValueError('baseline/candidate or repeat binding changed')
        timing = parse_engine_time(result.stdout, steps)
        if timing["ranks"] != self.resources["ranks"]:
            raise ValueError("observed LAMMPS rank count differs from the allocation contract")
        science = verify_large_science(result.stdout, (trial / "result.dump").read_text(),
                                       (trial / "COLVAR").read_text(), self.fixture)
        _write_json(trial / "science.json", science)
        device = self._device(trial, command, env, result, profile=profile)
        _write_json(trial / "device.json", device)
        if ((trial / "in.performance").read_text() != input_text
                or _fixture_digest(self.case, self.backend) != self.fixture_sha256
                or file_digest(self.launcher) != self.launcher_sha256):
            raise ValueError("input or launcher changed during the measured process")
        record = {"implementation": implementation, "warmup": warmup,
                  "node": self.node, "resources": self.resources,
                  "input_sha256": hashlib.sha256((self.fixture_sha256 + "\n" + input_text).encode()).hexdigest(),
                  "stdout": result.stdout, "wall_seconds": wall, "execution_device": device,
                  "scientific_verified": science["passed"], "trial": name,
                  "slurm_job": env.get("SLURM_JOB_ID", ""),
                  "binding": fixed_binding,
                  "timing_scope": "LAMMPS Loop engine interval; separate end-to-end process walltime",
                  "profiled": profile}
        _write_json(trial / "record.json", record)
        return record

    def run(self, *, repeats=3, initial_steps=100, minimum=5.0):
        if type(repeats) is not int or repeats < 3 or not math.isfinite(minimum) or minimum < 5:
            raise ValueError("need >=3 repeats and >=5-second baseline calibration")

        def baseline(steps, text):
            record = self.measure("baseline", f"calibration-{len(self.calibration_records):02d}", steps, text)
            self.calibration_records.append(record)
            return record["stdout"]

        frozen = calibrate(baseline, self.backend, self.fixture_sha256, self.node, self.resources,
                           minimum=minimum, initial_steps=initial_steps)
        _write_json(self.output / "frozen.json", frozen)
        _write_json(self.output / "calibration-records.json", self.calibration_records)
        if self.backend == 'pt' and self.nsys:
            for implementation in ('baseline', 'candidate'):
                trace = self.measure(implementation, f'trace-{implementation}', frozen['steps'],
                                     frozen['input'], profile=True)
                if trace['execution_device']['device_verified'] is not True:
                    raise ValueError('separate CUDA device trace did not prove GPU execution')
                self.device_proofs[implementation] = trace['execution_device']
        records = []
        for implementation in ("baseline", "candidate"):
            for index in range(repeats + 1):
                records.append(self.measure(implementation, f"{implementation}-{index:02d}",
                                            frozen["steps"], frozen["input"], warmup=index == 0))
        _write_json(self.output / "records.json", records)
        try:
            performance = verify_performance(records, frozen)
        except ValueError as error:
            report = {"passed": False, "valid_speedup": False, "reason": str(error),
                      "backend": self.backend, "scope": frozen["workload"]}
        else:
            report = {"passed": True, "valid_speedup": True, "backend": self.backend,
                      "performance": performance, "profiled": False,
                      "separate_cuda_trace": self.backend == "pt" and bool(self.nsys)}
        _write_json(self.output / "report.json", report)
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir")
    parser.add_argument("--backend", choices=BACKENDS, required=True)
    parser.add_argument("--resources-json", required=True)
    parser.add_argument("--launcher", required=True, help="host md_runtime.sh; invoked via bash")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--initial-steps", type=int, default=100)
    parser.add_argument("--minimum", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--nsys", help="absolute Nsight Systems executable; PT GPU evidence required for acceptance")
    args = parser.parse_args(argv)
    runner = Runner(args.case_dir, args.backend, json.loads(args.resources_json), args.launcher,
                    args.output_dir, nsys=args.nsys, timeout=args.timeout)
    report = runner.run(repeats=args.repeats, initial_steps=args.initial_steps, minimum=args.minimum)
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
