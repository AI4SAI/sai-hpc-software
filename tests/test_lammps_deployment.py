"""Physical copy integrity and no-clobber checks using tiny synthetic payloads."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE_PATH = Path(__file__).resolve().parents[1] / 'deployment/lammps-30Sep2026/install.py'
SPEC = importlib.util.spec_from_file_location('lammps_install', MODULE_PATH)
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


class LammpsDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / 'project'
        self.physical = self.root / 'physical'
        self.package = self.root / 'package'
        for path in (self.project, self.physical, self.package):
            path.mkdir()
        self.patch = patch.object(deploy, 'PACKAGE', self.package)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        (self.package / deploy.MODULE.name).write_text('#%Module1.0\n')
        image = self.project / 'fixture.sif'
        image.write_bytes(b'fixture, not a scientific artifact')
        specifications, identities, installations = [], {}, []
        for software in ('deepmd-kit', 'lammps'):
            prefix = Path('/opt/apps') / software / 'fixture-release' / '4V100'
            identity = dict(software=software, partition='4V100', install_prefix=str(prefix))
            identities[software] = identity
            source = self.project / 'export' / software
            (source / 'bin').mkdir(parents=True)
            (source / 'share/sai').mkdir(parents=True)
            command = source / 'bin/program'
            command.write_bytes(b'#!/bin/sh\nexit 0\n')
            command.chmod(0o755)
            (source / 'bin/alias').symlink_to('program')
            # Runtime APIs/data must survive pruning, including the Python
            # environment configuration and distinct backend libraries.
            for name in ('lib/libapplication.so', 'include/application.h', 'lib/cmake/application.cmake',
                         'share/lammps/potentials/model.eam', 'pyvenv.cfg',
                         'lib/python3.13/site-packages/deepmd/__init__.py',
                         'lib/python3.13/site-packages/deepmd/lib/backend.so',
                         'lib/python3.13/site-packages/deepmd_kit-3.2.0.dist-info/METADATA',
                         'share/sai/native-module.tcl', 'share/sai/smoke/check.py',
                         'share/lammps/bench/in.lj', 'modulefiles/application', '.gitignore',
                         'bin/pip3', 'bin/activate', 'lib/python3.13/site-packages/pip/__init__.py',
                         'lib/python3.13/site-packages/pip-26.2.1.dist-info/METADATA',
                         'lib/python3.13/site-packages/deepmd/__pycache__/__init__.cpython-313.pyc'):
                path = source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture ' + name)
                path.chmod(0o644)
            rows = []
            for path in [source, *sorted(source.rglob('*'))]:
                relative = path.relative_to(source)
                row = dict(path=str((prefix / relative)).lstrip('/'))
                if path.is_symlink():
                    row.update(kind='symlink', mode=0o777, target=os.readlink(path))
                elif path.is_dir():
                    path.chmod(0o755)
                    row.update(kind='directory', mode=0o755)
                else:
                    row.update(kind='file', mode=path.stat().st_mode & 0o777,
                               sha256=deploy.digest(path), size=path.stat().st_size)
                rows.append(row)
            manifest = source / 'share/sai/manifest.json'
            manifest.write_text(json.dumps(dict(schema=2, entry=dict(identity=identity), files=rows)))
            (source / 'share/sai/export.json').write_text('{}\n')
            (source / 'share/sai/README-export.txt').write_text('fixture\n')
            metadata = {}
            for name in ('manifest.json', 'export.json', 'README-export.txt'):
                path = source / 'share/sai' / name
                path.chmod(0o444)
                metadata['share/sai/' + name] = deploy.digest(path)
            specifications.append(dict(prefix=str(prefix), identity=identity,
                source=str(source.relative_to(self.project)), manifest_sha256=deploy.digest(manifest),
                metadata_sha256=metadata))
            installations.append(dict(expected_install_prefix=str(prefix), path=software,
                                      manifest_sha256=deploy.digest(manifest)))
        publication = self.project / 'publication.json'
        publication.write_text(json.dumps(dict(published=True, build_verified=True, scientific_verified=True,
            performance_verified=True, performance_scope='gpu', performance_backends=['pt'],
            artifact=str(image), artifact_sha256=deploy.digest(image), delivery=dict(identities=identities),
            native_export=dict(directory=str(self.project / 'export'), receipt=dict(installations=installations)))))
        self.release = dict(deliveries=[dict(partition='4V100', publication='publication.json',
            publication_sha256=deploy.digest(publication), image='fixture.sif', image_sha256=deploy.digest(image),
            installations=specifications)])
        (self.package / 'release.json').write_text(json.dumps(self.release))
        plan = []
        for spec in specifications:
            manifest = json.loads((self.project / spec['source'] / 'share/sai/manifest.json').read_text())
            rows, removed = deploy.runtime_records(manifest, spec)
            plan.append(dict(prefix=spec['prefix'], identity=spec['identity'],
                             manifest_sha256=spec['manifest_sha256'], files=rows, omitted=removed))
        (self.package / 'runtime-plan.json').write_text(json.dumps(dict(
            schema=1, policy=deploy.POLICY, release_sha256=deploy.digest(self.package / 'release.json'),
            installations=plan)))

    def install(self):
        with contextlib.redirect_stdout(io.StringIO()):
            deploy.install(self.release, self.project, self.physical)

    def target(self, index=0):
        spec = self.release['deliveries'][0]['installations'][index]
        return self.physical / Path(spec['prefix']).relative_to('/')

    def test_install_and_repeat_preserve_pair_links_modes_and_publish_last(self):
        original_umask = os.umask(0o077)
        try:
            self.install()
            self.install()
        finally:
            os.umask(original_umask)
        for index in (0, 1):
            target = self.target(index)
            self.assertEqual(os.readlink(target / 'bin/alias'), 'program')
            self.assertEqual((target / 'bin/program').stat().st_mode & 0o777, 0o755)
        self.assertTrue((self.physical / deploy.MODULE.relative_to('/')).is_file())

    def test_changed_source_prevents_any_installation_or_module(self):
        spec = self.release['deliveries'][0]['installations'][1]
        (self.project / spec['source'] / 'bin/program').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'payload changed'):
            self.install()
        self.assertFalse(self.target().exists())
        self.assertFalse((self.physical / deploy.MODULE.relative_to('/')).exists())

    def test_runtime_only_copy_keeps_apis_data_and_python_without_source_metadata(self):
        self.install()
        for index in (0, 1):
            target = self.target(index)
            for name in ('share/sai', 'modulefiles', 'share/lammps/bench', '.gitignore', 'bin/pip3',
                         'bin/activate', 'lib/python3.13/site-packages/pip',
                         'lib/python3.13/site-packages/pip-26.2.1.dist-info',
                         'lib/python3.13/site-packages/deepmd/__pycache__'):
                self.assertFalse((target / name).exists(), name)
            for name in ('lib/libapplication.so', 'include/application.h', 'lib/cmake/application.cmake',
                         'share/lammps/potentials/model.eam', 'pyvenv.cfg',
                         'lib/python3.13/site-packages/deepmd/__init__.py',
                         'lib/python3.13/site-packages/deepmd/lib/backend.so',
                         'lib/python3.13/site-packages/deepmd_kit-3.2.0.dist-info/METADATA'):
                self.assertEqual((target / name).read_text(), 'fixture ' + name)
        self.project.rename(self.root / 'unavailable-source')
        with contextlib.redirect_stdout(io.StringIO()):
            deploy.preflight(self.release, self.project, self.physical, installed=True)
        self.assertFalse(list((self.physical / 'opt').rglob('*.lock')))

    def test_pruning_rejects_runtime_link_to_omitted_file(self):
        spec = self.release['deliveries'][0]['installations'][0]
        manifest = json.loads((self.project / spec['source'] / 'share/sai/manifest.json').read_text())
        manifest['files'].append(dict(path=spec['prefix'].lstrip('/') + '/bin/benchmark',
                                     kind='symlink', mode=0o777, target='../share/lammps/bench/in.lj'))
        with self.assertRaisesRegex(ValueError, 'pruning breaks a runtime link'):
            deploy.runtime_records(manifest, spec)

    def test_runtime_plan_cannot_silently_omit_a_library(self):
        path = self.package / 'runtime-plan.json'
        plan = json.loads(path.read_text())
        plan['installations'][0]['files'] = [r for r in plan['installations'][0]['files']
                                              if r['path'] != 'lib/libapplication.so']
        path.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, 'runtime plan differs'):
            self.install()
        self.assertFalse(self.target().exists())

    def test_installed_verification_rejects_added_nonstandard_file(self):
        self.install()
        (self.target() / 'custom-test.py').write_text('not a runtime file')
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, 'unlisted file'):
            deploy.preflight(self.release, self.project, self.physical, installed=True)

    def test_existing_different_target_is_preserved(self):
        self.install()
        command = self.target() / 'bin/program'
        command.write_bytes(b'keep this existing installation')
        with self.assertRaisesRegex(ValueError, 'payload changed'):
            self.install()
        self.assertEqual(command.read_bytes(), b'keep this existing installation')

    def test_symlink_ancestor_and_unlisted_files_are_rejected(self):
        spec = self.release['deliveries'][0]['installations'][0]
        source = self.project / spec['source']
        (source / 'unexpected').write_text('not in inventory')
        with self.assertRaisesRegex(ValueError, 'unlisted file'):
            deploy.verify_tree(source, spec)
        (self.physical / 'opt').symlink_to(self.project, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            deploy.plain(self.target())


if __name__ == '__main__':
    unittest.main()
