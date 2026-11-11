"""Exercise the update1 handoff with real Squashfs bytes and untrusted paths."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1] / 'deployment/lammps-30Sep2026-update1'
sys.path.insert(0, str(PACKAGE))
SPEC = importlib.util.spec_from_file_location('lammps_update1_install', PACKAGE / 'install.py')
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)
sys.path.pop(0)


class LammpsUpdate1Deployment(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.package = self.root / 'package'; self.package.mkdir()
        self.source = self.root / 'squashfs'; self.source.mkdir()
        self.prefix = '/opt/apps/lammps/test/4V100'
        self.tree = self.source / self.prefix.lstrip('/')
        (self.tree / 'bin').mkdir(parents=True)
        (self.tree / 'lib').mkdir()
        (self.tree / 'share/lammps/potentials').mkdir(parents=True)
        for name, data in [('bin/lmp', b'ELF fixture'), ('lib/liblammps.so.0', b'shared library'),
                           ('share/lammps/potentials/Si.tersoff', b'potential file')]:
            path = self.tree / name; path.write_bytes(data); path.chmod(0o755 if name.startswith('bin/') else 0o644)
        (self.tree / 'lib/liblammps.so').symlink_to('liblammps.so.0')
        self.files = []
        for path in [self.tree, *sorted(self.tree.rglob('*'))]:
            name = str(path.relative_to(self.tree))
            if path.is_symlink():
                row = dict(path=name, kind='symlink', mode=0o777, target=os.readlink(path))
            elif path.is_dir():
                row = dict(path=name, kind='directory', mode=0o755)
            else:
                row = dict(path=name, kind='file', mode=path.stat().st_mode & 0o777,
                           size=path.stat().st_size, sha256=deploy.digest(path))
            self.files.append(row)
        self.files.sort(key=lambda r: r['path'])
        self.row = dict(partition='4V100', prefix=self.prefix, image='image.sif', files=self.files)
        self.patch = patch.object(deploy, 'PACKAGE', self.package)
        self.patch.start(); self.addCleanup(self.patch.stop)

    def image(self):
        if not shutil.which('mksquashfs') or not shutil.which('unsquashfs'):
            self.skipTest('squashfs-tools required')
        subprocess.run(['mksquashfs', str(self.source), str(self.package / 'image.sif'),
                        '-processors', '1', '-noappend', '-quiet'], check=True, stdout=subprocess.DEVNULL)

    def test_real_extract_retains_library_link_and_potentials(self):
        self.image()
        destination = self.root / 'stage'; destination.mkdir()
        with patch.object(deploy, 'image_offset', return_value=0):
            deploy.extract(self.row, destination)
        self.assertEqual((destination / 'lib/liblammps.so').read_bytes(), b'shared library')
        self.assertEqual((destination / 'share/lammps/potentials/Si.tersoff').read_bytes(), b'potential file')

    def test_corrupt_image_file_is_rejected_before_link_creation(self):
        self.image()
        next(r for r in self.files if r['path'] == 'bin/lmp')['sha256'] = '0' * 64
        destination = self.root / 'stage'; destination.mkdir()
        with patch.object(deploy, 'image_offset', return_value=0), self.assertRaisesRegex(ValueError, 'payload differs'):
            deploy.extract(self.row, destination)
        self.assertFalse((destination / 'lib/liblammps.so').is_symlink())

    def test_existing_different_target_is_not_replaced(self):
        physical = self.root / 'physical'
        target = physical / self.prefix.lstrip('/')
        shutil.copytree(self.tree, target, symlinks=True)
        (target / 'bin/lmp').write_bytes(b'other installation')
        with patch.object(deploy, 'preflight'), self.assertRaises(ValueError):
            deploy.install(dict(deliveries=[self.row]), physical)
        self.assertEqual((target / 'bin/lmp').read_bytes(), b'other installation')
        self.assertFalse((physical / deploy.MODULE.relative_to('/')).exists())

    def test_symlink_destination_parent_is_rejected(self):
        target = self.root / 'outside'; target.mkdir()
        (self.root / 'redirect').symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            deploy.directories(self.root / 'redirect/bin')
        self.assertFalse((target / 'bin').exists())

    def test_omitted_workflow_file_cannot_be_a_retained_link_target(self):
        identity = dict(install_prefix=self.prefix)
        records = [dict(r, path=self.prefix.lstrip('/') + ('' if r['path'] == '.' else '/' + r['path'])) for r in self.files]
        records += [dict(path=self.prefix.lstrip('/') + '/share/sai', kind='directory', mode=0o755),
                    dict(path=self.prefix.lstrip('/') + '/share/sai/test', kind='file', mode=0o644, size=0, sha256='0'*64)]
        next(r for r in records if r['path'].endswith('/lib/liblammps.so'))['target'] = '../share/sai/test'
        with self.assertRaisesRegex(ValueError, 'pruning breaks'):
            deploy.runtime_records(dict(entry=dict(identity=identity), files=records), dict(prefix=self.prefix, identity=identity))

if __name__ == '__main__':
    unittest.main()
