#!/usr/bin/env python3
"""Backport reviewed Python runtime fixes to the installed DeePMD package.

Patching after wheel installation preserves the upstream version metadata and
checkout. The package manifest and this receipt bind the changed runtime files.
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
PATCHES = (
    dict(name='composite-implicit-autograd-guard', file=FILE,
         original_sha256=ORIGINAL_SHA256, upstream_commit=UPSTREAM_COMMIT,
         before=ANCHOR, after=GUARD + ANCHOR),
    dict(name='python-output-definition-for-scripted-model',
         file='deepmd/pt/infer/deep_eval.py',
         original_sha256='241c011446538262fd2d1d79abd902c1aca6d2f45df6abd030e5f20585fe8c35',
         upstream_commit='8cfd46e37448feca361ff3427a6c2b514ce5a2bb',
         before='            inner.model_output_def(),\n',
         after='            self.output_def,\n'),
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def apply(package):
    records = []
    for patch in PATCHES:
        path = Path(package) / patch['file']
        original = path.read_bytes() if path.exists() else None
        before = digest(original) if original is not None else None
        changed = before == patch['original_sha256']
        if changed:
            text = original.decode()
            if text.count(patch['before']) != 1 or patch['after'] in text:
                raise ValueError('reviewed DeePMD patch anchor changed')
            updated = text.replace(patch['before'], patch['after']).encode()
            ast.parse(updated)
            path.write_bytes(updated)
        else:
            updated = original
        records.append(dict(patch=patch['name'], upstream_commit=patch['upstream_commit'],
                            file=patch['file'], changed=changed, original_sha256=before,
                            installed_sha256=digest(updated) if updated is not None else None))
    return dict(schema=2, changed=any(record['changed'] for record in records), files=records)


def verify(package, record):
    if (record.get('schema') != 2 or not isinstance(record.get('files'), list) or
            len(record['files']) != len(PATCHES) or type(record.get('changed')) is not bool):
        raise ValueError('invalid DeePMD compatibility receipt')
    for patch, entry in zip(PATCHES, record['files']):
        if (entry.get('file') != patch['file'] or entry.get('patch') != patch['name'] or
                entry.get('upstream_commit') != patch['upstream_commit'] or type(entry.get('changed')) is not bool):
            raise ValueError('invalid DeePMD compatibility receipt')
        path = Path(package) / patch['file']
        observed = path.read_bytes() if path.exists() else None
        if (digest(observed) if observed is not None else None) != entry['installed_sha256']:
            raise ValueError('installed DeePMD compatibility file changed')
        if entry['changed']:
            if (observed is None or observed.decode().count(patch['after']) != 1 or
                    digest(observed.decode().replace(patch['after'], patch['before']).encode()) != patch['original_sha256'] or
                    entry['original_sha256'] != patch['original_sha256']):
                raise ValueError('DeePMD compatibility backport differs from reviewed source')
        elif (entry['original_sha256'] != entry['installed_sha256'] or
              entry['original_sha256'] == patch['original_sha256']):
            raise ValueError('unrecorded or missing DeePMD compatibility change')
    if record['changed'] != any(entry['changed'] for entry in record['files']):
        raise ValueError('inconsistent DeePMD compatibility receipt')
    return record


if __name__ == '__main__':
    print(json.dumps(apply(sys.argv[1]), sort_keys=True))
