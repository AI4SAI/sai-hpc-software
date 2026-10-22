#!/usr/bin/env python3
"""Prepare the administrator handoff in the cluster home; never write public /opt."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import sys

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parents[1]
BASE = PACKAGE.with_name('cp2k-2026.2')
spec = importlib.util.spec_from_file_location('cp2k_install_base', BASE / 'install.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
BUILD = 'v2026.2-g67b5da876dd6-raed7aa53ff43'
SOURCE = '67b5da876dd6a76b8b021d5a04d1c81ba79a4c50'
RECIPE = 'aed7aa53ff436310f28e5fdc21e1c3ca211f2422c5dd6abe44b19aa1d3ae9277'
ARTIFACTS = {
    '4V100': ('4v100-avx512', '7ebe3fd7e9561379276cdab392854c00f4528de468d46b1ca5a66ab438e63098'),
    '16V100': ('16v100-avx2', 'adf6e250bd43050e6d6ce589ffceae8d8c818e0202a5e27b9cd6b43476172fe5'),
    '8V100V0': ('8v100v0-avx512', '601b10438d54008f25b74fc7e40e2d71d3ee36f6c4d9efb7d1d6a5daa0abd138'),
    'DSPRHBM': ('dsprhbm', 'ffa8f9fae69c5a45ade6cdcae8a71391c2c222a31ec438e1e7eb025c2d4338b0'),
}


def prepare(project, output):
    base.require(output.is_relative_to(project / 'deployment'), 'package must be in project home deployment')
    base.plain_path(output)
    output.mkdir(mode=0o755)
    for name in ('install.py', 'install.sh', '2026.2-auto', 'README.md'):
        shutil.copyfile(PACKAGE / name, output / name)
    shutil.copyfile(BASE / 'install.py', output / 'base_install.py')
    for name in ('submit-native-checks.sh', 'verify-native.sbatch', 'native-rank.sh', 'run-native-checks.py'):
        shutil.copyfile(BASE / name, output / name)
    shutil.copyfile(BASE / '2026.2-auto', output / 'previous-2026.2-auto')
    shutil.copyfile(REPO / 'controller/cp2k_site_elpa.json', output / 'shared-elpa.json')
    release = dict(schema=1, source_sha=SOURCE, version='v2026.2', recipe_sha256=RECIPE,
                   workflow='https://github.com/AI4SAI/sai-hpc-software/actions/runs/39365139034',
                   hash_images=False, base_installer_sha256=base.checksum(output / 'base_install.py'),
                   module_sha256=base.checksum(output / '2026.2-auto'),
                   previous_module_sha256=base.checksum(output / 'previous-2026.2-auto'),
                   shared_elpa_sha256=base.checksum(output / 'shared-elpa.json'), deliveries=[])
    for partition, (target, image_sha) in ARTIFACTS.items():
        run = '39365139034-1-release-' + target + '-2026-10-20-67b5da876dd6'
        source = project / 'containers/software/cp2k/release' / BUILD / partition / (run + '.native')
        manifest = json.loads((source / 'share/sai/manifest.json').read_text())
        identity = manifest['entry']['identity']
        base.require(identity['recipe_sha256'] == RECIPE and identity['source_sha'] == SOURCE
                     and identity['target'] == target, 'wrong accepted build')
        published = json.loads(source.with_suffix('.json').read_text())
        proof = published['cp2k_science']
        evidence = project / 'runtime-tests' / proof['run_id'] / 'results/evidence.json'
        base.require(proof['verified'] is True and proof['identity'] == identity
                     and proof['artifact_sha256'] == image_sha
                     and base.checksum(evidence) == proof['evidence_sha256'], 'science reference changed')
        base.require(base.checksum(source / 'share/sai/cp2k_site_elpa.json') == release['shared_elpa_sha256'],
                     'ELPA lock differs from accepted export')
        release['deliveries'].append(dict(partition=partition, build_id=BUILD, run_id=run,
            image_sha256=image_sha, manifest_sha256=base.checksum(source / 'share/sai/manifest.json'),
            export_sha256=base.checksum(source / 'share/sai/export.json'),
            sidecar_sha256=base.checksum(source.with_suffix('.json')),
            science_evidence_sha256=proof['evidence_sha256']))
    base.PACKAGE = output
    (output / 'release.json').write_bytes(base.canonical(release))
    base.shared_dependencies()
    # Hash the original images once during preparation; administrators install
    # directly from the pinned native files and need not repeat this read.
    deliveries = base.load_deliveries(project, hash_images=True)
    for entry, delivery in zip(release['deliveries'], deliveries):
        path = output / (entry['partition'] + '-receipt.json')
        path.write_bytes(base.canonical(delivery['receipt']))
        entry['receipt_sha256'] = base.checksum(path)
        entry['runtime_bytes'] = sum(r.get('size', 0) for r in delivery['receipt']['files'])
        entry['runtime_files'] = sum(r['kind'] == 'file' for r in delivery['receipt']['files'])
    (output / 'release.json').write_bytes(base.canonical(release))
    for path in output.iterdir():
        path.chmod(0o644)
    output.chmod(0o755)
    base.preflight(deliveries)
    print('Prepared administrator package: ' + str(output), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, default=Path('/home/stardust/sai-hpc-software'))
    parser.add_argument('--output', type=Path,
                        default=Path('/home/stardust/sai-hpc-software/deployment/cp2k-2026.2-shared-elpa'))
    args = parser.parse_args()
    prepare(args.project_root.absolute(), args.output.absolute())
