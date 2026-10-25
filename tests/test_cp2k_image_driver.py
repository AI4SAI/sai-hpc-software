import argparse
import json
import subprocess
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import cp2k_image_driver as driver
import software_controller as software
from remote_controller import container_command
from source_cache import checksum


class ImageDriverTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.run = self.root / 'runs/build'
        for name in ('runtime', 'results'):
            (self.run / name).mkdir(parents=True)
        self.binary = self.root / 'driver/bin/fuse2fs'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_bytes(b'fixture-driver')
        self.binary.chmod(0o555)
        self.config = self.root / 'site.conf'
        self.config.write_text('mount home = yes\nbinary path = $PATH:/usr/bin\n')
        for value in (patch.object(driver, 'driver_path', return_value=self.binary),
                      patch.object(driver, 'DRIVER_SHA256', checksum(self.binary)),
                      patch.object(driver, 'SITE_CONFIG', self.config)):
            value.start()
            self.addCleanup(value.stop)

    def test_private_driver_precedes_bundled_path_and_receipt_survives_clone(self):
        config = driver.prepare(self.root, self.run)
        self.assertTrue(config.is_relative_to(self.run / 'runtime'))
        self.assertEqual(config.read_text().splitlines()[-1],
                         'binary path = ' + str(self.binary.parent) + ':$PATH:/usr/bin:/bin:/usr/sbin:/sbin')
        record = json.loads((self.run / 'results/image-driver.json').read_text())
        driver.validate_receipt(self.root, record)
        self.assertEqual(record['config_sha256'], checksum(config))
        self.assertEqual(driver.prepare(self.root, self.run), config)
        application = self.root / 'runs/application'
        for name in ('runtime', 'results'):
            (application / name).mkdir(parents=True)
        (application / 'results/image-driver.json').write_bytes((self.run / 'results/image-driver.json').read_bytes())
        driver.prepare(self.root, application)
        self.assertEqual(checksum(application / 'results/image-driver.json'),
                         checksum(self.run / 'results/image-driver.json'))

    def test_changed_binary_and_configuration_fail_before_mount(self):
        driver.prepare(self.root, self.run)
        self.binary.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'writable or changed'):
            driver.prepare(self.root, self.run)
        self.binary.write_bytes(b'changed-driver')
        self.binary.chmod(0o555)
        with self.assertRaisesRegex(ValueError, 'writable or changed'):
            driver.prepare(self.root, self.run)
        self.binary.chmod(0o755)
        self.binary.write_bytes(b'fixture-driver')
        self.binary.chmod(0o555)
        self.config.write_text('different site configuration\n')
        with self.assertRaisesRegex(ValueError, 'configuration or receipt changed'):
            driver.prepare(self.root, self.run)

    def test_redirected_driver_or_config_is_rejected(self):
        driver.prepare(self.root, self.run)
        config = self.run / 'runtime/apptainer-fuse2fs.conf'
        config.unlink()
        config.symlink_to(self.config)
        with self.assertRaisesRegex(ValueError, 'untrusted'):
            driver.prepare(self.root, self.run)
        config.unlink()
        self.binary.unlink()
        self.binary.symlink_to(self.config)
        with self.assertRaisesRegex(ValueError, 'untrusted'):
            driver.prepare(self.root, self.run)

    def test_exec_wrapper_preserves_all_isolation_and_bind_arguments(self):
        command = container_command('/home/user/base.sif', ['/bin/true'],
                                    overlay=self.run / 'work.ext3', jobs=8, gpu=True)
        command[2:2] = ['--bind', str(self.run / 'results') + ':/results:rw']
        wrapped = driver.wrap_command(self.root, self.run, '/home/user/controller', command)
        self.assertEqual(wrapped[4:], command[1:])
        self.assertEqual(wrapped[4:7], ['exec', '--bind', str(self.run / 'results') + ':/results:rw'])
        with self.assertRaises(ValueError):
            driver.wrap_command(self.root, self.run, '/home/user/controller', ['apptainer', 'shell'])

    def test_build_export_and_emit_use_driver_but_final_sif_verifies_independently(self):
        args = argparse.Namespace(run_id='cp2k-test', software='cp2k', sha='a' * 40,
                                  version='2026.2', target='16v100-avx2', jobs=8, minutes=240,
                                  overlay_mb=16384, track='release', source_ref='v2026.2')
        with patch.object(software, 'ROOT', self.root):
            script = software.render_job(args)
        mounts = [shlex.split(line.split(' > ')[0]) for line in script.splitlines()
                  if '--overlay' in line]
        self.assertEqual(len(mounts), 3)
        for argv in mounts:
            self.assertTrue(argv[1].endswith('/cp2k_image_driver.py'))
            self.assertEqual(argv[4], 'exec')
            self.assertIn('--containall', argv)
        final = next(line for line in script.splitlines() if 'cp2k_container_entry.sh verify' in line)
        self.assertTrue(final.startswith('apptainer exec '))
        self.assertNotIn('--overlay', final)
        self.assertIn('sai-cp2k-build-16v100-avx2-' + software.recipe_fingerprint('cp2k')[:12], script)

    def test_mount_retry_is_bounded_and_preserves_binary_stdout(self):
        message = 'FATAL: container creation failed: mount hook function failure: fuse2fs failed to mount overlay in 10s'
        for code, text, accepted in ((255, message, True), (1, message, False),
                                     (255, 'CP2K failed', False), (255, 'fuse2fs failed', False)):
            self.assertEqual(driver.mount_timeout(code, text.encode()), accepted)
        # Exercise the actual subprocess stream, including non-text squashfs bytes.
        program = self.root / 'exercise.py'
        program.write_text('import sys; sys.path.insert(0, ' + repr(str(Path(driver.__file__).parent)) + '); '
                           'import cp2k_image_driver as d; d.time.sleep=lambda _:None; '
                           'raise SystemExit(d.execute(sys.argv[2:], sys.argv[1]))')
        payload = self.root / 'payload.py'
        counter = self.root / 'count'
        payload.write_text('from pathlib import Path; import sys; p=Path(' + repr(str(counter)) + '); '
                           'n=int(p.read_text()) if p.exists() else 0; p.write_text(str(n+1)); '
                           'sys.stderr.write(' + repr(message) + ' if n<2 else ""); '
                           'sys.stdout.buffer.write(b"" if n<2 else bytes([0,255,13,10])); '
                           'sys.exit(255 if n<2 else 0)')
        result = subprocess.run([sys.executable, str(program), str(self.run), sys.executable, str(payload)], capture_output=True)
        self.assertEqual((result.returncode, result.stdout), (0, bytes([0,255,13,10])))
        self.assertEqual(counter.read_text(), '3')
        self.assertEqual(len(list((self.run / 'results').glob('mount-retry-*.json'))), 2)
        payload.write_text('import sys; sys.stderr.write("payload failed"); sys.exit(255)')
        result = subprocess.run([sys.executable, str(program), str(self.run), sys.executable, str(payload)], capture_output=True)
        self.assertEqual(result.returncode, 255)
        self.assertNotIn(b'CONTAINER_MOUNT_RETRY', result.stderr)
        # The observed Apptainer teardown error exits zero even though the
        # writable driver was killed. Preserve the failure, with no retry.
        message = ('INFO: Terminating fuse2fs after timeout\n'
                   'INFO: Cleanup error: while stopping driver for overlay: fuse2fs exited: killed\n')
        payload.write_text('import sys; sys.stderr.write(' + repr(message) + '); sys.exit(0)')
        result = subprocess.run([sys.executable, str(program), str(self.run), sys.executable, str(payload)], capture_output=True)
        self.assertEqual(result.returncode, 74)
        self.assertNotIn(b'CONTAINER_MOUNT_RETRY', result.stderr)
        failures = list((self.run / 'results').glob('driver-cleanup-failure-*.json'))
        self.assertEqual(len(failures), 1)
        self.assertEqual(json.loads(failures[0].read_text())['returncode'], 0)


if __name__ == '__main__':
    unittest.main()
