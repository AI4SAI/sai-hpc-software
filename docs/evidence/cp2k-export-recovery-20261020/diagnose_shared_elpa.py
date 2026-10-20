#!/usr/bin/env python3
"""Isolate the failed shared-ELPA paths; these jobs cannot publish a candidate."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from submit import ROOT, execute, name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=('16v100-avx2', '4v100-avx512'))
    args = parser.parse_args()
    build = ROOT / 'runs' / (name(args.target) + '-export')
    original = ROOT / 'runtime-tests' / (build.name + '-science')
    request = json.loads((original / 'request.json').read_text())
    control = Path(json.loads((build / 'request.json').read_text())['controller'])
    sys.path.insert(0, str(control))
    import cp2k_acceptance as frozen
    from source_cache import checksum
    artifact, manifest = frozen.request_artifact(request)
    if manifest.get('cp2k_science', {}).get('verified'):
        raise ValueError('candidate already accepted; this diagnostic targets failed science')
    task = ROOT / 'runtime-tests' / ('cp2k-elpa-diagnostic-20261020-' + args.target)
    task.mkdir(mode=0o700)
    for directory in ('results', 'apptainer-runtime', 'apptainer-cache'):
        (task / directory).mkdir()
    # Keep the failed fixture's physics and convergence unchanged. Change only
    # solver/kernel selection; test the small distributed matrix separately.
    schedule = [('large-cusolver', 'single', 'water64-elpa', 'cusolver'),
                ('large-cpu', 'single', 'water64-elpa', 'cpu'),
                ('large-one-stage', 'single', 'water64-elpa', 'one-stage'),
                ('small-gpu', 'multi', 'water-elpa', 'gpu'),
                ('small-cpu', 'multi', 'water-elpa', 'cpu'),
                ('small-one-stage', 'multi', 'water-elpa', 'one-stage'),
                ('large-gpu', 'single', 'water64-elpa', 'gpu')]
    hashes = {}
    for case, mode, reference, backend in schedule:
        source = original / 'fixtures' / reference
        folder = task / 'fixtures' / case
        shutil.copytree(source, folder)
        text = (folder / 'input.inp').read_text()
        if backend == 'cusolver':
            text = text.replace('PREFERRED_DIAG_LIBRARY ELPA', 'PREFERRED_DIAG_LIBRARY CUSOLVER')
        elif backend == 'cpu':
            text = text.replace('  ELPA_PRINT T\n', '  ELPA_PRINT T\n  ELPA_KERNEL AVX2_BLOCK4\n')
        elif backend == 'one-stage':
            text = text.replace('  ELPA_PRINT T\n', '  ELPA_PRINT T\n  ELPA_ONE_STAGE T\n')
        (folder / 'input.inp').write_text(text)
        hashes[case] = {p.name: checksum(p) for p in folder.iterdir()}
    request.update(run_id=task.name, purpose='cp2k-shared-elpa-failure-diagnostic',
                   parent_science=original.name, controller=str(control),
                   diagnostic_schedule=schedule, fixture_hashes=hashes)
    script = frozen.render_job(argparse.Namespace(**request))
    script = script.replace('--job-name=cp2k-science-', '--job-name=cp2k-elpa-diagnostic-')
    boundary = 'run_one water-gpw single 1\n'
    if script.count(boundary) != 1:
        raise ValueError('unexpected frozen science script')
    script = script.split(boundary)[0]
    for case, mode, reference, backend in schedule:
        script += (f'if run_one {case} {mode} {frozen.MODES[mode]}; then rc=0; else rc=$?; fi\n'
                   f'printf "%s\\t%s\\t%s\\n" {case} {mode} "$rc" >> results/diagnostic-status.tsv\n')
    script += 'echo CP2K_ELPA_DIAGNOSTIC_FINISHED\n'
    path = task / 'job.sbatch'
    path.write_text(script)
    wrapper = task / 'rank-exec.sh'
    wrapper.write_text(frozen.RANK_WRAPPER)
    for p in (path, wrapper):
        p.chmod(0o700)
        execute(['bash', '-n', p])
    request.update(job_script_sha256=checksum(path), diagnostic_tool_sha256=checksum(Path(__file__)))
    (task / 'request.json').write_text(json.dumps(request, indent=2) + '\n')
    execute(['sbatch', '--test-only', path])
    job = execute(['sbatch', '--hold', '--parsable', path]).strip().split(';')[0]
    if not job.isdigit():
        raise ValueError('ambiguous held submission')
    (task / 'job.id').write_text(job + '\n')
    request['job'] = job
    (task / 'request.json').write_text(json.dumps(request, indent=2) + '\n')
    execute(['scontrol', 'release', job])
    print(json.dumps(dict(target=args.target, job=job, diagnostic=str(task), artifact=str(artifact))))


if __name__ == '__main__':
    main()
