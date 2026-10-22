"""Exercise the failed CMake FindOpenMP cleanup on a fresh home-backed overlay."""
import json
import os
from pathlib import Path
import subprocess

root = Path('/workspace/openmp-repro')
root.mkdir(parents=True)
(root / 'CMakeLists.txt').write_text('cmake_minimum_required(VERSION 3.21)\n'
    'project(openmp_cleanup LANGUAGES C Fortran)\nfind_package(OpenMP REQUIRED)\n')
Path('/workspace/tmp').mkdir(exist_ok=True)
env = dict(os.environ, TMPDIR='/workspace/tmp')
gcc = '/opt/devtools/gcc/14.3.0'
env['LD_LIBRARY_PATH'] = gcc + '/lib64:' + env.get('LD_LIBRARY_PATH', '')
records = []
for i in range(40):
    log = Path('/probe/results') / ('openmp-%02d.log' % i)
    with log.open('x') as stream:
        completed = subprocess.run(['/opt/devtools/cmake/3.31.6/bin/cmake',
            '-S', str(root), '-B', str(root / ('build-%02d' % i)),
            '-DCMAKE_C_COMPILER=' + gcc + '/bin/gcc',
            '-DCMAKE_Fortran_COMPILER=' + gcc + '/bin/gfortran'],
            env=env, stdout=stream, stderr=subprocess.STDOUT)
    record = dict(iteration=i, returncode=completed.returncode, log=str(log))
    records.append(record)
    print(json.dumps(record), flush=True)
    if completed.returncode:
        break
Path('/probe/results/openmp-repro.json').write_text(json.dumps(records, indent=2) + '\n')
raise SystemExit(any(row['returncode'] for row in records))
