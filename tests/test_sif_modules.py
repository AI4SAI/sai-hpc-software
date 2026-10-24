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
        memory = runtime.memory_root({'SLURM_JOB_ID': '123'})
        scratch = memory / 'runtime/rank1'
        environment = dict(SLURM_JOB_PARTITION='16V100', SLURM_JOB_ID='123', MPI_HOME=row['mpi_prefix'],
                           TMPDIR=str(memory), PATH='/usr/bin', CUDA_VISIBLE_DEVICES='0,1',
                           OMPI_ARGV='x,y $(touch /bad)', OMPI_COMM_WORLD_RANK='1',
                           OMP_NUM_THREADS='4', APPTAINER_BIND='/opt/apps:/opt/apps')
        with patch.object(runtime.shutil, 'which', return_value='/opt/devtools/apptainer/1.4.4/bin/apptainer'):
            args, child = runtime.command(self.snapshot, Path('/org/example/catalog.json'), 'lammps',
                'lmp', ['-in', 'input with spaces', '-var', 'value', 'a,b'], environment,
                Path('/org/example/work'), scratch)
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
        self.assertEqual(child['APPTAINER_CONFIGDIR'], str(memory / 'runtime/config'))
        self.assertIn(row['installed_deepmd_dependency']['identity']['install_prefix'] + ':' +
                      row['installed_deepmd_dependency']['identity']['install_prefix'] + ':ro', args)
        self.assertNotIn('/opt/apps:/opt/apps:ro', args)
        self.assertIn(str(scratch) + ':/tmp:rw', args)

    def test_memory_bootstrap_is_private_and_does_not_change_the_callers_environment(self):
        environment = dict(SLURM_JOB_ID='123', TMPDIR='/home/example/old-scratch')
        original = dict(environment)
        argv = ['/org/example/catalog.json', '_mpi', 'mpirun', '-np', '2', 'cp2k.psmp']
        with patch.object(runtime, 'memory_scope', return_value=None), \
                patch.object(runtime, 'apparmor_restricts_userns', return_value=False), \
                patch.object(runtime.os, 'execve', side_effect=RuntimeError('exec intercepted')) as execute:
            with self.assertRaisesRegex(RuntimeError, 'exec intercepted'):
                runtime.ensure_memory(argv, environment)
        command = execute.call_args.args[1]
        self.assertEqual(command[0], '/usr/bin/unshare')
        for flag in ('--user', '--map-current-user', '--mount', '--keep-caps', '--memory'):
            self.assertIn(flag, command)
        self.assertEqual(command[-len(argv):], argv)
        self.assertEqual(environment, original)

    def test_restricted_node_uses_the_apptainer_profile_before_mpi(self):
        environment = dict(SLURM_JOB_ID='123')
        argv = ['/org/example/catalog.json', '_mpi', 'mpirun']
        with patch.object(runtime, 'memory_scope', return_value=None), \
                patch.object(runtime, 'apparmor_restricts_userns', return_value=True), \
                patch.object(runtime, 'bootstrap_memory', side_effect=RuntimeError('bootstrap')) as bootstrap:
            with self.assertRaisesRegex(RuntimeError, 'bootstrap'):
                runtime.ensure_memory(argv, environment)
        bootstrap.assert_called_once_with(argv, environment)

    def test_bootstrap_cannot_skip_private_namespace_setup(self):
        with self.assertRaisesRegex(ValueError, 'did not create private namespaces'):
            runtime.main(['--bootstrap', os.readlink('/proc/self/ns/mnt'),
                          os.readlink('/proc/self/ns/user'), '{}', 'unused'])

    def test_mpi_rank_cannot_create_an_isolated_namespace_separate_from_its_peers(self):
        environment = dict(SLURM_JOB_ID='123', OMPI_COMM_WORLD_RANK='0')
        with patch.object(runtime, 'memory_scope', return_value=None), patch.object(runtime.os, 'execve') as execute:
            with self.assertRaisesRegex(ValueError, 'mpirun/mpiexec entry'):
                runtime.ensure_memory([], environment)
            execute.assert_not_called()

    def test_memory_environment_avoids_conflicting_prrte_tmpdir_options(self):
        environment = dict(SLURM_JOB_ID='123', TMPDIR='/home/example/old-scratch',
                           PRTE_MCA_prte_local_tmpdir_base='/tmp/local',
                           PRTE_MCA_prte_remote_tmpdir_base='/tmp/remote')
        with patch.object(runtime, 'private_directory'):
            result = runtime.memory_environment(environment)
        self.assertNotIn('PRTE_MCA_prte_local_tmpdir_base', result)
        self.assertNotIn('PRTE_MCA_prte_remote_tmpdir_base', result)
        self.assertEqual(result['PRTE_MCA_prte_tmpdir_base'], str(runtime.memory_root(environment) / 'mpi'))
        self.assertEqual(environment['TMPDIR'], '/home/example/old-scratch')

    def test_setup_refuses_mounting_in_the_existing_namespace(self):
        with self.assertRaisesRegex(ValueError, 'new mount and user namespaces'):
            runtime.enter_memory([os.readlink('/proc/self/ns/mnt'), os.readlink('/proc/self/ns/user'), 'unused'])

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

    def test_memory_checksum_index_is_job_scoped_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / 'image.sif'
            image.write_bytes(b'accepted image')
            image.chmod(0o444)
            expected = runtime.digest(image)
            cache = runtime.private_directory(root / 'cache')
            self.assertEqual(runtime.verify_image(image, expected, cache, job='1'), 'hashed')
            self.assertEqual(runtime.verify_image(image, expected, cache, job='1'), 'cached')
            for job in range(2, 70):
                self.assertEqual(runtime.verify_image(image, expected, cache, job=str(job)), 'hashed')
            self.assertEqual(len(list(cache.iterdir())), 1)
            self.assertLessEqual(len(json.loads((cache / 'images.json').read_text())), 64)
            self.assertEqual(runtime.verify_image(image, expected, cache, job='69'), 'cached')

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
