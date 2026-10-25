"""Daily publication keeps accepted aliases until complete numerical checks pass."""
import copy
from contextlib import ExitStack
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'controller'))
import beta_candidates as candidates
import beta_check
import beta_ci
import daily_beta as daily
import sif_beta
import sif_modules
from release_contract import SOFTWARE, PARTITIONS, resolve_tracks
from sif_runtime import digest
import test_sif_beta as previous_tests


def builds(software):
    source = resolve_tracks(software, ['development'], resolver=lambda repo, ref:
        dict(sha='a' * 40, ref=ref, version=ref + '-2026-10-25'))[0]
    return [dict(source, target=target) for target in SOFTWARE[software]['targets']]


class CandidateTests(unittest.TestCase):
    def test_rebuild_pointer_binds_both_requests_and_preserves_source_target(self):
        from release_contract import make_identity
        with tempfile.TemporaryDirectory() as temporary, patch.object(candidates, 'PROJECT', Path(temporary)):
            root = Path(temporary)
            original, recovered = root / 'runs/original', root / 'runs/recovered'
            for directory in (original, recovered):
                (directory / 'results').mkdir(parents=True)
            before = make_identity('cp2k', 'development', 'master', 'a' * 40, 'master-2026-10-25', 'b' * 64, 'dsprhbm')
            after = make_identity('cp2k', 'development', 'master', 'a' * 40, 'master-2026-10-25', 'c' * 64, 'dsprhbm')
            (original / 'request.json').write_text(json.dumps(dict(identity=before)))
            (recovered / 'request.json').write_text(json.dumps(dict(identity=after)))
            proof = original / 'results/build-recovery.json'
            record = dict(schema=1, source_run=original.name, run_id=recovered.name,
                          source_request_sha256=digest(original / 'request.json'),
                          request_sha256=digest(recovered / 'request.json'))
            proof.write_text(json.dumps(record))
            selected, extra = candidates.build_recovery('cp2k', original)
            self.assertEqual(selected, recovered)
            self.assertEqual(extra['build_recovery']['evidence_sha256'], digest(proof))
            # Changed recovered bytes fail even with the same nominal source.
            (recovered / 'request.json').write_text(json.dumps(dict(identity=before)))
            with self.assertRaisesRegex(ValueError, 'changed recovery request'):
                candidates.build_recovery('cp2k', original)
            after = make_identity('cp2k', 'development', 'master', 'd' * 40, 'master-2026-10-25', 'c' * 64, 'dsprhbm')
            (recovered / 'request.json').write_text(json.dumps(dict(identity=after)))
            record['request_sha256'] = digest(recovered / 'request.json')
            proof.write_text(json.dumps(record))
            with self.assertRaisesRegex(ValueError, 'selected source'):
                candidates.build_recovery('cp2k', original)
            (recovered / 'results/build-recovery.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'chained'):
                candidates.build_recovery('cp2k', original)

    def test_complete_matrix_and_identity_are_required(self):
        for software in SOFTWARE:
            rows = builds(software)
            self.assertEqual(candidates.validate_plan(software, rows), rows)
            for bad in (rows[:-1], rows + [rows[0]], [dict(rows[0], source_sha='b' * 40), *rows[1:]],
                        [dict(row, repository='other/repo') for row in rows]):
                with self.subTest(software=software), self.assertRaises(ValueError):
                    candidates.validate_plan(software, bad)

    def test_failed_jobs_rerun_locates_latest_eligible_original_attempt(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(candidates, 'PROJECT', Path(tmp)):
            root = Path(tmp) / 'runs'
            root.mkdir()
            row = builds('cp2k')[0]
            for attempt in (1, 3):
                (root / f'123-{attempt}-development-{row["target"]}-2026-10-25-{row["source_sha"][:12]}').mkdir()
            self.assertIn('123-1-', candidates.locate('cp2k', '123', '2', row).name)
            self.assertIn('123-3-', candidates.locate('cp2k', '123', '3', row).name)
            with self.assertRaises(ValueError):
                candidates.locate('cp2k', '123/escape', '3', row)

    def test_changed_or_writable_image_cannot_become_a_candidate(self):
        source = builds('deepmd-kit')[0]
        identity = __import__('deepmd_standalone').make_delivery(source, source['target'])['identity']
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'runtime').mkdir()
            (root / 'results').mkdir()
            (root / 'request.json').write_text(json.dumps(dict(controller=str(root / 'controller/frozen'))))
            (root / 'runtime/runtime-env.sh').write_text('export TEST=1\n')
            (root / 'results/status.json').write_text('{}')
            image = root / 'candidate.sif'
            image.write_bytes(b'sealed SIF')
            record = dict(build_verified=True, scientific_verified=True, delivery=dict(identity=identity), artifact_sha256=digest(image))
            with patch.object(candidates, 'run_controller', return_value=record):
                with self.assertRaisesRegex(ValueError, 'writable'):
                    candidates.candidate('deepmd-kit', root, source)
                image.chmod(0o444)
                self.assertEqual(candidates.candidate('deepmd-kit', root, source)['sha256'], record['artifact_sha256'])
                record['artifact_sha256'] = '0' * 64
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    candidates.candidate('deepmd-kit', root, source)


class PromotionTests(unittest.TestCase):
    setUp = previous_tests.SifBetaTests.setUp
    fake_bundle = previous_tests.SifBetaTests.fake_bundle

    def test_interrupted_generation_prepare_can_resume_but_changed_bytes_fail(self):
        bundle, catalog, checks = self.fake_bundle()
        root, modules = self.root / 'beta', self.root / 'modules'
        original = sif_beta.write
        calls = 0
        def interrupted(path, content, *args):
            nonlocal calls
            calls += 1
            original(path, content, *args)
            if calls == 2:
                raise ConnectionError('lost writer')
        with patch.object(sif_beta, 'write', side_effect=interrupted), self.assertRaises(ConnectionError):
            sif_beta.prepare(root, modules, 'one', bundle, catalog, checks, ['cp2k'], resume=True)
        self.assertFalse((modules / 'cp2k/master-beta-later').exists())
        sif_beta.prepare(root, modules, 'one', bundle, catalog, checks, ['cp2k'], resume=True)
        sif_beta.prepare(root, modules, 'one', bundle, catalog, checks, ['cp2k'], resume=True)
        sif_beta.select(root, 'one', ['cp2k'])
        module = root / 'generations/one/modules/cp2k'
        module.chmod(0o644)
        module.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'changed existing'):
            sif_beta.prepare(root, modules, 'one', bundle, catalog, checks, ['cp2k'], resume=True)

    def test_interrupted_image_copy_resumes_without_overwriting_sealed_bundle(self):
        bundle, catalog, _ = self.fake_bundle()
        snapshot = sif_modules.load_snapshot(catalog)
        snapshot['applications'] = {'gpumd': snapshot['applications']['gpumd']}
        destination = self.root / 'public-bundle'
        require = sif_modules.require
        def test_path(condition, message):
            if message == 'destination must be a new real home/project/opt directory':
                # Only permit this test's workspace in place of /home or /org.
                condition = destination.parent == self.root and not (destination / 'catalog.json').exists()
            require(condition, message)
        copyfile = sif_modules.shutil.copyfile
        def interrupted(source, target):
            Path(target).write_bytes(b'partial')
            raise ConnectionError('lost copy')
        with patch.object(sif_modules, 'require', side_effect=test_path):
            with patch.object(sif_modules.shutil, 'copyfile', side_effect=interrupted), self.assertRaises(ConnectionError):
                sif_modules.install(snapshot, destination, copy_images=True, resume=True)
            self.assertFalse((destination / 'catalog.json').exists())
            sif_modules.install(snapshot, destination, copy_images=True, resume=True)
            published = sif_modules.load_snapshot(destination / 'catalog.json')
            for row in published['applications']['gpumd']['partitions'].values():
                self.assertEqual(digest(Path(row['image'])), row['sha256'])
            self.assertFalse(list(destination.rglob('*.copying')))
            with self.assertRaisesRegex(ValueError, 'destination must'):
                sif_modules.install(snapshot, destination, copy_images=True, resume=True)

    def test_recipe_revision_keeps_same_day_versions_distinct(self):
        first = sif_beta.version_name('cp2k', self.snapshot)
        self.snapshot['applications']['cp2k']['build_revision'] = 'abc123abc123'
        self.assertEqual(sif_beta.version_name('cp2k', self.snapshot), first + '-babc123abc123')

    def test_scientific_failure_retains_alias_and_context_rejects_newer_selection(self):
        bundle, catalog, checks = self.fake_bundle()
        root, modules = self.root / 'beta', self.root / 'modules'
        sif_beta.prepare(root, modules, 'old', bundle, catalog, checks, ['cp2k'])
        sif_beta.select(root, 'old', ['cp2k'])
        task = self.root / 'task'
        task.mkdir()
        pointer = modules / 'cp2k/master-beta-later'
        previous = pointer.readlink()
        with patch.object(daily, 'ROOT', root), patch.object(daily, 'MODULES', modules), \
                patch.object(candidates, 'locate', return_value=self.root), \
                patch.object(candidates, 'candidate', side_effect=ValueError('science failed')):
            with self.assertRaisesRegex(ValueError, 'science failed'):
                daily.execute('cp2k', '123', '1', builds('cp2k'), task, self.root)
            self.assertEqual(pointer.readlink(), previous)
            request = daily.read(task / 'request.json')
            request['before']['generation'] = 'superseded'
            daily.save(task / 'request.json', request)
            with self.assertRaisesRegex(ValueError, 'public beta changed'):
                daily.execute('cp2k', '123', '1', builds('cp2k'), task, self.root)


class RuntimeTests(unittest.TestCase):
    def test_durable_held_submission_is_released_without_resubmission(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'job.sbatch').write_text('job')
            (directory / 'request.json').write_text('{}')
            saved = dict(job='123', request_sha256=digest(directory / 'request.json'), script_sha256=digest(directory / 'job.sbatch'))
            daily.save(directory / 'submission.json', saved)
            with patch.object(daily.subprocess, 'check_output', return_value='PENDING|JobHeldUser\n') as query, \
                    patch.object(daily.subprocess, 'run') as call:
                self.assertEqual(daily.submit(directory, 'cp2k', '16V100', directory, directory), saved)
                call.assert_called_once_with(['scontrol', 'release', '123'], check=True)
                self.assertEqual(query.call_count, 1)

    def test_infrastructure_retry_requires_recorded_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            row = ['123', 'FAILED', '1:0', '8V100V0', 'proj-sai-hpc-software', tmp, '8v100v0n09']
            for output in ('training/training.log', 'case/stdout.txt'):
                log = directory / output
                log.parent.mkdir()
                for error in ('loss is NaN', 'Error: Unable to get the user home directory'):
                    with self.subTest(output=output, error=error):
                        log.write_text(error)
                        self.assertIsNone(daily.retry_node(directory, row))
                for error in ('RuntimeError: CUDA error: uncorrectable ECC error encountered',
                              'container creation failed: mount hook function failure: fuse2fs failed to mount'):
                    with self.subTest(output=output, error=error):
                        log.write_text(error)
                        self.assertEqual(daily.retry_node(directory, row), '8v100v0n09')
                log.unlink()

    def test_transport_archive_has_every_fixture_and_rendered_jobs_are_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'payload.tar'
            beta_ci.package(archive, builds('deepmd-kit'))
            with tarfile.open(archive) as tar:
                tar.extractall(root / 'payload', filter='data')
            for path in (root / 'payload').rglob('*'):
                if path.is_file():
                    path.chmod(0o444)
            self.assertTrue((root / 'payload/fixtures/deepmd-kit/fixture.json').is_file())
            snapshot = json.loads((REPO / 'deployment/sif-development-20261024/snapshot.json').read_text())
            snapshot['runtime_sha256'] = digest(REPO / 'controller/sif_runtime.py')
            # Use the reviewed complete catalog used by the existing module tests.
            for software in SOFTWARE:
                for partition in snapshot['applications'][software]['partitions']:
                    bundle = root / 'bundle'
                    bundle.mkdir(exist_ok=True)
                    (bundle / 'catalog.json').write_text(json.dumps(snapshot))
                    output = root / (software + '-' + partition)
                    beta_check.prepare(software, partition, bundle, output, root / 'payload/fixtures')
                    script = beta_check.render(software, partition, bundle, output)
                    subprocess.run(['bash', '-n'], input=script, text=True, check=True)
                    if partition != 'DSPRHBM':
                        self.assertNotIn('--cpus-per-task', script)
                    self.assertIn('--account=proj-sai-hpc-software', script)
                    if software == 'abacus' and partition == '16V100':
                        # Exercise the pre-module environment with the same NIL
                        # submission that exposed MPI's missing-home failure.
                        prefix = script.split('source /etc/profile.d/lmod.sh')[0]
                        environment = subprocess.check_output(['bash', '-c', prefix +
                            'printf "%s\\n" "$HOME" "$USER" "$LOGNAME"'], text=True,
                            env={'PATH': '/usr/bin:/bin', 'SLURM_JOB_USER': 'stardust'})
                        self.assertEqual(environment.splitlines(), [str(Path.home()), 'stardust', 'stardust'])
                    if software == 'deepmd-kit':
                        self.assertIn("deepmd-python -c", script)


class WorkflowTests(unittest.TestCase):
    def test_all_daily_software_publish_independently_and_do_not_mask_failure(self):
        for filename, software in [('build.yml','abacus'), ('cp2k.yml','cp2k'), ('deepmd.yml','deepmd-kit'), ('lammps.yml','lammps'), ('gpumd.yml','gpumd')]:
            workflow = yaml.safe_load((REPO / '.github/workflows' / filename).read_text())
            job = workflow['jobs']['beta']
            self.assertEqual(job['uses'], './.github/workflows/beta.yml')
            self.assertEqual(job['with']['software'], software)
            self.assertIn('!cancelled()', job['if'])
            self.assertNotIn("needs.build.result == 'success'", job['if'])
            if 'result' in workflow['jobs']:
                self.assertIn('beta', workflow['jobs']['result']['needs'])
        workflow = yaml.safe_load((REPO / '.github/workflows/beta.yml').read_text())
        self.assertFalse(workflow['concurrency']['cancel-in-progress'])


if __name__ == '__main__':
    unittest.main()
