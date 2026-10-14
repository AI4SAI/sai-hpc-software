"""Cross-workflow regressions: failures must never become published deliveries."""
from pathlib import Path
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
from delivery_layout import check_performance, PerformanceRegression
from remote_controller import require_capacity
import ci
import cp2k_benchmark
import cp2k_spack
import release_contract


class ClosureGates(unittest.TestCase):
    def test_md_runtime_rejects_changed_image_and_identity(self):
        import md_controller as md
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / 'build.sif'
            image.write_bytes(b'verified image')
            delivery = {'fixture': 'paired identity'}
            status = dict(build_verified=True, state='COMPLETED', exit_code='0:0',
                          artifact=str(image), delivery=delivery, artifact_sha256=md.checksum(image))
            image.with_suffix('.json').write_text(json.dumps(status))
            with patch.object(md, 'validate_pair', return_value=delivery), \
                 patch.object(md, 'artifact_path', return_value=image):
                self.assertEqual(md.validate_runtime_image(image, delivery), image)
                image.write_bytes(b'replaced image')
                with self.assertRaisesRegex(ValueError, 'changed'):
                    md.validate_runtime_image(image, delivery)
                image.write_bytes(b'verified image')
                status['delivery'] = {'fixture': 'another identity'}
                image.with_suffix('.json').write_text(json.dumps(status))
                with self.assertRaisesRegex(ValueError, 'changed'):
                    md.validate_runtime_image(image, delivery)

    def test_performance_boundary_and_nonfinite_values(self):
        self.assertEqual(check_performance(110, 100), 1.1)
        with self.assertRaises(PerformanceRegression):
            check_performance(110.01, 100)
        for value in (0, -1, float('nan'), float('inf'), True):
            with self.assertRaises(ValueError):
                check_performance(value, 100)

    def test_capacity_uses_available_blocks_and_reserves_two_image_copies(self):
        with patch('os.statvfs', return_value=types.SimpleNamespace(f_bavail=17 * 1024, f_frsize=2**20)):
            with self.assertRaisesRegex(ValueError, '17 GiB available, 100 GiB required'):
                require_capacity('/site', 32768)
        with patch('os.statvfs', return_value=types.SimpleNamespace(f_bavail=101 * 1024, f_frsize=2**20)):
            self.assertEqual(require_capacity('/site', 32768)['required_bytes'], 100 * 2**30)

    def test_captured_remote_stderr_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, RUNNER_TEMP=tmp), \
             patch.object(ci.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['ssh'], stderr='schema mismatch')), \
             patch('sys.stderr', new_callable=io.StringIO) as stderr:
            with self.assertRaises(subprocess.CalledProcessError):
                ci.run(['ssh'], capture_output=True)
            self.assertIn('schema mismatch', stderr.getvalue())
            self.assertEqual((Path(tmp)/'results/command-errors.log').read_text(), 'schema mismatch\n')

    def test_mixed_layout_history_deduplicates_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = ('abacus', 'release', 'v3', 'a'*40, 'v3', 'b'*64, '16v100-avx2')
            old = release_contract.make_identity(*args, layout_schema=1)
            current = release_contract.make_identity(*args, layout_schema=2)
            release_contract.claim_build(tmp, [old], 'old')
            decision = release_contract.claim_build(tmp, [current], 'current')
            self.assertFalse(decision['build'])
            self.assertEqual(decision['skipped'][0]['previous_run'], 'old')
            self.assertTrue(release_contract.claim_build(tmp, [current], 'retry', retry=True)['build'])

    def test_gpu_reference_rechecks_and_ignores_stale_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(cp2k_benchmark, 'ROOT', Path(tmp)):
            for name in ('good','stale'):
                p=Path(tmp)/'runtime-tests'/name
                (p/'results').mkdir(parents=True)
                (p/'request.json').write_text(json.dumps({'software':'cp2k','gpus_per_node':1}))
                (p/'results/evidence.json').write_text('{}')
            def accepted(name):
                if name == 'stale': raise ValueError('raw data changed')
                return {}, 'a'*64
            with patch.object(cp2k_benchmark, 'accepted_reference', side_effect=accepted):
                self.assertEqual(cp2k_benchmark.latest_accepted_reference(), ('good','a'*64))
            with patch.object(cp2k_benchmark, 'accepted_reference', side_effect=ValueError('changed')):
                with self.assertRaisesRegex(ValueError, 'no compatible verified'):
                    cp2k_benchmark.latest_accepted_reference()

    def test_source_update_rejects_wrong_download_without_installing(self):
        with tempfile.TemporaryDirectory() as tmp, patch('urllib.request.urlopen') as fetch:
            fetch.return_value.__enter__.return_value.read.return_value = b'wrong source'
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                cp2k_spack.cache_source_updates(tmp)
            self.assertFalse((Path(tmp)/cp2k_spack.WIGNERNJ_ARCHIVE).exists())
