#!/usr/bin/env python3
"""Real frozen-water inference/PLUMED acceptance; never run on login hosts.

prepare(source_root, out_dir) reads upstream fixtures as DATA via an AST allowlist,
converts the small TensorFlow graph, and optionally converts that *same model*
to PT/JAX. No upstream Python module is executed. Generated references include
all 18 force and 9 total-virial components. Conversion and inference require an
verified, running Slurm allocation on this node. Network-isolated Apptainer
containers require allocation attestations passed by the trusted host renderer;
the presence of a container marker alone never authorizes computation.

The CLI provides prepare, python-eval, lammps-run, and verify subcommands.
Each engine emits md_evidence benchmark records: one warmup + >=3 runs, explicit
atol/rtol, shared input hashes, reference observables, allocation details. Python
timings measure warmed inference; LAMMPS timings measure end-to-end processes
and include startup. Never compare timings across those two engine categories.
verify requires BOTH engines for TF/PT/JAX; a TF-only success is not completion.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

from md_evidence import _compare_numeric, verify_benchmark


BACKENDS = {"tf": "model.pb", "pt": "model.pth", "jax": "model.savedmodel"}
STRESS_ORDER = (0, 4, 8, 3, 6, 7, 1, 2, 5)
NKTV2P = 1.6021765e6  # LAMMPS metal units; upstream source/lmp/tests/constants.py
TOLERANCES = {name: {"atol": 1e-7, "rtol": 1e-5}
              for name in ("energy", "forces", "virial")}


def require_execution_context():
    """Require allocated compute-node execution, including inside containers.

    Host execution queries Slurm directly. The trusted host renderer must pass
    SAI_MD_ALLOCATED_JOB and SAI_MD_ALLOCATED_NODE into network-isolated runtime
    containers after its allocation check. Merely entering Apptainer on a login
    node is insufficient, and stale attestations for another node are rejected.
    """
    contained = Path("/.singularity.d").is_dir()
    node = os.uname().nodename.split(".")[0]
    if contained:
        job_id = os.environ.get("SAI_MD_ALLOCATED_JOB", "")
        attested_node = os.environ.get("SAI_MD_ALLOCATED_NODE", "")
        if not re.fullmatch(r"[0-9]+", job_id) or not attested_node:
            raise RuntimeError("container requires trusted Slurm allocation attestations")
        if attested_node.split(".")[0] != node:
            raise RuntimeError("container is outside the attested compute node")
    else:
        job_id = os.environ.get("SLURM_JOB_ID", "")
        if not re.fullmatch(r"[0-9]+", job_id):
            raise RuntimeError("scientific execution requires a running Slurm allocation")
        job = subprocess.check_output(["scontrol", "show", "job", "-o", job_id], text=True)
        nodes = re.search(r"(?:^|\s)NodeList=(\S+)", job)
        if "JobState=RUNNING" not in job or not nodes:
            raise RuntimeError("Slurm allocation is not running")
        hosts = subprocess.check_output(["scontrol", "show", "hostnames", nodes.group(1)], text=True).split()
        if node not in {h.split(".")[0] for h in hosts}:
            raise RuntimeError("this host is outside the running Slurm allocation")
    tmp = Path(os.environ.get("TMPDIR", "/tmp")).resolve()
    if not contained and (tmp == Path("/tmp") or Path("/tmp") in tmp.parents):
        raise RuntimeError("host /tmp is forbidden; use the project allocation scratch directory")


def _flat(value):
    if isinstance(value, list):
        return [number for item in value for number in _flat(item)]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("reference contains non-finite or non-numeric data")
    return [value]


def _literal(node):
    """Evaluate only numerical literals, np.array(...), reshape and unary minus."""
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_literal(item) for item in node.elts]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _literal(node.operand)
        sign = -1 if isinstance(node.op, ast.USub) else 1
        def signed(item):
            return [signed(x) for x in item] if isinstance(item, list) else sign * item
        return signed(value)
    if isinstance(node, ast.Call) and not node.keywords and isinstance(node.func, ast.Attribute):
        if isinstance(node.func.value, ast.Name) and node.func.value.id == "np" and node.func.attr == "array" and len(node.args) == 1:
            return _literal(node.args[0])
        if node.func.attr == "reshape" and len(node.args) == 2:
            shape = [_literal(n) for n in node.args]
            if any(type(n) is not int or n < 1 for n in shape):
                raise ValueError("invalid reference reshape")
            values = _flat(_literal(node.func.value))
            if len(values) != shape[0] * shape[1]:
                raise ValueError("reference reshape size mismatch")
            return [values[i:i + shape[1]] for i in range(0, len(values), shape[1])]
    raise ValueError("unsupported expression in upstream numeric reference")


def parse_reference(source_text):
    """Statically extract the six-water-atom oracle without importing test code."""
    required = {"expected_ae", "expected_f", "expected_v", "coord", "box", "type_OH"}
    values = {}
    for statement in ast.parse(source_text).body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            name = statement.targets[0].id
            if name in required:
                if name in values:
                    raise ValueError(f"duplicate reference assignment: {name}")
                values[name] = _literal(statement.value)
    if values.keys() != required:
        raise ValueError("upstream frozen-water reference is incomplete")
    shapes = {"expected_ae": (6,), "expected_f": (6, 3), "expected_v": (6, 9),
              "coord": (6, 3), "box": (9,), "type_OH": (6,)}
    for name, shape in shapes.items():
        array = values[name]
        if not isinstance(array, list) or len(array) != shape[0]:
            raise ValueError(f"invalid reference shape: {name}")
        if len(shape) == 2 and any(not isinstance(row, list) or len(row) != shape[1] for row in array):
            raise ValueError(f"invalid reference shape: {name}")
        _flat(array)
    if values["box"] != [0, 13, 0, 13, 0, 13, 0, 0, 0] or values["type_OH"] != [1, 2, 2, 1, 2, 2]:
        raise ValueError("upstream water topology changed; review the scientific fixture")
    # Upstream expected_v is NEGATIVE physical virial (centroid/stress/atom).
    virial = [-math.fsum(row[j] for row in values["expected_v"]) for j in range(9)]
    reference = {"energy": math.fsum(values["expected_ae"]),
                 "forces": values["expected_f"], "virial": virial}
    delta = [values["coord"][1][i] - values["coord"][0][i] for i in range(3)]
    distance = math.sqrt(sum((d - 13 * round(d / 13)) ** 2 for d in delta))
    return {"reference": reference, "coordinates": values["coord"],
            "atom_types": [n - 1 for n in values["type_OH"]],
            "box": [13, 0, 0, 0, 13, 0, 0, 0, 13], "distance_angstrom": distance}


def render_data(fixture):
    rows = ["SAI frozen-water regression", "", "6 atoms", "2 atom types", "",
            "0 13 xlo xhi", "0 13 ylo yhi", "0 13 zlo zhi", "", "Atoms # atomic", ""]
    for i, (kind, xyz) in enumerate(zip(fixture["atom_types"], fixture["coordinates"]), 1):
        rows.append(f"{i} {kind + 1} " + " ".join(format(x, ".17g") for x in xyz))
    return "\n".join(rows) + "\n"


def render_lammps_input(backend="tf"):
    if backend not in BACKENDS:
        raise ValueError("unknown backend")
    # No time-integration fix: coordinates stay fixed while PLUMED really runs.
    lines = ["units metal", "boundary p p p", "atom_style atomic", "atom_modify map array",
             "read_data data.lmp", "mass 1 16", "mass 2 2", "neighbor 2.0 bin",
             "neigh_modify every 1 delay 0 check yes", f"pair_style deepmd {BACKENDS[backend]}",
             "pair_coeff * *", "timestep 0.0005", "compute sai_virial all centroid/stress/atom NULL pair",
             "fix sai_plumed all plumed plumedfile plumed.dat outfile plumed.log",
             "thermo 1", "thermo_style custom step pe", "thermo_modify format float %.17g",
             "run 1", 'print "SAI_ENERGY = $(pe:%.17g)"',
             "write_dump all custom result.dump id fx fy fz "
             + " ".join(f"c_sai_virial[{j}]" for j in range(1, 10))
             + " modify sort id format float %.17g"]
    return "\n".join(lines) + "\n"


PLUMED_INPUT = "UNITS LENGTH=A\nsai_distance: DISTANCE ATOMS=1,2\nPRINT ARG=sai_distance FILE=COLVAR STRIDE=1 FMT=%.17g\n"


def file_digest(path):
    """Hash a file or SavedModel directory deterministically, rejecting symlinks."""
    path = Path(path)
    digest = hashlib.sha256()
    files = sorted(path.rglob("*")) if path.is_dir() else [path]
    for item in files:
        if item.is_symlink():
            raise ValueError("scientific model contains a symlink")
        if not item.is_file():
            continue
        if path.is_dir():
            digest.update(item.relative_to(path).as_posix().encode() + b"\0")
        with item.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
    return digest.hexdigest()


def prepare(source_root, out_dir, backends=("tf", "pt", "jax")):
    """Generate model/data/input manifest only in an allocated compute context."""
    require_execution_context()
    if not backends or len(set(backends)) != len(backends) or any(b not in BACKENDS for b in backends):
        raise ValueError("invalid backend selection")
    source = Path(source_root)
    oracle = source / "source/lmp/tests/test_lammps.py"
    graph = source / "source/tests/infer/deeppot.pbtxt"
    fixture = parse_reference(oracle.read_text())
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=False)
    subprocess.run([sys.executable, "-m", "deepmd", "convert-from", "pbtxt", "-i", str(graph.resolve()),
                    "-o", str(out / BACKENDS["tf"])], check=True, cwd=out)
    fixture.update({"schema": 1, "source_reference_sha256": file_digest(oracle),
                    "source_graph_sha256": file_digest(graph), "models": {}, "tolerances": TOLERANCES,
                    "required_backends": list(BACKENDS), "prepared_backends": list(backends)})
    (out / "data.lmp").write_text(render_data(fixture))
    (out / "plumed.dat").write_text(PLUMED_INPUT)
    for backend in backends:
        model = out / BACKENDS[backend]
        if backend != "tf":
            subprocess.run([sys.executable, "-m", "deepmd", "convert-backend", str(out / BACKENDS["tf"]),
                            str(model)], check=True, cwd=out)
        if not model.exists():
            raise ValueError(f"conversion did not produce {backend} model")
        input_path = out / f"in.{backend}"
        input_path.write_text(render_lammps_input(backend))
        parts = [file_digest(path) for path in (model, out / "data.lmp", out / "plumed.dat", input_path)]
        fixture["models"][backend] = {"file": model.name, "sha256": parts[0],
            "input_sha256": hashlib.sha256("\n".join(parts).encode()).hexdigest()}
    (out / "fixture.json").write_text(json.dumps(fixture, sort_keys=True, indent=2) + "\n")
    return fixture


def parse_lammps_output(stdout, dump_text):
    """Parse energy and ID-sorted forces; turn stress*volume/bar into eV virial."""
    energy = re.findall(r"^SAI_ENERGY = (\S+)\s*$", stdout, re.M)
    if len(energy) != 1:
        raise ValueError("missing or duplicate LAMMPS energy marker")
    lines = dump_text.strip().splitlines()
    if len(lines) != 15 or lines[0] != "ITEM: TIMESTEP" or lines[2] != "ITEM: NUMBER OF ATOMS" or lines[3] != "6":
        raise ValueError("expected exactly one six-atom LAMMPS dump frame")
    expected = ["id", "fx", "fy", "fz"] + [f"c_sai_virial[{j}]" for j in range(1, 10)]
    if lines[8].split() != ["ITEM:", "ATOMS", *expected]:
        raise ValueError("LAMMPS dump columns do not match the scientific contract")
    rows = {}
    for line in lines[9:]:
        fields = line.split()
        if len(fields) != len(expected):
            raise ValueError("invalid LAMMPS dump row")
        atom_id = int(fields[0])
        if atom_id in rows:
            raise ValueError("duplicate LAMMPS atom ID")
        rows[atom_id] = [float(item) for item in fields[1:]]
    if set(rows) != set(range(1, 7)):
        raise ValueError("missing LAMMPS atom IDs")
    forces = [rows[i][:3] for i in range(1, 7)]
    virial = [0.0] * 9
    for column, tensor_index in enumerate(STRESS_ORDER):
        virial[tensor_index] = -math.fsum(row[column + 3] for row in rows.values()) / NKTV2P
    result = {"energy": float(energy[0]), "forces": forces, "virial": virial}
    for value in result.values():
        _flat(value)
    return result


def verify_plumed_output(text, expected_distance, atol=1e-8):
    _flat([expected_distance, atol])
    if expected_distance <= 0 or atol < 0:
        raise ValueError("invalid PLUMED reference distance or tolerance")
    fields = None
    rows = []
    for line in text.splitlines():
        if line.startswith("#! FIELDS "):
            fields = line.split()[2:]
        elif line.strip() and not line.startswith("#"):
            if not fields or len(line.split()) != len(fields):
                raise ValueError("invalid PLUMED output columns")
            row = dict(zip(fields, map(float, line.split())))
            if "sai_distance" not in row or "time" not in row:
                raise ValueError("PLUMED distance output is missing")
            _flat(list(row.values()))
            if abs(row["sai_distance"] - expected_distance) > atol:
                raise ValueError("PLUMED distance differs from the known geometry")
            rows.append(row)
    if not rows or max(row["time"] for row in rows) <= 0:
        raise ValueError("PLUMED did not execute a positive simulation timestep")
    return {"passed": True, "distance_angstrom": expected_distance, "samples": len(rows)}


def _load_fixture(case, backend):
    fixture = json.loads((case / "fixture.json").read_text())
    model = fixture["models"][backend]
    parts = [file_digest(path) for path in (case / model["file"], case / "data.lmp",
                                           case / "plumed.dat", case / f"in.{backend}")]
    if parts[0] != model["sha256"] or hashlib.sha256("\n".join(parts).encode()).hexdigest() != model["input_sha256"]:
        raise ValueError("scientific fixture hash mismatch")
    return fixture


def _record(fixture, backend, engine, implementation, resources, warmup, seconds, observed):
    if implementation not in ("baseline", "candidate"):
        raise ValueError("invalid implementation label")
    if not isinstance(resources, dict) or not resources:
        raise ValueError("explicit allocation resources are required")
    for name in TOLERANCES:
        tolerance = fixture["tolerances"][name]
        _compare_numeric(observed[name], fixture["reference"][name], **tolerance, path=name)
    return {"implementation": implementation, "backend": backend, "engine": engine,
            "node": os.uname().nodename, "resources": resources,
            "input_sha256": fixture["models"][backend]["input_sha256"],
            "warmup": warmup, "seconds": seconds, "observables": observed,
            "reference": fixture["reference"], "tolerances": fixture["tolerances"]}


def run_python(case_dir, backend, implementation, resources, repeats=3):
    require_execution_context()
    if repeats < 3:
        raise ValueError("at least three measurements are required")
    case = Path(case_dir).resolve()
    fixture = _load_fixture(case, backend)
    from deepmd.infer import DeepPot  # Scientific libraries imported only past guard.
    model = DeepPot(str(case / fixture["models"][backend]["file"]))
    records = []
    for index in range(repeats + 1):
        start = time.perf_counter()
        energy, force, virial = model.eval(fixture["coordinates"], fixture["box"], fixture["atom_types"])
        # Conversion to host lists synchronizes returned device results before timing.
        observed = {"energy": float(energy.reshape(-1)[0]),
                    "forces": force.reshape(6, 3).tolist(), "virial": virial.reshape(9).tolist()}
        seconds = time.perf_counter() - start
        records.append(_record(fixture, backend, "python", implementation, resources, index == 0, seconds, observed))
    return records


def copy_trial_inputs(case_dir, trial_dir, backend, fixture):
    """Copy the small task inputs so a trial=/work-only bind remains sufficient.

    These are scientific job inputs, not unpacked source or installation trees.
    In particular, a SavedModel directory must be copied rather than linked to
    a host/canonical path that may not be visible in the runtime container.
    """
    if backend not in BACKENDS or fixture["models"][backend]["file"] != BACKENDS[backend]:
        raise ValueError("unexpected model filename in scientific fixture")
    case = Path(case_dir)
    trial = Path(trial_dir)
    for name in (BACKENDS[backend], "data.lmp", "plumed.dat", f"in.{backend}"):
        source, target = case / name, trial / name
        if target.exists() or target.is_symlink():
            raise ValueError("trial input destination already exists")
        if source.is_dir():
            shutil.copytree(source, target, symlinks=False)
        else:
            shutil.copy2(source, target, follow_symlinks=True)


def run_lammps(case_dir, backend, implementation, resources, executable, output_dir, repeats=3, launcher=()):
    require_execution_context()
    if repeats < 3:
        raise ValueError("at least three measurements are required")
    case = Path(case_dir).resolve()
    fixture = _load_fixture(case, backend)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for index in range(repeats + 1):
        trial = output / str(index)
        trial.mkdir()
        copy_trial_inputs(case, trial, backend, fixture)
        start = time.perf_counter()
        result = subprocess.run([*launcher, str(executable), "-log", "log.lammps", "-in", f"in.{backend}"],
                                cwd=trial, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
        seconds = time.perf_counter() - start
        (trial / "stdout.txt").write_text(result.stdout)
        result.check_returncode()
        observed = parse_lammps_output(result.stdout, (trial / "result.dump").read_text())
        plumed = verify_plumed_output((trial / "COLVAR").read_text(), fixture["distance_angstrom"])
        record = _record(fixture, backend, "lammps", implementation, resources, index == 0, seconds, observed)
        record["plumed"] = plumed
        record["timing_scope"] = "end-to-end process including startup"
        records.append(record)
    return records


def verify_science(records):
    """Fail closed unless both implementations ran BOTH engines for TF/PT/JAX."""
    groups = {(backend, engine): [] for backend in BACKENDS for engine in ("python", "lammps")}
    for record in records:
        key = (record.get("backend"), record.get("engine"))
        if key not in groups:
            raise ValueError("unknown science backend/engine")
        if key[1] == "lammps" and record.get("plumed", {}).get("passed") is not True:
            raise ValueError("LAMMPS science record lacks successful PLUMED validation")
        groups[key].append(record)
    reports = {f"{backend}/{engine}": verify_benchmark(rows) for (backend, engine), rows in groups.items()}
    # Within each backend both engines must exercise the exact same model/input.
    for backend in BACKENDS:
        identity = {(r["input_sha256"], json.dumps(r["reference"], sort_keys=True))
                    for engine in ("python", "lammps") for r in groups[backend, engine]}
        if len(identity) != 1:
            raise ValueError("Python/LAMMPS are not testing the same model and reference")
    return {"passed": True, "complete_backends": list(BACKENDS), "benchmarks": reports}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("source_root")
    prep.add_argument("out_dir")
    prep.add_argument("--backends", nargs="+", choices=BACKENDS, default=list(BACKENDS))
    for engine in ("python-eval", "lammps-run"):
        run = commands.add_parser(engine)
        run.add_argument("case_dir")
        run.add_argument("--backend", choices=BACKENDS, required=True)
        run.add_argument("--implementation", choices=("baseline", "candidate"), required=True)
        run.add_argument("--resources-json", required=True)
        run.add_argument("--records", required=True)
        run.add_argument("--repeats", type=int, default=3)
        if engine == "lammps-run":
            run.add_argument("--executable", required=True)
            run.add_argument("--output-dir", required=True)
            run.add_argument("--launcher-json", default="[]")
    verify = commands.add_parser("verify")
    verify.add_argument("records", nargs="+")
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.source_root, args.out_dir, args.backends)
    elif args.command == "verify":
        result = verify_science([record for path in args.records for record in json.loads(Path(path).read_text())])
    else:
        common = (args.case_dir, args.backend, args.implementation, json.loads(args.resources_json))
        if args.command == "python-eval":
            result = run_python(*common, repeats=args.repeats)
        else:
            result = run_lammps(*common, args.executable, args.output_dir,
                                repeats=args.repeats, launcher=json.loads(args.launcher_json))
        with Path(args.records).open("x") as stream:
            json.dump(result, stream, sort_keys=True, indent=2, allow_nan=False)
    print(json.dumps(result, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
