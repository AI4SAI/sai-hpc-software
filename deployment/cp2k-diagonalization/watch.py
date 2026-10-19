#!/usr/bin/env python3
"""Wait for one frozen workflow's accepted GPU artifacts, then submit tuning."""
import argparse
import json
from pathlib import Path
import subprocess
import time

from run import ROOT, accepted_artifact, digest, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workflow', type=int)
    parser.add_argument('--recipe', required=True)
    parser.add_argument('--targets', nargs='+', default=['16v100-avx2'],
                        choices=['16v100-avx2','4v100-avx512','8v100v0-avx512'])
    parser.add_argument('--hours', type=int, default=12, choices=range(1,25))
    args = parser.parse_args()
    state = ROOT/'runtime-tests'/f'cp2k-direct-watch-{args.workflow}'
    state.mkdir(exist_ok=True)
    deadline = time.monotonic()+args.hours*3600
    while time.monotonic() < deadline:
        complete = True
        for target in args.targets:
            marker = state/(target+'.json')
            if marker.exists():
                continue
            complete = False
            runs = list((ROOT/'runs').glob(f'{args.workflow}-1-release-{target}-*-67b5da876dd6'))
            if not runs:
                continue
            if len(runs) != 1:
                raise ValueError('Ambiguous workflow build run')
            build = runs[0]
            request_file = build/'request.json'
            if request_file.exists():
                request = json.loads(request_file.read_text())
                if request['identity']['recipe_sha256'] != args.recipe:
                    raise ValueError('Workflow recipe differs from watcher request')
            artifact_path = build/'artifact.path'
            if not artifact_path.exists():
                continue
            artifact = Path(artifact_path.read_text().strip())
            sidecar = artifact.with_suffix('.json')
            if not sidecar.exists():
                continue
            manifest = json.loads(sidecar.read_text())
            if manifest.get('cp2k_science',{}).get('verified') is not True:
                continue
            if manifest['identity']['recipe_sha256'] != args.recipe:
                raise ValueError('Accepted artifact has another recipe')
            accepted_artifact(build.name)
            name = f'{args.workflow}-{target}-water216'
            experiment = ROOT/'runtime-tests'/('cp2k-direct-'+name)
            if experiment.exists():
                raise ValueError('Experiment already exists; inspect submission before retrying')
            result = subprocess.run(['python3',str(Path(__file__).with_name('run.py')),'submit',
                '--build-run',build.name,'--name',name,'--edge','6','--repeats','3'],
                check=True,capture_output=True,text=True)
            proof = json.loads(result.stdout)
            proof['watcher_sha256'] = digest(Path(__file__))
            write_json(marker, proof)
            print(result.stdout, flush=True)
        if complete:
            print('CP2K_TUNING_ALL_SUBMITTED', flush=True)
            return
        time.sleep(45)
    raise TimeoutError('No accepted artifact before watcher deadline; no unaccepted image was tuned')


if __name__ == '__main__':
    main()
