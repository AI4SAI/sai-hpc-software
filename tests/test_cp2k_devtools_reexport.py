import json
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

import test_cp2k_devtools_reverify as reverify_tests
import cp2k_devtools_reexport as recovery
import cp2k_devtools_stage as stage
from cp2k_image_driver import APPTAINER


class ExportRecoveryTests(reverify_tests.ReverificationTests):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.object(stage, 'accounting', side_effect=lambda run:
            ('101', ['FAILED', '74:0']) if Path(run).name == 'original' else ('102', ['COMPLETED', '0:0'])))
        (self.source / 'libraries.sif').unlink()
        (self.source / 'work.ext3').write_bytes(b'clean compiled dependency overlay')
        self.digest = stage.checksum(self.source / 'work.ext3')
        expected = stage.library_container(self.root / 'containers/base/minimal-v1.sif',
            ['/usr/bin/cat', '/workspace/final.squashfs'], overlay=str(self.source / 'work.ext3') + ':ro', jobs=8)
        failure = dict(argv=[str(APPTAINER), '--config', str(self.source / 'runtime/apptainer-fuse2fs.conf'), *expected[1:]],
            returncode=0, reason='image driver teardown failed after payload success',
            stderr_tail='INFO: Terminating fuse2fs after timeout\n')
        self.failure = self.source / 'results/driver-cleanup-failure-1.json'
        self.failure.write_text(json.dumps(failure))

    # Reuse fixture setup only, not the tests for a different recovery contract.
    test_running_or_unclean_source_cannot_be_reverified = None
    test_original_failure_is_retained_and_changed_images_or_evidence_are_rejected = None
    test_verification_script_uses_original_node_and_readonly_image_without_overlay = None

    def test_only_exact_readonly_export_failure_can_be_recovered(self):
        source = recovery.export_snapshot(self.root, 'original', self.digest)
        self.assertEqual(source['state'], ['FAILED', '74:0'])
        with self.assertRaises(ValueError):
            stage.source_snapshot(self.root, 'original')
        original = self.failure.read_bytes()
        for mutate in (
            lambda r: r.update(returncode=1),
            lambda r: r.update(stderr_tail='Cleanup error: unrelated'),
            lambda r: r['argv'].__setitem__(-1, '/another-file'),
            lambda r: r['argv'].__setitem__(r['argv'].index('--overlay') + 1, str(self.source / 'work.ext3')),
        ):
            row = json.loads(original)
            mutate(row)
            self.failure.write_text(json.dumps(row))
            with self.assertRaisesRegex(ValueError, 'known read-only'):
                recovery.export_snapshot(self.root, 'original', self.digest)
        self.failure.write_bytes(original)
        lifecycle = self.source / 'results/managed-overlay-clean.json'
        row = json.loads(lifecycle.read_text()); row['forced_stop'] = True
        lifecycle.write_text(json.dumps(row))
        with self.assertRaisesRegex(ValueError, 'teardown'):
            recovery.export_snapshot(self.root, 'original', self.digest)

    def test_recovery_reexports_a_sealed_copy_on_selected_node_without_compilation(self):
        source = recovery.export_snapshot(self.root, 'original', self.digest)
        run = self.root / 'runs/new-export'
        script = recovery.render(self.root, self.controller, run, source, 'healthy-node')
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)
        self.assertIn('#SBATCH --nodelist=healthy-node', script)
        self.assertIn('chmod 400 ' + str(run / 'work.ext3'), script)
        self.assertIn(str(run / 'work.ext3') + ':ro', script)
        self.assertIn('e2fsck -fn', script)
        self.assertIn('cp2k_devtools_entry.sh verify', script)
        self.assertNotIn('cp2k_devtools_entry.sh build', script)
        self.assertNotIn('resize2fs', script)
        self.assertNotIn('libraries.squashfs ' + str(self.source), script)

    def test_acceptance_requires_fresh_clean_export_and_unchanged_provenance(self):
        source = recovery.export_snapshot(self.root, 'original', self.digest)
        run = self.make_run('new-export', '102')
        for name in source['evidence_sha256']:
            shutil.copyfile(self.source / 'results' / name, run / 'results' / name)
        shutil.copyfile(self.source / 'work.ext3', run / 'work.ext3')
        request = json.loads((run / 'request.json').read_text())
        request['source_export'] = source
        (run / 'request.json').write_text(json.dumps(request))
        results = run / 'results'
        (results / 'reexport-fsck.log').write_text('clean\n')
        (results / 'sif-consumer').mkdir()
        provider = stage.read_json(results / 'provider.json')
        (results / 'sif-consumer/passed.json').write_text(json.dumps(dict(contract_sha256=provider['contract_sha256'])))
        (results / 'artifact.sha256').write_text(stage.checksum(run / 'libraries.sif') + '\n')
        with self.assertRaisesRegex(ValueError, 'exactly one new'):
            recovery.record(self.root, run, request)
        lifecycle = results / 'managed-overlay-new.json'
        row = dict(completed=True, accepted=True, readonly=True, forced_stop=False,
                   payload_returncode=0, unmount_returncode=0, driver_returncode=0,
                   overlay=str(run / 'work.ext3'))
        lifecycle.write_text(json.dumps(row))
        recovery.record(self.root, run, request)
        stage.accepted(self.root, 'new-export')
        with self.assertRaisesRegex(ValueError, 'not successfully completed'):
            stage.accepted(self.root, 'original')
        row['readonly'] = False
        lifecycle.write_text(json.dumps(row))
        with self.assertRaisesRegex(ValueError, 'read-only export'):
            stage.accepted(self.root, 'new-export')
        row['readonly'] = True
        lifecycle.write_text(json.dumps(row))
        (self.source / 'work.ext3').write_bytes(b'changed source')
        with self.assertRaisesRegex(ValueError, 'source or evidence changed'):
            stage.accepted(self.root, 'new-export')


if __name__ == '__main__':
    unittest.main()
