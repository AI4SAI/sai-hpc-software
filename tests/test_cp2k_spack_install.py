import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import cp2k_spack_install_submit as install
from cp2k_spack_environment import environment
from cp2k_spack_mpi_repair import check_repair


class SpackInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.parent = self.root / 'runs/native-probe'
        (self.parent / 'results').mkdir(parents=True)
        self.control = Path(install.__file__).resolve().parent
        self.request = {'partition': '16V100', 'job_id': '123',
                        'purpose': 'native-concretization-and-mirror-fetch-only',
                        'scripts': {name: install.checksum(self.control / name) for name in (
                            'cp2k_spack.py', 'cp2k_spack_environment.py', 'cp2k_spack_native.py', 'environment.sh')}}
        self.record_parent()
        (self.parent / 'work.ext3').write_bytes(b'fixture-overlay')
        (self.parent / 'results/spack.lock').write_text('{}')
        replacement = patch.object(install, 'Path', side_effect=lambda value:
                                  self.root if str(value) == '/home/stardust/sai-hpc-software' else Path(value))
        replacement.start()
        self.addCleanup(replacement.stop)

    def record_parent(self):
        (self.parent / 'request.json').write_text(json.dumps(self.request))
        (self.parent / 'results/probe-success.json').write_text(json.dumps({
            'partition': self.request['partition'], 'mirror_only': True,
            'installed': False, 'fetched': ['source-hash']}))

    def submit(self):
        with patch.object(install.subprocess, 'check_output', side_effect=['123|COMPLETED|0:0|\n', '456\n']), \
                patch.object(install.subprocess, 'run') as run, contextlib.redirect_stdout(io.StringIO()):
            install.submit('native-probe', 'install-pilot')
        self.assertEqual(run.call_args.args[0], ['scontrol', 'release', '456'])
        return self.root / 'runs/install-pilot'

    def test_exact_overlay_lock_and_native_partition_are_bound(self):
        result = self.submit()
        script = (result / 'job.sbatch').read_text()
        self.assertIn('--time=240', script)
        self.assertIn('--gpus-per-node=1', script)
        self.assertNotIn('--cpus-per-task', script)
        self.assertNotIn('--mem=', script)
        self.assertIn('cp --sparse=always', script)
        self.assertIn(install.checksum(self.parent / 'work.ext3'), script)
        self.assertIn(install.checksum(self.parent / 'results/spack.lock'), script)
        self.assertIn('--network none', script)
        request = json.loads((result / 'request.json').read_text())
        self.assertEqual(request['purpose'], 'native-dependency-install-pilot-not-cp2k-build')
        self.assertEqual(request['probe_job'], '123')
        self.assertEqual(request['status'], 'released')
        self.assertTrue((self.parent / 'install-child.json').is_file())

    def test_cpu_resources(self):
        self.request['partition'] = 'DSPRHBM'
        self.record_parent()
        result = self.submit()
        script = (result / 'job.sbatch').read_text()
        self.assertIn('--cpus-per-task=8', script)
        self.assertNotIn('--gpus-per-node', script)

    def test_failed_or_live_probe_is_not_an_install_candidate(self):
        for status in ('FAILED|1:0', 'RUNNING|0:0', 'COMPLETED|1:0'):
            with self.subTest(status=status), \
                    patch.object(install.subprocess, 'check_output', return_value='123|' + status + '|\n'):
                with self.assertRaisesRegex(ValueError, 'completed successful'):
                    install.submit('native-probe', 'install-pilot')

    def test_changed_recipe_and_duplicate_install_refused(self):
        self.request['scripts']['cp2k_spack_environment.py'] = '0' * 64
        self.record_parent()
        with patch.object(install.subprocess, 'check_output', return_value='123|COMPLETED|0:0|\n'):
            with self.assertRaisesRegex(ValueError, 'recipe changed'):
                install.submit('native-probe', 'install-pilot')
        self.request['scripts']['cp2k_spack_environment.py'] = install.checksum(self.control / 'cp2k_spack_environment.py')
        self.record_parent()
        self.submit()
        with patch.object(install.subprocess, 'check_output', return_value='123|COMPLETED|0:0|\n'):
            with self.assertRaisesRegex(ValueError, 'already submitted'):
                install.submit('native-probe', 'another-pilot')

    def test_installer_only_relocks_in_explicit_guarded_repair_and_never_uses_binary_cache(self):
        script = (self.control / 'cp2k_spack_install.sh').read_text()
        self.assertIn('--only-concrete --no-cache --fail-fast', script)
        self.assertEqual(script.count('sha256sum --check --status'), 2)
        self.assertIn('if [[ "$repair_mode" == mpi-runtime ]]', script)
        self.assertLess(script.index('cp2k_spack_mpi_repair.py'), script.index('concretize --force'))
        self.assertNotIn('--dirty', script)
        self.assertNotIn('--overwrite', script)

    def test_repair_guard_permits_only_external_mpi_environment(self):
        new = environment('16V100', '/opt/software/cp2k/development/spack-native-probe/16V100',
                          'zen3', 'ubuntu24.04')
        old = deepcopy(new)
        old['spack']['packages']['openmpi']['externals'][0].pop('extra_attributes')
        check_repair(old, new)
        for mutate in (
            lambda v: v['spack']['specs'].pop(),
            lambda v: v['spack']['config'].update(build_jobs=2),
            lambda v: v['spack']['packages']['openmpi']['externals'][0].update(prefix='/opt/other'),
        ):
            bad = deepcopy(new)
            mutate(bad)
            with self.assertRaisesRegex(ValueError, 'more than'):
                check_repair(old, bad)

    def test_install_repair_preserves_lineage_and_budget(self):
        first = self.submit()
        with patch.object(install.subprocess, 'check_output', side_effect=[
                '456|FAILED|\n', '123|COMPLETED|0:0|\n', '789\n']), \
                patch.object(install.subprocess, 'run'), contextlib.redirect_stdout(io.StringIO()):
            install.submit('native-probe', 'repair-1', 'install-pilot', 'MPI link lacks HCOLL')
        request = json.loads((self.root / 'runs/repair-1/request.json').read_text())
        self.assertEqual(request['retry_policy']['diagnosed_repairs_used'], 1)
        self.assertEqual(request['repair_mode'], 'mpi-runtime')
        self.assertEqual(request['repair_of'], 'install-pilot')
        self.assertTrue((first / 'repair-child.json').exists())
        with patch.object(install.subprocess, 'check_output', return_value='456|FAILED|\n'):
            with self.assertRaisesRegex(ValueError, 'already submitted'):
                install.submit('native-probe', 'duplicate-repair', 'install-pilot', 'MPI link lacks HCOLL')
        request['retry_policy']['diagnosed_repairs_used'] = 2
        (self.root / 'runs/repair-1/request.json').write_text(json.dumps(request))
        with self.assertRaisesRegex(ValueError, 'exhausted'):
            install.submit('native-probe', 'third-repair', 'repair-1', 'another failure')


if __name__ == '__main__':
    unittest.main()
