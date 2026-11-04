from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import devtools_install as install


class SifInstallerTests(unittest.TestCase):
    def test_primary_squashfs_boundaries_are_checked_without_container_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / 'test.sif'
            image.write_bytes(b'x' * 1000)
            valid = '1 | 1 | NONE | 100-900 | FS (Squashfs/*System/amd64)\n'
            with patch.object(install.subprocess, 'check_output', return_value=valid) as call:
                self.assertEqual(install.sif_offset(image), 100)
                self.assertEqual(call.call_args.args[0], [install.APPTAINER, 'sif', 'list', str(image)])
            for listing in ('', valid + valid, valid.replace('900', '1001'), valid.replace('*System', 'Data')):
                with patch.object(install.subprocess, 'check_output', return_value=listing), self.assertRaises(ValueError):
                    install.sif_offset(image)

    @unittest.skipUnless(shutil.which('mksquashfs') and shutil.which('unsquashfs'), 'Squashfs tools required')
    def test_extracts_real_squashfs_library_and_checks_files_before_installation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prefix = Path('/opt/devtools/hdf5/1.14.6-gcc14.3.0-zen3-' + 'a' * 32)
            tree = root / 'tree'
            source = tree / prefix.relative_to('/')
            for name in ('include/hdf5.mod', 'lib/libhdf5.a', 'lib/libhdf5.so.1', 'lib/cmake/HDF5Config.cmake'):
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name)
                path.chmod(0o644)
            for p in [source, *source.rglob('*')]:
                if p.is_dir():
                    p.chmod(0o755)
            (source / 'lib/libhdf5.so').symlink_to('libhdf5.so.1')
            expected = install.inventory(source)
            squash = root / 'payload.squashfs'
            subprocess.run(['mksquashfs', str(tree), str(squash), '-noappend', '-no-xattrs', '-processors', '1'],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            image = root / 'wrapped.sif'
            image.write_bytes(b'\0' * 4096 + squash.read_bytes())
            with patch.object(install, 'sif_offset', return_value=4096):
                result = install.extract_package(image, prefix, root / 'extracted', expected)
                self.assertEqual((result / 'lib/libhdf5.a').read_text(), 'lib/libhdf5.a')
                self.assertTrue((result / 'lib/libhdf5.so').is_symlink())
                changed = deepcopy(expected)
                next(row for row in changed if row['kind'] == 'file')['sha256'] = '0' * 64
                with self.assertRaisesRegex(ValueError, 'differs'):
                    install.extract_package(image, prefix, root / 'bad', changed)

    def test_sif_preflight_is_readonly_and_detects_corruption(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / 'libraries.sif'
            image.write_bytes(b'fixture-sif')
            manifest = root / 'files.json'
            manifest.write_text('[]')
            record = dict(name='hdf5', prefix='/opt/devtools/hdf5/1.14.6-gcc14.3.0-zen3-' + 'a' * 32,
                          image='libraries.sif', image_sha256=install.checksum(image), manifest='files.json',
                          manifest_sha256=install.checksum(manifest))
            release = root / 'release.json'
            release.write_text(json.dumps(dict(schema=1, purpose='cp2k-spack-devtools',
                                                partitions=['16V100'], packages=[record])))
            (root / 'release.sha256').write_text(install.checksum(release))
            target = root / 'native'
            with patch.object(install, 'package_prefix', return_value=target), \
                 patch.object(install.subprocess, 'run') as run:
                install.deploy(root)
                self.assertFalse(target.exists())
                run.assert_not_called()
                image.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'SIF checksum'):
                    install.deploy(root, install=True)
                self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
