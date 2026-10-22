import ctypes
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controller"))
from md_probe import lammps_inventory, suppress_native_output


class LibraryLayoutTests(unittest.TestCase):
    def test_resolves_shared_library_in_lib_or_lib64(self):
        for directory in ("lib", "lib64"):
            with self.subTest(directory=directory), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / directory).mkdir()
                library = root / directory / "liblammps.so.0"
                library.touch()
                (root / directory / "liblammps.so").symlink_to(library.name)
                # Stop at the native load boundary: this tests discovery without
                # requiring a LAMMPS installation or initializing any GPUs.
                with patch("md_probe.ctypes.CDLL", side_effect=RuntimeError("load boundary")) as load:
                    with self.assertRaisesRegex(RuntimeError, "load boundary"):
                        lammps_inventory(root)
                    load.assert_called_once_with(str(library.resolve()), mode=ctypes.RTLD_GLOBAL)

    def test_does_not_fall_back_outside_requested_prefix(self):
        with tempfile.TemporaryDirectory() as tmp, patch("md_probe.ctypes.CDLL") as load:
            with self.assertRaisesRegex(ValueError, "shared liblammps required"):
                lammps_inventory(tmp)
            load.assert_not_called()


class NativeOutputTests(unittest.TestCase):
    def test_native_file_descriptors_are_silenced_and_restored(self):
        read_fd, write_fd = os.pipe()
        saved = os.dup(1)
        try:
            os.dup2(write_fd, 1)
            with suppress_native_output():
                os.write(1, b"LAMMPS banner must not enter JSON\n")
            # The context restores the pipe fd as stdout; restore the original
            # terminal before closing the pipe's last descriptor.
            os.dup2(saved, 1)
            os.close(write_fd)
            write_fd = -1
            self.assertEqual(os.read(read_fd, 4096), b"")
        finally:
            if write_fd >= 0:
                os.close(write_fd)
            os.dup2(saved, 1)
            os.close(saved)
            os.close(read_fd)


if __name__ == "__main__":
    unittest.main()
