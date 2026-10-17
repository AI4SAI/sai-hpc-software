"""Protocol/unit tests only: synthetic rows never establish scientific acceptance."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'controller/md_baseline.sh'
sys.path.insert(0, str(ROOT / 'controller'))
from md_science import BACKENDS, TOLERANCES, _record, file_digest


def summary_function():
    text = SCRIPT.read_text()
    python = text.split("<<'SUMMARY_PY'\n", 1)[1].split('\nSUMMARY_PY', 1)[0]
    namespace = {'__name__': 'md_baseline_unit_test'}
    exec(compile(python, str(SCRIPT) + ':SUMMARY_PY', 'exec'), namespace)
    return namespace['summarize']


def synthetic_evidence(root):
    """Write deliberately synthetic fixtures to exercise only summary guards."""
    case, records = root / 'case', root / 'records'
    case.mkdir()
    records.mkdir()
    fixture = {'reference': {'energy': -21.0, 'forces': [[0.0] * 3 for _ in range(6)], 'virial': [0.0] * 9},
               'models': {}, 'required_backends': list(BACKENDS), 'prepared_backends': list(BACKENDS),
               'tolerances': TOLERANCES}
    for name in ('data.lmp', 'plumed.dat'):
        (case / name).write_text('synthetic fixture unit-test data\n')
    for backend, filename in BACKENDS.items():
        model = case / filename
        if backend == 'jax':
            model.mkdir()
            (model / 'saved_model.pb').write_text('synthetic saved model data, never executable')
        else:
            model.write_text('synthetic model data, never executable')
        (case / f'in.{backend}').write_text('synthetic input data')
        parts = [file_digest(path) for path in (model, case / 'data.lmp', case / 'plumed.dat', case / f'in.{backend}')]
        fixture['models'][backend] = {'file': filename, 'sha256': parts[0],
            'input_sha256': hashlib.sha256('\n'.join(parts).encode()).hexdigest()}
    (case / 'fixture.json').write_text(json.dumps(fixture))
    (root / 'source.sha').write_text('a' * 40 + '\n')
    (root / 'devices.json').write_text(json.dumps({'tf': ['CPU'], 'pt': 'cuda:0', 'jax': ['CPU']}))
    resources = {'nodes': 1, 'ranks': 1, 'allocated_gpus': 1, 'threads_per_rank': 1}
    for backend in BACKENDS:
        for engine in ('python', 'lammps'):
            rows = [_record(fixture, backend, engine, 'baseline', resources, index == 0,
                            0.01, copy.deepcopy(fixture['reference'])) for index in range(4)]
            if engine == 'lammps':
                for row in rows:
                    row['plumed'] = {'passed': True}
            (records / f'{engine}-{backend}.json').write_text(json.dumps(rows))


class BaselineProtocolTests(unittest.TestCase):
    def test_shell_syntax_and_contained_offline_checkout(self):
        subprocess.run(['bash', '-n', str(SCRIPT)], check=True)
        text = SCRIPT.read_text()
        self.assertIn('[[ -d /.singularity.d ]]', text)
        self.assertIn('SAI_MD_ALLOCATED_JOB', text)
        self.assertIn('SAI_MD_ALLOCATED_NODE%%.*', text)
        self.assertIn('from md_science import require_execution_context', text)
        self.assertLess(text.index('require_execution_context()'), text.index('clone --no-hardlinks'))
        self.assertIn('export TMPDIR=/workspace/tmp', text)
        self.assertIn('export XDG_CACHE_HOME=/workspace/cache', text)
        self.assertIn('GIT_ALLOW_PROTOCOL=file', text)
        self.assertIn('core.hooksPath=/dev/null', text)
        self.assertIn('/input/deepmd-kit /workspace/source', text)
        self.assertIn('checkout --detach "$sha"', text)
        self.assertNotIn('git fetch', text)
        self.assertNotIn('git submodule', text)
        self.assertNotRegex(text, r'(?:^|[= :])/tmp(?:/|$)')

    def test_invalid_arguments_and_unattested_container_cannot_start(self):
        for args in ([], ['4v100-avx512'], ['wrong', 'a' * 40], ['4v100-avx512', 'HEAD'],
                     ['4v100-avx512', 'a' * 40, 'extra'], ['4v100-avx512', 'a' * 40]):
            with self.subTest(args=args):
                result = subprocess.run(['bash', str(SCRIPT), *args], text=True, capture_output=True,
                                        env={'PATH': '/usr/bin:/bin'})
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn('/control/md_environment.sh: No such file', result.stderr)

    def test_clean_state_and_actual_scientific_engines_are_mandatory(self):
        text = SCRIPT.read_text()
        self.assertIn('! -e /workspace/source && ! -L /workspace/source', text)
        self.assertIn('! -e /workspace/baseline && ! -L /workspace/baseline', text)
        self.assertIn('source /control/md_environment.sh "$target"', text)
        self.assertIn('$site/tensorflow:$site/torch/lib:$MD_SYSTEM_DEEPMD/lib', text)
        self.assertIn('/control/md_science.py prepare /workspace/source "$out/case"', text)
        self.assertIn('--backends tf pt jax', text)
        self.assertIn('for backend in tf pt jax', text)
        self.assertIn('/control/md_science.py python-eval', text)
        self.assertIn('/control/md_science.py lammps-run', text)
        self.assertIn('--executable "$MD_SYSTEM_LAMMPS/bin/lmp"', text)
        self.assertIn('--implementation baseline', text)
        self.assertEqual(text.count('--repeats 3'), 2)
        self.assertIn('from deepmd.pt.utils.env import DEVICE', text)
        self.assertNotIn('torch.arange', text)
        self.assertNotIn('verify_science(', text)
        self.assertNotIn('sbatch ', text)

    def test_failures_keep_original_logs_and_no_stale_summary_can_pass(self):
        text = SCRIPT.read_text()
        self.assertIn('set -euo pipefail', text)
        self.assertIn('"$@" 2>&1 | tee "$out/logs/$phase.log"', text)
        self.assertIn('MD_BASELINE_FAILED phase=%s exit=%s', text)
        self.assertIn("(Path(root) / 'summary.json').open('x')", text)
        self.assertNotIn('|| true', text)


class BaselineSummaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        synthetic_evidence(self.root)
        self.summarize = summary_function()

    def summarize_current(self, **overrides):
        args = dict(root=self.root, target='4v100-avx512', sha='a' * 40,
                    job='123', node=os.uname().nodename)
        args.update(overrides)
        return self.summarize(**args)

    def mutate_rows(self, change, engine='python', backend='pt'):
        path = self.root / 'records' / f'{engine}-{backend}.json'
        rows = json.loads(path.read_text())
        change(rows)
        path.write_text(json.dumps(rows))

    def test_complete_baseline_has_actual_vectors_but_no_candidate_or_performance_claim(self):
        report = self.summarize_current()
        self.assertTrue(report['baseline_scientific_verified'])
        self.assertFalse(report['candidate_scientific_verified'])
        self.assertFalse(report['representative_performance_verified'])
        self.assertFalse(report['published'])
        self.assertEqual(len(report['records']), 6)
        for key, rows in report['records'].items():
            self.assertEqual(len(rows), 4)
            self.assertEqual(len(rows[0]['observables']['forces']), 6)
            self.assertEqual(len(rows[0]['observables']['virial']), 9)
            if key.endswith('/lammps'):
                self.assertTrue(all(row['plumed']['passed'] for row in rows))

    def test_stale_source_allocation_or_missing_backend_rejected(self):
        for kwargs in ({'sha': 'b' * 40}, {'sha': 'HEAD'}, {'job': 'not-a-job'}, {'node': 'another-node'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.summarize_current(**kwargs)
        (self.root / 'records/python-jax.json').unlink()
        with self.assertRaises(FileNotFoundError):
            self.summarize_current()

    def test_changed_or_partial_model_fixture_rejected(self):
        path = self.root / 'case/fixture.json'
        original = path.read_text()
        for changes in ({'prepared_backends': ['tf']}, {'required_backends': ['tf']},
                        {'tolerances': {key: {'atol': 1, 'rtol': 1} for key in TOLERANCES}}):
            with self.subTest(changes=changes):
                fixture = json.loads(original)
                fixture.update(changes)
                path.write_text(json.dumps(fixture))
                with self.assertRaises(ValueError):
                    self.summarize_current()
        path.write_text(original)
        (self.root / 'case/model.pb').write_text('changed model')
        with self.assertRaises(ValueError):
            self.summarize_current()

    def test_incomplete_or_changed_record_identity_rejected(self):
        path = self.root / 'records/python-pt.json'
        original = path.read_text()
        changes = ({'warmup': False}, {'resources': {'ranks': 2}}, {'implementation': 'candidate'},
                   {'backend': 'tf'}, {'engine': 'lammps'}, {'node': 'another-node'},
                   {'input_sha256': 'b' * 64}, {'seconds': float('nan')}, {'seconds': True})
        for change in changes:
            with self.subTest(change=change):
                path.write_text(original)
                self.mutate_rows(lambda rows: rows[0].update(change))
                with self.assertRaises(ValueError):
                    self.summarize_current()
        path.write_text(original)
        self.mutate_rows(lambda rows: rows.pop())
        with self.assertRaises(ValueError):
            self.summarize_current()

    def test_wrong_numeric_values_or_failed_plumed_rejected(self):
        path = self.root / 'records/python-pt.json'
        original = path.read_text()
        self.mutate_rows(lambda rows: rows[0]['observables'].update(energy=99))
        with self.assertRaises(ValueError):
            self.summarize_current()
        path.write_text(original)
        self.mutate_rows(lambda rows: rows[0].update(plumed={'passed': False}), engine='lammps')
        with self.assertRaises(ValueError):
            self.summarize_current()


if __name__ == '__main__':
    unittest.main()
