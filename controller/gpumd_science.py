#!/usr/bin/env python3
"""Trusted GPUMD numerical/parity/portability and same-allocation microbenchmark.

No upstream Python/shell test driver is executed on the host. All calls here
run inside a read-only candidate SIF; only the acceptance work directory is RW.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import time

BASELINE = Path("/opt/apps/gpumd/GPUMD-5.8")
TRAIN_CONFIG = """type 2 Te Pb
version 4
cutoff 8 4
n_max 6 6
basis_size 6 6
l_max 4 2 0
neuron 30
population 10
generation 2
output_interval 1
"""
STATIC_RUN = "potential nep.txt\nvelocity 1\nensemble nve\ntime_step 0\ndump_xyz 1 dump.xyz force precision double\nrun 1\n"


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for part in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def first_frame(path):
    lines = path.read_text().splitlines()
    return "\n".join(lines[:int(lines[0]) + 2]) + "\n"


def prepare(source, destination):
    """Package inputs and gold output from the exact resolved source revision."""
    destination.mkdir(parents=True)
    static = destination / "static"
    static.mkdir()
    shutil.copyfile(source / "examples/gpumd_static/model.xyz", static / "model.xyz")
    shutil.copyfile(source / "examples/gpumd_static/dump.xyz", static / "gold.xyz")
    shutil.copyfile(source / "examples/nep_train/nep.txt", static / "nep.txt")
    (static / "run.in").write_text(STATIC_RUN)
    prediction = destination / "prediction"
    prediction.mkdir()
    for name in ("nep.txt", "nep.in", "train.xyz"):
        shutil.copyfile(source / "examples/nep_prediction" / name, prediction / name)
    for name in ("energy_train.out", "force_train.out", "virial_train.out"):
        shutil.copyfile(source / "examples/nep_prediction" / name, prediction / f"gold-{name}")
    training = destination / "training"
    training.mkdir()
    for name in ("nep.txt", "nep.restart"):
        shutil.copyfile(source / "examples/nep_train" / name, training / name)
    (training / "train.xyz").write_text(first_frame(source / "examples/nep_train/train.xyz"))
    (training / "nep.in").write_text(TRAIN_CONFIG)
    gradient = destination / "gnep"
    gradient.mkdir()
    shutil.copyfile(source / "tests_pytest/fixtures/training/train.xyz", gradient / "train.xyz")
    (gradient / "gnep.in").write_text("type 3 Ba Ti O\ncutoff 8 4\nn_max 4 4\nbasis_size 8 8\nl_max 4\nneuron 30\nlambda_v 0\nbatch 4\nepoch 2\nseed 20260831\n")
    deepmd = destination / "deepmd"
    deepmd.mkdir()
    shutil.copyfile(source / "examples/gpumd_dp_pytorch/model.xyz", deepmd / "model.xyz")
    (deepmd / "dp_settings.txt").write_text("dp 1 Cu\n")
    (deepmd / "run.in").write_text(STATIC_RUN.replace("potential nep.txt", "potential dp_settings.txt frozen_model.pth"))
    throughput = destination / "throughput"
    throughput.mkdir()
    shutil.copyfile(static / "nep.txt", throughput / "nep.txt")
    # Replicate the real 250-atom model, retaining its triclinic lattice.
    rows = (static / "model.xyz").read_text().splitlines()
    lattice = [float(x) for x in re.search(r'Lattice="([^"]+)"', rows[1]).group(1).split()]
    atoms = [row.split() for row in rows[2:2 + int(rows[0])]]
    replicated = []
    for a in range(2):
        for b in range(2):
            for c in range(2):
                shift = [a * lattice[k] + b * lattice[k + 3] + c * lattice[k + 6] for k in range(3)]
                for atom in atoms:
                    replicated.append(atom[0] + " " + " ".join(str(float(atom[k + 1]) + shift[k]) for k in range(3)))
    (throughput / "model.xyz").write_text(str(len(replicated)) + '\npbc="T T T" Lattice="' +
        " ".join(str(2 * value) for value in lattice) + '" Properties=species:S:1:pos:R:3\n' + "\n".join(replicated) + "\n")
    (throughput / "run.in").write_text("potential nep.txt\nvelocity 300\nensemble nve\ntime_step 0.5\ndump_thermo 100\nrun 1000\n")


def inspect(prefix):
    """ldd is supplemented with ELF NEEDED and real JIT-resource checks."""
    expected = {path.name for path in (BASELINE / "bin").iterdir() if path.is_file() and os.access(path, os.X_OK)}
    installed = set((prefix / "share/sai/executables.txt").read_text().split())
    if not expected <= installed or not {"gpumd", "nep", "gnep"} <= installed:
        raise ValueError("installed executable set is below the site baseline")
    report = {}
    for name in sorted(installed):
        binary = prefix / "bin" / name
        dynamic = subprocess.check_output(["readelf", "-d", binary], text=True)
        dependencies = subprocess.check_output(["ldd", binary], text=True)
        if "not found" in dependencies:
            raise ValueError(f"{name}: missing shared libraries")
        # __FILE__ strings are debug provenance, not loader dependencies.
        if re.search(r"/workspace|/control|/home/[^/]+/sai-hpc-software", dynamic):
            raise ValueError(f"{name}: nonportable loader path")
        needed = set(re.findall(r"\(NEEDED\).*\[(.*?)\]", dynamic))
        if (BASELINE / "bin" / name).exists():
            baseline_dynamic = subprocess.check_output(["readelf", "-d", BASELINE / "bin" / name], text=True)
            baseline_needed = set(re.findall(r"\(NEEDED\).*\[(.*?)\]", baseline_dynamic))
            if not baseline_needed <= needed:
                raise ValueError(f"{name}: baseline NEEDED dependency lost: {baseline_needed - needed}")
        if name == "gpumd" and not {"libdeepmd_cc.so", "libcublas.so.12", "libcusolver.so.11", "libcufft.so.11"} <= needed:
            raise ValueError("GPUMD baseline CUDA/DeepMD features missing")
        report[name] = {"sha256": sha(binary), "needed": sorted(needed), "readelf": dynamic, "ldd": dependencies}
    for relative in ("main_nep/nep_specialized.cu", "utilities/nep_utilities.cuh"):
        if not (prefix / "share/gpumd/src" / relative).is_file():
            raise ValueError("missing installed NEP runtime specialization source")
    return report


def numbers(path):
    values = [[float(value) for value in line.split()] for line in path.read_text().splitlines()
              if line.strip() and not line.startswith("#")]
    if not values or not all(math.isfinite(value) for row in values for value in row):
        raise ValueError(f"missing/nonfinite numeric result: {path}")
    return values


def compare(actual, expected, tolerance, label):
    if len(actual) != len(expected) or any(len(a) != len(b) for a, b in zip(actual, expected)):
        raise ValueError(f"{label}: result dimensions changed")
    if not actual or any(not row for row in actual) or not all(
            math.isfinite(value) for matrix in (actual, expected) for row in matrix for value in row):
        raise ValueError(f"{label}: empty or nonfinite numeric data")
    error = max(abs(a - b) for row, ref in zip(actual, expected) for a, b in zip(row, ref))
    if not math.isfinite(error) or error > tolerance:
        raise ValueError(f"{label}: error {error} > {tolerance}")
    return error


def xyz(path):
    lines = path.read_text().splitlines()
    count = int(lines[0])
    energy = float(re.search(r"\benergy=(\S+)", lines[1]).group(1))
    rows = [line.split() for line in lines[2:count + 2]]
    forces = [[float(x) for x in row[-3:]] for row in rows]
    if len(forces) != count or not math.isfinite(energy) or not all(math.isfinite(value) for row in forces for value in row):
        raise ValueError("invalid XYZ result")
    return energy, forces


def required_results():
    """Fixed evidence set. A reporter cannot omit a failed scientific path."""
    files = set()
    for mode in ("candidate", "baseline"):
        for suffix in ("", "-repeat0", "-repeat1", "-repeat2"):
            for name in ("dump.xyz", "gold.xyz", "run.log", "execution.json", "run.in", "model.xyz", "nep.txt"):
                files.add(f"static-{mode}{suffix}/{name}")
            for name in ("run.log", "execution.json", "nep.in", "train.xyz", "nep.txt"):
                files.add(f"prediction-{mode}{suffix}/{name}")
            for name in ("energy_train.out", "force_train.out", "virial_train.out"):
                files.update((f"prediction-{mode}{suffix}/{name}", f"prediction-{mode}{suffix}/gold-{name}"))
        for label in (f"throughput-warmup-{mode}", *(f"throughput-{mode}-repeat{i}" for i in range(3))):
            for name in ("model.xyz", "run.in", "nep.txt", "thermo.out", "run.log", "execution.json"):
                files.add(f"{label}/{name}")
        for name in ("dump.xyz", "run.log", "execution.json", "model.xyz", "reference.json", "frozen_model.pth"):
            files.add(f"deepmd-{mode}/{name}")
    for mode in ("on", "off"):
        for name in ("loss.out", "nep.txt", "run.log", "execution.json"):
            files.add(f"training-{mode}-candidate/{name}")
    for label, names in {
            "gnep-train-candidate": ("loss.out", "nep.txt", "run.log"),
            "gnep-prediction-candidate": ("energy_train.out", "force_train.out", "nep.txt", "run.log"),
            "gnep-static-candidate": ("dump.xyz", "nep.txt", "model.xyz", "run.log"),
            "plumed-candidate": ("colvar", "dump.xyz", "model.xyz", "nep.txt", "run.log"),
            "throughput-input": ("run.in", "model.xyz", "nep.txt"),
            "throughput-pilot-baseline": ("run.log", "execution.json", "thermo.out"),
            "deepmd-input": ("reference.json", "frozen_model.pth", "model.xyz", "model-generation.log"),
    }.items():
        files.update(f"{label}/{name}" for name in names)
    return files


def recheck_results(task, report):
    """Recompute every acceptance comparison from its mandatory raw outputs.

    This is called by both the producer and the independent host publication
    verifier. `checks` booleans and benchmark summaries are never the oracle.
    """
    for relative in required_results():
        path = task / relative
        if not path.is_file() or path.is_symlink() or path.resolve() != path:
            raise ValueError(f"missing/untrusted mandatory GPUMD result: {relative}")
    checks = {}
    benchmark = {}
    for mode in ("candidate", "baseline"):
        for suffix in ("", "-repeat0", "-repeat1", "-repeat2"):
            static = task / f"static-{mode}{suffix}"
            energy, force = xyz(static / "dump.xyz")
            gold_e, gold_f = xyz(static / "gold.xyz")
            numerical = {"energy_error_ev": compare([[energy]], [[gold_e]], 1e-3, "static energy"),
                         "force_max_error_ev_per_a": compare(force, gold_f, 1e-4, "static force")}
            if not suffix:
                checks[f"static-{mode}"] = numerical
            prediction = task / f"prediction-{mode}{suffix}"
            for name in ("energy_train.out", "force_train.out", "virial_train.out"):
                error = compare(numbers(prediction / name), numbers(prediction / f"gold-{name}"), 2e-4, name)
                if not suffix:
                    checks[f"prediction-{mode}-{name}"] = error
        for kind in ("static", "prediction"):
            records = [json.loads((task / f"{kind}-{mode}-repeat{i}/execution.json").read_text()) for i in range(3)]
            times = [record["wall_seconds"] for record in records]
            if any(not math.isfinite(value) or value <= 0 for value in times):
                raise ValueError("invalid measured wall time")
            benchmark[f"{kind}-{mode}"] = {"seconds": times, "median_seconds": statistics.median(times),
                                            "min_seconds": min(times), "warmup": 1}
        samples = []
        for label in (f"throughput-warmup-{mode}", *(f"throughput-{mode}-repeat{i}" for i in range(3))):
            case = task / label
            for name in ("run.in", "model.xyz", "nep.txt"):
                if sha(case / name) != sha(task / "throughput-input" / name):
                    raise ValueError("MD benchmark input differs between repetitions/baselines")
            record = json.loads((case / "execution.json").read_text())
            if (record["host"] != report["host"] or record["job"] != report["job"] or
                    record["gpu_visible"] != report["gpu_visible"] or
                    record["environment"] != report["conditions"]):
                raise ValueError("MD benchmark conditions differ")
            log = (case / "run.log").read_text()
            duration = float(re.findall(r"Time used for this run = (\S+) second", log)[-1])
            speed = float(re.findall(r"Speed of this run = (\S+) atom\*step/second", log)[-1])
            elapsed = record["wall_seconds"]
            if any(not math.isfinite(value) or value <= 0 for value in (duration, speed, elapsed)):
                raise ValueError("nonfinite MD engine timing")
            n_atoms = int((case / "model.xyz").read_text().splitlines()[0])
            n_steps = int(re.findall(r"^run (\d+)$", (case / "run.in").read_text(), re.M)[-1])
            if n_atoms != 2000 or n_steps < 10000 or abs(speed * duration / (n_atoms * n_steps) - 1) > 2e-4:
                raise ValueError("MD engine timing inconsistent with work done")
            numbers(case / "thermo.out")
            if "warmup" not in label:
                samples.append({"wall_seconds": elapsed, "engine_seconds": duration, "atom_steps_per_second": speed,
                                "steps_per_second": speed / n_atoms})
        benchmark[f"md-throughput-{mode}"] = {"n_atoms": n_atoms, "n_steps": n_steps, "warmup": 1,
            "samples": samples, "input_sha256": sha(task / "throughput-input/run.in"),
            "model_sha256": sha(task / "throughput-input/model.xyz"),
            "median_atom_steps_per_second": statistics.median(row["atom_steps_per_second"] for row in samples),
            "median_wall_seconds": statistics.median(row["wall_seconds"] for row in samples)}
        reference = json.loads((task / "deepmd-input/reference.json").read_text())
        case = task / f"deepmd-{mode}"
        if sha(case / "frozen_model.pth") != sha(task / "deepmd-input/frozen_model.pth"):
            raise ValueError("DeepMD model changed between evaluators")
        energy, force = xyz(case / "dump.xyz")
        checks[f"deepmd-{mode}"] = {
            "energy_error_ev": compare([[energy]], [[reference["energy"]]], 1e-4, "DeepMD energy"),
            "force_error": compare(force, reference["forces"], 1e-4, "DeepMD forces")}
    train_on, train_off = task / "training-on-candidate", task / "training-off-candidate"
    log = (train_on / "run.log").read_text()
    if "Compile specialized NEP training kernels" not in log or "specialization disabled" in log:
        raise ValueError("NEP JIT missing or silently fell back")
    for case in (train_on, train_off):
        if len(numbers(case / "loss.out")) != 2:
            raise ValueError("NEP training did not complete two generations")
    checks["nep-jit-vs-generic-loss"] = compare(numbers(train_on / "loss.out"), numbers(train_off / "loss.out"), 2e-3, "JIT loss")
    gradient = task / "gnep-prediction-candidate"
    predicted_e = numbers(gradient / "energy_train.out")
    predicted_f = numbers(gradient / "force_train.out")
    if len(numbers(task / "gnep-train-candidate/loss.out")) != 2 or len(predicted_e) != 4 or len(predicted_f) != 160:
        raise ValueError("GNEP training/prediction dimensions changed")
    if sha(gradient / "nep.txt") != sha(task / "gnep-static-candidate/nep.txt"):
        raise ValueError("GNEP and GPUMD used different models")
    energy, force = xyz(task / "gnep-static-candidate/dump.xyz")
    checks["gnep-training-and-prediction"] = {
        "energy_error_ev": compare([[energy]], [[predicted_e[0][0] * 40]], 1e-3, "GNEP energy"),
        "force_error": compare(force, [row[:3] for row in predicted_f[:40]], 2e-4, "GNEP forces")}
    plumed = task / "plumed-candidate"
    for name in ("model.xyz", "nep.txt"):
        if sha(plumed / name) != sha(task / "static-candidate" / name):
            raise ValueError("PLUMED base model changed")
    positions = [[float(x) for x in line.split()[1:4]] for line in (plumed / "model.xyz").read_text().splitlines()[2:4]]
    displacement = [a - b for a, b in zip(*positions)]
    distance = math.sqrt(sum(value * value for value in displacement))
    colvar = numbers(plumed / "colvar")
    compare([[row[1], row[2]] for row in colvar], [[distance, distance ** 2]] * len(colvar), 2e-5, "PLUMED bias")
    _, unbiased = xyz(task / "static-candidate/dump.xyz")
    _, biased = xyz(plumed / "dump.xyz")
    for axis in range(3):
        unbiased[0][axis] -= 2 * displacement[axis]
        unbiased[1][axis] += 2 * displacement[axis]
    checks["plumed-force-feedback"] = compare(biased, unbiased, 2e-4, "PLUMED force feedback")
    checks["installed-jit-resources-from-unrelated-cwd"] = True
    return checks, benchmark


def run(prefix, task):
    if not os.environ.get("CUDA_VISIBLE_DEVICES") or not os.environ.get("SLURM_JOB_ID"):
        raise ValueError("scientific/benchmark execution requires the Slurm GPU allocation")
    task.mkdir(parents=True, exist_ok=True)
    inputs = prefix / "share/sai/cases"
    report = {"inspection": inspect(prefix), "checks": {}, "benchmark": {},
              "host": subprocess.check_output(["hostname"], text=True).strip(),
              "job": os.environ["SLURM_JOB_ID"], "gpu_visible": os.environ["CUDA_VISIBLE_DEVICES"],
              "benchmark_description": "single GPU same SIF/dependencies/inputs: replicated 2000-atom NEP MD, engine atom-step/s and total wall time, one warmup plus three repeats; separate startup microbenchmarks are not throughput"}
    # Driver management binaries may not exist in the minimal rootfs; capture
    # the actual allocation's metadata on the host before entering this SIF.
    gpu_metadata = task.parent / "gpu-host.txt"
    report["gpu"] = gpu_metadata.read_text()
    report["gpu_metadata_sha256"] = sha(gpu_metadata)
    report["conditions"] = {name: os.environ.get(name, "") for name in
                            ("OMP_NUM_THREADS", "DP_INTRA_OP_PARALLELISM_THREADS", "DP_INTER_OP_PARALLELISM_THREADS")}

    def execute(kind, name, case, mode="candidate", suffix=""):
        dest = task / f"{kind}-{mode}{suffix}"
        shutil.copytree(case, dest)
        executable = (prefix if mode == "candidate" else BASELINE) / "bin" / name
        environment = os.environ.copy()
        environment["GPUMD_SRC"] = str((prefix / "share/gpumd/src") if mode == "candidate" else (BASELINE / "src"))
        start = time.monotonic()
        with (dest / "run.log").open("w") as log:
            subprocess.run([executable], cwd=dest, env=environment, check=True, timeout=600,
                           stdout=log, stderr=subprocess.STDOUT)
        elapsed = time.monotonic() - start
        (dest / "execution.json").write_text(json.dumps({"wall_seconds": elapsed, "host": report["host"],
            "job": report["job"], "gpu_visible": report["gpu_visible"], "environment": report["conditions"],
            "executable_sha256": sha(executable)}, sort_keys=True))
        return dest, elapsed

    # Golden values track the resolved upstream, never an old hardcoded SHA.
    reference_e, reference_f = xyz(inputs / "static/gold.xyz")
    candidate_static = None
    for mode in ("candidate", "baseline"):
        case, elapsed = execute("static", "gpumd", inputs / "static", mode)
        energy, forces = xyz(case / "dump.xyz")
        report["checks"][f"static-{mode}"] = {
            "energy_error_ev": compare([[energy]], [[reference_e]], 1e-3, "static energy"),
            "force_max_error_ev_per_a": compare(forces, reference_f, 1e-4, "static forces")}
        if mode == "candidate":
            candidate_static = case
        case, elapsed = execute("prediction", "nep", inputs / "prediction", mode)
        for output in ("energy_train.out", "force_train.out", "virial_train.out"):
            report["checks"][f"prediction-{mode}-{output}"] = compare(
                numbers(case / output), numbers(case / f"gold-{output}"), 2e-4, output)
        for kind, name in (("static", "gpumd"), ("prediction", "nep")):
            # Earlier correctness run is warmup. Three fresh, identical cases.
            samples = [execute(kind, name, inputs / kind, mode, f"-repeat{i}")[1] for i in range(3)]
            report["benchmark"][f"{kind}-{mode}"] = {"seconds": samples,
                "median_seconds": statistics.median(samples), "min_seconds": min(samples), "warmup": 1}

    def engine_metrics(case, elapsed):
        log = (case / "run.log").read_text()
        duration = float(re.findall(r"Time used for this run = (\S+) second", log)[-1])
        speed = float(re.findall(r"Speed of this run = (\S+) atom\*step/second", log)[-1])
        if not math.isfinite(duration + speed) or duration <= 0 or speed <= 0:
            raise ValueError("missing MD engine throughput")
        numbers(case / "thermo.out")
        return {"wall_seconds": elapsed, "engine_seconds": duration, "atom_steps_per_second": speed,
                "steps_per_second": speed / 2000}

    pilot, elapsed = execute("throughput-pilot", "gpumd", inputs / "throughput", "baseline")
    pilot_metrics = engine_metrics(pilot, elapsed)
    # Calibrate using the site binary, then freeze identical inputs for both.
    steps = max(10000, math.ceil(5 / pilot_metrics["engine_seconds"]) * 1000)
    if steps > 1000000:
        raise ValueError("benchmark calibration outside its bounded MD budget")
    throughput = task / "throughput-input"
    shutil.copytree(inputs / "throughput", throughput)
    (throughput / "run.in").write_text((throughput / "run.in").read_text().replace("run 1000\n", f"run {steps}\n"))
    for mode in ("candidate", "baseline"):
        execute("throughput-warmup", "gpumd", throughput, mode)
        samples = []
        for repeat in range(3):
            case, elapsed = execute("throughput", "gpumd", throughput, mode, f"-repeat{repeat}")
            samples.append(engine_metrics(case, elapsed))
        report["benchmark"][f"md-throughput-{mode}"] = {"n_atoms": 2000, "n_steps": steps,
            "warmup": 1, "samples": samples, "input_sha256": sha(throughput / "run.in"),
            "model_sha256": sha(throughput / "model.xyz"),
            "median_atom_steps_per_second": statistics.median(row["atom_steps_per_second"] for row in samples),
            "median_wall_seconds": statistics.median(row["wall_seconds"] for row in samples)}

    train_cases = {}
    for setting in ("off", "on"):
        staged = task / f"training-input-{setting}"
        shutil.copytree(inputs / "training", staged)
        with (staged / "nep.in").open("a") as stream:
            stream.write(f"nep_compile {setting}\n")
        result, _ = execute(f"training-{setting}", "nep", staged)
        loss = numbers(result / "loss.out")
        if len(loss) < 2 or not (result / "nep.txt").is_file():
            raise ValueError("NEP short training did not produce two generations and a model")
        log = (result / "run.log").read_text()
        if setting == "on" and ("Compile specialized NEP training kernels" not in log or
                                "specialization disabled" in log):
            raise ValueError("NEP JIT did not execute successfully; fallback is not acceptance")
        train_cases[setting] = result
    report["checks"]["nep-jit-vs-generic-loss"] = compare(
        numbers(train_cases["on"] / "loss.out"), numbers(train_cases["off"] / "loss.out"), 2e-3, "NEP JIT loss")

    gradient, _ = execute("gnep-train", "gnep", inputs / "gnep")
    if len(numbers(gradient / "loss.out")) != 2 or not (gradient / "nep.txt").is_file():
        raise ValueError("GNEP did not train two epochs")
    # Separate prediction run is required; two training epochs alone produce no force arrays.
    with (gradient / "gnep.in").open("a") as stream:
        stream.write("prediction 1\n")
    gradient_prediction, _ = execute("gnep-prediction", "gnep", gradient)
    predicted_energy = numbers(gradient_prediction / "energy_train.out")
    predicted_force = numbers(gradient_prediction / "force_train.out")
    if len(predicted_energy) != 4 or len(predicted_force) != 160 or any(len(row) != 6 for row in predicted_force):
        raise ValueError("GNEP prediction output has wrong frame/atom count")
    gradient_static = task / "gnep-static-input"
    gradient_static.mkdir()
    (gradient_static / "model.xyz").write_text(first_frame(gradient_prediction / "train.xyz"))
    shutil.copyfile(gradient_prediction / "nep.txt", gradient_static / "nep.txt")
    (gradient_static / "run.in").write_text(STATIC_RUN)
    gradient_md, _ = execute("gnep-static", "gpumd", gradient_static)
    energy, force = xyz(gradient_md / "dump.xyz")
    report["checks"]["gnep-training-and-prediction"] = {
        "energy_error_ev": compare([[energy]], [[predicted_energy[0][0] * 40]], 1e-3, "GNEP vs GPUMD energy"),
        "force_error": compare(force, [row[:3] for row in predicted_force[:40]], 2e-4, "GNEP vs GPUMD forces")}

    plumed = task / "plumed-input"
    shutil.copytree(inputs / "static", plumed)
    (plumed / "run.in").write_text(STATIC_RUN.replace("run 1", "plumed plumed.dat 1 0\nrun 1"))
    (plumed / "plumed.dat").write_text("UNITS LENGTH=A TIME=fs ENERGY=eV\nFLUSH STRIDE=1\nDISTANCE ATOMS=1,2 LABEL=d1\nRESTRAINT ARG=d1 AT=0.0 KAPPA=2.0 LABEL=restraint\nPRINT FILE=colvar ARG=d1,restraint.bias STRIDE=1\n")
    result, _ = execute("plumed", "gpumd", plumed)
    colvar = numbers(result / "colvar")
    delta = [[float(x) for x in line.split()[1:4]] for line in (plumed / "model.xyz").read_text().splitlines()[2:4]]
    displacement = [a - b for a, b in zip(*delta)]
    distance = math.sqrt(sum(value * value for value in displacement))
    compare([[row[1], row[2]] for row in colvar], [[distance, distance ** 2]] * len(colvar), 2e-5, "PLUMED colvar/bias")
    _, biased_force = xyz(result / "dump.xyz")
    _, unbiased_force = xyz(candidate_static / "dump.xyz")
    expected_force = [row.copy() for row in unbiased_force]
    for axis in range(3):
        expected_force[0][axis] -= 2 * displacement[axis]
        expected_force[1][axis] += 2 * displacement[axis]
    report["checks"]["plumed-force-feedback"] = compare(biased_force, expected_force, 2e-4, "PLUMED force feedback")

    deepmd = task / "deepmd-input"
    shutil.copytree(inputs / "deepmd", deepmd)
    with (deepmd / "model-generation.log").open("w") as log:
        subprocess.run([os.environ["DEEPMD_PYTHON"], str(Path(__file__).with_name("gpumd_deepmd_probe.py")), deepmd],
                       check=True, timeout=900, stdout=log, stderr=subprocess.STDOUT)
    reference = json.loads((deepmd / "reference.json").read_text())
    for mode in ("candidate", "baseline"):
        result, _ = execute("deepmd", "gpumd", deepmd, mode)
        energy, force = xyz(result / "dump.xyz")
        report["checks"][f"deepmd-{mode}"] = {
            "energy_error_ev": compare([[energy]], [[reference["energy"]]], 1e-4, "DeepMD energy"),
            "force_error": compare(force, reference["forces"], 1e-4, "DeepMD force")}
    report["checks"]["installed-jit-resources-from-unrelated-cwd"] = True
    checks, benchmark = recheck_results(task, report)
    if checks != report["checks"] or benchmark != report["benchmark"]:
        raise ValueError("independent raw-output recheck disagrees with the producer")
    report["files"] = {str(path.relative_to(task)): sha(path) for path in sorted(task.rglob("*"))
                       if path.is_file() and path.name != "science.json"}
    (task / "science.json").write_text(json.dumps(report, sort_keys=True) + "\n")
    print(json.dumps({"GPUMD_SCIENTIFIC_ACCEPTANCE": True, "checks": report["checks"], "benchmark": report["benchmark"]}, sort_keys=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("prepare", "inspect", "run"))
    parser.add_argument("prefix", type=Path)
    parser.add_argument("destination", type=Path, nargs="?")
    arguments = parser.parse_args()
    if arguments.operation == "prepare":
        prepare(arguments.prefix, arguments.destination)
    elif arguments.operation == "inspect":
        print(json.dumps(inspect(arguments.prefix), sort_keys=True))
    else:
        run(arguments.prefix, arguments.destination)
