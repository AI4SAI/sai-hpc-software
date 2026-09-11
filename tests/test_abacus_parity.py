"""Presence is mandatory; it does not substitute for scientific benchmarks."""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))
import abacus_dependencies as deps
import abacus_features as features
import software_controller as controller


class ParityTests(unittest.TestCase):
    def setUp(self):
        self.lock = deps.load_lock()
        self.prefix = Path("/opt/software/abacus/v1/4v100-avx512")
        self.info = "\n".join(f"{key}: yes" for key in
                              self.lock["required_info"] + self.lock["required_gpu_info"])
        self.info = self.info.replace("LibRI Support: yes", "LibRI Support: yes (v2.1.1)")
        self.info = self.info.replace("LibTorch Support: yes", "LibTorch Support: yes (v2.1.2)")
        self.cache = "\n".join(f"{key}:BOOL=ON" for key in
                               self.lock["required_options"] + self.lock["required_gpu_options"])
        self.ldd = "\n".join([
            "libelpa_openmp.so.19 => /opt/devtools/elpa/elpa-2026.02.001-2603-gnu/nvidia/lib/libelpa_openmp.so.19",
            f"libtorch_cpu.so => {self.prefix}/dependencies/libtorch/lib/libtorch_cpu.so",
            f"libnep.so => {self.prefix}/dependencies/nep/lib/libnep.so",
            "libcusolverMp.so.0 => /opt/devtools/nvidia/mp_libs/lib/libcusolverMp.so.0",
            "libcublasmp.so.0 => /opt/devtools/nvidia/mp_libs/lib/libcublasmp.so.0",
            "libnccl.so.2 => /opt/devtools/nvidia/nccl_2.29.3/lib/libnccl.so.2",
        ])

    def check(self, *, info=None, cache=None, ldd=None, target="4v100-avx512"):
        return features.check_features(self.info if info is None else info,
                                       self.cache if cache is None else cache,
                                       self.ldd if ldd is None else ldd,
                                       target, self.prefix, self.lock)

    def test_union_baseline_requires_all_preinstalled_optional_features(self):
        result = self.check()
        self.assertIn("compile/link presence", result["scope"])
        for label in self.lock["required_info"]:
            with self.subTest(label=label):
                with self.assertRaisesRegex(ValueError, "feature parity failed"):
                    self.check(info=self.info.replace(f"{label}: yes", f"{label}: no"))

    def test_options_and_minimum_versions_cannot_silently_regress(self):
        for option in self.lock["required_options"] + self.lock["required_gpu_options"]:
            with self.subTest(option=option):
                with self.assertRaises(ValueError):
                    self.check(cache=self.cache.replace(f"{option}:BOOL=ON", f"{option}:BOOL=OFF"))
        for version in ("v2.1.1", "v2.1.2"):
            with self.assertRaises(ValueError):
                self.check(info=self.info.replace(version, "v1.0.0"))

    def test_cpu_retains_full_cpu_features_without_requiring_gpu_support(self):
        cache = "\n".join(f"{key}:BOOL=ON" for key in self.lock["required_options"]) + "\nUSE_CUDA:BOOL=OFF"
        self.check(cache=cache, target="dsprhbm")
        with self.assertRaises(ValueError):
            self.check(cache=cache.replace("USE_CUDA:BOOL=OFF", "USE_CUDA:BOOL=ON"), target="dsprhbm")

    def test_system_elpa_and_packaged_optional_libraries_are_not_interchangeable(self):
        for previous, wrong in (("elpa-2026.02.001", "elpa-2025.06.001"),
                                ("/opt/devtools/nvidia/mp_libs/lib", "/opt/devtools/nvidia/old-sdk/lib"),
                                (str(self.prefix), "/workspace/dependencies")):
            with self.subTest(path=wrong):
                with self.assertRaises(ValueError):
                    self.check(ldd=self.ldd.replace(previous, wrong))
        with self.assertRaises(ValueError):
            self.check(ldd=self.ldd + "\nlibbad.so => not found\n")

    def test_elf_runtime_tags_allow_origin_and_reject_build_or_snapshot_paths(self):
        binary = self.prefix / "bin/abacus"
        valid = "(NEEDED) Shared library: [libnep.so]\n(RUNPATH) Library runpath: [$ORIGIN/../dependencies/nep/lib]"
        self.assertEqual(len(features.check_dynamic(valid, binary, self.prefix)), 2)
        for bad in ("/workspace/build", "/control", "/input/repository", "/home/stardust/sai-hpc-software/controller/sha",
                    "$ORIGIN/../../../../../../../workspace", ""):
            with self.subTest(path=bad):
                with self.assertRaises(ValueError):
                    features.check_dynamic(f"(RPATH) Library rpath: [{bad}]", binary, self.prefix)
        with self.assertRaises(ValueError):
            features.check_dynamic("(NEEDED) Shared library: [/workspace/libnep.so]", binary, self.prefix)
        with self.assertRaises(ValueError):
            features.check_dynamic("[Requesting program interpreter: /control/ld.so]", binary, self.prefix)

    def test_dependency_archive_members_and_destination_fail_closed(self):
        deps.validate_members(["package/include/header.h", "package/"], "package")
        for value in ("../header.h", "/workspace/header.h", "package/../../header.h", "other/header.h"):
            with self.subTest(path=value):
                with self.assertRaises(ValueError):
                    deps.validate_members([value], "package")
        for destination in (Path("/opt/software/expanded"), Path("relative")):
            with self.assertRaisesRegex(ValueError, "build overlay"):
                deps.unpack(Path("/input/abacus-dependencies"), destination, self.lock)

    def test_zip_executable_modes_survive_and_zip_links_are_rejected(self):
        parent = ROOT / ".test-work"
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            folder = Path(directory)
            archive = folder / "torch.zip"
            entry = zipfile.ZipInfo("libtorch/bin/torch_shm_manager")
            entry.external_attr = (stat.S_IFREG | 0o755) << 16
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr(entry, b"test helper")
            deps.extract_archive(archive, folder / "extracted", "libtorch")
            self.assertTrue(os.access(folder / "extracted/libtorch/bin/torch_shm_manager", os.X_OK))
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(archive, "w") as stream:
                stream.writestr(entry, b"/etc/passwd")
            with self.assertRaisesRegex(ValueError, "zip links"):
                deps.extract_archive(archive, folder / "bad", "libtorch")

    def test_dependency_lock_is_content_pinned_and_build_mount_is_precise_readonly(self):
        self.assertEqual(len(self.lock["archives"]), 7)
        for archive in self.lock["archives"]:
            self.assertRegex(archive["sha256"], r"^[0-9a-f]{64}$")
        args = argparse.Namespace(software="abacus", run_id="test", sha="a" * 40,
                                  version="v1", target="dsprhbm", jobs=8, minutes=60,
                                  overlay_mb=8192)
        with patch.object(controller, "ROOT", Path("/home/test/sai-hpc-software")):
            script = controller.render_job(args)
        mount = self.lock["site_archive_root"] + ":/input/abacus-dependencies:ro"
        self.assertIn(mount, script)
        self.assertNotIn("--bind /opt/apps:/opt/apps", script)
        verify = next(line for line in script.splitlines() if "container_entry.sh verify" in line)
        self.assertNotIn("/input/abacus-dependencies", verify)
        subprocess.run(["bash", "-n"], input=script, text=True, check=True)
        for name in ("abacus_dependencies.py", "abacus_dependencies.sh", "abacus_dependency_lock.json", "abacus_features.py"):
            self.assertIn(name, controller.contract_files("abacus"))

    def test_nep_is_recompiled_with_soname_and_runtime_env_is_prefix_relative(self):
        recipe = (ROOT / "controller/abacus_dependencies.sh").read_text()
        self.assertIn("-march=native -mtune=native", recipe)
        self.assertIn("-Wl,-soname,libnep.so", recipe)
        self.assertIn("-DCMAKE_INSTALL_RPATH=$ORIGIN", recipe)
        self.assertNotIn("install/NEP_CPU-main/lib/libnep.so", recipe)
        entry = (ROOT / "controller/container_entry.sh").read_text()
        self.assertIn("BASH_SOURCE[0]", entry)
        self.assertIn('python3 "$INSTALL_PREFIX/share/sai/abacus_features.py"', entry)

    def test_config_package_install_layout_matches_consumed_paths(self):
        recipe = (ROOT / "controller/abacus_dependencies.sh").read_text()
        for package in ("cereal", "rapidjson"):
            configure = recipe.split(f"-B /workspace/dependency-build/{package} \\\n", 1)[1].split("cmake --install", 1)[0]
            self.assertIn("-DCMAKE_INSTALL_LIBDIR=lib", configure)
        for config in ("cereal/lib/cmake/cereal/cerealConfig.cmake",
                       "cereal/lib/cmake/cereal/cerealTargets.cmake",
                       "rapidjson/lib/cmake/RapidJSON/RapidJSONConfig.cmake"):
            self.assertIn(f'test -s "$deps/{config}"', recipe)


if __name__ == "__main__":
    unittest.main()
