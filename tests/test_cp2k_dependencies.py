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
            "unsupported": 'tblite_ver="0.8.0"\n',
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
            "elpa-2026.02.002.tar.gz": b"verified ELPA toolchain fixture",
        }
        for constant, name in (("WIGNERNJ_SHA256", "libwignernj-0.8.0.tar.gz"),
                               ("TBLITE_RELEASE_SHA256", "tblite-0.6.0.tar.xz"),
                               ("ELPA_SHA256", "elpa-2026.02.002.tar.gz")):
            digest = patch.object(cp2k_spack, constant,
                                  hashlib.sha256(self.contents[name]).hexdigest())
            digest.start()
            self.addCleanup(digest.stop)

    def download(self, url, **kwargs):
        name = url.removeprefix("https://www.cp2k.org/static/downloads/")
        return io.BytesIO(self.contents[name])

    def test_downloads_archives_and_only_refetches_damaged_cache_entries(self):
        expected = [self.cache / name for name in self.contents]
        with patch("urllib.request.urlopen", side_effect=self.download) as download:
            self.assertEqual(cp2k_spack.cache_source_updates(self.cache), expected)
            self.assertEqual(download.call_count, 3)
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


if __name__ == "__main__":
    unittest.main()
