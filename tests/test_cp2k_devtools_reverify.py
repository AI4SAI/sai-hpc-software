from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import cp2k_devtools_stage as stage
from test_cp2k_devtools import provider_fixture


class ReverificationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.controller = self.root / 'controller/frozen'
        self.controller.mkdir(parents=True)
        (self.controller / 'recipe.py').write_text('original recipe\n')
        self.source = self.make_run('original', '101')
        self.stack.enter_context(patch.object(stage, 'validate_driver'))
        self.stack.enter_context(patch.object(stage, 'accounting', side_effect=lambda run:
            ('101', ['FAILED', '1:0']) if Path(run).name == 'original' else ('102', ['COMPLETED', '0:0'])))
        self.stack.enter_context(patch.object(stage.subprocess, 'check_output', return_value='4v100n10\n'))

    def make_run(self, name, job):
        run = self.root / 'runs' / name
        results = run / 'results'
        results.mkdir(parents=True)
        for name in stage.BUILD_EVIDENCE:
            (results / name).write_text('{}\n')
        (results / 'overlay-sync-dependencies.json').write_text(json.dumps(dict(synchronized=True)))
        (results / 'managed-overlay-clean.json').write_text(json.dumps(dict(
            completed=True, accepted=True, forced_stop=False, payload_returncode=0,
            unmount_returncode=0, driver_returncode=0)))
        (results / 'provider.json').write_text(json.dumps(provider_fixture()))
        (run / 'libraries.sif').write_bytes(b'original image fixture')
        (run / 'job.id').write_text(job)
        (run / 'job.sbatch').write_text('#!/bin/bash\ntrue\n')
        (run / 'request.json').write_text(json.dumps(dict(
            purpose=stage.PURPOSE, partition='16V100', controller=str(self.controller), job_id=job,
            controller_files=stage.recipe_files(self.controller), script_sha256=stage.checksum(run / 'job.sbatch'))))
        return run

    def test_running_or_unclean_source_cannot_be_reverified(self):
        with patch.object(stage, 'accounting', return_value=('101', ['RUNNING', '0:0'])):
            with self.assertRaisesRegex(ValueError, 'terminal'):
                stage.source_snapshot(self.root, 'original')
        lifecycle = self.source / 'results/managed-overlay-clean.json'
        record = json.loads(lifecycle.read_text())
        record.update(forced_stop=True)
        lifecycle.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'teardown'):
            stage.source_snapshot(self.root, 'original')

    def test_original_failure_is_retained_and_changed_images_or_evidence_are_rejected(self):
        source = stage.source_snapshot(self.root, 'original')
        self.assertEqual(source['state'], ['FAILED', '1:0'])
        run = self.make_run('verification', '102')
        for name in source['evidence_sha256']:
            shutil.copyfile(self.source / 'results' / name, run / 'results' / name)
        request = json.loads((run / 'request.json').read_text())
        request['source_build'] = source
        (run / 'request.json').write_text(json.dumps(request))
        (run / 'results/sif-consumer').mkdir()
        (run / 'results/sif-consumer/passed.json').write_text(json.dumps(dict(
            contract_sha256=provider_fixture()['contract_sha256'])))
        (run / 'results/artifact.sha256').write_text(stage.checksum(run / 'libraries.sif') + '\n')
        stage.accepted(self.root, 'verification')
        with self.assertRaisesRegex(ValueError, 'not successfully completed'):
            stage.accepted(self.root, 'original')
        for path, message in (
            (self.source / 'libraries.sif', 'Original SIF'),
            (run / 'libraries.sif', 'different SIF'),
            (run / 'results/spack.lock', 'Copied build evidence'),
        ):
            original = path.read_bytes()
            path.write_bytes(original + b'changed')
            with self.assertRaisesRegex(ValueError, message):
                stage.accepted(self.root, 'verification')
            path.write_bytes(original)
        self.assertEqual(stage.accounting(self.source), ('101', ['FAILED', '1:0']))

    def test_verification_script_uses_original_node_and_readonly_image_without_overlay(self):
        source = stage.source_snapshot(self.root, 'original')
        run = self.root / 'runs/verification'
        # Use the real shell syntax check, independently of mocked Slurm output.
        script = stage.render_reverify(self.root, self.controller, run, source)
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)
        self.assertIn('#SBATCH --nodelist=4v100n10', script)
        self.assertIn(source['sif_sha256'], script)
        self.assertIn('cp2k_devtools_entry.sh verify 16V100', script)
        self.assertNotIn('work.ext3', script)
        self.assertNotIn('cp2k_devtools_entry.sh build', script)


if __name__ == '__main__':
    unittest.main()
