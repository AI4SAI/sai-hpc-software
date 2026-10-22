"""Native deployment gates: real parsers, frozen inputs, rank and Slurm checks."""
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

REPOSITORY = Path(__file__).resolve().parents[1]
PACKAGE = REPOSITORY / 'deployment/lammps-30Sep2026'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


with patch.dict(sys.modules, install=load('native_install', PACKAGE / 'install.py')):
    native = load('lammps_native_checks', PACKAGE / 'native_checks.py')
evidence = load('native_evidence', REPOSITORY / 'controller/md_evidence.py')
with patch.dict(sys.modules, md_evidence=evidence):
    science = load('native_science', REPOSITORY / 'controller/md_science.py')


class LammpsNativeChecksTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.release, self.reference = native.release_reference()
        self.ref = self.reference['partitions']['4V100']
        self.delivery = next(d for d in self.release['deliveries'] if d['partition'] == '4V100')
        self.specs = {s['identity']['software']: s for s in self.delivery['installations']}

    def test_reference_rejects_changed_release_or_relaxed_tolerance(self):
        shutil.copyfile(PACKAGE / 'native-reference.json', self.root / 'native-reference.json')
        (self.root / 'release.json').write_text('different release')
        with patch.object(native, 'PACKAGE', self.root), self.assertRaisesRegex(ValueError, 'different release'):
            native.release_reference()
        shutil.copyfile(PACKAGE / 'release.json', self.root / 'release.json')
        changed = copy.deepcopy(self.reference)
        changed['partitions']['4V100']['tolerances']['forces']['atol'] = 1.0
        (self.root / 'native-reference.json').write_text(json.dumps(changed))
        with patch.object(native, 'PACKAGE', self.root), self.assertRaisesRegex(ValueError, 'tolerance changed'):
            native.release_reference()

    def test_changed_input_and_path_escape_fail(self):
        source = self.root / 'model'
        source.write_bytes(b'pinned model')
        pins = {'model': native.digest(source)}
        source.write_bytes(b'different model')
        with self.assertRaisesRegex(ValueError, 'pinned file changed'):
            native.check_files(self.root, pins)
        with self.assertRaisesRegex(ValueError, 'invalid pinned path'):
            native.check_files(self.root, {'../other': '0' * 64})

    def test_wrong_partition_or_container_wrapper_is_rejected(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, 'wrong partition'):
            native.check_environment(dict(partition='4V100'), self.specs)
        environment = dict(SLURM_JOB_PARTITION='4V100', SAI_LAMMPS_NATIVE_PARTITION='4V100',
                           SAI_LAMMPS_PREFIX=self.specs['lammps']['prefix'])
        with patch.dict(os.environ, environment, clear=True), patch.object(native.shutil, 'which', return_value='/some/container/lmp'), \
                self.assertRaisesRegex(ValueError, 'ELF executable'):
            native.check_environment(dict(partition='4V100'), self.specs)

    def test_rank_evidence_requires_both_nodes_exact_binary_and_visible_gpu(self):
        folder = self.root / 'case'
        (folder / 'ranks').mkdir(parents=True)
        binary = self.specs['lammps']['prefix'] + '/bin/lmp'
        checksum = self.ref['binary_sha256']
        for rank, host in enumerate(('node-a', 'node-b')):
            (folder / 'ranks' / f'rank-{rank}.tsv').write_text(f'{rank}\t{host}\t{binary}\t0\t1\t{checksum}\n')
        native.check_ranks(folder, ['node-a', 'node-b'], binary, checksum)
        path = folder / 'ranks/rank-1.tsv'
        original = path.read_text()
        for old, new in [('node-b', 'node-a'), (binary, '/old/lmp'), ('\t0\t1\t', '\t-1\t1\t'), (checksum, '0' * 64)]:
            path.write_text(original.replace(old, new))
            with self.assertRaisesRegex(ValueError, 'MPI rank/node/binary/GPU'):
                native.check_ranks(folder, ['node-a', 'node-b'], binary, checksum)
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'missing/extra MPI rank'):
            native.check_ranks(folder, ['node-a', 'node-b'], binary, checksum)

    def test_full_force_and_virial_tensors_are_checked_from_raw_dump(self):
        expected = self.ref['science']['tf']['lammps']
        stdout = 'SAI_ENERGY = ' + repr(expected['energy']) + '\n'
        columns = 'id fx fy fz ' + ' '.join(f'c_sai_virial[{i}]' for i in range(1, 10))
        header = 'ITEM: TIMESTEP\n1\nITEM: NUMBER OF ATOMS\n6\nITEM: BOX BOUNDS pp pp pp\n0 13\n0 13\n0 13\nITEM: ATOMS ' + columns + '\n'
        rows = []
        for index, force in enumerate(expected['forces'], 1):
            stress = [-expected['virial'][i] * science.NKTV2P / 6 for i in science.STRESS_ORDER]
            rows.append([str(index), *map(repr, force + stress)])

        def parsed():
            return science.parse_lammps_output(stdout, header + '\n'.join(' '.join(row) for row in rows))

        native.compare_science(parsed(), expected, self.ref['tolerances'], evidence)
        # Preserve the energy and alter the very last atom's force component.
        original = rows[-1][3]
        rows[-1][3] = str(float(original) + 0.1)
        with self.assertRaises(ValueError):
            native.compare_science(parsed(), expected, self.ref['tolerances'], evidence)
        rows[-1][3] = original
        rows[-1][-1] = str(float(rows[-1][-1]) + science.NKTV2P)
        with self.assertRaises(ValueError):
            native.compare_science(parsed(), expected, self.ref['tolerances'], evidence)

    def test_incomplete_cases_cannot_pass_raw_revalidation(self):
        (self.root / 'results').mkdir()
        for name in native.expected_cases(self.ref) - {'deepmd-jax-multi'}:
            (self.root / 'results' / name).mkdir()
        self.assertEqual(len(native.expected_cases(self.ref)), 19)  # 17 science + help + C API
        with patch.object(native, 'context', return_value=({}, self.ref, self.delivery, self.specs)), \
                patch.object(native, 'helpers', return_value=(evidence, science, None, None)), \
                self.assertRaisesRegex(ValueError, 'incomplete native case matrix'):
            native.verify_raw(self.root)

    def test_feature_inventory_checks_packages_and_every_style(self):
        ref = self.ref['lammps_features']
        help_text = 'Large-scale Atomic/Molecular Massively Parallel Simulator - 30 Sep 2026\n\nInstalled packages:\n' + ' '.join(ref['packages']) + '\n\n'
        help_text += '* Body styles:\n' + ' '.join(self.ref['help_body_styles']) + '\n\n'
        for category, styles in ref['styles'].items():
            heading = 'Command styles' if category == 'command' else category + ' styles:'
            help_text += '* ' + heading + '\n' + ' '.join(v for v in styles if not v.endswith(('/kk/host', '/kk/device'))) + '\n\n'
        native.check_features(help_text, ref, evidence, self.ref['help_body_styles'])
        changed = copy.deepcopy(ref)
        changed['styles']['pair'].append('missing/new/style')
        with self.assertRaisesRegex(ValueError, 'packages/styles changed'):
            native.check_features(help_text, changed, evidence, self.ref['help_body_styles'])

    def test_slurm_success_must_match_job_exit_and_partition(self):
        with patch.object(native.subprocess, 'check_output', return_value='123|COMPLETED|0:0|4V100|\n'):
            native.completed_job('123', '4V100')
        for text in ('123|RUNNING|0:0|4V100|\n', '123|COMPLETED|1:0|4V100|\n',
                     '123|COMPLETED|0:0|16V100|\n', ''):
            with patch.object(native.subprocess, 'check_output', return_value=text), \
                    self.assertRaisesRegex(ValueError, 'not COMPLETED'):
                native.completed_job('123', '4V100')

    def test_c_api_checks_kokkos_aliases_omitted_from_help(self):
        prefix = self.specs['lammps']['prefix']
        observed = dict(copy.deepcopy(self.ref['lammps_features']), library=prefix + '/lib/liblammps.so', ldd='resolved')
        native.check_inventory(observed, self.ref['lammps_features'], prefix)
        alias = next(v for v in observed['styles']['pair'] if v.endswith('/kk/device'))
        observed['styles']['pair'].remove(alias)
        with self.assertRaisesRegex(ValueError, 'complete C API inventory differs'):
            native.check_inventory(observed, self.ref['lammps_features'], prefix)

    def test_missing_installation_never_submits_compute_job(self):
        with patch.object(native, 'preflight', return_value=(self.release, self.reference)), \
                patch.object(native, 'home_work_root', return_value=self.root), \
                patch.object(native.Path, 'resolve', return_value=self.root), \
                patch.object(native.deployment, 'preflight', side_effect=ValueError('missing installed files')), \
                patch.object(native.subprocess, 'check_output') as submit, \
                self.assertRaisesRegex(ValueError, 'missing installed files'):
            native.submit(['4V100'])
        submit.assert_not_called()

    def test_empty_results_return_failure_despite_pass_flag(self):
        part = self.root / '4V100'
        part.mkdir()
        (part / 'request.json').write_text('{}')
        checksum = native.digest(part / 'request.json')
        (self.root / 'submission.json').write_text(json.dumps(dict(partitions=['4V100'], requests={'4V100': checksum})))
        (part / 'native-acceptance.json').write_text(json.dumps(dict(passed=True, request_sha256=checksum, files={})))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(native.verify(self.root), 1)

    def test_deployment_shell_syntax(self):
        for name in ('install.sh', 'submit-native-checks.sh', 'verify-native.sbatch', 'native-rank.sh'):
            subprocess.run(['bash', '-n', str(PACKAGE / name)], check=True)


if __name__ == '__main__':
    unittest.main()
