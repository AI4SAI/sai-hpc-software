import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
from cp2k_spack_environment import environment


class SpackEnvironmentTests(unittest.TestCase):
    def env(self, partition='16V100', cpu='zen3', **kwargs):
        return environment(partition, '/opt/software/cp2k/development/test/' + partition,
                           cpu, 'ubuntu24.04', **kwargs)['spack']

    def test_gpu_dependencies_preserve_all_replacements(self):
        env = self.env()
        self.assertEqual(len(env['specs']), 7)
        self.assertEqual({s.split('@')[0] for s in env['specs']},
                         {'libint', 'libxsmm', 'libvori', 'hdf5', 'plumed', 'cosma', 'spla'})
        self.assertIn('hdf5@1.14.6 +mpi +fortran ~hl ~cxx ~shared', env['specs'])
        self.assertTrue(all('+cuda' in s for s in env['specs'] if s.startswith(('cosma@', 'spla@'))))
        self.assertEqual(env['packages']['tiled-mm']['require'], 'cuda_arch=70')

    def test_cpu_does_not_claim_site_elpa_is_cuda_free(self):
        env = self.env('DSPRHBM', 'sapphirerapids')
        self.assertTrue(all('~cuda' in s for s in env['specs'] if s.startswith(('cosma@', 'spla@'))))
        self.assertNotIn('elpa', env['packages'])
        self.assertNotIn('cp2k', env['packages'])
        self.assertNotIn('tiled-mm', env['packages'])

    def test_install_stage_and_repos_are_overlay_paths(self):
        env = self.env()
        self.assertEqual(env['config']['install_tree']['root'],
                         '/opt/software/cp2k/development/test/16V100/dependencies/spack')
        self.assertEqual(env['config']['build_stage'], ['/workspace/spack-stage'])
        self.assertEqual(env['repos']['builtin'], '/workspace/spack-packages/repos/spack_repo/builtin')
        self.assertFalse(env['view'])
        self.assertTrue(env['config']['checksum'])

    def test_external_dependencies_never_borrow_old_cp2k(self):
        for partition, isa in [('16V100', 'avx2'), ('DSPRHBM', 'avx512'),
                               ('4V100', 'avx512'), ('8V100V0', 'avx2')]:
            env = self.env(partition)
            self.assertNotIn('/opt/apps', json.dumps(env))
            for name in ('openmpi', 'openblas'):
                self.assertFalse(env['packages'][name]['buildable'])
                self.assertTrue(env['packages'][name]['externals'][0]['prefix'].endswith('-' + isa))

    def test_spack_12_language_provider_constraints_and_native_flags(self):
        packages = self.env()['packages']
        for name in ('c', 'cxx', 'fortran'):
            self.assertEqual(packages[name]['require'], 'gcc@13.3.0')
        self.assertNotIn('%gcc@13.3.0', packages['all']['require'])
        compiler = packages['gcc']['externals'][0]['extra_attributes']
        for name in ('cflags', 'cxxflags', 'fflags'):
            self.assertEqual(compiler['flags'][name], '-O3 -march=native -mtune=native')

    def test_no_public_mirror_bootstrap_or_unvalidated_binary_reuse(self):
        env = self.env()
        self.assertFalse(env['bootstrap']['enable'])
        self.assertFalse(env['concretizer']['reuse'])
        self.assertTrue(env['concretizer']['targets']['host_compatible'])
        self.assertEqual(env['mirrors:'], {'sai-sources': {
            'url': 'file:///input/spack/sources', 'source': True, 'binary': False}})

    def test_architecture_must_be_explicit_and_prefix_partition_matched(self):
        for cpu in ('native', 'x86_64', 'x86_64_v3', 'zen3 +cuda', '../zen3', ''):
            with self.subTest(cpu=cpu), self.assertRaises(ValueError):
                self.env(cpu=cpu)
        with self.assertRaises(ValueError):
            environment('16V100', '/opt/software/cp2k/development/test/DSPRHBM', 'zen3', 'ubuntu24.04')
        for jobs in (0, 65):
            with self.assertRaises(ValueError):
                self.env(jobs=jobs)


if __name__ == '__main__':
    unittest.main()
