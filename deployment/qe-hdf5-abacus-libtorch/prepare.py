#!/usr/bin/env python3
"""Create a SIF-only administrator handoff after native consumer acceptance."""
import argparse
import json
from pathlib import Path
import shutil
import sys

PACKAGE = Path(__file__).resolve().parent
CONTROL = PACKAGE.parents[1]/'controller'
sys.path.insert(0,str(CONTROL))
from app_dependency_stage import accepted
from devtools_install import checksum, plain, require


def prepare(project, run_id, output):
    project, output = plain(project), plain(output)
    provider = accepted(project,run_id)
    require(output.parent in (project/'deployment', Path('/home/stardust/sai-hpc-software/deployment')),
            'Unexpected handoff directory')
    require(not output.exists(), 'Use a fresh handoff directory')
    run = project/'runs'/run_id
    output.mkdir(mode=0o755)
    (output/'images').mkdir(mode=0o755)
    (output/'manifests').mkdir(mode=0o755)
    shutil.copyfile(CONTROL/'devtools_install.py',output/'install.py')
    for name in ('install.sh','README.md'):
        shutil.copyfile(PACKAGE/name,output/name)
    image = output/'images/libraries.sif'
    expected = checksum(run/'libraries.sif')
    shutil.copyfile(run/'libraries.sif',image)
    require(checksum(image) == expected, 'Handoff SIF copy changed')
    image.chmod(0o444)
    release = dict(schema=1,purpose='application-dependencies',partitions=['DSPRHBM'],packages=[],
                   build=dict(run_id=run_id,job_id=(run/'job.id').read_text().strip()))
    for name, package in provider['packages'].items():
        manifest = output/'manifests'/(name+'.json')
        manifest.write_text(json.dumps(package['files'],indent=2,sort_keys=True)+'\n')
        release['packages'].append(dict(name=name,version=package['version'],prefix=package['prefix'],
            image='images/libraries.sif',image_sha256=expected,manifest='manifests/'+manifest.name,
            manifest_sha256=checksum(manifest)))
    (output/'release.json').write_text(json.dumps(release,indent=2,sort_keys=True)+'\n')
    (output/'release.sha256').write_text(checksum(output/'release.json')+'\n')
    for path in output.rglob('*'):
        path.chmod(0o755 if path.is_dir() else 0o444 if path.suffix=='.sif' else 0o644)
    print(output)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--run',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    prepare(args.project,args.run,args.output)
