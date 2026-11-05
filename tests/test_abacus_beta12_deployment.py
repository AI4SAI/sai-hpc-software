"""Reuse native-install integrity cases for the beta12 SIF delivery path."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import test_abacus_deployment as baseline

PACKAGE = Path(__file__).resolve().parents[1] / 'deployment/abacus-3.11.0-beta12'
SPEC = importlib.util.spec_from_file_location('abacus_beta12_install', PACKAGE / 'install.py')
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


class AbacusBeta12DeploymentTests(baseline.AbacusDeploymentTests):
    def setUp(self):
        binding = patch.object(baseline, 'deploy', deploy)
        binding.start(); self.addCleanup(binding.stop)
        super().setUp()
        origin = patch.object(deploy, 'ORIGIN', self.project)
        origin.start(); self.addCleanup(origin.stop)
        for delivery in self.release['deliveries']:
            publication_path = self.project / delivery['publication']
            publication = json.loads(publication_path.read_text())
            delivery['origin_image'] = publication['artifact']
            publication['native_export']['image_sha256'] = delivery['image_sha256']
            for value in publication.values():
                if isinstance(value, dict) and 'artifact_sha256' in value:
                    value['identity'] = publication['identity']
            publication_path.write_text(json.dumps(publication))
            delivery['publication_sha256'] = deploy.digest(publication_path)
            for spec in delivery['installations']:
                source = self.project / spec['source'] / 'share/sai/manifest.json'
                destination = self.project / 'public-manifest.json'
                shutil.copyfile(source, destination)
                spec['manifest'] = destination.name
        self.refresh_plan_hash()

    def refresh_plan_hash(self):
        (self.package / 'release.json').write_text(json.dumps(self.release))
        path = self.package / 'runtime-plan.json'
        plan = json.loads(path.read_text())
        plan['release_sha256'] = deploy.digest(self.package / 'release.json')
        path.write_text(json.dumps(plan))

    def extract(self, image, files, destination):
        self.assertFalse((self.physical / deploy.MODULE.relative_to('/')).exists())
        spec, = self.release['deliveries'][0]['installations']
        for row in files:
            source = self.project / spec['source'] / row['path']
            target = destination / row['path']
            shutil.copyfile(source, target)
            target.chmod(row['mode'])

    def install(self):
        with patch.object(deploy, 'extract_files', side_effect=self.extract), contextlib.redirect_stdout(io.StringIO()):
            deploy.install(self.release, self.project, self.physical)

    def test_symlink_ancestor_and_unlisted_files_are_rejected(self):
        (self.physical / 'opt').symlink_to(self.project, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.install()
        self.assertFalse((self.project / 'apps').exists())

    def test_changed_image_prevents_any_installation(self):
        (self.project / 'fixture.sif').write_bytes(b'changed SIF')
        with self.assertRaisesRegex(ValueError, 'SIF changed'):
            self.install()
        self.assertEqual(list(self.physical.iterdir()), [])

    def test_check_uses_public_evidence_without_restricted_native_source(self):
        (self.project / 'export').rename(self.root / 'restricted-export')
        with contextlib.redirect_stdout(io.StringIO()):
            deploy.preflight(self.release, self.project, self.physical)
        self.assertEqual(list(self.physical.iterdir()), [])

    def test_acceptance_for_a_different_image_or_identity_is_rejected(self):
        row = self.release['deliveries'][0]
        path = self.project / row['publication']
        publication = json.loads(path.read_text())
        publication['gpu_features']['identity'] = dict(partition='different')
        path.write_text(json.dumps(publication))
        row['publication_sha256'] = deploy.digest(path)
        self.refresh_plan_hash()
        with self.assertRaisesRegex(ValueError, 'incomplete acceptance'):
            self.install()
        self.assertEqual(list(self.physical.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
