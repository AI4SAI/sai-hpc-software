#!/usr/bin/env python3
"""Run individual ELPA GPU phase controls, without changing installed libraries."""
import argparse
import json
from pathlib import Path
import shlex
import shutil
import sys

from submit import ROOT, execute, name


def main():
    target = '4v100-avx512'
    build = ROOT / 'runs' / (name(target) + '-export')
    original = ROOT / 'runtime-tests' / (build.name + '-science')
    request = json.loads((original / 'request.json').read_text())
    control = Path(json.loads((build / 'request.json').read_text())['controller'])
    sys.path.insert(0, str(control))
    import cp2k_acceptance as frozen
    from source_cache import checksum
    frozen.request_artifact(request)
    task = ROOT / 'runtime-tests/cp2k-elpa-phases-20261020-4v100-avx512'
    task.mkdir(mode=0o700)
    for directory in ('results', 'apptainer-runtime', 'apptainer-cache'):
        (task / directory).mkdir()
    settings = {
        'cpu-tridi-to-band': {'ELPA_FORCE_real_kernel': 'ELPA_2STAGE_REAL_AVX2_BLOCK4'},
        'cpu-solve-tridi': {'ELPA_FORCE_gpu_solve_tridi': '0'},
        'cpu-band-to-full': {'ELPA_FORCE_gpu_trans_ev_band_to_full': '0'},
        'cpu-bandred': {'ELPA_FORCE_gpu_bandred': '0'},
    }
    schedule = [(case, 'single', 'water64-elpa') for case in settings]
    schedule += [('small-cpu-tridi-to-band', 'multi', 'water-elpa')]
    settings['small-cpu-tridi-to-band'] = settings['cpu-tridi-to-band']
    hashes = {}
    for case, mode, reference in schedule:
        folder = task / 'fixtures' / case
        shutil.copytree(original / 'fixtures' / reference, folder)
        hashes[case] = {p.name: checksum(p) for p in folder.iterdir()}
    # Preserve the production launcher and helper identities. This diagnostic
    # copy forwards only the additional ELPA controls into the same sealed SIF.
    original_launcher = Path(request['launcher'])
    text = original_launcher.read_text()
    anchor = 'launcher_control=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)'
    if text.count(anchor) != 1 or text.count('|OMP_*)') != 1:
        raise ValueError('unexpected frozen runtime launcher')
    text = text.replace(anchor, 'launcher_control=' + shlex.quote(str(control)))
    text = text.replace('|OMP_*)', '|OMP_*|ELPA_*)')
    launcher = task / 'cp2k-phase'
    launcher.write_text(text)
    launcher.chmod(0o700)
    request.update(run_id=task.name, purpose='cp2k-shared-elpa-phase-diagnostic',
                   parent_science=original.name, controller=str(control),
                   production_launcher=str(original_launcher), launcher=str(launcher),
                   launcher_sha256=checksum(launcher), diagnostic_schedule=schedule,
                   phase_settings=settings, fixture_hashes=hashes, minutes=30)
    script = frozen.render_job(argparse.Namespace(**request))
    script = script.replace('--job-name=cp2k-science-', '--job-name=cp2k-elpa-phases-')
    script = script.split('run_one water-gpw single 1\n')[0]
    variables = sorted({key for values in settings.values() for key in values})
    for case, mode, reference in schedule:
        script += 'unset ' + ' '.join(variables) + '\n'
        script += 'export ' + ' '.join(shlex.quote(k + '=' + v) for k, v in settings[case].items()) + '\n'
        script += (f'if run_one {case} {mode} {frozen.MODES[mode]}; then rc=0; else rc=$?; fi\n'
                   f'printf "%s\\t%s\\t%s\\n" {case} {mode} "$rc" >> results/diagnostic-status.tsv\n')
    script += 'echo CP2K_ELPA_PHASE_DIAGNOSTIC_FINISHED\n'
    path = task / 'job.sbatch'
    path.write_text(script)
    wrapper = task / 'rank-exec.sh'
    wrapper.write_text(frozen.RANK_WRAPPER)
    for p in (path, wrapper, launcher):
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
    print(json.dumps(dict(target=target, job=job, diagnostic=str(task))))


if __name__ == '__main__':
    main()
