"""Release-only qualified targets must not broaden other software matrices."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
from daily_relay import make_plan
from release_contract import PARTITIONS, SOFTWARE, TRACKS, claim_build, make_identity, resolve_tracks
import spingnn_release as native


class SpinGNNReleaseTests(unittest.TestCase):
    @staticmethod
    def cpu_only():
        settings = copy.deepcopy(native.site())
        for target, row in settings['targets'].items():
            row.update(enabled=target == 'dsprhbm', reason='qualification pending')
        return settings

    @staticmethod
    def identity(**changes):
        values = dict(software='spingnn2', track='release', source_ref='v2.0.0',
                      source_sha='2f4e7ca93e64b7113df1a9e9eafae4c1dc8279ec',
                      source_version='v2.0.0', recipe_sha256='b' * 64, target='dsprhbm')
        return make_identity(**dict(values, **changes))

    @staticmethod
    def sources(software, tracks):
        return resolve_tracks(software, tracks, resolver=lambda repository, ref:
                              dict(sha='a' * 40, ref='v2.0.0', version='v2.0.0'))

    def test_only_formal_release_is_resolved_from_mixed_daily_selection(self):
        resolver = Mock(return_value=dict(sha='a' * 40, ref='v2.0.0', version='v2.0.0'))
        rows = resolve_tracks('spingnn2', TRACKS, resolver)
        resolver.assert_called_once_with('Hyyu-Lab/SpinGNN', 'latest-release')
        self.assertEqual([row['status'] for row in rows], ['skipped', 'skipped', 'resolved'])
        self.assertEqual(rows[-1]['track'], 'release')

    def test_no_prerelease_development_or_unqualified_gpu_build(self):
        for changes in (dict(track='development'), dict(track='prerelease'),
                        dict(target='a100')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.identity(**changes)
        row = self.identity()
        self.assertEqual((row['partition'], row['gpus'], row['cuda_arch']), ('DSPRHBM', 0, ''))
        with patch('spingnn_config.site', return_value=self.cpu_only()):
            for target in ('4v100-avx512', '16v100-avx2', '8v100v0-avx512'):
                source = dict(self.sources('spingnn2', ['release'])[0], target=target)
                with self.subTest(target=target), self.assertRaisesRegex(ValueError, 'no accepted recipe'):
                    native.delivery(source, 'unqualified')

    def test_daily_adds_one_cpu_release_and_no_beta(self):
        with patch('spingnn_config.site', return_value=self.cpu_only()):
            generation = make_plan('all', ','.join(TRACKS), 'all', self.sources)
        tasks = [task for task in generation['tasks'] if task['software'] == 'spingnn2']
        self.assertEqual([(task['kind'], task['source']['track'], task['source']['target'])
                          for task in tasks], [('build', 'release', 'dsprhbm')])
        self.assertEqual({row['target'] for row in generation['skipped_sources'] if 'target' in row},
                         set(SOFTWARE['spingnn2']['targets']) - {'dsprhbm'})
        for software in ('qe', 'abacus', 'cp2k', 'gpumd', 'deepmd-kit', 'lammps'):
            expected = make_plan(software, ','.join(TRACKS), 'all', self.sources)['tasks']
            self.assertEqual([task for task in generation['tasks'] if task['software'] == software], expected)
        self.assertEqual(make_plan('spingnn2', 'development,prerelease', 'all', self.sources)['tasks'], [])

    def test_extra_partitions_are_only_selected_for_spingnn_and_qualified_individually(self):
        settings = self.cpu_only()
        settings['targets']['4v100-avx512']['enabled'] = True
        settings['targets']['cpu-misc']['enabled'] = True
        with patch('spingnn_config.site', return_value=settings):
            tasks = make_plan('spingnn2', 'release', 'all', self.sources)['tasks']
        self.assertEqual({row['source']['target'] for row in tasks}, {'dsprhbm', '4v100-avx512', 'cpu-misc'})
        self.assertEqual(tuple(PARTITIONS), ('dsprhbm', '4v100-avx512', '16v100-avx2', '8v100v0-avx512'))
        for software in ('qe', 'abacus', 'cp2k', 'gpumd', 'deepmd-kit', 'lammps'):
            with self.subTest(software=software), self.assertRaisesRegex(ValueError, 'no selected software'):
                make_plan(software, 'release', 'cpu-misc', self.sources)

    def test_release_absence_skips_and_network_error_does_not_fallback(self):
        resolver = Mock(side_effect=ValueError('no latest-release available'))
        row, = resolve_tracks('spingnn2', ['release'], resolver)
        self.assertEqual(row['status'], 'skipped')
        with self.assertRaisesRegex(OSError, 'offline'):
            resolve_tracks('spingnn2', ['release'], Mock(side_effect=OSError('offline')))

    def test_claims_keep_failure_and_recipe_changes_explicit_and_resume_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            identity = self.identity()
            original = claim_build(root, [identity], 'initial')
            self.assertEqual(claim_build(root, [identity], 'initial', resume=True), original)
            self.assertFalse(claim_build(root, [identity], 'unchanged')['build'])
            changed_recipe = self.identity(recipe_sha256='c' * 64)
            self.assertFalse(claim_build(root, [changed_recipe], 'new-recipe')['build'])
            self.assertTrue(claim_build(root, [changed_recipe], 'retry', retry=True)['build'])
            changed_source = self.identity(source_sha='d' * 40)
            self.assertTrue(claim_build(root, [changed_source], 'moved-tag')['build'])
            with self.assertRaisesRegex(ValueError, 'differs'):
                claim_build(root, [changed_source], 'initial', resume=True)

    def test_attempt_prefix_keeps_complete_source_and_recipe_and_never_aliases_retry(self):
        first = self.identity(installation_run='attempt-one')
        second = self.identity(installation_run='attempt-two')
        self.assertNotEqual(first['install_prefix'], second['install_prefix'])
        self.assertIn('/' + first['source_sha'] + '/', first['install_prefix'])
        self.assertIn('/' + first['recipe_sha256'] + '/', first['install_prefix'])
        self.assertTrue(first['install_prefix'].startswith('/home/stardust/spingnn/versions/'))
        with self.assertRaises(ValueError):
            self.identity(installation_run='../elsewhere')


class NativeLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = dict(software='spingnn2', repository='Hyyu-Lab/SpinGNN', track='release',
                           requested_ref='latest-release', status='resolved', source_ref='v2.0.1',
                           source_version='v2.0.1', source_sha='c' * 40, target='dsprhbm')

    def test_partial_write_never_publishes_and_retry_preserves_existing_bytes(self):
        path = self.root / 'receipt.json'
        with patch.object(native.os, 'fsync', side_effect=OSError('interrupted write')):
            with self.assertRaises(OSError):
                native.immutable(path, b'complete\n')
        self.assertFalse(path.exists())
        native.immutable(path, b'complete\n')
        before = path.stat()
        native.immutable(path, b'complete\n')
        self.assertEqual(before.st_ino, path.stat().st_ino)
        self.assertEqual(path.stat().st_mode & 0o777, 0o444)
        with self.assertRaisesRegex(ValueError, 'changed'):
            native.immutable(path, b'changed\n')
        self.assertEqual(path.read_bytes(), b'complete\n')

    def test_concurrent_publications_keep_one_complete_immutable_file(self):
        path = self.root / 'receipt.json'
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: native.immutable(path, b'complete\n'), range(4)))
        self.assertEqual(path.read_bytes(), b'complete\n')
        redirected = self.root / 'link'
        redirected.symlink_to(path)
        with self.assertRaisesRegex(ValueError, 'redirected'):
            native.immutable(redirected, b'complete\n')

    def test_submission_is_held_then_released_and_recovered_without_resubmitting(self):
        value = native.delivery(self.source, 'one')
        with patch.object(native, 'ROOT', self.root), patch.object(native, 'PROJECT', self.root), \
                patch.object(native, 'check_access', return_value=True), \
                patch.object(native.subprocess, 'run') as run, \
                patch.object(native.subprocess, 'check_output', return_value='123;cluster\n') as submit:
            native.claim('one', value)
            native.save(self.root / 'runs/one/input/sources.json', dict(source_sha='c' * 40))
            request = native.submit('one', value)
            self.assertEqual(native.submit('one', value), request)
            submit.assert_called_once()
            self.assertIn('--hold', submit.call_args.args[0])
            self.assertEqual(run.call_args.args[0], ['scontrol', 'release', '123'])
            script = (self.root / 'runs/one/job.sbatch').read_text()
            subprocess.run(['bash', '-n'], input=script, text=True, check=True)
            self.assertIn('--partition=DSPRHBM', script)
            self.assertNotIn('--gres', script)

    def test_ambiguous_submission_does_not_submit_twice(self):
        value = native.delivery(self.source, 'one')
        with patch.object(native, 'ROOT', self.root), patch.object(native, 'PROJECT', self.root), \
                patch.object(native, 'check_access', return_value=True), \
                patch.object(native.subprocess, 'run'), \
                patch.object(native.subprocess, 'check_output', side_effect=OSError('lost sbatch reply')) as submit:
            native.claim('one', value)
            native.save(self.root / 'runs/one/input/sources.json', dict(source_sha='c' * 40))
            with self.assertRaises(OSError):
                native.submit('one', value)
            with self.assertRaisesRegex(ValueError, 'recover the held Slurm handle'):
                native.submit('one', value)
            self.assertEqual(submit.call_count, 1)

    def test_gpu_requests_follow_site_policy_and_cpu_evidence_cannot_be_adopted(self):
        settings = copy.deepcopy(native.site())
        for target in ('4v100-avx512', '16v100-avx2', '8v100v0-avx512'):
            settings['targets'][target]['enabled'] = True
        with patch('spingnn_config.site', return_value=settings):
            for target, partition, qos in (('4v100-avx512', '4V100', 'rush-1o2gpu'),
                                           ('16v100-avx2', '16V100', 'flood-1o2gpu'),
                                           ('8v100v0-avx512', '8V100V0', 'rush-1o2gpu')):
                source = dict(self.source, target=target, source_ref='v2.0.0', source_version='v2.0.0',
                              source_sha='2f4e7ca93e64b7113df1a9e9eafae4c1dc8279ec')
                value = native.delivery(source, 'gpu')
                script = native.render('gpu', value)
                self.assertIn('--partition=' + partition, script)
                self.assertIn('--qos=' + qos, script)
                self.assertIn('--gres=gpu:1', script)
                self.assertNotIn('--cpus', script)
                self.assertNotIn('--mem', script)
                with patch.object(native, 'read', return_value={'dsprhbm': {'qualified': True}}):
                    self.assertIsNone(native.bootstrap(value['identity']))
                self.assertIn('nvhpc26.3-gnu-cuda12-auto', native.module_text(value['identity'], self.root))
                self.assertIn('"cuda"', native.module_text(value['identity'], self.root))

    def test_pointer_does_not_change_on_failed_verification_or_older_completion(self):
        settings = dict(native.site(), root=str(self.root))
        records = {}
        for index, name in enumerate(('new', 'old')):
            directory = self.root / 'runs' / name
            directory.mkdir(parents=True)
            (directory / 'completion.json').write_text('{}\n')
            records[name] = (dict(delivery=native.delivery(self.source, name), created_ns=2-index),
                             dict(prefix=str(self.root / name)))
        with patch.object(native, 'ROOT', self.root), patch.object(native, 'site', return_value=settings), \
                patch.object(native, 'check_access', return_value=True), \
                patch.object(native, 'verified', side_effect=lambda name: records[name]) as verified:
            result = native.publish('new')
            current = self.root / 'release-targets/dsprhbm/current'
            selected = current.read_text()
            self.assertTrue(result['published'])
            self.assertFalse(native.publish('old')['current'])
            self.assertEqual(current.read_text(), selected)
            verified.side_effect = ValueError('scientific gate failed')
            with self.assertRaisesRegex(ValueError, 'scientific gate'):
                native.publish('old')
            self.assertEqual(current.read_text(), selected)

    def test_partition_publications_keep_independent_accepted_pointers(self):
        settings = copy.deepcopy(native.site())
        settings['root'] = str(self.root)
        settings['targets']['4v100-avx512']['enabled'] = True
        records = {}
        with patch('spingnn_config.site', return_value=settings):
            for name, target in (('cpu', 'dsprhbm'), ('gpu', '4v100-avx512')):
                directory = self.root / 'runs' / name
                directory.mkdir(parents=True)
                (directory / 'completion.json').write_text('{}\n')
                value = native.delivery(dict(self.source, target=target), name)
                records[name] = (dict(delivery=value, created_ns=1), dict(prefix=str(self.root / name)))
            with patch.object(native, 'ROOT', self.root), patch.object(native, 'site', return_value=settings), \
                    patch.object(native, 'check_access', return_value=True), \
                    patch.object(native, 'verified', side_effect=lambda name: records[name]) as verified:
                native.publish('cpu')
                cpu = self.root / 'release-targets/dsprhbm/current'
                original = cpu.read_bytes()
                native.publish('gpu')
                gpu = self.root / 'release-targets/4v100-avx512/current'
                self.assertEqual(cpu.read_bytes(), original)
                self.assertTrue(gpu.is_file())
                verified.side_effect = ValueError('failed new GPU science')
                with self.assertRaises(ValueError):
                    native.publish('gpu')
                self.assertEqual(cpu.read_bytes(), original)

    def test_science_cannot_pass_with_skipped_changed_or_nonfinite_results(self):
        prefix, case = self.root / 'prefix', self.root / 'case'
        for directory in (prefix / 'bin', prefix / 'lib', prefix / 'venv', case):
            directory.mkdir(parents=True)
        for name in ('bin/lmp_spingnn2', 'lib/libspingnn2.so.1'):
            (prefix / name).write_bytes(b'qualified test bytes')
        row = dict(variant='baseline', exitcode=0, max_force=0.4, max_spin_force=0.2,
                   native_errors={key: 1e-16 for key in native.FIELDS},
                   oracle_errors={key: 0 for key in native.FIELDS},
                   lammps_errors={key: 1e-16 for key in ('energy_ev', 'force_ev_angstrom', 'spin_force')})
        value = dict(passed=True, backend='cpu', partition='DSPRHBM', job_id='123',
                     torch_imported=False, python=str(prefix / 'venv/bin/python'),
                     environment=dict(MPI_HOME=native.target_policy('dsprhbm')['mpi_prefix']),
                     spingnn2_file=str(prefix / 'venv/lib/spingnn2/__init__.py'),
                     binary_sha256=native.checksum(prefix / 'bin/lmp_spingnn2'),
                     library_sha256=native.checksum(prefix / 'lib/libspingnn2.so.1'),
                     tolerances=dict(native_fp64=1e-10, lammps_fp64=1e-11),
                     rows=[row, dict(copy.deepcopy(row), variant='rotated')], spin_rotation_energy_change_ev=0.003)
        names = ('model.sp2.json', 'model.sp2.bin', 'graph.npz', 'graph-rotated.npz',
                 'reference.npz', 'reference-rotated.npz', 'eager.npz')
        for name in (*names, 'kernel.so'):
            (case / name).write_bytes(b'test fixture')
        fixture = dict(source_commit='c' * 40, trained_potential=False, seed=1793,
                       oracle_vs_eager_max_abs={key: 0 for key in native.FIELDS},
                       hashes={name: native.checksum(case / name) for name in names},
                       spin_rotation_energy_change_ev=0.003)
        (case / 'artifact.json').write_text(json.dumps(dict(backend='cpu', precision='float64', gpu_arch='',
            contract_sha256=fixture['hashes']['model.sp2.json'], artifact='kernel.so',
            artifact_sha256=native.checksum(case / 'kernel.so'))))
        (case / 'fixture.json').write_text(json.dumps(fixture))
        identity = native.delivery(self.source, 'one')['identity']
        def verify(record):
            (case / 'acceptance.json').write_text(json.dumps(record))
            return native.science(case, identity, prefix, '123')
        verify(value)
        for field, replacement in (('passed', False), ('rows', value['rows'][:1]),
                                   ('backend', 'cuda'), ('partition', '4V100'),
                                   ('environment', dict(MPI_HOME='/other/mpi')),
                                   ('python', '/usr/bin/python'), ('library_sha256', '0' * 64),
                                   ('spin_rotation_energy_change_ev', 0)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify(dict(value, **{field: replacement}))
        for error in (float('nan'), -1, 1e-10):
            changed = copy.deepcopy(value)
            changed['rows'][0]['native_errors']['forces'] = error
            with self.subTest(error=error), self.assertRaises(ValueError):
                verify(changed)


if __name__ == '__main__':
    unittest.main()
