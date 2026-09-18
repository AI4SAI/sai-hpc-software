import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import cp2k_spack_native as native
import cp2k_spack_probe_submit as probe


class SpackProbeTests(unittest.TestCase):
    def test_debian_repack_suffix_is_not_a_different_upstream_version(self):
        cases = {'1:1.3.dfsg-3.1ubuntu2.1': '1.3', '2:6.3.0+dfsg-2ubuntu6.1': '6.3.0',
                 '2.7.1+dfsg-6ubuntu2': '2.7.1', '1:1.16.5-1.3ubuntu1': '1.16.5',
                 '1.3.1-3ubuntu1': '1.3.1', '2.0+feature-1': '2.0+feature'}
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(native.upstream_debian_version(value), expected)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'runs').mkdir()
        image = self.root / 'containers/base/minimal-v1.sif'
        image.parent.mkdir(parents=True)
        image.write_bytes(b'test-image')
        path_patch = patch.object(probe, 'Path', side_effect=lambda value:
                                 self.root if str(value) == '/home/stardust/sai-hpc-software' else Path(value))
        path_patch.start()
        self.addCleanup(path_patch.stop)
        cache = patch.object(probe, 'validate_cache')
        cache.start()
        self.addCleanup(cache.stop)

    def submit(self, partition='16V100', run_id='test', **kwargs):
        with patch.object(probe.subprocess, 'check_output', return_value='1234\n'), \
                patch.object(probe.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            probe.submit(partition, run_id, **kwargs)
        run.assert_called_once_with(['scontrol', 'release', '1234'], check=True)
        return self.root / 'runs' / run_id

    def test_probe_is_offline_overlay_only_and_records_release(self):
        result = self.submit()
        text = (result / 'job.sbatch').read_text()
        for expected in ('--network none', '--overlay', '/input/spack:ro', '/results:rw',
                         '/var/lib/dpkg:ro', '/etc/alternatives:ro', '--gpus-per-node=1',
                         '--time=15', 'cp2k_spack_probe.sh 16V100'):
            self.assertIn(expected, text)
        self.assertNotIn('/opt/apps', text)
        self.assertNotIn('/workspace:rw', text)
        receipt = json.loads((result / 'request.json').read_text())
        self.assertEqual(receipt['status'], 'released')
        self.assertEqual(receipt['retry_policy']['diagnosed_repairs_used'], 0)
        self.assertEqual(receipt['job_id'], '1234')

    def test_cpu_probe_requests_no_gpu(self):
        result = self.submit('DSPRHBM')
        text = (result / 'job.sbatch').read_text()
        self.assertIn('--qos=rush-cpu', text)
        self.assertIn('--cpus-per-task=8', text)
        self.assertNotIn('--gpus-per-node', text)
        self.assertNotIn('--nv ', text)

    def test_duplicate_run_never_submits(self):
        self.submit()
        with patch.object(probe.subprocess, 'check_output') as sbatch:
            with self.assertRaises(FileExistsError):
                probe.submit('16V100', 'test')
        sbatch.assert_not_called()

    def parent(self, repairs=0):
        parent = self.root / 'runs' / 'parent'
        parent.mkdir()
        (parent / 'request.json').write_text(json.dumps({
            'partition': '16V100', 'job_id': '123',
            'retry_policy': {'diagnosed_repairs_used': repairs}}))
        return parent

    def test_repair_requires_diagnosis_terminal_parent_and_budget(self):
        parent = self.parent(2)
        with patch.object(probe.subprocess, 'check_output') as check:
            with self.assertRaisesRegex(ValueError, 'diagnosis'):
                probe.submit('16V100', 'new', 'parent')
            with self.assertRaisesRegex(ValueError, 'exhausted'):
                probe.submit('16V100', 'new', 'parent', 'verified cause')
        check.assert_not_called()
        (parent / 'request.json').write_text(json.dumps({
            'partition': '16V100', 'job_id': '123', 'retry_policy': {}}))
        with patch.object(probe.subprocess, 'check_output', return_value='123|RUNNING|\n'):
            with self.assertRaisesRegex(ValueError, 'terminal'):
                probe.submit('16V100', 'new', 'parent', 'verified cause')
        self.assertFalse((self.root / 'runs/new').exists())

    def test_repair_records_lineage_and_refuses_a_second_child(self):
        parent = self.parent()
        with patch.object(probe.subprocess, 'check_output', side_effect=['123|FAILED|\n', '1234\n']), \
                patch.object(probe.subprocess, 'run'), contextlib.redirect_stdout(io.StringIO()):
            probe.submit('16V100', 'fixed', 'parent', 'missing read-only metadata mount')
        receipt = json.loads((self.root / 'runs/fixed/request.json').read_text())
        self.assertEqual(receipt['retry_policy']['diagnosed_repairs_used'], 1)
        self.assertEqual(receipt['repair_of'], 'parent')
        self.assertTrue((parent / 'repair-child.json').is_file())
        with patch.object(probe.subprocess, 'check_output', return_value='123|FAILED|\n'):
            with self.assertRaisesRegex(ValueError, 'already'):
                probe.submit('16V100', 'duplicate', 'parent', 'same cause')

    def test_offline_solver_wheels_are_pinned(self):
        self.assertEqual(len(native.WHEELS), 3)
        for name, digest in native.WHEELS.items():
            self.assertTrue(name.endswith('.whl'))
            self.assertRegex(digest, r'^[0-9a-f]{64}$')
        script = (Path(__file__).resolve().parents[1] / 'controller/cp2k_spack_probe.sh').read_text()
        self.assertIn('PYTHONNOUSERSITE=1', script)
        self.assertNotIn('spack_run install', script)


if __name__ == '__main__':
    unittest.main()
