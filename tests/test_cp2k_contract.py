import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controller"))
import cp2k_feature_contract as contract
import software_controller as software
from export_native import MANIFEST_PATH, inventory, read_installed_manifests, write_manifests
from release_contract import make_identity
from remote_controller import HPCX_ROOT, HCOLL_BUILD_PREFIX


class CP2KSourceLayoutTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.source = Path(temporary.name) / "source"
        self.prefix = Path(temporary.name) / "installed"
        (self.source / "src").mkdir(parents=True)
        (self.prefix / "share/sai").mkdir(parents=True)
        (self.source / "CMakeLists.txt").write_text("project(cp2k)\n")
        (self.source / "src/libgrpp_integrals.F").write_text("   USE libgrpp, ONLY: libgrpp_init\n")
        self.lists = "list(\n APPEND\n CP2K_SRCS_C\n grpp/grpp.c)\nlist(APPEND CP2K_SRCS_F grpp/libgrpp.F)\n"
        self.include = 'include("${CMAKE_CURRENT_LIST_DIR}/CMakeSources.cmake")\n'

    def check(self):
        contract.source_changes(self.prefix, "a" * 40, self.source)
        return json.loads((self.prefix / "share/sai/upstream-feature-changes.json").read_text())

    def test_release_and_development_layouts_pin_the_actual_source_lists(self):
        for split in (False, True):
            with self.subTest(split=split):
                (self.source / "src/CMakeLists.txt").write_text(self.include if split else self.lists)
                (self.source / "src/CMakeSources.cmake").write_text(self.lists)
                evidence = self.check()["evidence"]
                expected = {"CMakeLists.txt", "src/CMakeLists.txt", "src/libgrpp_integrals.F"}
                if split:
                    expected.add("src/CMakeSources.cmake")
                self.assertEqual(set(evidence), expected)
                for name, digest in evidence.items():
                    self.assertEqual(digest, hashlib.sha256((self.source / name).read_bytes()).hexdigest())

    def test_unused_or_commented_source_list_does_not_prove_libgrpp(self):
        (self.source / "src/CMakeSources.cmake").write_text(self.lists)
        for text in ("", "# " + self.include, "# grpp/grpp.c grpp/libgrpp.F\n",
                     self.lists.replace("CP2K_SRCS_C", "UNUSED_SOURCES"),
                     self.lists.replace("list(APPEND CP2K_SRCS_F", "# list(APPEND CP2K_SRCS_F")):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "proven built in"):
                (self.source / "src/CMakeLists.txt").write_text(text)
                self.check()

    def test_split_layout_still_requires_both_languages_and_fortran_integration(self):
        (self.source / "src/CMakeLists.txt").write_text(self.include)
        for missing in ("grpp/grpp.c", "grpp/libgrpp.F"):
            with self.subTest(missing=missing), self.assertRaisesRegex(ValueError, "proven built in"):
                (self.source / "src/CMakeSources.cmake").write_text(self.lists.replace(missing, ""))
                self.check()
        (self.source / "src/CMakeSources.cmake").write_text(self.lists)
        (self.source / "src/libgrpp_integrals.F").write_text("! USE libgrpp\n")
        with self.assertRaisesRegex(ValueError, "integration is absent"):
            self.check()


class CP2KContractTests(unittest.TestCase):
    def test_installed_verifier_runs_without_controller_imports(self):
        self.assertEqual(contract.HPCX_ROOT, HPCX_ROOT)
        self.assertEqual(contract.HCOLL_BUILD_PREFIX, HCOLL_BUILD_PREFIX)
        with tempfile.TemporaryDirectory() as temporary:
            installed = Path(temporary) / "cp2k_feature_contract.py"
            shutil.copyfile(contract.__file__, installed)
            process = subprocess.run([sys.executable, "-I", str(installed), "--help"],
                                     cwd=temporary, text=True, capture_output=True)
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertIn("verify", process.stdout)

    def test_runtime_roots_exclude_other_cp2k_installation(self):
        roots = contract.runtime_roots("16v100-avx2")
        self.assertFalse(any(root.startswith("/opt/apps/cp2k/") for root in roots))
        self.assertFalse(any(root.startswith("/opt/mellanox") for root in roots))

    def flags(self, gpu=True):
        flags = contract.BASE_FLAGS | {"libxs", "libxsmm"}
        if gpu:
            flags |= contract.GPU_FLAGS
        return " CP2K version 2026.2\n cp2kflags: " + " ".join(sorted(flags)) + "\n"

    def cache(self, target="4v100-avx512"):
        entries = {"CP2K_USE_" + key: "ON" for key in contract.REQUIRED_OPTIONS}
        entries.update(CMAKE_INSTALL_PREFIX="/opt/software/cp2k/test/" + target,
                       CMAKE_INSTALL_LIBDIR="lib64",
                       CP2K_USE_ACCEL="NONE" if target == "dsprhbm" else "CUDA",
                       CP2K_USE_SPLA_GEMM_OFFLOADING="OFF" if target == "dsprhbm" else "ON",
                       CP2K_ELPA_ROOT="/opt/software/cp2k/test/" + target + "/dependencies/elpa",
                       CP2K_USE_CUSOLVER_MP="ON")
        for lang in ("C", "CXX", "Fortran"):
            entries[f"CMAKE_{lang}_FLAGS"] = "-O3 -march=native -mtune=native"
            executable = {"C": "gcc", "CXX": "g++", "Fortran": "gfortran"}[lang]
            entries[f"CMAKE_{lang}_COMPILER"] = "/opt/devtools/gcc/14.3.0/bin/" + executable
        for library in ("cusolver", "cudart", "cublasLt", "cublas"):
            entries["pkgcfg_lib_CP2K_ELPA_" + library] = (
                "/opt/devtools/nvidia/cuda-12.9.1/lib64/lib" + library + ".so")
        return "\n".join(f"{key}:STRING={value}" for key, value in entries.items())

    def test_hdf5_accepts_cmake_and_autotools_parallel_settings_only(self):
        for value in ("yes", "ON", "on", "YES"):
            contract.check_parallel_hdf5("Features:\n                     Parallel HDF5: " + value + "\n")
        for text in ("", "Parallel Filtered Dataset Writes: ON\n", "Parallel HDF5: OFF\n",
                     "Parallel HDF5: no\n", "Parallel HDF5: unknown\n",
                     "Parallel HDF5: ON\nParallel HDF5: OFF\n", "Parallel HDF5: ON extra\n",
                     "Parallel HDF5:\nON\n"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "parallel support"):
                contract.check_parallel_hdf5(text)

    def test_cmake_cannot_silently_choose_system_compiler_aliases(self):
        text = self.cache()
        prefix = "/opt/software/cp2k/test/4v100-avx512"
        for executable, alias in (("gcc", "cc"), ("g++", "c++"), ("gfortran", "f95")):
            pinned = "/opt/devtools/gcc/14.3.0/bin/" + executable
            for wrong in ("/usr/bin/" + alias, pinned.replace("14.3.0", "13.3.0"), ""):
                with self.subTest(wrong=wrong), self.assertRaisesRegex(ValueError, "pinned GCC"):
                    contract.check_cache(text.replace(pinned, wrong), prefix, "4v100-avx512")

    def test_native_gpu_elpa_requires_resolved_cuda_link_libraries(self):
        text = self.cache("16v100-avx2")
        prefix = "/opt/software/cp2k/test/16v100-avx2"
        for library in ("cudart", "cublas"):
            full = "/opt/devtools/nvidia/cuda-12.9.1/lib64/lib" + library + ".so"
            for replacement in (library, "-l" + library, "", full + "-NOTFOUND",
                                full.replace("12.9.1", "12.8.0")):
                with self.subTest(library=library, replacement=replacement):
                    with self.assertRaisesRegex(ValueError, "ELPA CUDA library"):
                        contract.check_cache(text.replace(full, replacement), prefix, "16v100-avx2")
        parsed = contract.check_cache(text.replace("/lib64/lib", "/targets/x86_64-linux/lib/lib"),
                                      prefix, "16v100-avx2")
        self.assertEqual(parsed["CP2K_USE_ACCEL"], "CUDA")
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

    def test_spla_offload_requires_matching_cache_and_binary_capability(self):
        for target in ("16v100-avx2", "4v100-avx512", "8v100v0-avx512", "dsprhbm"):
            text = self.cache(target)
            prefix = "/opt/software/cp2k/test/" + target
            expected = "OFF" if target == "dsprhbm" else "ON"
            entry = "CP2K_USE_SPLA_GEMM_OFFLOADING:STRING=" + expected
            contract.check_cache(text, prefix, target)
            for wrong in ("", entry.replace(expected, "ON" if expected == "OFF" else "OFF")):
                with self.subTest(target=target, wrong=wrong):
                    with self.assertRaisesRegex(ValueError, "SpLA GEMM offload"):
                        contract.check_cache(text.replace(entry, wrong), prefix, target)
            flags = self.flags(gpu=target != "dsprhbm")
            wrong_flags = (flags.replace("spla_gemm_offloading", "") if target != "dsprhbm"
                           else flags.rstrip() + " spla_gemm_offloading\n")
            with self.subTest(target=target), self.assertRaises(ValueError):
                contract.check_flags(wrong_flags, target)

    def test_cmake_on_and_correct_prefix_are_required(self):
        text = self.cache()
        contract.check_cache(text, "/opt/software/cp2k/test/4v100-avx512", "4v100-avx512")
        for bad in (text.replace("CP2K_USE_ELPA:STRING=ON", "CP2K_USE_ELPA:STRING=OFF"),
                    text.replace("/dependencies/elpa", "/dependencies/wrong-elpa"),
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

    def test_spla_dependency_alone_does_not_establish_cp2k_support(self):
        # Installed dependencies and version flags did not catch the missing
        # CP2K_USE_SPLA option. Check actual application configuration on both
        # CPU and GPU targets, including a cache that omits the option entirely.
        for target in ("dsprhbm", "16v100-avx2"):
            text = self.cache(target)
            prefix = "/opt/software/cp2k/test/" + target
            for replacement in ("CP2K_USE_SPLA:BOOL=OFF", ""):
                with self.subTest(target=target, replacement=replacement):
                    bad = text.replace("CP2K_USE_SPLA:STRING=ON", replacement)
                    with self.assertRaisesRegex(ValueError, "disabled: SPLA"):
                        contract.check_cache(bad, prefix, target)

    def test_ldd_and_rpath_must_resolve_outside_build_tree(self):
        prefix = "/opt/apps/cp2k/test/4V100"
        links = "\n".join((
            "libelpa_openmp.so => " + prefix + "/dependencies/elpa/lib/libelpa_openmp.so.19",
            "libhcoll.so.1 => " + HPCX_ROOT + "/hcoll/lib/libhcoll.so.1",
            "libocoms.so.0 => " + HPCX_ROOT + "/hcoll/lib/libocoms.so.0",
            "libcusolverMp.so.0 => /opt/devtools/nvidia/mp_libs/lib/libcusolverMp.so.0",
            "libnccl.so.2 => /opt/devtools/nvidia/nccl_2.29.3_cuda12.9/lib/libnccl.so.2",
            "libcuda.so.1 => /opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/cuda/12.9/compat/libcuda.so.1"))
        contract.check_linkage(links, "4v100-avx512", prefix)
        for wrong in ("/opt/devtools/elpa/elpa-2026.02.001-2603-gnu/nvidia", prefix + "/../another"):
            with self.assertRaisesRegex(ValueError, "native ELPA"):
                contract.check_linkage(links.replace(prefix + "/dependencies/elpa", wrong), "4v100-avx512", prefix)
        # A resolved host libcuda can still reject the NVRTC 12.9 PTX used by
        # DBCSR. All GPU deliveries must use the qualified compatibility API.
        for target in ("4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
            old_driver = links.replace(
                "/opt/devtools/nvidia/hpc_sdk/Linux_x86_64/26.3/cuda/12.9/compat/libcuda.so.1",
                "/.singularity.d/libs/libcuda.so.1")
            with self.subTest(target=target), self.assertRaisesRegex(ValueError, "libcuda"):
                contract.check_linkage(old_driver, target, prefix)
        contract.check_linkage("\n".join(links.splitlines()[:3]), "dsprhbm", prefix)
        for replacement in ("/opt/mellanox/hcoll", "/opt/devtools/another-hpcx/hcoll"):
            with self.subTest(replacement=replacement), self.assertRaisesRegex(ValueError, "shared HPCX"):
                contract.check_linkage(links.replace(HPCX_ROOT + "/hcoll", replacement), "4v100-avx512", prefix)
        for extra in ("libfoo => not found", "libfoo => /workspace/lib/libfoo.so"):
            with self.assertRaises(ValueError):
                contract.check_linkage(links + "\n" + extra, "4v100-avx512", prefix)
        contract.check_dynamic_paths("(RUNPATH) Library runpath: [$ORIGIN/../lib64:/opt/devtools/test/lib]")
        with self.assertRaises(ValueError):
            contract.check_dynamic_paths("(RUNPATH) Library runpath: [/workspace/build/lib]")

    def test_cp2k_cannot_publish_without_candidate_scientific_acceptance(self):
        for target in ("dsprhbm", "4v100-avx512", "16v100-avx2", "8v100v0-avx512"):
            self.assertEqual(software.required_acceptance("cp2k", target), ("cp2k_science",))
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
                               ("RUNPATH", "/opt/mellanox/hcoll/lib"),
                               ("RUNPATH", "/opt/mellanox/./hcoll/lib"),
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
        self.assertIn('--shell-script-stdin', script)
        export = next(line for line in script.splitlines() if 'cp2k_container_entry.sh export' in line)
        self.assertNotIn('/input/', export)
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
                                   + HPCX_ROOT + "/hcoll/lib:" + HPCX_ROOT + "/sharp/lib:"
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
                            ("LD_LIBRARY_PATH", environment["LD_LIBRARY_PATH"] + ":/opt/mellanox/hcoll/lib"),
                            ("LD_LIBRARY_PATH", environment["LD_LIBRARY_PATH"].replace(HPCX_ROOT + "/hcoll/lib:", "")),
                            ("LD_LIBRARY_PATH", environment["LD_LIBRARY_PATH"].replace(HPCX_ROOT + "/sharp/lib:", "")),
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

    def test_export_normalizes_real_setgid_copy_without_changing_build_store(self):
        identity = self.identity()
        prefix = Path(identity["install_prefix"]).relative_to('/')
        entry = contract.native_entry(identity, self.environment(identity))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source' / prefix
            library = Path('dependencies/spack/linux-zen5/libint-hash/lib')
            (source / library).mkdir(parents=True)
            (source / library / 'libint2.so').write_bytes(b'packaging fixture, not ELF')
            (source / library).chmod(0o2755)
            (source / 'bin').mkdir()
            binary = source / 'bin/cp2k.psmp'
            binary.write_text('#!/bin/sh\nexit 0\n')
            binary.chmod(0o755)
            exported = root / 'export' / prefix
            shutil.copytree(source, exported, symlinks=True)
            self.assertEqual(stat.S_IMODE((exported / library).stat().st_mode), 0o2755)
            with self.assertRaisesRegex(ValueError, 'unsafe installed-tree mode'):
                inventory([entry], root / 'export')
            # Execute the production normalization itself, substituting only
            # the private staging directory for its fixed container path.
            script = (Path(contract.__file__).parent / 'cp2k_container_entry.sh').read_text()
            command = next(line.strip() for line in script.splitlines() if line.strip().startswith('chmod -R '))
            command = command.replace('/workspace/export$INSTALL_PREFIX', '$CP2K_TEST_EXPORTED_PREFIX')
            subprocess.run(['bash', '-eu', '-c', command], check=True,
                           env=dict(os.environ, CP2K_TEST_EXPORTED_PREFIX=str(exported)))
            self.assertEqual(stat.S_IMODE((exported / library).stat().st_mode), 0o755)
            expected = write_manifests([entry], root / 'export')
            self.assertEqual(read_installed_manifests([entry], root / 'export'), expected)
            self.assertEqual(stat.S_IMODE((source / library).stat().st_mode), 0o2755)
            self.assertEqual((source / library / 'libint2.so').read_bytes(), b'packaging fixture, not ELF')


if __name__ == "__main__":
    unittest.main()
