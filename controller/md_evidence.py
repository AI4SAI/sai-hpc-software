"""Fail-closed, dependency-free validation of MD acceptance evidence.

``verify_parity(baseline, candidate)`` accepts two dictionaries containing:
  lammps: {version: str, packages: [str], styles: {category: [str]}}
  deepmd: {version: str, backends: [str]}  # tf, pt, jax, optionally others
  plumed: {version: str, kernel_sha256: str, features: [str]}
The full 14-category LAMMPS registry is mandatory; style registration alone
does not demonstrate scientific functionality. Callers must also run models.

``verify_benchmark(records)`` accepts a list of dictionaries containing:
  implementation: "baseline" or "candidate"
  node: str; resources: nonempty dict; input_sha256: 64 lowercase hex digits
  warmup: bool; seconds: positive finite number
  observables, reference: {energy, forces, virial: number or nested lists}
  tolerances: {energy, forces, virial: {atol: number, rtol: number}}
All records must share node/resources/input/reference/tolerances. Each side
needs an initial warmup and at least three measured runs. Every observable,
including warmup, is checked using abs(observed-reference) <= atol+rtol*abs(ref).
Medians/speedup are reported, without silently imposing a performance cutoff.

``audit_runtime_metadata(value)`` accepts only caller-selected *runtime*
metadata (strings or JSON-compatible containers), never build provenance:
build logs may legitimately mention /workspace whereas runtime paths may not.
All validators raise ValueError for missing, malformed, or failing evidence.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from collections.abc import Mapping


STYLE_CATEGORIES = (
    "atom", "integrate", "minimize", "pair", "bond", "angle", "dihedral",
    "improper", "kspace", "fix", "compute", "region", "dump", "command",
)
OBSERVABLES = ("energy", "forces", "virial")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_./+\-]+\Z")
_ALIASES = {"integrator": "integrate", "integration": "integrate",
            "minimization": "minimize", "minimizer": "minimize",
            "k-space": "kspace"}


def _mapping(value, name):
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _names(value, name):
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{name} must be a nonempty list")
    result = [_text(item, name) for item in value]
    if any(item != item.strip() for item in result) or len(set(result)) != len(result):
        raise ValueError(f"{name} has whitespace or duplicate entries")
    return set(result)


def _number(value, name, *, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if positive and number <= 0 or nonnegative and number < 0:
        raise ValueError(f"{name} is outside its allowed range")
    return number


def _hash(value, name):
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{name} must be a SHA256 hex digest")
    return value


def parse_lammps_help(text):
    """Parse a complete ``lmp -h`` string; reject partial/truncated registries.

    Accepts ``* Pair styles:`` and ``Pair styles:`` headings, wrapped rows,
    and the common Integrator/Minimization spelling variants. The version
    comes from the LAMMPS banner, never from unrelated library versions.
    """
    _text(text, "LAMMPS help")
    version = None
    packages = []
    styles = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        banner = re.search(r"(?:Large-scale Atomic/Molecular Massively Parallel "
                           r"Simulator\s*-\s*(.+)|\bLAMMPS\s*\(([^)]+)\))", line)
        if banner:
            version = next(group for group in banner.groups() if group)
        header = re.fullmatch(r"\*?\s*Installed packages\s*:\s*(.*)", line, re.I)
        if header:
            if packages:
                raise ValueError("duplicate installed packages section")
            current = packages
            line = header.group(1)
        else:
            header = re.fullmatch(r"\*?\s*([A-Za-z-]+) styles\s*:\s*(.*)", line, re.I)
            if header:
                kind = header.group(1).lower()
                kind = _ALIASES.get(kind, kind)
                if kind not in STYLE_CATEGORIES:
                    raise ValueError(f"unknown LAMMPS style category: {kind}")
                if kind in styles:
                    raise ValueError(f"duplicate LAMMPS style section: {kind}")
                current = styles[kind] = []
                line = header.group(2)
        if not line:
            if current:
                current = None
            continue
        if current is not None:
            tokens = line.split()
            if not all(_TOKEN.fullmatch(token) for token in tokens):
                raise ValueError(f"unrecognized LAMMPS registry line: {line}")
            current.extend(tokens)
    result = {"version": version, "packages": packages, "styles": styles}
    _validate_lammps(result, "LAMMPS help")
    return result


def _validate_lammps(value, name):
    value = _mapping(value, name)
    _text(value.get("version"), f"{name}.version")
    packages = _names(value.get("packages"), f"{name}.packages")
    styles = _mapping(value.get("styles"), f"{name}.styles")
    missing = set(STYLE_CATEGORIES) - styles.keys()
    if missing:
        raise ValueError(f"{name}: missing style categories {sorted(missing)}")
    return packages, {kind: _names(items, f"{name}.styles.{kind}")
                      for kind, items in styles.items()}


def verify_parity(baseline, candidate):
    """Require full capability supersets and the exact reused PLUMED kernel."""
    baseline = _mapping(baseline, "baseline")
    candidate = _mapping(candidate, "candidate")
    before_packages, before_styles = _validate_lammps(baseline.get("lammps"), "baseline.lammps")
    after_packages, after_styles = _validate_lammps(candidate.get("lammps"), "candidate.lammps")
    missing = before_packages - after_packages
    if missing:
        raise ValueError(f"missing LAMMPS packages: {sorted(missing)}")
    for kind, required in before_styles.items():
        missing = required - after_styles.get(kind, set())
        if missing:
            raise ValueError(f"missing LAMMPS {kind} styles: {sorted(missing)}")
    backend_sets = []
    for side, evidence in (("baseline", baseline), ("candidate", candidate)):
        deepmd = _mapping(evidence.get("deepmd"), f"{side}.deepmd")
        _text(deepmd.get("version"), f"{side}.deepmd.version")
        backends = _names(deepmd.get("backends"), f"{side}.deepmd.backends")
        backends = {{"tensorflow": "tf", "pytorch": "pt"}.get(b.lower(), b.lower())
                    for b in backends}
        if not {"tf", "pt", "jax"} <= backends:
            raise ValueError(f"{side}: TF, PT, and JAX backends are mandatory")
        backend_sets.append(backends)
    if not backend_sets[0] <= backend_sets[1]:
        raise ValueError("candidate is missing baseline DeePMD backends")
    kernels, features = [], []
    for side, evidence in (("baseline", baseline), ("candidate", candidate)):
        plumed = _mapping(evidence.get("plumed"), f"{side}.plumed")
        _text(plumed.get("version"), f"{side}.plumed.version")
        kernels.append(_hash(plumed.get("kernel_sha256"), f"{side}.plumed.kernel_sha256"))
        features.append(_names(plumed.get("features"), f"{side}.plumed.features"))
    if kernels[0] != kernels[1]:
        raise ValueError("PLUMED kernel differs from the preinstalled baseline")
    if not features[0] <= features[1]:
        raise ValueError(f"missing PLUMED features: {sorted(features[0] - features[1])}")
    return {"passed": True, "packages": len(after_packages),
            "styles": {key: len(value) for key, value in after_styles.items()},
            "backends": sorted(backend_sets[1]), "plumed_kernel_sha256": kernels[1]}


def _compare_numeric(observed, reference, atol, rtol, path):
    if isinstance(reference, list):
        if not reference or not isinstance(observed, list) or len(observed) != len(reference):
            raise ValueError(f"{path}: empty or mismatched numeric array shape")
        for index, (actual, expected) in enumerate(zip(observed, reference)):
            _compare_numeric(actual, expected, atol, rtol, f"{path}[{index}]")
        return
    expected = _number(reference, f"{path} reference")
    actual = _number(observed, path)
    limit = atol + rtol * abs(expected)
    if not math.isfinite(limit):
        raise ValueError(f"{path}: tolerance overflow")
    if abs(actual - expected) > limit:
        raise ValueError(f"{path}: {actual} differs from reference {expected}")


def verify_benchmark(records):
    """Validate equal conditions and numerical correctness; return raw medians."""
    if not isinstance(records, list) or not records:
        raise ValueError("benchmark records must be a nonempty list")
    identity = None
    times = {"baseline": [], "candidate": []}
    warmups = {"baseline": 0, "candidate": 0}
    for index, record in enumerate(records):
        record = _mapping(record, f"record {index}")
        side = record.get("implementation")
        if side not in times:
            raise ValueError("implementation must be baseline or candidate")
        node = _text(record.get("node"), "node")
        resources = _mapping(record.get("resources"), "resources")
        if not resources:
            raise ValueError("resources must describe the allocation")
        digest = _hash(record.get("input_sha256"), "input_sha256")
        seconds = _number(record.get("seconds"), "seconds", positive=True)
        warmup = record.get("warmup")
        if not isinstance(warmup, bool):
            raise ValueError("warmup must be boolean")
        observed = _mapping(record.get("observables"), "observables")
        reference = _mapping(record.get("reference"), "reference")
        tolerances = _mapping(record.get("tolerances"), "tolerances")
        for name in OBSERVABLES:
            tolerance = _mapping(tolerances.get(name), f"{name} tolerances")
            atol = _number(tolerance.get("atol"), f"{name}.atol", nonnegative=True)
            rtol = _number(tolerance.get("rtol"), f"{name}.rtol", nonnegative=True)
            _compare_numeric(observed.get(name), reference.get(name), atol, rtol, name)
        if set(observed) != set(reference) or set(observed) != set(OBSERVABLES):
            raise ValueError("observables/reference must contain exactly energy, forces, virial")
        # Serialization checks resource values too (NaN is not valid evidence).
        try:
            current = json.dumps([node, resources, digest, reference, tolerances],
                                 sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("benchmark identity must be finite JSON data") from exc
        if identity is None:
            identity = current
        elif identity != current:
            raise ValueError("node/resources/input/reference/tolerances differ between records")
        if warmup:
            if times[side]:
                raise ValueError(f"{side}: warmup must precede measurements")
            warmups[side] += 1
        else:
            if not warmups[side]:
                raise ValueError(f"{side}: measurement before warmup")
            times[side].append(seconds)
    for side in times:
        if warmups[side] < 1 or len(times[side]) < 3:
            raise ValueError(f"{side}: require warmup and at least three measurements")
    medians = {side: statistics.median(values) for side, values in times.items()}
    speedup = medians["baseline"] / medians["candidate"]
    if not math.isfinite(speedup):
        raise ValueError("speedup overflow")
    return {"passed": True, "median_seconds": medians, "speedup": speedup,
            "warmups": warmups, "measurements": {side: len(values) for side, values in times.items()},
            "performance_threshold_applied": False}


def audit_runtime_metadata(value):
    """Reject build-tree leakage in explicitly selected runtime metadata only."""
    if isinstance(value, str):
        for forbidden in ("/workspace", "/control",
                          "/home/stardust/sai-hpc-software/controller"):
            if re.search(r"(?<![A-Za-z0-9_./-])" + re.escape(forbidden)
                         + r"(?![A-Za-z0-9_.-])", value):
                raise ValueError(f"runtime metadata leaks build path: {forbidden}")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("runtime metadata keys must be strings")
            audit_runtime_metadata(key)
            audit_runtime_metadata(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            audit_runtime_metadata(item)
    elif value is None or isinstance(value, bool):
        pass
    elif isinstance(value, (int, float)):
        _number(value, "runtime metadata")
    else:
        raise ValueError("runtime metadata must be JSON-compatible")
    return {"passed": True}
