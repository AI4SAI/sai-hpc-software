import copy
import json
import os
from argparse import Namespace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
import md_controller as md
import md_tracking as tracking
import md_ci
import export_native


def delivery_request(target='4v100-avx512', recipe='e' * 64):
    def resolver(repo, ref):
        sha = ('a' if 'deepmd' in repo else 'b') * 40
        return {'sha': sha, 'ref': ref, 'version': ref + '-2026-09-13'}
    plan = tracking.resolve_track_pairs(['development'], [target], resolver)['pairs'][0]
    return dict(schema=2, plan=plan, recipe_sha256=recipe,
                identities=tracking.identify_track_pair(plan, recipe))


class MDControllerTests(unittest.TestCase):
    def pair(self, target='4v100-avx512'):
        return delivery_request(target)

    def test_both_live_sources_are_resolved_and_target_is_validated(self):
        value = self.pair()
        self.assertEqual(set(value['plan']['sources']), {'deepmd-kit', 'lammps'})
        self.assertEqual(value['plan']['version'], 'dp-development-aaaaaaaaaaaa-lmp-development-bbbbbbbbbbbb')
        with self.assertRaises(ValueError):
            self.pair('dsprhbm')

    def test_all_native_gpu_jobs_are_contained_and_never_publish(self):
        for target in tracking.TARGETS:
            script = md.render(self.pair(target), 'md-test-' + target)
            subprocess.run(['bash', '-n'], input=script, text=True, check=True)
            self.assertIn('#SBATCH --partition=' + md.TARGETS[target]['partition'], script)
            self.assertIn('--gpus-per-node=1', script)
            self.assertNotIn('#SBATCH --cpus-per-task', script)
            self.assertNotIn('#SBATCH --mem', script)
            self.assertIn('--network none', script)
            self.assertIn('/input/deepmd-kit:ro', script)
            self.assertIn('/input/lammps:ro', script)
            self.assertIn('/opt/apps:/opt/apps:ro', script)
            self.assertNotIn('current.sif', script)
            self.assertNotIn('modulefiles/', script)
            self.assertNotRegex(script, r'(?:^|[= :])/tmp(?:/|$)')
            for identity in self.pair(target)['identities'].values():
                self.assertIn(identity['install_prefix'], script)
                self.assertIn('-2026-09-13-g', identity['build_id'])

    def test_source_pair_and_resource_bounds_fail_closed(self):
        pair = self.pair()
        for bad in ({}, dict(pair, schema=1), pair['plan'], dict(pair, target='a100')):
            with self.assertRaises((ValueError, KeyError)):
                md.validate_pair(bad)
        for extras in ({'jobs': 7}, {'minutes': 181}, {'overlay_mb': 1024}):
            with self.assertRaises(ValueError):
                md.render(pair, 'test', **extras)

    def test_both_identities_bind_sources_recipe_track_and_partition(self):
        original = self.pair()
        for component in ('deepmd-kit', 'lammps'):
            for field, value in (('source_sha', 'f' * 40), ('recipe_sha256', 'f' * 64),
                                 ('partition', '16V100'), ('track', 'release'),
                                 ('install_prefix', '/opt/software/forged')):
                changed = copy.deepcopy(original)
                changed['identities'][component][field] = value
                with self.subTest(component=component, field=field), self.assertRaises(ValueError):
                    md.validate_pair(changed)

    def test_submit_monitor_sidecar_preserve_locked_delivery(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(md, 'ROOT', Path(temporary)):
            request = self.pair()
            request_file = Path(temporary) / 'delivery.json'
            request_file.write_text(json.dumps(request))
            args = Namespace(run_id='schema2-candidate', request=str(request_file), jobs=6,
                             minutes=180, overlay_mb=32768, timeout=1, interval=0)
            def run(argv, **kwargs):
                if argv[0] == 'bash':
                    return subprocess.run(argv, check=True, text=True)
                return subprocess.CompletedProcess(argv, 0, '123\n' if argv[0] == 'sbatch' else '')
            with patch.object(md, 'fingerprint', return_value=request['recipe_sha256']), patch.object(md, 'run', side_effect=run):
                md.submit(args)
                directory = md.task(args.run_id)
                record = json.loads((directory / 'request.json').read_text())
                self.assertEqual(record['delivery'], request)
                artifact = (Path(temporary) / 'containers/software/deepmd-lammps' /
                            request['plan']['selection_sha256'] / request['recipe_sha256'] /
                            request['identities']['lammps']['partition'] / (args.run_id + '.sif'))
                artifact.parent.mkdir(parents=True)
                artifact.write_bytes(b'synthetic SIF protocol fixture; never executed')
                (directory / 'artifact.path').write_text(str(artifact) + '\n')
                with patch.object(md, 'run', side_effect=[subprocess.CompletedProcess([], 0, ''),
                     subprocess.CompletedProcess([], 0, '123|COMPLETED|0:0|\n')]):
                    self.assertEqual(md.monitor(args), 0)
                sidecar = json.loads(artifact.with_suffix('.json').read_text())
                self.assertEqual(sidecar['delivery'], request)
                self.assertTrue(sidecar['build_verified'])
                self.assertFalse(sidecar['published'])
                self.assertFalse(sidecar['scientific_verified'])
                self.assertFalse(sidecar['performance_verified'])
                self.assertFalse(sidecar['native_runtime_verified'])
                record['delivery']['identities']['lammps']['partition'] = '16V100'
                (directory / 'request.json').write_text(json.dumps(record))
                with patch.object(md, 'run', side_effect=[subprocess.CompletedProcess([], 0, ''),
                     subprocess.CompletedProcess([], 0, '123|COMPLETED|0:0|\n')]), self.assertRaises(ValueError):
                    md.monitor(args)

    def test_recipe_mismatch_is_rejected_before_any_remote_submit(self):
        with tempfile.TemporaryDirectory() as temporary:
            request_file = Path(temporary) / 'delivery.json'
            request_file.write_text(json.dumps(self.pair()))
            with patch.object(md, 'fingerprint', return_value='f' * 64), patch.object(md, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'recipe differs'):
                    md.submit(Namespace(request=str(request_file)))
                run.assert_not_called()

    def test_runner_uploads_shared_helpers_and_locks_before_transport(self):
        with tempfile.TemporaryDirectory() as temporary:
            environment = dict(MD_PAIR=json.dumps(self.pair()['plan']), REMOTE_USER='unit',
                               GITHUB_SHA='c' * 40, GITHUB_RUN_ID='123', GITHUB_RUN_ATTEMPT='1',
                               RUNNER_TEMP=temporary)
            result = subprocess.CompletedProcess([], 0, json.dumps({'cache_shas': ['a' * 40, 'b' * 40]}))
            with patch.dict(os.environ, environment), patch.object(md_ci, 'run', return_value=result) as run, \
                    patch.object(md_ci.subprocess, 'run', return_value=result):
                md_ci.main()
            uploaded = json.loads((Path(temporary) / 'pair.json').read_text())
            self.assertEqual(md.validate_pair(uploaded), uploaded)
            self.assertEqual(uploaded['recipe_sha256'], tracking.fingerprint(ROOT / 'controller'))
            commands = [list(map(str, call.args[0])) for call in run.call_args_list]
            uploads = [argv[-2] for argv in commands if argv[0] == 'scp']
            for name in ('release_contract.py', 'native_module.py', 'export_native.py', 'resolve_source.py'):
                self.assertIn(str(ROOT / 'controller' / name), uploads)
            self.assertEqual(uploaded, json.loads((Path(temporary) / 'results/delivery.json').read_text()))

    def test_recipe_uses_shared_writer_for_both_native_prefixes(self):
        recipe = (ROOT / 'controller/md_build.sh').read_text()
        marker = '# Package the observed runtime directly with the shared native delivery writer.'
        self.assertLess(recipe.index('/control/md_science.py prepare'), recipe.index(marker))
        code = recipe.split(marker, 1)[1].split("<<'PY'\n", 1)[1].split('\nPY', 1)[0]
        request = self.pair()
        environment = dict(SAI_MD_DELIVERY=json.dumps(request), PATH='/usr/bin:/bin',
                           LD_LIBRARY_PATH='/usr/lib', LOADEDMODULES='cuda/12.9.1:lammps/site:cmake/3.31.6',
                           MD_SYSTEM_DEEPMD='/opt/apps/conda_env/deepmd-kit-3.2.0',
                           MD_SYSTEM_LAMMPS='/opt/apps/lammps/old', MD_SYSTEM_PLUMED='/opt/apps/plumed/plumed-2.10.1',
                           PLUMED_KERNEL='/opt/apps/plumed/plumed-2.10.1/lib/libplumedKernel.so',
                           LAMMPS_POTENTIALS=request['identities']['lammps']['install_prefix'] + '/share/lammps/potentials')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for software, command in (('deepmd-kit', 'dp'), ('lammps', 'lmp')):
                prefix = root / request['identities'][software]['install_prefix'].lstrip('/')
                (prefix / 'bin').mkdir(parents=True)
                executable = prefix / 'bin' / command
                executable.write_text('#!/bin/sh\nexit 0\n')
                executable.chmod(0o755)
            write = export_native.write_manifests
            with patch.dict(os.environ, environment, clear=True), \
                    patch.object(export_native, 'write_manifests', side_effect=lambda entries: write(entries, root)) as packaged:
                exec(compile(code, str(ROOT / 'controller/md_build.sh'), 'exec'), {})
            entries = packaged.call_args.args[0]
            self.assertEqual({item['identity']['software'] for item in entries}, {'deepmd-kit', 'lammps'})
            self.assertEqual(entries[0]['runtime']['modules'], ['cuda/12.9.1'])
            self.assertEqual(entries[0]['runtime']['prepend']['PATH'], ['/usr/bin'])
            self.assertEqual(export_native.read_installed_manifests(entries, root), export_native.inventory(entries, root))

    def test_fingerprint_tracks_actual_code_not_only_upstream_sha(self):
        first = tracking.fingerprint(ROOT / 'controller')
        self.assertRegex(first, r'^[a-f0-9]{64}$')
        self.assertEqual(first, tracking.fingerprint(ROOT / 'controller'))

    def test_runtime_and_build_require_new_package_and_native_configuration(self):
        recipe = (ROOT / 'controller/md_build.sh').read_text()
        self.assertIn('-march=native -mtune=native', recipe)
        self.assertIn('-DENABLE_TENSORFLOW=ON -DENABLE_PYTORCH=ON -DENABLE_JAX=ON', recipe)
        self.assertIn('-DDOWNLOAD_PLUMED=OFF', recipe)
        self.assertIn("['lammps']['packages']", recipe)
        self.assertIn('verify_parity', recipe)
        self.assertIn('check_dynamic', (ROOT / 'controller/md_relocate_audit.py').read_text())


if __name__ == '__main__':
    unittest.main()
