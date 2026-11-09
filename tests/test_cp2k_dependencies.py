"""Check dependency source selection and the runner's verified archive cache."""
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "controller"))
import cp2k_spack


class CP2KDependencyVersionTests(unittest.TestCase):
    def test_unknown_or_missing_toolchain_version_stops_before_cmake(self):
        cases = {
            "unsupported": 'tblite_ver="0.9.0"\n',
            "missing-version": "# No dependency version was selected.\n",
            "missing-toolchain": None,
        }
        parent = ROOT / ".test-work"
        parent.mkdir(exist_ok=True)
        for name, toolchain_text in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory(dir=parent) as directory:
                root = Path(directory)
                source = root / "source"
                toolchain = source / "tools/toolchain/scripts/stage8/install_tblite.sh"
                toolchain.parent.mkdir(parents=True)
                if toolchain_text is not None:
                    toolchain.write_text(toolchain_text)
                binaries = root / "bin"
                binaries.mkdir()
                cmake = binaries / "cmake"
                cmake.write_text('#!/bin/sh\nprintf called > "$CMAKE_SENTINEL"\nexit 91\n')
                cmake.chmod(0o755)
                marker = root / "cmake-called"
                environment = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ["PATH"],
                                   CMAKE_SENTINEL=str(marker), CC="/usr/bin/false",
                                   CXX="/usr/bin/false", FC="/usr/bin/false")
                result = subprocess.run(
                    ["bash", str(ROOT / "controller/cp2k_dependencies.sh"),
                     str(root / "cache"), str(root / "work"), str(root / "prefix"),
                     str(source), str(root / "updates")],
                    env=environment, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse(marker.exists(), result.stdout + result.stderr)
                diagnostic = (result.stdout + result.stderr).lower()
                self.assertIn("tblite", diagnostic)
                if toolchain_text is not None:
                    self.assertIn("version", diagnostic)


class CP2KDependencyArchiveTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".test-work"
        parent.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.cache = Path(temporary.name)
        self.contents = {
            "libwignernj-0.8.0.tar.gz": b"verified wignernj fixture",
            "tblite-0.6.0.tar.xz": b"verified tblite release fixture",
            "tblite-0.8.0.tar.xz": b"verified tblite development fixture",
            "elpa-2026.02.002.tar.gz": b"verified ELPA toolchain fixture",
            "libxc-7.1.2.tar.bz2": b"verified LibXC toolchain fixture",
            "libxs-cc9188a.tar.gz": b"verified LibXS toolchain fixture",
        }
        for constant, name in (("WIGNERNJ_SHA256", "libwignernj-0.8.0.tar.gz"),
                               ("TBLITE_RELEASE_SHA256", "tblite-0.6.0.tar.xz"),
                               ("TBLITE_DEVELOPMENT_SHA256", "tblite-0.8.0.tar.xz"),
                               ("ELPA_SHA256", "elpa-2026.02.002.tar.gz"),
                               ("LIBXC_SHA256", "libxc-7.1.2.tar.bz2"),
                               ("LIBXS_SHA256", "libxs-cc9188a.tar.gz")):
            digest = patch.object(cp2k_spack, constant,
                                  hashlib.sha256(self.contents[name]).hexdigest())
            digest.start()
            self.addCleanup(digest.stop)

    def download(self, url, **kwargs):
        name = (cp2k_spack.LIBXS_ARCHIVE if url == cp2k_spack.LIBXS_URL else
                url.removeprefix("https://www.cp2k.org/static/downloads/"))
        return io.BytesIO(self.contents[name])

    def test_downloads_archives_and_only_refetches_damaged_cache_entries(self):
        expected = [self.cache / name for name in self.contents]
        with patch("urllib.request.urlopen", side_effect=self.download) as download:
            self.assertEqual(cp2k_spack.cache_source_updates(self.cache), expected)
            self.assertEqual(download.call_count, 6)
            self.assertEqual(download.call_args.args[0], cp2k_spack.LIBXS_URL)
            for archive in expected:
                self.assertEqual(archive.read_bytes(), self.contents[archive.name])
            download.reset_mock()
            self.assertEqual(cp2k_spack.cache_source_updates(self.cache), expected)
            download.assert_not_called()

            tblite = self.cache / "tblite-0.6.0.tar.xz"
            tblite.write_bytes(b"damaged cached archive")
            self.assertEqual(cp2k_spack.cache_source_updates(self.cache), expected)
            download.assert_called_once()
            self.assertEqual(download.call_args.args[0],
                             "https://www.cp2k.org/static/downloads/tblite-0.6.0.tar.xz")
            self.assertEqual(tblite.read_bytes(), self.contents[tblite.name])

    def test_bad_download_is_rejected_without_publishing_unverified_bytes(self):
        wignernj = self.cache / "libwignernj-0.8.0.tar.gz"
        wignernj.write_bytes(self.contents[wignernj.name])
        tblite = self.cache / "tblite-0.6.0.tar.xz"
        for previous in (None, b"damaged cached archive"):
            with self.subTest(previous_cache=previous is not None):
                if previous is None:
                    tblite.unlink(missing_ok=True)
                else:
                    tblite.write_bytes(previous)
                with patch("urllib.request.urlopen", return_value=io.BytesIO(b"wrong download")), \
                        self.assertRaisesRegex(ValueError, "checksum"):
                    cp2k_spack.cache_source_updates(self.cache)
                if previous is None:
                    self.assertFalse(tblite.exists())
                else:
                    self.assertEqual(tblite.read_bytes(), previous)
                self.assertEqual(wignernj.read_bytes(), self.contents[wignernj.name])


class CP2KDevelopmentBundleTests(unittest.TestCase):
    def test_changed_pins_or_damaged_bundle_stop_before_cmake(self):
        parent = ROOT / '.test-work'
        parent.mkdir(exist_ok=True)
        for case in ('tblite-checksum', 'dftd4-version', 'dftd4-checksum', 'damaged-archive'):
            with self.subTest(case=case), tempfile.TemporaryDirectory(dir=parent) as temporary:
                root = Path(temporary)
                source = root / 'source'
                stage = source / 'tools/toolchain/scripts/stage8'
                stage.mkdir(parents=True)
                tblite_sha = '0' * 64 if case == 'tblite-checksum' else cp2k_spack.TBLITE_DEVELOPMENT_SHA256
                dftd4_ver = '4.4.0' if case == 'dftd4-version' else '4.3.1'
                dftd4_sha = ('0' * 64 if case == 'dftd4-checksum' else
                            '32c0b498a7b07b1552e727333d9c83ab627d1bdf4ff46b7b8b31af9159fb2928')
                (stage / 'install_tblite.sh').write_text(
                    f'tblite_ver="0.8.0"\ntblite_sha256="{tblite_sha}"\n')
                (stage / 'install_dftd4.sh').write_text(
                    f'dftd4_ver="{dftd4_ver}"\ndftd4_sha256="{dftd4_sha}"\n')
                updates = root / 'updates'
                updates.mkdir()
                (updates / cp2k_spack.TBLITE_DEVELOPMENT_ARCHIVE).write_bytes(b'damaged archive')
                binaries = root / 'bin'
                binaries.mkdir()
                cmake = binaries / 'cmake'
                cmake.write_text('#!/bin/sh\nprintf called > "$CMAKE_SENTINEL"\nexit 91\n')
                cmake.chmod(0o755)
                marker = root / 'cmake-called'
                env = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ['PATH'],
                           CMAKE_SENTINEL=str(marker))
                result = subprocess.run(
                    ['bash', str(ROOT / 'controller/cp2k_dependencies.sh'),
                     str(root / 'cache'), str(root / 'work'), str(root / 'prefix'),
                     str(source), str(updates)], env=env, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse(marker.exists())
                self.assertFalse((root / 'work/tblite-source').exists())
                if case != 'damaged-archive':
                    self.assertIn('unsupported CP2K', result.stderr)


class CP2KLibxsSelectionTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".test-work"
        parent.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.toolchain = self.source / "tools/toolchain/scripts/stage4/install_libxs.sh"
        self.toolchain.parent.mkdir(parents=True)

    def run_helper(self, version, checksum, declaration=""):
        self.toolchain.write_text(
            f'libxs_ver="{version}"\nlibxs_sha256="{checksum}"\n{declaration}')
        return subprocess.run(
            ["bash", str(ROOT / "controller/cp2k_libxs.sh"), str(self.source),
             str(self.root / "cache"), str(self.root / "updates"),
             str(self.root / "work"), str(self.root / "prefix")],
            capture_output=True, text=True, timeout=10)

    def test_source_declaration_selects_release_or_updated_archive_without_fallback(self):
        for version, checksum, directory in (
                ("1.0.0", "de26f50cb986a2f0e4f92c0eb489d40a44f7e4c5acd22751a6cfa2829dabd04d", "cache"),
                ("930a2c4", "552f9dd5af66b593d81aeb5aeb8acedba01943ae8d0c1bc036ea79f5fa591e88", "updates"),
                ("cc9188a", cp2k_spack.LIBXS_SHA256, "updates")):
            with self.subTest(version=version):
                result = self.run_helper(version, checksum)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(str(self.root / directory / f"libxs-{version}.tar.gz"), result.stderr)
                self.assertFalse((self.root / "work").exists())

    def test_changed_version_or_checksum_is_rejected(self):
        for version, checksum in (("1.1.0", cp2k_spack.LIBXS_SHA256),
                                  ("930a2c4", "0" * 64), ("cc9188a", "0" * 64), ("", "")):
            with self.subTest(version=version):
                result = self.run_helper(version, checksum)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unsupported or missing CP2K LibXS", result.stderr)
                self.assertFalse((self.root / "work").exists())

    def test_intermediate_development_cannot_drop_declared_jit_repair(self):
        result = self.run_helper(
            "1.0.0", "de26f50cb986a2f0e4f92c0eb489d40a44f7e4c5acd22751a6cfa2829dabd04d",
            'patch -p1 < "libxs-${libxs_ver}-jit-handle.patch"\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing LIBXS patch", result.stderr)
        self.assertFalse((self.root / "work").exists())

    def test_development_rejects_damaged_archive_before_extraction(self):
        updates = self.root / "updates"
        updates.mkdir()
        (updates / cp2k_spack.LIBXS_ARCHIVE).write_bytes(b"damaged archive")
        result = self.run_helper("cc9188a", cp2k_spack.LIBXS_SHA256)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "work").exists())


class CP2KLibxcSelectionTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".test-work"
        parent.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "source"
        self.toolchain = self.source / "tools/toolchain/scripts/stage3/install_libxc.sh"
        self.toolchain.parent.mkdir(parents=True)
        binaries = self.root / "bin"
        binaries.mkdir()
        cmake = binaries / "cmake"
        cmake.write_text('#!/bin/sh\nprintf called > "$CMAKE_SENTINEL"\nexit 91\n')
        cmake.chmod(0o755)
        self.marker = self.root / "cmake-called"
        self.site = self.root / "site-libxc"
        config = self.site / "lib/cmake/Libxc"
        config.mkdir(parents=True)
        (config / "LibxcConfig.cmake").touch()
        (config / "LibxcConfigVersion.cmake").write_text('set(PACKAGE_VERSION "7.0.0")\n')
        self.environment = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ["PATH"],
                                CMAKE_SENTINEL=str(self.marker), LIBXC_ROOT=str(self.site))

    def run_helper(self, version, checksum, *, accel="CUDA", architecture="70"):
        self.toolchain.write_text(f'libxc_ver="{version}"\nlibxc_sha256="{checksum}"\n')
        return subprocess.run(
            ["bash", str(ROOT / "controller/cp2k_libxc.sh"), str(self.source),
             str(self.root / "updates"), str(self.root / "work"),
             str(self.root / "private-libxc"), accel, architecture],
            env=self.environment, capture_output=True, text=True, timeout=10)

    def test_accepted_release_keeps_site_libxc_without_build_or_download(self):
        result = self.run_helper("7.0.0", "e9ae69f8966d8de6b7585abd9fab588794ada1fab8f689337959a35abbf9527d")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.root / "private-libxc").exists())
        self.assertIn(str(self.site), (self.root / "work/environment.sh").read_text())

    def test_unknown_source_or_changed_checksum_cannot_silently_use_old_site(self):
        for version, checksum in (("7.2.0", cp2k_spack.LIBXC_SHA256),
                                  ("7.1.2", "0" * 64), ("", "")):
            with self.subTest(version=version, checksum=checksum):
                result = self.run_helper(version, checksum)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unsupported or missing CP2K LibXC", result.stderr)
                self.assertFalse(self.marker.exists())
                self.assertFalse((self.root / "work/environment.sh").exists())

    def test_development_rejects_damaged_archive_before_cmake(self):
        updates = self.root / "updates"
        updates.mkdir()
        (updates / cp2k_spack.LIBXC_ARCHIVE).write_bytes(b"damaged archive")
        result = self.run_helper("7.1.2", cp2k_spack.LIBXC_SHA256)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.root / "work/environment.sh").exists())

    def test_release_rejects_replaced_site_version(self):
        (self.site / "lib/cmake/Libxc/LibxcConfigVersion.cmake").write_text(
            'set(PACKAGE_VERSION "6.2.2")\n')
        result = self.run_helper("7.0.0", "e9ae69f8966d8de6b7585abd9fab588794ada1fab8f689337959a35abbf9527d")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.root / "work/environment.sh").exists())


if __name__ == "__main__":
    unittest.main()
