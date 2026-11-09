import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
import app_dependency_stage as stage
import devtools_install as installer


class ApplicationDependencies(unittest.TestCase):
    def test_app_prefixes_do_not_allow_other_application_trees(self):
        for name, prefix in installer.APP_LIBRARY_PREFIXES.items():
            self.assertEqual(installer.package_prefix(dict(name=name, prefix=prefix)), Path(prefix))
            for changed in (prefix+'/../other', '/opt/apps/abacus', prefix+'-replacement'):
                with self.assertRaises(ValueError):
                    installer.package_prefix(dict(name=name, prefix=changed))

    def test_release_family_cannot_install_the_other_familys_payload(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            manifest=root/'files.json'; manifest.write_text('[]\n')
            release=dict(schema=1,purpose='application-dependencies',partitions=['DSPRHBM'],
                packages=[dict(name='qe-hdf5',prefix=installer.APP_LIBRARY_PREFIXES['qe-hdf5'],
                    manifest='files.json',manifest_sha256=installer.checksum(manifest),payload='payload')])
            def save():
                (root/'release.json').write_text(json.dumps(release))
                (root/'release.sha256').write_text(installer.checksum(root/'release.json'))
            save()
            self.assertEqual(installer.read_release(root)['purpose'],'application-dependencies')
            release['purpose']='cp2k-spack-devtools'
            save()
            with self.assertRaisesRegex(ValueError,'release family'):
                installer.read_release(root)

    def test_build_is_project_backed_and_only_reads_host_dependencies(self):
        project=Path('/org/org-open-sai/proj-sai-hpc-software')
        control=project/'controller/app-dependency-test'
        run=project/'runs/app-dependency-test'
        script=stage.render(project,control,run,project/'cache/qe-input')
        subprocess.run(['bash','-n'],input=script,text=True,check=True)
        self.assertIn('#SBATCH --account=proj-sai-hpc-software',script)
        self.assertIn('--ignore-fakeroot-command',script)
        self.assertIn('cp2k_image_driver.py',script)
        self.assertIn(str(run/'work.ext3'),script)
        self.assertNotIn('/opt/apps:/opt/apps',script)
        self.assertNotIn('SBATCH --partition=16V100',script)


if __name__ == '__main__':
    unittest.main()
