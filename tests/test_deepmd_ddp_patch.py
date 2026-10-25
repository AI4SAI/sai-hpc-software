import ast
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
from deepmd_ddp_patch import ANCHOR, PATCH, apply


class CompressionPatchTests(unittest.TestCase):
    def test_only_compression_parameter_lists_are_frozen(self):
        class Parameter:
            requires_grad = True

        class ParameterList(list):
            def requires_grad_(self, enabled):
                for parameter in self:
                    parameter.requires_grad = enabled

        compression = [Parameter(), Parameter()]
        weight, unrelated_empty = Parameter(), Parameter()
        child = SimpleNamespace(compress_info=ParameterList(compression[:1]),
                                compress_data=ParameterList(compression[1:]), weight=weight)
        parent = SimpleNamespace(compress_info='unrelated attribute',
                                 other=ParameterList([unrelated_empty]))
        wrapper = SimpleNamespace(modules=lambda: (parent, child))
        import textwrap
        exec(textwrap.dedent(PATCH), dict(self=SimpleNamespace(wrapper=wrapper),
                                        torch=SimpleNamespace(nn=SimpleNamespace(ParameterList=ParameterList))))
        self.assertTrue(weight.requires_grad)
        self.assertTrue(unrelated_empty.requires_grad)
        self.assertTrue(all(not value.requires_grad for value in compression))
        self.assertIs(child.compress_info[0], compression[0])

    def test_patch_is_before_ddp_and_rejects_source_drift_or_reapplication(self):
        source = 'class Trainer:\n    def __init__(self):\n' + ANCHOR + '            self.wrapper = DDP(self.wrapper)\n'
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'deepmd/pt/train/training.py'
            path.parent.mkdir(parents=True)
            path.write_text(source)
            result = apply(temporary)
            ast.parse(path.read_text())
            self.assertLess(path.read_text().index('tables.requires_grad_(False)'),
                            path.read_text().index('DDP('))
            self.assertNotEqual(result['original_sha256'], result['patched_sha256'])
            with self.assertRaisesRegex(ValueError, 'already applied'):
                apply(temporary)
            for changed in (source.replace('DDP(', 'AnotherWrapper('), source + source,
                            source.replace('torch.cuda.set_device(LOCAL_RANK)', 'select_device()')):
                path.write_text(changed)
                with self.assertRaisesRegex(ValueError, 'trainer changed'):
                    apply(temporary)
                self.assertEqual(path.read_text(), changed)
