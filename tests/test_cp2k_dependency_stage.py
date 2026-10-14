from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import cp2k_dependency_stage as stage
from cp2k_spack_link import ROOTS
from release_contract import make_identity
from source_cache import checksum


class DependencyStageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.control = self.root / 'controller/frozen'
        self.control.mkdir(parents=True)
        self.run = self.root / 'runs/dependencies'
        self.run.mkdir(parents=True)
        self.results = self.run / 'results'
        self.results.mkdir()
        self.identity = make_identity('cp2k', 'development', 'master', 'a' * 40,
                                      '2026.2', 'b' * 64, '16v100-avx2')
        roots = {name: self.identity['install_prefix'] + '/dependencies/spack/linux-zen3/' + name + '-hash'
                 for name in sorted(ROOTS)}
        install = dict(partition='16V100', install_prefix=self.identity['install_prefix'],
                       dependencies_installed=True, cp2k_built=False, published=False,
                       roots=roots, runtime_prefixes=list(roots.values()),
                       nodes=[dict(name=name, prefix=path, external=False) for name, path in roots.items()])
        for name in stage.EVIDENCE:
            (self.results / name).write_text('{}')
        self.write('install-success.json', install)
        self.write('stage-identity.json', self.identity)
        for name in ('validated-graph.json', 'probe-success.json'):
            self.write(name, dict(partition='16V100', install_prefix=self.identity['install_prefix'],
                                 mirror_only=True, installed=False))
        (self.run / 'job.sbatch').write_text('#!/bin/bash\ntrue\n')
        (self.run / 'job.id').write_text('42\n')
        (self.run / 'work.ext3').write_bytes(b'synthetic-overlay-file-not-mounted')
        self.request = dict(purpose=stage.PURPOSE, identity=self.identity, controller=str(self.control),
                            job_id='42', script_sha256=checksum(self.run / 'job.sbatch'), overlay_mb=32768)
        (self.run / 'request.json').write_text(json.dumps(self.request))
        self.fingerprint = patch('software_controller.recipe_fingerprint', return_value='b' * 64).start()
        self.addCleanup(patch.stopall)
        self.accounting = patch.object(stage, 'accounting', return_value=('42', ['COMPLETED', '0:0'])).start()
        patch.object(stage, 'require_capacity').start()

    def write(self, name, data):
        (self.results / name).write_text(json.dumps(data))

    def seal(self):
        return stage.validate_completed(self.root, self.run, self.identity, self.control, seal=True)

    def test_seal_binds_image_script_and_raw_receipts(self):
        with self.assertRaisesRegex(ValueError, 'sealed'):
            stage.validate_completed(self.root, self.run, self.identity, self.control)
        sealed = self.seal()
        self.assertEqual(sealed['overlay_sha256'], checksum(self.run / 'work.ext3'))
        self.assertEqual(set(sealed['evidence_sha256']), set(stage.EVIDENCE))
        self.assertEqual(self.seal(), sealed)
        (self.results / 'spack.lock').write_text('changed-lock')
        with self.assertRaisesRegex(ValueError, 'changed'):
            stage.validate_completed(self.root, self.run, self.identity, self.control)

    def test_rejects_changed_identity_recipe_script_or_terminal_status(self):
        for state in (['RUNNING', '0:0'], ['FAILED', '1:0'], ['COMPLETED', '1:0'], None):
            self.accounting.return_value = ('42', state)
            with self.assertRaisesRegex(ValueError, 'terminal'):
                self.seal()
        self.accounting.return_value = ('42', ['COMPLETED', '0:0'])
        wrong = deepcopy(self.identity)
        wrong['source_sha'] = 'c' * 40
        with self.assertRaises(ValueError):
            stage.validate_completed(self.root, self.run, wrong, self.control, seal=True)
        self.fingerprint.return_value = 'c' * 64
        with self.assertRaisesRegex(ValueError, 'recipe'):
            self.seal()
        self.fingerprint.return_value = 'b' * 64
        (self.run / 'job.sbatch').write_text('# changed after submit\n')
        with self.assertRaisesRegex(ValueError, 'script changed'):
            self.seal()

    def test_rejects_relabelled_spack_graph_or_redirected_evidence(self):
        self.write('validated-graph.json', dict(partition='16V100', install_prefix='/opt/software/cp2k/old'))
        with self.assertRaisesRegex(ValueError, 'prefix'):
            self.seal()
        self.write('validated-graph.json', dict(partition='16V100', install_prefix=self.identity['install_prefix']))
        (self.results / 'spack.lock').unlink()
        (self.root / 'outside.lock').write_text('{}')
        (self.results / 'spack.lock').symlink_to(self.root / 'outside.lock')
        with self.assertRaisesRegex(ValueError, 'redirected'):
            self.seal()

    def test_clone_preserves_checkpoint_and_detects_later_mutation(self):
        receipt = self.seal()
        app = self.root / 'runs/application'
        (app / 'results').mkdir(parents=True)
        stage.clone(self.root, self.control, self.run.name, app.name, self.identity)
        self.assertEqual(checksum(app / 'work.ext3'), receipt['overlay_sha256'])
        self.assertEqual(checksum(self.run / 'work.ext3'), receipt['overlay_sha256'])
        self.assertEqual(json.loads((app / 'results/dependency-stage.json').read_text()), receipt)
        (app / 'work.ext3').write_bytes(b'application-mount-writes-only-this-clone')
        self.assertEqual(checksum(self.run / 'work.ext3'), receipt['overlay_sha256'])
        with self.assertRaisesRegex(ValueError, 'without an overlay'):
            stage.clone(self.root, self.control, self.run.name, app.name, self.identity)
        (self.run / 'work.ext3').chmod(0o600)
        (self.run / 'work.ext3').write_bytes(b'changed-checkpoint')
        other = self.root / 'runs/other'
        (other / 'results').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'changed'):
            stage.clone(self.root, self.control, self.run.name, other.name, self.identity)
        self.assertFalse((other / 'work.ext3').exists())

    def test_submission_records_identity_before_releasing_held_allocation(self):
        submitted = self.root / 'runs/fresh-dependencies'
        seen = []
        def execute(argv, **kwargs):
            seen.append(argv)
            if argv[0] == 'sbatch':
                self.assertIn('--hold', argv)
                request = json.loads((submitted / 'request.json').read_text())
                self.assertEqual(request['identity'], self.identity)
                self.assertEqual(request['script_sha256'], checksum(submitted / 'job.sbatch'))
                return subprocess.CompletedProcess(argv, 0, stdout='43;cluster\n')
            if argv[:2] == ['scontrol', 'release']:
                self.assertEqual((submitted / 'job.id').read_text(), '43\n')
                request = json.loads((submitted / 'request.json').read_text())
                self.assertEqual(request['job_id'], '43')
            return subprocess.CompletedProcess(argv, 0)
        with patch.object(stage.subprocess, 'run', side_effect=execute):
            stage.submit(self.root, self.control, submitted.name, self.identity)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                stage.submit(self.root, self.control, submitted.name, self.identity)
        self.assertEqual(seen[-1], ['scontrol', 'release', '43'])
        self.assertFalse((submitted / 'work.ext3').exists())

    def test_all_partitions_render_native_bounded_jobs(self):
        for target, partition in (('dsprhbm', 'DSPRHBM'), ('16v100-avx2', '16V100'),
                                  ('4v100-avx512', '4V100'), ('8v100v0-avx512', '8V100V0')):
            identity = make_identity('cp2k', 'development', 'master', 'a' * 40,
                                     '2026.2', 'b' * 64, target)
            script = stage.render(self.root, self.control, self.run, identity)
            subprocess.run(['bash', '-n'], input=script, text=True, check=True)
            self.assertIn('#SBATCH --partition=' + partition, script)
            self.assertIn('#SBATCH --time=240', script)
            self.assertIn('#SBATCH --dependency=singleton', script)
            self.assertLess(script.index('require_capacity('), script.index('cp --sparse'))
            self.assertIn(identity['install_prefix'], script)
            if target != 'dsprhbm':
                with self.assertRaisesRegex(ValueError, 'bounds'):
                    stage.render(self.root, self.control, self.run, identity, minutes=241)
                self.assertNotIn('--cpus-per-task=', script)
            else:
                self.assertIn('#SBATCH --time=720', stage.render(self.root, self.control, self.run, identity, minutes=720))


if __name__ == '__main__':
    unittest.main()
