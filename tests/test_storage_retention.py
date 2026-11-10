"""Manual cleanup preserves images/proofs and refuses changed or live builds."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import storage_retention as storage


class StorageRetentionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.beta = self.root / 'beta'
        self.beta.mkdir()
        self.run = self.root / 'experimental/deepmd-lammps/runs/accepted-build'
        (self.run / 'results').mkdir(parents=True)
        self.delivery = dict(identity=dict(software='lammps'))
        (self.run / 'request.json').write_text(json.dumps(dict(delivery=self.delivery)))
        (self.run / 'job.id').write_text('123\n')
        self.image = self.run / 'candidate.sif'
        self.image.write_bytes(b'accepted SIF'); self.image.chmod(0o444)
        self.status = self.run / 'results/status.json'
        self.status.write_text(json.dumps(dict(delivery=self.delivery, job='123', state='COMPLETED',
                                              exit_code='0:0', build_verified=True,
                                              artifact_sha256=storage.digest(self.image))))
        os.utime(self.status, (time.time()-2*86400, time.time()-2*86400))
        for name in storage.SCRATCH:
            (self.run / name).write_bytes(b'disposable')
        (self.run / 'results/science.json').write_text('retained evidence')
        self.plan = storage.plan(self.root, self.beta)

    @staticmethod
    def scheduler(command):
        return '' if command[0] == 'squeue' else '123|COMPLETED|0:0\n'

    def test_manual_apply_removes_only_reviewed_scratch_and_preserves_evidence(self):
        self.assertEqual(self.plan['candidate_inodes'], 2)
        with patch.object(storage, 'command', side_effect=self.scheduler):
            storage.apply(self.plan)
        self.assertEqual(self.image.read_bytes(), b'accepted SIF')
        self.assertTrue(self.status.exists())
        self.assertTrue((self.run / 'results/science.json').exists())
        self.assertFalse(any((self.run / name).exists() for name in storage.SCRATCH))

    def test_changed_file_blocks_whole_batch_before_deletion(self):
        (self.run / 'work.ext3').write_bytes(b'changed since review')
        with patch.object(storage, 'command', side_effect=self.scheduler):
            with self.assertRaisesRegex(ValueError, 'scratch file changed'):
                storage.apply(self.plan)
        self.assertTrue((self.run / 'final.squashfs').exists())

    def test_changed_sif_pin_and_active_job_block_cleanup(self):
        with patch.object(storage, 'command', return_value='123\n'):
            with self.assertRaisesRegex(ValueError, 'active or queued'):
                storage.apply(self.plan)
        (self.run / '.keep-build').touch()
        with patch.object(storage, 'command', side_effect=self.scheduler):
            with self.assertRaisesRegex(ValueError, 'pinned'):
                storage.apply(self.plan)
        (self.run / '.keep-build').unlink()
        self.image.chmod(0o644); self.image.write_bytes(b'corrupt'); self.image.chmod(0o444)
        with patch.object(storage, 'command', side_effect=self.scheduler):
            with self.assertRaisesRegex(ValueError, 'build proof changed'):
                storage.apply(self.plan)
        self.assertTrue(all((self.run / name).exists() for name in storage.SCRATCH))

    def test_sif_path_symlink_and_duplicate_cannot_become_cleanup_targets(self):
        changed = copy.deepcopy(self.plan)
        changed['candidates'][0]['files'][0]['path'] = str(self.image)
        with self.assertRaisesRegex(ValueError, 'not a disposable'):
            storage.validate_plan(changed)
        changed = copy.deepcopy(self.plan)
        changed['candidates'] *= 2
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            storage.validate_plan(changed)
        path = self.run / 'work.ext3'
        path.unlink(); path.symlink_to(self.image)
        with self.assertRaisesRegex(ValueError, 'redirected'):
            storage.validate_plan(self.plan)

    def test_failed_and_recent_builds_stay_out_of_plan(self):
        record = json.loads(self.status.read_text())
        record['build_verified'] = False
        self.status.write_text(json.dumps(record))
        self.assertFalse(storage.plan(self.root, self.beta)['candidates'])
        record['build_verified'] = True
        self.status.write_text(json.dumps(record))
        self.assertFalse(storage.plan(self.root, self.beta)['candidates'])

    def test_beta_retention_protects_current_pins_recent_and_distinct_builds(self):
        now = time.time()
        modules = self.root / 'modules/lammps'
        modules.mkdir(parents=True)
        for number in range(6):
            generation = 'g' + str(number)
            directory = self.beta / 'generations' / generation
            directory.mkdir(parents=True)
            bundle = self.beta / 'runtimes' / generation
            bundle.mkdir(parents=True)
            image = bundle / 'lammps.sif'; image.write_text(generation)
            record = dict(image=str(image), identity=dict(source_sha=str(number), recipe_sha256='r',
                          source_ref='develop', track='development'))
            catalog = bundle / 'catalog.json'
            catalog.write_text(json.dumps(dict(applications={'lammps': dict(partitions={'16V100': record})})))
            manifest = directory / 'manifest.json'
            manifest.write_text(json.dumps(dict(bundle=str(bundle), catalog_sha256=storage.digest(catalog),
                                      module_root=str(modules.parent), versions={'lammps': dict(name=generation)})))
            os.utime(manifest, (now-(10+number)*86400, now-(10+number)*86400))
            (modules / generation).write_text(generation)
        (modules / 'develop-beta-later').symlink_to('g3')
        (self.beta / 'storage-pins.json').write_text('["g4"]')
        result = storage.beta_retention(self.beta, now)
        self.assertFalse(result['automatic_deletion'])
        self.assertEqual([row['generation'] for row in result['generations'] if row['review_only']], ['g5'])
        self.assertFalse(result['errors'])


if __name__ == '__main__':
    unittest.main()
