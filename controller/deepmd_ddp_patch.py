#!/usr/bin/env python3
"""Freeze DeePMD's inference-only compression tables before training wrappers.

Some descriptors create these ParameterLists with requires_grad=True; DPA2
also re-enables their gradients when assembling its children. They are never
used by the uncompressed training graph. Keep their state-dict names and
optimizer ordering, but exclude them from DDP's gradient reduction.
"""
import ast
import hashlib
import json
from pathlib import Path
import sys


ANCHOR = "        if self.is_distributed:\n            torch.cuda.set_device(LOCAL_RANK)\n"
PATCH = '''        # Compression tables are inference state, not trainable weights.
        # Freeze after descriptor assembly: parent descriptors may have reset
        # requires_grad on their children. Keep ParameterLists for checkpoint
        # compatibility; DDP must not wait for their nonexistent gradients.
        for module in self.wrapper.modules():
            for name in ("compress_info", "compress_data"):
                tables = getattr(module, name, None)
                if isinstance(tables, torch.nn.ParameterList):
                    tables.requires_grad_(False)

'''


def apply(source):
    path = Path(source) / 'deepmd/pt/train/training.py'
    original = path.read_text()
    if PATCH in original:
        raise ValueError('DeePMD compression patch was already applied')
    tree = ast.parse(original)
    ddp = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
           and isinstance(node.func, ast.Name) and node.func.id == 'DDP']
    if len(ddp) != 1:
        raise ValueError('DeePMD trainer changed: review the compression patch before building')
    sha = lambda value: hashlib.sha256(value.encode()).hexdigest()
    # The accepted 3.2.0 release already handles unused parameters. Leave its
    # tagged tree clean so setuptools-scm retains the exact release version.
    if any(option.arg == 'find_unused_parameters' and isinstance(option.value, ast.Constant)
           and option.value.value is True for option in ddp[0].keywords):
        return dict(patch='not-required-upstream-handles-unused-parameters', changed=False,
                    file='deepmd/pt/train/training.py',
                    original_sha256=sha(original), patched_sha256=sha(original))
    if original.count(ANCHOR) != 1:
        raise ValueError('DeePMD trainer changed: review the compression patch before building')
    position = original.index(ANCHOR)
    if original[:position].count('\n') >= ddp[0].lineno:
        raise ValueError('compression tables must be frozen before DDP construction')
    updated = original.replace(ANCHOR, PATCH + ANCHOR)
    ast.parse(updated)
    path.write_text(updated)
    return dict(patch='freeze-inference-compression-tables-v1', changed=True, file='deepmd/pt/train/training.py',
                original_sha256=sha(original), patched_sha256=sha(updated))


if __name__ == '__main__':
    print(json.dumps(apply(sys.argv[1]), sort_keys=True))
