"""The daily tester must verify upstream science, GPU dispatch and provenance."""
import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import cp2k_regtest as reg
import cp2k_regtest_suite as suite
from cp2k_feature_contract import BASE_FLAGS, GPU_FLAGS
from delivery_layout import artifact_path
from release_contract import make_identity
from source_cache import checksum
import software_controller as controller


def upstream_fixture():
    # Deliberately small source tree. Real upstream execution is validated by
    # the cluster trial; these fixtures test the gate's rejection paths offline.
    source = {f'tests/{name}': b'# upstream harness\n' for name in suite.HARNESS}
    source['tests/QS/sample_xyz/SI_8.xyz'] = b'1\nSi fixture\nSi 0 0 0\n'
    for directory, inputs in suite.CASES.items():
        specs = []
        for name in inputs:
            solver = ('  PREFERRED_DIAG_LIBRARY CUSOLVER\n' if directory == 'regtest-cusolver'
                      else '' if name == 'tailblock_hang.inp'
                      else '  PREFERRED_DIAG_LIBRARY ELPA\n  ELPA_NEIGVEC_MIN 1\n  ELPA_QR T\n')
            text = ('&GLOBAL\n' + solver + '  RUN_TYPE ENERGY\n&END\n&FORCE_EVAL\n'
                    '  &DFT\n    BASIS_SET_FILE_NAME BASIS_MOLOPT\n    &SCF\n'
                    '      EPS_SCF 1.0E-7\n      MAX_SCF 5\n      IGNORE_CONVERGENCE_FAILURE T\n'
                    '    &END\n  &END\n&END\n')
            source[f'tests/QS/{directory}/{name}'] = text.encode()
            match = '[]' if name == 'tailblock_hang.inp' else '[{matcher="E_total", tol=2e-14, ref=-17.25}]'
            specs.append(json.dumps(name) + ' = ' + match)
        source[f'tests/QS/{directory}/TEST_FILES.toml'] = ('\n'.join(specs) + '\n').encode()
    return source


def write_results(task, request):
    def write(relative, text):
        path = task / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    flags = BASE_FLAGS | GPU_FLAGS | {'libxs', 'libxsmm', 'libxc_gpu', 'libwignernj', 's_dftd3', 'openblas'}
    write('results/version.txt', ' CP2K version 2026.2\n cp2kflags: ' + ' '.join(sorted(flags)) + '\n')
    write('results/harness-exit-code', '0\n')
    write('results/regtest.log', 'Number of FAILED tests 0\nNumber of WRONG tests 0\n'
          'Number of CORRECT tests 9\nStatus: OK\n')
    devices = [f'GPU-{i}' for i in range(4)]
    write('results/devices.json', json.dumps(devices))
    for directory, inputs in suite.CASES.items():
        for name, timer in inputs.items():
            folder = f'results/regtesting/TEST-fixture/QS/{directory}'
            write(f'{folder}/{name}', (task / f'tests/QS/{directory}/{name}').read_text())
            output = (' CP2K| source code revision number: ' + request['source_sha'][:12] + '\n'
                      'GLOBAL| Diagonalization library cuSOLVER\n Total energy: -17.25\n'
                      'SCF run NOT converged\nPROGRAM ENDED AT now\n')
            if timer:
                output += f' {timer} 5 1.0 0.1 0.2 0.3 0.4\n'
            write(f'{folder}/{name}.out', output)
            for rank in range(4):
                trace = dict(rank=rank, size=4, omp_threads=4, cores=[[0, rank*4+i] for i in range(4)],
                    gpu=devices[rank], binary=request['identity']['install_prefix'] + '/bin/cp2k.psmp',
                    image_sha256=request['artifact_sha256'])
                write(f'{folder}/rank-{name}-{rank}.json', json.dumps(trace))
    suite.write_report(task, request)


class SuiteTests(unittest.TestCase):
    def test_rank_trace_uses_upstream_positional_input_name(self):
        self.assertEqual(suite.input_name(['Si8-generalized.inp']), 'Si8-generalized.inp')
        self.assertEqual(suite.input_name(['-i', 'H13-tailblock.inp']), 'H13-tailblock.inp')
        self.assertEqual(suite.input_name(['--version']), 'version')
        for args in ([], ['--shell'], ['../outside.inp'], ['x.inp', 'y.inp']):
            with self.subTest(args=args), self.assertRaises(ValueError):
                suite.input_name(args)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir='/tmp')
        self.addCleanup(tmp.cleanup)
        self.task = Path(tmp.name)
        self.source = upstream_fixture()
        self.request = dict(target='16v100-avx2', source_sha='a'*40, recipe_sha256='b'*64,
                            identity={'install_prefix': '/opt/apps/cp2k/fixture/16V100'},
                            artifact_sha256='c'*64, suite_files=suite.prepare(self.source.__getitem__, self.task))
        write_results(self.task, self.request)

    def test_adaptations_preserve_science_and_references(self):
        for directory, inputs in suite.CASES.items():
            relative = f'tests/QS/{directory}/'
            self.assertEqual(suite.specs((self.task / relative / 'TEST_FILES.toml').read_text()),
                             suite.specs(self.source[relative + 'TEST_FILES.toml'].decode()))
            for name in inputs:
                before = self.source[relative + name].decode()
                after = (self.task / relative / name).read_text()
                if directory == 'regtest-cusolver':
                    self.assertEqual(before, after)
                else:
                    scientific = lambda text: [line for line in text.splitlines()
                                               if not line.strip().startswith(('ELPA_', 'PREFERRED_DIAG_LIBRARY'))]
                    self.assertEqual(scientific(before), scientific(after))
                    self.assertIn('PREFERRED_DIAG_LIBRARY CUSOLVER', after)
        self.assertEqual(self.request['suite_files'], suite.prepare(self.source.__getitem__))

    def test_complete_suite_allows_intentionally_short_scf_and_records_actual_coverage(self):
        result = suite.verify_results(self.task, self.request)
        self.assertEqual(len(result['cases']), 9)
        self.assertEqual(sum(c['coverage'] != 'fallback' for c in result['cases']), 6)
        self.assertEqual(result['numerical_checks'], 8)
        self.assertEqual(result['reported_checks'], 9)

    def test_missing_output_dispatch_wrong_number_feature_and_device_are_rejected(self):
        work = 'results/regtesting/TEST-fixture/QS/regtest-cusolver/'
        output = work + 'Si8-generalized.inp.out'
        trace = work + 'rank-Si8-generalized.inp-1.json'
        for path, before, after in (
            (output, 'cp_fm_general_cusolver', 'cp_fm_diag_scalapack'),
            (output, '-17.25', '-17.2501'),
            (output, '-17.25', 'nan'),
            (output, 'PROGRAM ENDED AT', 'PROGRAM DID NOT END'),
            (output, 'a'*12, 'b'*12),
            ('results/version.txt', ' cusolvermp ', ' '),
            ('results/version.txt', ' libwignernj ', ' '),
            ('results/regtest.log', 'CORRECT tests 9', 'CORRECT tests 8'),
            ('results/harness-exit-code', '0', '1'),
            (trace, 'GPU-1', 'GPU-0'),
            (trace, 'c'*64, 'd'*64),
        ):
            p = self.task / path
            original = p.read_text()
            self.assertIn(before, original)
            p.write_text(original.replace(before, after))
            with self.subTest(path=path, change=after), self.assertRaises(ValueError):
                suite.verify_results(self.task, self.request)
            p.write_text(original)
        (self.task / output).unlink()
        with self.assertRaises(ValueError):
            suite.verify_results(self.task, self.request)

    def test_matcher_columns_and_relative_tolerances(self):
        for matcher, line in [('E_total', ' Total energy: -2.0'),
                              ('M011', ' ENERGY| Total FORCE_EVAL ( QS ) energy [hartree] -2.0'),
                              ('M095', ' Fermi energy: -2.0')]:
            check = dict(matcher=matcher, ref=-2.0, tol=1e-12)
            self.assertEqual(suite.numeric_result(line, check)['value'], -2.0)
            with self.assertRaises(ValueError):
                suite.numeric_result(line.replace('-2.0', '-2.01'), check)

    def test_executed_input_or_redirected_evidence_is_rejected(self):
        path = self.task / 'results/regtesting/TEST-fixture/QS/regtest-elpa/H13-tailblock.inp'
        path.write_text(path.read_text().replace('MAX_SCF 5', 'MAX_SCF 1'))
        with self.assertRaisesRegex(ValueError, 'executed input'):
            suite.verify_results(self.task, self.request)
        path.unlink()
        path.symlink_to(self.task / 'tests/QS/regtest-elpa/H13-tailblock.inp')
        with self.assertRaisesRegex(ValueError, 'redirected'):
            suite.verify_results(self.task, self.request)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir='/tmp')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.control = self.root / 'controller/snapshot'
        self.control.mkdir(parents=True)
        for name in controller.contract_files('cp2k'):
            (self.control / name).write_text('# trusted ' + name + '\n')
        for module, name, value in ((reg, 'ROOT', self.root), (reg, 'CONTROL', self.control),
                                    (reg.science, 'ROOT', self.root),
                                    (controller, 'ROOT', self.root), (controller, 'CONTROL', self.control)):
            p = patch.object(module, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.identity = make_identity('cp2k', 'development', 'master', 'a'*40, 'master-test',
                                      controller.recipe_fingerprint('cp2k'), '16v100-avx2')
        image = artifact_path(self.root, self.identity, 'build')
        self.task = reg.science.run_dir('regtest')
        (self.task / 'results').mkdir(parents=True)
        (self.task / 'bin').mkdir()
        self.source = upstream_fixture()
        self.request = dict(run_id='regtest', build_run_id='build', minutes=20, purpose=reg.PURPOSE,
            software='cp2k', identity=self.identity, source_sha='a'*40, version='master-test',
            target='16v100-avx2', recipe_sha256=self.identity['recipe_sha256'],
            install_prefix=self.identity['install_prefix'], artifact=str(image), artifact_sha256='c'*64,
            launcher=str(self.control / 'cp2k_regtest_suite.py'),
            launcher_sha256=checksum(self.control / 'cp2k_regtest_suite.py'),
            controller_sha256=checksum(self.control / 'cp2k_regtest.py'),
            helpers_sha256=checksum(self.control / 'cp2k_feature_contract.py'), job='123', **suite.RESOURCES,
            suite_files=suite.prepare(self.source.__getitem__, self.task))
        (self.task / 'job.sbatch').write_text(reg.render_job(argparse.Namespace(**self.request)))
        (self.task / 'bin/cp2k.psmp').write_text(reg.RANK_WRAPPER)
        self.request['job_script_sha256'] = checksum(self.task / 'job.sbatch')
        self.request['rank_wrapper_sha256'] = checksum(self.task / 'bin/cp2k.psmp')
        (self.task / 'request.json').write_text(json.dumps(self.request))
        (self.task / 'job.id').write_text('123\n')
        write_results(self.task, self.request)
        for name, value in {
            'allocation.txt': 'JobId=123 Partition=16V100 NumNodes=1 NumTasks=16 NumCPUs=32 TresPerNode=gres/gpu:4',
            'nodes.txt': '16v100n1\n', 'modules.txt': 'openmpi/5.0.10-nvhpc26.3-gnu-cuda12-auto\n',
            'mpirun.txt': '/opt/devtools/openmpi/openmpi-5.0.10-nvhpc263-gnu-cuda12-avx2/bin/mpirun\n',
            'mpi-version.txt': 'mpirun (Open MPI) 5.0.10\n',
            'slurm-123.log': 'CP2K_CUSOLVERMP_REGTEST_FINISHED\n',
        }.items():
            (self.task / 'results' / name).write_text(value)
        for p in (patch.object(reg, 'source_reader', return_value=self.source.__getitem__),
                  patch.object(reg.science, 'request_artifact', return_value=(image, {}))):
            p.start()
            self.addCleanup(p.stop)

    def test_four_gpu_container_job_has_no_memory_request(self):
        script = (self.task / 'job.sbatch').read_text()
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)
        self.assertNotIn('--mem', script)
        self.assertIn('--gpus-per-node=4', script)
        self.assertIn('cp2k_regtest_suite.py', script)
        self.assertIn(self.request['artifact'], script)
        evidence = reg.verify_evidence(self.task, self.request)
        self.assertEqual(evidence['job'], '123')
        self.assertEqual(len(evidence['cases']), 9)

    def test_changed_source_snapshot_cannot_be_accepted_by_rewriting_request_hashes(self):
        self.source['tests/QS/regtest-elpa/H13-tailblock.inp'] += b'# changed\n'
        with self.assertRaisesRegex(ValueError, 'candidate source tree'):
            reg.verify_evidence(self.task, self.request)

    def test_failed_slurm_job_overrides_any_stale_success_report(self):
        args = argparse.Namespace(run_id='regtest', timeout=5, interval=0)
        queue = subprocess.CompletedProcess([], 0, stdout='')
        with patch.object(reg.subprocess, 'run', return_value=queue), \
                patch.object(reg.subprocess, 'check_output', return_value='123|TIMEOUT|0:0|\n'), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(reg.monitor(args), 1)
        self.assertTrue((self.task / 'results/report.txt').read_text().endswith('Status: FAILED\n'))
        self.assertFalse(json.loads((self.task / 'results/status.json').read_text())['verified'])

    def test_publication_requires_raw_regression_evidence_after_science(self):
        self.assertEqual(controller.required_acceptance('cp2k', '16v100-avx2', 'development'),
                         ('cp2k_science', 'cp2k_regtest'))
        for track, target in (('release', '16v100-avx2'), ('development', 'dsprhbm')):
            self.assertEqual(controller.required_acceptance('cp2k', target, track), ('cp2k_science',))
        manifest = dict(software='cp2k', target='16v100-avx2', identity=self.identity,
                        sha256='c'*64, artifact=self.request['artifact'])
        # Isolate this proof's validation; the existing science gate is tested
        # separately and the required proof list above checks their composition.
        with patch.object(controller, 'required_acceptance', return_value=('cp2k_regtest',)):
            with self.assertRaisesRegex(ValueError, 'cp2k_regtest'):
                controller.validate_acceptance(manifest)
            evidence = reg.verify_evidence(self.task, self.request)
            path = self.task / 'results/evidence.json'
            path.write_text(json.dumps(evidence))
            (self.task / 'results/status.json').write_text(json.dumps(
                dict(job='123', state='COMPLETED', exit_code='0:0', verified=True)))
            manifest['cp2k_regtest'] = dict(self.request, verified=True, evidence_sha256=checksum(path))
            controller.validate_acceptance(manifest)
            out = self.task / 'results/regtesting/TEST-fixture/QS/regtest-elpa/H13-tailblock.inp.out'
            out.write_text(out.read_text().replace('-17.25', '-17.0'))
            with self.assertRaisesRegex(ValueError, 'difference'):
                controller.validate_acceptance(manifest)

    def test_publication_keeps_the_scientifically_tested_user_launcher(self):
        request = dict(self.request, controller=str(self.control))
        image = Path(request['artifact'])
        image.parent.mkdir(parents=True)
        launcher = self.control / 'cp2k'
        manifest = dict(request, sha256=request['artifact_sha256'],
                        cp2k_science={'launcher_sha256': checksum(launcher)},
                        cp2k_regtest={'launcher_sha256': request['launcher_sha256']})
        with patch.object(controller, 'validate_candidate'), \
                patch.object(controller, 'publish_module', return_value={}) as publish, \
                patch('export_native.export_delivery', return_value={}):
            controller._publish_runtime_entry(request, image, manifest)
        self.assertEqual(manifest['runtime_launcher'], str(launcher))
        self.assertEqual(publish.call_args.args[3], launcher)


if __name__ == '__main__':
    unittest.main()
