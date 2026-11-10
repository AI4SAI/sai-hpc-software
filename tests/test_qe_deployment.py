"""QE admin deployment preserves accepted bytes and publishes the module last."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1] / 'deployment/qe-7.6'
spec = importlib.util.spec_from_file_location('qe_deployment', PACKAGE / 'install.py')
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class QEDeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.package, self.physical = self.root / 'package', self.root / 'physical'
        self.package.mkdir(); self.physical.mkdir()
        self.patch = patch.object(deployment, 'PACKAGE', self.package)
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.module = (PACKAGE / '7.6-auto').read_bytes()
        (self.package / '7.6-auto').write_bytes(self.module)
        self.payloads, rows = {}, []
        commands = {name + '.x': 'bin/' + name + '.x'
                    for name in ['pw', 'ph', 'pp', *('tool' + str(i) for i in range(64))]}
        for partition in sorted(deployment.PARTITIONS):
            build_id = 'qe-7.6-g9f93ddec427d-r' + 'b' * 12
            prefix = '/opt/apps/qe/qe-release-' + build_id + '/' + partition
            identity = dict(software='qe', track='release', source_sha=deployment.SOURCE,
                            source_version='qe-7.6', partition=partition, build_id=build_id, install_prefix=prefix)
            manifest = dict(entry=dict(identity=identity, commands=commands), files=[])
            for name in [*commands.values(), 'share/licenses/qe/License']:
                data = (partition + ':' + name).encode()
                source = prefix.lstrip('/') + '/' + name
                mode = 0o555 if name.startswith('bin/') else 0o444
                manifest['files'].append(dict(path=source, kind='file', size=len(data), sha256=sha(data), mode=mode))
                self.payloads[source] = data
            if partition == 'DSPRHBM':
                source = prefix.lstrip('/') + '/deps/hdf5/share/COPYING'
                data = b'HDF5 license'
                manifest['files'].append(dict(path=source, kind='file', size=len(data), sha256=sha(data), mode=0o644))
                self.payloads[source] = data
                license = dict(path='share/licenses/hdf5/COPYING', source=source,
                               image_partition='DSPRHBM', size=len(data), sha256=sha(data), mode=0o444)
            image = self.package / (partition + '.sif')
            image.write_bytes(b'accepted-' + partition.encode()); image.chmod(0o444)
            manifest_path = self.package / (partition + '-manifest.json')
            manifest_path.write_text(json.dumps(manifest))
            record = dict(published=True, verified=True, build_verified=True, identity=identity,
                          artifact='/project/' + image.name, sha256=deployment.digest(image),
                          qe_science=dict(verified=True, artifact_sha256=deployment.digest(image),
                                          identity=identity, checks=dict(gpu_executed=partition != 'DSPRHBM')),
                          native_export=dict(roundtrip_verified=True, image_sha256=deployment.digest(image),
                                             receipt=dict(installations=[dict(manifest_sha256=deployment.digest(manifest_path))])))
            publication = self.package / (partition + '-publication.json')
            publication.write_text(json.dumps(record))
            rows.append(dict(partition=partition, identity=identity, prefix=prefix, origin_image=record['artifact'],
                             image=image.name, image_sha256=deployment.digest(image),
                             publication=publication.name, publication_sha256=deployment.digest(publication),
                             manifest=manifest_path.name, manifest_sha256=deployment.digest(manifest_path),
                             files=deployment.runtime_records(manifest, prefix.lstrip('/'))))
        for row in rows:
            row['files'] = sorted([*row['files'], license], key=lambda entry: entry['path'])
        self.release = dict(schema=1, software='qe', version='qe-7.6', module_sha256=sha(self.module),
                            hdf5_license=license, deliveries=rows)
        (self.package / 'release.json').write_text(json.dumps(self.release))

    def extract(self, image, files, destination):
        self.assertFalse((self.physical / deployment.MODULE.relative_to('/')).exists())
        for record in files:
            path = destination / record['path']
            deployment.directories(path.parent)
            path.write_bytes(self.payloads[record['source']]); path.chmod(record['mode'])

    def install(self):
        with patch.object(deployment, 'extract_files', side_effect=self.extract), contextlib.redirect_stdout(io.StringIO()):
            deployment.install(deployment.load_release(), self.package, self.physical)

    def test_install_repeat_and_verify_without_images(self):
        self.install()
        self.install()
        for row in self.release['deliveries']:
            target = self.physical / Path(row['prefix']).relative_to('/')
            self.assertEqual({p.name for p in target.iterdir()}, {'bin', 'share'})
            self.assertFalse((target / 'share/sai').exists())
            (self.package / row['image']).unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            deployment.preflight(self.release, self.package, self.physical, installed=True)
        self.assertEqual((self.physical / deployment.MODULE.relative_to('/')).read_bytes(), self.module)

    def test_site_hdf5_license_is_selected_once_and_legacy_layout_still_works(self):
        row = next(row for row in self.release['deliveries'] if row['partition'] == 'DSPRHBM')
        manifest = json.loads((self.package / row['manifest']).read_text())
        self.assertEqual(deployment.hdf5_license(manifest), self.release['hdf5_license'])
        original = next(record for record in manifest['files'] if record['path'].endswith('/deps/hdf5/share/COPYING'))
        original['path'] = row['prefix'].lstrip('/') + '/share/licenses/hdf5/COPYING'
        original['mode'] = 0o444
        license = deployment.hdf5_license(manifest)
        records = deployment.runtime_records(manifest, row['prefix'].lstrip('/'))
        selected = deployment.with_hdf5_license(records, license)
        self.assertEqual(sum(record['path'] == license['path'] for record in selected), 1)
        self.assertEqual(deployment.with_hdf5_license(selected, license), selected)
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            deployment.with_hdf5_license(records, dict(license, sha256='0' * 64))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            deployment.with_hdf5_license(records + [records[0]], license)

    def test_changed_image_fails_before_any_installation(self):
        image = self.package / self.release['deliveries'][-1]['image']
        image.chmod(0o644); image.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'SIF changed'):
            self.install()
        self.assertEqual(list(self.physical.iterdir()), [])

    def test_corrupt_extraction_cannot_publish_module(self):
        self.payloads[next(iter(self.payloads))] = b'corrupt'
        with self.assertRaisesRegex(ValueError, 'payload changed'):
            self.install()
        self.assertFalse((self.physical / deployment.MODULE.relative_to('/')).exists())

    def test_existing_different_files_are_preserved(self):
        row = self.release['deliveries'][0]
        target = self.physical / Path(row['prefix']).relative_to('/')
        target.mkdir(parents=True)
        (target / 'keep').write_text('existing user data')
        with self.assertRaisesRegex(ValueError, 'unlisted installed'):
            self.install()
        self.assertEqual((target / 'keep').read_text(), 'existing user data')

    def test_modified_selection_and_symlink_ancestor_are_rejected(self):
        self.release['deliveries'][0]['files'].pop(0)
        (self.package / 'release.json').write_text(json.dumps(self.release))
        with self.assertRaisesRegex(ValueError, 'selection changed'):
            self.install()
        target = self.root / 'outside'
        target.mkdir()
        (self.physical / 'opt').symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            deployment.directories(self.physical / 'opt/apps/qe')

    def test_existing_release_gains_only_standard_links_without_images_or_recopy(self):
        self.install()
        frozen_release = (self.package / 'release.json').read_bytes()
        before = {}
        for row in self.release['deliveries']:
            root = self.physical / Path(row['prefix']).relative_to('/')
            binary = root / 'bin/pw.x'
            before[str(binary)] = (binary.read_bytes(), binary.stat().st_ino, binary.stat().st_mtime_ns)
            for name in deployment.COMMAND_ALIASES:
                (root / name).unlink()
            (self.package / row['image']).unlink()
        with patch.object(deployment, 'extract_files', side_effect=AssertionError('must not extract')), \
                contextlib.redirect_stdout(io.StringIO()):
            deployment.complete_commands(deployment.load_release(), self.physical)
            deployment.complete_commands(deployment.load_release(), self.physical)
            deployment.preflight(self.release, self.package, self.physical, installed=True)
        self.assertEqual((self.package / 'release.json').read_bytes(), frozen_release)
        for row in self.release['deliveries']:
            root = self.physical / Path(row['prefix']).relative_to('/')
            binary = root / 'bin/pw.x'
            self.assertEqual((binary.read_bytes(), binary.stat().st_ino, binary.stat().st_mtime_ns), before[str(binary)])
            for name in deployment.COMMAND_ALIASES:
                self.assertEqual(os.readlink(root / name), 'pw.x')

    def test_completion_rejects_changed_or_redirected_alias_before_creating_any_links(self):
        self.install()
        roots = [self.physical / Path(row['prefix']).relative_to('/') for row in self.release['deliveries']]
        for root in roots:
            for name in deployment.COMMAND_ALIASES:
                (root / name).unlink()
        collision = roots[-1] / 'bin/manypw.x'
        for target in ('../pw.x', str(roots[-1] / 'bin/pw.x'), '/missing/outside'):
            with self.subTest(target=target):
                collision.symlink_to(target)
                with self.assertRaisesRegex(ValueError, 'command link differs'), contextlib.redirect_stdout(io.StringIO()):
                    deployment.complete_commands(deployment.load_release(), self.physical)
                self.assertFalse((roots[0] / 'bin/manypw.x').exists())
                self.assertEqual(os.readlink(collision), target)
                collision.unlink()
        collision.write_text('administrator file')
        with self.assertRaisesRegex(ValueError, 'replaced by a file'), contextlib.redirect_stdout(io.StringIO()):
            deployment.complete_commands(deployment.load_release(), self.physical)
        self.assertEqual(collision.read_text(), 'administrator file')

    def test_native_inventory_accepts_only_the_exact_relative_manypw_link(self):
        row = self.release['deliveries'][0]
        manifest = json.loads((self.package / row['manifest']).read_text())
        prefix = row['prefix'].lstrip('/')
        for path, target in deployment.COMMAND_ALIASES.items():
            manifest['entry']['commands'][Path(path).name] = path
            manifest['files'].append(dict(path=prefix + '/' + path, kind='symlink', target=target, mode=0o755))
        records = deployment.runtime_records(manifest, prefix)
        self.assertEqual(sum(r.get('kind') == 'symlink' for r in records), 1)
        self.assertEqual(sum(r['path'].startswith('bin/') for r in records), 68)
        manifest['files'][-1]['target'] = '/opt/other/pw.x'
        with self.assertRaisesRegex(ValueError, 'command link'):
            deployment.runtime_records(manifest, prefix)

    @unittest.skipUnless(shutil.which('mksquashfs') and shutil.which('unsquashfs'), 'squashfs tools unavailable')
    def test_real_squashfs_content_extraction_and_hash_guard(self):
        source = self.root / 'source'
        (source / 'bin').mkdir(parents=True)
        records = []
        for name, data in [('pw.x', b'pw' * 12000), ('ph.x', b'phonon')]:
            (source / 'bin' / name).write_bytes(data)
            records.append(dict(path='bin/' + name, source='bin/' + name, size=len(data), sha256=sha(data), mode=0o555))
        image = self.root / 'payload.squashfs'
        subprocess.run(['mksquashfs', str(source), str(image), '-noappend', '-no-progress', '-processors', '1'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        with patch.object(deployment, 'image_offset', return_value=0):
            deployment.extract_files(image, records, self.physical)
            deployment.verify_installed(self.physical, records)
            records[0]['sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'SIF file changed'):
                deployment.extract_files(image, records, self.root / 'bad')


if __name__ == '__main__':
    unittest.main()
