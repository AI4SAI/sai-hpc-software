"""Reject incomplete/wrong native ABACUS evidence before accepting a deployment."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1] / 'deployment/abacus-3.11.0-beta11'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


with patch.dict(sys.modules, install=load('abacus_install_native', PACKAGE / 'install.py')):
    native = load('abacus_deployment_native', PACKAGE / 'native_checks.py')


class AbacusNativeChecksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release, self.reference = native.release_reference()
        self.delivery = next(d for d in self.release['deliveries'] if d['partition'] == '4V100')
        self.spec = self.delivery['installations'][0]
        self.ref = self.reference['partitions']['4V100']

    def test_science_requires_convergence_finite_energy_and_units(self):
        self.assertEqual(native.energy('#SCF IS CONVERGED#\n!FINAL_ETOT_IS -1.23 eV\n'), -1.23)
        for text in ('!FINAL_ETOT_IS -1.23 eV', '#SCF IS CONVERGED#\n!FINAL_ETOT_IS nan eV',
                     '#SCF IS CONVERGED#\n!FINAL_ETOT_IS -1.23 Ha', '#SCF IS CONVERGED#'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                native.energy(text)

    def test_reference_rejects_relaxed_tolerance(self):
        reference = copy.deepcopy(self.reference)
        reference['partitions']['4V100']['tolerance_ev'] = 1.0
        shutil.copyfile(PACKAGE / 'release.json', self.root / 'release.json')
        (self.root / 'native-reference.json').write_text(json.dumps(reference))
        with patch.object(native, 'PACKAGE', self.root), self.assertRaisesRegex(ValueError, 'tolerance changed'):
            native.release_reference()

    def test_changed_source_and_escaping_paths_fail(self):
        path = self.root / 'model.ptg'
        path.write_text('original')
        pins = {'model.ptg': native.digest(path)}
        path.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'pinned file changed'):
            native.check_files(self.root, pins)
        with self.assertRaisesRegex(ValueError, 'unsafe pinned path'):
            native.check_files(self.root, {'../other': '0' * 64})

    def test_loader_rejects_old_elpa_mpi_and_other_torch(self):
        prefix = self.spec['prefix']
        libraries = {
            'libmpi.so.40': '/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-avx512/lib/',
            'libelpa_openmp.so.19': '/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-avx512/nvidia/lib/',
            'libtorch_cpu.so': prefix + '/dependencies/libtorch/lib/',
            'libnep.so': prefix + '/dependencies/nep/lib/',
            'libcusolverMp.so': '/opt/devtools/nvidia/mp_libs/lib/',
            'libcublasmp.so': '/opt/devtools/nvidia/mp_libs/lib/',
            'libnccl.so.2': '/opt/devtools/nvidia/nccl_2.29.3_cuda12.9_sai_v2.29.3-1-sai.2/lib/'}
        text = '\n'.join(f'{name} => {root}{name} (0x123)' for name, root in libraries.items())
        native.check_loader(text, self.spec)
        for old, new in (('gnu-avx512/nvidia', 'gnu/nvidia'), ('cuda12-avx512', 'cuda12-avx2'),
                         (prefix + '/dependencies/libtorch', '/other/torch')):
            with self.subTest(new=new), self.assertRaisesRegex(ValueError, 'wrong loader path'):
                native.check_loader(text.replace(old, new), self.spec)
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            native.check_loader(text + '\nlibmissing.so => not found', self.spec)

    def test_single_gpu_adapts_only_kpoint_pool_count(self):
        data = b'INPUT_PARAMETERS\ndevice gpu\nkpar 2\nbndpar 1\n'
        changed = native.case_input(data, 'pw', 1, '4V100')
        self.assertIn(b'device gpu', changed)
        self.assertIn(b'kpar 1', changed)
        self.assertNotIn(b'kpar 2', changed)
        self.assertEqual(native.case_input(data, 'pw', 2, '4V100'), data)
        self.assertEqual(native.case_input(data, 'pw', 1, 'DSPRHBM'), data)

    def test_gpu_launch_inherits_slurm_affinity_and_cpu_keeps_eight_ranks(self):
        with patch.object(native.shutil, 'which', return_value='/mpi/mpirun'):
            gpu = native.command_for(self.root, ['node-a', 'node-b'], False, [])
            cpu = native.command_for(self.root, ['node-a', 'node-b'], True, [])
            single_cpu = native.command_for(self.root, ['node-a'], True, [])
        self.assertEqual(gpu[gpu.index('--bind-to')+1], 'none')
        self.assertEqual(gpu[gpu.index('-np')+1], '2')
        self.assertEqual(gpu[gpu.index('--host')+1], 'node-a,node-b')
        self.assertEqual(cpu[cpu.index('-np')+1], '8')
        self.assertEqual(cpu[cpu.index('--map-by')+1], 'ppr:4:node:PE=2')
        self.assertEqual(cpu[cpu.index('--host')+1], 'node-a:4,node-b:4')
        self.assertEqual(single_cpu[single_cpu.index('--host')+1], 'node-a:4')
        self.assertEqual(single_cpu[single_cpu.index('-np')+1], '4')

    def test_missing_installation_never_submits_compute_job(self):
        with patch.object(native, 'preflight', return_value=(self.release, self.reference)), \
                patch.object(native, 'native_work_root', return_value=self.root), \
                patch.object(native.Path, 'resolve', return_value=self.root), \
                patch.object(native.deployment, 'preflight', side_effect=ValueError('missing installed files')), \
                patch.object(native.subprocess, 'check_output') as submit, \
                self.assertRaisesRegex(ValueError, 'missing installed files'):
            native.submit(['4V100'])
        submit.assert_not_called()

    def test_work_root_allows_home_and_authorized_project_but_rejects_symlink_escape(self):
        project = self.root / 'project'
        home = self.root / 'home'
        other = self.root / 'other'
        for path in (project, home, other):
            path.mkdir()
        (project / 'escape').symlink_to(other, target_is_directory=True)
        with patch.object(native, 'PROJECT_WORK_ROOT', project), patch.object(native.Path, 'home', return_value=home):
            for path in (project, project / 'new-run', home / 'new-run'):
                self.assertEqual(native.native_work_root(str(path)), path)
            for path in (other, project / 'escape' / 'new-run', self.root / 'project-other'):
                with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'work directory must be'):
                    native.native_work_root(str(path))

    def test_project_billing_and_work_directory_checked_before_releasing_job(self):
        project = self.root / 'project'
        project.mkdir()
        outputs = []
        def freeze(output, source, work_root, partition, reference, account=None):
            output.mkdir()
            (output / 'request.json').write_text('{}')
            self.assertEqual(source, PACKAGE)
            self.assertEqual(work_root, project)
            self.assertEqual(account, native.PROJECT_ACCOUNT)
            outputs.append(output)

        def slurm(command, **kwargs):
            if command[0] == 'sbatch':
                self.assertIn('--hold', command)
                self.assertIn('--account=' + native.PROJECT_ACCOUNT, command)
                self.assertIn('--chdir=' + str(outputs[-1]), command)
                self.assertEqual(kwargs['cwd'], outputs[-1])
                return '123\n'
            return f'JobId=123 Partition=4V100 Account={native.PROJECT_ACCOUNT} WorkDir={outputs[-1]}\n'

        with patch.dict(os.environ, SAI_SOFTWARE_ROOT=str(PACKAGE), SAI_ABACUS_NATIVE_WORK_ROOT=str(project)), \
                patch.object(native, 'PROJECT_WORK_ROOT', project), \
                patch.object(native, 'preflight', return_value=(self.release, self.reference)), \
                patch.object(native.deployment, 'preflight'), patch.object(native, 'freeze', side_effect=freeze), \
                patch.object(native.subprocess, 'check_output', side_effect=slurm), \
                patch.object(native.subprocess, 'run') as release, contextlib.redirect_stdout(io.StringIO()):
            native.submit(['4V100'])
        release.assert_called_once_with(['scontrol', 'release', '123'], check=True)
        self.assertTrue((outputs[0] / 'submission-allocation.txt').is_file())

    def test_wrong_project_billing_never_releases_held_job(self):
        project = self.root / 'project'
        project.mkdir()
        def freeze(output, *args):
            output.mkdir()
            (output / 'request.json').write_text('{}')
        with patch.dict(os.environ, SAI_SOFTWARE_ROOT=str(PACKAGE), SAI_ABACUS_NATIVE_WORK_ROOT=str(project)), \
                patch.object(native, 'PROJECT_WORK_ROOT', project), \
                patch.object(native, 'preflight', return_value=(self.release, self.reference)), \
                patch.object(native.deployment, 'preflight'), patch.object(native, 'freeze', side_effect=freeze), \
                patch.object(native.subprocess, 'check_output', side_effect=[
                    '123\n', 'JobId=123 Partition=4V100 Account=private-stardust WorkDir=/home/stardust\n']), \
                patch.object(native.subprocess, 'run') as release, contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaisesRegex(ValueError, 'Slurm allocation differs'):
            native.submit(['4V100'])
        release.assert_not_called()

    def test_allocation_rejects_other_account_directory_job_and_partition(self):
        text = 'JobId=123 Partition=4V100 Account=proj-sai-hpc-software WorkDir=/org/project/run\n'
        native.check_allocation(text, '123', '4V100', Path('/org/project/run'), native.PROJECT_ACCOUNT)
        for old, new in [('123', '124'), ('4V100', '16V100'), ('proj-sai-hpc-software', 'private-stardust'),
                         ('/org/project/run', '/home/stardust')]:
            with self.subTest(old=old), self.assertRaisesRegex(ValueError, 'Slurm allocation differs'):
                native.check_allocation(text.replace(old, new), '123', '4V100', Path('/org/project/run'), native.PROJECT_ACCOUNT)

    def test_slurm_requires_job_partition_and_exit_status(self):
        with patch.object(native.subprocess, 'check_output', return_value='123|COMPLETED|0:0|4V100|\n'):
            native.completed_job('123', '4V100')
        for text in ('123|RUNNING|0:0|4V100|\n', '123|COMPLETED|1:0|4V100|\n', '123|COMPLETED|0:0|16V100|\n', ''):
            with patch.object(native.subprocess, 'check_output', return_value=text), self.assertRaises(ValueError):
                native.completed_job('123', '4V100')

    def test_pass_flag_without_raw_results_cannot_pass(self):
        output = self.root / '4V100'
        output.mkdir()
        (output / 'request.json').write_text('{}')
        checksum = native.digest(output / 'request.json')
        (self.root / 'submission.json').write_text(json.dumps(dict(partitions=['4V100'], requests={'4V100': checksum})))
        (output / 'native-acceptance.json').write_text(json.dumps(dict(passed=True, request_sha256=checksum, files={})))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(native.verify(self.root), 1)

    def test_complete_raw_matrix_and_mutated_science_rank_gpu_evidence(self):
        # Synthetic outputs exercise the verifier, not the scientific program.
        ref = copy.deepcopy(self.ref)
        for name, case in ref['cases'].items():
            case['inputs'] = {'INPUT': {}}
            folder = self.root / 'inputs' / name
            folder.mkdir(parents=True)
            (folder / 'INPUT').write_bytes(b'INPUT_PARAMETERS\nkpar 2\n')
        prefix = self.spec['prefix']
        mpi = '/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-avx512'
        plan = native.deployment.runtime_plan(self.release)[prefix]
        binary_sha = next(r['sha256'] for r in plan['files'] if r['path'] == 'bin/abacus_max_gpu')
        for filename, value in {'job.id': '123', 'nodes.txt': 'node-a\nnode-b',
                                'binary.txt': prefix + '/bin/abacus', 'mpirun.txt': mpi + '/bin/mpirun',
                                'modules.txt': 'abacus/3.11.0-beta11-auto\nelpa/2026.02.001-2603-gnu-auto\nopenmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto'}.items():
            (self.root / filename).write_text(value + '\n')
        matrix = [('version', None, 2), *native.cases(ref)]
        for key, name, nodes in matrix:
            folder = self.root / 'results' / key
            (folder / 'ranks').mkdir(parents=True)
            (folder / 'process.json').write_text(json.dumps(dict(returncode=0, job='123', wall_seconds=1.0)))
            for rank, host in enumerate(('node-a', 'node-b')[:nodes]):
                (folder / f'ranks/rank-{rank}.tsv').write_text(
                    f'{rank}\t{host}\t{prefix}/bin/abacus\t0\t1\t{binary_sha}\t{mpi}\t1\n')
                (folder / f'ranks/ldd-{rank}.txt').write_text('loader fixture')
            text = 'ABACUS v3.11.0-beta11\n' + ''.join(label + ': yes\n' for label in ref['required_info'])
            if name:
                data = native.case_input((self.root / 'inputs' / name / 'INPUT').read_bytes(), name, nodes, '4V100')
                (folder / 'INPUT').write_bytes(data)
                (folder / 'OUT.autotest').mkdir()
                (folder / 'OUT.autotest/running_scf.log').write_text(
                    f"#SCF IS CONVERGED#\nGPU (x{nodes})\n!FINAL_ETOT_IS {ref['cases'][name]['candidate_energy_ev']} eV\n")
                if name == 'cusolvermp':
                    text += '[Api][cusolverMpSygvd] jobz=1\n'
                if name == 'nccl':
                    text += 'NCCL INFO AllGather: opCount 0 count 64 [nranks=2]\n'
            (folder / 'stdout.log').write_text(text)
        with patch.object(native, 'context', return_value=(dict(partition='4V100'), ref, self.delivery)), \
                patch.object(native, 'check_loader'):
            self.assertEqual(len(native.verify_raw(self.root)['samples']), 8)
            mutations = [
                ('pw-multi/OUT.autotest/running_scf.log', '!FINAL_ETOT_IS', '!REMOVED_ETOT_IS'),
                ('hse-multi/OUT.autotest/running_scf.log', '#SCF IS CONVERGED#', 'not converged'),
                ('pw-multi/ranks/rank-1.tsv', 'node-b', 'node-a'),
                ('pw-multi/ranks/rank-1.tsv', '\t0\t1\t', '\t-1\t1\t'),
                ('cusolvermp-multi/stdout.log', 'cusolverMpSygvd', 'cusolverMpSygvd_bufferSize'),
                ('nccl-multi/stdout.log', 'AllGather:', 'Init:'),
            ]
            for filename, old, new in mutations:
                path = self.root / 'results' / filename
                original = path.read_text()
                path.write_text(original.replace(old, new))
                with self.subTest(filename=filename), self.assertRaises(ValueError):
                    native.verify_raw(self.root)
                path.write_text(original)
            (self.root / 'results/nccl-multi').rename(self.root / 'missing-case')
            with self.assertRaisesRegex(ValueError, 'incomplete native case matrix'):
                native.verify_raw(self.root)

    def test_deployment_shell_syntax(self):
        for name in ('install.sh', 'submit-native-checks.sh', 'native-rank.sh', 'verify-native.sbatch'):
            subprocess.run(['bash', '-n', str(PACKAGE / name)], check=True)


@unittest.skipUnless(shutil.which('tclsh'), 'Tcl is required to evaluate the public module')
class AbacusModuleTests(unittest.TestCase):
    def evaluate(self, partition, mode='load', saved=None):
        # Real Tcl evaluation, with filesystem/module operations recorded by a
        # minimal module host. Loading on the real site is checked after install.
        script = '''
set calls {}
proc module-whatis {args} {}
proc conflict {args} {}
proc module-info {what mode} {global current; return [expr {$mode eq $current}]}
proc prepend-path {args} {global calls; lappend calls $args}
proc depends-on {name} {global calls; lappend calls [list dependency $name]}
proc setenv {name value} {global env current; if {$current eq "remove"} {unset -nocomplain env($name)} else {set env($name) $value}}
rename file realfile
proc file {what args} {if {$what eq "executable"} {return 1}; if {$what eq "type"} {return directory}; return [realfile $what {*}$args]}
set current $env(TEST_MODE)
if {[catch {source $env(TEST_MODULE)} message]} {puts stderr $message; exit 1}
puts $calls
puts [info exists env(SAI_ABACUS_PREFIX)]
'''
        env = dict(os.environ, TEST_MODULE=str(PACKAGE / '3.11.0-beta11-auto'), TEST_MODE=mode,
                   SLURM_JOB_ID='123', SLURM_JOB_PARTITION=partition)
        for key in ('SAI_ABACUS_PREFIX', 'SAI_ABACUS_NATIVE_PARTITION'):
            env.pop(key, None)
        if saved:
            env['SAI_ABACUS_NATIVE_PARTITION'] = saved
            env['SAI_ABACUS_PREFIX'] = '/opt/apps/abacus/abacus-release-' + native.deployment.BUILD_ID + '/' + saved
        return subprocess.run(['tclsh'], input=script, env=env, capture_output=True, text=True)

    def test_each_partition_loads_native_binary_and_matching_dependency_family(self):
        for partition in native.PARTITIONS:
            with self.subTest(partition=partition):
                result = self.evaluate(partition)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('/' + partition + '/bin', result.stdout)
                self.assertIn('elpa/2026.02.001-2603-gnu-auto', result.stdout)
                self.assertIn('gcc/13.3.0' if partition == 'DSPRHBM' else 'nvmplibs/26.7-tmp', result.stdout)
                self.assertNotIn('apptainer', result.stdout.lower())
                self.assertNotIn('/home/', result.stdout)

    def test_unload_uses_saved_partition_and_unknown_partition_fails(self):
        result = self.evaluate('other', mode='remove', saved='16V100')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('/16V100/bin', result.stdout)
        self.assertTrue(result.stdout.endswith('0\n'))
        self.assertNotEqual(self.evaluate('other').returncode, 0)
        self.assertNotEqual(self.evaluate('4V100', saved='16V100').returncode, 0)


if __name__ == '__main__':
    unittest.main()
