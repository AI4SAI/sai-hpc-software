import copy
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controller"))
from md_science import (BACKENDS, NKTV2P, STRESS_ORDER, TOLERANCES, _record, copy_trial_inputs,
                        parse_lammps_output, parse_reference, render_data,
                        render_lammps_input, require_execution_context,
                        verify_plumed_output, verify_science)


def oracle_text():
    return "\n".join([
        "import never_import_this_upstream_module",
        "raise RuntimeError('upstream code must not execute')",
        "expected_ae = np.array([-1., -2., -3., -4., -5., -6.])",
        "expected_f = np.array(" + repr([0.01 * i for i in range(18)]) + ").reshape(6, 3)",
        "expected_v = -np.array(" + repr([0.1 * i for i in range(54)]) + ").reshape(6, 9)",
        "box = np.array([0,13,0,13,0,13,0,0,0])",
        "coord = np.array([[12.83,2.56,2.18],[12.09,2.87,2.74],[0.25,3.32,1.68],"
        "[3.36,3.,1.81],[3.51,2.51,2.60],[4.27,3.22,1.56]])",
        "type_OH = np.array([1,2,2,1,2,2])",
    ])


def dump_text(fixture):
    lines = ["ITEM: TIMESTEP", "1", "ITEM: NUMBER OF ATOMS", "6",
             "ITEM: BOX BOUNDS pp pp pp", "0 13", "0 13", "0 13",
             "ITEM: ATOMS id fx fy fz " + " ".join(f"c_sai_virial[{i}]" for i in range(1, 10))]
    for atom_id in (6, 2, 3, 1, 4, 5):
        forces = fixture["reference"]["forces"][atom_id - 1]
        stress = [-fixture["reference"]["virial"][i] * NKTV2P / 6 for i in STRESS_ORDER]
        lines.append(str(atom_id) + " " + " ".join(format(v, ".17g") for v in forces + stress))
    return "\n".join(lines) + "\n"


def complete_records():
    fixture = parse_reference(oracle_text())
    fixture["tolerances"] = TOLERANCES
    fixture["models"] = {b: {"input_sha256": "a" * 64} for b in BACKENDS}
    records = []
    for backend in BACKENDS:
        for engine in ("python", "lammps"):
            for side in ("baseline", "candidate"):
                for index in range(4):
                    record = _record(fixture, backend, engine, side, {"ranks": 1, "gpus": 1},
                                     index == 0, 1.0, copy.deepcopy(fixture["reference"]))
                    if engine == "lammps":
                        record["plumed"] = {"passed": True}
                    records.append(record)
    return records


class ReferenceTests(unittest.TestCase):
    def test_static_extraction_does_not_import_or_execute_upstream(self):
        result = parse_reference(oracle_text())
        self.assertEqual(result["reference"]["energy"], -21.0)
        self.assertEqual(len(result["reference"]["forces"]), 6)
        self.assertAlmostEqual(result["reference"]["virial"][0], sum(i * 0.1 for i in range(0, 54, 9)))
        self.assertAlmostEqual(result["distance_angstrom"], math.sqrt(0.74 ** 2 + 0.31 ** 2 + 0.56 ** 2))

    def test_reject_executable_expression_missing_or_changed_shape(self):
        good = oracle_text()
        variants = [good.replace("np.array([-1., -2., -3., -4., -5., -6.])", "__import__('os').system('bad')"),
                    good.replace(".reshape(6, 3)", ".reshape(3, 6)"),
                    good.replace("[0,13,0,13,0,13,0,0,0]", "[0,12,0,13,0,13,0,0,0]"),
                    good + "\nexpected_ae = np.array([1,2,3,4,5,6])", "coord = np.array([1])"]
        for text in variants:
            with self.subTest(text=text[-60:]), self.assertRaises(ValueError):
                parse_reference(text)

    def test_true_upstream_reference_when_local_cache_available(self):
        cache = Path(__file__).resolve().parents[1] / ".md-source-cache/deepmd-kit.git"
        if not cache.is_dir():
            self.skipTest("optional local source cache is not present in CI")
        source = subprocess.check_output(["git", f"--git-dir={cache}", "show",
                                          "HEAD:source/lmp/tests/test_lammps.py"], text=True)
        result = parse_reference(source)
        self.assertAlmostEqual(result["reference"]["energy"], -930.9691834787725)
        self.assertAlmostEqual(result["reference"]["forces"][0][0], -0.30340454207011797)

    def test_generated_lammps_input_executes_plumed_but_does_not_move_atoms(self):
        fixture = parse_reference(oracle_text())
        data = render_data(fixture)
        self.assertIn("6 atoms", data)
        self.assertIn("Atoms # atomic", data)
        for backend, model in BACKENDS.items():
            text = render_lammps_input(backend)
            self.assertIn("pair_style deepmd " + model, text)
            self.assertIn("fix sai_plumed all plumed", text)
            self.assertIn("run 1", text)
            self.assertNotIn(" nve", text)
            self.assertIn("c_sai_virial[9]", text)
        with self.assertRaises(ValueError):
            render_lammps_input("unknown")


class OutputTests(unittest.TestCase):
    def test_lammps_id_sort_stress_sign_and_tensor_mapping(self):
        fixture = parse_reference(oracle_text())
        result = parse_lammps_output("SAI_ENERGY = -21\n", dump_text(fixture))
        self.assertEqual(result["energy"], -21)
        self.assertEqual(result["forces"], fixture["reference"]["forces"])
        for actual, reference in zip(result["virial"], fixture["reference"]["virial"]):
            self.assertAlmostEqual(actual, reference)

    def test_reject_bad_energy_ids_columns_and_nonfinite(self):
        dump = dump_text(parse_reference(oracle_text()))
        for energy, text in (("", dump), ("SAI_ENERGY = -21\nSAI_ENERGY = -21", dump),
                             ("SAI_ENERGY = nan", dump), ("SAI_ENERGY = -21", dump + dump),
                             ("SAI_ENERGY = -21", dump.replace("c_sai_virial[9]", "wrong")),
                             ("SAI_ENERGY = -21", dump.replace("\n6 ", "\n2 "))):
            with self.subTest(energy=energy), self.assertRaises(ValueError):
                parse_lammps_output(energy, text)

    def test_plumed_known_geometry_and_real_timestep(self):
        text = "#! FIELDS time sai_distance\n0 0.98\n0.0005 0.98\n"
        self.assertTrue(verify_plumed_output(text, 0.98)["passed"])
        for bad in ("", "#! FIELDS time sai_distance\n0 0.98\n", text.replace("0.98", "1.0"),
                    text.replace("sai_distance", "unknown"), text.replace("0.98", "nan")):
            with self.subTest(text=bad), self.assertRaises(ValueError):
                verify_plumed_output(bad, 0.98)


class ScienceGateTests(unittest.TestCase):
    def test_all_three_backends_both_engines_both_implementations(self):
        report = verify_science(complete_records())
        self.assertTrue(report["passed"])
        self.assertEqual(report["complete_backends"], ["tf", "pt", "jax"])
        self.assertEqual(len(report["benchmarks"]), 6)

    def test_tf_only_is_not_complete_and_plumed_is_mandatory(self):
        records = complete_records()
        with self.assertRaises(ValueError):
            verify_science([r for r in records if r["backend"] == "tf"])
        next(r for r in records if r["engine"] == "lammps")["plumed"] = {"passed": False}
        with self.assertRaises(ValueError):
            verify_science(records)

    def test_python_lammps_must_share_exact_fixture(self):
        records = complete_records()
        for record in records:
            if record["engine"] == "lammps":
                record["input_sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            verify_science(records)

    def test_actual_observable_mismatch_rejected_at_record_creation(self):
        fixture = parse_reference(oracle_text())
        fixture.update(tolerances=TOLERANCES, models={"tf": {"input_sha256": "a" * 64}})
        observed = copy.deepcopy(fixture["reference"])
        observed["forces"][0][0] += 0.1
        with self.assertRaises(ValueError):
            _record(fixture, "tf", "python", "candidate", {"ranks": 1}, False, 1.0, observed)


class ContextGuardTests(unittest.TestCase):
    def test_login_host_without_allocation_rejected(self):
        with patch("md_science.Path.is_dir", return_value=False), patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                require_execution_context()

    def test_stale_slurm_environment_is_not_enough(self):
        with patch("md_science.Path.is_dir", return_value=False), patch.dict("os.environ", {"SLURM_JOB_ID": "123"}, clear=True), \
             patch("md_science.subprocess.check_output", return_value="JobId=123 JobState=COMPLETED NodeList=node1"):
            with self.assertRaises(RuntimeError):
                require_execution_context()

    def test_running_job_on_another_host_rejected(self):
        with patch("md_science.Path.is_dir", return_value=False), patch.dict("os.environ", {"SLURM_JOB_ID": "123"}, clear=True), \
             patch("md_science.subprocess.check_output", side_effect=["JobState=RUNNING NodeList=not-this-host", "not-this-host\n"]):
            with self.assertRaises(RuntimeError):
                require_execution_context()

    def test_container_marker_alone_is_not_compute_authorization(self):
        with patch("md_science.Path.is_dir", return_value=True), patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                require_execution_context()

    def test_container_attestations_require_job_and_matching_node(self):
        host = os.uname().nodename
        for env in ({"SAI_MD_ALLOCATED_JOB": "123"},
                    {"SAI_MD_ALLOCATED_NODE": host},
                    {"SAI_MD_ALLOCATED_JOB": "bad", "SAI_MD_ALLOCATED_NODE": host},
                    {"SAI_MD_ALLOCATED_JOB": "123", "SAI_MD_ALLOCATED_NODE": "another-host"}):
            with self.subTest(env=env), patch("md_science.Path.is_dir", return_value=True), \
                 patch.dict("os.environ", env, clear=True), self.assertRaises(RuntimeError):
                require_execution_context()

    def test_container_accepts_trusted_matching_allocation_attestations(self):
        env = {"SAI_MD_ALLOCATED_JOB": "123", "SAI_MD_ALLOCATED_NODE": os.uname().nodename}
        with patch("md_science.Path.is_dir", return_value=True), patch.dict("os.environ", env, clear=True), \
             patch("md_science.subprocess.check_output") as slurm:
            require_execution_context()
            slurm.assert_not_called()

    def test_host_running_matching_allocation_but_host_tmp_forbidden(self):
        host = os.uname().nodename
        for scratch, accepted in (("/tmp", False), ("/tmp/project", False), ("/work/project/scratch", True)):
            env = {"SLURM_JOB_ID": "123", "TMPDIR": scratch}
            with self.subTest(scratch=scratch), patch("md_science.Path.is_dir", return_value=False), \
                 patch.dict("os.environ", env, clear=True), patch("md_science.subprocess.check_output", \
                 side_effect=[f"JobId=123 JobState=RUNNING NodeList={host}", host]):
                if accepted:
                    require_execution_context()
                else:
                    with self.assertRaises(RuntimeError):
                        require_execution_context()


class TrialInputTests(unittest.TestCase):
    def test_trial_copy_is_self_contained_for_work_only_bind(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, trial = root / "case", root / "trial"
            case.mkdir()
            trial.mkdir()
            for name in ("model.pb", "data.lmp", "plumed.dat", "in.tf"):
                (case / name).write_text("fixture content " + name)
            fixture = {"models": {"tf": {"file": "model.pb"}}}
            copy_trial_inputs(case, trial, "tf", fixture)
            for name in ("model.pb", "data.lmp", "plumed.dat", "in.tf"):
                self.assertFalse((trial / name).is_symlink())
                self.assertEqual((trial / name).read_text(), (case / name).read_text())
            with self.assertRaises(ValueError):
                copy_trial_inputs(case, trial, "tf", fixture)

    def test_savedmodel_directory_is_copied_not_symlinked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case, trial = root / "case", root / "trial"
            case.mkdir()
            trial.mkdir()
            model = case / "model.savedmodel"
            (model / "variables").mkdir(parents=True)
            (model / "saved_model.pb").write_text("small model")
            (model / "variables/variables.index").write_text("small variables")
            for name in ("data.lmp", "plumed.dat", "in.jax"):
                (case / name).write_text("fixture content")
            fixture = {"models": {"jax": {"file": "model.savedmodel"}}}
            copy_trial_inputs(case, trial, "jax", fixture)
            self.assertFalse((trial / "model.savedmodel").is_symlink())
            self.assertEqual((trial / "model.savedmodel/variables/variables.index").read_text(), "small variables")

    def test_manifest_cannot_redirect_task_model_outside_trial(self):
        fixture = {"models": {"tf": {"file": "../../outside/model.pb"}}}
        with self.assertRaises(ValueError):
            copy_trial_inputs("/nonexistent", "/nonexistent", "tf", fixture)


if __name__ == "__main__":
    unittest.main()
