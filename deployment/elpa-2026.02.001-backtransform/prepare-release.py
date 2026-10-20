#!/usr/bin/env python3
"""Prepare the two NVIDIA replacements after all three CP2K diagnostic gates pass."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parents[1]
ROOT = Path('/home/stardust/sai-hpc-software')
PREVIOUS = PACKAGE.with_name('elpa-2026.02.001-commfix')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Reuse the existing numerical/lifecycle parser and replacement primitives.
    sys.path.insert(0, str(PREVIOUS))
    spec = importlib.util.spec_from_file_location('elpa_previous_prepare', PREVIOUS / 'prepare-release.py')
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    checksum, require, inventory = legacy.checksum, legacy.require, legacy.inventory
    targets = ('16v100-avx2', '4v100-avx512', '8v100v0-avx512')
    checker = REPO / 'docs/evidence/cp2k-export-recovery-20261020/check_elpa_candidate.py'
    checks = {}
    for target in targets:
        subprocess.run(['python3', str(checker), target, '--verify'], check=True)
        task = ROOT / 'runtime-tests' / ('cp2k-elpa-host-staging-20261020-' + target)
        request = json.loads((task / 'request.json').read_text())
        evidence = task / 'results/diagnostic-evidence.json'
        checks[target] = dict(job_id=request['job'], evidence=str(evidence), evidence_sha256=checksum(evidence),
                              elpa_library_sha256=request['elpa_library_sha256'],
                              artifact_sha256=request['artifact_sha256'])
    old = json.loads((PREVIOUS / 'release.json').read_text())
    module = Path('/opt/modules/modulefiles/devtools/elpa/2026.02.001-2603-gnu-auto')
    require(checksum(module) == old['module_sha256'], 'Public module changed')
    proofs = {}
    for isa in ('avx2', 'avx512'):
        run = ROOT / 'runs' / ('elpa-2026.02.001-backtransform-20261020-' + isa)
        job = (run / 'job.id').read_text().strip()
        accounting = subprocess.check_output(['sacct', '-X', '-nP', '-j', job, '-o', 'JobIDRaw,State,ExitCode'], text=True)
        require(accounting.strip().split('|')[:3] == [job, 'COMPLETED', '0:0'], 'ELPA build failed')
        require((run / 'accepted.txt').read_text().startswith('ELPA_BUILD_AND_CHECKS_PASSED ' + isa), 'Missing build marker')
        inputs = json.loads((run / 'candidate.json').read_text())['inputs']
        require(all(checksum(run / p) == digest for p, digest in inputs.items()), 'Frozen ELPA input changed')
        sif = run / ('elpa-' + isa + '.sif')
        require(checksum(sif) == (run / 'artifact.sha256').read_text().split()[0], 'ELPA SIF changed')
        proofs[isa] = legacy.verify_results(run / 'work/verify', isa)
    out = args.output
    require(out.is_absolute() and out.resolve() == out and not out.exists(), 'Use a new canonical absolute package path')
    out.mkdir(parents=True, mode=0o755)
    for filename in ('install.py', 'install.sh', 'README.md'):
        shutil.copyfile(PACKAGE / filename, out / filename)
    shutil.copyfile(PREVIOUS / 'install.py', out / 'base_install.py')
    patch = REPO / 'docs/evidence/cp2k-export-recovery-20261020/gpu-backtransform-host-staging.patch'
    release = dict(revision='backtransform-host-staging-1', version='2026.02.001',
                   source_sha256=old['source_sha256'], module_sha256=checksum(module),
                   base_installer_sha256=checksum(out / 'base_install.py'),
                   backtransform_patch_sha256=checksum(patch), builds={}, cp2k_diagnostics=checks,
                   scope='ELPA replacement candidate; physical acceptance and a new CP2K dependency lock remain required')
    for isa in ('avx2', 'avx512'):
        run = ROOT / 'runs' / ('elpa-2026.02.001-backtransform-20261020-' + isa)
        sif = run / ('elpa-' + isa + '.sif')
        prefix = legacy.PREFIXES[isa]
        scratch = run / 'work' / ('handoff-' + out.name)
        scratch.mkdir()
        squash = scratch / 'root.squashfs'
        with squash.open('xb') as stream:
            subprocess.run(['/opt/devtools/apptainer/1.4.4/bin/apptainer', 'sif', 'dump', '1', str(sif)],
                           stdout=stream, check=True)
        subprocess.run(['unsquashfs', '-no-progress', '-no-xattrs', '-processors', '2', '-d', str(scratch / 'root'),
                        str(squash), str(prefix).lstrip('/')], check=True)
        payload = out / 'payload' / isa
        payload.parent.mkdir(exist_ok=True)
        shutil.copytree(scratch / 'root' / str(prefix).lstrip('/'), payload, symlinks=True)
        require(checksum(payload / 'share/sai/gpu-backtransform-host-staging.patch') == checksum(patch), 'Wrong SIF patch')
        require(inventory(payload) == inventory(run / 'install'), 'SIF payload differs from the CP2K-tested candidate')
        library_sha = checksum(payload / 'lib/libelpa_openmp.so.19')
        for target in targets:
            if (target == '4v100-avx512') == (isa == 'avx512'):
                require(checks[target]['elpa_library_sha256'] == library_sha, 'CP2K tested a different ELPA library')
        previous = PREVIOUS / (isa + '-manifest.json')
        require(checksum(previous) == old['builds'][isa]['manifest_sha256'], 'Previous manifest changed')
        require(inventory(prefix) == json.loads(previous.read_text()), 'Existing physical library changed')
        shutil.copyfile(previous, out / (isa + '-previous-manifest.json'))
        manifest = out / (isa + '-manifest.json')
        manifest.write_text(json.dumps(inventory(payload), indent=2, sort_keys=True) + '\n')
        release['builds'][isa] = dict(prefix=str(prefix), sif=str(sif), sif_sha256=checksum(sif),
            job_id=(run / 'job.id').read_text().strip(), manifest_sha256=checksum(manifest),
            previous_manifest_sha256=checksum(previous), **proofs[isa])
        proof = out / 'evidence' / isa
        shutil.copytree(run / 'work/verify', proof, ignore=shutil.ignore_patterns('smoke', 'fortran-smoke', 'comm-trace.so'))
    for target, check in checks.items():
        shutil.copyfile(check['evidence'], out / 'evidence' / (target + '-cp2k.json'))
    (out / 'release.json').write_text(json.dumps(release, indent=2, sort_keys=True) + '\n')
    for path in out.rglob('*'):
        if not path.is_symlink():
            path.chmod(0o755 if path.is_dir() else (path.stat().st_mode & 0o755 if path.is_relative_to(out / 'payload') else 0o644))
    subprocess.run(['bash', str(out / 'install.sh'), '--check'], check=True)
    print(out)


if __name__ == '__main__':
    main()
