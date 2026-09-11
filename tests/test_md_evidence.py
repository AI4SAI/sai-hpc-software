import copy
import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controller"))
from md_evidence import (STYLE_CATEGORIES, audit_runtime_metadata,
                         parse_lammps_help, verify_benchmark, verify_parity)


def capabilities():
    return {
        "lammps": {"version": "4 Jul 2026", "packages": ["KOKKOS", "PLUMED"],
                   "styles": {kind: [kind + "/example"] for kind in STYLE_CATEGORIES}},
        "deepmd": {"version": "3.2.0", "backends": ["tf", "pt", "jax"]},
        "plumed": {"version": "2.10.1", "kernel_sha256": "a" * 64,
                   "features": ["MPI", "FFTW", "OPENMP"]},
    }


def benchmarks():
    records = []
    reference = {"energy": -2.0, "forces": [[0.0, 1.0, -1.0]],
                 "virial": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]}
    for side, duration in (("baseline", 2.0), ("candidate", 1.0)):
        for index in range(4):
            records.append({
                "implementation": side, "node": "gpu-node-1",
                "resources": {"ranks": 2, "gpus": 2, "threads_per_rank": 1},
                "input_sha256": "b" * 64, "warmup": index == 0,
                "seconds": duration if index else 10 * duration,
                "observables": copy.deepcopy(reference),
                "reference": copy.deepcopy(reference),
                "tolerances": {name: {"atol": 1e-8, "rtol": 1e-6} for name in reference},
            })
    return records


class LammpsHelpTests(unittest.TestCase):
    def help_text(self, star=False):
        heading = "* " if star else ""
        lines = ["Large-scale Atomic/Molecular Massively Parallel Simulator - 4 Jul 2026",
                 "", "Installed packages:", "", "KOKKOS PLUMED", ""]
        for kind in STYLE_CATEGORIES:
            display = {"integrate": "Integrator", "minimize": "Minimization"}.get(kind, kind.title())
            lines.extend([f"{heading}{display} styles:", "", f"{kind}/one {kind}/two", ""])
        return "\n".join(lines)

    def test_full_help_variants(self):
        for star in (False, True):
            with self.subTest(star=star):
                result = parse_lammps_help(self.help_text(star))
                self.assertEqual(result["version"], "4 Jul 2026")
                self.assertEqual(result["packages"], ["KOKKOS", "PLUMED"])
                self.assertEqual(result["styles"]["pair"], ["pair/one", "pair/two"])

    def test_lammps_parenthesized_banner(self):
        text = self.help_text().replace(
            "Large-scale Atomic/Molecular Massively Parallel Simulator - 4 Jul 2026",
            "LAMMPS (4 Jul 2026)")
        self.assertEqual(parse_lammps_help(text)["version"], "4 Jul 2026")

    def test_reject_partial_unknown_and_duplicate(self):
        good = self.help_text()
        for text in ("", "CUDA error 100", good.split("Pair styles:")[0],
                     good.replace("Atom styles:", "Quantum styles:"),
                     good + "\nPair styles:\nlj/cut\n", good.replace("KOKKOS PLUMED", "KOKKOS KOKKOS"),
                     good.replace("pair/one pair/two", "error: unexpected failure!")):
            with self.subTest(text=text[-60:]), self.assertRaises(ValueError):
                parse_lammps_help(text)


class ParityTests(unittest.TestCase):
    def test_checked_in_site_registry_is_complete(self):
        path = Path(__file__).resolve().parents[1] / "docs/evidence/deepmd-lammps-site-2026-09-11.json"
        evidence = json.loads(path.read_text())
        report = verify_parity(evidence, evidence)
        self.assertEqual(report["packages"], 70)
        self.assertEqual(sum(report["styles"].values()), 2197)
        self.assertFalse(evidence["collection"]["scientific_acceptance"])

    def test_superset_and_backend_aliases(self):
        baseline = capabilities()
        candidate = copy.deepcopy(baseline)
        candidate["lammps"]["packages"].append("GPU")
        candidate["lammps"]["styles"]["pair"].append("deepmd/kk")
        candidate["deepmd"]["backends"] = ["tensorflow", "pytorch", "jax", "ptexpt"]
        self.assertTrue(verify_parity(baseline, candidate)["passed"])

    def test_fail_closed_capability_loss(self):
        changes = [
            lambda x: x["lammps"].update(version=""),
            lambda x: x["lammps"]["packages"].remove("PLUMED"),
            lambda x: x["lammps"]["styles"].pop("command"),
            lambda x: x["lammps"]["styles"].update(pair=["wrong"]),
            lambda x: x["deepmd"].update(version=" "),
            lambda x: x["deepmd"]["backends"].remove("jax"),
            lambda x: x["plumed"].update(kernel_sha256="c" * 64),
            lambda x: x["plumed"].update(kernel_sha256="unknown"),
            lambda x: x["plumed"]["features"].remove("MPI"),
            lambda x: x["plumed"].update(features=[]),
        ]
        for change in changes:
            candidate = capabilities()
            change(candidate)
            with self.subTest(change=change), self.assertRaises(ValueError):
                verify_parity(capabilities(), candidate)

    def test_baseline_must_itself_be_complete(self):
        baseline = capabilities()
        baseline["deepmd"]["backends"] = ["pt"]
        with self.assertRaises(ValueError):
            verify_parity(baseline, capabilities())
        with self.assertRaises(ValueError):
            verify_parity({}, {})

    def test_optional_backend_must_not_disappear(self):
        baseline = capabilities()
        baseline["deepmd"]["backends"].append("ptexpt")
        with self.assertRaises(ValueError):
            verify_parity(baseline, capabilities())


class BenchmarkTests(unittest.TestCase):
    def test_median_excludes_warmup(self):
        report = verify_benchmark(benchmarks())
        self.assertEqual(report["median_seconds"], {"baseline": 2.0, "candidate": 1.0})
        self.assertEqual(report["speedup"], 2.0)
        self.assertFalse(report["performance_threshold_applied"])

    def test_slow_candidate_is_reported_not_hidden_or_rejected(self):
        records = benchmarks()
        for record in records:
            if record["implementation"] == "candidate":
                record["seconds"] = 20.0
        self.assertEqual(verify_benchmark(records)["speedup"], 0.1)

    def test_explicit_tolerances(self):
        records = benchmarks()
        records[-1]["observables"]["energy"] += 1e-7
        records[-1]["observables"]["forces"][0][0] += 1e-9
        self.assertTrue(verify_benchmark(records)["passed"])

    def test_condition_mismatch(self):
        for field, value in (("node", "other-node"), ("resources", {"gpus": 1}),
                             ("input_sha256", "c" * 64)):
            records = benchmarks()
            records[-1][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify_benchmark(records)

    def test_references_and_tolerances_cannot_change(self):
        records = benchmarks()
        records[-1]["reference"]["energy"] += 1e-8
        with self.assertRaises(ValueError):
            verify_benchmark(records)
        records = benchmarks()
        records[-1]["tolerances"]["energy"]["atol"] = 1
        with self.assertRaises(ValueError):
            verify_benchmark(records)

    def test_missing_warmup_or_repeats(self):
        records = benchmarks()
        for selected in (records[1:], records[:-1], [], records[:4]):
            with self.subTest(count=len(selected)), self.assertRaises(ValueError):
                verify_benchmark(selected)
        records[4], records[5] = records[5], records[4]
        with self.assertRaises(ValueError):
            verify_benchmark(records)

    def test_numeric_error_shape_empty_nonfinite_and_missing(self):
        for name in ("energy", "forces", "virial"):
            for value in (100.0, [], [1.0], float("nan"), float("inf"), True, "1", None):
                records = benchmarks()
                records[-1]["observables"][name] = value
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    verify_benchmark(records)

    def test_bad_duration_or_tolerance(self):
        for value in (0, -1, math.nan, math.inf, True, "2"):
            records = benchmarks()
            records[-1]["seconds"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                verify_benchmark(records)
        for value in (-1, math.nan, None):
            records = benchmarks()
            records[-1]["tolerances"]["energy"]["atol"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                verify_benchmark(records)

    def test_warmup_also_needs_correct_observables(self):
        records = benchmarks()
        records[0]["observables"]["energy"] = 100
        with self.assertRaises(ValueError):
            verify_benchmark(records)


class RuntimeMetadataTests(unittest.TestCase):
    def test_canonical_prefix_and_readonly_system_paths(self):
        metadata = {"prefix": "/opt/software/deepmd-kit/3.2.0/4v100-avx512",
                    "paths": ["/opt/apps/plumed/plumed-2.10.1/lib", "/opt/devtools/cuda/lib"],
                    "status": True, "optional": None}
        self.assertTrue(audit_runtime_metadata(metadata)["passed"])

    def test_nested_metadata_leakage(self):
        for value in ("/workspace/build/lib", "LD_LIBRARY_PATH=/control/lib:/usr/lib",
                      "/home/stardust/sai-hpc-software/controller/launch.py",
                      "/control", "{\"path\":\"/workspace\"}", "[/workspace]", "/control,"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                audit_runtime_metadata({"nested": [value]})

    def test_path_boundaries_and_build_metadata_not_implicitly_scanned(self):
        self.assertTrue(audit_runtime_metadata("/opt/workspace /controller-data /workspace-other")["passed"])
        whole_manifest = {"build": {"source": "/workspace/source"},
                          "runtime": {"prefix": "/opt/software/lammps/latest/4v100-avx512"}}
        self.assertTrue(audit_runtime_metadata(whole_manifest["runtime"])["passed"])

    def test_bad_metadata_type(self):
        with self.assertRaises(ValueError):
            audit_runtime_metadata(object())
        with self.assertRaises(ValueError):
            audit_runtime_metadata({"bad": math.nan})


if __name__ == "__main__":
    unittest.main()
