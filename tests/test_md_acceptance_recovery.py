"""Recovery preserves compiled identity while pinning a separate verifier snapshot."""
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
from md_acceptance_recovery import capture, validate
from md_tracking import fingerprint
from test_md_controller import delivery_request


class RecoveryBindingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        source = Path(__file__).resolve().parents[1] / 'controller'
        self.original = self.root / 'controller/build'
        self.control = self.root / 'controller/acceptance'
        for directory in (self.original, self.control):
            shutil.copytree(source, directory, ignore=shutil.ignore_patterns('__pycache__'))
        with (self.control / 'md_runtime.sh').open('a') as stream:
            stream.write('\n# synthetic acceptance fix\n')
        self.delivery = delivery_request(recipe=fingerprint(self.original))
        self.request = self.root / 'runs/build/request.json'
        self.request.parent.mkdir(parents=True)
        self.request.write_text(json.dumps(dict(run_id='build', controller=str(self.original), delivery=self.delivery)))
        self.binding = capture(self.root, self.control, 'build', self.delivery, 'Repair baseline plugin lookup')

    def check(self, binding=None):
        return validate(self.binding if binding is None else binding, self.root, self.control, 'build', self.delivery)

    def test_both_distinct_recipes_are_retained(self):
        self.assertEqual(self.check(), self.original)
        self.assertEqual(self.binding['build_recipe_sha256'], self.delivery['recipe_sha256'])
        self.assertNotEqual(self.binding['build_recipe_sha256'], self.binding['acceptance_recipe_sha256'])

    def test_changed_compiled_controller_is_rejected(self):
        (self.original / 'md_runtime.sh').write_text('changed original')
        with self.assertRaisesRegex(ValueError, 'original compiled controller'):
            self.check()

    def test_changed_acceptance_controller_is_rejected(self):
        (self.control / 'md_runtime.sh').write_text('changed acceptance runtime')
        with self.assertRaisesRegex(ValueError, 'binding changed'):
            self.check()

    def test_changed_original_request_is_rejected_even_with_same_delivery(self):
        request = json.loads(self.request.read_text())
        request['job_script_sha256'] = 'a' * 64
        self.request.write_text(json.dumps(request))
        with self.assertRaisesRegex(ValueError, 'binding changed'):
            self.check()

    def test_unknown_fields_or_replaced_build_are_rejected(self):
        for changes in ({'build_run': 'another-build'}, {'build_recipe_sha256': 'a' * 64},
                        {'acceptance_recipe_sha256': 'b' * 64}, {'unchecked': True}, {'reason': ''}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.check(dict(self.binding, **changes))

    def test_symlink_and_same_controller_are_rejected(self):
        link = self.root / 'controller/link'
        link.symlink_to(self.control)
        for control in (link, self.original):
            with self.subTest(control=control), self.assertRaises(ValueError):
                capture(self.root, control, 'build', self.delivery, 'explicit reason')

    def test_changed_source_delivery_is_rejected(self):
        delivery = copy.deepcopy(self.delivery)
        delivery['plan']['sources']['lammps']['sha'] = 'a' * 40
        with self.assertRaisesRegex(ValueError, 'delivery changed'):
            validate(self.binding, self.root, self.control, 'build', delivery)
