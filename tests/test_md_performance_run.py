import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from test_md_science import oracle_text
from md_science import parse_reference, NKTV2P, STRESS_ORDER
from md_performance_exec import binding_record
from md_performance_run import (ATOMS, replicated_reference, parse_large_lammps_output,
                                verify_large_science, parse_nsight_kernel_trace, Runner)


def large_dump(fixture):
    ref = replicated_reference(fixture)
    lines = ['ITEM: TIMESTEP', '1001', 'ITEM: NUMBER OF ATOMS', str(ATOMS),
             'ITEM: BOX BOUNDS pp pp pp', '0 91', '0 91', '0 91',
             'ITEM: ATOMS id fx fy fz ' + ' '.join(f'c_sai_virial[{j}]' for j in range(1, 10))]
    stress = [-ref['virial'][j] * NKTV2P / ATOMS for j in STRESS_ORDER]
    for atom in range(ATOMS, 0, -1):
        lines.append(str(atom) + ' ' + ' '.join(format(v, '.17g') for v in ref['forces'][atom - 1] + stress))
    return '\n'.join(lines) + '\n'


class RepresentativeRunnerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = parse_reference(oracle_text())
        self.reference = replicated_reference(self.fixture)
        self.stdout = f'SAI_ENERGY = {self.reference["energy"]}\n'
        self.dump = large_dump(self.fixture)
        self.colvar = f'#! FIELDS time sai_distance\n0.001 {self.fixture["distance_angstrom"]}\n'

    def test_replication_is_exact_2058_atoms_and_all_force_components_checked(self):
        self.assertEqual(len(self.reference['forces']), 2058)
        self.assertEqual(self.reference['forces'][6], self.fixture['reference']['forces'][0])
        result = verify_large_science(self.stdout, self.dump, self.colvar, self.fixture)
        self.assertTrue(result['passed'])
        self.assertEqual(len(result['observables']['forces']), 2058)
        lines = self.dump.splitlines()
        last = lines[-1].split()
        last[1] = str(float(last[1]) + 1)
        lines[-1] = ' '.join(last)
        with self.assertRaises(ValueError):
            verify_large_science(self.stdout, '\n'.join(lines), self.colvar, self.fixture)

    def test_bad_frame_geometry_ids_and_nonfinite_fail(self):
        for dump in [self.dump.replace('2058\n', '6\n', 1), self.dump.replace('0 91', '0 13', 1),
                     self.dump.replace('2058 ', '2057 ', 1), self.dump.replace('2058 ', '9999 ', 1),
                     self.dump.replace('2058 ', '2058 nan ', 1)]:
            with self.assertRaises(ValueError):
                parse_large_lammps_output(self.stdout, dump)
        with self.assertRaises(ValueError):
            verify_large_science(self.stdout, self.dump, self.colvar.replace('0.001', '0'), self.fixture)

    def test_device_proof_requires_actual_positive_kernel_csv_not_transfer(self):
        header = 'Start (ns),Duration (ns),Name,Device,GrdX,GrdY,GrdZ\n'
        value = parse_nsight_kernel_trace(header + '1,42,deepmd_compute_kernel,Tesla V100,4,1,1\n')
        self.assertEqual(value['kernel_events'], 1)
        for row in ['', '1,42,Memcpy HtoD,Tesla V100,,,\n',
                    '1,42,Memcpy HtoD,Tesla V100,1,1,1\n',
                    '1,nan,kernel,Tesla V100,1,1,1\n', '1,42,kernel,Tesla V100,0,1,1\n',
                    '1,42,kernel,Tesla V100,1,1,1\n2,42,kernel,Tesla A100,1,1,1\n']:
            with self.assertRaises(ValueError):
                parse_nsight_kernel_trace(header + row)

    def test_scientific_context_guard_is_first_runner_action(self):
        with patch('md_performance_run.require_execution_context', side_effect=RuntimeError('no allocation')):
            with self.assertRaisesRegex(RuntimeError, 'no allocation'):
                Runner('/missing', 'tf', {'ranks': 1}, '/missing', '/missing')

    def test_actual_affinity_and_physical_topology_are_measured(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            topology = root / 'cpu9/topology'
            topology.mkdir(parents=True)
            (topology / 'physical_package_id').write_text('1\n')
            (topology / 'core_id').write_text('4\n')
            with patch('md_performance_exec.os.sched_getaffinity', return_value={9}):
                result = binding_record(9, root)
                self.assertEqual(result['physical_cores'], [[1, 4]])
                self.assertEqual(result['logical_cpus'], [9])
            for actual in ({8}, {9, 10}, set()):
                with patch('md_performance_exec.os.sched_getaffinity', return_value=actual):
                    with self.assertRaises(ValueError):
                        binding_record(9, root)

    def test_final_container_wrapper_requires_context_before_exec(self):
        from md_performance_exec import main
        with patch('md_performance_exec.require_execution_context', side_effect=RuntimeError('no allocation')), \
                patch('md_performance_exec.os.execv') as execute:
            with self.assertRaisesRegex(RuntimeError, 'no allocation'):
                main(['/bin/true'])
            execute.assert_not_called()

    def test_separate_profile_proof_is_checksum_bound_and_not_used_as_timing(self):
        from md_science import file_digest
        runner = Runner.__new__(Runner)
        runner.backend, runner.node, runner.nsys = 'pt', 'node1', '/opt/devtools/nsys'
        with tempfile.TemporaryDirectory() as directory:
            runner.output = root = Path(directory)
            trace, timed = root / 'trace-baseline', root / 'baseline-01'
            trace.mkdir(); timed.mkdir()
            (trace / 'cuda-trace.nsys-rep').write_text('synthetic trace; parser tested separately')
            (trace / 'cuda-kernels.csv').write_text('synthetic raw CSV')
            (timed / 'stdout.txt').write_text('synthetic measured output')
            evidence = {'trial': trace.name, 'report_sha256': file_digest(trace / 'cuda-trace.nsys-rep'),
                        'csv_sha256': file_digest(trace / 'cuda-kernels.csv')}
            runner.device_proofs = {'baseline': {'device_verified': True, 'evidence': evidence}}
            result = subprocess.CompletedProcess([], 0)
            proof = runner._device(timed, [], {'SAI_MD_IMPLEMENTATION': 'baseline'}, result)
            self.assertIn('benchmark intervals are unprofiled', proof['evidence']['policy'])
            (trace / 'cuda-kernels.csv').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'evidence changed'):
                runner._device(timed, [], {'SAI_MD_IMPLEMENTATION': 'baseline'}, result)

    def test_run_traces_each_implementation_separately_from_eight_measurements(self):
        runner = Runner.__new__(Runner)
        runner.backend, runner.nsys = 'pt', '/opt/devtools/nsys'
        runner.fixture_sha256, runner.node, runner.resources = 'a' * 64, 'node1', {'ranks': 1}
        runner.device_proofs, runner.calibration_records = {}, []
        frozen = {'steps': 100, 'input': 'synthetic frozen input', 'workload': 'test only'}
        calls = []
        def measure(implementation, name, steps, text, **kwargs):
            calls.append((implementation, name, kwargs))
            return {'execution_device': {'device_verified': True}, 'profiled': kwargs.get('profile', False)}
        with tempfile.TemporaryDirectory() as directory:
            runner.output = Path(directory)
            with patch.object(runner, 'measure', side_effect=measure), \
                    patch('md_performance_run.calibrate', return_value=frozen), \
                    patch('md_performance_run.verify_performance', return_value={'speedup': 1.0}) as verify:
                report = runner.run()
                self.assertEqual(len(calls), 10)
                self.assertTrue(all(row[2].get('profile') is True for row in calls[:2]))
                self.assertTrue(all('profile' not in row[2] for row in calls[2:]))
                self.assertTrue(all(row['profiled'] is False for row in verify.call_args.args[0]))
                self.assertFalse(report['profiled'])
                self.assertTrue(report['separate_cuda_trace'])

    def test_measure_clears_inherited_profiler_and_checks_actual_binding(self):
        from md_science import file_digest
        runner = Runner.__new__(Runner)
        runner.backend, runner.resources, runner.timeout = 'tf', {'ranks': 1}, 30
        runner.fixture_sha256, runner.node, runner.cpu = 'a' * 64, 'node1', 9
        runner.fixture, runner.nsys, runner.binding = {}, None, None
        runner.physical_cores = [[1, 4]]
        with tempfile.TemporaryDirectory() as directory:
            runner.output = runner.case = root = Path(directory)
            runner.launcher = root / 'launcher.sh'
            runner.launcher.write_text('# synthetic test; not executed\n')
            runner.launcher_sha256 = file_digest(runner.launcher)
            def execute(command, **kwargs):
                self.assertEqual(command[:3], ['taskset', '--cpu-list', '9'])
                self.assertNotIn('SAI_MD_PROFILE_LMP', kwargs['env'])
                self.assertEqual(kwargs['env']['CUDA_VISIBLE_DEVICES'], '')
                self.assertEqual(kwargs['env']['OMP_NUM_THREADS'], '1')
                trial = kwargs['cwd']
                (trial / 'performance-binding.json').write_text(json.dumps({
                    'logical_cpus': [9], 'physical_cores': [[1, 4]], 'node': 'node1', 'job': '123',
                    'omp_threads': '1', 'dp_intra_threads': '1', 'dp_inter_threads': '1'}))
                (trial / 'result.dump').write_text('synthetic data')
                (trial / 'COLVAR').write_text('synthetic data')
                return subprocess.CompletedProcess(command, 0,
                    'Loop time of 6 on 1 procs for 100 steps with 2058 atoms\n')
            with patch('md_performance_run.require_execution_context'), \
                    patch('md_performance_run._fixture_digest', return_value='a' * 64), \
                    patch('md_performance_run.copy_trial_inputs'), \
                    patch('md_performance_run.verify_large_science', return_value={'passed': True}), \
                    patch('md_performance_run.subprocess.run', side_effect=execute), \
                    patch.dict(os.environ, {'SLURM_JOB_ID': '123', 'SAI_MD_PROFILE_LMP': '/wrong/nsys'}):
                record = runner.measure('baseline', 'baseline-01', 100, 'synthetic input')
                self.assertFalse(record['profiled'])
                self.assertEqual(record['binding']['physical_cores'], [[1, 4]])
                runner.physical_cores = [[2, 5]]
                with self.assertRaisesRegex(ValueError, 'binding/allocation differs'):
                    runner.measure('candidate', 'candidate-01', 100, 'synthetic input')


if __name__ == '__main__':
    unittest.main()
