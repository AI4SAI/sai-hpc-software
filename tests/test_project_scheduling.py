"""New jobs use project billing; home-backed frozen controllers retain their paths."""
from argparse import Namespace
from contextlib import ExitStack
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'controller'))
import abacus_benchmark
import cp2k_acceptance
import cp2k_dependency_stage
import gpu_feature_controller
import md_acceptance_controller
import md_controller
import md_lammps_standalone
import md_performance_controller
import runtime_controller
import software_controller
from delivery_layout import artifact_path
from release_contract import make_identity
from remote_controller import SAI_PROJECT_ACCOUNT, SAI_PROJECT_ROOT, TARGETS
from test_md_controller import delivery_request

HOME_ROOT = Path('/home/stardust/sai-hpc-software')
GPU_TARGETS = ('4v100-avx512', '16v100-avx2', '8v100v0-avx512')
ALL_TARGETS = ('dsprhbm', *GPU_TARGETS)
RECIPE = 'b' * 64


def identity(software, target):
    return make_identity(software, 'development', 'develop' if software == 'abacus' else 'master',
                         'a' * 40, 'v1', RECIPE, target)


class ProjectSchedulingTests(unittest.TestCase):
    def check_science_qos(self, script, root, target):
        if target == 'dsprhbm':
            expected = 'rush-cpu'
        elif root == SAI_PROJECT_ROOT and target in ('4v100-avx512', '8v100v0-avx512'):
            expected = 'rush-1o2gpu'
        else:
            expected = 'flood-1o2gpu'
        self.assertEqual([line for line in script.splitlines() if line.startswith('#SBATCH --qos=')],
                         ['#SBATCH --qos=' + expected])

    def check_script(self, script, root, task, target):
        directives = [line for line in script.splitlines() if line.startswith('#SBATCH ')]
        self.assertIn('#SBATCH --partition=' + TARGETS[target]['partition'], directives)
        account = [line for line in directives if line.startswith('#SBATCH --account=')]
        chdir = [line for line in directives if line.startswith('#SBATCH --chdir=')]
        if root == SAI_PROJECT_ROOT:
            self.assertEqual(account, ['#SBATCH --account=' + SAI_PROJECT_ACCOUNT])
            self.assertEqual(chdir, ['#SBATCH --chdir=' + str(task)])
        else:
            self.assertEqual(account, [])
            self.assertEqual(chdir, [])
            self.assertNotIn(str(SAI_PROJECT_ROOT), script)
        self.assertIn(str(task), script)
        subprocess.run(['bash', '-n'], input=script, text=True, check=True)

    def test_abacus_and_cp2k_application_builds(self):
        for root in (SAI_PROJECT_ROOT, HOME_ROOT):
            with patch.multiple(software_controller, ROOT=root, CONTROL=root / 'controller/frozen'), \
                    patch.object(software_controller, 'recipe_fingerprint', return_value=RECIPE):
                for software in ('abacus', 'cp2k'):
                    for target in ALL_TARGETS:
                        with self.subTest(root=root, software=software, target=target):
                            record = identity(software, target)
                            args = Namespace(run_id='build', software=software, target=target,
                                             sha=record['source_sha'], version='v1', track='development',
                                             source_ref=record['source_ref'], identity=record,
                                             jobs=8, minutes=240, overlay_mb=32768)
                            script = software_controller.render_job(args)
                            self.check_script(script, root, root / 'runs/build', target)

    def test_abacus_runtime_gpu_features_and_benchmarks(self):
        for root in (SAI_PROJECT_ROOT, HOME_ROOT):
            with ExitStack() as stack:
                for module in (runtime_controller, gpu_feature_controller, abacus_benchmark):
                    stack.enter_context(patch.object(module, 'ROOT', root))
                for target in ALL_TARGETS:
                    record = identity('abacus', target)
                    common = dict(run_id='science', identity=record, target=target, version='v1',
                                  artifact=str(artifact_path(root, record, 'build')),
                                  launcher=str(root / 'controller/frozen/abacus'), nodes=2, minutes=30)
                    with self.subTest(root=root, target=target, stage='runtime'):
                        script = runtime_controller.render_job(Namespace(**common))
                        self.check_script(script, root, root / 'runtime-tests/science', target)
                        self.check_science_qos(script, root, target)
                    if target in GPU_TARGETS:
                        with self.subTest(root=root, target=target, stage='GPU features'):
                            script = gpu_feature_controller.render_job(Namespace(**common, gpus_per_node=1))
                            self.check_script(script, root, root / 'runtime-tests/science', target)
                            self.check_science_qos(script, root, target)
                    cpu = target == 'dsprhbm'
                    request = dict(common, ranks=8 if cpu else 2, ranks_per_node=4 if cpu else 1,
                                   threads=2 if cpu else 1, dependency_isa=TARGETS[target]['dependency_isa'],
                                   system_module=abacus_benchmark.SYSTEM_MODULE,
                                   artifact_sha256='c' * 64, launcher_sha256='d' * 64,
                                   runs=[dict(arm='candidate', id='m000-candidate')])
                    task = root / 'runtime-tests/benchmark-science'
                    with self.subTest(root=root, target=target, stage='benchmark'):
                        script = abacus_benchmark.render_job(request, task)
                        self.check_script(script, root, task, target)
                        self.check_science_qos(script, root, target)

    def test_cp2k_dependencies_and_science(self):
        for root in (SAI_PROJECT_ROOT, HOME_ROOT):
            control = root / 'controller/frozen'
            with patch.object(cp2k_acceptance, 'ROOT', root):
                for target in ALL_TARGETS:
                    record = identity('cp2k', target)
                    task = root / 'runs/dependencies'
                    with self.subTest(root=root, target=target, stage='dependencies'):
                        script = cp2k_dependency_stage.render(root, control, task, record)
                        self.check_script(script, root, task, target)
                    request = dict(software='cp2k', identity=record, install_prefix=record['install_prefix'],
                                   version='v1', target=target, source_sha=record['source_sha'],
                                   recipe_sha256=RECIPE, build_run_id='build', run_id='science',
                                   artifact=str(artifact_path(root, record, 'build')),
                                   launcher=str(control / 'cp2k'), minutes=60)
                    with self.subTest(root=root, target=target, stage='science'):
                        script = cp2k_acceptance.render_job(Namespace(**request))
                        self.check_script(script, root, root / 'runtime-tests/science', target)
                        self.check_science_qos(script, root, target)

    def test_md_build_stages_and_scientific_performance_acceptance(self):
        for root in (SAI_PROJECT_ROOT, HOME_ROOT):
            mdroot = root / 'experimental/deepmd-lammps'
            with ExitStack() as stack:
                for module in (md_controller, md_acceptance_controller, md_performance_controller):
                    stack.enter_context(patch.multiple(module, PROJECT=root, ROOT=mdroot))
                for target in GPU_TARGETS:
                    delivery = delivery_request(target)
                    for stage in ('all', 'deepmd', 'lammps'):
                        with self.subTest(root=root, target=target, stage=stage):
                            dependency = '12345' if stage == 'lammps' else None
                            script = md_controller.render(delivery, 'build', stage=stage, dependency=dependency)
                            self.check_script(script, root, mdroot / 'runs/build', target)
                            if dependency:
                                self.assertIn('#SBATCH --dependency=afterok:12345,singleton', script)
                    request = dict(run_id='science', build_run='build', target=target,
                                   nodes=2, ranks=4, delivery=delivery, sources=delivery['plan']['sources'],
                                   version=delivery['plan']['version'],
                                   artifact=str(md_controller.artifact_path(mdroot, delivery, 'build')))
                    task = mdroot / 'runtime-tests/science'
                    with self.subTest(root=root, target=target, stage='science'):
                        self.check_script(md_acceptance_controller.render(request, task), root, task, target)
                    request.update(run_id='performance', science_run='science', performance_scope='gpu',
                                   resources=md_performance_controller.RESOURCES)
                    task = mdroot / 'runtime-tests/performance'
                    with self.subTest(root=root, target=target, stage='performance'):
                        self.check_script(md_performance_controller.render(request, task), root, task, target)

    def test_standalone_lammps_build(self):
        for root in (SAI_PROJECT_ROOT, HOME_ROOT):
            mdroot = root / 'experimental/deepmd-lammps'
            with patch.multiple(md_controller, PROJECT=root, ROOT=mdroot), \
                    patch.multiple(md_lammps_standalone, PROJECT=root, ROOT=mdroot):
                for target in GPU_TARGETS:
                    delivery = md_lammps_standalone.make_delivery(
                        dict(track='development', source_ref='develop', source_sha='a' * 40,
                             source_version='develop'), target)
                    with self.subTest(root=root, target=target):
                        script = md_lammps_standalone.render(delivery, 'build')
                        self.check_script(script, root, mdroot / 'runs/build', target)


if __name__ == '__main__':
    unittest.main()
