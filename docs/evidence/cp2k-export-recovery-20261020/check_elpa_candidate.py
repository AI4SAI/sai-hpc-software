#!/usr/bin/env python3
"""Repeat unmodified CP2K science inputs with an explicitly recorded candidate ELPA bind."""
import argparse
import json
from pathlib import Path
import shlex
import sys

from submit import ROOT, execute, name


def verify(task):
    request = json.loads((task / 'request.json').read_text())
    sys.path.insert(0, request['controller'])
    import cp2k_acceptance as frozen
    from source_cache import checksum
    if request['purpose'] != 'cp2k-science-with-isolated-elpa-candidate' or request['publication_allowed']:
        raise ValueError('not an isolated dependency diagnostic')
    frozen.request_artifact(request)
    job = (task / 'job.id').read_text().strip()
    accounting = execute(['sacct', '-X', '-nP', '-j', job, '-o', 'JobIDRaw,State,ExitCode'])
    if accounting.strip().split('|')[:3] != [job, 'COMPLETED', '0:0'] or job != request['job']:
        raise ValueError('candidate science has not completed successfully')
    if 'CP2K_ISOLATED_ELPA_SCIENCE_FINISHED' not in (task / 'results' / ('slurm-' + job + '.log')).read_text():
        raise ValueError('candidate science completion marker missing')
    for path, key in [(task / 'job.sbatch', 'job_script_sha256'),
                      (Path(request['launcher']), 'launcher_sha256'),
                      (task / 'rank-exec.sh', 'rank_wrapper_sha256'),
                      (Path(frozen.__file__), 'controller_sha256'),
                      (Path(frozen.fixtures.__file__), 'helpers_sha256')]:
        if checksum(path) != request[key]:
            raise ValueError('diagnostic control changed: ' + str(path))
    candidate = Path(request['elpa_candidate']) / 'install'
    current = {str(p.relative_to(candidate)): checksum(p) for p in candidate.rglob('*')
               if p.is_file() and not p.is_symlink()}
    if current != request['elpa_candidate_files']:
        raise ValueError('candidate ELPA installation changed')
    hosts = (task / 'results/nodes.txt').read_text().splitlines()
    if len(hosts) != 2 or len(set(hosts)) != 2:
        raise ValueError('expected two distinct compute nodes')
    if (task / 'results/run-order.tsv').read_text() != ''.join(c + '\t' + m + '\n' for c, m in frozen.schedule()):
        raise ValueError('science schedule changed')
    samples, placements, files = {}, {}, {}
    for case, mode in frozen.schedule():
        relative = 'cases/' + case + '/' + mode
        folder = task / relative
        for filename, expected in frozen.fixture_hashes(case).items():
            for path in (folder / filename, task / 'fixtures' / case / filename):
                if checksum(path) != expected:
                    raise ValueError('scientific input changed')
        for rank in range(frozen.MODES[mode]):
            proof = (folder / ('elpa-library.rank' + str(rank) + '.sha256')).read_text().split()
            if len(proof) != 2 or proof[0] != request['elpa_library_sha256']:
                raise ValueError('rank did not see candidate library bytes')
        placements[relative] = frozen.verify_ranks(task, relative, request, hosts[:frozen.MODES[mode]], files)
        samples[relative] = frozen.parse_science((folder / 'cp2k.log').read_text(),
            (folder / 'forces.xyz').read_text(), (folder / 'elapsed.txt').read_text(), case)
        for filename in ('cp2k.log', 'forces.xyz', 'elapsed.txt', 'input.inp'):
            files[relative + '/' + filename] = checksum(folder / filename)
    comparisons = {case: frozen.compare_science(samples['cases/' + case + '/single'],
                     samples['cases/' + case + '/multi']) for case in frozen.CASES}
    solvers = {}
    for mode in frozen.MODES:
        for case in ('water-diag', 'water-elpa', 'water64-elpa'):
            reference = 'water64-gpw' if case.startswith('water64-') else 'water-gpw'
            solvers[case + '/' + mode] = frozen.compare_science(samples['cases/' + reference + '/' + mode],
                                                               samples['cases/' + case + '/' + mode])
    proof = dict(job=job, run_id=task.name, verified=True, publication_allowed=False,
                 artifact_sha256=request['artifact_sha256'], elpa_library_sha256=request['elpa_library_sha256'],
                 scope='Unmodified 14-case CP2K protocol with an isolated candidate ELPA bind; public dependency remains unaccepted.',
                 comparisons=comparisons, solver_comparisons=solvers, samples=samples,
                 placements=placements, files=files)
    (task / 'results/diagnostic-evidence.json').write_text(json.dumps(proof, sort_keys=True) + '\n')
    print(json.dumps(dict(run_id=task.name, job=job, verified=True, calculations=len(samples), publication_allowed=False)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('target', choices=('4v100-avx512', '16v100-avx2', '8v100v0-avx512'))
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    task = ROOT / 'runtime-tests' / ('cp2k-elpa-host-staging-20261020-' + args.target)
    if args.verify:
        return verify(task)
    isa = 'avx512' if args.target == '4v100-avx512' else 'avx2'
    candidate = ROOT / 'runs' / ('elpa-2026.02.001-backtransform-20261020-' + isa)
    if not (candidate / 'accepted.txt').is_file():
        raise ValueError('candidate has not passed standalone SIF checks')
    candidate_job = (candidate / 'job.id').read_text().strip()
    accounting = execute(['sacct', '-X', '-nP', '-j', candidate_job, '-o', 'JobIDRaw,State,ExitCode'])
    if accounting.strip().split('|')[:3] != [candidate_job, 'COMPLETED', '0:0']:
        raise ValueError('candidate build is not completed successfully')
    build = ROOT / 'runs' / (name(args.target) + '-export')
    original = ROOT / 'runtime-tests' / (build.name + '-science')
    request = json.loads((original / 'request.json').read_text())
    control = Path(json.loads((build / 'request.json').read_text())['controller'])
    sys.path.insert(0, str(control))
    import cp2k_acceptance as frozen
    from source_cache import checksum
    frozen.request_artifact(request)
    task.mkdir(mode=0o700)
    for directory in ('results', 'apptainer-runtime', 'apptainer-cache'):
        (task / directory).mkdir()
    frozen.stage_fixtures(task)
    prefix = '/opt/devtools/elpa/elpa-2026.02.001-2603-gnu-' + isa + '/nvidia'
    library = candidate / 'install/lib/libelpa_openmp.so.19'
    candidate_files = {str(p.relative_to(candidate / 'install')): checksum(p)
                       for p in (candidate / 'install').rglob('*') if p.is_file() and not p.is_symlink()}
    original_launcher = Path(request['launcher'])
    text = original_launcher.read_text()
    anchor = 'launcher_control=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)'
    launch = '"${args[@]}" "$image" /usr/bin/bash --noprofile --norc -c'
    if text.count(anchor) != 1 or text.count(launch) != 1 or text.count('driver=${LD_LIBRARY_PATH:-};') != 1:
        raise ValueError('unexpected frozen runtime launcher')
    text = text.replace(anchor, 'launcher_control=' + shlex.quote(str(control)))
    text = text.replace(launch, 'args+=(--bind ' + shlex.quote(str(candidate / 'install') + ':' + prefix + ':ro') + ')\n' + launch)
    text = text.replace('driver=${LD_LIBRARY_PATH:-};',
        'sha256sum ' + prefix + '/lib/libelpa_openmp.so.19 > /work/elpa-library.rank${OMPI_COMM_WORLD_RANK}.sha256; driver=${LD_LIBRARY_PATH:-};')
    launcher = task / 'cp2k-candidate'
    launcher.write_text(text)
    launcher.chmod(0o700)
    request.update(run_id=task.name, purpose='cp2k-science-with-isolated-elpa-candidate',
                   parent_science=original.name, controller=str(control),
                   production_launcher=str(original_launcher), launcher=str(launcher),
                   launcher_sha256=checksum(launcher), elpa_candidate=str(candidate),
                   elpa_library_sha256=checksum(library), elpa_candidate_files=candidate_files,
                   publication_allowed=False)
    script = frozen.render_job(argparse.Namespace(**request))
    script = script.replace('--job-name=cp2k-science-', '--job-name=cp2k-elpa-candidate-')
    script = script.replace('CP2K_CANDIDATE_SCIENCE_FINISHED', 'CP2K_ISOLATED_ELPA_SCIENCE_FINISHED')
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
    print(json.dumps(dict(target=args.target, job=job, run=str(task), publication_allowed=False)))


if __name__ == '__main__':
    main()
