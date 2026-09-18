import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controller"))
import cp2k_feature_contract as contract
import software_controller as software
from export_native import MANIFEST_PATH, read_installed_manifests
from release_contract import make_identity


class CP2KContractTests(unittest.TestCase):
    def test_runtime_roots_exclude_other_cp2k_installation(self):
        roots = contract.runtime_roots("16v100-avx2")
        self.assertFalse(any(root.startswith("/opt/apps/cp2k/") for root in roots))

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
            entries[f"CMAKE_{lang}_FLAGS"] = "-O3 -march=native -mtune=native"
        for library in ("cusolver", "cudart", "cublasLt", "cublas"):
            entries["pkgcfg_lib_CP2K_ELPA_" + library] = (
                "/opt/devtools/nvidia/cuda-12.9.1/lib64/lib" + library + ".so")
        return "\n".join(f"{key}:STRING={value}" for key, value in entries.items())

    def test_cpu_elpa_still_requires_resolved_cuda_link_libraries(self):
        text = self.cache("dsprhbm")
        prefix = "/opt/software/cp2k/test/dsprhbm"
        for library in ("cusolver", "cudart", "cublasLt", "cublas"):
            full = "/opt/devtools/nvidia/cuda-12.9.1/lib64/lib" + library + ".so"
            for replacement in (library, "-l" + library, "", full + "-NOTFOUND",
                                full.replace("12.9.1", "12.8.0")):
                with self.subTest(library=library, replacement=replacement):
                    with self.assertRaisesRegex(ValueError, "ELPA CUDA library"):
                        contract.check_cache(text.replace(full, replacement), prefix, "dsprhbm")
        parsed = contract.check_cache(text.replace("/lib64/lib", "/targets/x86_64-linux/lib/lib"),
                                      prefix, "dsprhbm")
        self.assertEqual(parsed["CP2K_USE_ACCEL"], "NONE")
        script = (Path(contract.__file__).parent / "cp2k_build.sh").read_text()
        self.assertIn('-DCMAKE_LIBRARY_PATH="$cuda_libraries"', script)

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
                    text.replace("-mtune=native", "-mtune=generic"),
                    text.replace("/opt/software/cp2k/test/", "/opt/software/test/")):
            with self.assertRaises(ValueError):
                contract.check_cache(bad, "/opt/software/cp2k/test/4v100-avx512", "4v100-avx512")

    def test_real_cmake_comments_blank_lines_and_duplicate_options(self):
        text = "# This is the CMakeCache file.\n" + "\n".join(
            "\n//Documentation for this option.\n" + line for line in self.cache().splitlines())
        parsed = contract.check_cache(text, "/opt/software/cp2k/test/4v100-avx512", "4v100-avx512")
        self.assertEqual(parsed["CP2K_USE_MPI"], "ON")
        self.assertEqual(parsed["CP2K_USE_ELPA"], "ON")
        self.assertEqual(contract.check_cache(text.replace("\n", "\r\n"),
                         "/opt/software/cp2k/test/4v100-avx512", "4v100-avx512"), parsed)
        with self.assertRaisesRegex(ValueError, "disabled: MPI"):
            contract.check_cache(text.replace("CP2K_USE_MPI:STRING=ON", "CP2K_USE_MPI:STRING=OFF"),
                                 "/opt/software/cp2k/test/4v100-avx512", "4v100-avx512")
        for duplicate in ("CP2K_USE_MPI:BOOL=ON", "CP2K_USE_MPI:BOOL=OFF"):
            for duplicate_text in (text + "\n" + duplicate, duplicate + "\n" + text):
                with self.subTest(duplicate=duplicate), self.assertRaisesRegex(ValueError, "duplicate"):
                    contract.check_cache(duplicate_text,
                                         "/opt/software/cp2k/test/4v100-avx512", "4v100-avx512")

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

    def test_recursive_loader_contract_rejects_old_prefix_and_external_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            prefix = Path(temporary) / "opt/software/cp2k/test/16v100-avx2"
            binary = prefix / "dependencies/libxs/lib/libxs.so"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"\x7fELFfixture")
            for tag, value in (("NEEDED", "/opt/software/cp2k-dependencies/tblite/lib/libtblite.so"),
                               ("NEEDED", "../lib/libdependency.so"),
                               ("RUNPATH", "/opt/software/cp2k-dependencies/tblite/lib"),
                               ("RUNPATH", "/home/stardust/controller/lib"),
                               ("RUNPATH", "/opt/apps/unrelated/lib"),
                               ("RUNPATH", "$ORIGIN/../../../../../../outside"),
                               ("RUNPATH", "$ORIGIN:")):
                with self.subTest(tag=tag, value=value), self.assertRaises(ValueError):
                    contract.check_dynamic(f"({tag}) Library path: [{value}]", binary, prefix, "16v100-avx2")
            tags = "(RUNPATH) Library runpath: [$ORIGIN:/opt/devtools/saiblas/2603-gnu-avx2/lib]"
            with patch.object(contract, "run", return_value=tags):
                self.assertEqual(set(contract.verify_tree(prefix, "16v100-avx2")),
                                 {"dependencies/libxs/lib/libxs.so"})
                (prefix / "escape").symlink_to("/usr/lib")
                with self.assertRaisesRegex(ValueError, "symlink"):
                    contract.verify_tree(prefix, "16v100-avx2")

    def test_final_sif_verifier_has_no_source_cache_bind(self):
        args = argparse.Namespace(run_id="cp2k-test", software="cp2k", sha="a" * 40,
                                  version="test", target="dsprhbm", jobs=8, minutes=120,
                                  overlay_mb=16384, track="development", source_ref="master")
        script = software.render_job(args)
        final = next(line for line in script.splitlines() if "cp2k_container_entry.sh verify" in line)
        self.assertIn("result.sif", final)
        self.assertNotIn("--overlay", final)
        self.assertNotIn("/input/dependencies", final)
        self.assertNotIn("/input/probe", final)
        self.assertNotIn("/input/repository", final)
        self.assertIn("SAI_BUILD_PARTITION=DSPRHBM", final)
        self.assertIn("/runtime:/runtime:rw", final)
        self.assertIn("TMPDIR=/runtime", final)


class CP2KNativeDeliveryTests(unittest.TestCase):
    def identity(self, target="4v100-avx512"):
        return make_identity("cp2k", "development", "master", "a" * 40, "2026.2",
                             "b" * 64, target)

    def environment(self, identity):
        prefix = identity["install_prefix"]
        return {"PATH": prefix + "/bin:/usr/bin:/bin",
                "LD_LIBRARY_PATH": prefix + "/lib64:" + prefix + "/dependencies/tblite/lib:"
                                   "/opt/devtools/elpa/elpa-2026.02.001-2603-gnu/nvidia/lib:"
                                   "/opt/devtools/openmpi/5.0.10/lib:/.singularity.d/libs",
                "LOADEDMODULES": "cmake/3.31.6:openmpi/5.0.10:elpa/2026.02.001-2603-gnu:apptainer/1.4.4",
                "MODULEPATH": "/opt/modules/modulefiles/apps:/opt/modules/modulefiles/devtools",
                "CP2K_DATA_DIR": prefix + "/share/cp2k/data"}

    def test_observed_runtime_keeps_exact_prefix_and_dependency_modules(self):
        for target in ("dsprhbm", "4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
            identity = self.identity(target)
            entry = contract.native_entry(identity, self.environment(identity))
            self.assertEqual(entry["identity"], identity)
            self.assertEqual(entry["commands"], {"cp2k.psmp": "bin/cp2k.psmp"})
            self.assertEqual(entry["runtime"]["modules"], ["openmpi/5.0.10", "elpa/2026.02.001-2603-gnu"])
            self.assertEqual(entry["runtime"]["prepend"]["MODULEPATH"], ["/opt/modules/modulefiles/devtools"])
            self.assertIn("/opt/modules/modulefiles/devtools", entry["external_roots"])
            self.assertEqual(entry["runtime"]["set"]["CP2K_DATA_DIR"], identity["install_prefix"] + "/share/cp2k/data")
            self.assertNotIn("/.singularity.d", json.dumps(entry))
            self.assertNotIn("/opt/devtools", entry["external_roots"])

    def test_runtime_rejects_old_prefix_build_paths_and_unrecorded_modules(self):
        identity = self.identity()
        environment = self.environment(identity)
        for name, value in (("CP2K_DATA_DIR", "/opt/apps/cp2k/data"),
                            ("LOADEDMODULES", ""), ("LOADEDMODULES", "cp2k/2026.1"),
                            ("MODULEPATH", "/workspace/modules"),
                            ("PATH", "/workspace/bin:/usr/bin"),
                            ("LD_LIBRARY_PATH", "/workspace/lib"),
                            ("LD_LIBRARY_PATH", "/opt/software/cp2k/old/lib"),
                            ("LD_LIBRARY_PATH", "/opt/apps/unrelated/lib")):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                contract.native_entry(identity, dict(environment, **{name: value}))

    def test_packaged_single_folder_is_verified_and_detects_modified_data(self):
        identity = self.identity()
        prefix = Path(identity["install_prefix"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            installed = root / prefix.relative_to("/")
            metadata = installed / "share/sai"
            metadata.mkdir(parents=True)
            (metadata / "release-identity.json").write_text(json.dumps(identity))
            binary = installed / "bin/cp2k.psmp"
            binary.parent.mkdir()
            binary.write_text("#!/bin/sh\nexit 0\n")
            binary.chmod(0o755)
            data = installed / "share/cp2k/data/BASIS_MOLOPT"
            data.parent.mkdir(parents=True)
            data.write_text("synthetic scientific data fixture\n")
            with patch.dict(os.environ, self.environment(identity), clear=True):
                contract.native_delivery(prefix, identity["target"], identity["source_sha"], "native-entry", root)
            contract.native_delivery(prefix, identity["target"], identity["source_sha"], "native-package", root)
            contract.native_delivery(prefix, identity["target"], identity["source_sha"], "native-verify", root)
            entry = json.loads((metadata / "native-entry.json").read_text())
            manifest = read_installed_manifests([entry], root)
            self.assertEqual(manifest["entries"], [entry])
            self.assertTrue((installed / MANIFEST_PATH).is_file())
            selector = installed / "modulefiles/cp2k/development" / identity["build_id"]
            self.assertTrue(selector.is_file())
            self.assertFalse((root / "sai-delivery.json").exists())
            data.write_text("changed scientific data\n")
            with self.assertRaisesRegex(ValueError, "inventory"):
                contract.native_delivery(prefix, identity["target"], identity["source_sha"], "native-verify", root)


if __name__ == "__main__":
    unittest.main()
