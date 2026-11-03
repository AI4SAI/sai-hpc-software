"""Identical image-contained workload for both writable-mount mechanisms."""
import concurrent.futures
import ctypes
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

started = time.monotonic()
root = Path('/workspace/efficiency')
root.mkdir(parents=True)
Path('/workspace/tmp').mkdir(exist_ok=True)
workers = int(os.environ['BUILD_JOBS'])
record = {'affinity': sorted(os.sched_getaffinity(0)), 'workers': workers}
gcc = '/opt/devtools/gcc/14.3.0/bin/gcc'
record['gcc'] = subprocess.check_output([gcc, '--version'], text=True).splitlines()[0]

t = time.monotonic()
data = root / 'data'
data.mkdir()
for n in range(4096):
    (data / str(n)).write_bytes(bytes([n % 256]) * 4096)
for n in range(0, 4096, 3):
    (data / str(n)).unlink()
for n in range(4096):
    if n % 3:
        assert (data / str(n)).read_bytes() == bytes([n % 256]) * 4096
record['io_seconds'] = time.monotonic() - t

units = 64
functions = 96
source_hash = hashlib.sha256()
t = time.monotonic()
for n in range(units):
    source = '#include <math.h>\n'
    for f in range(functions):
        source += (f'double f{n}_{f}(double x, const double *a, int count) {{\n'
                   f' double s={n + f}.0;\n'
                   ' for(int i=0;i<count;i++) {\n'
                   '  s += sin(x+a[i])*cos(x-a[i]);\n'
                   '  x = sqrt(fabs(x+s)+1.0);\n'
                   ' }\n return s+x;\n}\n')
    source_hash.update(source.encode())
    (root / f'f{n}.c').write_text(source)
main = ''.join(f'double f{n}_0(double,const double*,int);\n' for n in range(units))
main += '#include <stdio.h>\nint main(void){double s=0.0;\n'
main += ''.join(f's+=f{n}_0(1.0,0,0);\n' for n in range(units))
main += 'printf("%.0f\\n",s);return s!=2080.0;}\n'
(root / 'main.c').write_text(main)
source_hash.update(main.encode())
record['source_sha256'] = source_hash.hexdigest()
record['source_seconds'] = time.monotonic() - t

before = resource.getrusage(resource.RUSAGE_CHILDREN)
t = time.monotonic()
def compile_one(n):
    subprocess.run([gcc, '-O2', '-c', str(root / f'f{n}.c'),
                    '-o', str(root / f'f{n}.o')], check=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
    list(pool.map(compile_one, range(units)))
subprocess.run([gcc, '-O2', str(root / 'main.c'),
                *(str(root / f'f{n}.o') for n in range(units)),
                '-lm', '-o', str(root / 'sum')], check=True)
record['compile_seconds'] = time.monotonic() - t
after = resource.getrusage(resource.RUSAGE_CHILDREN)
record['compiler_cpu_seconds'] = (after.ru_utime + after.ru_stime
                                  - before.ru_utime - before.ru_stime)
record['compiler_average_cores'] = record['compiler_cpu_seconds'] / record['compile_seconds']
record['output'] = subprocess.check_output([str(root / 'sum')], text=True).strip()
assert record['output'] == '2080'

cuda = ctypes.CDLL('libcuda.so.1')
assert cuda.cuInit(0) == 0
devices = ctypes.c_int()
assert cuda.cuDeviceGetCount(ctypes.byref(devices)) == 0 and devices.value > 0
record['cuda_devices'] = devices.value
sys.path.insert(0, '/bench/controller')
from md_driver_paths import normalize
_, driver_evidence = normalize(os.environ.get('LD_LIBRARY_PATH', ''))
record['driver_libraries'] = [entry['name'] for entry in driver_evidence['libraries']]
assert set(('libcuda.so.1', 'libnvidia-ml.so.1', 'libnvidia-ptxjitcompiler.so.1')) <= set(record['driver_libraries'])

t = time.monotonic()
fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
try:
    libc = ctypes.CDLL(None, use_errno=True)
    assert libc.syncfs(fd) == 0, ctypes.get_errno()
finally:
    os.close(fd)
record['syncfs_seconds'] = time.monotonic() - t
record['payload_seconds'] = time.monotonic() - started
Path('/results/payload.json').write_text(json.dumps(record, indent=2) + '\n')
print('EFFICIENCY_PAYLOAD ' + json.dumps(record), flush=True)
