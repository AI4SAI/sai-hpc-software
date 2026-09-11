"""GPUMD fail-closed registration, native target, science and cache contracts."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))
import gpumd_acceptance as acceptance
import gpumd_science as science
import software_controller as build
from remote_controller import TARGETS
import test_build_lifecycle as lifecycle


class GpumdContractTests(unittest.TestCase):
    def test_only_three_native_gpu_targets_are_registered(self):
        self.assertEqual(set(acceptance.GPU_TARGETS), {"4v100-avx512", "16v100-avx2", "8v100v0-avx512"})
        for target in acceptance.GPU_TARGETS:
            self.assertEqual(build.required_acceptance("gpumd", target), ("gpumd_science",))
            self.assertEqual(TARGETS[target]["cuda_arch"], "70")
        for target in ("dsprhbm", "a100", "unknown"):
            with self.assertRaises(ValueError):
                build.required_acceptance("gpumd", target)
        self.assertEqual(TARGETS["8v100v0-avx512"]["cpu_arch"], "skylake-avx512")
        self.assertEqual(TARGETS["8v100v0-avx512"]["dependency_isa"], "avx2")

    def test_all_scientific_build_and_launcher_files_are_hashed(self):
        contract = build.contract_files("gpumd")
        self.assertTrue({"gpumd_environment.sh", "gpumd_build.sh", "gpumd_container_entry.sh",
            "gpumd_science.py", "gpumd_deepmd_probe.py", "gpumd_acceptance.py", "gpumd", "nep", "gnep"} <= set(contract))
        original = build.recipe_fingerprint("gpumd", ROOT / "controller")
        with tempfile.TemporaryDirectory() as temporary:
            control = Path(temporary)
            for name in contract:
                shutil.copyfile(ROOT / "controller" / ("gpumd_runtime.sh" if name in ("gpumd", "nep", "gnep") else name), control / name)
            self.assertEqual(original, build.recipe_fingerprint("gpumd", control))
            for name in ("gpumd_science.py", "gpumd_deepmd_probe.py", "nep"):
                previous = (control / name).read_text()
                (control / name).write_text(previous + "\n# changed\n")
                self.assertNotEqual(original, build.recipe_fingerprint("gpumd", control))
                (control / name).write_text(previous)

    def test_missing_numerical_benchmark_proof_blocks_publication(self):
        with self.assertRaisesRegex(ValueError, "scientific/parity/benchmark"):
            acceptance.validate_manifest({"software": "gpumd", "target": "4v100-avx512"})

    def test_rendered_build_never_uses_host_tmp_or_host_source_tree(self):
        for target in acceptance.GPU_TARGETS:
            args = argparse.Namespace(software="gpumd", target=target, run_id="gpumd-test", sha="a" * 40,
                version="master-a", jobs=6, minutes=120, overlay_mb=8192, resume_run=None)
            script = build.render_job(args)
            subprocess.run(["bash", "-n"], input=script, text=True, check=True)
            self.assertIn("/control/gpumd_container_entry.sh build gpumd", script)
            self.assertIn("/opt/apps:/opt/apps:ro", script)
            self.assertIn("--overlay", script)
            self.assertNotIn("#SBATCH --mem", script)
            self.assertNotIn("#SBATCH --cpus-per-task", script)
            self.assertNotIn("TMPDIR=/tmp", script)

    def test_science_script_uses_single_gpu_and_same_image_baseline(self):
        request = {"target": "8v100v0-avx512", "version": "master-a", "run_id": "gpumd-test-science",
                   "artifact": "/home/test/sai-hpc-software/containers/software/gpumd/master-a/8v100v0-avx512/build.sif",
                   "launcher": "/home/test/sai-hpc-software/controller/gpumd-test/gpumd"}
        script = acceptance.render_job(request, Path("/home/test/sai-hpc-software/runtime-tests/gpumd-test-science"))
        subprocess.run(["bash", "-n"], input=script, text=True, check=True)
        self.assertIn("#SBATCH --partition=8V100V0", script)
        self.assertIn("#SBATCH --gpus-per-node=1", script)
        self.assertIn("gpumd_science.py run", script)
        self.assertIn('"CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES}"', script)
        self.assertNotIn("TMPDIR=/tmp", script)
        self.assertIn("/results:/work:rw", script)

    def test_comparison_rejects_nan_wrong_shape_and_excess_error(self):
        self.assertLess(science.compare([[1.0]], [[1.00001]], 0.001, "test"), 0.001)
        for actual in ([[float("nan")]], [[1, 2]], [[3]], []):
            with self.assertRaises(ValueError):
                science.compare(actual, [[1]], 0.001, "test")
        with self.assertRaises(ValueError):
            science.compare([[1, float("nan")]], [[1, 2]], 0.001, "later NaN")

    def test_explicit_native_and_optional_feature_baseline(self):
        environment = (ROOT / "controller/gpumd_environment.sh").read_text()
        recipe = (ROOT / "controller/gpumd_build.sh").read_text()
        self.assertIn("GPUMD_CPU_ARCH=native", environment)
        self.assertIn("GPUMD_EXPECTED_CPU_ARCH=skylake-avx512", environment)
        self.assertIn("module load cuda/12.9.1 deepmd-kit/3.2.0 gcc/13.3.0", environment)
        self.assertNotIn("module load elpa", environment)
        self.assertIn("-DUSE_DEEPMD -DUSE_PLUMED", recipe)
        self.assertIn("share/gpumd/src", recipe)


class GpumdCiTests(unittest.TestCase):
    setUp = lifecycle.CiLifecycleTests.setUp
    execute = lifecycle.CiLifecycleTests.execute
    def test_gpumd_requires_science_and_benchmark_before_publication(self):
        self.assertEqual(self.execute(software="gpumd"), [
            ("software_controller.py", "submit"), ("software_controller.py", "monitor"),
            ("gpumd_acceptance.py", "submit"), ("gpumd_acceptance.py", "monitor"),
            ("software_controller.py", "publish")])

    def test_failed_gpumd_science_cannot_publish(self):
        commands = self.execute(software="gpumd", fail_monitor="gpumd_acceptance.py")
        self.assertNotIn(("software_controller.py", "publish"), commands)


if __name__ == "__main__":
    unittest.main()
