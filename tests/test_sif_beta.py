import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'controller'))
import sif_beta as beta
from sif_modules import render_module
from sif_runtime import digest, PROGRAMS


class SifBetaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.snapshot = json.loads((REPO / 'deployment/sif-development-20261024/snapshot.json').read_text())

    def fragment(self, software, generation):
        path = self.root / 'generations' / generation / 'modules' / software
        path.parent.mkdir(parents=True, exist_ok=True)
        app = self.snapshot['applications'][software]
        templates = self.root / generation / 'templates' / software
        info = f"Commit: {next(iter(app['partitions'].values()))['identity']['source_sha']}\nTemplates: {templates}"
        path.write_text(render_module(self.root / generation, software, app,
            help_text=info, notice='loaded ' + generation,
            extra_environment={beta.stem(software) + '_TEMPLATES': str(templates)}, compute_only=True))
        return path

    def evaluate(self, source, *, mode='load', partition=None, saved=None, compute=True):
        if not shutil.which('tclsh'):
            self.skipTest('tclsh is required')
        setup = '''
proc module-info {kind value} {return [expr {$value eq $::mode}]}
proc module-whatis {args} {}
proc conflict {args} {}
proc module {args} {puts "MODULE $args"}
proc prepend-path {name value} {puts "PATH $name=$value"}
proc setenv {name value} {puts "ENV $name=$value"}
'''
        setup += f'set mode {mode}\n'
        if partition:
            setup += f'set env(SLURM_JOB_PARTITION) {beta.tcl(partition)}\n'
        if compute:
            setup += 'set env(SLURM_JOB_ID) 123\nset env(SLURMD_NODENAME) compute-node\n'
        for key, value in (saved or {}).items():
            setup += f'set env({key}) {beta.tcl(value)}\n'
        setup += 'if {[catch {\n' + source + '\n} err]} {puts stderr $err; exit 1}\n'
        return subprocess.run(['tclsh'], input=setup, text=True, capture_output=True,
                              env={k: v for k, v in os.environ.items() if not k.startswith(('SLURM_', 'SAI_'))})

    def test_login_load_and_salloc_print_help_without_partial_environment(self):
        fragment = self.fragment('cp2k', 'first').read_text()
        for partition in (None, '16V100'):
            result = self.evaluate(fragment, partition=partition, compute=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn('cfe5e5d87aaf302953f14eb43587bef2411a8fb2', result.stderr)
            self.assertIn('Templates:', result.stderr)
            self.assertIn('compute node', result.stderr)
            self.assertNotIn('MODULE load', result.stdout)
            self.assertNotIn('ENV ', result.stdout)
            self.assertNotIn('PATH ', result.stdout)

    def test_all_partition_selections_report_version_and_keep_fixed_launchers(self):
        for software, app in self.snapshot['applications'].items():
            source = self.fragment(software, 'first').read_text()
            for partition, row in app['partitions'].items():
                with self.subTest(software=software, partition=partition):
                    result = self.evaluate(source, partition=partition)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(row['image'], result.stdout)
                    self.assertIn(str(self.root / 'first' / 'bin' / software), result.stdout)
                    self.assertEqual(result.stderr.count('loaded first'), 1)
                    self.assertIn('partition=' + partition, result.stderr)
        bad = self.evaluate(self.fragment('gpumd', 'first').read_text(), partition='DSPRHBM')
        self.assertEqual(bad.returncode, 1)
        self.assertIn('Templates:', bad.stderr)
        self.assertNotIn('MODULE load', bad.stdout)

    def test_fixed_gateway_observes_rotation_but_unload_uses_saved_generation(self):
        for generation in ('first', 'second'):
            self.fragment('cp2k', generation)
        (self.root / 'channels').mkdir()
        pointer = self.root / 'channels/cp2k'
        pointer.write_text('first\n')
        home_module = self.root / 'home-module'
        home_module.write_text(beta.render_channel(self.root, 'cp2k'))
        gateway = beta.render_gateway(home_module)
        first = self.evaluate(gateway, partition='16V100')
        self.assertEqual(first.returncode, 0, first.stderr)
        pointer.write_text('second\n')
        second = self.evaluate(gateway, partition='16V100')
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn('/second/bin/cp2k', second.stdout)
        saved = {'SAI_SIF_CP2K_BETA_GENERATION': 'first', 'SAI_SIF_CP2K_PARTITION': '16V100'}
        # Current pointer may even be missing; unloading must still work.
        pointer.unlink()
        unload = self.evaluate(gateway, mode='remove', saved=saved, compute=False)
        self.assertEqual(unload.returncode, 0, unload.stderr)
        self.assertIn('/first/bin/cp2k', unload.stdout)
        self.assertNotIn('/second/', unload.stdout)
        self.assertEqual(unload.stderr, '')
        corrupt = self.evaluate(gateway, mode='remove', saved={'SAI_SIF_CP2K_BETA_GENERATION': '../../escape'})
        self.assertEqual(corrupt.returncode, 1)

    def fake_bundle(self):
        bundle = self.root / 'bundle'
        (bundle / 'libexec').mkdir(parents=True)
        runtime = bundle / 'libexec/sif_runtime.py'
        shutil.copyfile(REPO / 'controller/sif_runtime.py', runtime)
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['runtime_sha256'] = digest(runtime)
        for name, app in snapshot['applications'].items():
            for partition, row in app['partitions'].items():
                image = bundle / (name + '-' + partition + '.sif')
                image.write_bytes((name + partition).encode())
                image.chmod(0o444)
                row['image'], row['sha256'] = str(image), digest(image)
            (bundle / 'bin' / name).mkdir(parents=True)
            for program in PROGRAMS[name]:
                argv = ['/usr/bin/python3', '-I', '-B', str(runtime), str(bundle / 'catalog.json'), name, program]
                (bundle / 'bin' / name / program).write_text('#!/usr/bin/env bash\nexec ' + shlex.join(argv) + ' "$@"\n')
        catalog = bundle / 'catalog.json'
        catalog.write_text(json.dumps(snapshot))
        reports = []
        for partition in ('4V100', '16V100', '8V100V0', 'DSPRHBM'):
            report = {'partition': partition, 'job': '123', 'passed': True,
                      'catalog_sha256': digest(catalog), 'runtime_sha256': digest(runtime)}
            report.update({name: {'result': True} for name, app in snapshot['applications'].items()
                           if partition in app['partitions']})
            reports.append(report)
        checks = self.root / 'checks.json'
        checks.write_text(json.dumps(reports))
        return bundle, catalog, checks

    def test_promotion_requires_matching_runtime_acceptance_and_complete_partition_matrix(self):
        bundle, catalog, checks = self.fake_bundle()
        reports = json.loads(checks.read_text())
        reports[0]['catalog_sha256'] = '0' * 64
        checks.write_text(json.dumps(reports))
        with self.assertRaisesRegex(ValueError, 'matching SIF execution acceptance'):
            beta.verified_bundle(bundle, catalog, checks, ['cp2k'])
        snapshot = json.loads(catalog.read_text())
        del snapshot['applications']['cp2k']['partitions']['16V100']
        catalog.write_text(json.dumps(snapshot))
        with self.assertRaisesRegex(ValueError, 'complete partition matrix'):
            beta.verified_bundle(bundle, catalog, checks, ['cp2k'])

    def test_relocated_bundle_must_retain_all_accepted_metadata(self):
        bundle, catalog, checks = self.fake_bundle()
        original = self.root / 'accepted.json'
        shutil.copyfile(catalog, original)
        snapshot = json.loads(catalog.read_text())
        snapshot['applications']['cp2k']['partitions']['16V100']['mpi_prefix'] = '/unaccepted/mpi'
        catalog.write_text(json.dumps(snapshot))
        with self.assertRaisesRegex(ValueError, 'relocated bundle differs'):
            beta.verified_bundle(bundle, original, checks, ['cp2k'])

    def test_prepare_select_and_template_shell_syntax(self):
        bundle, catalog, checks = self.fake_bundle()
        root, modules = self.root / 'beta', self.root / 'modulefiles'
        destination = beta.prepare(root, modules, 'one', bundle, catalog, checks, list(PROGRAMS))
        self.assertFalse((root / 'channels').exists())
        beta.select(root, 'one', ['cp2k'])
        self.assertEqual((root / 'channels/cp2k').read_text(), 'one\n')
        for template in destination.glob('templates/*/*.sbatch'):
            result = subprocess.run(['bash', '-n', str(template)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = template.read_text()
            self.assertIn('TMPDIR="$PWD/', content)
            if template.name == 'gpu.sbatch':
                self.assertNotIn('--cpus-per-task', content)
        fragment = destination / 'modules/cp2k'
        fragment.chmod(0o644)
        fragment.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'changed generation file'):
            beta.select(root, 'one', ['cp2k'])

    def test_checked_in_gateways_are_fixed_home_forwarders(self):
        for name in PROGRAMS:
            relative = Path(name) / beta.channel(name)
            self.assertEqual((REPO / 'deployment/sif-beta/modulefiles' / relative).read_text(),
                beta.render_gateway(Path('/home/stardust/sai-hpc-software/modulefiles/apps') / relative))


if __name__ == '__main__':
    unittest.main()
