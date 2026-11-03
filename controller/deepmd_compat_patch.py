#!/usr/bin/env python3
"""Backport a reviewed PyTorch dispatcher guard to the installed Python package.

Patching after wheel installation preserves the upstream version metadata and
checkout. The package manifest and this receipt bind the changed runtime file.
"""
import ast
import hashlib
import json
from pathlib import Path
import sys

FILE = 'deepmd/pt_expt/utils/tabulate_ops.py'
ORIGINAL_SHA256 = '222644ad4bed33af20675e1163829134c598997fb04fba809ee479fdbe489ee9'
UPSTREAM_COMMIT = '1ded5d2429467fb16cb1d6c3d3059fac881f82a7'
ANCHOR = '    try:\n        torch.library.register_fake(op_name)(fn)\n'
GUARD = '''    # CompositeImplicitAutograd operators use their decomposition on fake
    # tensors; PyTorch forbids a separate Meta registration for such operators.
    if torch._C._dispatch_has_kernel_for_dispatch_key(
        op_name, "CompositeImplicitAutograd"
    ):
        return
'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


def apply(package):
    path = Path(package) / FILE
    original = path.read_bytes() if path.exists() else None
    before = digest(original) if original is not None else None
    changed = before == ORIGINAL_SHA256
    if changed:
        text = original.decode()
        if text.count(ANCHOR) != 1:
            raise ValueError('reviewed DeePMD dispatcher patch anchor changed')
        updated = text.replace(ANCHOR, GUARD + ANCHOR).encode()
        ast.parse(updated)
        path.write_bytes(updated)
    else:
        updated = original
    return dict(schema=1, patch='upstream-composite-implicit-autograd-guard',
                upstream_commit=UPSTREAM_COMMIT, file=FILE, changed=changed,
                original_sha256=before, installed_sha256=digest(updated) if updated is not None else None)


def verify(package, record):
    if (record.get('schema') != 1 or record.get('file') != FILE or
            record.get('patch') != 'upstream-composite-implicit-autograd-guard' or
            record.get('upstream_commit') != UPSTREAM_COMMIT or type(record.get('changed')) is not bool):
        raise ValueError('invalid DeePMD compatibility receipt')
    path = Path(package) / FILE
    observed = path.read_bytes() if path.exists() else None
    if (digest(observed) if observed is not None else None) != record['installed_sha256']:
        raise ValueError('installed DeePMD compatibility file changed')
    if record['changed']:
        if (observed is None or observed.decode().count(GUARD) != 1 or
                digest(observed.decode().replace(GUARD, '').encode()) != ORIGINAL_SHA256 or
                record['original_sha256'] != ORIGINAL_SHA256):
            raise ValueError('DeePMD compatibility backport differs from reviewed source')
    elif record['original_sha256'] != record['installed_sha256']:
        raise ValueError('unrecorded DeePMD compatibility change')
    return record


if __name__ == '__main__':
    print(json.dumps(apply(sys.argv[1]), sort_keys=True))
