"""Daily publication keeps accepted aliases until complete numerical checks pass."""
import copy
from contextlib import ExitStack
import json
import os
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

    def test_initial_application_missing_alias_is_allowed_but_broken_pointer_is_not(self):
        root, modules = self.root / 'beta', self.root / 'modules'
        with patch.object(daily, 'MODULES', modules), patch.object(daily, 'ROOT', root):
            self.assertIsNone(daily.current('qe', allow_missing=True))
            with self.assertRaisesRegex(ValueError, 'missing beta'):
                daily.current('qe')
            path = modules / 'qe' / sif_beta.channel('qe')
            path.parent.mkdir(parents=True)
            path.symlink_to('missing-generation')
            with self.assertRaises((ValueError, FileNotFoundError)):
                daily.current('qe', allow_missing=True)

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

    def test_native_ecc_probe_is_bound_to_the_failed_job_and_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'control').mkdir()
            helper = directory / 'control/gpu_health.py'
            helper.write_bytes((REPO / 'controller/gpu_health.py').read_bytes())
            daily.save(directory / 'request.json', dict(files={'control/gpu_health.py': digest(helper)}))
            row = ['123', 'FAILED', '74:0', '8V100V0', 'proj-sai-hpc-software', tmp, '8v100v0n09']
            report = dict(schema=1, healthy=False, job='123', partition='8V100V0',
                          workdir=tmp, node='8v100v0n09', probe_sha256=digest(helper),
                          devices=[dict(set_device=dict(code=214))])
            daily.save(directory / 'gpu-health.json', report)
            self.assertEqual(daily.retry_node(directory, row), '8v100v0n09')
            for key, value in (('job', '122'), ('node', '8v100v0n08'), ('workdir', '/elsewhere'),
                               ('partition', '16V100'), ('probe_sha256', 'a' * 64)):
                daily.save(directory / 'gpu-health.json', dict(report, **{key: value}))
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'allocation'):
                    daily.retry_node(directory, row)
            for code in (0, 35, 100, 999):
                daily.save(directory / 'gpu-health.json', dict(report,
                           devices=[dict(set_device=dict(code=code))]))
                self.assertIsNone(daily.retry_node(directory, row))

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
            snapshot = previous_tests.add_qe(snapshot)
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
                        self.assertIn('/usr/bin/python3 -B control/gpu_health.py > gpu-health.json', script)
                        request = json.loads((output / 'request.json').read_text())
                        self.assertEqual(request['files']['control/gpu_health.py'],
                                         digest(REPO / 'controller/gpu_health.py'))
                    else:
                        self.assertNotIn('gpu-health.json', script)
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
                    if software == 'qe':
                        self.assertIn('/usr/bin/bash ./rank.sh -ni 2', script)
                        request = json.loads((output / 'request.json').read_text())
                        for index in range(2):
                            self.assertEqual(request['files'][f'case/manypw/pw_{index}.in'],
                                             digest(output / 'case/scf.in'))
                        self.assertEqual(request['files']['case/manypw/rank.sh'],
                                         digest(output / 'case/manypw/rank.sh'))


class QeManypwTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.reference = dict(energy_ry=-15.84452726, energy_tolerance_ry=2e-5)
        self.launcher = ('path-images division:  nimage = 2\nGPU acceleration is ACTIVE\n'
                         '[1,0]<stderr>:launch CUDA kernel device=0\n'
                         '[1,1]<stderr>:launch CUDA kernel device=1\n')
        (self.root / 'stdout.txt').write_text(self.launcher)
        for index in range(2):
            rank = self.root / f'rank-output/rank.{index}'
            rank.mkdir(parents=True)
            (rank / 'stdout').write_text('path-images division: nimage = 2\nGPU acceleration is ACTIVE\n'
                                        if index == 0 else '')
            (rank / 'stderr').write_text(f'SAI_QE_MPI_RANK={index}\nlaunch CUDA kernel device={index}\n')
            (self.root / f'pw_{index}.out').write_text(
                f'Reading input from pw_{index}.in\nconvergence has been achieved\n'
                '! total energy = -15.84452726 Ry\n' + ('JOB DONE.\n' if index == 0 else ''))
            scratch = self.root / f'tmp_{index}/silicon.save'
            scratch.mkdir(parents=True)
            (scratch / 'data-file-schema.xml').write_text('<completed/>')

    def test_two_images_need_only_one_global_completion_marker(self):
        result = beta_check.qe_manypw(self.root, self.reference, gpu=True)
        self.assertTrue(result['passed'])
        self.assertEqual(result['gpu_devices_by_rank'], {'0': ['0'], '1': ['1']})
        self.assertEqual([r['scratch'] for r in result['images']], ['tmp_0', 'tmp_1'])

    def test_wrong_second_image_energy_is_rejected(self):
        output = self.root / 'pw_1.out'
        output.write_text(output.read_text().replace('-15.84452726', '-14.84452726'))
        with self.assertRaisesRegex(ValueError, 'image energy'):
            beta_check.qe_manypw(self.root, self.reference, gpu=False)

    def test_shared_scratch_or_shared_gpu_is_rejected(self):
        rank1 = self.root / 'rank-output/rank.1/stderr'
        rank1.write_text('SAI_QE_MPI_RANK=1\nlaunch CUDA kernel device=0\n')
        with self.assertRaisesRegex(ValueError, 'separate GPU'):
            beta_check.qe_manypw(self.root, self.reference, gpu=True)
        rank1.write_text('SAI_QE_MPI_RANK=1\nlaunch CUDA kernel device=1\n')
        shutil.rmtree(self.root / 'tmp_1')
        (self.root / 'tmp_1').symlink_to('tmp_0')
        with self.assertRaisesRegex(ValueError, 'independent completed scratch'):
            beta_check.qe_manypw(self.root, self.reference, gpu=False)

    def test_missing_global_completion_is_rejected(self):
        output = self.root / 'pw_0.out'
        output.write_text(output.read_text().replace('JOB DONE.', ''))
        with self.assertRaisesRegex(ValueError, 'did not finish'):
            beta_check.qe_manypw(self.root, self.reference, gpu=False)

    def test_interleaved_forwarded_bytes_do_not_change_rank_device_evidence(self):
        # The failed real allocation had only one MPI tag on each corrupt
        # line: a non-greedy tag regex cannot recover the source rank.
        mixed = ('[1,0]<stderr>: launch CUDA kernel file=force_us.f90 '
                 'function=force_us line=123 device=1 threadid=1 num_gangs=1\n'
                 '[1,1]<stderr>: launch CUDA kernel device=0 num_wolaunch CUDA kernel device=1\n')
        (self.root / 'stdout.txt').write_text(mixed)
        result = beta_check.qe_manypw(self.root, self.reference, gpu=True)
        self.assertEqual(result['gpu_devices_by_rank'], {'0': ['0'], '1': ['1']})
        for rank in ('0', '1'):
            path = self.root / f'rank-output/rank.{rank}/stderr'
            self.assertEqual(result['rank_output'][rank]['stderr']['sha256'], digest(path))
            self.assertEqual(result['rank_output'][rank]['cuda_kernel_launches'], 1)

    def test_independent_stream_with_wrong_or_malformed_device_is_rejected(self):
        path = self.root / 'rank-output/rank.0/stderr'
        for text, reason in [('launch CUDA kernel device=0\nlaunch CUDA kernel device=1\n', 'separate GPU'),
                             ('launch CUDA kernel device=0 num_wolaunch CUDA kernel device=1\n', 'malformed'),
                             ('launch CUDA kernel device=\n', 'malformed'), ('', 'separate GPU')]:
            with self.subTest(text=text):
                path.write_text('SAI_QE_MPI_RANK=0\n' + text)
                with self.assertRaisesRegex(ValueError, reason):
                    beta_check.qe_manypw(self.root, self.reference, gpu=True)

    def test_rank_outputs_cannot_be_missing_duplicated_or_redirected(self):
        path = self.root / 'rank-output/rank.1/stderr'
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'rank stream'):
            beta_check.qe_manypw(self.root, self.reference, gpu=True)
        path.symlink_to('../rank.0/stderr')
        with self.assertRaisesRegex(ValueError, 'rank stream'):
            beta_check.qe_manypw(self.root, self.reference, gpu=True)
        path.unlink(); path.write_text('SAI_QE_MPI_RANK=0\nlaunch CUDA kernel device=1\n')
        with self.assertRaisesRegex(ValueError, 'identity differs'):
            beta_check.qe_manypw(self.root, self.reference, gpu=True)
        path.write_text('SAI_QE_MPI_RANK=1\nlaunch CUDA kernel device=1\n')
        (self.root / 'rank-output/rank.2').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'exactly two'):
            beta_check.qe_manypw(self.root, self.reference, gpu=True)


class QeRankCaptureTests(unittest.TestCase):
    def test_real_rank_shell_keeps_concurrent_streams_and_exit_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle, fixtures = root / 'bundle', root / 'fixtures'
            bundle.mkdir(); (fixtures / 'qe').mkdir(parents=True)
            (bundle / 'catalog.json').write_text(json.dumps(dict(runtime_sha256='a' * 64,
                applications={'qe': {'partitions': {'DSPRHBM': {'identity': {'install_prefix': '/accepted/qe'}}}}})))
            (fixtures / 'references.json').write_text('{"qe": {}}')
            (fixtures / 'qe/scf.in').write_text('input')
            (fixtures / 'qe/Si.pz-vbc.UPF').write_text('pseudo')
            output = root / 'check'
            beta_check.prepare('qe', 'DSPRHBM', bundle, output, fixtures)
            case = output / 'case/manypw'
            programs = root / 'bin'; programs.mkdir()
            executable = programs / 'manypw.x'
            executable.write_text('#!/usr/bin/env python3\nimport os,sys\n'
                'rank=os.environ["OMPI_COMM_WORLD_RANK"]\n'
                'assert sys.argv[1:] == ["-ni", "2"]\n'
                'for index in range(64):\n'
                ' os.write(1, (rank * 8192 + " stdout\\n").encode())\n'
                ' os.write(2, (rank * 8192 + " stderr\\n").encode())\n'
                'sys.exit(7 if rank == "1" else 0)\n')
            executable.chmod(0o755)
            processes = [subprocess.Popen(['/usr/bin/bash', './rank.sh', '-ni', '2'], cwd=case,
                env=dict(os.environ, PATH=str(programs) + ':' + os.environ['PATH'], OMPI_COMM_WORLD_RANK=str(rank)),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for rank in (0, 1)]
            for rank, process in enumerate(processes):
                stdout, stderr = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 7 if rank == 1 else 0)
                self.assertEqual((stdout, stderr), ('', ''))
                streams = case / f'rank-output/rank.{rank}'
                self.assertEqual((streams / 'stdout').read_text(), (str(rank) * 8192 + ' stdout\n') * 64)
                self.assertEqual((streams / 'stderr').read_text(), f'SAI_QE_MPI_RANK={rank}\n'
                                 + (str(rank) * 8192 + ' stderr\n') * 64)


class WorkflowTests(unittest.TestCase):
    def test_all_daily_software_publish_independently_and_do_not_mask_failure(self):
        for filename, software in [('build.yml','abacus'), ('cp2k.yml','cp2k'), ('deepmd.yml','deepmd-kit'), ('lammps.yml','lammps'), ('gpumd.yml','gpumd'), ('qe.yml','qe')]:
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
        self.assertEqual(set(workflow[True]['workflow_dispatch']['inputs']['software']['options']), set(SOFTWARE))


if __name__ == '__main__':
    unittest.main()
