"""SIF evidence must be new; exported native paths and module guards agree."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import spingnn_release as release
import spingnn_sif as sif
from spingnn_package import entry, version_module
from spingnn_install import install
from native_module import validate_native_entry, render_native_selector
from export_native import write_manifests, validate_packaged_modules


def value(target='dsprhbm', run='test'):
    return release.delivery(dict(software='spingnn2', repository='Hyyu-Lab/SpinGNN', track='release',
        requested_ref='latest-release', status='resolved', source_ref='v2.0.0', source_version='v2.0.0',
        source_sha='2f4e7ca93e64b7113df1a9e9eafae4c1dc8279ec', target=target), run)


def lua_cases():
    identity = value()['identity']
    prefix = identity['install_prefix']
    codes = dict(fragment=release.module_text(identity, prefix), version=version_module(identity),
                 release=release.selector_text())
    # Exercise executable Lua, not substring matching or a Python translation.
    head = 'local codes = {\n' + '\n'.join(name + '=[====[' + code + ']====],' for name, code in codes.items()) + '\n}\n'
    return head + r'''
local compile = loadstring or load
local count = 0
local function exercise(kind, action, job, partition, expected, saved, missing_flag)
  local calls, reads = 0, 0
  local env = {SLURM_JOB_ID=job, SLURM_JOB_PARTITION=partition}
  if saved then
    env.SAI_SPINGNN_LOADED_PREFIX = "/opt/apps/spingnn/v2.0.0/DSPRHBM"
    env.SAI_SPINGNN_LOADED_PARTITION = "DSPRHBM"
    env._SAI_SPINGNN_DEVTOOLS_ADDED = "0"
    env.SAI_SPINGNN_RELEASE_FRAGMENT = "''' + release.site()['root'] + r'''/modulefiles/spingnn/dsprhbm/accepted.lua"
  end
  if missing_flag then env._SAI_SPINGNN_DEVTOOLS_ADDED = nil end
  os.getenv = function(k) return env[k] end
  mode = function() return action end
  whatis = function() end; help = function() end
  LmodError = function(message) error(message, 0) end
  local effect = function() calls=calls+1 end
  conflict=effect; depends_on=effect; prepend_path=effect; pushenv=effect
  isFile = function() reads=reads+1; return true end
  io.open = function() reads=reads+1; return {read=function() return "accepted.lua" end,close=function() end} end
  loadfile = function() reads=reads+1; return effect end
  local ok, detail = pcall(assert(compile(codes[kind])))
  assert(ok == expected, kind .. "/" .. action .. "/" .. tostring(job) .. "/" .. tostring(partition) .. ": " .. tostring(detail))
  if not expected or (action ~= "load" and action ~= "unload") then
    assert(calls == 0 and reads == 0, "rejected/metadata request accessed runtime")
  else assert(calls > 0, "runtime was not selected") end
  count=count+1
end
for kind, _ in pairs(codes) do
  for _, bad in ipairs({"", "0", "-1", "01", "x", "1.0", "1;bad"}) do exercise(kind,"load",bad,"DSPRHBM",false,false) end
  exercise(kind,"load",nil,"DSPRHBM",false,false)
  for _, bad in ipairs({"", "UNKNOWN", "CPU-MISC", "8A100M40", "8A100M80"}) do exercise(kind,"load","123",bad,false,false) end
  exercise(kind,"load","123",nil,false,false)
  exercise(kind,"load","123","DSPRHBM",true,false)
  for _, action in ipairs({"help", "whatis", "show"}) do exercise(kind,action,nil,nil,true,false) end
  exercise(kind,"unload",nil,nil,true,true)
  exercise(kind,"unload","0","CPU-MISC",true,true)
  exercise(kind,"unload",nil,nil,false,false)
end
for _, kind in ipairs({"version", "release"}) do
  for _, part in ipairs({"4V100", "16V100", "8V100V0"}) do exercise(kind,"load","123",part,true,false) end
end
exercise("fragment", "unload", nil, nil, false, true, true)
print("SPINGNN_MODULE_CASES_PASSED " .. count)
'''


class SIFTests(unittest.TestCase):
    def test_native_receipts_never_skip_sif_and_adoption_is_rejected(self):
        delivery = value()
        with patch.object(release, 'read') as read:
            self.assertIsNone(release.bootstrap(delivery['identity']))
            read.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'cannot qualify a SIF'):
            release.adopt('test', delivery)

    def test_container_build_and_final_acceptance_have_distinct_safe_binds(self):
        directory = release.task('test')
        for target in ('dsprhbm', '4v100-avx512', '16v100-avx2', '8v100v0-avx512'):
            identity = value(target)['identity']
            with patch.dict(os.environ, SLURM_JOB_ID='123', SLURM_JOB_PARTITION=identity['partition']):
                build = sif.container(directory, identity, 'build')
                final = sif.container(directory, identity, 'verify')
            self.assertIn(str(directory / 'work.ext3'), build)
            self.assertNotIn('--overlay', final)
            self.assertIn(str(directory / 'result.sif'), final)
            self.assertIn('SLURM_JOB_ID=123', final)
            self.assertIn('--nv', final) if identity['gpus'] else self.assertNotIn('--nv', final)
            for command in (build, final):
                self.assertIn('--cleanenv', command)
                self.assertNotIn('/opt/apps:/opt/apps:ro', command)
                self.assertFalse(any(arg.startswith('/home/') for arg in command))
            self.assertFalse(any('conda_env' in arg or '/sources' in arg for arg in final))

    def test_generic_export_manifests_support_exact_spingnn_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = value()['identity']
            prefix = root / identity['install_prefix'].lstrip('/')
            (prefix / 'bin').mkdir(parents=True, mode=0o755)
            for path in [prefix, *prefix.parents]:
                if path == root: break
                path.chmod(0o755)
            binary = prefix / 'bin/lmp_spingnn2'
            binary.write_text('#!/bin/sh\nexit 0\n')
            binary.chmod(0o755)
            actual = validate_native_entry(entry(identity))
            manifest = write_manifests([actual], root)
            validate_packaged_modules(manifest)
            self.assertEqual(manifest['entries'][0]['identity']['install_prefix'], '/opt/apps/spingnn/v2.0.0/DSPRHBM')
            self.assertNotIn('8A100', render_native_selector(identity))
            self.assertNotIn('CPU-MISC', render_native_selector(identity))

    def test_completed_stage_resume_rechecks_request_and_output_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'request.json').write_text('{}')
            artifact = root / 'result.sif'
            def finish():
                artifact.write_bytes(b'complete image')
                return [artifact]
            action = Mock(side_effect=finish)
            sif.stage(root, 'image', action)
            sif.stage(root, 'image', action)
            action.assert_called_once()
            artifact.write_bytes(b'changed image')
            with self.assertRaisesRegex(ValueError, 'changed completed'):
                sif.stage(root, 'image', action)
            self.assertEqual(action.call_count, 1)

    def test_failed_stage_does_not_get_a_success_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'request.json').write_text('{}')
            with self.assertRaises(OSError):
                sif.stage(root, 'build', Mock(side_effect=OSError('interrupted compiler')))
            self.assertFalse((root / 'stage-build.json').exists())

    def test_installer_refuses_existing_partition_or_different_public_module(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            identity = value()['identity']
            prefix = root / identity['install_prefix'].lstrip('/')
            prefix.mkdir(parents=True)
            marker = prefix / 'user-data'
            marker.write_bytes(b'accepted existing install')
            with patch('spingnn_install.check', return_value=dict(identity=identity)):
                with self.assertRaisesRegex(ValueError, 'already installed'):
                    install(root / 'unused', root)
                self.assertEqual(marker.read_bytes(), b'accepted existing install')
                marker.unlink(); prefix.rmdir()
                module = root / 'opt/modules/modulefiles/apps/spingnn/2.0.0.lua'
                module.parent.mkdir(parents=True)
                module.write_bytes(b'current public module')
                with self.assertRaisesRegex(ValueError, 'do not overwrite'):
                    install(root / 'unused', root)
                self.assertFalse(prefix.exists())
                self.assertEqual(module.read_bytes(), b'current public module')

    @unittest.skipUnless(shutil.which('lua') or shutil.which('lua5.4'), 'Lua interpreter unavailable locally; also execute on SAI')
    def test_lua_allocation_guards_metadata_and_saved_unload(self):
        result = subprocess.run([shutil.which('lua') or shutil.which('lua5.4'), '-'],
                                input=lua_cases(), text=True, capture_output=True, check=True)
        self.assertIn('SPINGNN_MODULE_CASES_PASSED 70', result.stdout)


if __name__ == '__main__':
    unittest.main()
