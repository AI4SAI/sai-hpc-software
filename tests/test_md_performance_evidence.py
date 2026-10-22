"""Publication revalidation tests use synthetic files, never simulation jobs."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_md_science import oracle_text
from test_md_performance_run import large_dump
from md_performance import ATOMS, parse_engine_time, render_input
from md_performance_evidence import verify_performance_evidence
from md_performance_exec import executable_identity
from md_performance_run import _fixture_digest, parse_nsight_kernel_trace, replicated_reference
from md_science import (BACKENDS, TOLERANCES, PLUMED_INPUT, file_digest, parse_reference,
                        render_data, render_lammps_input)


class RawPerformanceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.case, self.output = self.root / 'case', self.root / 'performance'
        self.case.mkdir()
        self.output.mkdir()
        self.launcher = self.root / 'md_runtime.sh'
        self.launcher.write_text('# synthetic launcher; never executed\n')
        self.image = self.root / 'candidate.sif'
        self.image.write_bytes(b'synthetic image; never executed')
        self.baseline = self.root / 'baseline-lmp'
        self.baseline.write_bytes(b'synthetic baseline binary; never executed')
        self.candidate_binary_sha = hashlib.sha256(b'synthetic image-resident LAMMPS').hexdigest()
        self.resources = {'nodes': 1, 'ranks': 1, 'threads_per_rank': 1, 'gpus_per_node': 1}
        self.expected = {'slurm_job': '12345', 'resources': self.resources,
                         'launcher_sha256': file_digest(self.launcher),
                         'artifact': str(self.image), 'artifact_sha256': file_digest(self.image),
                         'target': '16v100-avx2', 'nsys': '/opt/devtools/nsys/bin/nsys',
                         'candidate_executable': '/opt/apps/lammps/test/16V100/bin/lmp',
                         'baseline_executable': str(self.baseline)}
        self.binding = {'logical_cpus': [3], 'physical_cores': [[0, 3]], 'omp_threads': '1',
                        'dp_intra_threads': '1', 'dp_inter_threads': '1', 'node': 'node1', 'job': '12345'}
        self.csv = 'Start (ns),Duration (ns),Name,Device,GrdX,GrdY,GrdZ\n1,42,deepmd_compute,Tesla V100,4,1,1\n'

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value, sort_keys=True) + '\n')

    @staticmethod
    def read(path):
        return json.loads(path.read_text())

    def build(self, backend='tf'):
        self.backend = backend
        fixture = parse_reference(oracle_text())
        fixture.update(schema=2, tolerances=TOLERANCES, models={})
        (self.case / 'data.lmp').write_text(render_data(fixture))
        (self.case / 'plumed.dat').write_text(PLUMED_INPUT)
        (self.case / f'in.{backend}').write_text(render_lammps_input(backend))
        model = self.case / BACKENDS[backend]
        if backend == 'jax':
            model.mkdir()
            (model / 'saved_model.pb').write_bytes(b'synthetic model')
        else:
            model.write_bytes(b'synthetic model')
        parts = [file_digest(path) for path in (model, self.case / 'data.lmp', self.case / 'plumed.dat',
                                               self.case / f'in.{backend}')]
        fixture['models'][backend] = {'file': BACKENDS[backend], 'sha256': parts[0],
            'input_sha256': hashlib.sha256('\n'.join(parts).encode()).hexdigest()}
        self.write(self.case / 'fixture.json', fixture)
        self.fixture = fixture
        fixture_sha = _fixture_digest(self.case, backend)
        self.frozen = {'schema': 1, 'backend': backend, 'steps': 100, 'atoms': ATOMS,
                       'workload': 'synthetic replicated fixed-geometry force evaluation',
                       'resources': self.resources, 'node': 'node1', 'fixture_sha256': fixture_sha,
                       'input': render_input(backend, 100), 'minimum_engine_seconds': 3.0}
        self.frozen['input_sha256'] = hashlib.sha256((fixture_sha + '\n' + self.frozen['input']).encode()).hexdigest()
        calibration = self.trial('calibration-00', 'baseline')
        self.frozen['calibration'] = [parse_engine_time(calibration['stdout'], 100)]
        self.write(self.output / 'calibration-records.json', [calibration])
        self.write(self.output / 'frozen.json', self.frozen)
        traces = {}
        if backend == 'pt':
            for side in ('baseline', 'candidate'):
                record = self.trial(f'trace-{side}', side, profile=True)
                traces[side] = record['execution_device']
        records = [self.trial(f'{side}-{index:02d}', side, warmup=index == 0, proof=traces.get(side))
                   for index in range(4) for side in ('baseline', 'candidate')]
        self.write(self.output / 'records.json', records)
        self.write(self.output / 'report.json', {'passed': True, 'performance': {'speedup': 999}})

    def trial(self, name, side, *, profile=False, warmup=False, proof=None):
        trial = self.output / name
        trial.mkdir()
        for filename in (BACKENDS[self.backend], 'data.lmp', 'plumed.dat', f'in.{self.backend}'):
            source, destination = self.case / filename, trial / filename
            shutil.copytree(source, destination) if source.is_dir() else shutil.copyfile(source, destination)
        (trial / 'in.performance').write_text(self.frozen['input'])
        (trial / 'result.dump').write_text(large_dump(self.fixture))
        (trial / 'COLVAR').write_text(f'#! FIELDS time sai_distance\n0.001 {self.fixture["distance_angstrom"]}\n')
        duration = 6 if side == 'baseline' else 6.5
        stdout = (f'SAI_ENERGY = {replicated_reference(self.fixture)["energy"]}\n'
                  f'Loop time of {duration} on 1 procs for 100 steps with 2058 atoms\n')
        (trial / 'stdout.txt').write_text(stdout)
        self.write(trial / 'process.json', {'returncode': 0, 'wall_seconds': 8.0})
        command = ['taskset', '--cpu-list', '3', 'bash', str(self.launcher),
                   'lmp', '-log', 'log.lammps', '-in', 'in.performance']
        identity = {key: self.expected[key] for key in ('artifact', 'artifact_sha256', 'target')}
        identity['implementation'] = side
        execution = dict(identity, command=command, slurm_job='12345', node='node1', resources=self.resources,
                         CUDA_VISIBLE_DEVICES='' if self.backend in ('tf', 'jax') else '0',
                         launcher_sha256=file_digest(self.launcher))
        self.write(trial / 'execution.json', execution)
        child = [self.expected[side + '_executable'], '-log', 'log.lammps', '-in', 'in.performance']
        if profile:
            child = [self.expected['nsys'], 'profile', '--trace=cuda', '--sample=none', '--cpuctxsw=none',
                     '--force-overwrite=false', '--output', '/work/cuda-trace', *child]
        self.write(trial / 'performance-binding.json', dict(self.binding, **identity, argv=child,
            executable=self.expected[side + '_executable'],
            executable_sha256=file_digest(self.baseline) if side == 'baseline' else self.candidate_binary_sha))
        if self.backend == 'pt':
            if profile:
                (trial / 'cuda-trace.nsys-rep').write_bytes(b'synthetic binary report')
                (trial / 'cuda-kernels.csv').write_text(self.csv)
                evidence = {'collector': 'runner-owned nsys profile + cuda_gpu_trace; not imported attestation',
                            'trial': name, 'profile_command': command,
                            'stats_command': [self.expected['nsys'], 'stats', '--report', 'cuda_gpu_trace',
                                '--format', 'csv', '--timeunit', 'ns', str(trial / 'cuda-trace.nsys-rep')],
                            'trace': parse_nsight_kernel_trace(self.csv),
                            'report_sha256': file_digest(trial / 'cuda-trace.nsys-rep'),
                            'csv_sha256': file_digest(trial / 'cuda-kernels.csv')}
                device = dict(kind='gpu', backend='pt', device_verified=True, model='Tesla V100', evidence=evidence)
            elif proof is None:
                device = {'kind': 'gpu', 'backend': 'pt', 'device_verified': False,
                          'evidence': {'reason': 'calibration precedes separate trace'}}
            else:
                device = copy.deepcopy(proof)
                device['evidence'].update(measured_stdout_sha256=file_digest(trial / 'stdout.txt'),
                                          policy='separate same-input trace')
        else:
            device = {'kind': 'cpu', 'backend': self.backend, 'device_verified': True, 'model': 'CPU on node1',
                      'evidence': {'command': command, 'stdout_sha256': file_digest(trial / 'stdout.txt'), 'returncode': 0}}
        self.write(trial / 'device.json', device)
        self.write(trial / 'science.json', {'passed': False, 'reason': 'untrusted cached summary'})
        record = {'trial': name, 'implementation': side, 'warmup': warmup, 'profiled': profile,
                  'input_sha256': self.frozen['input_sha256'], 'node': 'node1', 'resources': self.resources,
                  'slurm_job': '12345', 'binding': self.binding, 'stdout': stdout, 'wall_seconds': 8.0,
                  'execution_device': device, 'scientific_verified': False}
        self.write(trial / 'record.json', record)
        return record

    def verify(self):
        return verify_performance_evidence(self.output, self.case, backend=self.backend, expected=self.expected)

    def change_record(self, name, change):
        path = self.output / name / 'record.json'
        record = self.read(path)
        change(record)
        self.write(path, record)
        rows = self.read(self.output / 'records.json')
        self.write(self.output / 'records.json', [record if row['trial'] == name else row for row in rows])

    def test_cpu_acceptance_recomputes_science_and_timing_and_retains_hashes(self):
        self.build()
        with patch('md_performance_evidence.subprocess.run') as execute:
            result = self.verify()
        execute.assert_not_called()
        self.assertTrue(result['performance_verified'])
        self.assertEqual(result, json.loads(json.dumps(result)))
        self.assertAlmostEqual(result['performance']['speedup'], 6 / 6.5)
        self.assertEqual(result['files']['candidate-03/result.dump'], file_digest(self.output / 'candidate-03/result.dump'))
        self.assertNotIn('report.json', result['files'])
        self.assertEqual(result['binaries'], {'baseline': file_digest(self.baseline),
                                              'candidate': self.candidate_binary_sha})

    def test_executable_wrapper_hashes_measured_lammps_not_profiler(self):
        direct = [str(self.baseline), '-log', 'log.lammps']
        profiled = ['/opt/devtools/nsys/bin/nsys', 'profile', '--trace=cuda', '--sample=none',
                    '--cpuctxsw=none', '--force-overwrite=false', '--output', '/work/cuda-trace', *direct]
        self.assertEqual(executable_identity(direct), executable_identity(profiled))
        self.assertEqual(executable_identity(profiled)['executable_sha256'], file_digest(self.baseline))
        for argv in [profiled[:8], [*profiled[:2], '--unexpected', *profiled[3:]],
                     ['relative-lmp'], ['/untrusted/nsys', *profiled[1:]]]:
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                executable_identity(argv)
        link = self.root / 'site-lmp'
        link.symlink_to(self.baseline)
        self.assertEqual(executable_identity([str(link)])['executable_sha256'], file_digest(self.baseline))

    def test_baseline_site_binary_changes_block_publication(self):
        self.build()
        self.baseline.write_bytes(b'changed preinstalled LAMMPS')
        with self.assertRaisesRegex(ValueError, 'current site binary'):
            self.verify()

    def test_candidate_binary_consistency_covers_trace_and_measured_trials(self):
        self.build('pt')
        path = self.output / 'candidate-01/performance-binding.json'
        binding = self.read(path)
        self.write(path, dict(binding, executable_sha256='a' * 64))
        with patch('md_performance_evidence.subprocess.run', return_value=subprocess.CompletedProcess([], 0, self.csv)):
            with self.assertRaisesRegex(ValueError, 'executable bytes changed'):
                self.verify()

    def test_missing_binary_hash_in_calibration_or_candidate_cannot_pass(self):
        self.build()
        for trial in ['calibration-00', 'candidate-00']:
            path = self.output / trial / 'performance-binding.json'
            original = self.read(path)
            without_hash = dict(original)
            without_hash.pop('executable_sha256')
            self.write(path, without_hash)
            with self.subTest(trial=trial), self.assertRaisesRegex(ValueError, 'checksum is missing'):
                self.verify()
            self.write(path, original)

    def test_savedmodel_directory_inputs_are_verified(self):
        self.build('jax')
        self.assertTrue(self.verify()['passed'])
        (self.output / 'candidate-01' / BACKENDS['jax'] / 'saved_model.pb').write_bytes(b'changed model')
        with self.assertRaisesRegex(ValueError, 'input changed'):
            self.verify()

    def test_real_raw_force_change_blocks_even_with_cached_passed_flags(self):
        self.build()
        path = self.output / 'candidate-01/result.dump'
        lines = path.read_text().splitlines()
        fields = lines[-1].split()
        fields[1] = str(float(fields[1]) + 1)
        lines[-1] = ' '.join(fields)
        path.write_text('\n'.join(lines))
        self.change_record('candidate-01', lambda value: value.update(scientific_verified=True))
        with self.assertRaisesRegex(ValueError, 'forces'):
            self.verify()

    def test_mismatched_image_launcher_and_raw_stdout_are_rejected(self):
        self.build()
        original_image = self.image.read_bytes()
        self.image.write_bytes(b'substituted image')
        with self.assertRaisesRegex(ValueError, 'image checksum'):
            self.verify()
        self.image.write_bytes(original_image)
        original_launcher = self.launcher.read_text()
        self.launcher.write_text('changed launcher')
        with self.assertRaisesRegex(ValueError, 'launcher checksum'):
            self.verify()
        self.launcher.write_text(original_launcher)
        self.change_record('candidate-01', lambda value: value.update(stdout=value['stdout'].replace('6.5', '3.0')))
        with self.assertRaisesRegex(ValueError, 'raw stdout'):
            self.verify()

    def test_final_container_job_affinity_and_image_are_checked(self):
        self.build()
        path = self.output / 'candidate-01/performance-binding.json'
        original = self.read(path)
        for key, bad in [('job', '999'), ('logical_cpus', [4]), ('artifact_sha256', 'a' * 64),
                         ('implementation', 'baseline'), ('argv', ['/bin/true'])]:
            self.write(path, dict(original, **{key: bad}))
            with self.subTest(field=key), self.assertRaises(ValueError):
                self.verify()
        self.write(path, original)
        self.assertTrue(self.verify()['passed'])

    def test_cpu_gpu_visibility_and_nonzero_process_are_rejected(self):
        self.build()
        path = self.output / 'candidate-01/execution.json'
        record = self.read(path)
        self.write(path, dict(record, CUDA_VISIBLE_DEVICES='0'))
        with self.assertRaisesRegex(ValueError, 'hide CUDA'):
            self.verify()
        self.write(path, record)
        self.write(self.output / 'candidate-01/process.json', {'returncode': 1, 'wall_seconds': 8.0})
        with self.assertRaisesRegex(ValueError, 'process did not succeed'):
            self.verify()

    def test_trial_reordering_duplicates_and_symlinks_are_rejected(self):
        self.build()
        path = self.output / 'records.json'
        rows = self.read(path)
        self.write(path, rows[1:] + rows[:1])
        with self.assertRaisesRegex(ValueError, 'alternate'):
            self.verify()
        self.write(path, rows)
        raw = self.output / 'candidate-01/result.dump'
        raw.unlink()
        raw.symlink_to(self.output / 'baseline-01/result.dump')
        with self.assertRaisesRegex(ValueError, 'untrusted evidence'):
            self.verify()

    def test_pt_proof_regenerates_binary_report_csv_and_rechecks_science(self):
        self.build('pt')
        with patch('md_performance_evidence.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'Processing report (different preamble)\n' + self.csv)) as stats:
            self.assertTrue(self.verify()['passed'])
        self.assertEqual(stats.call_count, 2)
        self.assertEqual(stats.call_args.args[0][1:4], ['stats', '--report', 'cuda_gpu_trace'])
        trace_dump = self.output / 'trace-candidate/result.dump'
        trace_dump.write_text('malformed')
        with patch('md_performance_evidence.subprocess.run', return_value=subprocess.CompletedProcess([], 0, self.csv)):
            with self.assertRaisesRegex(ValueError, 'dump frame'):
                self.verify()

    def test_forged_csv_with_updated_hash_and_summary_still_rejected(self):
        self.build('pt')
        name = 'trace-baseline'
        csv_path = self.output / name / 'cuda-kernels.csv'
        csv_path.write_text(self.csv.replace('deepmd_compute', 'invented_compute'))
        device_path = self.output / name / 'device.json'
        device = self.read(device_path)
        device['evidence'].update(csv_sha256=file_digest(csv_path), trace=parse_nsight_kernel_trace(csv_path.read_text()))
        self.write(device_path, device)
        path = self.output / name / 'record.json'
        record = self.read(path)
        record['execution_device'] = device
        self.write(path, record)
        with patch('md_performance_evidence.subprocess.run', return_value=subprocess.CompletedProcess([], 0, self.csv)):
            with self.assertRaisesRegex(ValueError, 'does not derive'):
                self.verify()

    def test_timed_pt_trial_cannot_borrow_other_implementation_trace(self):
        self.build('pt')
        device = self.read(self.output / 'baseline-01/device.json')
        self.write(self.output / 'candidate-01/device.json', device)
        self.change_record('candidate-01', lambda value: value.update(execution_device=device))
        with patch('md_performance_evidence.subprocess.run', return_value=subprocess.CompletedProcess([], 0, self.csv)):
            with self.assertRaisesRegex(ValueError, 'different GPU trace'):
                self.verify()

    def test_calibration_history_and_raw_inputs_cannot_be_substituted(self):
        self.build()
        path = self.output / 'frozen.json'
        frozen = self.read(path)
        frozen['calibration'][0]['engine_seconds'] = 5.0
        self.write(path, frozen)
        with self.assertRaisesRegex(ValueError, 'calibration summary'):
            self.verify()
        self.write(path, self.frozen)
        (self.output / 'calibration-00/data.lmp').write_text('changed geometry')
        with self.assertRaisesRegex(ValueError, 'input changed'):
            self.verify()

    def test_duplicate_json_and_nonfinite_walltime_are_rejected(self):
        self.build()
        self.change_record('candidate-01', lambda value: value.update(wall_seconds=float('nan')))
        with self.assertRaisesRegex(ValueError, 'nonfinite'):
            self.verify()
        (self.output / 'records.json').write_text('{"a": 1, "a": 2}')
        with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
            self.verify()


if __name__ == '__main__':
    unittest.main()
