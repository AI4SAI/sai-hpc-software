"""Paired delivery publication cannot bypass raw evidence, export or pinned modules."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import md_publication as publication
from delivery_layout import PerformanceRegression
from module_publication import tcl
from source_cache import checksum
from test_md_controller import delivery_request


class MDPublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name) / 'project'
        self.root = self.project / 'experimental/deepmd-lammps'
        self.control = self.root / 'controller/snapshot'
        self.control.mkdir(parents=True)
        self.recipe = 'e' * 64
        (self.control / 'md_tracking.py').write_text('def fingerprint(path):\n    return ' + repr(self.recipe) + '\n')
        (self.control / 'md_runtime.sh').write_text(
            '#!/bin/bash\n'
            'python3 - "$@" <<\'PYCODE\'\n'
            'import json,os,sys\n'
            'print(json.dumps({"args":sys.argv[1:],"environment":dict(os.environ)}))\n'
            'PYCODE\n')
        for name, value in (('PROJECT', self.project), ('ROOT', self.root), ('CONTROL', self.control)):
            patcher = patch.object(publication, name, value)
            patcher.start(); self.addCleanup(patcher.stop)
        self.fingerprint = patch.object(publication, 'fingerprint', return_value=self.recipe).start()
        self.science = patch.object(publication, 'verify_science').start()
        self.performance = patch.object(publication, 'verify_performance').start()
        self.exporter = patch.object(publication, 'export_delivery', side_effect=self.fake_export).start()
        self.export_check = patch.object(publication, 'validate_export', side_effect=lambda record, *_: record).start()
        self.addCleanup(patch.stopall)
        self.prepare('build-one')

    def fake_export(self, image, digest, destination, identities):
        return dict(schema=1, directory=str(destination), image_sha256=digest,
                    identities=identities, roundtrip_verified=True, verified_files=3)

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True))

    def prepare(self, build_run, target='16v100-avx2'):
        self.build_run = build_run
        self.science_run, self.performance_run = build_run + '-science', build_run + '-performance'
        self.delivery = delivery_request(target, recipe=self.recipe)
        self.image = publication.artifact_path(self.root, self.delivery, build_run)
        self.image.parent.mkdir(parents=True, exist_ok=True)
        self.image.write_bytes(('paired SIF test stub ' + build_run).encode())
        self.digest = checksum(self.image)
        self.build_dir = self.root / 'runs' / build_run
        self.build_dir.mkdir(parents=True, exist_ok=True)
        (self.build_dir / 'job-deepmd.sbatch').write_text('DeepMD job\n')
        (self.build_dir / 'job.sbatch').write_text('LAMMPS job\n')
        self.request = dict(run_id=build_run, controller=str(self.control), delivery=self.delivery,
                            stage_job_script_sha256={
                                'deepmd': checksum(self.build_dir / 'job-deepmd.sbatch'),
                                'lammps': checksum(self.build_dir / 'job.sbatch')},
                            job_script_sha256=checksum(self.build_dir / 'job.sbatch'))
        self.write_json(self.build_dir / 'request.json', self.request)
        build_status = dict(state='COMPLETED', exit_code='0:0', build_verified=True,
                            delivery=self.delivery, artifact=str(self.image), artifact_sha256=self.digest)
        self.write_json(self.build_dir / 'results/status.json', build_status)
        self.write_json(self.image.with_suffix('.json'), build_status)
        self.bound = dict(build_run=build_run, delivery=self.delivery, artifact=str(self.image),
                          artifact_sha256=self.digest)
        for run_id in (self.science_run, self.performance_run):
            self.write_json(self.root / 'runtime-tests' / run_id / 'request.json',
                            dict(self.bound, run_id=run_id, nodes=2, ranks=4))
            self.write_json(self.root / 'runtime-tests' / run_id / 'results/status.json',
                            dict(state='COMPLETED', exit_code='0:0', scientific_verified=True,
                                 performance_verified=True))
        self.science.return_value = dict(self.bound, passed=True, files={'raw-science': 'a' * 64})
        self.performance.return_value = dict(self.bound, performance_verified=True,
                                             files={'raw-performance': 'b' * 64})

    def publish(self):
        return publication.publish(self.build_run, self.science_run, self.performance_run)

    def evaluate(self, metadata, *, partition=None, mode='load', saved=None):
        binary = shutil.which('tclsh')
        if not binary:
            self.skipTest('Tcl interpreter required')
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith('SAI_') and key != 'SLURM_JOB_PARTITION'}
        if partition:
            environment['SLURM_JOB_PARTITION'] = partition
        environment.update(saved or {})
        script = '\n'.join([
            f'set mode {tcl(mode)}',
            'proc module-info {kind arg} {global mode; return [expr {$kind eq "mode" && $arg eq $mode}]}',
            'proc module-whatis {args} {}', 'proc conflict {args} {}',
            'proc module {args} {}',
            'proc prepend-path {name value} {puts "PATH $name $value"}',
            'proc setenv {name value} {global env; set env($name) $value; puts "ENV $name $value"}',
            f'if {{[catch {{source {tcl(metadata["modulefile"])}}} reason]}} {{puts stderr $reason; exit 1}}'])
        return subprocess.run([binary], input=script, text=True, capture_output=True, env=environment)

    def test_publishes_both_modules_after_science_performance_and_export_then_revalidates(self):
        manifest = self.publish()
        self.assertTrue(manifest['published'])
        self.assertEqual(set(manifest['modules']), {'deepmd-kit', 'lammps'})
        self.assertEqual(publication.verify(self.build_run), manifest)
        self.assertEqual(self.science.call_count, 2)
        self.assertEqual(self.performance.call_count, 2)
        self.assertEqual(self.export_check.call_count, 2)
        for metadata in manifest['modules'].values():
            self.assertEqual(self.evaluate(metadata, partition='16V100').returncode, 0)
            self.assertNotEqual(self.evaluate(metadata).returncode, 0)
            self.assertNotEqual(self.evaluate(metadata, partition='DSPRHBM').returncode, 0)
        self.assertEqual(self.publish(), manifest)  # Same immutable evidence is idempotent.

    def test_raw_performance_regression_blocks_export_and_modules(self):
        self.performance.side_effect = PerformanceRegression('repeat still exceeds 10 percent')
        with self.assertRaises(PerformanceRegression):
            self.publish()
        self.exporter.assert_not_called()
        self.assertFalse((self.root / 'modulefiles').exists())
        self.assertFalse(self.image.with_suffix('.published.json').exists())

    def test_prepared_boolean_verdict_cannot_override_raw_verifier(self):
        self.science.side_effect = ValueError('corrupt raw forces')
        with self.assertRaisesRegex(ValueError, 'raw forces'):
            self.publish()
        self.exporter.assert_not_called()
        self.science.side_effect = None
        self.performance.return_value['artifact_sha256'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'another delivery'):
            self.publish()
        self.exporter.assert_not_called()

    def test_failed_export_roundtrip_cannot_expose_a_module(self):
        self.export_check.side_effect = ValueError('export payload byte changed')
        with self.assertRaisesRegex(ValueError, 'payload byte'):
            self.publish()
        self.assertFalse((self.root / 'modulefiles').exists())
        self.assertFalse(self.image.with_suffix('.published.json').exists())

    def test_publication_requires_two_node_science_and_matching_run_request(self):
        path = self.root / 'runtime-tests' / self.science_run / 'request.json'
        request = json.loads(path.read_text())
        for fields in ({'nodes': 1, 'ranks': 2}, {'build_run': 'other-build'}):
            self.write_json(path, dict(request, **fields))
            with self.assertRaises(ValueError):
                self.publish()
        self.exporter.assert_not_called()

    def test_changed_build_image_stage_script_or_controller_cannot_publish(self):
        self.image.write_bytes(b'changed SIF')
        with self.assertRaisesRegex(ValueError, 'provenance'):
            self.publish()
        self.prepare('build-new')
        (self.build_dir / 'job-deepmd.sbatch').write_text('replacement job')
        with self.assertRaisesRegex(ValueError, 'scripts'):
            self.publish()
        self.prepare('build-third')
        self.fingerprint.return_value = '0' * 64
        with self.assertRaisesRegex(ValueError, 'provenance'):
            self.publish()
        self.exporter.assert_not_called()

    def test_runtime_wrappers_pin_pair_image_control_and_strip_test_overrides(self):
        manifest = self.publish()
        environment = dict(os.environ, SAI_MD_IMAGE='/elsewhere.sif', SAI_MD_IMPLEMENTATION='baseline',
                           SAI_MD_TEST_CONTROL='/elsewhere', SAI_MD_PROFILE_LMP='bad',
                           SAI_MD_PERFORMANCE_CPU='33', SAI_MD_TRACE_RANKS='1')
        for software, metadata in manifest['modules'].items():
            for launcher in metadata['launchers']:
                result = subprocess.run([launcher, 'argument with spaces', '$literal'], env=environment,
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                output = json.loads(result.stdout)
                self.assertEqual(output['args'], [Path(launcher).name, 'argument with spaces', '$literal'])
                self.assertEqual(output['environment']['SAI_MD_IMAGE'], str(self.image))
                self.assertEqual(json.loads(output['environment']['SAI_MD_DELIVERY']), self.delivery)
                self.assertEqual(output['environment']['SAI_MD_PUBLISHED'], '1')
                self.assertNotIn('SAI_MD_IMPLEMENTATION', output['environment'])
                self.assertNotIn('SAI_MD_TEST_CONTROL', output['environment'])
        (self.control / 'md_tracking.py').write_text('def fingerprint(path):\n    return "changed"\n')
        launcher = next(iter(manifest['modules']['lammps']['launchers']))
        self.assertNotEqual(subprocess.run([launcher], capture_output=True).returncode, 0)

    def test_saved_module_unloads_original_image_after_new_target_or_run_publication(self):
        first = self.publish()
        original_image = self.image
        first_delivery = copy.deepcopy(self.delivery)
        self.prepare('build-two')
        second = self.publish()
        self.prepare('build-three', '4v100-avx512')
        third = self.publish()
        for software, metadata in first['modules'].items():
            identity = first_delivery['identities'][software]
            stem = 'SAI_' + software.upper().replace('-', '_') + '_MODULE_'
            saved = {stem + key: value for key, value in (
                ('TARGET', identity['target']), ('PARTITION', identity['partition']),
                ('TRACK', identity['track']), ('BUILD_ID', identity['build_id']), ('RUN', 'build-one'))}
            result = self.evaluate(metadata, partition='4V100', mode='remove', saved=saved)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(original_image), result.stdout)
            self.assertNotIn(second['artifact'], result.stdout)
            self.assertNotIn(third['artifact'], result.stdout)
        metadata = first['modules']['lammps']
        self.assertNotEqual(self.evaluate(metadata, partition='16V100',
                                         saved={'SAI_DEEPMD_KIT_STACK_DIGEST': 'wrong-pair'}).returncode, 0)

    def test_changed_evidence_module_wrapper_or_export_fails_publication_verification(self):
        manifest = self.publish()
        self.performance.return_value['files']['raw-performance'] = 'c' * 64
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            publication.verify(self.build_run)
        self.performance.return_value['files']['raw-performance'] = 'b' * 64
        self.export_check.side_effect = ValueError('export changed')
        with self.assertRaisesRegex(ValueError, 'export changed'):
            publication.verify(self.build_run)
        self.export_check.side_effect = lambda record, *_: record
        wrapper = Path(next(iter(manifest['modules']['lammps']['launchers'])))
        wrapper.chmod(0o755)
        wrapper.write_text('# changed wrapper\n')
        with self.assertRaisesRegex(ValueError, 'runtime wrapper changed'):
            publication.verify(self.build_run)

    def test_symlinked_acceptance_request_is_rejected(self):
        path = self.root / 'runtime-tests' / self.science_run / 'request.json'
        moved = path.with_name('elsewhere.json')
        path.rename(moved)
        path.symlink_to(moved.name)
        with self.assertRaisesRegex(ValueError, 'regular file'):
            self.publish()
        self.science.assert_not_called()


if __name__ == '__main__':
    unittest.main()
