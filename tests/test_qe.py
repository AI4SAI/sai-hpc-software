import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'controller'))
import qe_science as science
import qe_dependencies as dependencies
import release_contract as contract
import software_controller as controller
from daily_plan import plan
from ci import runtime_launchers
from sif_modules import render_module
from sif_runtime import MPI_PROGRAMS, PROGRAMS


SCF = '''GPU acceleration is ACTIVE.
launch CUDA kernel file=pw.f90 function=h_psi line=1 device=0
! total energy = -15.84452726 Ry
 convergence has been achieved in 6 iterations
 atom 1 type 1 force = 0.0 0.0 0.0
 atom 2 type 1 force = 0.0 0.0 0.0
 JOB DONE.
'''


class QETests(unittest.TestCase):
    def test_workflow_resolves_release_and_develop_and_skips_absent_prerelease(self):
        workflow = Path(__file__).resolve().parents[1]/'.github/workflows/qe.yml'
        snippet = textwrap.dedent(workflow.read_text().split("python3 - <<'PY'\n", 1)[1].split("\n          PY", 1)[0])
        def resolve(repository, ref):
            self.assertEqual(repository, 'QEF/q-e')
            if ref == 'latest-prerelease':
                raise ValueError('no latest-prerelease available')
            return dict(sha='a'*40, ref='qe-7.6' if ref == 'latest-release' else ref, version='qe-7.6')
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)/'output'
            env = dict(GITHUB_EVENT_NAME='schedule', GITHUB_OUTPUT=str(output),
                       TARGETS=','.join(contract.PARTITIONS), TRACKS='development,prerelease,release', SOURCE_REF='')
            with patch.dict(os.environ, env, clear=True), patch('resolve_source.resolve', side_effect=resolve), redirect_stdout(io.StringIO()):
                exec(compile(snippet, str(workflow), 'exec'), {})
            rows = json.loads(output.read_text().split('=', 1)[1])
            self.assertEqual(len(rows), 8)
            self.assertEqual({r['track'] for r in rows}, {'development', 'release'})
            self.assertEqual({r['target'] for r in rows}, set(contract.PARTITIONS))
            self.assertTrue(all(r['source_ref'] == ('develop' if r['track'] == 'development' else 'qe-7.6') for r in rows))
            env['SOURCE_REF'] = 'custom'
            with patch.dict(os.environ, env, clear=True), self.assertRaisesRegex(ValueError, 'exactly one'):
                exec(compile(snippet, str(workflow), 'exec'), {})

    def test_all_partition_channels_have_distinct_canonical_prefixes(self):
        self.assertEqual(set(plan('qe')['qe'].split(',')), set(contract.PARTITIONS))
        prefixes = set()
        for target in contract.PARTITIONS:
            for track in ('release', 'development'):
                identity = contract.make_identity('qe', track, 'qe-7.6' if track == 'release' else 'develop',
                    'a'*40, '7.6', controller.recipe_fingerprint('qe'), target)
                self.assertEqual(contract.validate_identity(identity), identity)
                prefixes.add(identity['install_prefix'])
        self.assertEqual(len(prefixes), 8)

    def test_scf_parser_rejects_missing_convergence_forces_nonfinite_and_duplicate_energy(self):
        self.assertAlmostEqual(science.parse_scf(SCF)['energy_ry'], -15.84452726)
        for text in (SCF.replace('JOB DONE.', ''), SCF.replace('convergence has been achieved', 'not converged'),
                     SCF.replace('atom 2', 'missing'), SCF.replace('-15.84452726', '1e999'),
                     SCF + '! total energy = -15.0 Ry\n'):
            with self.assertRaises(ValueError):
                science.parse_scf(text)

    def test_raw_science_requires_kernel_execution_and_checks_reference(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for rank in (1, 2):
                case = root/('mpi'+str(rank))
                science.prepare_case(case)
                (case/'pw.out').write_text(SCF)
            case = root/'mpi2'
            (case/'ph.out').write_text('\n'.join(f'freq ({i+1}) = 1.0 [THz] = {v} [cm-1]'
                for i, v in enumerate(science.fixture()['frequencies_cm1']))+'\nJOB DONE.\n')
            (case/'pp.out').write_text('JOB DONE.\n')
            (case/'rho.cube').write_text('density\ncube\n2 0 0 0\n1 1 0 0\n1 0 1 0\n1 0 0 1\n14 4 0 0 0\n14 4 1 1 1\n8\n')
            self.assertTrue(science.recheck(root, gpu=True)['gpu_executed'])
            (case/'pw.out').write_text(SCF.replace('launch CUDA kernel', 'library available'))
            with self.assertRaisesRegex(ValueError, 'kernel execution'):
                science.recheck(root, gpu=True)
            science.recheck(root, gpu=False)
            (case/'pw.out').write_text(SCF.replace('-15.84452726', '-15.8445'))
            with self.assertRaisesRegex(ValueError, 'reference'):
                science.recheck(root, gpu=False)
            (case/'pw.out').write_text(SCF)
            (case/'scf.in').write_text('different input')
            with self.assertRaisesRegex(ValueError, 'fixture changed'):
                science.recheck(root, gpu=False)

    def test_submodules_must_match_both_parent_gitlinks_and_records(self):
        def git(repository, *args):
            if args[0] == 'show':
                return '\n'.join('a'*40+' '+name for name in dependencies.REPOSITORIES)
            return '160000 commit '+'b'*40+'\t'+args[-1]
        with patch.object(dependencies, 'git', side_effect=git), self.assertRaisesRegex(ValueError, 'disagree'):
            dependencies.pins('/repo', 'c'*40)

    def test_jobs_use_managed_container_and_partition_local_builds(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(controller, 'ROOT', Path(temporary)):
            for target, partition in contract.PARTITIONS.items():
                args = argparse.Namespace(run_id='qe-test', software='qe', sha='a'*40, version='7.6',
                    target=target, track='release', source_ref='qe-7.6', jobs=6, minutes=120,
                    overlay_mb=16384, resume_run=None, dependencies_run=None)
                script = controller.render_job(args)
                self.assertIn('#SBATCH --partition='+partition, script)
                self.assertIn('/input/qe-dependencies:ro', script)
                self.assertIn('cp2k_image_driver.py', script)
                self.assertIn('overlay-after-export-fsck.txt', script)
                self.assertNotIn('build_capacity.py', script)
                self.assertEqual(controller.required_acceptance('qe', target), ('qe_science',))

    def test_qe_lmod_uses_matching_fortran_mpi_and_preserves_standard_command_names(self):
        identity = contract.make_identity('qe', 'release', 'qe-7.6', 'a'*40, '7.6', 'b'*64, '16v100-avx2')
        app = dict(version='7.6', partitions={'16V100':dict(identity=identity, image='/images/qe.sif')})
        module = render_module(Path('/bundle'), 'qe', app)
        self.assertIn('openmpi/5.0.10-nvhpc26.3-pgi-cuda12-auto', module)
        self.assertNotIn('-gnu-cuda12', module)
        self.assertIn('qe', MPI_PROGRAMS)
        self.assertTrue({'pw.x','ph.x','neb.x','pp.x','pwcond.x'} <= set(runtime_launchers('qe')))
        self.assertTrue(set(PROGRAMS['qe']) <= set(runtime_launchers('qe')))


if __name__ == '__main__':
    unittest.main()
