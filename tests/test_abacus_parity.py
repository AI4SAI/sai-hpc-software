"""Presence is mandatory; it does not substitute for scientific benchmarks."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import shutil
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))
import abacus_dependencies as deps
import abacus_features as features
import software_controller as controller
from source_cache import checksum


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

    def test_installed_feature_checker_reuses_shared_hash_without_exporter_dependency(self):
        self.assertIs(features.checksum, checksum)
        # Exercise the copied installation, without repo/controller on sys.path.
        with tempfile.TemporaryDirectory() as temporary:
            installed = Path(temporary)
            files = ("abacus_features.py", "release_contract.py", "resolve_source.py",
                     "remote_controller.py", "source_cache.py")
            for name in files:
                shutil.copy2(ROOT / "controller" / name, installed / name)
            # Isolated mode excludes the checkout; add only installed metadata.
            result = subprocess.run([sys.executable, "-I", "-c",
                "import runpy,sys; sys.path.insert(0,sys.argv[1]); sys.argv=[sys.argv[1]+'/abacus_features.py','--help']; runpy.run_path(sys.argv[0],run_name='__main__')",
                str(installed)], check=True, capture_output=True, text=True)
            self.assertIn("--native-phase", result.stdout)
            self.assertFalse((installed / "export_native.py").exists())
        entry = (ROOT / "controller/container_entry.sh").read_text()
        self.assertIn('/control/source_cache.py "$INSTALL_PREFIX/share/sai/"', entry)

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
        self.assertIs(deps.checksum, checksum)
        deps.validate_members(["package/include/header.h", "package/"], "package")
        for value in ("../header.h", "/workspace/header.h", "package/../../header.h", "other/header.h"):
            with self.subTest(path=value):
                with self.assertRaises(ValueError):
                    deps.validate_members([value], "package")
        for destination in (Path("/opt/software/expanded"), Path("relative")):
            with self.assertRaisesRegex(ValueError, "build overlay"):
                deps.unpack(Path("/input/abacus-dependencies"), destination, self.lock)

    def test_required_headers_are_nonempty_regular_archive_members(self):
        header = "package/include/RI/physics/LR.h"
        for suffix in (".tar.gz", ".zip"):
            for kind in ("present", "missing", "empty", "directory"):
                with self.subTest(suffix=suffix, kind=kind), tempfile.TemporaryDirectory() as temporary:
                    archive = Path(temporary) / ("package" + suffix)
                    destination = Path(temporary) / "extracted"
                    name = "package/unrelated.h" if kind == "missing" else header
                    data = b"header contents" if kind != "empty" else b""
                    if suffix == ".zip":
                        entry = zipfile.ZipInfo(name + ("/" if kind == "directory" else ""))
                        entry.external_attr = ((stat.S_IFDIR if kind == "directory" else stat.S_IFREG) | 0o644) << 16
                        with zipfile.ZipFile(archive, "w") as stream:
                            stream.writestr(entry, data)
                    else:
                        entry = tarfile.TarInfo(name)
                        entry.size = len(data)
                        if kind == "directory":
                            entry.type, entry.size = tarfile.DIRTYPE, 0
                        with tarfile.open(archive, "w:gz") as stream:
                            stream.addfile(entry, io.BytesIO(data))
                    if kind == "present":
                        deps.extract_archive(archive, destination, "package", ["include/RI/physics/LR.h"])
                        self.assertEqual((destination / header).read_bytes(), data)
                    else:
                        with self.assertRaisesRegex(ValueError, "nonempty regular dependency file"):
                            deps.extract_archive(archive, destination, "package", ["include/RI/physics/LR.h"])
                        self.assertFalse(destination.exists())

    def test_download_cache_is_pinned_shared_and_never_expands_archives(self):
        payload = b"opaque archive bytes; never extract on the host"
        item = dict(file="LibRI.tar.gz", url="https://example.invalid/LibRI.tar.gz",
                    sha256=hashlib.sha256(payload).hexdigest())
        lock = {"archives": [item, dict(file="site-only.tar.gz", sha256="a" * 64)]}
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            start = threading.Barrier(4)
            def populate(_):
                start.wait(timeout=5)
                deps.cache_archives(cache, lock)
            with patch.object(deps, "urlopen", side_effect=lambda *a, **kw: io.BytesIO(payload)) as download, \
                    ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(populate, range(4)))
                deps.cache_archives(cache, lock)
            download.assert_called_once_with(item["url"], timeout=120)
            self.assertEqual((cache / item["file"]).read_bytes(), payload)
            self.assertEqual({path.name for path in cache.iterdir()}, {item["file"], ".archives.lock"})
            self.assertEqual(stat.S_IMODE((cache / item["file"]).stat().st_mode), 0o444)

    def test_download_cache_rejects_bad_contents_and_preserves_existing_files(self):
        item = dict(file="LibRI.tar.gz", url="https://example.invalid/LibRI.tar.gz",
                    sha256=hashlib.sha256(b"expected").hexdigest())
        lock = {"archives": [item]}
        for kind in ("bad-download", "existing-mismatch", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                cache = Path(temporary)
                archive = cache / item["file"]
                if kind == "existing-mismatch":
                    archive.write_bytes(b"preserve existing")
                elif kind == "symlink":
                    (cache / "target").write_bytes(b"expected")
                    archive.symlink_to(cache / "target")
                with patch.object(deps, "urlopen", return_value=io.BytesIO(b"wrong")) as download:
                    with self.assertRaisesRegex(ValueError, "checksum mismatch|changed cached dependency"):
                        deps.cache_archives(cache, lock)
                if kind == "bad-download":
                    self.assertFalse(archive.exists())
                    self.assertEqual({path.name for path in cache.iterdir()}, {".archives.lock"})
                else:
                    download.assert_not_called()
                    self.assertEqual(archive.is_symlink(), kind == "symlink")
                    self.assertEqual(archive.read_bytes(), b"expected" if kind == "symlink" else b"preserve existing")

    def test_unpack_selects_update_cache_only_for_url_archives(self):
        items = [dict(file="site.tar.gz", root="site", sha256="a" * 64),
                 dict(file="updated.tar.gz", root="updated", sha256="a" * 64,
                      url="https://example.invalid/update", required_files=["include/RI/physics/LR.h"])]
        source, destination = Path("/input/abacus-dependencies"), Path("/workspace/dependencies")
        with patch.object(Path, "exists", return_value=False), \
                patch.object(Path, "is_file", return_value=True), \
                patch.object(Path, "mkdir"), patch.object(Path, "write_text"), \
                patch.object(deps, "checksum", return_value="a" * 64) as digest, \
                patch.object(deps, "extract_archive") as extract:
            deps.unpack(source, destination, {"archives": items})
        self.assertEqual([call.args[0] for call in digest.call_args_list],
                         [source / "site.tar.gz", Path(deps.CACHE_MOUNT) / "updated.tar.gz"])
        self.assertEqual([call.args for call in extract.call_args_list],
                         [(source / "site.tar.gz", destination, "site", ()),
                          (Path(deps.CACHE_MOUNT) / "updated.tar.gz", destination, "updated",
                           ["include/RI/physics/LR.h"])])

    def test_uploaded_cache_source_is_offline_and_hits_do_not_require_reupload(self):
        payload = b"uploaded archive"
        item = dict(file="LibRI.tar.gz", url="https://example.invalid/LibRI.tar.gz",
                    sha256=hashlib.sha256(payload).hexdigest())
        with tempfile.TemporaryDirectory() as temporary, patch.object(deps, "urlopen") as download:
            root = Path(temporary)
            source, cache = root / "input", root / "cache"
            source.mkdir()
            (source / item["file"]).write_bytes(payload)
            deps.cache_archives(cache, {"archives": [item]}, source=source)
            (source / item["file"]).unlink()
            deps.cache_archives(cache, {"archives": [item]}, source=source)
            self.assertEqual((cache / item["file"]).read_bytes(), payload)
            self.assertEqual({path.name for path in cache.iterdir()}, {item["file"], ".archives.lock"})
        download.assert_not_called()

    def test_bad_uploaded_source_never_falls_back_to_network_or_leaves_partial_cache(self):
        item = dict(file="LibRI.tar.gz", url="https://example.invalid/LibRI.tar.gz",
                    sha256=hashlib.sha256(b"expected").hexdigest())
        for kind in ("missing", "mismatch", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary, \
                    patch.object(deps, "urlopen") as download:
                root = Path(temporary)
                source, cache = root / "input", root / "cache"
                source.mkdir()
                uploaded = source / item["file"]
                if kind == "mismatch":
                    uploaded.write_bytes(b"wrong")
                elif kind == "symlink":
                    (root / "target").write_bytes(b"expected")
                    uploaded.symlink_to(root / "target")
                with self.assertRaisesRegex(ValueError, "uploaded dependency|checksum mismatch"):
                    deps.cache_archives(cache, {"archives": [item]}, source=source)
                download.assert_not_called()
                self.assertEqual({path.name for path in cache.iterdir()}, {".archives.lock"})

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
                                  overlay_mb=8192, track="development", source_ref="develop")
        with patch.object(controller, "ROOT", Path("/home/test/sai-hpc-software")):
            script = controller.render_job(args)
        mount = self.lock["site_archive_root"] + ":/input/abacus-dependencies:ro"
        self.assertIn(mount, script)
        if any("url" in item for item in self.lock["archives"]):
            self.assertIn(f"/home/test/sai-hpc-software/{deps.ARCHIVE_CACHE}:{deps.CACHE_MOUNT}:ro", script)
        self.assertNotIn("--bind /opt/apps:/opt/apps", script)
        verify = next(line for line in script.splitlines() if "container_entry.sh verify" in line)
        self.assertNotIn("/input/abacus-dependencies", verify)
        self.assertNotIn(deps.CACHE_MOUNT, verify)
        subprocess.run(["bash", "-n"], input=script, text=True, check=True)
        for name in ("abacus_dependencies.py", "abacus_dependencies.sh", "abacus_dependency_lock.json", "abacus_features.py"):
            self.assertIn(name, controller.contract_files("abacus"))

    def test_site_only_dependencies_do_not_create_or_bind_an_update_cache(self):
        lock = dict(self.lock, archives=[item for item in self.lock["archives"] if "url" not in item])
        with tempfile.TemporaryDirectory() as temporary, patch.object(deps, "load_lock", return_value=lock), \
                patch.object(deps, "urlopen") as download:
            cache = Path(temporary) / "unused"
            deps.cache_archives(cache)
            self.assertFalse(cache.exists())
            self.assertEqual(deps.dependency_binds(Path("/home/test/sai-hpc-software")),
                             ((Path(lock["site_archive_root"]), "/input/abacus-dependencies"),))
        download.assert_not_called()

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

    def test_export_permissions_do_not_depend_on_fakeroot_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "prefix"
            directory.mkdir(mode=0o700)
            with self.assertRaisesRegex(ValueError, "ordinary users"):
                features.check_public_mode(directory)
            directory.chmod(0o755)
            features.check_public_mode(directory)
            library = directory / "libnep.so"
            library.write_bytes(b"elf fixture")
            for mode in (0o600, 0o700, 0o666):
                library.chmod(mode)
                with self.assertRaisesRegex(ValueError, "ordinary users"):
                    features.check_public_mode(library)
            library.chmod(0o644)
            features.check_public_mode(library)
            with self.assertRaises(ValueError):
                features.check_public_mode(library, executable=True)
            library.chmod(0o755)
            features.check_public_mode(library, executable=True)
        entry = (ROOT / "controller/container_entry.sh").read_text()
        self.assertIn("umask 022", entry)
        self.assertIn("chmod -R a+rX,u+w,go-w /workspace/export/opt/software", entry)
        self.assertNotIn("chmod -R a+rX,u+w,go-w /workspace/export\n", entry)


if __name__ == "__main__":
    unittest.main()
