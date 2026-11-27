"""Apply the qualified SAI SM70 adaptation only to its exact reviewed inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def adapt(source, architecture):
    source = Path(source)
    if source.resolve() != source or source.is_symlink():
        raise ValueError('redirected SpinGNN source')
    if architecture != '70':
        return dict(adaptation=None)
    control = Path(__file__).resolve().parent
    patch = control / 'spingnn_sm70.patch'
    expected = json.loads((control / 'spingnn_sm70.json').read_text())
    observed = {}
    for name, hashes in expected.items():
        path = source / name
        if path.resolve() != path or path.is_symlink():
            raise ValueError('redirected SpinGNN adaptation input')
        observed[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    original = all(observed[name] == row['original_sha256'] for name, row in expected.items())
    adapted = all(observed[name] == row['adapted_sha256'] for name, row in expected.items())
    if not original and not adapted:
        raise ValueError('unknown or partially adapted SpinGNN SM70 source; qualify this release before publication')
    if original:
        command = ['patch', '--batch', '--fuzz=0', '--forward', '-p1', '-i', str(patch)]
        subprocess.run([*command, '--dry-run'], cwd=source, check=True, capture_output=True)
        subprocess.run(command, cwd=source, check=True, capture_output=True)
    for name, hashes in expected.items():
        if hashlib.sha256((source / name).read_bytes()).hexdigest() != hashes['adapted_sha256']:
            raise ValueError('SpinGNN SM70 adaptation output mismatch')
    return dict(adaptation='SAI SM70 (not upstream official support)',
                patch_sha256=hashlib.sha256(patch.read_bytes()).hexdigest(), files=expected)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('architecture')
    args = parser.parse_args()
    print(json.dumps(adapt(args.source, args.architecture), indent=2, sort_keys=True))
