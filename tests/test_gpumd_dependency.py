"""Real-header ABI regression, frozen selection and complete native dependency export."""
import argparse
import copy
from contextlib import contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
import gpumd_dependency as dep
import gpumd_science as science
import export_native as native
import software_controller as build
from native_module import validate_native_entry
from release_contract import make_identity
import test_gpumd


def locked(root, target='4v100-avx512'):
    identity = make_identity('deepmd-kit', 'development', 'master', 'd' * 40,
                             'master-2026-11-23', 'b' * 64, target)
    prefix = identity['install_prefix']
    image = root / (target + '.sif')
    image.write_bytes(b'accepted dependency fixture')
    image.chmod(0o444)
    entry = dict(identity=identity, commands={'dp': 'bin/dp'}, external_roots=[],
                 runtime=dict(modules=[], prepend={'LD_LIBRARY_PATH': [prefix + '/lib'],
                              'PYTHONPATH': [prefix + '/lib/python3.13/site-packages']},
                              set={'DP_BACKEND_PLUGIN_PATH': prefix + '/lib'}))
    return dict(schema=1, identity=identity, image=str(image), sha256=dep.checksum(image), binds=[],
                acceptance=dict(build_verified=True, scientific_verified=True, evidence='fixture',
                                evidence_sha256='e' * 64), entry=entry, manifest_sha256='f' * 64,
                api_files={name: 'a' * 64 for name in dep.API_FILES})


class DependencySelectionTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, 'fork'), 'process interruption test requires POSIX fork')
    def test_interrupted_first_publication_leaves_no_final_lock_and_can_retry(self):
        for failure in ('write', 'close', 'process-exit'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                first = locked(root)
                original_open = Path.open
                original_temporary = tempfile.NamedTemporaryFile

                @contextmanager
                def interrupt(output):
                    with output as stream:
                        write = stream.write

                        def interrupted_write(content):
                            if failure == 'close':
                                return write(content)
                            write(content[:len(content) // 2])
                            stream.flush()
                            if failure == 'process-exit':
                                os._exit(73)  # No finally/close cleanup in the writer.
                            raise OSError('injected partial write failure')

                        with patch.object(stream, 'write', side_effect=interrupted_write):
                            yield stream
                    if failure == 'close':
                        raise OSError('injected close failure')

                def open_path(path, mode='r', *args, **kwargs):
                    stream = original_open(path, mode, *args, **kwargs)
                    return interrupt(stream) if path.name == dep.LOCK and mode == 'x' else stream

                def temporary(*args, **kwargs):
                    return interrupt(original_temporary(*args, **kwargs))

                with patch.object(dep, 'resolve', return_value=first), \
                        patch.object(Path, 'open', open_path), \
                        patch.object(tempfile, 'NamedTemporaryFile', side_effect=temporary):
                    if failure == 'process-exit':
                        child = os.fork()
                        if child == 0:
                            try:
                                dep.select(root, '4v100-avx512', root)
                            except BaseException:
                                os._exit(74)
                            os._exit(75)
                        _, status = os.waitpid(child, 0)
                        self.assertEqual(os.waitstatus_to_exitcode(status), 73)
                    else:
                        with self.assertRaisesRegex(OSError, 'injected'):
                            dep.select(root, '4v100-avx512', root)
                self.assertFalse((root / dep.LOCK).exists())
                self.assertFalse((root / dep.LOCK).is_symlink())
                with patch.object(dep, 'resolve', return_value=first):
                    self.assertEqual(dep.select(root, '4v100-avx512', root), first)
                self.assertEqual((root / dep.LOCK).read_text(), dep.canonical(first))
                self.assertEqual((root / dep.LOCK).stat().st_mode & 0o777, 0o444)
                with patch.object(dep, 'resolve', side_effect=AssertionError('must keep the frozen identity')):
                    self.assertEqual(dep.select(root, '4v100-avx512', root), first)

    def test_existing_invalid_or_changed_lock_fails_without_reselection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = locked(root)
            invalid = copy.deepcopy(first)
            invalid['acceptance']['scientific_verified'] = False
            for content in ('', '{', dep.canonical(invalid)):
                with self.subTest(content=content):
                    final = root / dep.LOCK
                    final.write_text(content)
                    with patch.object(dep, 'resolve', side_effect=AssertionError('must not reselect')):
                        with self.assertRaises(ValueError):
                            dep.select(root, '4v100-avx512', root)
                    self.assertEqual(final.read_text(), content)
            final.write_text(dep.canonical(first))
            image = Path(first['image'])
            image.chmod(0o644)
            image.write_bytes(b'changed accepted image')
            image.chmod(0o444)
            with patch.object(dep, 'resolve', side_effect=AssertionError('must not reselect')):
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    dep.select(root, '4v100-avx512', root)
            self.assertEqual(final.read_text(), dep.canonical(first))

    def test_publication_is_readonly_and_does_not_replace_an_intervening_path(self):
        for collision in ('file', 'symlink'):
            with self.subTest(collision=collision), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                first = locked(root)
                final = root / dep.LOCK
                preserved = root / 'preserved.json'
                preserved.write_text('preserve existing bytes')
                original_link = os.link

                def collide(source, destination):
                    self.assertEqual(Path(source).parent, root)
                    self.assertEqual(Path(source).read_text(), dep.canonical(first))
                    self.assertEqual(Path(source).stat().st_mode & 0o777, 0o444)
                    if collision == 'symlink':
                        final.symlink_to(preserved)
                    else:
                        final.write_text(preserved.read_text())
                    return original_link(source, destination)

                with patch.object(dep, 'resolve', return_value=first), \
                        patch.object(dep.os, 'link', side_effect=collide):
                    with self.assertRaises(FileExistsError):
                        dep.select(root, '4v100-avx512', root)
                self.assertEqual(final.is_symlink(), collision == 'symlink')
                self.assertEqual(final.read_text(), 'preserve existing bytes')
                self.assertEqual(preserved.read_text(), 'preserve existing bytes')
                with patch.object(dep, 'resolve', side_effect=AssertionError('must not reselect')):
                    with self.assertRaises(ValueError):
                        dep.select(root, '4v100-avx512', root)

    def test_resolve_uses_latest_accepted_target_and_revalidates_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = locked(root)
            other = locked(root, '16v100-avx2')

            def status(name, timestamp, identity, accepted=False, recovery=False, built=True):
                directory = root / 'experimental/deepmd-standalone/runs' / ('dp-' + name)
                (directory / 'results').mkdir(parents=True)
                request = directory / 'request.json'
                request.write_text('{}')
                os.utime(request, (timestamp, timestamp))
                (directory / 'results/status.json').write_text(json.dumps(dict(
                    delivery={'identity': identity}, build_verified=built, scientific_verified=accepted)))
                if recovery:
                    (directory / 'results/acceptance-recovery.json').write_text('{}')
                return directory

            status('older', 100, first['identity'], accepted=True)
            selected = status('recovered', 200, first['identity'], recovery=True)
            status('wrong-target', 500, other['identity'], accepted=True)
            status('unaccepted', 600, first['identity'])
            status('unbuilt', 700, first['identity'], accepted=True, built=False)
            with patch('beta_candidates.candidate', return_value=first) as candidate, \
                    patch.object(dep, 'from_candidate', return_value=first) as inspect:
                self.assertEqual(dep.resolve(root, '4v100-avx512'), first)
                candidate.assert_called_once_with('deepmd-kit', selected, first['identity'])
                inspect.assert_called_once_with(first, '4v100-avx512')
            with patch('beta_candidates.candidate', side_effect=ValueError('recovery proof changed')):
                with self.assertRaisesRegex(ValueError, 'recovery proof changed'):
                    dep.resolve(root, '4v100-avx512')
            with self.assertRaisesRegex(ValueError, 'no accepted'):
                dep.resolve(root, '8v100v0-avx512')

    def test_selection_rejects_redirected_controller_or_mutex(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            control = root / 'control'
            control.mkdir()
            link = root / 'redirect'
            link.symlink_to(control)
            with self.assertRaisesRegex(ValueError, 'controller directory'):
                dep.select(root, '4v100-avx512', link)
            existing = root / 'existing'
            existing.write_text('preserve')
            (control / '.gpumd-deepmd-selection.lock').symlink_to(existing)
            with self.assertRaisesRegex(ValueError, 'redirected'):
                dep.select(root, '4v100-avx512', control)
            self.assertEqual(existing.read_text(), 'preserve')

    def test_retries_keep_same_accepted_delivery_after_rotation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = locked(root)
            with patch.object(dep, 'resolve', return_value=first) as resolve:
                self.assertEqual(dep.select(root, '4v100-avx512', root), first)
                resolve.assert_called_once()
            with patch.object(dep, 'resolve', side_effect=AssertionError('must not select again')):
                self.assertEqual(dep.select(root, '4v100-avx512', root), first)
            self.assertEqual((root / dep.LOCK).stat().st_mode & 0o777, 0o444)
            with self.assertRaisesRegex(ValueError, 'architecture'):
                dep.select(root, '16v100-avx2', root)

    def test_missing_unaccepted_redirected_and_changed_dependency_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertIsNone(dep.read(root, '4v100-avx512'))
            with self.assertRaisesRegex(ValueError, 'requires a frozen'):
                dep.read(root, '4v100-avx512', required=True)
            first = locked(root)
            changed = copy.deepcopy(first)
            changed['acceptance']['scientific_verified'] = False
            with self.assertRaisesRegex(ValueError, 'acceptance'):
                dep.validate(changed, '4v100-avx512')
            image = Path(first['image'])
            image.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'writable'):
                dep.verify_image(first, '4v100-avx512')
            image.write_bytes(b'changed image')
            image.chmod(0o444)
            with self.assertRaisesRegex(ValueError, 'checksum'):
                dep.verify_image(first, '4v100-avx512')
            link = root / 'link.sif'
            link.symlink_to(image)
            first['image'] = str(link)
            with self.assertRaisesRegex(ValueError, 'redirected'):
                dep.verify_image(first, '4v100-avx512')

    def test_dependency_is_bound_into_recipe_request_and_build_image(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in build.contract_files('gpumd'):
                source = ROOT / 'controller' / name
                if name in ('gpumd', 'nep', 'gnep'):
                    source = ROOT / 'controller/gpumd_runtime.sh'
                shutil.copyfile(source, root / name)
            before = build.recipe_fingerprint('gpumd', root)
            first = locked(root)
            (root / dep.LOCK).write_text(json.dumps(first))
            self.assertNotEqual(before, build.recipe_fingerprint('gpumd', root))
            args = argparse.Namespace(software='gpumd', target='4v100-avx512', run_id='gpumd-test',
                sha='a' * 40, version='master-2026-11-23', track='development', source_ref='master',
                jobs=8, minutes=30, overlay_mb=8192, resume_run=None)
            with patch.object(build, 'CONTROL', root):
                script = build.render_job(args)
                identity = build.request_identity(args)
                request = dict(vars(args), identity=identity, source_sha=args.sha,
                               recipe_sha256=identity['recipe_sha256'], controller=str(root), deepmd_dependency=first)
                self.assertEqual(build.export_identities(request), [identity, first['identity']])
                request['deepmd_dependency'] = locked(root, '16v100-avx2')
                with self.assertRaises(ValueError):
                    build.export_identities(request)
            subprocess.run(['bash', '-n'], input=script, text=True, check=True)
            self.assertIn(first['image'], script)
            self.assertIn('verify-image', script)
            self.assertNotIn('/containers/base/minimal-v1.sif', script)

    def test_library_resolution_requires_the_selected_prefix(self):
        good = 'libdeepmd_cc.so => /opt/selected/lib/libdeepmd_cc.so (0x1)\n'
        dep.verify_link(good, '/opt/selected')
        for report in (good.replace('/opt/selected', science.DEEPMD), 'libdeepmd_cc.so => not found', ''):
            with self.assertRaises(ValueError):
                dep.verify_link(report, '/opt/selected')


@unittest.skipUnless(shutil.which('g++'), 'g++ is required for real-header ABI regression')
class RealHeaderTests(unittest.TestCase):
    def test_frozen_gpumd_calls_accept_daily_deepmd_and_reject_site_320(self):
        source = gzip.decompress((ROOT / 'tests/fixtures/gpumd_dp_91287ce2.cu.gz').read_bytes()).decode()
        start = source.index('    deep_pot.compute_canonical_graph_gpu(')
        call = source[start:source.index(');', start) + 2]
        prefix = '''#include "DeepPot.h"
#include <cstdint>
namespace deepmd_compat = deepmd;
template<class T> struct Buffer { T* data() const; };
void check(deepmd::DeepPot& deep_pot) {
  int N = 4, edge_storage = 8;
  Buffer<double> dp_atom_energy_gpu, dp_force_rowmajor, dp_atom_virial_gpu;
  Buffer<std::int64_t> source_order;
  std::int64_t *model_type=nullptr, *destination_row_ptr=nullptr, *source_row_ptr=nullptr;
  int* source=nullptr; float* edge_vec=nullptr; double *d_coord=nullptr, *box_ptr=nullptr;
  (void)deepmd_compat::canonical_index_width(static_cast<std::int64_t>(N));
  (void)deep_pot.has_long_range();
'''
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            translation = root / 'actual-gpumd-call.cpp'
            translation.write_text(prefix + call + '\n}\n')
            for version, succeeds in (('ed859426', True), ('v3_2_0', False)):
                with self.subTest(version=version):
                    headers = root / version
                    with tarfile.open(ROOT / f'tests/fixtures/deepmd_headers_{version}.tar.gz') as archive:
                        archive.extractall(headers, filter='data')
                    result = subprocess.run(['g++', '-std=c++14', '-fsyntax-only', '-I' + str(headers),
                                             str(translation)], capture_output=True, text=True)
                    self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                    if not succeeds:
                        self.assertIn('canonical_index_width', result.stderr)
                        self.assertIn('has_long_range', result.stderr)
                    header_text = {name: (headers / name).read_text() if (headers / name).exists() else ''
                                   for name in ('DeepPot.h', 'canonical_graph.h', 'c_api.h')}
                    if succeeds:
                        self.assertGreaterEqual(dep.require_api(header_text), 31)
                    else:
                        with self.assertRaisesRegex(ValueError, 'API 30'):
                            dep.require_api(header_text)


class BundledNativeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('mksquashfs') and shutil.which('unsquashfs'), 'Squashfs tools required')
    def test_candidate_reads_real_headers_and_preserves_daily_runtime_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            entry = json.loads((ROOT / 'tests/fixtures/deepmd_native_entry_ed859426.json').read_text())
            source = root / 'rootfs'
            prefix = source / entry['identity']['install_prefix'].lstrip('/')
            headers = prefix / 'include/deepmd'
            headers.mkdir(parents=True)
            with tarfile.open(ROOT / 'tests/fixtures/deepmd_headers_ed859426.tar.gz') as archive:
                archive.extractall(headers, filter='data')
            (prefix / 'bin').mkdir()
            (prefix / 'bin/dp').write_text('#!/bin/sh\nexit 0\n')
            (prefix / 'bin/dp').chmod(0o555)
            (prefix / 'lib').mkdir()
            # Synthetic payload verifies manifest plumbing, not executable ABI.
            for name in ('libdeepmd_cc.so', 'libdeepmd_c.so'):
                (prefix / 'lib' / name).write_bytes(b'synthetic library fixture')
            native.write_manifests([entry], source)
            image = root / 'dependency.squashfs'
            subprocess.run(['mksquashfs', str(source), str(image), '-noappend', '-no-progress', '-processors', '1'],
                           check=True, stdout=subprocess.DEVNULL)
            image.chmod(0o444)
            record = dict(identity=entry['identity'], image=str(image), sha256=dep.checksum(image), binds=[],
                          build_verified=True, scientific_verified=True, evidence='synthetic fixture',
                          evidence_sha256='e' * 64)
            dependency = dep.from_candidate(record, '4v100-avx512',
                                             reader_factory=lambda path: native.SquashfsReader(path, 0))
            self.assertEqual(dependency['entry'], entry)
            self.assertEqual(dependency['manifest_sha256'], dep.checksum(prefix / native.MANIFEST_PATH))
            for name, digest in dependency['api_files'].items():
                self.assertEqual(digest, dep.checksum(prefix / name))
            installed_prefix = Path(entry['identity']['install_prefix'])
            with patch.object(dep, 'installed', return_value=installed_prefix):
                script = dep.environment(dependency)
            subprocess.run(['bash', '-n'], input=script, text=True, check=True)
            observed = subprocess.check_output(['bash', '--noprofile', '--norc', '-c', script +
                '\nprintf "%s\\n" "$DEEPMD_ROOT" "$DEEPMD_PYTHON" "$DP_BACKEND_PLUGIN_PATH" "$PYTHONPATH" "$PATH"'],
                text=True, env={'PATH': '/usr/bin:/bin'}).splitlines()
            self.assertEqual(observed[:4], [str(installed_prefix), str(installed_prefix / 'bin/python'),
                entry['runtime']['set']['DP_BACKEND_PLUGIN_PATH'],
                ':'.join(entry['runtime']['prepend']['PYTHONPATH'])])
            self.assertEqual(observed[4], str(installed_prefix / 'bin') + ':/usr/bin:/bin')
            self.assertIn('/opt/devtools/gcc/14.3.0/lib64', script)
            self.assertNotIn('/opt/devtools/gcc/14.3.0/bin', script)

    def test_inspection_checks_bundled_inventory_and_rejects_wrong_loaded_library(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dependency = locked(root)
            prefix = root / 'gpumd'
            metadata = prefix / 'share/sai'
            metadata.mkdir(parents=True)
            (prefix / 'bin').mkdir()
            baseline = root / 'baseline'
            (baseline / 'bin').mkdir(parents=True)
            for name in science.COMMANDS:
                (prefix / 'bin' / name).write_bytes(b'fixture')
            (metadata / 'executables.txt').write_text(' '.join(science.COMMANDS))
            (metadata / 'deepmd-dependency.json').write_text(json.dumps(dependency))
            (metadata / 'release-identity.json').write_text(json.dumps({'target': '4v100-avx512'}))
            wrong = 'libdeepmd_cc.so => ' + science.DEEPMD + '/lib/libdeepmd_cc.so (0x1)\n'

            def output(command, **kwargs):
                return wrong if command[0] == 'ldd' else ''

            with patch.object(science, 'BASELINE', baseline), \
                    patch.object(dep, 'installed') as installed, \
                    patch.object(science.subprocess, 'check_output', side_effect=output):
                with self.assertRaisesRegex(ValueError, 'different DeePMD library'):
                    science.inspect(prefix)
                installed.assert_called_once_with(dependency)

    def test_native_environment_and_baseline_resolve_separate_matching_abis(self):
        with tempfile.TemporaryDirectory() as folder:
            locked_dep = locked(Path(folder))
            old, environment, reports = test_gpumd.GpumdDeliveryTests.entry(None, 'development', '4v100-avx512')
            prefix = locked_dep['identity']['install_prefix']
            environment.update(DEEPMD_ROOT=prefix, DEEPMD_PYTHON=prefix + '/bin/python',
                               DP_BACKEND_PLUGIN_PATH=prefix + '/lib', PYTHONPATH=prefix + '/lib/python3.13/site-packages')
            environment['LD_LIBRARY_PATH'] = prefix + '/lib:' + environment['LD_LIBRARY_PATH']
            reports['gpumd'] += 'libdeepmd_cc.so => ' + prefix + '/lib/libdeepmd_cc.so (0x1)\n'
            entry = science.build_native_entry(old['identity'], old['identity']['install_prefix'],
                                               environment, reports, dependency=locked_dep)
            self.assertEqual(entry['bundled_dependencies'], [dep.declaration(locked_dep)])
            self.assertNotIn(prefix, entry['external_roots'])
            baseline = science.baseline_environment(environment, locked_dep)
            self.assertEqual(baseline['DEEPMD_ROOT'], science.DEEPMD)
            self.assertNotIn(prefix, baseline['LD_LIBRARY_PATH'])
            self.assertNotIn(prefix, baseline['PYTHONPATH'])
            reports['gpumd'] = reports['gpumd'].replace(prefix, science.DEEPMD)
            with self.assertRaisesRegex(ValueError, 'different DeePMD'):
                science.build_native_entry(old['identity'], old['identity']['install_prefix'],
                                           environment, reports, dependency=locked_dep)

    @unittest.skipUnless(shutil.which('mksquashfs') and shutil.which('unsquashfs'), 'Squashfs tools required')
    def test_export_carries_dependency_and_refuses_missing_or_wrong_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dependency = locked(root)
            dp_entry = dependency['entry']
            identity = make_identity('gpumd', 'development', 'master', 'a' * 40,
                                     'master-2026-11-23', 'c' * 64, '4v100-avx512')
            gp_entry = dict(identity=identity, commands={'gpumd': 'bin/gpumd'}, external_roots=[],
                bundled_dependencies=[dep.declaration(dependency)], runtime=dict(modules=[], prepend={},
                    set={'DEEPMD_ROOT': dependency['identity']['install_prefix']}))
            source = root / 'rootfs'
            for entry in (gp_entry, dp_entry):
                prefix = source / entry['identity']['install_prefix'].lstrip('/')
                (prefix / 'bin').mkdir(parents=True, mode=0o755)
                prefix.chmod(0o755)
                binary = prefix / next(iter(entry['commands'].values()))
                binary.write_text('#!/bin/sh\nexit 0\n')
                binary.chmod(0o555)
            native.write_manifests([dp_entry], source)
            dp_manifest = source / dependency['identity']['install_prefix'].lstrip('/') / native.MANIFEST_PATH
            dependency['manifest_sha256'] = dep.checksum(dp_manifest)
            gp_entry['bundled_dependencies'] = [dep.declaration(dependency)]
            native.write_manifests([gp_entry, dp_entry], source)
            with self.assertRaisesRegex(ValueError, 'bundled DeePMD installation'):
                native.inventory([gp_entry], source)
            wrong = copy.deepcopy(gp_entry)
            wrong['bundled_dependencies'][0]['identity'] = locked(root, '16v100-avx2')['identity']
            with self.assertRaisesRegex(ValueError, 'target'):
                validate_native_entry(wrong)
            image = root / 'bundle.squashfs'
            subprocess.run(['mksquashfs', str(source), str(image), '-noappend', '-no-progress', '-processors', '1'],
                           check=True, stdout=subprocess.DEVNULL)
            reader = lambda path: native.SquashfsReader(path, 0)
            selected = reader(image).manifest(prefixes=[identity['install_prefix']])
            self.assertEqual(len(selected['entries']), 2)
            exported = native.export_delivery(image, dep.checksum(image), root / 'export',
                                               [identity, dependency['identity']], reader_factory=reader)
            self.assertTrue(exported['roundtrip_verified'])
            gp_receipt = next(row for row in exported['receipt']['installations']
                              if row['expected_install_prefix'] == identity['install_prefix'])
            self.assertEqual(gp_receipt['required_install_prefixes'], [dependency['identity']['install_prefix']])


if __name__ == '__main__':
    unittest.main()
