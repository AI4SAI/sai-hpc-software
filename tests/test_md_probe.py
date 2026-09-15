import ctypes
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "controller"))
from md_probe import suppress_native_output


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
