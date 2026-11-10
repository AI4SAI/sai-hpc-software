"""Installed HDF5 reuse must fail before compiling against changed dependencies."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import devtools_install
import qe_site_hdf5 as site


class SiteHDF5Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.provider = self.root / 'provider'
        for directory in ('lib', 'include', 'share'):
            (self.provider / directory).mkdir(parents=True)
        for name in site.LIBRARIES:
            (self.provider / 'lib' / name).write_bytes(name.encode())
        (self.provider / 'include/hdf5.mod').write_bytes(b'compiler-specific module')
        (self.provider / 'share/COPYING').write_text('license')
        self.tools = self.root / 'bin'
        self.tools.mkdir()
        tools = {}
        for name in ('nvc', 'nvfortran', 'mpicc', 'mpifort'):
            path = self.tools / name
            path.write_text('#!/bin/sh\n'); path.chmod(0o755)
            tools[name] = dict(path=str(path), sha256=site.checksum(path))
        self.record = dict(schema=1, package=dict(prefix=str(self.provider), version='1.14.6',
                                                files=devtools_install.inventory(self.provider)),
                           toolchain=dict(source_sha256=site.HDF5_SHA256, mpi_interface='F90', mpi_f08=False,
                                          mpi_home='/fixture/mpi', tools=tools),
                           provenance=dict(manifest_sha256='a' * 64))
        for context in (patch.object(site, 'PREFIX', self.provider),
                        patch.dict(os.environ, PATH=str(self.tools), MPI_HOME='/fixture/mpi')):
            context.start(); self.addCleanup(context.stop)

    def test_prepare_checks_provider_and_exports_only_license_and_receipt(self):
        record = self.root / 'record.json'
        record.write_text(json.dumps(self.record))
        destination = self.root / 'application'
        with patch.object(site, 'RECORD', record):
            self.assertEqual(site.prepare(destination), self.provider)
        self.assertFalse((destination / 'lib').exists())
        self.assertEqual((destination / 'share/licenses/hdf5/COPYING').read_text(), 'license')
        self.assertTrue(json.loads((destination / 'share/sai/site-hdf5.json').read_text())['verified'])

    def test_changed_module_or_archive_is_rejected(self):
        for name in ('include/hdf5.mod', 'lib/libhdf5.a'):
            with self.subTest(name=name):
                path = self.provider / name
                original = path.read_bytes()
                path.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'differs from manifest'):
                    site.verify_provider(self.record)
                path.write_bytes(original)

    def test_changed_mpi_or_compiler_is_rejected(self):
        with patch.dict(os.environ, MPI_HOME='/another/mpi'):
            with self.assertRaisesRegex(ValueError, 'MPI provider differs'):
                site.verify_provider(self.record)
        (self.tools / 'nvfortran').write_text('changed executable')
        with self.assertRaisesRegex(ValueError, 'compiler/MPI tool changed'):
            site.verify_provider(self.record)

    def test_link_validation_rejects_unpinned_or_dynamic_hdf5(self):
        build = self.root / 'build'
        build.mkdir()
        link = build / 'link.txt'
        valid = 'nvfortran ' + ' '.join(str(self.provider / 'lib' / name) for name in sorted(site.LIBRARIES))
        link.write_text(valid)
        self.assertEqual(set(site.verify_links(build)['libraries']), site.LIBRARIES)
        for bad in (valid + ' -lhdf5', valid + ' /another/libhdf5.a',
                    valid + ' ' + str(self.provider / 'lib/libhdf5.so'), 'nvfortran -o pw.x'):
            with self.subTest(command=bad):
                link.write_text(bad)
                with self.assertRaises(ValueError):
                    site.verify_links(build)


if __name__ == '__main__':
    unittest.main()
