"""Execute generated Tcl, including cross-partition publication and unload."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
from module_publication import publish_module, validate_module, tcl
from source_cache import checksum


class ModulePublicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def publish(self, target, run, software='abacus'):
        artifact = self.root / f'containers/software/{software}/v1/{target}/{run}.sif'
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text('accepted image ' + run)
        launcher = self.root / f'controller/{run}/{software}'
        launcher.parent.mkdir(parents=True)
        launcher.write_text('# tested launcher ' + run)
        request = dict(software=software, version='v1', target=target)
        modules = ['module load openmpi/native-auto']
        if target != 'dsprhbm':
            modules += ['module load cuda/12.9.1 nvmplibs/26.7-tmp']
        manifest = publish_module(self.root, request, artifact, launcher, modules, software.upper())
        manifest.update(runtime_launcher=str(launcher), runtime_launcher_sha256=checksum(launcher))
        return manifest, artifact, launcher

    def evaluate(self, manifest, partition=None, mode='load', saved=None):
        binary = shutil.which('tclsh')
        if not binary:
            self.skipTest('Tcl interpreter required for module execution tests')
        environment = os.environ.copy()
        for name in list(environment):
            if name.startswith('SAI_') or name == 'SLURM_JOB_PARTITION':
                del environment[name]
        if partition:
            environment['SLURM_JOB_PARTITION'] = partition
        environment.update(saved or {})
        script = '\n'.join([
            f'set mode {tcl(mode)}',
            'proc module-info {kind arg} {global mode; return [expr {$kind eq "mode" && $arg eq $mode}]}',
            'proc module-whatis {args} {}', 'proc conflict {args} {}',
            'proc module {args} {puts "MODULE $args"}',
            'proc prepend-path {name value} {puts "PATH $name $value"}',
            'proc setenv {name value} {global env; set env($name) $value; puts "ENV $name $value"}',
            f'if {{[catch {{source {tcl(manifest["modulefile"])}}} reason]}} {{puts stderr $reason; exit 1}}',
        ])
        return subprocess.run([binary], input=script, text=True, capture_output=True, env=environment)

    def test_later_partition_cannot_replace_tested_image_launcher_or_dependencies(self):
        cpu, cpu_image, cpu_launcher = self.publish('dsprhbm', 'cpu-old', 'cp2k')
        original_selector = Path(cpu['modulefile']).read_bytes()
        gpu, gpu_image, gpu_launcher = self.publish('8v100v0-avx512', 'gpu-new', 'cp2k')
        self.assertEqual(Path(cpu['modulefile']).read_bytes(), original_selector)
        for partition, manifest, artifact, launcher in (
                ('DSPRHBM', cpu, cpu_image, cpu_launcher), ('8V100V0', gpu, gpu_image, gpu_launcher)):
            result = self.evaluate(manifest, partition)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('ENV SAI_CP2K_IMAGE ' + str(artifact), result.stdout)
            self.assertIn('PATH PATH ' + str(launcher.parent), result.stdout)
            self.assertEqual('nvmplibs/26.7-tmp' in result.stdout, partition != 'DSPRHBM')
            validate_module(manifest, artifact)

    def test_loaded_module_unloads_its_original_pair_after_new_publication(self):
        old, image, launcher = self.publish('4v100-avx512', 'first')
        self.publish('4v100-avx512', 'second')
        result = self.evaluate(old, '4V100')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('second.sif', result.stdout)
        result = self.evaluate(old, mode='remove', saved={
            'SAI_ABACUS_MODULE_TARGET': '4v100-avx512', 'SAI_ABACUS_MODULE_RUN': 'first'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(image), result.stdout)
        self.assertIn(str(launcher.parent), result.stdout)
        self.assertNotIn('second.sif', result.stdout)

    def test_load_requires_allocation_and_accepted_target_but_help_does_not(self):
        manifest, _, _ = self.publish('4v100-avx512', 'first')
        self.assertNotEqual(self.evaluate(manifest).returncode, 0)
        self.assertEqual(self.evaluate(manifest, mode='display').returncode, 0)
        self.assertEqual(self.evaluate(manifest, 'DSPRHBM', mode='display').returncode, 0)
        self.assertEqual(self.evaluate(manifest, 'unknown', mode='display').returncode, 0)
        self.assertNotEqual(self.evaluate(manifest, 'DSPRHBM').returncode, 0)
        self.assertNotEqual(self.evaluate(manifest, 'unknown').returncode, 0)
        saved = {'SAI_ABACUS_MODULE_TARGET': '../outside', 'SAI_ABACUS_MODULE_RUN': 'first'}
        self.assertNotEqual(self.evaluate(manifest, mode='remove', saved=saved).returncode, 0)

    def test_fragment_and_launcher_corruption_cannot_pass_cache_validation(self):
        manifest, artifact, launcher = self.publish('dsprhbm', 'cpu')
        validate_module(manifest, artifact)
        fragment = Path(manifest['module_fragment'])
        original = fragment.read_text()
        fragment.chmod(0o644)
        fragment.write_text(original + '# tampered\n')
        with self.assertRaises(ValueError):
            validate_module(manifest, artifact)
        fragment.write_text(original)
        launcher.write_text('# changed\n')
        with self.assertRaises(ValueError):
            validate_module(manifest, artifact)

    def test_tcl_path_quotes_do_not_execute_or_substitute_user_paths(self):
        self.root = self.root / 'space $variable [error bad] "quoted"'
        manifest, artifact, _ = self.publish('dsprhbm', 'cpu')
        result = self.evaluate(manifest, 'DSPRHBM')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(artifact), result.stdout)

    def test_broken_or_changed_selector_and_old_target_pointer_miss_cache(self):
        manifest, artifact, _ = self.publish('dsprhbm', 'cpu')
        selector = Path(manifest['modulefile'])
        content = selector.read_text()
        selector.chmod(0o644)
        selector.write_text('# not the trusted selector')
        with self.assertRaises(ValueError):
            validate_module(manifest, artifact)
        selector.unlink()
        with self.assertRaises(ValueError):
            validate_module(manifest, artifact)
        selector.write_text(content)
        validate_module(manifest, artifact)
        self.publish('dsprhbm', 'new-cpu')
        with self.assertRaises(ValueError):
            validate_module(manifest, artifact)

    def test_launcher_parent_symlink_cannot_redirect_accepted_snapshot(self):
        manifest, artifact, launcher = self.publish('dsprhbm', 'cpu')
        moved = self.root / 'outside-controller'
        launcher.parent.rename(moved)
        launcher.parent.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ValueError):
            validate_module(manifest, artifact)


if __name__ == '__main__':
    unittest.main()
