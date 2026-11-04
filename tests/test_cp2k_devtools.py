from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import cp2k_devtools as dev
import cp2k_devtools_stage as stage
import devtools_install as installer
from cp2k_spack_environment import environment
from cp2k_spack_store import patch_id, validate_layout


def provider_fixture():
    roots = {}
    nodes = []
    versions = dict(libint='2.6.0', libxsmm='2.0.0', libvori='220621', hdf5='1.14.6',
                    plumed='2.10.1', cosma='2.7.0', spla='1.6.1', costa='2.3.3')
    for i, (name, version) in enumerate(versions.items()):
        dag = chr(ord('a') + i) * 32
        prefix = f'/opt/devtools/{name}/{version}-gcc14.3.0-zen3-{dag}'
        if name == 'libint':
            prefix += '-p' + patch_id()
        if name != 'costa':
            roots[name] = prefix
        nodes.append(dict(name=name, version=version, target='zen3', hash=dag,
                          prefix=prefix, external=False, dependencies=[]))
    nodes[5]['dependencies'] = [nodes[-1]['hash']]
    installed = dict(partition='16V100', layout='devtools', install_prefix='/opt/devtools',
                     dependencies_installed=True, cp2k_built=False, published=False,
                     roots=roots, runtime_prefixes=[node['prefix'] for node in nodes], nodes=nodes)
    contract = dev.contract('16V100', 'zen3', 'ubuntu24.04')
    return dict(schema=1, kind='spack-devtools', install=installed, contract=contract,
                contract_sha256=dev.digest(contract), packages={node['prefix']: [] for node in nodes})


class DevtoolsTests(unittest.TestCase):
    def test_identity_has_no_cp2k_source_run_or_parallelism(self):
        c = dev.contract('16V100', 'zen3', 'ubuntu24.04')
        self.assertNotIn('source_sha', c)
        self.assertNotIn('build_jobs', json.dumps(c))
        self.assertNotIn('/opt/apps/', json.dumps(c))
        other = dev.contract('4V100', 'zen4', 'ubuntu24.04')
        self.assertNotEqual(dev.digest(c), dev.digest(other))
        data = provider_fixture()
        dev.validate_provider(data)
        changed = deepcopy(data)
        changed['contract']['packages']['openmpi']['externals'][0]['spec'] = 'openmpi@5.0.8'
        changed['contract_sha256'] = dev.digest(changed['contract'])
        with self.assertRaisesRegex(ValueError, 'ABI or recipe'):
            dev.validate_provider(changed)

    def test_devtools_layout_does_not_weaken_private_prefix_validation(self):
        with self.assertRaises(ValueError):
            validate_layout('16V100', '/opt/devtools')
        for prefix in ('/opt/devtools/gcc', '/opt/devtools/../apps', '/tmp/devtools'):
            with self.assertRaises(ValueError):
                validate_layout('16V100', prefix, 'devtools')
        new = environment('16V100', '/opt/devtools', 'zen3', 'ubuntu24.04', layout='devtools')['spack']
        self.assertEqual(new['config']['install_tree']['root'], '/opt/devtools')
        self.assertIn(patch_id(), new['config']['install_tree']['projections']['libint'])
        old = environment('16V100', '/opt/apps/cp2k/cp2k-development-test/16V100', 'zen3', 'ubuntu24.04')['spack']
        self.assertIn('/dependencies/spack', old['config']['install_tree']['root'])

    def test_provider_requires_transitive_closure_and_exact_native_prefixes(self):
        for mutation in (
            lambda d: d['install']['runtime_prefixes'].pop(),
            lambda d: d['install']['nodes'][0].update(target='zen4'),
            lambda d: d['install']['nodes'][0].update(version='2.7.0'),
            lambda d: d['install']['roots'].update(libint='/opt/apps/cp2k/old/libint'),
            lambda d: d['packages'].pop(next(iter(d['packages']))),
        ):
            value = provider_fixture()
            mutation(value)
            with self.assertRaises(ValueError):
                dev.validate_provider(value)

    def test_generated_jobs_build_in_overlay_and_verify_in_readonly_sif(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for partition in dev.TARGETS:
                script = stage.render(root, root / 'controller/frozen', root / 'runs/fresh', partition)
                subprocess.run(['bash', '-n'], input=script, text=True, check=True)
                self.assertNotIn('/opt/devtools:/opt/devtools:ro', script)
                self.assertNotIn('/opt/devtools:/opt/devtools:rw', script)
                self.assertIn('/opt/devtools/gcc:/opt/devtools/gcc:ro', script)
                self.assertIn('cp2k_image_driver.py', script)
                self.assertIn('libraries.sif /usr/bin/bash /control/cp2k_devtools_entry.sh verify', script)
                self.assertIn('e2fsck -fn', script)
                self.assertNotIn('--mem=', script) if partition != 'DSPRHBM' else None
                if partition == '8V100V0':
                    self.assertIn('--gpus-per-node=2', script)
                    self.assertIn('BUILD_JOBS=12', script)

    def test_payload_preserves_headers_archives_and_cmake_and_omits_spack_logs(self):
        # Exercise the actual copy/inventory path with a bounded synthetic source.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            for name in ('include/api.h', 'lib/libx.a', 'lib/libx.so.1', 'lib/cmake/x/xConfig.cmake',
                         'lib/pkgconfig/x.pc', 'share/data/table', '.spack/build.log'):
                p = source / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('fixture')
            (source / 'lib/libx.so').symlink_to('libx.so.1')
            results = root / 'results'
            results.mkdir()
            (results / 'install-success.json').write_text(json.dumps({'partition': '16V100'}))
            (results / 'validated-graph.json').write_text(json.dumps(dict(
                partition='16V100', install_prefix='/opt/devtools', target='zen3', os='ubuntu24.04')))
            export = root / 'export'
            with patch.object(dev, 'validate_install', return_value=[{'prefix': str(source)}]), \
                 patch.object(dev, 'validate_provider'):
                record = dev.stage(results, export)
            names = {row['path'] for row in record['packages'][str(source)]}
            self.assertIn('include/api.h', names)
            self.assertIn('lib/libx.a', names)
            self.assertIn('lib/cmake/x/xConfig.cmake', names)
            self.assertIn('lib/pkgconfig/x.pc', names)
            self.assertNotIn('.spack', names)
            self.assertTrue((source / '.spack/build.log').is_file())

    def test_installer_detects_changed_files_external_links_and_unsafe_targets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'lib').mkdir()
            (root / 'lib').chmod(0o755)
            (root / 'lib/libx.so.1').write_text('original')
            (root / 'lib/libx.so.1').chmod(0o644)
            (root / 'lib/libx.so').symlink_to('libx.so.1')
            original = installer.inventory(root)
            installer.verify(root, original)
            (root / 'lib/libx.so.1').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'differs'):
                installer.verify(root, original)
            (root / 'bad').symlink_to('/etc/passwd')
            with self.assertRaisesRegex(ValueError, 'External'):
                installer.inventory(root)
        for name, prefix in [('gcc', '/opt/devtools/gcc/14.3.0'), ('hdf5', '/opt/devtools/hdf5/existing')]:
            with self.assertRaises(ValueError):
                installer.package_prefix(dict(name=name, prefix=prefix))


if __name__ == '__main__':
    unittest.main()
