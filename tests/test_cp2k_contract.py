import argparse
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controller"))
import cp2k_feature_contract as contract
import software_controller as software


class CP2KContractTests(unittest.TestCase):
    def flags(self, gpu=True):
        flags = contract.BASE_FLAGS | {"libxs", "libxsmm"}
        if gpu:
            flags |= contract.GPU_FLAGS
        return " CP2K version 2026.2\n cp2kflags: " + " ".join(sorted(flags)) + "\n"

    def cache(self, target="4v100-avx512"):
        entries = {"CP2K_USE_" + key: "ON" for key in contract.REQUIRED_OPTIONS}
        entries.update(CMAKE_INSTALL_PREFIX="/opt/software/cp2k/test/" + target,
                       CP2K_USE_ACCEL="NONE" if target == "dsprhbm" else "CUDA",
                       CP2K_ELPA_ROOT="/opt/devtools/elpa/elpa-2026.02.001-2603-gnu/nvidia",
                       CP2K_USE_CUSOLVER_MP="ON")
        for lang in ("C", "CXX", "Fortran"):
            entries[f"CMAKE_{lang}_FLAGS"] = "-O3 -march=native"
        return "\n".join(f"{key}:STRING={value}" for key, value in entries.items())

    def test_all_baseline_features_required(self):
        contract.check_flags(self.flags(), "4v100-avx512")
        for feature in contract.BASE_FLAGS | contract.GPU_FLAGS | {"libxs", "libxsmm"}:
            with self.subTest(feature=feature), self.assertRaises(ValueError):
                text = self.flags().replace(" " + feature + " ", " ")
                text = text.replace(" " + feature + "\n", "\n")
                contract.check_flags(text, "4v100-avx512")

    def test_cpu_is_native_and_gpu_free(self):
        contract.check_flags(self.flags(gpu=False), "dsprhbm")
        with self.assertRaises(ValueError):
            contract.check_flags(self.flags(), "dsprhbm")
        contract.check_cache(self.cache("dsprhbm"), "/opt/software/cp2k/test/dsprhbm", "dsprhbm")
        with self.assertRaises(ValueError):
            contract.check_cache(self.cache().replace("-march=native", "-march=znver4"),
                                 "/opt/software/cp2k/test/4v100-avx512", "4v100-avx512")

    def test_cmake_on_and_correct_prefix_are_required(self):
        text = self.cache()
        contract.check_cache(text, "/opt/software/cp2k/test/4v100-avx512", "4v100-avx512")
        for bad in (text.replace("CP2K_USE_ELPA:STRING=ON", "CP2K_USE_ELPA:STRING=OFF"),
                    text.replace("elpa-2026.02.001", "elpa-2024.05.001"),
                    text.replace("/opt/software/cp2k/test/", "/opt/software/test/")):
            with self.assertRaises(ValueError):
                contract.check_cache(bad, "/opt/software/cp2k/test/4v100-avx512", "4v100-avx512")

    def test_ldd_and_rpath_must_resolve_outside_build_tree(self):
        links = "\n".join((
            "libelpa_openmp.so => /opt/devtools/elpa/elpa-2026.02.001-2603-gnu/nvidia/lib/libelpa_openmp.so",
            "libcusolverMp.so.0 => /opt/devtools/nvidia/mp_libs/lib/libcusolverMp.so.0",
            "libnccl.so.2 => /opt/devtools/nvidia/nccl_2.29.3_cuda12.9/lib/libnccl.so.2"))
        contract.check_linkage(links, "4v100-avx512")
        for extra in ("libfoo => not found", "libfoo => /workspace/lib/libfoo.so"):
            with self.assertRaises(ValueError):
                contract.check_linkage(links + "\n" + extra, "4v100-avx512")
        contract.check_dynamic_paths("(RUNPATH) Library runpath: [$ORIGIN/../lib64:/opt/devtools/test/lib]")
        with self.assertRaises(ValueError):
            contract.check_dynamic_paths("(RUNPATH) Library runpath: [/workspace/build/lib]")

    def test_cp2k_cannot_publish_without_scientific_benchmark(self):
        for target in ("dsprhbm", "4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
            self.assertEqual(software.required_acceptance("cp2k", target), ("cp2k_benchmark",))
        with self.assertRaises(ValueError):
            software.required_acceptance("cp2k", "a100")

    def test_final_sif_verifier_has_no_source_cache_bind(self):
        args = argparse.Namespace(run_id="cp2k-test", software="cp2k", sha="a" * 40,
                                  version="test", target="dsprhbm", jobs=8, minutes=120,
                                  overlay_mb=16384)
        script = software.render_job(args)
        final = next(line for line in script.splitlines() if "cp2k_container_entry.sh verify" in line)
        self.assertIn("result.sif", final)
        self.assertNotIn("--overlay", final)
        self.assertNotIn("/input/dependencies", final)
        self.assertNotIn("/input/probe", final)
        self.assertNotIn("/input/repository", final)
        self.assertIn("SAI_BUILD_PARTITION=DSPRHBM", final)


if __name__ == "__main__":
    unittest.main()
