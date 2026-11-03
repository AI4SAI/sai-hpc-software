"""Backport upstream dispatcher behavior without suppressing unrelated errors."""
import ast
import copy
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'controller'))
import deepmd_compat_patch as compat


class DispatcherBackportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.package = Path(self.temporary.name)
        self.path = self.package / compat.FILE
        self.path.parent.mkdir(parents=True)
        self.original = (ROOT / 'tests/fixtures/deepmd-tabulate-v3.2.0b0.py').read_bytes()
        self.assertEqual(compat.digest(self.original), compat.ORIGINAL_SHA256)
        self.path.write_bytes(self.original)

    def function(self, composite, registration_error=None):
        compat.apply(self.package)
        node, = [node for node in ast.parse(self.path.read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name == '_try_register_fake']
        register = Mock(return_value=Mock(), side_effect=registration_error)
        context = dict(Callable=object, Any=object, _registered=set(),
                       torch=SimpleNamespace(library=SimpleNamespace(register_fake=register),
                           _C=SimpleNamespace(_dispatch_has_kernel_for_dispatch_key=Mock(return_value=composite))))
        # Only annotations need typing names; evaluate none of upstream's
        # module-level imports or registration side effects in the unit test.
        node.returns = None
        for arg in node.args.args: arg.annotation = None
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<reviewed-backport>', 'exec'), context)
        return context['_try_register_fake'], register

    def test_composite_decomposition_is_kept_and_other_ops_still_register(self):
        function, register = self.function(True)
        function('deepmd::tabulate_fusion_se_a', object())
        register.assert_not_called()
        function, register = self.function(False)
        function('deepmd::tabulate_fusion_se_a', object())
        function('deepmd::tabulate_fusion_se_a', object())
        register.assert_called_once_with('deepmd::tabulate_fusion_se_a')

    def test_unrelated_registration_errors_still_fail(self):
        function, register = self.function(False, RuntimeError('operator does not exist'))
        with self.assertRaisesRegex(RuntimeError, 'does not exist'):
            function('deepmd::tabulate_fusion_se_a', object())
        register.assert_called_once()

    def test_receipt_verifies_the_exact_installed_backport_and_rejects_tampering(self):
        receipt = compat.apply(self.package)
        self.assertTrue(receipt['changed'])
        self.assertEqual(compat.verify(self.package, receipt), receipt)
        self.assertEqual(self.path.read_text().replace(compat.GUARD, '').encode(), self.original)
        self.path.write_text(self.path.read_text() + '\n# altered\n')
        with self.assertRaisesRegex(ValueError, 'file changed'):
            compat.verify(self.package, receipt)
        changed = dict(receipt, installed_sha256=compat.digest(self.path.read_bytes()))
        with self.assertRaisesRegex(ValueError, 'reviewed source'):
            compat.verify(self.package, changed)
        changed = dict(receipt, file='../another-package.py')
        with self.assertRaisesRegex(ValueError, 'invalid'):
            compat.verify(self.package, changed)

    def test_unknown_or_upstream_fixed_sources_are_left_unchanged(self):
        for content in (b'# another upstream version\n', self.original + b'\n'):
            self.path.write_bytes(content)
            receipt = compat.apply(self.package)
            self.assertFalse(receipt['changed'])
            self.assertEqual(self.path.read_bytes(), content)
            compat.verify(self.package, receipt)
        self.path.unlink()
        receipt = compat.apply(self.package)
        self.assertFalse(receipt['changed'])
        compat.verify(self.package, receipt)
