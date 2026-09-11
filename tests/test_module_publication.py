"""Execute generated Tcl, including cross-partition publication and unload."""
import os
import copy
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
from module_publication import publish_module, validate_module, tcl
from delivery_layout import artifact_path
from release_contract import make_identity
from source_cache import checksum


class ModulePublicationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def publish(self, target, run, software='abacus', track='development', sha='a' * 40,
                version='v1'):
        identity = make_identity(software, track, 'develop', sha, version, 'b' * 64, target)
        artifact = artifact_path(self.root, identity, run)
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text('accepted image ' + run)
        launcher = self.root / f'controller/{track}/{run}/{software}'
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text('# tested launcher ' + run)
        request = dict(software=software, version=version, target=target, identity=identity,
                       recipe_sha256=identity['recipe_sha256'], source_sha=sha)
        modules = ['module load openmpi/native-auto']
        if target != 'dsprhbm':
            modules += ['module load cuda/12.9.1 nvmplibs/26.7-tmp']
        manifest = publish_module(self.root, request, artifact, launcher, modules, software.upper())
        manifest.update(request)
        manifest.update(runtime_launcher=str(launcher), runtime_launcher_sha256=checksum(launcher))
        return manifest, artifact, launcher

    def validate(self, manifest, artifact):
        return validate_module(manifest, artifact, root=self.root)

    def saved(self, manifest, run):
        identity = manifest['identity']
        stem = 'SAI_' + identity['software'].upper().replace('-', '_')
        return {stem + '_MODULE_TARGET': identity['target'],
                stem + '_MODULE_PARTITION': identity['partition'],
                stem + '_MODULE_RUN': run, stem + '_MODULE_TRACK': identity['track'],
                stem + '_MODULE_BUILD_ID': identity['build_id']}

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
            self.assertIn('ENV SAI_CP2K_VERSION v1', result.stdout)
            self.assertIn('ENV SAI_CP2K_PREFIX ' + manifest['identity']['install_prefix'], result.stdout)
            self.assertEqual(artifact.parent.name, partition)
            self.validate(manifest, artifact)

    def test_loaded_module_unloads_its_original_pair_after_new_publication(self):
        old, image, launcher = self.publish('4v100-avx512', 'first')
        self.publish('4v100-avx512', 'second')
        result = self.evaluate(old, '4V100')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('second.sif', result.stdout)
        result = self.evaluate(old, partition='16V100', mode='remove', saved=self.saved(old, 'first'))
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
        saved = self.saved(manifest, 'first')
        saved['SAI_ABACUS_MODULE_TARGET'] = '../outside'
        self.assertNotEqual(self.evaluate(manifest, mode='remove', saved=saved).returncode, 0)

    def test_fragment_and_launcher_corruption_cannot_pass_cache_validation(self):
        manifest, artifact, launcher = self.publish('dsprhbm', 'cpu')
        self.validate(manifest, artifact)
        fragment = Path(manifest['module_fragment'])
        original = fragment.read_text()
        fragment.chmod(0o644)
        fragment.write_text(original + '# tampered\n')
        with self.assertRaises(ValueError):
            self.validate(manifest, artifact)
        fragment.write_text(original)
        launcher.write_text('# changed\n')
        with self.assertRaises(ValueError):
            self.validate(manifest, artifact)

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
            self.validate(manifest, artifact)
        selector.unlink()
        with self.assertRaises(ValueError):
            self.validate(manifest, artifact)
        selector.write_text(content)
        self.validate(manifest, artifact)
        self.publish('dsprhbm', 'new-cpu')
        with self.assertRaises(ValueError):
            self.validate(manifest, artifact)

    def test_launcher_parent_symlink_cannot_redirect_accepted_snapshot(self):
        manifest, artifact, launcher = self.publish('dsprhbm', 'cpu')
        moved = self.root / 'outside-controller'
        launcher.parent.rename(moved)
        launcher.parent.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.validate(manifest, artifact)

    def test_channels_and_builds_cannot_share_selector_or_unload_state(self):
        dev, dev_image, _ = self.publish('4v100-avx512', 'same-run')
        release, release_image, _ = self.publish('4v100-avx512', 'same-run', track='release')
        prerelease, pre_image, _ = self.publish('4v100-avx512', 'same-run', track='prerelease')
        self.assertEqual(len({row['modulefile'] for row in (dev, release, prerelease)}), 3)
        for manifest, artifact in ((dev, dev_image), (release, release_image), (prerelease, pre_image)):
            result = self.evaluate(manifest, '4V100')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(artifact), result.stdout)
            self.validate(manifest, artifact)
        saved = self.saved(dev, 'same-run')
        for mode in ('load', 'remove'):
            result = self.evaluate(release, '4V100', mode=mode, saved=saved)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('MODULE load', result.stdout)
        newer, _, _ = self.publish('4v100-avx512', 'new-run', sha='c' * 40)
        for mode in ('load', 'remove'):
            self.assertNotEqual(self.evaluate(newer, '4V100', mode=mode, saved=saved).returncode, 0)

    def test_identity_is_required_and_artifact_cannot_be_relabelled(self):
        manifest, artifact, launcher = self.publish('dsprhbm', 'cpu')
        for mutate in (lambda row: row.pop('identity'),
                       lambda row: row.update(version='other'),
                       lambda row: row['identity'].update(partition='4V100'),
                       lambda row: row.update(source_sha='c' * 40)):
            invalid = copy.deepcopy(manifest)
            mutate(invalid)
            with self.assertRaises(ValueError):
                self.validate(invalid, artifact)
            with self.assertRaises(ValueError):
                publish_module(self.root, invalid, artifact, launcher, [], 'ABACUS')
        alternate = artifact.with_name('relabelled.sif')
        alternate.write_text(artifact.read_text())
        with self.assertRaises(ValueError):
            self.validate(manifest, alternate)
        with self.assertRaises(ValueError):
            validate_module(manifest, artifact, root=self.root / 'wrong-root')

    def test_version_quoted_literal_and_gpu_only_software_rejects_cpu(self):
        version = '3.11 $bad [error injected] "quoted";not-command'
        manifest, _, _ = self.publish('4v100-avx512', 'gpu', software='gpumd', version=version)
        result = self.evaluate(manifest, '4V100')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ENV SAI_GPUMD_VERSION ' + version, result.stdout)
        self.assertNotEqual(self.evaluate(manifest, 'DSPRHBM').returncode, 0)

    def test_partition_symlink_cannot_redirect_tcl_source(self):
        manifest, artifact, _ = self.publish('dsprhbm', 'cpu')
        moved = self.root / 'moved-partition'
        artifact.parent.rename(moved)
        artifact.parent.symlink_to(moved, target_is_directory=True)
        result = self.evaluate(manifest, 'DSPRHBM')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('MODULE load', result.stdout)
        with self.assertRaises(ValueError):
            self.validate(manifest, artifact)


if __name__ == '__main__':
    unittest.main()
