import copy
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'controller'))
import sif_modules as modules
import sif_runtime as runtime


class SifModulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = json.loads((REPO / 'deployment/sif-development-20261024/snapshot.json').read_text())

    def test_snapshot_replays_archived_acceptance_and_exact_runtime_environment(self):
        spec = importlib.util.spec_from_file_location('sif_prepare', REPO / 'deployment/sif-development-20261024/prepare.py')
        prepare = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prepare)
        self.assertEqual(prepare.snapshot(), self.snapshot)

    def test_unaccepted_or_mixed_source_matrix_is_rejected(self):
        altered = copy.deepcopy(self.snapshot)
        altered['applications']['lammps']['partitions']['16V100']['scientific_verified'] = False
        with self.assertRaisesRegex(ValueError, 'scientific acceptance'):
            modules.validate_snapshot(altered)
        altered = copy.deepcopy(self.snapshot)
        altered['applications']['deepmd-kit']['partitions']['4V100']['environment'] += '\n'
        with self.assertRaisesRegex(ValueError, 'runtime environment'):
            modules.validate_snapshot(altered)

    def evaluate(self, software, partition=None, mode='load', saved=None):
        if not shutil.which('tclsh'):
            self.skipTest('tclsh is required')
        app = self.snapshot['applications'][software]
        source = modules.render_module(Path('/opt/apps/sai-sif/20261024'), software, app)
        setup = '''
proc module-info {kind value} {return [expr {$value eq $::mode}]}
proc module-whatis {args} {}
proc conflict {args} {}
proc module {args} {puts "MODULE $args"}
proc prepend-path {name value} {puts "PATH $name=$value"}
proc setenv {name value} {puts "ENV $name=$value"}
'''
        setup += f'set mode {mode}\n'
        if partition:
            setup += f'set env(SLURM_JOB_PARTITION) {partition}\n'
        if saved:
            setup += f'set env(SAI_SIF_{software.upper().replace("-", "_")}_PARTITION) {saved}\n'
        # tclsh on stdin does not set an error exit status by itself.
        script = setup + 'if {[catch {\n' + source + '\n} err]} {puts stderr $err; exit 1}\n'
        return subprocess.run(['tclsh'], input=script, text=True, capture_output=True,
                              env={k: v for k, v in os.environ.items() if not k.startswith(('SLURM_', 'SAI_'))})

    def test_module_discovery_needs_no_allocation(self):
        for software in self.snapshot['applications']:
            result = self.evaluate(software, mode='display')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('MODULE load', result.stdout)
            self.assertEqual(self.evaluate(software).returncode, 1)

    def test_real_tcl_selects_all_partitions_and_unloads_without_slurm(self):
        for software, app in self.snapshot['applications'].items():
            for partition, row in app['partitions'].items():
                for mode, kwargs in [('load', dict(partition=partition)), ('remove', dict(saved=partition))]:
                    result = self.evaluate(software, mode=mode, **kwargs)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(row['image'], result.stdout)
        self.assertEqual(self.evaluate('lammps', 'DSPRHBM').returncode, 1)
        self.assertEqual(self.evaluate('cp2k', '16V100,4V100').returncode, 1)

    def test_host_mpi_must_match_partition(self):
        environment = {'SLURM_JOB_PARTITION': '16V100', 'MPI_HOME': '/other/mpi'}
        with self.assertRaisesRegex(ValueError, 'host MPI'):
            runtime.selection(self.snapshot, 'cp2k', 'cp2k.psmp', environment)

    def test_mpi_arguments_and_comma_environment_survive_without_nested_launcher(self):
        row = self.snapshot['applications']['lammps']['partitions']['16V100']
        environment = dict(SLURM_JOB_PARTITION='16V100', MPI_HOME=row['mpi_prefix'],
                           TMPDIR='/org/example/job/tmp', PATH='/usr/bin', CUDA_VISIBLE_DEVICES='0,1',
                           OMPI_ARGV='x,y $(touch /bad)', OMPI_COMM_WORLD_RANK='1',
                           OMP_NUM_THREADS='4', APPTAINER_BIND='/opt/apps:/opt/apps')
        with patch.object(runtime.shutil, 'which', return_value='/opt/devtools/apptainer/1.4.4/bin/apptainer'):
            args, child = runtime.command(self.snapshot, Path('/org/example/catalog.json'), 'lammps',
                'lmp', ['-in', 'input with spaces', '-var', 'value', 'a,b'], environment,
                Path('/org/example/work'), Path('/org/example/job/tmp/rank1'))
        self.assertEqual(args[-5:], ['-in', 'input with spaces', '-var', 'value', 'a,b'])
        self.assertEqual(child['APPTAINERENV_CUDA_VISIBLE_DEVICES'], '0,1')
        self.assertEqual(child['APPTAINERENV_OMPI_ARGV'], 'x,y $(touch /bad)')
        self.assertEqual(child['APPTAINERENV_OMP_NUM_THREADS'], '4')
        self.assertNotIn('APPTAINER_BIND', child)
        for option in ('mpirun', '--fakeroot', '--overlay', '--net', '--network', '--containall', '--env'):
            self.assertNotIn(option, args)
        self.assertIn('--no-eval', args)
        self.assertIn('--sharens', args)
        self.assertEqual(child['APPTAINERENV_OMPI_MCA_shmem_mmap_backing_file_base_dir'], '/dev/shm')
        self.assertEqual(child['APPTAINERENV_OMPI_MCA_shmem_mmap_relocate_backing_file'], '1')
        self.assertEqual(child['APPTAINER_CONFIGDIR'], '/org/example/job/tmp/config')
        self.assertIn(row['installed_deepmd_dependency']['identity']['install_prefix'] + ':' +
                      row['installed_deepmd_dependency']['identity']['install_prefix'] + ':ro', args)
        self.assertNotIn('/opt/apps:/opt/apps:ro', args)
        self.assertIn('/org/example/job/tmp/rank1:/tmp:rw', args)

    def test_checksum_cache_reuses_read_then_detects_changed_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / 'image.sif'
            image.write_bytes(b'accepted image')
            image.chmod(0o444)
            expected = runtime.digest(image)
            cache = runtime.private_directory(root / 'cache')
            self.assertEqual(runtime.verify_image(image, expected, cache), 'hashed')
            with patch.object(runtime, 'digest', side_effect=AssertionError('repeated full-image read')):
                self.assertEqual(runtime.verify_image(image, expected, cache), 'cached')
            image.chmod(0o644)
            image.write_bytes(b'changed image!')
            image.chmod(0o444)
            # Some local/CI filesystems expose coarse timestamps. Advance
            # mtime explicitly so this checks cache invalidation, not the clock.
            info = image.stat()
            os.utime(image, ns=(info.st_atime_ns, info.st_mtime_ns + 2_000_000_000))
            with self.assertRaisesRegex(ValueError, 'checksum differs'):
                runtime.verify_image(image, expected, cache)

    def test_cache_and_image_reject_redirection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'real').mkdir(mode=0o700)
            (root / 'link').symlink_to(root / 'real')
            with self.assertRaisesRegex(ValueError, 'private directory'):
                runtime.private_directory(root / 'link')
            image = root / 'image.sif'
            image.write_bytes(b'accepted image')
            with self.assertRaisesRegex(ValueError, 'read-only'):
                runtime.file_identity(image)

    def test_concurrent_ranks_hash_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / 'image.sif'
            image.write_bytes(b'image' * 10000)
            image.chmod(0o444)
            expected = runtime.digest(image)
            cache = runtime.private_directory(root / 'cache')
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(lambda _: runtime.verify_image(image, expected, cache), range(8)))
            self.assertEqual(results.count('hashed'), 1)
            self.assertEqual(results.count('cached'), 7)

    def test_admin_handoff_uses_copied_environment_and_checks_its_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = copy.deepcopy(self.snapshot)
            row = snapshot['applications']['deepmd-kit']['partitions']['16V100']
            (root / 'environment.sh').write_text(row.pop('environment'))
            row['environment_file'] = 'environment.sh'
            (root / 'catalog.json').write_text(json.dumps(snapshot))
            restored = modules.load_snapshot(root / 'catalog.json')
            self.assertEqual(restored, self.snapshot)
            (root / 'environment.sh').write_text('export PATH=/wrong/bin\n')
            with self.assertRaisesRegex(ValueError, 'prepared environment changed'):
                modules.load_snapshot(root / 'catalog.json')


if __name__ == '__main__':
    unittest.main()
