import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
import md_controller as md
import md_tracking as tracking


class MDControllerTests(unittest.TestCase):
    def pair(self, target='4v100-avx512'):
        def resolver(repo, ref):
            sha = ('a' if 'deepmd' in repo else 'b') * 40
            return {'sha': sha, 'ref': ref, 'version': ref + '-' + sha[:12]}
        return tracking.pair('master', 'develop', target, resolver)

    def test_both_live_sources_are_resolved_and_target_is_validated(self):
        value = self.pair()
        self.assertEqual(set(value['sources']), {'deepmd-kit', 'lammps'})
        self.assertEqual(value['version'], 'dp-aaaaaaaaaaaa-lmp-bbbbbbbbbbbb')
        with self.assertRaises(ValueError):
            self.pair('dsprhbm')

    def test_all_native_gpu_jobs_are_contained_and_never_publish(self):
        for target in tracking.TARGETS:
            script = md.render(self.pair(target), 'md-test-' + target)
            subprocess.run(['bash', '-n'], input=script, text=True, check=True)
            self.assertIn('#SBATCH --partition=' + md.TARGETS[target]['partition'], script)
            self.assertIn('--gpus-per-node=1', script)
            self.assertNotIn('#SBATCH --cpus-per-task', script)
            self.assertNotIn('#SBATCH --mem', script)
            self.assertIn('--network none', script)
            self.assertIn('/input/deepmd-kit:ro', script)
            self.assertIn('/input/lammps:ro', script)
            self.assertIn('/opt/apps:/opt/apps:ro', script)
            self.assertNotIn('current.sif', script)
            self.assertNotIn('modulefiles/', script)
            self.assertNotRegex(script, r'(?:^|[= :])/tmp(?:/|$)')

    def test_source_pair_and_resource_bounds_fail_closed(self):
        pair = self.pair()
        for bad in ({}, dict(pair, target='a100'), dict(pair, sources={'lammps': pair['sources']['lammps']})):
            with self.assertRaises((ValueError, KeyError)):
                md.validate_pair(bad)
        for extras in ({'jobs': 7}, {'minutes': 181}, {'overlay_mb': 1024}):
            with self.assertRaises(ValueError):
                md.render(pair, 'test', **extras)

    def test_fingerprint_tracks_actual_code_not_only_upstream_sha(self):
        first = tracking.fingerprint(ROOT / 'controller')
        self.assertRegex(first, r'^[a-f0-9]{64}$')
        self.assertEqual(first, tracking.fingerprint(ROOT / 'controller'))

    def test_runtime_and_build_require_new_package_and_native_configuration(self):
        recipe = (ROOT / 'controller/md_build.sh').read_text()
        self.assertIn('-march=native -mtune=native', recipe)
        self.assertIn('-DENABLE_TENSORFLOW=ON -DENABLE_PYTORCH=ON -DENABLE_JAX=ON', recipe)
        self.assertIn('-DDOWNLOAD_PLUMED=OFF', recipe)
        self.assertIn("['lammps']['packages']", recipe)
        self.assertIn('verify_parity', recipe)
        self.assertIn('check_dynamic', (ROOT / 'controller/md_relocate_audit.py').read_text())


if __name__ == '__main__':
    unittest.main()
